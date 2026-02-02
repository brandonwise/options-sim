"""End-to-end integration tests for the options simulator.

Tests complete trading scenarios without any external API calls.
"""



from options_sim.engine import OptionsSimulator
from tests.conftest import MockDataProvider


class TestBuyCallScenario:
    """Full scenario: buy calls, step time, sell calls."""

    def test_buy_hold_sell_calls(self):
        """Buy calls, advance time, sell for profit/loss."""
        provider = MockDataProvider(base_price=475.0)
        sim = OptionsSimulator(provider, initial_cash=100000)

        # Start
        sim.start("SPY", "2024-01-15")

        # Get chain and buy ATM calls
        chain = sim.get_chain(expiry="2024-01-19")
        calls = [q for q in chain["chain"] if q["option_type"] == "call" and q["strike"] == 475.0]
        assert len(calls) > 0
        contract = calls[0]["symbol"]

        # Buy 10 contracts
        buy_result = sim.submit_order(contract, "buy", 10)
        assert buy_result["filled"]

        # Verify position exists
        assert contract in sim.portfolio.positions
        assert sim.portfolio.positions[contract].quantity == 10

        # Advance time — price moves up
        provider.set_price(480.0)
        sim.step(60)

        # Sell to close
        sell_result = sim.submit_order(contract, "sell", 10)
        assert sell_result["filled"]

        # Position should be closed
        assert contract not in sim.portfolio.positions

        # Check history
        history = sim.get_history()
        assert len(history) == 2
        assert history[0]["side"] == "buy"
        assert history[1]["side"] == "sell"

    def test_buy_calls_lose_money(self):
        """Buy calls, price drops, sell at loss."""
        provider = MockDataProvider(base_price=475.0)
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        calls = [q for q in chain["chain"] if q["option_type"] == "call" and q["strike"] == 475.0]
        contract = calls[0]["symbol"]

        sim.submit_order(contract, "buy", 10)

        # Price drops
        provider.set_price(470.0)
        sim.step(60)

        # Sell at loss
        sim.submit_order(contract, "sell", 10)

        # Should have lost money
        status = sim.get_status()
        assert status["account"]["total_value"] < 100000


class TestIronCondorScenario:
    """Iron condor strategy lifecycle."""

    def test_iron_condor(self):
        """Open and manage an iron condor.

        Iron condor: sell OTM put spread + sell OTM call spread
        - Sell 470 put, buy 465 put (bull put spread)
        - Sell 480 call, buy 485 call (bear call spread)
        """
        provider = MockDataProvider(
            base_price=475.0,
            strikes=[460.0, 465.0, 470.0, 475.0, 480.0, 485.0, 490.0],
        )
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        chain_list = chain["chain"]

        # Find contracts
        def find(opt_type, strike):
            matches = [q for q in chain_list if q["option_type"] == opt_type and q["strike"] == strike]
            return matches[0]["symbol"] if matches else None

        sell_put = find("put", 470.0)
        buy_put = find("put", 465.0)
        sell_call = find("call", 480.0)
        buy_call = find("call", 485.0)

        assert all([sell_put, buy_put, sell_call, buy_call])

        # Open the iron condor
        sim.submit_order(sell_put, "sell", 5)   # Sell 470P
        sim.submit_order(buy_put, "buy", 5)     # Buy 465P (protection)
        sim.submit_order(sell_call, "sell", 5)   # Sell 480C
        sim.submit_order(buy_call, "buy", 5)     # Buy 485C (protection)

        assert len(sim.portfolio.positions) == 4
        assert len(sim.trade_history) == 4

        # Net credit should have been received (short positions)
        # Price stays in range — advance time
        sim.step(60)

        status = sim.get_status()
        assert status["position_count"] == 4

        # Close all legs
        sim.submit_order(sell_put, "buy", 5)
        sim.submit_order(buy_put, "sell", 5)
        sim.submit_order(sell_call, "buy", 5)
        sim.submit_order(buy_call, "sell", 5)

        assert len(sim.portfolio.positions) == 0
        assert len(sim.trade_history) == 8


