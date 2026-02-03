"""Tests for the live trading engine.

All tests use a mocked Polygon API — no real network calls.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from options_sim.live_engine import LiveEngine


# ─── Fixtures ──────────────────────────────────────────────────


class MockPolygonAPI:
    """Mock PolygonLiveProvider for testing."""

    def __init__(self):
        self.quotes = {}
        self.chains = {}
        self.prices = {}

    def get_stock_quote(self, symbol):
        return self.quotes.get(symbol, {
            "symbol": symbol,
            "price": 100.0,
            "open": 99.0,
            "high": 101.0,
            "low": 98.5,
            "volume": 1000000,
            "prev_close": 99.5,
            "change": 0.5,
            "change_pct": 0.5,
        })

    def get_underlying_price(self, symbol):
        return self.prices.get(symbol, 100.0)

    def get_option_chain(self, symbol, expiry=None, strikes_around_atm=None):
        return self.chains.get(symbol, [
            {
                "symbol": f"{symbol}240119C00100000",
                "underlying": symbol,
                "strike": 100.0,
                "expiry": "2024-01-19",
                "option_type": "call",
                "bid": 2.50,
                "ask": 2.70,
                "mid": 2.60,
                "last": 2.55,
                "volume": 1000,
                "open_interest": 5000,
                "iv": 0.25,
                "delta": 0.50,
                "gamma": 0.03,
                "theta": -0.05,
                "vega": 0.20,
            },
            {
                "symbol": f"{symbol}240119P00095000",
                "underlying": symbol,
                "strike": 95.0,
                "expiry": "2024-01-19",
                "option_type": "put",
                "bid": 1.80,
                "ask": 2.00,
                "mid": 1.90,
                "last": 1.85,
                "volume": 800,
                "open_interest": 3000,
                "iv": 0.28,
                "delta": -0.35,
                "gamma": 0.02,
                "theta": -0.04,
                "vega": 0.18,
            },
        ])

    def get_option_quote(self, contract_symbol):
        # Look through all chains
        for chain in self.chains.values():
            for q in chain:
                if q["symbol"] == contract_symbol:
                    return q
        # Default
        return {
            "symbol": contract_symbol,
            "bid": 2.50,
            "ask": 2.70,
            "mid": 2.60,
            "strike": 100.0,
            "expiry": "2024-01-19",
            "option_type": "call",
            "delta": 0.50,
            "gamma": 0.03,
            "theta": -0.05,
            "vega": 0.20,
        }

    def get_available_expiries(self, symbol):
        return ["2024-01-19", "2024-01-26", "2024-02-02"]


@pytest.fixture
def mock_api():
    return MockPolygonAPI()


@pytest.fixture
def tmp_session_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def engine(mock_api, tmp_session_file):
    e = LiveEngine(
        initial_cash=100000,
        api=mock_api,
        session_file=tmp_session_file,
    )
    return e


@pytest.fixture
def started_engine(engine):
    engine.start()
    return engine


# ─── Session Management ───────────────────────────────────────


class TestSessionManagement:
    """Tests for live session start/resume/save."""

    def test_start_creates_session(self, engine, tmp_session_file):
        """Starting creates a session file."""
        engine.start()
        assert tmp_session_file.exists()

    def test_start_returns_status(self, engine):
        """start() returns status dict."""
        result = engine.start()
        assert result["mode"] == "live"
        assert "account" in result
        assert result["account"]["cash"] == 100000

    def test_not_started_raises(self, engine):
        """Operations before start() raise."""
        with pytest.raises(RuntimeError, match="not started"):
            engine.get_status()

    def test_resume_restores_state(self, started_engine, mock_api, tmp_session_file):
        """Resume loads state from disk."""
        # Make a trade first
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)

        # Create a new engine and resume
        engine2 = LiveEngine(
            initial_cash=100000,
            api=mock_api,
            session_file=tmp_session_file,
        )
        result = engine2.resume()

        assert result["trade_count"] == 1
        assert result["position_count"] == 1

    def test_resume_no_session_raises(self, mock_api):
        """Resume without session file raises."""
        nonexistent = Path("/tmp/nonexistent_session_12345.json")
        engine = LiveEngine(api=mock_api, session_file=nonexistent)
        with pytest.raises(FileNotFoundError):
            engine.resume()

    def test_clear_session(self, started_engine, tmp_session_file):
        """clear_session removes the file."""
        assert tmp_session_file.exists()
        started_engine.clear_session()
        assert not tmp_session_file.exists()


# ─── Session Persistence ──────────────────────────────────────


class TestSessionPersistence:
    """Tests for live session serialization."""

    def test_session_file_structure(self, started_engine, tmp_session_file):
        """Session file has correct structure."""
        with open(tmp_session_file) as f:
            state = json.load(f)

        assert state["mode"] == "live"
        assert "started_at" in state
        assert state["initial_cash"] == 100000
        assert state["cash"] == 100000
        assert "positions" in state
        assert "trades" in state

    def test_trades_persist(self, started_engine, tmp_session_file):
        """Trades are saved to disk."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 3)

        with open(tmp_session_file) as f:
            state = json.load(f)

        assert len(state["trades"]) == 1
        assert state["trades"][0]["contract"] == "AAPL240119C00100000"
        assert state["trades"][0]["side"] == "buy"
        assert state["trades"][0]["quantity"] == 3

    def test_positions_persist(self, started_engine, tmp_session_file):
        """Positions are saved to disk."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)

        with open(tmp_session_file) as f:
            state = json.load(f)

        positions = state["positions"]["positions"]
        assert "AAPL240119C00100000" in positions

    def test_cash_updates_on_trade(self, started_engine, tmp_session_file):
        """Cash balance updates after trade."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 1)

        with open(tmp_session_file) as f:
            state = json.load(f)

        assert state["cash"] < 100000

    def test_roundtrip_multiple_trades(self, started_engine, mock_api, tmp_session_file):
        """Multiple trades survive save/load."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)
        started_engine.submit_order("AAPL240119P00095000", "sell", 3)

        engine2 = LiveEngine(api=mock_api, session_file=tmp_session_file)
        engine2.resume()

        assert len(engine2.trade_history) == 2
        assert len(engine2.portfolio.positions) == 2


# ─── Order Execution ──────────────────────────────────────────


class TestLiveOrderExecution:
    """Tests for live order execution."""

    def test_buy_fills_at_ask(self, started_engine):
        """Buy order fills at ask price."""
        result = started_engine.submit_order("AAPL240119C00100000", "buy", 1)
        assert result["filled"] is True
        assert result["fill_price"] == 2.70  # Ask price

    def test_sell_fills_at_bid(self, started_engine):
        """Sell order fills at bid price."""
        result = started_engine.submit_order("AAPL240119C00100000", "sell", 1)
        assert result["filled"] is True
        assert result["fill_price"] == 2.50  # Bid price

    def test_buy_reduces_cash(self, started_engine):
        """Buying reduces cash."""
        initial = started_engine.cash
        started_engine.submit_order("AAPL240119C00100000", "buy", 1)
        assert started_engine.cash < initial

    def test_sell_increases_cash(self, started_engine):
        """Selling increases cash."""
        initial = started_engine.cash
        started_engine.submit_order("AAPL240119C00100000", "sell", 1)
        assert started_engine.cash > initial

    def test_commission_applied(self, started_engine):
        """Commission is charged."""
        result = started_engine.submit_order("AAPL240119C00100000", "buy", 10)
        assert result["commission"] == 6.50  # 0.65 * 10

    def test_insufficient_funds(self, mock_api, tmp_session_file):
        """Rejects when insufficient funds."""
        engine = LiveEngine(initial_cash=1.0, api=mock_api, session_file=tmp_session_file)
        engine.start()
        result = engine.submit_order("AAPL240119C00100000", "buy", 100)
        assert result["filled"] is False
        assert "insufficient" in result["reason"].lower() or "funds" in result["reason"].lower()

    def test_invalid_side(self, started_engine):
        """Invalid side returns error."""
        result = started_engine.submit_order("AAPL240119C00100000", "hold", 1)
        assert "error" in result

    def test_zero_quantity(self, started_engine):
        """Zero quantity returns error."""
        result = started_engine.submit_order("AAPL240119C00100000", "buy", 0)
        assert "error" in result

    def test_limit_buy_above_ask(self, started_engine):
        """Buy limit above ask fills."""
        result = started_engine.submit_order(
            "AAPL240119C00100000", "buy", 1, limit_price=3.00
        )
        assert result["filled"] is True

    def test_limit_buy_below_ask(self, started_engine):
        """Buy limit below ask rejected."""
        result = started_engine.submit_order(
            "AAPL240119C00100000", "buy", 1, limit_price=2.50
        )
        assert result["filled"] is False

    def test_position_created(self, started_engine):
        """Trade creates a position."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)
        assert "AAPL240119C00100000" in started_engine.portfolio.positions
        assert started_engine.portfolio.positions["AAPL240119C00100000"].quantity == 5

    def test_trade_recorded(self, started_engine):
        """Trade is recorded in history."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)
        assert len(started_engine.trade_history) == 1


# ─── Positions & Account ──────────────────────────────────────


class TestPositionsAndAccount:
    """Tests for positions and account with live prices."""

    def test_get_positions_empty(self, started_engine):
        """Empty positions work."""
        pos = started_engine.get_positions()
        assert pos["position_count"] == 0

    def test_get_positions_with_trades(self, started_engine):
        """Positions reflect trades."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)
        pos = started_engine.get_positions()
        assert pos["position_count"] == 1

    def test_account_summary(self, started_engine):
        """Account summary has required fields."""
        acct = started_engine.get_account()
        assert "cash" in acct
        assert "total_value" in acct
        assert "realized_pnl" in acct
        assert "unrealized_pnl" in acct
        assert "total_commissions" in acct
        assert "total_return_pct" in acct

    def test_history(self, started_engine):
        """Trade history returns list of dicts."""
        started_engine.submit_order("AAPL240119C00100000", "buy", 5)
        started_engine.submit_order("AAPL240119P00095000", "sell", 3)
        history = started_engine.get_history()
        assert len(history) == 2
        assert history[0]["side"] == "buy"
        assert history[1]["side"] == "sell"

    def test_status_structure(self, started_engine):
        """Status has all required fields."""
        status = started_engine.get_status()
        assert status["mode"] == "live"
        assert "account" in status
        assert "positions" in status
        assert "portfolio_greeks" in status
        assert "trade_count" in status
