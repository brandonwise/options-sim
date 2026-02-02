"""Tests for fill models and order execution."""


from options_sim.execution import calculate_fill


class TestFillModels:
    """Tests for different fill models."""

    def test_midpoint_buy(self):
        """Midpoint fill: buy at (bid + ask) / 2."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10, model="midpoint")
        assert result.filled
        assert result.fill_price == 2.55

    def test_midpoint_sell(self):
        """Midpoint fill: sell at (bid + ask) / 2."""
        result = calculate_fill("sell", 2.50, 2.60, 1000, 10, model="midpoint")
        assert result.filled
        assert result.fill_price == 2.55

    def test_aggressive_buy(self):
        """Aggressive fill: buy at ask."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10, model="aggressive")
        assert result.filled
        assert result.fill_price == 2.60

    def test_aggressive_sell(self):
        """Aggressive fill: sell at bid."""
        result = calculate_fill("sell", 2.50, 2.60, 1000, 10, model="aggressive")
        assert result.filled
        assert result.fill_price == 2.50

    def test_passive_buy(self):
        """Passive fill: buy at bid."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10, model="passive")
        assert result.filled
        assert result.fill_price == 2.50

    def test_passive_sell(self):
        """Passive fill: sell at ask."""
        result = calculate_fill("sell", 2.50, 2.60, 1000, 10, model="passive")
        assert result.filled
        assert result.fill_price == 2.60


class TestLiquidity:
    """Tests for liquidity checks."""

    def test_zero_volume_rejected(self):
        """Order rejected when volume is zero."""
        result = calculate_fill("buy", 2.50, 2.60, 0, 10)
        assert not result.filled
        assert "liquidity" in result.reason.lower() or "volume" in result.reason.lower()

    def test_positive_volume_fills(self):
        """Order fills when volume is positive."""
        result = calculate_fill("buy", 2.50, 2.60, 100, 10)
        assert result.filled


class TestSlippage:
    """Tests for large order slippage."""

    def test_small_order_no_slippage(self):
        """Small order (< 10% volume) has no slippage."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 5, model="midpoint")
        assert result.filled
        assert result.slippage == 0.0

    def test_large_order_has_slippage(self):
        """Large order (> 10% volume) incurs slippage."""
        result = calculate_fill("buy", 2.50, 2.60, 100, 50, model="midpoint")
        assert result.filled
        assert result.slippage > 0

    def test_slippage_increases_buy_price(self):
        """Slippage increases fill price for buys."""
        small = calculate_fill("buy", 2.50, 2.60, 1000, 5, model="midpoint")
        large = calculate_fill("buy", 2.50, 2.60, 100, 50, model="midpoint")
        assert large.fill_price >= small.fill_price

    def test_slippage_decreases_sell_price(self):
        """Slippage decreases fill price for sells."""
        small = calculate_fill("sell", 2.50, 2.60, 1000, 5, model="midpoint")
        large = calculate_fill("sell", 2.50, 2.60, 100, 50, model="midpoint")
        assert large.fill_price <= small.fill_price


class TestLimitOrders:
    """Tests for limit order behavior."""

    def test_buy_limit_above_fill_succeeds(self):
        """Buy limit above fill price fills."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10, limit_price=2.60)
        assert result.filled

    def test_buy_limit_below_fill_rejected(self):
        """Buy limit below fill price rejected."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10, limit_price=2.40, model="aggressive")
        assert not result.filled
        assert "limit" in result.reason.lower() or "exceeds" in result.reason.lower()

    def test_sell_limit_below_fill_succeeds(self):
        """Sell limit below fill price fills."""
        result = calculate_fill("sell", 2.50, 2.60, 1000, 10, limit_price=2.40)
        assert result.filled

    def test_sell_limit_above_fill_rejected(self):
        """Sell limit above fill price rejected."""
        result = calculate_fill("sell", 2.50, 2.60, 1000, 10, limit_price=2.70, model="aggressive")
        assert not result.filled
        assert "limit" in result.reason.lower() or "below" in result.reason.lower()


class TestEdgeCases:
    """Tests for edge cases."""

    def test_invalid_side(self):
        """Invalid side rejected."""
        result = calculate_fill("hold", 2.50, 2.60, 1000, 10)
        assert not result.filled
        assert "invalid" in result.reason.lower()

    def test_zero_quantity(self):
        """Zero quantity rejected."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 0)
        assert not result.filled

    def test_zero_bid_ask(self):
        """Zero bid and ask rejected."""
        result = calculate_fill("buy", 0, 0, 1000, 10)
        assert not result.filled

    def test_fill_result_to_dict_filled(self):
        """FillResult.to_dict() for filled order."""
        result = calculate_fill("buy", 2.50, 2.60, 1000, 10)
        d = result.to_dict()
        assert d["filled"] is True
        assert "fill_price" in d
        assert "quantity" in d

    def test_fill_result_to_dict_rejected(self):
        """FillResult.to_dict() for rejected order."""
        result = calculate_fill("buy", 2.50, 2.60, 0, 10)
        d = result.to_dict()
        assert d["filled"] is False
        assert "reason" in d
