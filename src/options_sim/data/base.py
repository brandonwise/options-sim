"""Abstract base class for data providers.

All data providers must implement this interface. The simulation engine
interacts with market data exclusively through this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from options_sim.data.schema import MarketSnapshot, OptionQuote


class DataProvider(ABC):
    """Abstract interface for options market data providers.

    Implementations include CSV/Parquet file loaders, Polygon.io API,
    ThetaData API, and synthetic data generators.
    """

    @abstractmethod
    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """Get a complete market snapshot at a given timestamp.

        Args:
            symbol: Underlying ticker (e.g. 'SPY').
            timestamp: Point in time to query.

        Returns:
            MarketSnapshot with underlying price and full option chain.

        Raises:
            ValueError: If no data available for the given timestamp.
        """

    @abstractmethod
    def get_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Get option chain for a specific expiry at a given timestamp.

        Args:
            underlying: Underlying ticker (e.g. 'SPY').
            expiry: Expiration date as 'YYYY-MM-DD'.
            timestamp: Point in time to query.

        Returns:
            List of OptionQuote objects for the expiry.
        """

    @abstractmethod
    def get_underlying_price(self, symbol: str, timestamp: datetime) -> float:
        """Get the underlying asset price at a given timestamp.

        Args:
            symbol: Underlying ticker.
            timestamp: Point in time to query.

        Returns:
            Current price of the underlying.
        """

    @abstractmethod
    def get_quote(self, symbol: str, timestamp: datetime) -> OptionQuote | None:
        """Get a single option contract quote at a given timestamp.

        Args:
            symbol: OCC option symbol (e.g. 'SPY240119C00470000').
            timestamp: Point in time to query.

        Returns:
            OptionQuote if found, None otherwise.
        """

    @abstractmethod
    def available_dates(self, symbol: str) -> list[str]:
        """List available trading dates for an underlying.

        Args:
            symbol: Underlying ticker.

        Returns:
            Sorted list of date strings ('YYYY-MM-DD').
        """

    @abstractmethod
    def available_expiries(self, symbol: str, timestamp: datetime) -> list[str]:
        """List available expiration dates at a given timestamp.

        Args:
            symbol: Underlying ticker.
            timestamp: Point in time to query.

        Returns:
            Sorted list of expiration date strings ('YYYY-MM-DD').
        """
