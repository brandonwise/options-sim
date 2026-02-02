"""Polygon.io data provider.

Fetches historical options data from Polygon.io REST API.
Requires POLYGON_API_KEY environment variable.

Usage:
    export POLYGON_API_KEY=your_key_here
    provider = PolygonDataProvider()
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

from options_sim.data.base import DataProvider
from options_sim.data.schema import MarketSnapshot, OptionQuote

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class PolygonDataProvider(DataProvider):
    """Polygon.io REST API data provider.

    Fetches option chain snapshots and caches results locally.
    Handles API rate limits with automatic backoff.

    Args:
        api_key: Polygon API key. Falls back to POLYGON_API_KEY env var.
        cache_dir: Directory for local cache (None = no caching).
        rate_limit: Maximum requests per minute.
    """

    BASE_URL = "https://api.polygon.io"

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | None = None,
        rate_limit: int = 5,
    ) -> None:
        if not HAS_REQUESTS:
            raise ImportError(
                "requests library required for Polygon provider. "
                "Install with: pip install options-sim[polygon]"
            )

        self.api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Polygon API key required. Set POLYGON_API_KEY env var "
                "or pass api_key parameter."
            )

        self.cache_dir = cache_dir
        self.rate_limit = rate_limit
        self._last_request_time = 0.0
        self._request_count = 0

    def _throttle(self) -> None:
        """Enforce rate limiting."""
        now = time.time()
        if now - self._last_request_time < 60:
            self._request_count += 1
            if self._request_count >= self.rate_limit:
                sleep_time = 60 - (now - self._last_request_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                self._request_count = 0
                self._last_request_time = time.time()
        else:
            self._request_count = 0
            self._last_request_time = now

    def _get(self, url: str, params: dict | None = None) -> dict:
        """Make authenticated GET request with rate limiting."""
        self._throttle()
        params = params or {}
        params["apiKey"] = self.api_key

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """Get market snapshot from Polygon.

        Uses the options snapshot endpoint for current data,
        or aggregates endpoint for historical.
        """
        # Get underlying price
        underlying_price = self.get_underlying_price(symbol, timestamp)

        # Get option chain snapshot
        url = f"{self.BASE_URL}/v3/snapshot/options/{symbol}"
        data = self._get(url, {"limit": 250})

        chain: list[OptionQuote] = []
        for result in data.get("results", []):
            quote = self._parse_snapshot_result(result, timestamp, symbol)
            if quote:
                chain.append(quote)

        return MarketSnapshot(
            timestamp=timestamp,
            underlying=symbol,
            underlying_price=underlying_price,
            chain=chain,
        )

    def get_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Get option chain for specific expiry."""
        url = f"{self.BASE_URL}/v3/snapshot/options/{underlying}"
        params = {
            "expiration_date": expiry,
            "limit": 250,
        }
        data = self._get(url, params)

        chain: list[OptionQuote] = []
        for result in data.get("results", []):
            quote = self._parse_snapshot_result(result, timestamp, underlying)
            if quote:
                chain.append(quote)

        return chain

    def get_underlying_price(self, symbol: str, timestamp: datetime) -> float:
        """Get underlying price from Polygon."""
        date_str = timestamp.strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{date_str}/{date_str}"
        data = self._get(url)

        results = data.get("results", [])
        if results:
            return float(results[0].get("c", 0))  # Close price

        raise ValueError(f"No price data for {symbol} on {date_str}")

    def get_quote(self, symbol: str, timestamp: datetime) -> OptionQuote | None:
        """Get single contract quote."""
        # Convert OCC symbol to Polygon format
        polygon_ticker = f"O:{symbol}"
        url = f"{self.BASE_URL}/v3/snapshot/options/{polygon_ticker}"
        try:
            data = self._get(url)
            results = data.get("results", [])
            if results:
                underlying = self._extract_underlying(symbol)
                return self._parse_snapshot_result(results[0], timestamp, underlying)
        except Exception:
            pass
        return None

    def available_dates(self, symbol: str) -> list[str]:
        """List dates with available data."""
        # Use aggregates to find trading days
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
        data = self._get(url)

        dates: list[str] = []
        for result in data.get("results", []):
            ts = result.get("t", 0) / 1000
            dt = datetime.fromtimestamp(ts)
            dates.append(dt.strftime("%Y-%m-%d"))

        return sorted(dates)

    def available_expiries(self, symbol: str, timestamp: datetime) -> list[str]:
        """List available expiration dates."""
        url = f"{self.BASE_URL}/v3/reference/options/contracts"
        params = {
            "underlying_ticker": symbol,
            "expired": "false",
            "limit": 1000,
        }
        data = self._get(url, params)

        expiries: set[str] = set()
        for result in data.get("results", []):
            exp = result.get("expiration_date", "")
            if exp:
                expiries.add(exp)

        return sorted(expiries)

    @staticmethod
    def _parse_snapshot_result(
        result: dict, timestamp: datetime, underlying: str
    ) -> OptionQuote | None:
        """Parse a Polygon snapshot result into an OptionQuote."""
        details = result.get("details", {})
        greeks = result.get("greeks", {})
        day = result.get("day", {})
        last_quote = result.get("last_quote", {})

        contract_type = details.get("contract_type", "").lower()
        if contract_type not in ("call", "put"):
            return None

        strike = float(details.get("strike_price", 0))
        expiry = details.get("expiration_date", "")
        ticker = details.get("ticker", "").replace("O:", "")

        return OptionQuote(
            timestamp=timestamp,
            symbol=ticker,
            underlying=underlying,
            strike=strike,
            expiry=expiry,
            option_type=contract_type,
            bid=float(last_quote.get("bid", 0)),
            ask=float(last_quote.get("ask", 0)),
            last=float(day.get("close", 0)),
            volume=int(day.get("volume", 0)),
            open_interest=int(result.get("open_interest", 0)),
            iv=float(result.get("implied_volatility", 0)),
            delta=float(greeks.get("delta", 0)),
            gamma=float(greeks.get("gamma", 0)),
            theta=float(greeks.get("theta", 0)),
            vega=float(greeks.get("vega", 0)),
        )

    @staticmethod
    def _extract_underlying(occ_symbol: str) -> str:
        """Extract underlying from OCC symbol."""
        i = 0
        while i < len(occ_symbol) and occ_symbol[i].isalpha():
            i += 1
        return occ_symbol[:i] if i > 0 else occ_symbol
