"""Tests for the options scanner module.

Uses mock data — no real API calls.
"""

from __future__ import annotations

import pytest

from options_sim.scanner import (
    _percentile_rank,
    scan_earnings_plays,
    scan_high_iv,
    scan_high_theta,
    scan_near_money,
    scan_unusual_volume,
)


# ─── Mock API ──────────────────────────────────────────────────


class MockScannerAPI:
    """Mock API that returns a configurable chain."""

    def __init__(self, chain=None, underlying_price=100.0):
        self._chain = chain or []
        self._underlying_price = underlying_price

    def get_option_chain(self, symbol, expiry=None, strikes_around_atm=None):
        chain = self._chain
        if expiry:
            chain = [q for q in chain if q.get("expiry") == expiry]
        return chain

    def get_underlying_price(self, symbol):
        return self._underlying_price


def _make_chain():
    """Generate a diverse mock chain for testing."""
    chain = []
    for strike in [90, 95, 100, 105, 110]:
        for opt_type in ["call", "put"]:
            cp = "C" if opt_type == "call" else "P"
            iv = 0.20 + (abs(strike - 100) * 0.005)
            # Make 110 strike have very high IV
            if strike == 110:
                iv = 0.60
            # Make 95 strike have unusual volume
            volume = 500
            oi = 2000
            if strike == 95 and opt_type == "call":
                volume = 5000  # vol/OI = 2.5x
            chain.append({
                "symbol": f"AAPL240119{cp}{strike * 1000:08d}",
                "underlying": "AAPL",
                "strike": float(strike),
                "expiry": "2024-01-19",
                "option_type": opt_type,
                "bid": max(0.10, round(5.0 - abs(strike - 100) * 0.4, 2)),
                "ask": max(0.20, round(5.0 - abs(strike - 100) * 0.4 + 0.10, 2)),
                "mid": max(0.15, round(5.0 - abs(strike - 100) * 0.4 + 0.05, 2)),
                "volume": volume,
                "open_interest": oi,
                "iv": round(iv, 4),
                "delta": 0.5 if opt_type == "call" else -0.5,
                "gamma": 0.03,
                "theta": round(-0.05 - (100 - strike) * 0.001, 4) if strike <= 100 else round(-0.03, 4),
                "vega": 0.20,
            })

    # Add a far-term expiry with slightly different IV
    for strike in [95, 100, 105]:
        cp = "C"
        chain.append({
            "symbol": f"AAPL240216C{strike * 1000:08d}",
            "underlying": "AAPL",
            "strike": float(strike),
            "expiry": "2024-02-16",
            "option_type": "call",
            "bid": 3.0,
            "ask": 3.20,
            "mid": 3.10,
            "volume": 200,
            "open_interest": 1000,
            "iv": 0.30,
            "delta": 0.55,
            "gamma": 0.02,
            "theta": -0.03,
            "vega": 0.25,
        })

    return chain


@pytest.fixture
def mock_chain():
    return _make_chain()


@pytest.fixture
def mock_api(mock_chain):
    return MockScannerAPI(chain=mock_chain, underlying_price=100.0)


# ─── High IV Scan ──────────────────────────────────────────────


class TestScanHighIV:
    """Tests for high IV scanner."""

    def test_finds_high_iv(self, mock_api):
        """Finds contracts above IV percentile."""
        results = scan_high_iv("AAPL", mock_api, threshold_percentile=50)
        assert len(results) > 0
        # All results should have IV above median
        for r in results:
            assert r["iv"] > 0

    def test_sorted_by_iv_descending(self, mock_api):
        """Results sorted by IV descending."""
        results = scan_high_iv("AAPL", mock_api, threshold_percentile=0)
        ivs = [r["iv"] for r in results]
        assert ivs == sorted(ivs, reverse=True)

    def test_highest_iv_is_110_strike(self, mock_api):
        """110 strike has highest IV (0.60)."""
        results = scan_high_iv("AAPL", mock_api, threshold_percentile=80)
        assert len(results) > 0
        assert results[0]["strike"] == 110.0

    def test_scan_type_label(self, mock_api):
        """Results include scan_type."""
        results = scan_high_iv("AAPL", mock_api)
        if results:
            assert results[0]["scan_type"] == "high_iv"

    def test_empty_chain(self):
        """Empty chain returns empty results."""
        api = MockScannerAPI(chain=[])
        assert scan_high_iv("AAPL", api) == []

    def test_filter_by_expiry(self, mock_api):
        """Filters by expiry date."""
        results = scan_high_iv("AAPL", mock_api, expiry="2024-01-19")
        for r in results:
            assert r["expiry"] == "2024-01-19"


# ─── Unusual Volume Scan ──────────────────────────────────────


class TestScanUnusualVolume:
    """Tests for unusual volume scanner."""

    def test_finds_unusual_volume(self, mock_api):
        """Finds contracts with vol > 2x OI."""
        results = scan_unusual_volume("AAPL", mock_api, volume_oi_ratio=2.0)
        assert len(results) > 0

    def test_95_call_flagged(self, mock_api):
        """95 call with vol=5000 / OI=2000 = 2.5x ratio."""
        results = scan_unusual_volume("AAPL", mock_api, volume_oi_ratio=2.0)
        symbols = [r["symbol"] for r in results]
        assert any("95000" in s and "C" in s for s in symbols)

    def test_sorted_by_ratio(self, mock_api):
        """Results sorted by vol/OI ratio descending."""
        results = scan_unusual_volume("AAPL", mock_api, volume_oi_ratio=0.1)
        ratios = [r["volume_oi_ratio"] for r in results]
        assert ratios == sorted(ratios, reverse=True)

    def test_scan_type_label(self, mock_api):
        """Results include scan_type."""
        results = scan_unusual_volume("AAPL", mock_api)
        if results:
            assert results[0]["scan_type"] == "unusual_volume"

    def test_empty_chain(self):
        """Empty chain returns empty."""
        api = MockScannerAPI(chain=[])
        assert scan_unusual_volume("AAPL", api) == []


