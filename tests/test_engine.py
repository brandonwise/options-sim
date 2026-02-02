"""Tests for the simulation engine."""


import pytest

from options_sim.engine import OptionsSimulator


class TestStartSimulation:
    """Tests for starting a simulation."""

    def test_start_returns_status(self, simulator):
        """start() returns initial status dict."""
        result = simulator.start("SPY", "2024-01-15")
        assert "timestamp" in result
        assert "symbol" in result
        assert result["symbol"] == "SPY"
        assert "account" in result

    def test_start_sets_initial_cash(self, simulator):
        """Starting sets cash to initial amount."""
        result = simulator.start("SPY", "2024-01-15")
        assert result["account"]["cash"] == 100000.0

    def test_start_with_custom_cash(self, mock_provider):
        """Custom initial cash amount."""
        sim = OptionsSimulator(mock_provider, initial_cash=50000)
        result = sim.start("SPY", "2024-01-15")
        assert result["account"]["cash"] == 50000.0

    def test_start_uppercase_symbol(self, simulator):
        """Symbol is uppercased."""
        result = simulator.start("spy", "2024-01-15")
        assert result["symbol"] == "SPY"

    def test_start_sets_time(self, simulator):
        """Starting sets simulation time to 9:30 AM."""
        simulator.start("SPY", "2024-01-15")
        assert simulator.current_time.hour == 9
        assert simulator.current_time.minute == 30

    def test_not_started_raises(self, simulator):
        """Operations before start() raise RuntimeError."""
        with pytest.raises(RuntimeError, match="not started"):
            simulator.get_status()


class TestStepTime:
    """Tests for advancing simulation time."""

    def test_step_advances_time(self, started_sim):
        """step() advances clock by given minutes."""
        t0 = started_sim.current_time
        started_sim.step(30)
        assert started_sim.current_time > t0

    def test_step_default_15min(self, started_sim):
        """Default step is 15 minutes."""
        t0 = started_sim.current_time
        started_sim.step()
        diff = (started_sim.current_time - t0).total_seconds()
        assert diff == 15 * 60

    def test_step_returns_status(self, started_sim):
        """step() returns updated status."""
        result = started_sim.step(30)
        assert "timestamp" in result
        assert "underlying_price" in result


class TestGetChain:
    """Tests for option chain retrieval."""

    def test_chain_returns_data(self, started_sim):
        """get_chain() returns option chain."""
        result = started_sim.get_chain()
        assert "chain" in result
        assert len(result["chain"]) > 0
        assert "underlying_price" in result

    def test_chain_filter_by_expiry(self, started_sim):
        """get_chain() filters by expiry."""
        result = started_sim.get_chain(expiry="2024-01-19")
        for q in result["chain"]:
            assert q["expiry"] == "2024-01-19"

    def test_chain_includes_greeks(self, started_sim):
        """Chain entries include Greeks."""
        result = started_sim.get_chain()
        q = result["chain"][0]
        assert "delta" in q
        assert "gamma" in q
        assert "theta" in q
        assert "vega" in q

    def test_chain_has_bid_ask(self, started_sim):
        """Chain entries have bid/ask prices."""
        result = started_sim.get_chain()
        q = result["chain"][0]
        assert q["bid"] > 0
        assert q["ask"] > 0
        assert q["ask"] >= q["bid"]

    def test_chain_lists_expiries(self, started_sim):
        """Chain response lists available expiries."""
        result = started_sim.get_chain()
        assert "expiries" in result
        assert len(result["expiries"]) > 0


