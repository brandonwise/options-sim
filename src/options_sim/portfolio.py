"""Position tracking, P&L calculation, and portfolio Greeks aggregation.

Manages option and underlying positions with accurate cost basis tracking,
realized/unrealized P&L, and aggregate portfolio Greeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Position:
    """A single option position.

    Attributes:
        contract: OCC option symbol.
        quantity: Signed quantity (positive=long, negative=short).
        avg_cost: Average cost per contract.
        current_price: Current market price per contract.
        underlying: Underlying ticker.
        strike: Strike price.
        expiry: Expiration date (YYYY-MM-DD).
        option_type: 'call' or 'put'.
        delta: Position delta (per contract).
        gamma: Position gamma.
        theta: Position theta (per day).
        vega: Position vega (per 1% IV).
    """

    contract: str
    quantity: int
    avg_cost: float
    current_price: float = 0.0
    underlying: str = ""
    strike: float = 0.0
    expiry: str = ""
    option_type: str = ""
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0

    @property
    def market_value(self) -> float:
        """Current market value (per-contract price * quantity * 100 multiplier)."""
        return self.current_price * self.quantity * 100

    @property
    def cost_basis(self) -> float:
        """Total cost basis (per-contract cost * quantity * 100 multiplier)."""
        return self.avg_cost * self.quantity * 100

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L."""
        return (self.current_price - self.avg_cost) * self.quantity * 100

    @property
    def position_delta(self) -> float:
        """Total position delta (delta * quantity * 100)."""
        return self.delta * self.quantity * 100

    @property
    def position_gamma(self) -> float:
        """Total position gamma."""
        return self.gamma * self.quantity * 100

    @property
    def position_theta(self) -> float:
        """Total position theta (daily)."""
        return self.theta * self.quantity * 100

    @property
    def position_vega(self) -> float:
        """Total position vega."""
        return self.vega * self.quantity * 100

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "contract": self.contract,
            "quantity": self.quantity,
            "avg_cost": round(self.avg_cost, 4),
            "current_price": round(self.current_price, 4),
            "market_value": round(self.market_value, 2),
            "cost_basis": round(self.cost_basis, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "underlying": self.underlying,
            "strike": self.strike,
            "expiry": self.expiry,
            "option_type": self.option_type,
            "delta": round(self.position_delta, 2),
            "gamma": round(self.position_gamma, 4),
            "theta": round(self.position_theta, 2),
            "vega": round(self.position_vega, 2),
        }


@dataclass
class Trade:
    """Record of an executed trade.

    Attributes:
        timestamp: Execution time.
        contract: OCC option symbol.
        side: 'buy' or 'sell'.
        quantity: Number of contracts.
        price: Fill price per contract.
        commission: Total commission for the trade.
        underlying_price: Underlying price at time of trade.
    """

    timestamp: datetime
    contract: str
    side: str
    quantity: int
    price: float
    commission: float
    underlying_price: float = 0.0

    @property
    def total_cost(self) -> float:
        """Total cost including commission (positive = cash outflow)."""
        notional = self.price * self.quantity * 100
        if self.side == "buy":
            return notional + self.commission
        return -notional + self.commission

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "contract": self.contract,
            "side": self.side,
            "quantity": self.quantity,
            "price": round(self.price, 4),
            "commission": round(self.commission, 2),
            "total_cost": round(self.total_cost, 2),
            "underlying_price": round(self.underlying_price, 4),
        }


