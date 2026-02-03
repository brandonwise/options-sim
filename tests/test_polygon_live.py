"""Tests for the live Polygon.io data provider.

All tests use mocked HTTP responses — no real API calls.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from options_sim.data.polygon_live import (
    PolygonLiveProvider,
    RateLimiter,
    TTLCache,
    extract_underlying_from_occ,
    get_polygon_key,
    occ_to_polygon_ticker,
    parse_occ_symbol,
    polygon_ticker_to_occ,
)


# ─── OCC Symbol Conversion ────────────────────────────────────


class TestOCCSymbolConversion:
    """Tests for OCC ↔ Polygon ticker conversion."""

    def test_polygon_to_occ_with_prefix(self):
        """Strip O: prefix from Polygon ticker."""
        assert polygon_ticker_to_occ("O:SPY240119C00475000") == "SPY240119C00475000"

    def test_polygon_to_occ_without_prefix(self):
        """Already-OCC format passes through."""
        assert polygon_ticker_to_occ("SPY240119C00475000") == "SPY240119C00475000"

    def test_occ_to_polygon(self):
        """Add O: prefix for Polygon format."""
        assert occ_to_polygon_ticker("SPY240119C00475000") == "O:SPY240119C00475000"

    def test_occ_to_polygon_already_prefixed(self):
        """Don't double-prefix."""
        assert occ_to_polygon_ticker("O:SPY240119C00475000") == "O:SPY240119C00475000"

    def test_extract_underlying_spy(self):
        """Extract underlying from SPY option."""
        assert extract_underlying_from_occ("SPY240119C00475000") == "SPY"

    def test_extract_underlying_aapl(self):
        """Extract underlying from AAPL option."""
        assert extract_underlying_from_occ("AAPL250117C00200000") == "AAPL"

    def test_extract_underlying_nvda(self):
        """Extract underlying from NVDA option."""
        assert extract_underlying_from_occ("NVDA240216P00850000") == "NVDA"

    def test_extract_underlying_with_prefix(self):
        """Extract underlying from Polygon-prefixed ticker."""
        assert extract_underlying_from_occ("O:TSLA240119C00250000") == "TSLA"

    def test_parse_occ_call(self):
        """Parse OCC call symbol."""
        result = parse_occ_symbol("SPY240119C00475000")
        assert result["underlying"] == "SPY"
        assert result["expiry"] == "2024-01-19"
        assert result["option_type"] == "call"
        assert result["strike"] == 475.0

    def test_parse_occ_put(self):
        """Parse OCC put symbol."""
        result = parse_occ_symbol("AAPL250117P00200000")
        assert result["underlying"] == "AAPL"
        assert result["expiry"] == "2025-01-17"
        assert result["option_type"] == "put"
        assert result["strike"] == 200.0

    def test_parse_occ_fractional_strike(self):
        """Parse OCC symbol with fractional strike."""
        result = parse_occ_symbol("SPY240119C00475500")
        assert result["strike"] == 475.5

    def test_parse_occ_invalid(self):
        """Invalid OCC symbol returns empty dict."""
        assert parse_occ_symbol("INVALID") == {}

    def test_parse_occ_with_polygon_prefix(self):
        """Parse with O: prefix stripped."""
        result = parse_occ_symbol("O:SPY240119C00475000")
        assert result["underlying"] == "SPY"


# ─── TTL Cache ─────────────────────────────────────────────────


