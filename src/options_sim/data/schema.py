"""Canonical data schema for options market data.

All data providers normalize their output to these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OptionQuote:
    """A single option contract quote at a point in time.

    Attributes:
        timestamp: Quote timestamp.
        symbol: OCC option symbol (e.g. SPY240119C00470000).
        underlying: Underlying ticker (e.g. SPY).
        strike: Strike price.
        expiry: Expiration date as string YYYY-MM-DD.
        option_type: 'call' or 'put'.
        bid: Best bid price.
        ask: Best ask price.
        last: Last traded price.
        volume: Contracts traded today.
        open_interest: Open interest.
        iv: Implied volatility (0-1 scale, e.g. 0.25 = 25%).
        delta: Delta.
        gamma: Gamma.
        theta: Theta (per day).
        vega: Vega (per 1% IV move).
    """

    timestamp: datetime
    symbol: str
    underlying: str
    strike: float
    expiry: str
    option_type: str  # 'call' or 'put'
    bid: float
    ask: float
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0

    @property
    def mid(self) -> float:
        """Midpoint price."""
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "underlying": self.underlying,
            "strike": self.strike,
            "expiry": self.expiry,
            "option_type": self.option_type,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "iv": round(self.iv, 6),
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 6),
            "vega": round(self.vega, 6),
            "mid": round(self.mid, 4),
        }


@dataclass
class MarketSnapshot:
    """Complete market state at a point in time.

    Attributes:
        timestamp: Snapshot timestamp.
        underlying: Underlying ticker.
        underlying_price: Current underlying price.
        chain: List of option quotes in the snapshot.
    """

    timestamp: datetime
    underlying: str
    underlying_price: float
    chain: list[OptionQuote] = field(default_factory=list)

    def get_quote(self, symbol: str) -> OptionQuote | None:
        """Look up a specific contract by OCC symbol."""
        for q in self.chain:
            if q.symbol == symbol:
                return q
        return None

    def get_chain_for_expiry(self, expiry: str) -> list[OptionQuote]:
        """Filter chain to a specific expiration date."""
        return [q for q in self.chain if q.expiry == expiry]

    def available_expiries(self) -> list[str]:
        """List all available expiration dates."""
        return sorted(set(q.expiry for q in self.chain))

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "underlying": self.underlying,
            "underlying_price": self.underlying_price,
            "chain": [q.to_dict() for q in self.chain],
        }
