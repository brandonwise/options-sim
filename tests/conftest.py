"""Shared test fixtures for options-sim tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from options_sim.data.base import DataProvider
from options_sim.data.schema import MarketSnapshot, OptionQuote
from options_sim.engine import OptionsSimulator
from options_sim.pricing import calculate_greeks


class MockDataProvider(DataProvider):
    """In-memory data provider for tests.

    Generates synthetic option chain data on-the-fly using BSM pricing.
    No files or API calls needed.
    """

    def __init__(
        self,
        underlying: str = "SPY",
        base_price: float = 475.0,
        base_iv: float = 0.20,
        strikes: list[float] | None = None,
        expiries: list[str] | None = None,
    ) -> None:
        self.underlying = underlying
        self.base_price = base_price
        self.base_iv = base_iv
        self.strikes = strikes or [
            460.0, 465.0, 470.0, 475.0, 480.0, 485.0, 490.0,
        ]
        self.expiries = expiries or ["2024-01-19", "2024-01-26", "2024-02-02"]
        self._price_offset = 0.0  # For simulating price movement

    def set_price(self, price: float) -> None:
        """Override underlying price for testing."""
        self.base_price = price

    def advance_price(self, change: float) -> None:
        """Move the price by a given amount."""
        self.base_price += change

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        """Generate synthetic snapshot."""
        chain = []
        for expiry in self.expiries:
            chain.extend(self._generate_chain(symbol, expiry, timestamp))

        return MarketSnapshot(
            timestamp=timestamp,
            underlying=symbol,
            underlying_price=self.base_price,
            chain=chain,
        )

    def get_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Generate chain for specific expiry."""
        return self._generate_chain(underlying, expiry, timestamp)

    def get_underlying_price(self, symbol: str, timestamp: datetime) -> float:
        """Return current base price."""
        return self.base_price

    def get_quote(self, symbol: str, timestamp: datetime) -> OptionQuote | None:
        """Get single quote by parsing the OCC symbol."""
        underlying = self._extract_underlying(symbol)
        snapshot = self.get_snapshot(underlying, timestamp)
        return snapshot.get_quote(symbol)

    def available_dates(self, symbol: str) -> list[str]:
        """Return a fixed set of dates."""
        return ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"]

    def available_expiries(self, symbol: str, timestamp: datetime) -> list[str]:
        """Return configured expiries."""
        return self.expiries

    def _generate_chain(
        self, underlying: str, expiry: str, timestamp: datetime
    ) -> list[OptionQuote]:
        """Generate option chain with BSM pricing."""
        quotes = []
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
        T = max((expiry_dt - timestamp).total_seconds() / (365.25 * 86400), 1e-10)

        for strike in self.strikes:
            for opt_type in ("call", "put"):
                greeks = calculate_greeks(
                    S=self.base_price,
                    K=strike,
                    T=T,
                    r=0.05,
                    sigma=self.base_iv,
                    option_type=opt_type,
                )

                theo = greeks.price
                if theo < 0.01:
                    theo = 0.01

                # Realistic spread
                spread = max(theo * 0.03, 0.05)
                bid = round(max(theo - spread / 2, 0.01), 2)
                ask = round(theo + spread / 2, 2)

                # OCC symbol
                exp_str = expiry_dt.strftime("%y%m%d")
                type_char = "C" if opt_type == "call" else "P"
                strike_int = int(strike * 1000)
                occ = f"{underlying}{exp_str}{type_char}{strike_int:08d}"

                quotes.append(
                    OptionQuote(
                        timestamp=timestamp,
                        symbol=occ,
                        underlying=underlying,
                        strike=strike,
                        expiry=expiry,
                        option_type=opt_type,
                        bid=bid,
                        ask=ask,
                        last=round(theo, 2),
                        volume=1000,
                        open_interest=5000,
                        iv=self.base_iv,
                        delta=greeks.delta,
                        gamma=greeks.gamma,
                        theta=greeks.theta,
                        vega=greeks.vega,
                    )
                )

        return quotes

    @staticmethod
    def _extract_underlying(occ_symbol: str) -> str:
        i = 0
        while i < len(occ_symbol) and occ_symbol[i].isalpha():
            i += 1
        return occ_symbol[:i] if i > 0 else occ_symbol


@pytest.fixture
def mock_provider():
    """Create a fresh MockDataProvider."""
    return MockDataProvider()


@pytest.fixture
def simulator(mock_provider):
    """Create a simulator with mock data, ready to use."""
    sim = OptionsSimulator(data_provider=mock_provider)
    return sim


@pytest.fixture
def started_sim(simulator):
    """Create a simulator that's already started."""
    simulator.start("SPY", "2024-01-15")
    return simulator
