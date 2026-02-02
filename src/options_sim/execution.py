"""Fill models for order execution.

Provides realistic execution simulation with multiple fill models,
liquidity checks, and slippage for large orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FillModelType(str, Enum):
    """Available fill models.

    MIDPOINT: Fill at (bid + ask) / 2. Balanced default.
    AGGRESSIVE: Buy at ask, sell at bid. Most realistic for market orders.
    PASSIVE: Buy at bid, sell at ask. Best case / limit order fill.
    """

    MIDPOINT = "midpoint"
    AGGRESSIVE = "aggressive"
    PASSIVE = "passive"


@dataclass
class FillResult:
    """Result of an order execution attempt.

    Attributes:
        filled: Whether the order was filled.
        fill_price: Execution price per contract (None if not filled).
        quantity: Number of contracts filled.
        reason: Rejection reason if not filled.
        slippage: Price impact from order size.
    """

    filled: bool
    fill_price: float | None = None
    quantity: int = 0
    reason: str = ""
    slippage: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        d: dict = {"filled": self.filled}
        if self.filled:
            d["fill_price"] = round(self.fill_price, 4) if self.fill_price else None
            d["quantity"] = self.quantity
            if self.slippage > 0:
                d["slippage"] = round(self.slippage, 4)
        else:
            d["reason"] = self.reason
        return d


def calculate_fill(
    side: str,
    bid: float,
    ask: float,
    volume: int,
    quantity: int = 1,
    limit_price: float | None = None,
    model: str = "midpoint",
) -> FillResult:
    """Calculate execution fill price for an order.

    Args:
        side: 'buy' or 'sell'.
        bid: Current best bid price.
        ask: Current best ask price.
        volume: Current daily volume for liquidity check.
        quantity: Number of contracts to trade.
        limit_price: Limit price (None for market order).
        model: Fill model ('midpoint', 'aggressive', 'passive').

    Returns:
        FillResult with fill details or rejection reason.
    """
    side = side.lower()
    if side not in ("buy", "sell"):
        return FillResult(filled=False, reason=f"Invalid side: {side}")

    if quantity <= 0:
        return FillResult(filled=False, reason="Quantity must be positive")

    if bid <= 0 and ask <= 0:
        return FillResult(filled=False, reason="No market (bid and ask are zero)")

    # Liquidity check: reject if no volume
    if volume <= 0:
        return FillResult(filled=False, reason="No liquidity (volume = 0)")

    # Validate bid/ask
    if bid < 0:
        bid = 0.0
    if ask <= 0:
        ask = bid + 0.01  # Minimum spread

    # Base fill price from model
    model_type = FillModelType(model)
    if side == "buy":
        if model_type == FillModelType.AGGRESSIVE:
            base_price = ask
        elif model_type == FillModelType.PASSIVE:
            base_price = bid
        else:  # MIDPOINT
            base_price = (bid + ask) / 2.0
    else:  # sell
        if model_type == FillModelType.AGGRESSIVE:
            base_price = bid
        elif model_type == FillModelType.PASSIVE:
            base_price = ask
        else:  # MIDPOINT
            base_price = (bid + ask) / 2.0

    # Slippage for large orders (> 10% of daily volume)
    slippage = 0.0
    if volume > 0 and quantity > volume * 0.1:
        # Linear slippage model: 1% of spread per 10% of volume exceeded
        spread = ask - bid
        excess_ratio = (quantity / volume - 0.1) / 0.1
        slippage = spread * min(excess_ratio * 0.01, 0.5)  # Cap at 50% of spread

        if side == "buy":
            base_price += slippage
        else:
            base_price -= slippage
            base_price = max(base_price, 0.01)  # Floor at penny

    # Round to penny
    fill_price = round(base_price, 2)

    # Limit order check
    if limit_price is not None:
        if side == "buy" and fill_price > limit_price:
            return FillResult(
                filled=False,
                reason=f"Fill price {fill_price:.2f} exceeds limit {limit_price:.2f}",
            )
        if side == "sell" and fill_price < limit_price:
            return FillResult(
                filled=False,
                reason=f"Fill price {fill_price:.2f} below limit {limit_price:.2f}",
            )

    return FillResult(
        filled=True,
        fill_price=fill_price,
        quantity=quantity,
        slippage=round(slippage, 4),
    )