class Portfolio:
    """Manages option positions and tracks P&L.

    Handles position opening/closing, average cost calculation,
    realized P&L tracking, and portfolio-level Greeks aggregation.
    """

    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.total_commissions: float = 0.0

    def add_position(
        self,
        contract: str,
        quantity: int,
        price: float,
        commission: float = 0.0,
        underlying: str = "",
        strike: float = 0.0,
        expiry: str = "",
        option_type: str = "",
    ) -> float:
        """Add to or open a position. Returns realized P&L if closing.

        Args:
            contract: OCC option symbol.
            quantity: Signed quantity to add (positive=buy, negative=sell).
            price: Execution price per contract.
            commission: Commission for this trade.
            underlying: Underlying ticker.
            strike: Strike price.
            expiry: Expiration date.
            option_type: 'call' or 'put'.

        Returns:
            Realized P&L from this trade (0 if opening).
        """
        self.total_commissions += commission
        realized = 0.0

        if contract in self.positions:
            pos = self.positions[contract]
            old_qty = pos.quantity
            new_qty = old_qty + quantity

            if old_qty != 0 and (
                (old_qty > 0 and quantity < 0) or (old_qty < 0 and quantity > 0)
            ):
                # Closing (partially or fully)
                closing_qty = min(abs(quantity), abs(old_qty))
                if old_qty > 0:
                    # Was long, selling to close
                    realized = (price - pos.avg_cost) * closing_qty * 100
                else:
                    # Was short, buying to close
                    realized = (pos.avg_cost - price) * closing_qty * 100

                realized -= commission
                self.realized_pnl += realized

                if new_qty == 0:
                    del self.positions[contract]
                    return realized

                if abs(new_qty) < abs(old_qty):
                    # Partial close — keep same avg cost
                    pos.quantity = new_qty
                else:
                    # Flipped sides — new avg cost is the new price
                    pos.quantity = new_qty
                    pos.avg_cost = price
            else:
                # Adding to position — update average cost
                total_cost = pos.avg_cost * abs(old_qty) + price * abs(quantity)
                pos.quantity = new_qty
                pos.avg_cost = total_cost / abs(new_qty) if new_qty != 0 else 0

            # Update metadata
            if underlying:
                pos.underlying = underlying
            if strike:
                pos.strike = strike
            if expiry:
                pos.expiry = expiry
            if option_type:
                pos.option_type = option_type
        else:
            # New position
            self.positions[contract] = Position(
                contract=contract,
                quantity=quantity,
                avg_cost=price,
                underlying=underlying,
                strike=strike,
                expiry=expiry,
                option_type=option_type,
            )
            # Commission reduces realized P&L for new positions too
            self.realized_pnl -= commission
            realized = -commission

        return realized

    def mark_to_market(
        self,
        contract: str,
        price: float,
        delta: float = 0.0,
        gamma: float = 0.0,
        theta: float = 0.0,
        vega: float = 0.0,
    ) -> None:
        """Update current market price and Greeks for a position.

        Args:
            contract: OCC option symbol.
            price: Current market price per contract.
            delta: Current per-contract delta.
            gamma: Current per-contract gamma.
            theta: Current per-contract theta.
            vega: Current per-contract vega.
        """
        if contract in self.positions:
            pos = self.positions[contract]
            pos.current_price = price
            pos.delta = delta
            pos.gamma = gamma
            pos.theta = theta
            pos.vega = vega

    def expire_position(self, contract: str, intrinsic_value: float) -> float:
        """Handle option expiration.

        Args:
            contract: OCC option symbol.
            intrinsic_value: Per-contract intrinsic value at expiry.

        Returns:
            Realized P&L from expiration.
        """
        if contract not in self.positions:
            return 0.0

        pos = self.positions[contract]
        realized = 0.0

        if intrinsic_value > 0:
            # ITM — exercise/assignment
            if pos.quantity > 0:
                # Long: receive intrinsic value
                realized = (intrinsic_value - pos.avg_cost) * abs(pos.quantity) * 100
            else:
                # Short: pay intrinsic value
                realized = (pos.avg_cost - intrinsic_value) * abs(pos.quantity) * 100
        else:
            # OTM — expires worthless
            if pos.quantity > 0:
                # Long: lose premium paid
                realized = -pos.avg_cost * abs(pos.quantity) * 100
            else:
                # Short: keep premium received
                realized = pos.avg_cost * abs(pos.quantity) * 100

        self.realized_pnl += realized
        del self.positions[contract]
        return realized

    @property
    def total_unrealized_pnl(self) -> float:
        """Total unrealized P&L across all positions."""
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_market_value(self) -> float:
        """Total market value of all positions."""
        return sum(p.market_value for p in self.positions.values())

    @property
    def portfolio_delta(self) -> float:
        """Aggregate portfolio delta."""
        return sum(p.position_delta for p in self.positions.values())

    @property
    def portfolio_gamma(self) -> float:
        """Aggregate portfolio gamma."""
        return sum(p.position_gamma for p in self.positions.values())

    @property
    def portfolio_theta(self) -> float:
        """Aggregate portfolio theta (daily)."""
        return sum(p.position_theta for p in self.positions.values())

    @property
    def portfolio_vega(self) -> float:
        """Aggregate portfolio vega."""
        return sum(p.position_vega for p in self.positions.values())

    def get_summary(self) -> dict:
        """Get portfolio summary as dictionary."""
        return {
            "positions": [p.to_dict() for p in self.positions.values()],
            "position_count": len(self.positions),
            "total_market_value": round(self.total_market_value, 2),
            "total_unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_commissions": round(self.total_commissions, 2),
            "portfolio_delta": round(self.portfolio_delta, 2),
            "portfolio_gamma": round(self.portfolio_gamma, 4),
            "portfolio_theta": round(self.portfolio_theta, 2),
            "portfolio_vega": round(self.portfolio_vega, 2),
        }

    def to_state(self) -> dict:
        """Serialize portfolio state for persistence."""
        return {
            "positions": {
                k: {
                    "contract": v.contract,
                    "quantity": v.quantity,
                    "avg_cost": v.avg_cost,
                    "current_price": v.current_price,
                    "underlying": v.underlying,
                    "strike": v.strike,
                    "expiry": v.expiry,
                    "option_type": v.option_type,
                }
                for k, v in self.positions.items()
            },
            "realized_pnl": self.realized_pnl,
            "total_commissions": self.total_commissions,
        }

    @classmethod
    def from_state(cls, state: dict) -> Portfolio:
        """Deserialize portfolio from saved state."""
        p = cls()
        p.realized_pnl = state.get("realized_pnl", 0.0)
        p.total_commissions = state.get("total_commissions", 0.0)
        for k, v in state.get("positions", {}).items():
            p.positions[k] = Position(**v)
        return p