class TestSubmitOrder:
    """Tests for order submission."""

    def test_buy_call(self, started_sim):
        """Buy call fills successfully."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        result = started_sim.submit_order(contract, "buy", 1)
        assert result["filled"] is True
        assert result["quantity"] == 1
        assert result["fill_price"] > 0

    def test_sell_put(self, started_sim):
        """Sell put fills successfully."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        # Find a put
        puts = [q for q in chain["chain"] if q["option_type"] == "put"]
        contract = puts[0]["symbol"]

        result = started_sim.submit_order(contract, "sell", 1)
        assert result["filled"] is True

    def test_buy_reduces_cash(self, started_sim):
        """Buying reduces cash balance."""
        initial_cash = started_sim.cash
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        started_sim.submit_order(contract, "buy", 1)
        assert started_sim.cash < initial_cash

    def test_sell_increases_cash(self, started_sim):
        """Selling increases cash balance."""
        initial_cash = started_sim.cash
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        started_sim.submit_order(contract, "sell", 1)
        assert started_sim.cash > initial_cash

    def test_commission_applied(self, started_sim):
        """Commission is charged on trades."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        result = started_sim.submit_order(contract, "buy", 10)
        assert result["commission"] == 6.50  # 0.65 * 10

    def test_insufficient_funds(self, mock_provider):
        """Order rejected when insufficient funds."""
        sim = OptionsSimulator(mock_provider, initial_cash=1.0)  # $1
        sim.start("SPY", "2024-01-15")
        chain = sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        result = sim.submit_order(contract, "buy", 100)
        assert result["filled"] is False
        assert "insufficient" in result["reason"].lower() or "funds" in result["reason"].lower()

    def test_invalid_contract(self, started_sim):
        """Order rejected for unknown contract."""
        result = started_sim.submit_order("INVALID000000", "buy", 1)
        assert "error" in result

    def test_trade_recorded(self, started_sim):
        """Executed trade is recorded in history."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        started_sim.submit_order(contract, "buy", 5)
        assert len(started_sim.trade_history) == 1
        assert started_sim.trade_history[0].quantity == 5

    def test_position_created(self, started_sim):
        """Executed trade creates a position."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]

        started_sim.submit_order(contract, "buy", 5)
        assert contract in started_sim.portfolio.positions
        assert started_sim.portfolio.positions[contract].quantity == 5


class TestMultiplePositions:
    """Tests for handling multiple simultaneous positions."""

    def test_multiple_contracts(self, started_sim):
        """Can hold positions in multiple contracts."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        c1 = chain["chain"][0]["symbol"]
        c2 = chain["chain"][1]["symbol"]

        started_sim.submit_order(c1, "buy", 5)
        started_sim.submit_order(c2, "buy", 3)

        assert len(started_sim.portfolio.positions) == 2

    def test_long_and_short(self, started_sim):
        """Can hold long and short positions."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        calls = [q for q in chain["chain"] if q["option_type"] == "call"]
        puts = [q for q in chain["chain"] if q["option_type"] == "put"]

        started_sim.submit_order(calls[0]["symbol"], "buy", 5)
        started_sim.submit_order(puts[0]["symbol"], "sell", 3)

        positions = started_sim.portfolio.positions
        long_pos = positions[calls[0]["symbol"]]
        short_pos = positions[puts[0]["symbol"]]
        assert long_pos.quantity > 0
        assert short_pos.quantity < 0


class TestStatus:
    """Tests for status reporting."""

    def test_status_structure(self, started_sim):
        """Status has required fields."""
        status = started_sim.get_status()
        assert "timestamp" in status
        assert "symbol" in status
        assert "account" in status
        assert "positions" in status
        assert "portfolio_greeks" in status
        assert "trade_count" in status

    def test_account_summary(self, started_sim):
        """Account summary has required fields."""
        account = started_sim.get_account()
        assert "cash" in account
        assert "total_value" in account
        assert "realized_pnl" in account
        assert "unrealized_pnl" in account
        assert "total_commissions" in account
        assert "total_return_pct" in account


class TestStatePersistence:
    """Tests for state serialization."""

    def test_serialize_roundtrip(self, started_sim, mock_provider):
        """State survives serialize/deserialize cycle."""
        chain = started_sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]
        started_sim.submit_order(contract, "buy", 5)

        state = started_sim.to_state()

        sim2 = OptionsSimulator(mock_provider)
        sim2.load_state(state)

        assert sim2.symbol == "SPY"
        assert sim2.cash == started_sim.cash
        assert len(sim2.trade_history) == 1
        assert contract in sim2.portfolio.positions