# ─── Near Money Scan ──────────────────────────────────────────


class TestScanNearMoney:
    """Tests for near-money scanner."""

    def test_finds_near_money(self, mock_api):
        """Finds contracts within 5% of underlying."""
        results = scan_near_money("AAPL", mock_api, range_pct=5.0)
        assert len(results) > 0

    def test_all_within_range(self, mock_api):
        """All results are within the specified range."""
        results = scan_near_money("AAPL", mock_api, range_pct=3.0)
        for r in results:
            assert abs(r["strike"] - 100) / 100 * 100 <= 3.5  # Small tolerance

    def test_sorted_by_distance(self, mock_api):
        """Results sorted by distance from ATM ascending."""
        results = scan_near_money("AAPL", mock_api, range_pct=10.0)
        distances = [r["distance_from_atm_pct"] for r in results]
        assert distances == sorted(distances)

    def test_includes_underlying_price(self, mock_api):
        """Results include underlying_price."""
        results = scan_near_money("AAPL", mock_api)
        if results:
            assert results[0]["underlying_price"] == 100.0

    def test_scan_type_label(self, mock_api):
        """Results include scan_type."""
        results = scan_near_money("AAPL", mock_api)
        if results:
            assert results[0]["scan_type"] == "near_money"


# ─── High Theta Scan ──────────────────────────────────────────


class TestScanHighTheta:
    """Tests for high theta scanner."""

    def test_finds_high_theta(self, mock_api):
        """Finds contracts with above-median theta."""
        results = scan_high_theta("AAPL", mock_api)
        assert len(results) > 0

    def test_sorted_by_abs_theta(self, mock_api):
        """Results sorted by absolute theta descending."""
        results = scan_high_theta("AAPL", mock_api)
        thetas = [r["abs_theta"] for r in results]
        assert thetas == sorted(thetas, reverse=True)

    def test_scan_type_label(self, mock_api):
        """Results include scan_type."""
        results = scan_high_theta("AAPL", mock_api)
        if results:
            assert results[0]["scan_type"] == "high_theta"


# ─── Earnings Scan ─────────────────────────────────────────────


class TestScanEarnings:
    """Tests for earnings/near-term scan."""

    def test_finds_near_term(self):
        """Finds near-term options (within max_dte)."""
        from datetime import datetime, timedelta

        # Use future dates for this test
        future_1 = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        future_2 = (datetime.now() + timedelta(days=25)).strftime("%Y-%m-%d")
        chain = [
            {
                "symbol": "AAPL260119C00100000",
                "expiry": future_1,
                "iv": 0.35,
                "bid": 2.50,
                "volume": 500,
            },
            {
                "symbol": "AAPL260216C00100000",
                "expiry": future_2,
                "iv": 0.40,
                "bid": 3.00,
                "volume": 300,
            },
        ]
        api = MockScannerAPI(chain=chain)
        results = scan_earnings_plays("AAPL", api, max_dte=30)
        assert len(results) == 2

    def test_sorted_by_expiry(self):
        """Results sorted by DTE ascending."""
        from datetime import datetime, timedelta

        future_1 = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        future_2 = (datetime.now() + timedelta(days=25)).strftime("%Y-%m-%d")
        chain = [
            {"symbol": "A", "expiry": future_2, "iv": 0.3, "bid": 1.0, "volume": 100},
            {"symbol": "B", "expiry": future_1, "iv": 0.3, "bid": 1.0, "volume": 100},
        ]
        api = MockScannerAPI(chain=chain)
        results = scan_earnings_plays("AAPL", api, max_dte=30)
        dtes = [r["dte"] for r in results]
        assert dtes == sorted(dtes)

    def test_scan_type_label(self):
        """Results include scan_type."""
        from datetime import datetime, timedelta

        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        chain = [{"symbol": "X", "expiry": future, "iv": 0.3, "bid": 1.0, "volume": 100}]
        api = MockScannerAPI(chain=chain)
        results = scan_earnings_plays("AAPL", api, max_dte=30)
        assert len(results) > 0
        assert results[0]["scan_type"] == "earnings"


# ─── Helper Functions ──────────────────────────────────────────


class TestHelperFunctions:
    """Tests for scanner helper functions."""

    def test_percentile_rank_lowest(self):
        """Lowest value is 0th percentile."""
        assert _percentile_rank(1, [1, 2, 3, 4, 5]) == 0.0

    def test_percentile_rank_highest(self):
        """Highest value is 80th percentile (4 below out of 5)."""
        assert _percentile_rank(5, [1, 2, 3, 4, 5]) == 80.0

    def test_percentile_rank_middle(self):
        """Middle value has correct rank."""
        assert _percentile_rank(3, [1, 2, 3, 4, 5]) == 40.0

    def test_percentile_rank_empty(self):
        """Empty list returns 0."""
        assert _percentile_rank(1, []) == 0.0
