"""ThetaData API provider.

Fetches historical options data from ThetaData's REST API.
Requires THETADATA_API_KEY environment variable.

Usage:
    export THETADATA_API_KEY=your_key_here
    provider = ThetaDataProvider()
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from options_sim.data.base import DataProvider
from options_sim.data.schema import MarketSnapshot, OptionQuote

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class ThetaDataProvider(DataProvider):
    """ThetaData REST API data provider.

    Provides access to historical options quotes and OHLCV data.

    Args:
        api_key: ThetaData API key. Falls back to THETADATA_API_KEY env var.
        rate_limit: Maximum requests per second.
    """

    BASE_URL = "http://127.0.0.1:25510"  # ThetaData Terminal default

    def __init__(
        self,
        api_key: str | None = None,
        use_cloud: bool = False,
        rate_limit: int = 10,
    ) -> None:
        if not HAS_REQUESTS:
            raise ImportError(
                "requests library required for ThetaData provider. "
                "Install with: pip install requests"
            )

        self.api_key = api_key or os.environ.get("THETADATA_API_KEY", "")
        if use_cloud and not self.api_key:
            raise ValueError(
                "ThetaData API key required for cloud mode. "
                "Set THETADATA_API_KEY env var."
            )

        self.use_cloud = use_cloud
        if use_cloud:
            self.BASE_URL = "https://api.thetadata.us"
        self.rate_limit = rate_limit
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        """Enforce rate limiting."""
        now = time.time()
        min_interval = 1.0 / self.rate_limit
        elapsed = now - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make GET request."""
        self._throttle()
        params = params or {}
        if self.api_key:
            params["apikey"] = self.api_key

        url = f"{self.BASE_URL}{endpoint}"
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """Get market snapshot from ThetaData."""
        underlying_price = self.get_underlying_price(symbol, timestamp)

        # Get all options for the underlying
        data = self._get(
            "/v2/bulk_snapshot/option/quote",
            {"root": symbol, "exp": "0"},  # All expirations
        )

        chain = self._parse_bulk_response(data, timestamp, symbol)

        return MarketSnapshot(
            timestamp=timestamp,
            underlying=symbol,
            underlying_price=underlying_price,
            chain=chain,
        )

    def get_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Get chain for specific expiry."""
        exp_str = expiry.replace("-", "")
        data = self._get(
            "/v2/bulk_snapshot/option/quote",
            {"root": underlying, "exp": exp_str},
        )
        return self._parse_bulk_response(data, timestamp, underlying)

    def get_underlying_price(self, symbol: str, timestamp: datetime) -> float:
        """Get underlying price."""
        date_str = timestamp.strftime("%Y%m%d")
        data = self._get(
            "/hist/stock/eod",
            {"root": symbol, "start_date": date_str, "end_date": date_str},
        )
        response = data.get("response", [])
        if response:
            # ThetaData returns [date, open, high, low, close, volume]
            return float(response[0].get("close", response[0].get("c", 0)))
        raise ValueError(f"No price data for {symbol}")

    def get_quote(self, symbol: str, timestamp: datetime) -> OptionQuote | None:
        """Get single contract quote."""
        underlying = self._extract_underlying(symbol)
        try:
            snapshot = self.get_snapshot(underlying, timestamp)
            return snapshot.get_quote(symbol)
        except Exception:
            return None

    def available_dates(self, symbol: str) -> list[str]:
        """List available dates."""
        data = self._get("/v2/list/dates/stock/eod", {"root": symbol})
        dates = data.get("response", [])
        return sorted(str(d) for d in dates)

    def available_expiries(self, symbol: str, timestamp: datetime) -> list[str]:
        """List available expirations."""
        data = self._get("/v2/list/expirations", {"root": symbol})
        exps = data.get("response", [])
        return sorted(
            f"{str(e)[:4]}-{str(e)[4:6]}-{str(e)[6:8]}"
            for e in exps
            if len(str(e)) == 8
        )

    def _parse_bulk_response(
        self, data: dict, timestamp: datetime, underlying: str
    ) -> list[OptionQuote]:
        """Parse ThetaData bulk response."""
        chain: list[OptionQuote] = []
        for item in data.get("response", []):
            try:
                contract = item.get("contract", {})
                strike = float(contract.get("strike", 0)) / 1000  # ThetaData uses millis
                right = contract.get("right", "").upper()
                option_type = "call" if right == "C" else "put"
                exp = str(contract.get("exp", ""))
                expiry = f"{exp[:4]}-{exp[4:6]}-{exp[6:8]}" if len(exp) == 8 else exp

                # Build OCC symbol
                strike_int = int(strike * 1000)
                occ = f"{underlying}{exp[2:]}{right[0]}{strike_int:08d}"

                quote = item.get("quote", {})
                chain.append(
                    OptionQuote(
                        timestamp=timestamp,
                        symbol=occ,
                        underlying=underlying,
                        strike=strike,
                        expiry=expiry,
                        option_type=option_type,
                        bid=float(quote.get("bid", 0)),
                        ask=float(quote.get("ask", 0)),
                        last=float(quote.get("last", 0)),
                        volume=int(quote.get("volume", 0)),
                        open_interest=int(item.get("open_interest", 0)),
                        iv=float(item.get("iv", 0)),
                        delta=float(item.get("delta", 0)),
                        gamma=float(item.get("gamma", 0)),
                        theta=float(item.get("theta", 0)),
                        vega=float(item.get("vega", 0)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue

        return chain

    @staticmethod
    def _extract_underlying(occ_symbol: str) -> str:
        """Extract underlying from OCC symbol."""
        i = 0
        while i < len(occ_symbol) and occ_symbol[i].isalpha():
            i += 1
        return occ_symbol[:i] if i > 0 else occ_symbol
