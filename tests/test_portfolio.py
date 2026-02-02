"""Tests for portfolio and position management."""


from options_sim.portfolio import Portfolio, Position, Trade


class TestPosition:
    """Tests for individual positions."""

    def test_long_position(self):
        """Long position has positive quantity."""
        pos = Position(contract="SPY240119C00470000", quantity=10, avg_cost=2.50)
        assert pos.quantity == 10
        assert pos.avg_cost == 2.50

    def test_short_position(self):
        """Short position has negative quantity."""
        pos = Position(contract="SPY240119P00460000", quantity=-5, avg_cost=1.80)
        assert pos.quantity == -5

    def test_market_value_long(self):
        """Market value = price * qty * 100 for long."""
        pos = Position(contract="SPY240119C00470000", quantity=10, avg_cost=2.50, current_price=3.00)
        assert pos.market_value == 3.00 * 10 * 100

    def test_market_value_short(self):
        """Market value is negative for short positions."""
        pos = Position(contract="SPY240119P00460000", quantity=-5, avg_cost=1.80, current_price=2.00)
        assert pos.market_value == 2.00 * (-5) * 100
        assert pos.market_value < 0

    def test_unrealized_pnl_profit(self):
        """Unrealized P&L when price rises (long)."""
        pos = Position(contract="SPY240119C00470000", quantity=10, avg_cost=2.50, current_price=3.00)
        assert pos.unrealized_pnl == (3.00 - 2.50) * 10 * 100
        assert pos.unrealized_pnl == 500.0

    def test_unrealized_pnl_loss(self):
        """Unrealized P&L when price drops (long)."""
        pos = Position(contract="SPY240119C00470000", quantity=10, avg_cost=2.50, current_price=2.00)
        assert pos.unrealized_pnl == (2.00 - 2.50) * 10 * 100
        assert pos.unrealized_pnl == -500.0

    def test_position_delta(self):
        """Position delta = delta * quantity * 100."""
        pos = Position(contract="SPY240119C00470000", quantity=10, avg_cost=2.50, delta=0.5)
        assert pos.position_delta == 0.5 * 10 * 100
        assert pos.position_delta == 500.0

    def test_to_dict(self):
        """Position serializes to dict correctly."""
        pos = Position(
            contract="SPY240119C00470000",
            quantity=10,
            avg_cost=2.50,
            current_price=3.00,
            underlying="SPY",
            strike=470.0,
            expiry="2024-01-19",
            option_type="call",
        )
        d = pos.to_dict()
        assert d["contract"] == "SPY240119C00470000"
        assert d["quantity"] == 10
        assert d["unrealized_pnl"] == 500.0