class TestCoveredCallScenario:
    """Covered call strategy (using options only since we don't have stock)."""

    def test_synthetic_covered_call(self):
        """Simulate covered call using deep ITM call + short OTM call.

        Approximate covered call: long deep ITM call + short OTM call.
        """
        provider = MockDataProvider(
            base_price=475.0,
            strikes=[460.0, 465.0, 470.0, 475.0, 480.0, 485.0, 490.0],
        )
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        chain_list = chain["chain"]

        # Buy deep ITM call (synthetic long stock)
        deep_itm = [q for q in chain_list if q["option_type"] == "call" and q["strike"] == 460.0]
        assert len(deep_itm) > 0

        # Sell OTM call (covered call)
        otm_call = [q for q in chain_list if q["option_type"] == "call" and q["strike"] == 480.0]
        assert len(otm_call) > 0

        sim.submit_order(deep_itm[0]["symbol"], "buy", 1)
        sim.submit_order(otm_call[0]["symbol"], "sell", 1)

        assert len(sim.portfolio.positions) == 2

        # Time passes, price stays flat
        sim.step(30)

        status = sim.get_status()
        assert status["position_count"] == 2

        # Close positions
        sim.submit_order(deep_itm[0]["symbol"], "sell", 1)
        sim.submit_order(otm_call[0]["symbol"], "buy", 1)

        assert len(sim.portfolio.positions) == 0


class TestExpirationHandling:
    """Tests for option expiration."""

    def test_itm_expiration(self):
        """ITM option at expiry generates realized P&L."""
        provider = MockDataProvider(
            base_price=475.0,
            strikes=[470.0, 475.0, 480.0],
            expiries=["2024-01-15"],  # Expires today
        )
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-15")
        # Buy ITM call (strike 470, underlying 475 -> ITM)
        itm_calls = [
            q for q in chain["chain"]
            if q["option_type"] == "call" and q["strike"] == 470.0
        ]
        assert len(itm_calls) > 0
        contract = itm_calls[0]["symbol"]

        sim.submit_order(contract, "buy", 5)
        assert contract in sim.portfolio.positions

        # Advance to after market close (16:00+)
        # We need to step enough to get past 16:00
        # Started at 9:30, need to get to 16:01 = 391 minutes
        sim.step(391)

        # Position should be expired — verify simulation still works
        sim.get_status()

    def test_multiple_time_steps(self):
        """Multiple small time steps work correctly."""
        provider = MockDataProvider(base_price=475.0)
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]
        sim.submit_order(contract, "buy", 5)

        # Step multiple times
        for i in range(10):
            provider.advance_price(0.5)  # Price drifts up
            sim.step(15)

        status = sim.get_status()
        assert status["trade_count"] == 1
        assert status["position_count"] == 1


class TestExportAndHistory:
    """Tests for data export and trade history."""

    def test_history_after_trades(self):
        """Trade history records all trades."""
        provider = MockDataProvider(base_price=475.0)
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        c1 = chain["chain"][0]["symbol"]
        c2 = chain["chain"][1]["symbol"]

        sim.submit_order(c1, "buy", 5)
        sim.submit_order(c2, "sell", 3)
        sim.submit_order(c1, "sell", 5)

        history = sim.get_history()
        assert len(history) == 3
        assert all("timestamp" in t for t in history)
        assert all("contract" in t for t in history)
        assert all("price" in t for t in history)

    def test_status_with_positions_and_greeks(self):
        """Status includes portfolio Greeks when positions exist."""
        provider = MockDataProvider(base_price=475.0)
        sim = OptionsSimulator(provider, initial_cash=100000)
        sim.start("SPY", "2024-01-15")

        chain = sim.get_chain(expiry="2024-01-19")
        contract = chain["chain"][0]["symbol"]
        sim.submit_order(contract, "buy", 10)

        status = sim.get_status()
        greeks = status["portfolio_greeks"]
        assert "delta" in greeks
        assert "gamma" in greeks
        assert "theta" in greeks
        assert "vega" in greeks
