"""Live Polygon.io data provider.

Fetches real-time/delayed market data from Polygon.io REST API.
Includes rate limiting (5 calls/min free tier), caching with TTL,
and macOS Keychain integration for API key retrieval.

Usage:
    from options_sim.data.polygon_live import PolygonLiveProvider
    api = PolygonLiveProvider()
    price = api.get_underlying_price("AAPL")
    chain = api.get_option_chain("AAPL")
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def get_polygon_key() -> str | None:
    """Retrieve Polygon API key from env var or macOS Keychain.

    Returns:
        API key string, or None if not found.
    """
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                "clawdbot",
                "-s",
                "POLYGON_API_KEY",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def polygon_ticker_to_occ(polygon_ticker: str) -> str:
    """Convert Polygon option ticker to OCC standard format.

    Polygon format: O:SPY240119C00475000 or O:AAPL250117C00200000
    OCC format:     SPY240119C00475000

    Args:
        polygon_ticker: Polygon-format option ticker.

    Returns:
        OCC-standard option symbol.
    """
    ticker = polygon_ticker.replace("O:", "")
    # Already in OCC format if it starts with letters followed by digits
    return ticker


def occ_to_polygon_ticker(occ_symbol: str) -> str:
    """Convert OCC option symbol to Polygon format.

    OCC format:     SPY240119C00475000
    Polygon format: O:SPY240119C00475000

    Args:
        occ_symbol: OCC-standard option symbol.

    Returns:
        Polygon-format option ticker.
    """
    if occ_symbol.startswith("O:"):
        return occ_symbol
    return f"O:{occ_symbol}"


def extract_underlying_from_occ(occ_symbol: str) -> str:
    """Extract underlying ticker from OCC option symbol.

    OCC format: SPY240119C00475000
    The alphabetic prefix is the underlying.

    Args:
        occ_symbol: OCC option symbol.

    Returns:
        Underlying ticker (e.g., 'SPY').
    """
    occ_symbol = occ_symbol.replace("O:", "")
    i = 0
    while i < len(occ_symbol) and occ_symbol[i].isalpha():
        i += 1
    return occ_symbol[:i].upper() if i > 0 else occ_symbol.upper()


def parse_occ_symbol(occ_symbol: str) -> dict:
    """Parse OCC option symbol into components.

    Format: SPY240119C00475000
    - underlying: SPY
    - expiry: 2024-01-19  (YYMMDD -> YYYY-MM-DD)
    - option_type: call/put  (C/P)
    - strike: 475.0  (00475000 / 1000)

    Args:
        occ_symbol: OCC option symbol.

    Returns:
        Dict with underlying, expiry, option_type, strike.
    """
    occ_symbol = occ_symbol.replace("O:", "")
    match = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", occ_symbol)
    if not match:
        return {}
    underlying = match.group(1)
    date_str = match.group(2)
    cp = match.group(3)
    strike_raw = match.group(4)

    # Convert YYMMDD to YYYY-MM-DD
    yy = int(date_str[:2])
    mm = int(date_str[2:4])
    dd = int(date_str[4:6])
    year = 2000 + yy
    expiry = f"{year:04d}-{mm:02d}-{dd:02d}"

    return {
        "underlying": underlying,
        "expiry": expiry,
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
    }


class TTLCache:
    """Simple in-memory cache with TTL (time-to-live).

    Args:
        default_ttl: Default cache duration in seconds.
    """

    def __init__(self, default_ttl: float = 60.0) -> None:
        self.default_ttl = default_ttl
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        """Get cached value if not expired."""
        if key in self._store:
            expiry, value = self._store[key]
            if time.time() < expiry:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: object, ttl: float | None = None) -> None:
        """Store value with TTL."""
        ttl = ttl if ttl is not None else self.default_ttl
        self._store[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    def invalidate(self, key: str) -> None:
        """Remove a specific cache entry."""
        self._store.pop(key, None)


class RateLimiter:
    """Simple rate limiter using token bucket approach.

    Args:
        calls_per_minute: Maximum API calls per minute.
    """

    def __init__(self, calls_per_minute: int = 5) -> None:
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self._last_call_time: float = 0.0

    def wait(self) -> None:
        """Block until a call is allowed under the rate limit."""
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            time.sleep(sleep_time)
        self._last_call_time = time.time()


class PolygonLiveProvider:
    """Live market data provider using Polygon.io REST API.

    Fetches real-time (or 15-min delayed on free tier) stock and
    options data. Implements rate limiting and caching to stay
    within free tier limits (5 calls/minute).

    Args:
        api_key: Polygon API key. If None, retrieves from env/Keychain.
        calls_per_minute: Rate limit (default 5 for free tier).
        quote_cache_ttl: Cache TTL for stock quotes in seconds.
        chain_cache_ttl: Cache TTL for option chains in seconds.
    """

    BASE_URL = "https://api.polygon.io"

    def __init__(
        self,
        api_key: str | None = None,
        calls_per_minute: int = 5,
        quote_cache_ttl: float = 60.0,
        chain_cache_ttl: float = 120.0,
    ) -> None:
        if not HAS_REQUESTS:
            raise ImportError(
                "requests library required. Install with: pip install requests"
            )

        self.api_key = api_key or get_polygon_key()
        if not self.api_key:
            raise ValueError(
                "Polygon API key required. Set POLYGON_API_KEY env var, "
                "store in macOS Keychain, or pass api_key parameter."
            )

        self._rate_limiter = RateLimiter(calls_per_minute)
        self._quote_cache = TTLCache(default_ttl=quote_cache_ttl)
        self._chain_cache = TTLCache(default_ttl=chain_cache_ttl)
        self._quote_cache_ttl = quote_cache_ttl
        self._chain_cache_ttl = chain_cache_ttl

    def _get(self, url: str, params: dict | None = None) -> dict:
        """Make authenticated GET request with rate limiting.

        Args:
            url: Full URL to request.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            requests.HTTPError: On API errors.
        """
        self._rate_limiter.wait()
        params = params or {}
        params["apiKey"] = self.api_key

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_stock_quote(self, symbol: str) -> dict:
        """Get comprehensive stock quote data.

        Tries the free-tier prev-close endpoint first, then falls back
        to the snapshot endpoint (requires Stocks subscription).

        Args:
            symbol: Stock ticker (e.g., 'AAPL').

        Returns:
            Dict with price, open, high, low, volume, change, etc.
        """
        symbol = symbol.upper()
        cached = self._quote_cache.get(f"stock:{symbol}")
        if cached is not None:
            return cached

        # Try free-tier endpoint first (prev day close)
        try:
            return self._get_prev_close_quote(symbol)
        except Exception:
            pass

        # Fall back to snapshot (requires subscription)
        url = f"{self.BASE_URL}/v3/snapshot"
        data = self._get(url, {"ticker.any_of": symbol})

        results = data.get("results", [])
        if not results:
            raise ValueError(f"No price data for {symbol}")

        result = results[0]
        session = result.get("session", {})
        prev = result.get("prev_day", {})

        price = session.get("close") or session.get("price") or prev.get("close", 0)
        prev_close = prev.get("close", 0)
        change = price - prev_close if prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0

        quote = {
            "symbol": symbol,
            "price": round(float(price), 2),
            "open": round(float(session.get("open", 0)), 2),
            "high": round(float(session.get("high", 0)), 2),
            "low": round(float(session.get("low", 0)), 2),
            "volume": int(session.get("volume", 0)),
            "prev_close": round(float(prev_close), 2),
            "change": round(float(change), 2),
            "change_pct": round(float(change_pct), 2),
            "timestamp": result.get("last_updated", datetime.now().isoformat()),
        }

        self._quote_cache.set(f"stock:{symbol}", quote, self._quote_cache_ttl)
        return quote

    def _get_prev_close_quote(self, symbol: str) -> dict:
        """Fallback: get stock data from previous close endpoint.

        Args:
            symbol: Stock ticker.

        Returns:
            Dict with price data.
        """
        url = f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/prev"
        data = self._get(url)

        results = data.get("results", [])
        if not results:
            raise ValueError(f"No price data for {symbol}")

        r = results[0]
        prev_close = float(r.get("c", 0))

        quote = {
            "symbol": symbol,
            "price": round(prev_close, 2),
            "open": round(float(r.get("o", 0)), 2),
            "high": round(float(r.get("h", 0)), 2),
            "low": round(float(r.get("l", 0)), 2),
            "volume": int(r.get("v", 0)),
            "prev_close": round(prev_close, 2),
            "change": 0.0,
            "change_pct": 0.0,
            "timestamp": datetime.now().isoformat(),
        }

        self._quote_cache.set(f"stock:{symbol}", quote, self._quote_cache_ttl)
        return quote

    def get_underlying_price(self, symbol: str) -> float:
        """Get current underlying price.

        Args:
            symbol: Stock ticker (e.g., 'AAPL').

        Returns:
            Current price as float.
        """
        quote = self.get_stock_quote(symbol)
        return float(quote["price"])

    def get_option_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        strikes_around_atm: int | None = None,
    ) -> list[dict]:
        """Get live option chain with Greeks.

        Uses the snapshot endpoint which returns full chain in one call
        including bid, ask, Greeks, IV, volume, and OI.

        Args:
            symbol: Underlying ticker (e.g., 'AAPL').
            expiry: Filter to specific expiry date (YYYY-MM-DD). None = all.
            strikes_around_atm: Limit to N strikes around ATM. None = all.

        Returns:
            List of normalized option quote dicts.
        """
        symbol = symbol.upper()
        cache_key = f"chain:{symbol}:{expiry or 'all'}"
        cached = self._chain_cache.get(cache_key)
        if cached is not None:
            chain = cached
        else:
            chain = self._fetch_full_chain(symbol, expiry)
            self._chain_cache.set(cache_key, chain, self._chain_cache_ttl)

        # Filter by strikes around ATM
        if strikes_around_atm and chain:
            try:
                underlying_price = self.get_underlying_price(symbol)
                chain = self._filter_strikes_around_atm(
                    chain, underlying_price, strikes_around_atm
                )
            except Exception:
                pass

        return chain

    def _fetch_full_chain(
        self, symbol: str, expiry: str | None = None
    ) -> list[dict]:
        """Fetch full option chain from Polygon snapshot endpoint.

        Args:
            symbol: Underlying ticker.
            expiry: Optional expiry filter.

        Returns:
            List of normalized option quote dicts.
        """
        url = f"{self.BASE_URL}/v3/snapshot/options/{symbol}"
        params: dict = {"limit": 250}
        if expiry:
            params["expiration_date"] = expiry

        all_results: list[dict] = []
        while url:
            data = self._get(url, params)
            results = data.get("results", [])
            all_results.extend(results)

            # Handle pagination
            next_url = data.get("next_url")
            if next_url and len(all_results) < 2000:  # Safety cap
                url = next_url
                params = {}  # next_url includes params
            else:
                url = None

        chain = []
        for result in all_results:
            quote = self._parse_option_snapshot(result, symbol)
            if quote:
                chain.append(quote)

        return chain

    def _parse_option_snapshot(self, result: dict, underlying: str) -> dict | None:
        """Parse a Polygon option snapshot result into normalized dict.

        Args:
            result: Raw Polygon API result dict.
            underlying: Underlying ticker.

        Returns:
            Normalized option quote dict, or None if invalid.
        """
        details = result.get("details", {})
        greeks = result.get("greeks", {})
        day = result.get("day", {})
        last_quote = result.get("last_quote", {})

        contract_type = details.get("contract_type", "").lower()
        if contract_type not in ("call", "put"):
            return None

        strike = float(details.get("strike_price", 0))
        expiry = details.get("expiration_date", "")
        polygon_ticker = details.get("ticker", "")
        occ_symbol = polygon_ticker_to_occ(polygon_ticker)

        bid = float(last_quote.get("bid", 0))
        ask = float(last_quote.get("ask", 0))
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0

        return {
            "symbol": occ_symbol,
            "underlying": underlying.upper(),
            "strike": strike,
            "expiry": expiry,
            "option_type": contract_type,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "mid": round(mid, 4),
            "last": round(float(day.get("close", 0)), 2),
            "volume": int(day.get("volume", 0)),
            "open_interest": int(result.get("open_interest", 0)),
            "iv": round(float(result.get("implied_volatility", 0)), 6),
            "delta": round(float(greeks.get("delta", 0)), 6),
            "gamma": round(float(greeks.get("gamma", 0)), 6),
            "theta": round(float(greeks.get("theta", 0)), 6),
            "vega": round(float(greeks.get("vega", 0)), 6),
            "timestamp": last_quote.get(
                "last_updated",
                datetime.now().isoformat(),
            ),
        }

    def get_option_quote(self, contract_symbol: str) -> dict:
        """Get quote for a single option contract.

        Args:
            contract_symbol: OCC option symbol (e.g., 'SPY240119C00475000').

        Returns:
            Normalized option quote dict.

        Raises:
            ValueError: If no quote found.
        """
        contract_symbol = contract_symbol.replace("O:", "")
        cached = self._quote_cache.get(f"option:{contract_symbol}")
        if cached is not None:
            return cached

        underlying = extract_underlying_from_occ(contract_symbol)
        polygon_ticker = occ_to_polygon_ticker(contract_symbol)

        url = f"{self.BASE_URL}/v3/snapshot/options/{underlying}/{polygon_ticker}"
        data = self._get(url)

        results = data.get("results", {})
        if not results:
            raise ValueError(f"No quote found for {contract_symbol}")

        # Single contract returns the result directly (not in a list)
        if isinstance(results, list):
            if not results:
                raise ValueError(f"No quote found for {contract_symbol}")
            result = results[0]
        else:
            result = results

        quote = self._parse_option_snapshot(result, underlying)
        if not quote:
            raise ValueError(f"Could not parse quote for {contract_symbol}")

        self._quote_cache.set(
            f"option:{contract_symbol}", quote, self._quote_cache_ttl
        )
        return quote

    def get_available_expiries(self, symbol: str) -> list[str]:
        """Get available expiration dates for an underlying.

        Args:
            symbol: Underlying ticker.

        Returns:
            Sorted list of expiry dates (YYYY-MM-DD).
        """
        symbol = symbol.upper()
        cached = self._chain_cache.get(f"expiries:{symbol}")
        if cached is not None:
            return cached

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

        result_list = sorted(expiries)
        self._chain_cache.set(f"expiries:{symbol}", result_list, self._chain_cache_ttl)
        return result_list

    @staticmethod
    def _filter_strikes_around_atm(
        chain: list[dict], underlying_price: float, n_strikes: int
    ) -> list[dict]:
        """Filter chain to N strikes above and below ATM.

        Args:
            chain: Full option chain.
            underlying_price: Current underlying price.
            n_strikes: Number of strikes above and below ATM.

        Returns:
            Filtered chain.
        """
        if not chain:
            return chain

        # Get unique strikes
        strikes = sorted(set(q["strike"] for q in chain))
        if not strikes:
            return chain

        # Find ATM strike
        atm_idx = min(
            range(len(strikes)),
            key=lambda i: abs(strikes[i] - underlying_price),
        )

        # Select range
        low_idx = max(0, atm_idx - n_strikes)
        high_idx = min(len(strikes), atm_idx + n_strikes + 1)
        selected_strikes = set(strikes[low_idx:high_idx])

        return [q for q in chain if q["strike"] in selected_strikes]