class TestTTLCache:
    """Tests for in-memory TTL cache."""

    def test_set_and_get(self):
        """Store and retrieve a value."""
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_missing_key(self):
        """Missing key returns None."""
        cache = TTLCache(default_ttl=60)
        assert cache.get("nonexistent") is None

    def test_expired_entry(self):
        """Expired entry returns None."""
        cache = TTLCache(default_ttl=0.01)
        cache.set("key1", "value1")
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_custom_ttl(self):
        """Custom TTL overrides default."""
        cache = TTLCache(default_ttl=60)
        cache.set("short", "val", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("short") is None

    def test_clear(self):
        """Clear removes all entries."""
        cache = TTLCache(default_ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_invalidate(self):
        """Invalidate removes specific entry."""
        cache = TTLCache(default_ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_overwrite(self):
        """Setting same key overwrites."""
        cache = TTLCache(default_ttl=60)
        cache.set("key", "old")
        cache.set("key", "new")
        assert cache.get("key") == "new"


# ─── Rate Limiter ──────────────────────────────────────────────


class TestRateLimiter:
    """Tests for rate limiter."""

    def test_first_call_immediate(self):
        """First call doesn't sleep."""
        limiter = RateLimiter(calls_per_minute=60)
        t0 = time.time()
        limiter.wait()
        elapsed = time.time() - t0
        assert elapsed < 0.5

    def test_rapid_calls_throttled(self):
        """Rapid calls are spaced out."""
        limiter = RateLimiter(calls_per_minute=600)  # 0.1s interval
        limiter.wait()
        t0 = time.time()
        limiter.wait()
        elapsed = time.time() - t0
        assert elapsed >= 0.08  # Allow small tolerance


# ─── Polygon API Response Parsing ──────────────────────────────


MOCK_STOCK_SNAPSHOT = {
    "results": [
        {
            "ticker": "AAPL",
            "session": {
                "open": 185.0,
                "high": 188.5,
                "low": 184.2,
                "close": 187.3,
                "volume": 42000000,
                "price": 187.3,
            },
            "prev_day": {"close": 185.5},
            "last_updated": "2024-02-02T16:00:00-05:00",
        }
    ]
}

MOCK_PREV_CLOSE = {
    "results": [
        {
            "o": 185.0,
            "h": 188.5,
            "l": 184.2,
            "c": 187.3,
            "v": 42000000,
        }
    ]
}

MOCK_OPTION_SNAPSHOT = {
    "results": [
        {
            "details": {
                "ticker": "O:AAPL240119C00190000",
                "contract_type": "call",
                "strike_price": 190.0,
                "expiration_date": "2024-01-19",
            },
            "greeks": {
                "delta": 0.45,
                "gamma": 0.03,
                "theta": -0.08,
                "vega": 0.25,
            },
            "day": {"close": 2.50, "volume": 1500},
            "last_quote": {"bid": 2.40, "ask": 2.60},
            "open_interest": 5000,
            "implied_volatility": 0.32,
        },
        {
            "details": {
                "ticker": "O:AAPL240119P00180000",
                "contract_type": "put",
                "strike_price": 180.0,
                "expiration_date": "2024-01-19",
            },
            "greeks": {
                "delta": -0.35,
                "gamma": 0.02,
                "theta": -0.06,
                "vega": 0.20,
            },
            "day": {"close": 1.80, "volume": 800},
            "last_quote": {"bid": 1.75, "ask": 1.90},
            "open_interest": 3000,
            "implied_volatility": 0.28,
        },
    ]
}

MOCK_EXPIRIES = {
    "results": [
        {"expiration_date": "2024-01-19"},
        {"expiration_date": "2024-01-26"},
        {"expiration_date": "2024-02-02"},
    ]
}


class TestPolygonResponseParsing:
    """Tests for parsing Polygon API responses."""

    @patch("options_sim.data.polygon_live.requests")
    def test_get_stock_quote(self, mock_requests):
        """Parse prev close response into quote dict (free tier primary)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_PREV_CLOSE
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        quote = api.get_stock_quote("AAPL")

        assert quote["symbol"] == "AAPL"
        assert quote["price"] == 187.3
        assert quote["open"] == 185.0
        assert quote["high"] == 188.5
        assert quote["low"] == 184.2
        assert quote["volume"] == 42000000

    @patch("options_sim.data.polygon_live.requests")
    def test_get_stock_quote_fallback_to_snapshot(self, mock_requests):
        """Falls back to snapshot if prev close fails."""
        mock_resp_error = MagicMock()
        mock_resp_error.raise_for_status.side_effect = Exception("no prev data")

        mock_resp_snapshot = MagicMock()
        mock_resp_snapshot.json.return_value = MOCK_STOCK_SNAPSHOT
        mock_resp_snapshot.raise_for_status = MagicMock()

        mock_requests.get.side_effect = [mock_resp_error, mock_resp_snapshot]

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        quote = api.get_stock_quote("AAPL")

        assert quote["price"] == 187.3

    @patch("options_sim.data.polygon_live.requests")
    def test_get_underlying_price(self, mock_requests):
        """get_underlying_price returns float price."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_PREV_CLOSE
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        price = api.get_underlying_price("AAPL")
        assert price == 187.3

    @patch("options_sim.data.polygon_live.requests")
    def test_get_option_chain(self, mock_requests):
        """Parse option chain snapshot into list of dicts."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_OPTION_SNAPSHOT
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        chain = api.get_option_chain("AAPL")

        assert len(chain) == 2

        call = chain[0]
        assert call["symbol"] == "AAPL240119C00190000"
        assert call["underlying"] == "AAPL"
        assert call["strike"] == 190.0
        assert call["option_type"] == "call"
        assert call["bid"] == 2.40
        assert call["ask"] == 2.60
        assert call["delta"] == 0.45
        assert call["iv"] == 0.32

        put = chain[1]
        assert put["option_type"] == "put"
        assert put["strike"] == 180.0

    @patch("options_sim.data.polygon_live.requests")
    def test_get_option_quote_single(self, mock_requests):
        """Get quote for a single contract."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": MOCK_OPTION_SNAPSHOT["results"][0]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        quote = api.get_option_quote("AAPL240119C00190000")

        assert quote["symbol"] == "AAPL240119C00190000"
        assert quote["bid"] == 2.40
        assert quote["ask"] == 2.60

    @patch("options_sim.data.polygon_live.requests")
    def test_get_available_expiries(self, mock_requests):
        """Parse expiry dates from contracts endpoint."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_EXPIRIES
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        expiries = api.get_available_expiries("AAPL")

        assert expiries == ["2024-01-19", "2024-01-26", "2024-02-02"]

    @patch("options_sim.data.polygon_live.requests")
    def test_chain_caching(self, mock_requests):
        """Second call uses cache, no extra API request."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_OPTION_SNAPSHOT
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000, chain_cache_ttl=60)
        chain1 = api.get_option_chain("AAPL")
        chain2 = api.get_option_chain("AAPL")

        assert chain1 == chain2
        # Only one API call should have been made
        assert mock_requests.get.call_count == 1

    @patch("options_sim.data.polygon_live.requests")
    def test_quote_caching(self, mock_requests):
        """Stock quotes are cached."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_STOCK_SNAPSHOT
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000, quote_cache_ttl=60)
        q1 = api.get_stock_quote("AAPL")
        q2 = api.get_stock_quote("AAPL")

        assert q1 == q2
        assert mock_requests.get.call_count == 1

    @patch("options_sim.data.polygon_live.requests")
    def test_filter_strikes_around_atm(self, mock_requests):
        """Filter chain to N strikes around ATM."""
        # Build a chain with many strikes
        results = []
        for strike in [170, 175, 180, 185, 190, 195, 200, 205, 210]:
            results.append({
                "details": {
                    "ticker": f"O:AAPL240119C{strike * 1000:08d}",
                    "contract_type": "call",
                    "strike_price": strike,
                    "expiration_date": "2024-01-19",
                },
                "greeks": {"delta": 0.5, "gamma": 0.02, "theta": -0.05, "vega": 0.2},
                "day": {"close": 5.0, "volume": 100},
                "last_quote": {"bid": 4.90, "ask": 5.10},
                "open_interest": 500,
                "implied_volatility": 0.25,
            })

        # Two calls: one for chain, one for underlying price
        mock_chain_resp = MagicMock()
        mock_chain_resp.json.return_value = {"results": results}
        mock_chain_resp.raise_for_status = MagicMock()

        mock_price_resp = MagicMock()
        mock_price_resp.json.return_value = {
            "results": [{"o": 189, "h": 191, "l": 188, "c": 190, "v": 1000000}]
        }
        mock_price_resp.raise_for_status = MagicMock()

        mock_requests.get.side_effect = [mock_chain_resp, mock_price_resp]

        api = PolygonLiveProvider(api_key="test_key", calls_per_minute=6000)
        chain = api.get_option_chain("AAPL", strikes_around_atm=2)

        strikes = set(q["strike"] for q in chain)
        # ATM=190, should get 180, 185, 190, 195, 200 (2 above, 2 below + ATM)
        assert 190 in strikes
        assert len(strikes) <= 5

    def test_parse_option_snapshot_invalid_type(self):
        """Contracts with invalid type are skipped."""
        api = PolygonLiveProvider.__new__(PolygonLiveProvider)
        result = {
            "details": {
                "ticker": "O:AAPL240119X00190000",
                "contract_type": "other",
                "strike_price": 190,
                "expiration_date": "2024-01-19",
            },
            "greeks": {},
            "day": {},
            "last_quote": {},
        }
        assert api._parse_option_snapshot(result, "AAPL") is None


# ─── API Key Retrieval ─────────────────────────────────────────


class TestAPIKeyRetrieval:
    """Tests for API key retrieval."""

    def test_env_var_priority(self):
        """Environment variable takes priority."""
        with patch.dict("os.environ", {"POLYGON_API_KEY": "env_key"}):
            assert get_polygon_key() == "env_key"

    @patch("options_sim.data.polygon_live.subprocess.run")
    def test_keychain_fallback(self, mock_run):
        """Falls back to macOS Keychain when env var not set."""
        mock_run.return_value = MagicMock(returncode=0, stdout="keychain_key\n")
        with patch.dict("os.environ", {}, clear=True):
            # Remove POLYGON_API_KEY if it exists
            import os
            old = os.environ.pop("POLYGON_API_KEY", None)
            try:
                key = get_polygon_key()
                assert key == "keychain_key"
            finally:
                if old:
                    os.environ["POLYGON_API_KEY"] = old

    def test_no_key_available(self):
        """Returns None when no key found."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("options_sim.data.polygon_live.subprocess.run", side_effect=Exception("no keychain")):
                import os
                old = os.environ.pop("POLYGON_API_KEY", None)
                try:
                    assert get_polygon_key() is None
                finally:
                    if old:
                        os.environ["POLYGON_API_KEY"] = old

    def test_provider_requires_key(self):
        """Provider raises if no API key."""
        with patch("options_sim.data.polygon_live.get_polygon_key", return_value=None):
            with pytest.raises(ValueError, match="API key required"):
                PolygonLiveProvider(api_key=None)
