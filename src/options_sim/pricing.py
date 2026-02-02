"""Black-Scholes pricing and Greeks calculation.

Provides a pure-Python BSM implementation with optional py_vollib acceleration.
All public functions work without py_vollib installed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Standard normal CDF and PDF using math.erf
_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


@dataclass
class Greeks:
    """Option Greeks container.

    Attributes:
        price: Theoretical option price.
        delta: Rate of change of price w.r.t. underlying price.
        gamma: Rate of change of delta w.r.t. underlying price.
        theta: Rate of change of price w.r.t. time (per calendar day).
        vega: Rate of change of price w.r.t. 1% change in volatility.
        rho: Rate of change of price w.r.t. 1% change in interest rate.
    """

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "price": round(self.price, 6),
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 6),
            "vega": round(self.vega, 6),
            "rho": round(self.rho, 6),
        }


def _d1d2(
    S: float, K: float, T: float, r: float, sigma: float
) -> tuple[float, float]:
    """Calculate d1 and d2 for Black-Scholes formula.

    Args:
        S: Underlying price.
        K: Strike price.
        T: Time to expiry in years.
        r: Risk-free interest rate (annualized).
        sigma: Volatility (annualized, e.g. 0.25 for 25%).

    Returns:
        Tuple of (d1, d2).
    """
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Calculate Black-Scholes option price.

    Args:
        S: Underlying price.
        K: Strike price.
        T: Time to expiry in years (e.g. 30/365 for 30 days).
        r: Risk-free rate (annualized, e.g. 0.05 for 5%).
        sigma: Volatility (annualized, e.g. 0.25 for 25%).
        option_type: 'call' or 'put'.

    Returns:
        Theoretical option price.
    """
    if T <= 0:
        # At expiry: intrinsic value
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)

    if option_type == "call":
        return S * _norm_cdf(d1) - K * discount * _norm_cdf(d2)
    else:
        return K * discount * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def calculate_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> Greeks:
    """Calculate all Greeks for an option.

    Args:
        S: Underlying price.
        K: Strike price.
        T: Time to expiry in years.
        r: Risk-free rate (annualized).
        sigma: Volatility (annualized).
        option_type: 'call' or 'put'.

    Returns:
        Greeks dataclass with price, delta, gamma, theta, vega, rho.
    """
    price = black_scholes_price(S, K, T, r, sigma, option_type)

    if T <= 1e-10 or sigma <= 1e-10:
        # At or very near expiry — return intrinsic value, zero Greeks
        if option_type == "call":
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return Greeks(price=price, delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    discount = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)

    # Gamma (same for calls and puts)
    gamma = pdf_d1 / (S * sigma * sqrt_T)

    # Vega (same for calls and puts) — per 1% IV move
    vega = S * pdf_d1 * sqrt_T / 100.0

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -(S * pdf_d1 * sigma) / (2.0 * sqrt_T)
            - r * K * discount * _norm_cdf(d2)
        ) / 365.0  # Per calendar day
        rho = K * T * discount * _norm_cdf(d2) / 100.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -(S * pdf_d1 * sigma) / (2.0 * sqrt_T)
            + r * K * discount * _norm_cdf(-d2)
        ) / 365.0  # Per calendar day
        rho = -K * T * discount * _norm_cdf(-d2) / 100.0

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
    )


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Calculate implied volatility using Newton-Raphson method.

    Args:
        market_price: Observed option price.
        S: Underlying price.
        K: Strike price.
        T: Time to expiry in years.
        r: Risk-free rate.
        option_type: 'call' or 'put'.
        tol: Convergence tolerance.
        max_iter: Maximum iterations.

    Returns:
        Implied volatility (annualized).

    Raises:
        ValueError: If IV cannot be solved (e.g. price below intrinsic).
    """
    if T <= 0:
        raise ValueError("Cannot solve IV at expiry (T <= 0)")

    # Intrinsic value check
    if option_type == "call":
        intrinsic = max(S - K * math.exp(-r * T), 0.0)
    else:
        intrinsic = max(K * math.exp(-r * T) - S, 0.0)

    if market_price < intrinsic - tol:
        raise ValueError(
            f"Market price {market_price:.4f} below intrinsic {intrinsic:.4f}"
        )

    # Initial guess using Brenner-Subrahmanyam approximation
    sigma = math.sqrt(2.0 * math.pi / T) * market_price / S
    sigma = max(sigma, 0.01)
    sigma = min(sigma, 5.0)

    for _ in range(max_iter):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < tol:
            return sigma

        # Vega for Newton step (raw vega, not per-percent)
        d1, _ = _d1d2(S, K, T, r, sigma)
        vega_raw = S * _norm_pdf(d1) * math.sqrt(T)

        if vega_raw < 1e-12:
            break

        sigma -= diff / vega_raw
        sigma = max(sigma, 1e-6)
        sigma = min(sigma, 10.0)

    # If we didn't converge with Newton, try bisection
    lo, hi = 0.001, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        price = black_scholes_price(S, K, T, r, mid, option_type)
        if abs(price - market_price) < tol:
            return mid
        if price > market_price:
            hi = mid
        else:
            lo = mid

    raise ValueError(
        f"IV did not converge for price={market_price:.4f}, S={S}, K={K}, T={T:.4f}"
    )
