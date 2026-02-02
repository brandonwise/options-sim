"""Tests for Black-Scholes pricing and Greeks calculation."""

import math

import pytest

from options_sim.pricing import (
    black_scholes_price,
    calculate_greeks,
    implied_volatility,
)


class TestBlackScholesPrice:
    """Tests for BSM option pricing."""

    def test_call_price_known_value(self):
        """BSM call price matches known textbook value."""
        # S=100, K=100, T=1yr, r=5%, sigma=20%
        # Known result ≈ 10.4506
        price = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "call")
        assert abs(price - 10.4506) < 0.01

    def test_put_price_known_value(self):
        """BSM put price matches known textbook value."""
        # Put-call parity: P = C - S + K*exp(-rT)
        call = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "call")
        put = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "put")
        # Put-call parity check
        parity = call - put - 100 + 100 * math.exp(-0.05)
        assert abs(parity) < 0.001

    def test_call_at_expiry_itm(self):
        """At expiry, ITM call = intrinsic value."""
        price = black_scholes_price(110, 100, 0.0, 0.05, 0.20, "call")
        assert abs(price - 10.0) < 0.001

    def test_call_at_expiry_otm(self):
        """At expiry, OTM call = 0."""
        price = black_scholes_price(90, 100, 0.0, 0.05, 0.20, "call")
        assert abs(price) < 0.001

    def test_put_at_expiry_itm(self):
        """At expiry, ITM put = intrinsic value."""
        price = black_scholes_price(90, 100, 0.0, 0.05, 0.20, "put")
        assert abs(price - 10.0) < 0.001

    def test_put_at_expiry_otm(self):
        """At expiry, OTM put = 0."""
        price = black_scholes_price(110, 100, 0.0, 0.05, 0.20, "put")
        assert abs(price) < 0.001

    def test_deep_itm_call(self):
        """Deep ITM call ≈ S - K*exp(-rT)."""
        price = black_scholes_price(200, 100, 1.0, 0.05, 0.20, "call")
        intrinsic_pv = 200 - 100 * math.exp(-0.05)
        assert price > intrinsic_pv - 0.01  # At least intrinsic

    def test_deep_otm_call(self):
        """Deep OTM call ≈ 0."""
        price = black_scholes_price(50, 100, 0.1, 0.05, 0.20, "call")
        assert price < 0.01

    def test_higher_vol_higher_price(self):
        """Higher volatility → higher option price."""
        low_vol = black_scholes_price(100, 100, 1.0, 0.05, 0.10, "call")
        high_vol = black_scholes_price(100, 100, 1.0, 0.05, 0.40, "call")
        assert high_vol > low_vol

    def test_longer_time_higher_price(self):
        """More time to expiry → higher option price."""
        short = black_scholes_price(100, 100, 0.1, 0.05, 0.20, "call")
        long = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "call")
        assert long > short


class TestGreeks:
    """Tests for Greeks calculation."""

    def test_call_delta_range(self):
        """Call delta should be between 0 and 1."""
        g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "call")
        assert 0 <= g.delta <= 1

    def test_put_delta_range(self):
        """Put delta should be between -1 and 0."""
        g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "put")
        assert -1 <= g.delta <= 0

    def test_atm_call_delta_near_half(self):
        """ATM call delta ≈ 0.5 (slightly above due to drift from interest rate)."""
        g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "call")
        assert abs(g.delta - 0.5) < 0.15  # r > 0 pushes delta above 0.5

    def test_gamma_positive(self):
        """Gamma is always positive for long options."""
        for opt_type in ("call", "put"):
            g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, opt_type)
            assert g.gamma > 0

    def test_gamma_highest_atm(self):
        """Gamma is highest at-the-money."""
        atm = calculate_greeks(100, 100, 0.1, 0.05, 0.20, "call")
        itm = calculate_greeks(110, 100, 0.1, 0.05, 0.20, "call")
        otm = calculate_greeks(90, 100, 0.1, 0.05, 0.20, "call")
        assert atm.gamma > itm.gamma
        assert atm.gamma > otm.gamma

    def test_theta_negative_long_call(self):
        """Theta is negative for long options (time decay)."""
        g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "call")
        assert g.theta < 0

    def test_vega_positive(self):
        """Vega is positive for long options."""
        for opt_type in ("call", "put"):
            g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, opt_type)
            assert g.vega > 0

    def test_put_call_delta_parity(self):
        """Call delta - Put delta = 1 (approximately, ignoring rates)."""
        call_g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "call")
        put_g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "put")
        assert abs(call_g.delta - put_g.delta - 1.0) < 0.01

    def test_greeks_at_expiry(self):
        """Greeks at expiry are zero (except delta)."""
        g = calculate_greeks(110, 100, 0.0, 0.05, 0.20, "call")
        assert g.gamma == 0.0
        assert g.theta == 0.0
        assert g.vega == 0.0
        assert g.delta == 1.0  # ITM call

    def test_greeks_to_dict(self):
        """Greeks.to_dict() returns proper dict."""
        g = calculate_greeks(100, 100, 1.0, 0.05, 0.20, "call")
        d = g.to_dict()
        assert "price" in d
        assert "delta" in d
        assert "gamma" in d
        assert "theta" in d
        assert "vega" in d
        assert "rho" in d


class TestImpliedVolatility:
    """Tests for IV solver."""

    def test_roundtrip_call(self):
        """BSM price → IV → BSM price roundtrip for call."""
        sigma = 0.25
        price = black_scholes_price(100, 100, 0.5, 0.05, sigma, "call")
        iv = implied_volatility(price, 100, 100, 0.5, 0.05, "call")
        assert abs(iv - sigma) < 0.001

    def test_roundtrip_put(self):
        """BSM price → IV → BSM price roundtrip for put."""
        sigma = 0.30
        price = black_scholes_price(100, 100, 0.5, 0.05, sigma, "put")
        iv = implied_volatility(price, 100, 100, 0.5, 0.05, "put")
        assert abs(iv - sigma) < 0.001

    def test_itm_call_iv(self):
        """IV solver works for ITM call."""
        sigma = 0.20
        price = black_scholes_price(110, 100, 0.5, 0.05, sigma, "call")
        iv = implied_volatility(price, 110, 100, 0.5, 0.05, "call")
        assert abs(iv - sigma) < 0.001

    def test_otm_put_iv(self):
        """IV solver works for OTM put."""
        sigma = 0.35
        price = black_scholes_price(110, 100, 0.5, 0.05, sigma, "put")
        iv = implied_volatility(price, 110, 100, 0.5, 0.05, "put")
        assert abs(iv - sigma) < 0.005

    def test_iv_at_expiry_raises(self):
        """Cannot solve IV at expiry."""
        with pytest.raises(ValueError, match="T <= 0"):
            implied_volatility(10.0, 100, 90, 0.0, 0.05, "call")

    def test_iv_below_intrinsic_raises(self):
        """Price below intrinsic value raises error."""
        # For a call with S=100, K=50 (deep ITM), intrinsic ≈ 50*exp(-rT) ≈ 48.78
        # Price of 0.01 is well below intrinsic
        with pytest.raises(ValueError):
            implied_volatility(0.01, 100, 50, 0.5, 0.05, "call")

    def test_high_iv_roundtrip(self):
        """IV solver works for high volatility."""
        sigma = 1.5
        price = black_scholes_price(100, 100, 0.5, 0.05, sigma, "call")
        iv = implied_volatility(price, 100, 100, 0.5, 0.05, "call")
        assert abs(iv - sigma) < 0.01