class TestPortfolio:
    """Tests for portfolio operations."""

    def test_open_long(self):
        """Open a new long position."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        assert "SPY240119C00470000" in p.positions
        assert p.positions["SPY240119C00470000"].quantity == 10
        assert p.positions["SPY240119C00470000"].avg_cost == 2.50

    def test_open_short(self):
        """Open a new short position."""
        p = Portfolio()
        p.add_position("SPY240119P00460000", -5, 1.80)
        assert p.positions["SPY240119P00460000"].quantity == -5

    def test_add_to_long(self):
        """Add to an existing long position updates avg cost."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        p.add_position("SPY240119C00470000", 5, 3.00)
        pos = p.positions["SPY240119C00470000"]
        assert pos.quantity == 15
        expected_avg = (2.50 * 10 + 3.00 * 5) / 15
        assert abs(pos.avg_cost - expected_avg) < 0.001

    def test_close_long_fully(self):
        """Fully closing a long position removes it."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        realized = p.add_position("SPY240119C00470000", -10, 3.00)
        assert "SPY240119C00470000" not in p.positions
        # Realized P&L = (3.00 - 2.50) * 10 * 100 = 500
        assert abs(realized - 500.0) < 0.01

    def test_close_long_partially(self):
        """Partially closing keeps remaining position."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        p.add_position("SPY240119C00470000", -5, 3.00)
        pos = p.positions["SPY240119C00470000"]
        assert pos.quantity == 5
        assert pos.avg_cost == 2.50  # Avg cost unchanged for remaining

    def test_close_short_fully(self):
        """Fully closing a short position removes it."""
        p = Portfolio()
        p.add_position("SPY240119P00460000", -10, 1.80)
        realized = p.add_position("SPY240119P00460000", 10, 1.50)
        assert "SPY240119P00460000" not in p.positions
        # Short P&L = (1.80 - 1.50) * 10 * 100 = 300
        assert abs(realized - 300.0) < 0.01

    def test_realized_pnl_tracks(self):
        """Portfolio tracks cumulative realized P&L."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        p.add_position("SPY240119C00470000", -10, 3.00)
        assert abs(p.realized_pnl - 500.0) < 0.01

    def test_commission_tracking(self):
        """Commissions are tracked separately."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50, commission=6.50)
        assert p.total_commissions == 6.50

    def test_mark_to_market(self):
        """Mark-to-market updates position price and Greeks."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        p.mark_to_market("SPY240119C00470000", 3.00, delta=0.5, gamma=0.02, theta=-0.05, vega=0.15)
        pos = p.positions["SPY240119C00470000"]
        assert pos.current_price == 3.00
        assert pos.delta == 0.5

    def test_expire_itm_long(self):
        """ITM long position at expiry — receive intrinsic value."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        realized = p.expire_position("SPY240119C00470000", 5.00)
        assert "SPY240119C00470000" not in p.positions
        # (5.00 - 2.50) * 10 * 100 = 2500
        assert abs(realized - 2500.0) < 0.01

    def test_expire_otm_long(self):
        """OTM long position expires worthless."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        realized = p.expire_position("SPY240119C00470000", 0.0)
        assert "SPY240119C00470000" not in p.positions
        # Lose full premium: -2.50 * 10 * 100 = -2500
        assert abs(realized - (-2500.0)) < 0.01

    def test_expire_otm_short(self):
        """OTM short position expires worthless — keep premium."""
        p = Portfolio()
        p.add_position("SPY240119P00460000", -10, 1.80)
        realized = p.expire_position("SPY240119P00460000", 0.0)
        assert "SPY240119P00460000" not in p.positions
        # Keep premium: 1.80 * 10 * 100 = 1800
        assert abs(realized - 1800.0) < 0.01

    def test_portfolio_delta_aggregation(self):
        """Portfolio delta sums across positions."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        p.add_position("SPY240119P00460000", -5, 1.80)
        p.mark_to_market("SPY240119C00470000", 2.50, delta=0.5)
        p.mark_to_market("SPY240119P00460000", 1.80, delta=-0.3)
        # Long call delta: 0.5 * 10 * 100 = 500
        # Short put delta: -0.3 * -5 * 100 = 150
        assert abs(p.portfolio_delta - 650.0) < 0.01

    def test_serialize_roundtrip(self):
        """Portfolio state serializes and deserializes correctly."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50, commission=6.50)
        p.add_position("SPY240119P00460000", -5, 1.80, commission=3.25)

        state = p.to_state()
        p2 = Portfolio.from_state(state)

        assert len(p2.positions) == 2
        assert p2.positions["SPY240119C00470000"].quantity == 10
        assert abs(p2.total_commissions - 9.75) < 0.01

    def test_get_summary(self):
        """get_summary() returns proper structure."""
        p = Portfolio()
        p.add_position("SPY240119C00470000", 10, 2.50)
        summary = p.get_summary()
        assert "positions" in summary
        assert "position_count" in summary
        assert "total_market_value" in summary
        assert "realized_pnl" in summary
        assert "portfolio_delta" in summary


class TestTrade:
    """Tests for trade records."""

    def test_buy_trade_cost(self):
        """Buy trade total cost is positive (cash outflow)."""
        from datetime import datetime

        trade = Trade(
            timestamp=datetime(2024, 1, 15, 10, 0),
            contract="SPY240119C00470000",
            side="buy",
            quantity=10,
            price=2.50,
            commission=6.50,
        )
        # 2.50 * 10 * 100 + 6.50 = 2506.50
        assert abs(trade.total_cost - 2506.50) < 0.01

    def test_sell_trade_cost(self):
        """Sell trade total cost is negative (cash inflow)."""
        from datetime import datetime

        trade = Trade(
            timestamp=datetime(2024, 1, 15, 10, 0),
            contract="SPY240119C00470000",
            side="sell",
            quantity=10,
            price=3.00,
            commission=6.50,
        )
        # -(3.00 * 10 * 100) + 6.50 = -2993.50
        assert abs(trade.total_cost - (-2993.50)) < 0.01

    def test_trade_to_dict(self):
        """Trade serializes correctly."""
        from datetime import datetime

        trade = Trade(
            timestamp=datetime(2024, 1, 15, 10, 0),
            contract="SPY240119C00470000",
            side="buy",
            quantity=10,
            price=2.50,
            commission=6.50,
        )
        d = trade.to_dict()
        assert d["contract"] == "SPY240119C00470000"
        assert d["side"] == "buy"
        assert d["quantity"] == 10
