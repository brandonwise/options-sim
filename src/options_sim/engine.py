"""Core simulation engine.

The OptionsSimulator orchestrates time-stepping market replay with
realistic execution. It manages the simulation clock, coordinates
data providers, and processes orders through fill models.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from options_sim.data.base import DataProvider
from options_sim.data.schema import MarketSnapshot, OptionQuote
from options_sim.execution import calculate_fill
from options_sim.portfolio import Portfolio, Trade
from options_sim.pricing import calculate_greeks


class OptionsSimulator:
    """Options trading simulation engine.

    Replays historical market data with time-stepping, supports order
    execution with multiple fill models, and tracks positions/P&L.

    Args:
        data_provider: Source of market data.
        initial_cash: Starting cash balance.
        fill_model: Execution model ('midpoint', 'aggressive', 'passive').
        commission_per_contract: Commission charged per contract.
        risk_free_rate: Annual risk-free rate for Greeks calculation.
    """

    def __init__(
        self,
        data_provider: DataProvider,
        initial_cash: float = 100000.0,
        fill_model: str = "midpoint",
        commission_per_contract: float = 0.65,
        risk_free_rate: float = 0.05,
    ) -> None:
        self.data = data_provider
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.fill_model = fill_model
        self.commission_per_contract = commission_per_contract
        self.risk_free_rate = risk_free_rate

        self.current_time: datetime | None = None
        self.symbol: str = ""
        self.portfolio = Portfolio()
        self.trade_history: list[Trade] = []
        self._started = False
        self._last_snapshot: MarketSnapshot | None = None

    def start(self, symbol: str, start_date: str | datetime) -> dict:
        """Initialize simulation at a given date/time.

        Args:
            symbol: Underlying ticker to simulate (e.g. 'SPY').
            start_date: Start date as 'YYYY-MM-DD' or datetime. Time defaults
                to 9:30 AM ET (market open) if only date provided.

        Returns:
            Dictionary with initial market state.

        Raises:
            ValueError: If no data available for the date.
        """
        if isinstance(start_date, str):
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            start_date = dt.replace(hour=9, minute=30, second=0)

        self.symbol = symbol.upper()
        self.current_time = start_date
        self.cash = self.initial_cash
        self.portfolio = Portfolio()
        self.trade_history = []
        self._started = True

        # Load initial snapshot
        self._last_snapshot = self.data.get_snapshot(self.symbol, self.current_time)

        return self.get_status()

    def step(self, minutes: int = 15) -> dict:
        """Advance simulation clock forward.

        Args:
            minutes: Number of minutes to advance.

        Returns:
            Dictionary with updated market state.

        Raises:
            RuntimeError: If simulation not started.
        """
        self._check_started()
        assert self.current_time is not None

        self.current_time += timedelta(minutes=minutes)

        # Reload market data at new time
        try:
            self._last_snapshot = self.data.get_snapshot(
                self.symbol, self.current_time
            )
        except ValueError:
            pass  # Keep last snapshot if no new data

        # Mark all positions to market
        self._mark_positions()

        # Check for expirations
        expired = self._check_expirations()

        status = self.get_status()
        if expired:
            status["expired_positions"] = expired
        return status

    def get_chain(self, symbol: str | None = None, expiry: str | None = None) -> dict:
        """Get option chain at current time.

        Args:
            symbol: Underlying ticker (defaults to simulation symbol).
            expiry: Filter to specific expiry date ('YYYY-MM-DD').

        Returns:
            Dictionary with chain data.
        """
        self._check_started()
        assert self.current_time is not None

        symbol = (symbol or self.symbol).upper()
        snapshot = self.data.get_snapshot(symbol, self.current_time)

        if expiry:
            quotes = snapshot.get_chain_for_expiry(expiry)
        else:
            quotes = snapshot.chain

        # Enrich with Greeks if not provided by data source
        enriched = []
        for q in quotes:
            if q.delta == 0 and q.mid > 0:
                q = self._enrich_greeks(q, snapshot.underlying_price)
            enriched.append(q)

        return {
            "timestamp": self.current_time.isoformat(),
            "underlying": symbol,
            "underlying_price": snapshot.underlying_price,
            "expiries": snapshot.available_expiries(),
            "chain": [q.to_dict() for q in enriched],
            "count": len(enriched),
        }

    def submit_order(
        self,
        contract: str,
        side: str,
        quantity: int,
        limit_price: float | None = None,
    ) -> dict:
        """Submit an order for execution.

        Args:
            contract: OCC option symbol (e.g. 'SPY240119C00470000').
            side: 'buy' or 'sell'.
            quantity: Number of contracts.
            limit_price: Limit price per contract (None = market).

        Returns:
            Dictionary with execution result.
        """
        self._check_started()
        assert self.current_time is not None

        side = side.lower()
        if side not in ("buy", "sell"):
            return {"error": f"Invalid side: {side}. Use 'buy' or 'sell'."}

        if quantity <= 0:
            return {"error": "Quantity must be positive"}

        # Get current quote
        quote = self.data.get_quote(contract, self.current_time)
        if quote is None:
            # Try from last snapshot
            if self._last_snapshot:
                quote = self._last_snapshot.get_quote(contract)
            if quote is None:
                return {"error": f"No quote found for {contract}"}

        # Execute through fill model
        result = calculate_fill(
            side=side,
            bid=quote.bid,
            ask=quote.ask,
            volume=quote.volume,
            quantity=quantity,
            limit_price=limit_price,
            model=self.fill_model,
        )

        if not result.filled:
            return {"filled": False, "reason": result.reason}

        assert result.fill_price is not None

        # Calculate costs
        commission = self.commission_per_contract * quantity
        notional = result.fill_price * quantity * 100  # Options are 100 shares

        # Check buying power
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
            # Selling: receive premium minus commission
            # Check if we have the position to sell (or allow naked shorts)
            self.cash += notional - commission
            signed_qty = -quantity

        # Get underlying price for trade record
        underlying_price = 0.0
        if self._last_snapshot:
            underlying_price = self._last_snapshot.underlying_price

        # Update portfolio
        self.portfolio.add_position(
            contract=contract,
            quantity=signed_qty,
            price=result.fill_price,
            commission=commission,
            underlying=quote.underlying,
            strike=quote.strike,
            expiry=quote.expiry,
            option_type=quote.option_type,
        )

        # Record trade
        trade = Trade(
            timestamp=self.current_time,
            contract=contract,
            side=side,
            quantity=quantity,
            price=result.fill_price,
            commission=commission,
            underlying_price=underlying_price,
        )
        self.trade_history.append(trade)

        return {
            "filled": True,
            "contract": contract,
            "side": side,
            "quantity": quantity,
            "fill_price": result.fill_price,
            "commission": round(commission, 2),
            "total_cost": round(notional + commission if side == "buy" else -(notional - commission), 2),
            "slippage": result.slippage,
            "cash_remaining": round(self.cash, 2),
        }

    def get_status(self) -> dict:
        """Get full simulation state.

        Returns:
            Dictionary with account, positions, and market state.
        """
        self._check_started()
        assert self.current_time is not None

        underlying_price = 0.0
        if self._last_snapshot:
            underlying_price = self._last_snapshot.underlying_price

        portfolio_summary = self.portfolio.get_summary()
        total_value = self.cash + self.portfolio.total_market_value

        return {
            "timestamp": self.current_time.isoformat(),
            "symbol": self.symbol,
            "underlying_price": underlying_price,
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

    def get_positions(self) -> dict:
        """Get current positions."""
        self._check_started()
        return self.portfolio.get_summary()

    def get_history(self) -> list[dict]:
        """Get trade history."""
        return [t.to_dict() for t in self.trade_history]

    def get_account(self) -> dict:
        """Get account summary."""
        status = self.get_status()
        return status["account"]

    def _mark_positions(self) -> None:
        """Update all position prices and Greeks from current market data."""
        if not self._last_snapshot:
            return

        for contract, pos in list(self.portfolio.positions.items()):
            quote = self._last_snapshot.get_quote(contract)
            if quote:
                self.portfolio.mark_to_market(
                    contract=contract,
                    price=quote.mid,
                    delta=quote.delta,
                    gamma=quote.gamma,
                    theta=quote.theta,
                    vega=quote.vega,
                )
            else:
                # Try to calculate theoretical price
                if pos.underlying and pos.strike and pos.expiry:
                    try:
                        greeks = self._calc_greeks_for_position(pos)
                        self.portfolio.mark_to_market(
                            contract=contract,
                            price=greeks.price if greeks else pos.current_price,
                            delta=greeks.delta if greeks else 0,
                            gamma=greeks.gamma if greeks else 0,
                            theta=greeks.theta if greeks else 0,
                            vega=greeks.vega if greeks else 0,
                        )
                    except Exception:
                        pass

    def _check_expirations(self) -> list[dict]:
        """Check and handle expired positions."""
        assert self.current_time is not None
        expired: list[dict] = []
        today = self.current_time.strftime("%Y-%m-%d")

        for contract, pos in list(self.portfolio.positions.items()):
            if pos.expiry == today and self.current_time.hour >= 16:
                # Market closed on expiry day — expire the position
                underlying_price = 0.0
                if self._last_snapshot:
                    underlying_price = self._last_snapshot.underlying_price

                if pos.option_type == "call":
                    intrinsic = max(underlying_price - pos.strike, 0) / 100
                else:
                    intrinsic = max(pos.strike - underlying_price, 0) / 100

                realized = self.portfolio.expire_position(contract, intrinsic)

                # Cash settlement
                if intrinsic > 0:
                    settlement = intrinsic * abs(pos.quantity) * 100
                    if pos.quantity > 0:
                        self.cash += settlement
                    else:
                        self.cash -= settlement

                expired.append({
                    "contract": contract,
                    "quantity": pos.quantity,
                    "intrinsic_value": round(intrinsic, 4),
                    "realized_pnl": round(realized, 2),
                    "settlement": "ITM — exercised/assigned" if intrinsic > 0 else "OTM — expired worthless",
                })

        return expired

    def _enrich_greeks(
        self, quote: OptionQuote, underlying_price: float
    ) -> OptionQuote:
        """Calculate and attach Greeks to a quote."""
        if self.current_time is None:
            return quote

        try:
            expiry_dt = datetime.strptime(quote.expiry, "%Y-%m-%d")
            T = max((expiry_dt - self.current_time).total_seconds() / (365.25 * 86400), 1e-10)

            # Use IV from quote if available, else estimate
            iv = quote.iv if quote.iv > 0 else 0.25  # Default 25% IV

            greeks = calculate_greeks(
                S=underlying_price,
                K=quote.strike,
                T=T,
                r=self.risk_free_rate,
                sigma=iv,
                option_type=quote.option_type,
            )

            quote.delta = greeks.delta
            quote.gamma = greeks.gamma
            quote.theta = greeks.theta
            quote.vega = greeks.vega
        except Exception:
            pass

        return quote

    def _calc_greeks_for_position(self, pos) -> object | None:
        """Calculate Greeks for a position using BSM."""
        if not self.current_time or not self._last_snapshot:
            return None

        try:
            expiry_dt = datetime.strptime(pos.expiry, "%Y-%m-%d")
            T = max(
                (expiry_dt - self.current_time).total_seconds() / (365.25 * 86400),
                1e-10,
            )

            return calculate_greeks(
                S=self._last_snapshot.underlying_price,
                K=pos.strike,
                T=T,
                r=self.risk_free_rate,
                sigma=0.25,
                option_type=pos.option_type,
            )
        except Exception:
            return None

    def _check_started(self) -> None:
        """Verify simulation has been started."""
        if not self._started:
            raise RuntimeError(
                "Simulation not started. Call start() first."
            )

    def to_state(self) -> dict:
        """Serialize full simulation state for persistence."""
        return {
            "symbol": self.symbol,
            "current_time": self.current_time.isoformat() if self.current_time else None,
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "fill_model": self.fill_model,
            "commission_per_contract": self.commission_per_contract,
            "risk_free_rate": self.risk_free_rate,
            "started": self._started,
            "portfolio": self.portfolio.to_state(),
            "trade_history": [t.to_dict() for t in self.trade_history],
        }

    def load_state(self, state: dict) -> None:
        """Restore simulation from saved state."""
        self.symbol = state["symbol"]
        ct = state.get("current_time")
        self.current_time = datetime.fromisoformat(ct) if ct else None
        self.initial_cash = state["initial_cash"]
        self.cash = state["cash"]
        self.fill_model = state.get("fill_model", "midpoint")
        self.commission_per_contract = state.get("commission_per_contract", 0.65)
        self.risk_free_rate = state.get("risk_free_rate", 0.05)
        self._started = state.get("started", False)
        self.portfolio = Portfolio.from_state(state.get("portfolio", {}))

        # Reconstruct trades
        self.trade_history = []
        for td in state.get("trade_history", []):
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

        # Reload snapshot
        if self._started and self.current_time:
            try:
                self._last_snapshot = self.data.get_snapshot(
                    self.symbol, self.current_time
                )
            except ValueError:
                self._last_snapshot = None
