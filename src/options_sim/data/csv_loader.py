"""CSV/Parquet data loader.

Loads historical options data from local CSV or Parquet files.
Expects files following the canonical schema defined in schema.py.

Expected CSV columns:
    timestamp, symbol, underlying, strike, expiry, option_type,
    bid, ask, last, volume, open_interest, iv, delta, gamma, theta, vega
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from options_sim.data.base import DataProvider
from options_sim.data.schema import MarketSnapshot, OptionQuote


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse timestamp string to datetime."""
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str}")


class CsvDataProvider(DataProvider):
    """Load options data from CSV or Parquet files.

    Supports loading a single file or a directory of files.
    Data is indexed by timestamp for fast lookups.

    Args:
        path: Path to a CSV/Parquet file or directory containing them.
        underlying_price_col: Column name for underlying price (optional).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: pd.DataFrame | None = None
        self._underlying_prices: dict[str, dict[str, float]] = {}
        self._load_data()

    def _load_data(self) -> None:
        """Load all data files into memory."""
        frames: list[pd.DataFrame] = []

        if self.path.is_file():
            frames.append(self._load_file(self.path))
        elif self.path.is_dir():
            for f in sorted(self.path.iterdir()):
                if f.suffix in (".csv", ".parquet", ".pq"):
                    frames.append(self._load_file(f))
                # Also check for underlying price file
                if f.name == "underlying.csv":
                    self._load_underlying_prices(f)
        else:
            raise FileNotFoundError(f"Data path not found: {self.path}")

        if not frames:
            raise ValueError(f"No data files found at: {self.path}")

        self._data = pd.concat(frames, ignore_index=True)
        self._data["timestamp"] = pd.to_datetime(self._data["timestamp"])
        self._data = self._data.sort_values("timestamp").reset_index(drop=True)

        # Extract underlying prices from data if not loaded separately
        if not self._underlying_prices:
            self._extract_underlying_prices()

    def _load_file(self, path: Path) -> pd.DataFrame:
        """Load a single data file."""
        if path.suffix in (".parquet", ".pq"):
            return pd.read_parquet(path)
        else:
            return pd.read_csv(path)

    def _load_underlying_prices(self, path: Path) -> None:
        """Load underlying prices from a separate CSV."""
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            symbol = str(row["symbol"])
            ts = str(row["timestamp"])
            price = float(row["price"])
            if symbol not in self._underlying_prices:
                self._underlying_prices[symbol] = {}
            self._underlying_prices[symbol][ts] = price

    def _extract_underlying_prices(self) -> None:
        """Extract unique underlying prices from option data."""
        if self._data is None:
            return
        # Group by underlying and timestamp, use midpoint of nearest-ATM options
        for underlying in self._data["underlying"].unique():
            mask = self._data["underlying"] == underlying
            subset = self._data[mask]
            self._underlying_prices[underlying] = {}
            for ts in subset["timestamp"].unique():
                ts_mask = subset["timestamp"] == ts
                ts_data = subset[ts_mask]
                # Approximate underlying from median strike
                strikes = ts_data["strike"]
                if len(strikes) > 0:
                    # Approximate underlying from put-call parity or use mid-strike
                    mid_strike = strikes.median()
                    self._underlying_prices[underlying][
                        str(ts)
                    ] = float(mid_strike)

    def _find_nearest_timestamp(
        self, symbol: str, timestamp: datetime
    ) -> datetime | None:
        """Find the nearest available timestamp <= requested time."""
        if self._data is None:
            return None
        mask = self._data["underlying"] == symbol
        available = self._data[mask]["timestamp"].unique()
        candidates = [t for t in available if t <= pd.Timestamp(timestamp)]
        if not candidates:
            # Fall back to earliest available
            candidates = sorted(available)
            return candidates[0] if candidates else None
        return max(candidates)

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """Get complete market snapshot at timestamp."""
        if self._data is None:
            raise ValueError("No data loaded")

        nearest = self._find_nearest_timestamp(symbol, timestamp)
        if nearest is None:
            raise ValueError(f"No data for {symbol} at {timestamp}")

        mask = (self._data["underlying"] == symbol) & (
            self._data["timestamp"] == nearest
        )
        rows = self._data[mask]

        chain = [self._row_to_quote(row) for _, row in rows.iterrows()]

        # Get underlying price
        price = self._get_underlying_price_internal(symbol, str(nearest))

        return MarketSnapshot(
            timestamp=nearest.to_pydatetime() if hasattr(nearest, "to_pydatetime") else nearest,
            underlying=symbol,
            underlying_price=price,
            chain=chain,
        )

    def get_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Get option chain for specific expiry."""
        snapshot = self.get_snapshot(underlying, timestamp)
        return snapshot.get_chain_for_expiry(expiry)

    def get_underlying_price(self, symbol: str, timestamp: datetime) -> float:
        """Get underlying price at timestamp."""
        nearest = self._find_nearest_timestamp(symbol, timestamp)
        if nearest is None:
            raise ValueError(f"No data for {symbol} at {timestamp}")
        return self._get_underlying_price_internal(symbol, str(nearest))

    def _get_underlying_price_internal(self, symbol: str, ts_key: str) -> float:
        """Look up underlying price from cache."""
        if symbol in self._underlying_prices:
            prices = self._underlying_prices[symbol]
            if ts_key in prices:
                return prices[ts_key]
            # Find nearest
            if prices:
                keys = sorted(prices.keys())
                for k in reversed(keys):
                    if k <= ts_key:
                        return prices[k]
                return prices[keys[0]]
        return 0.0

    def get_quote(self, symbol: str, timestamp: datetime) -> OptionQuote | None:
        """Get single contract quote."""
        if self._data is None:
            return None

        # Extract underlying from OCC symbol
        underlying = self._extract_underlying(symbol)
        nearest = self._find_nearest_timestamp(underlying, timestamp)
        if nearest is None:
            return None

        mask = (self._data["symbol"] == symbol) & (
            self._data["timestamp"] == nearest
        )
        rows = self._data[mask]
        if rows.empty:
            return None

        return self._row_to_quote(rows.iloc[0])

    def available_dates(self, symbol: str) -> list[str]:
        """List available trading dates."""
        if self._data is None:
            return []
        mask = self._data["underlying"] == symbol
        dates = self._data[mask]["timestamp"].dt.date.unique()
        return sorted(str(d) for d in dates)

    def available_expiries(self, symbol: str, timestamp: datetime) -> list[str]:
        """List available expiration dates."""
        snapshot = self.get_snapshot(symbol, timestamp)
        return snapshot.available_expiries()

    @staticmethod
    def _row_to_quote(row) -> OptionQuote:
        """Convert DataFrame row to OptionQuote."""
        ts = row["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()

        expiry = str(row.get("expiry", ""))
        if hasattr(expiry, "strftime"):
            expiry = expiry.strftime("%Y-%m-%d")

        return OptionQuote(
            timestamp=ts,
            symbol=str(row["symbol"]),
            underlying=str(row["underlying"]),
            strike=float(row["strike"]),
            expiry=expiry,
            option_type=str(row["option_type"]),
            bid=float(row.get("bid", 0)),
            ask=float(row.get("ask", 0)),
            last=float(row.get("last", 0)),
            volume=int(row.get("volume", 0)),
            open_interest=int(row.get("open_interest", 0)),
            iv=float(row.get("iv", 0)),
            delta=float(row.get("delta", 0)),
            gamma=float(row.get("gamma", 0)),
            theta=float(row.get("theta", 0)),
            vega=float(row.get("vega", 0)),
        )

    @staticmethod
    def _extract_underlying(occ_symbol: str) -> str:
        """Extract underlying ticker from OCC option symbol.

        OCC format: SPY240119C00470000
        Underlying is the alphabetic prefix.
        """
        i = 0
        while i < len(occ_symbol) and occ_symbol[i].isalpha():
            i += 1
        return occ_symbol[:i] if i > 0 else occ_symbol
