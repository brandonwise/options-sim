"""Live trading engine for real-time paper trading.

Instead of replaying historical data with time-stepping, the LiveEngine
fetches current market prices on demand and executes orders at live
bid/ask prices. Session state persists to disk so you can close the
terminal and resume later.

Usage:
    from options_sim.live_engine import LiveEngine
    engine = LiveEngine(initial_cash=100000)
    engine.start()
    quote = engine.get_stock_quote("AAPL")
    engine.submit_order("AAPL240119C00200000", "buy", 2)
    engine.get_positions()  # fetches fresh prices, recalculates P&L
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from options_sim.data.polygon_live import (
    PolygonLiveProvider,
    extract_underlying_from_occ,
    parse_occ_symbol,
)
from options_sim.portfolio import Portfolio, Trade


SESSION_DIR = Path.home() / ".options-sim"
LIVE_SESSION_FILE = SESSION_DIR / "live-session.json"


class LiveEngine:
    """Live paper trading engine.

    Fetches real market data from Polygon.io and tracks positions/P&L.
    State persists to ~/.options-sim/live-session.json.

    Args:
        initial_cash: Starting cash balance.
        commission_per_contract: Commission per contract traded.
        api: Optional pre-configured PolygonLiveProvider.
        session_file: Path to session persistence file.
    """

    def __init__(
        self,
        initial_cash: float = 100000.0,
        commission_per_contract: float = 0.65,
        api: PolygonLiveProvider | None = None,
        session_file: Path | None = None,
    ) -> None:
        self.api = api or PolygonLiveProvider()
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_per_contract = commission_per_contract
        self.portfolio = Portfolio()
        self.trade_history: list[Trade] = []
        self.started_at: datetime | None = None
        self._started = False
        self._session_file = session_file or LIVE_SESSION_FILE

    def start(self) -> dict:
        """Start a new live trading session.

        Returns:
            Dict with initial session state.
        """
        self.started_at = datetime.now()
        self._started = True
        self._save_session()
        return self.get_status()

    def resume(self) -> dict:
        """Resume an existing session from disk.

        Returns:
            Dict with restored session state.

        Raises:
            FileNotFoundError: If no session file exists.
        """
        self._load_session()
        return self.get_status()

    def get_stock_quote(self, symbol: str) -> dict:
        """Get a live stock quote.

        Args:
            symbol: Stock ticker (e.g., 'AAPL').

        Returns:
            Dict with price, change, volume, etc.
        """
        return self.api.get_stock_quote(symbol.upper())

    def get_chain(
        self,
        symbol: str,
        expiry: str | None = None,
        strikes: int | None = None,
    ) -> dict:
        """Get live option chain.

        Args:
            symbol: Underlying ticker.
            expiry: Filter by expiry date (YYYY-MM-DD).
            strikes: Limit to N strikes around ATM.

        Returns:
            Dict with chain data.
        """
        symbol = symbol.upper()
        underlying_price = self.api.get_underlying_price(symbol)
        chain = self.api.get_option_chain(
            symbol, expiry=expiry, strikes_around_atm=strikes
        )

        # Collect unique expiries
        expiries = sorted(set(q["expiry"] for q in chain if q.get("expiry")))

        return {
            "timestamp": datetime.now().isoformat(),
            "underlying": symbol,
            "underlying_price": underlying_price,
            "expiries": expiries,
            "chain": chain,
            "count": len(chain),
        }

    def submit_order(
        self,
        contract: str,
        side: str,
        quantity: int,
        limit_price: float | None = None,
    ) -> dict:
        """Submit an order at current live prices.

        Orders fill at the current bid (sell) or ask (buy).

        Args:
            contract: OCC option symbol.
            side: 'buy' or 'sell'.
            quantity: Number of contracts.
            limit_price: Limit price (None = market order).

        Returns:
            Dict with execution result.
        """
        self._check_started()

        side = side.lower()
        if side not in ("buy", "sell"):
            return {"error": f"Invalid side: {side}. Use 'buy' or 'sell'."}
        if quantity <= 0:
            return {"error": "Quantity must be positive"}

        # Get live quote
        try:
            quote = self.api.get_option_quote(contract)
        except Exception as e:
            return {"error": f"Could not get quote for {contract}: {e}"}

        bid = float(quote.get("bid", 0))
        ask = float(quote.get("ask", 0))

        if bid <= 0 and ask <= 0:
            return {"error": f"No market for {contract} (bid={bid}, ask={ask})"}

        # Determine fill price (market order = aggressive)
        if side == "buy":
            fill_price = ask if ask > 0 else bid
        else:
            fill_price = bid if bid > 0 else ask

        # Round to penny
        fill_price = round(fill_price, 2)

        # Check limit price
        if limit_price is not None:
            if side == "buy" and fill_price > limit_price:
                return {
                    "filled": False,
                    "reason": f"Ask {fill_price:.2f} exceeds limit {limit_price:.2f}",
                }
            if side == "sell" and fill_price < limit_price:
                return {
                    "filled": False,
                    "reason": f"Bid {fill_price:.2f} below limit {limit_price:.2f}",
                }

        # Calculate costs
        commission = self.commission_per_contract * quantity
        notional = fill_price * quantity * 100

        if side == "buy":
            total_cost = notional + commission
            if total_cost > self.cash:
                return {
                    "filled": False,
                    "reason": f"Insufficient funds. Need ${total_cost:.2f}, have ${self.cash:.2f}",
                }
            self.cash -= total_cost
            signed_qty = quantity
        else:
            self.cash += notional - commission
            signed_qty = -quantity

        # Parse contract info
        parsed = parse_occ_symbol(contract)
        underlying = parsed.get("underlying", extract_underlying_from_occ(contract))
        strike = parsed.get("strike", float(quote.get("strike", 0)))
        expiry = parsed.get("expiry", quote.get("expiry", ""))
        option_type = parsed.get("option_type", quote.get("option_type", ""))

        # Update portfolio
        self.portfolio.add_position(
            contract=contract,
            quantity=signed_qty,
            price=fill_price,
            commission=commission,
            underlying=underlying,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
        )

        # Get underlying price for trade record
        try:
            underlying_price = self.api.get_underlying_price(underlying)
        except Exception:
            underlying_price = 0.0

        # Record trade
        trade = Trade(
            timestamp=datetime.now(),
            contract=contract,
            side=side,
            quantity=quantity,
            price=fill_price,
            commission=commission,
            underlying_price=underlying_price,
        )
        self.trade_history.append(trade)
        self._save_session()

        return {
            "filled": True,
            "contract": contract,
            "side": side,
            "quantity": quantity,
            "fill_price": fill_price,
            "commission": round(commission, 2),
            "total_cost": round(
                notional + commission if side == "buy" else -(notional - commission), 2
            ),
            "cash_remaining": round(self.cash, 2),
        }

    def get_positions(self) -> dict:
        """Get current positions with live prices.

        Fetches fresh quotes and recalculates P&L.

        Returns:
            Portfolio summary dict.
        """
        self._check_started()
        self._refresh_positions()
        return self.portfolio.get_summary()

    def get_account(self) -> dict:
        """Get account summary with live portfolio value.

        Returns:
            Account summary dict.
        """
        self._check_started()
        self._refresh_positions()

        total_value = self.cash + self.portfolio.total_market_value
        return {
            "cash": round(self.cash, 2),
            "portfolio_value": round(self.portfolio.total_market_value, 2),
            "total_value": round(total_value, 2),
            "initial_cash": self.initial_cash,
            "total_return_pct": round(
                (total_value - self.initial_cash) / self.initial_cash * 100, 4
            ),
            "realized_pnl": round(self.portfolio.realized_pnl, 2),
            "unrealized_pnl": round(self.portfolio.total_unrealized_pnl, 2),
            "total_commissions": round(self.portfolio.total_commissions, 2),
        }

    def get_status(self) -> dict:
        """Get full session status.

        Returns:
            Dict with mode, account, positions, etc.
        """
        self._check_started()

        if self.portfolio.positions:
            self._refresh_positions()

        portfolio_summary = self.portfolio.get_summary()
        total_value = self.cash + self.portfolio.total_market_value

        return {
            "mode": "live",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "timestamp": datetime.now().isoformat(),
            "account": {
                "cash": round(self.cash, 2),
                "portfolio_value": round(self.portfolio.total_market_value, 2),
                "total_value": round(total_value, 2),
                "initial_cash": self.initial_cash,
                "total_return_pct": round(
                    (total_value - self.initial_cash) / self.initial_cash * 100, 4
                ),
                "realized_pnl": round(self.portfolio.realized_pnl, 2),
                "unrealized_pnl": round(self.portfolio.total_unrealized_pnl, 2),
                "total_commissions": round(self.portfolio.total_commissions, 2),
            },
            "positions": portfolio_summary["positions"],
            "position_count": portfolio_summary["position_count"],
            "portfolio_greeks": {
                "delta": portfolio_summary["portfolio_delta"],
                "gamma": portfolio_summary["portfolio_gamma"],
                "theta": portfolio_summary["portfolio_theta"],
                "vega": portfolio_summary["portfolio_vega"],
            },
            "trade_count": len(self.trade_history),
        }

    def get_history(self) -> list[dict]:
        """Get trade history.

        Returns:
            List of trade dicts.
        """
        return [t.to_dict() for t in self.trade_history]

    def _refresh_positions(self) -> None:
        """Fetch fresh quotes for all positions and update P&L/Greeks."""
        for contract, pos in list(self.portfolio.positions.items()):
            try:
                quote = self.api.get_option_quote(contract)
                mid = float(quote.get("mid", 0))
                if mid <= 0:
                    bid = float(quote.get("bid", 0))
                    ask = float(quote.get("ask", 0))
                    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0

                self.portfolio.mark_to_market(
                    contract=contract,
                    price=mid,
                    delta=float(quote.get("delta", 0)),
                    gamma=float(quote.get("gamma", 0)),
                    theta=float(quote.get("theta", 0)),
                    vega=float(quote.get("vega", 0)),
                )
            except Exception:
                # Keep last known price if quote fails
                pass

    def _save_session(self) -> None:
        """Persist session state to disk."""
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "mode": "live",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "commission_per_contract": self.commission_per_contract,
            "positions": self.portfolio.to_state(),
            "trades": [t.to_dict() for t in self.trade_history],
            "commissions": self.portfolio.total_commissions,
        }
        with open(self._session_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _load_session(self) -> None:
        """Load session state from disk."""
        if not self._session_file.exists():
            raise FileNotFoundError(
                f"No live session found at {self._session_file}. "
                "Start one with: options-sim start --live"
            )

        with open(self._session_file) as f:
            state = json.load(f)

        self.started_at = (
            datetime.fromisoformat(state["started_at"])
            if state.get("started_at")
            else None
        )
        self.initial_cash = state.get("initial_cash", 100000.0)
        self.cash = state.get("cash", self.initial_cash)
        self.commission_per_contract = state.get("commission_per_contract", 0.65)
        self.portfolio = Portfolio.from_state(state.get("positions", {}))
        self._started = True

        # Reconstruct trades
        self.trade_history = []
        for td in state.get("trades", []):
            self.trade_history.append(
                Trade(
                    timestamp=datetime.fromisoformat(td["timestamp"]),
                    contract=td["contract"],
                    side=td["side"],
                    quantity=td["quantity"],
                    price=td["price"],
                    commission=td["commission"],
                    underlying_price=td.get("underlying_price", 0.0),
                )
            )

    def _check_started(self) -> None:
        """Verify session has been started."""
        if not self._started:
            raise RuntimeError(
                "Live session not started. Use 'options-sim start --live' or resume."
            )

    def clear_session(self) -> None:
        """Delete the persisted session file."""
        if self._session_file.exists():
            self._session_file.unlink()
        self._started = False
