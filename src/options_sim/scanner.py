"""Options opportunity scanner.

Scans option chains for interesting trading opportunities based on
volatility, volume, proximity to money, and theta decay.

Usage:
    from options_sim.scanner import scan_high_iv, scan_unusual_volume
    from options_sim.data.polygon_live import PolygonLiveProvider

    api = PolygonLiveProvider()
    high_iv = scan_high_iv("AAPL", api)
    unusual = scan_unusual_volume("SPY", api)
"""

from __future__ import annotations

import statistics
from datetime import datetime


def scan_high_iv(
    symbol: str,
    api,
    threshold_percentile: float = 50.0,
    expiry: str | None = None,
) -> list[dict]:
    """Find options with implied volatility above a percentile threshold.

    Identifies contracts with unusually high IV relative to the rest
    of the chain — potential candidates for selling premium.

    Args:
        symbol: Underlying ticker.
        api: PolygonLiveProvider instance.
        threshold_percentile: IV percentile threshold (0-100).
        expiry: Optional expiry filter.

    Returns:
        List of option dicts with IV above threshold, sorted by IV descending.
    """
    chain = api.get_option_chain(symbol, expiry=expiry)
    if not chain:
        return []

    # Collect all IVs
    ivs = [q["iv"] for q in chain if q.get("iv", 0) > 0]
    if not ivs:
        return []

    # Calculate threshold value
    sorted_ivs = sorted(ivs)
    idx = int(len(sorted_ivs) * threshold_percentile / 100.0)
    idx = min(idx, len(sorted_ivs) - 1)
    iv_threshold = sorted_ivs[idx]

    results = [
        {**q, "scan_type": "high_iv", "iv_percentile": _percentile_rank(q["iv"], sorted_ivs)}
        for q in chain
        if q.get("iv", 0) >= iv_threshold and q.get("bid", 0) > 0
    ]

    return sorted(results, key=lambda x: x.get("iv", 0), reverse=True)


def scan_unusual_volume(
    symbol: str,
    api,
    volume_oi_ratio: float = 2.0,
    expiry: str | None = None,
) -> list[dict]:
    """Find options with unusually high volume relative to open interest.

    Volume significantly exceeding OI often signals institutional activity
    or upcoming catalysts.

    Args:
        symbol: Underlying ticker.
        api: PolygonLiveProvider instance.
        volume_oi_ratio: Minimum volume/OI ratio (default 2.0x).
        expiry: Optional expiry filter.

    Returns:
        List of option dicts with unusual volume, sorted by vol/OI ratio.
    """
    chain = api.get_option_chain(symbol, expiry=expiry)
    if not chain:
        return []

    results = []
    for q in chain:
        volume = q.get("volume", 0)
        oi = q.get("open_interest", 0)
        if volume > 0 and oi > 0:
            ratio = volume / oi
            if ratio >= volume_oi_ratio:
                results.append({
                    **q,
                    "scan_type": "unusual_volume",
                    "volume_oi_ratio": round(ratio, 2),
                })

    return sorted(results, key=lambda x: x.get("volume_oi_ratio", 0), reverse=True)


def scan_near_money(
    symbol: str,
    api,
    range_pct: float = 5.0,
    expiry: str | None = None,
) -> list[dict]:
    """Find options with strikes within a percentage of current price.

    Near-the-money options have the highest gamma and are most sensitive
    to underlying price moves.

    Args:
        symbol: Underlying ticker.
        api: PolygonLiveProvider instance.
        range_pct: Percentage range around current price.
        expiry: Optional expiry filter.

    Returns:
        List of near-money option dicts, sorted by distance from ATM.
    """
    try:
        underlying_price = api.get_underlying_price(symbol)
    except Exception:
        return []

    chain = api.get_option_chain(symbol, expiry=expiry)
    if not chain:
        return []

    lower = underlying_price * (1 - range_pct / 100.0)
    upper = underlying_price * (1 + range_pct / 100.0)

    results = []
    for q in chain:
        strike = q.get("strike", 0)
        if lower <= strike <= upper and q.get("bid", 0) > 0:
            distance_pct = abs(strike - underlying_price) / underlying_price * 100
            results.append({
                **q,
                "scan_type": "near_money",
                "distance_from_atm_pct": round(distance_pct, 2),
                "underlying_price": underlying_price,
            })

    return sorted(results, key=lambda x: x.get("distance_from_atm_pct", 999))


def scan_high_theta(
    symbol: str,
    api,
    min_theta: float | None = None,
    expiry: str | None = None,
) -> list[dict]:
    """Find options with high theta decay — best candidates for selling.

    High theta means rapid time decay, which benefits premium sellers.

    Args:
        symbol: Underlying ticker.
        api: PolygonLiveProvider instance.
        min_theta: Minimum absolute theta value. If None, uses median.
        expiry: Optional expiry filter.

    Returns:
        List of high-theta option dicts, sorted by absolute theta descending.
    """
    chain = api.get_option_chain(symbol, expiry=expiry)
    if not chain:
        return []

    # Theta is negative for long options; we want absolute value
    thetas = [abs(q.get("theta", 0)) for q in chain if q.get("theta", 0) != 0]
    if not thetas:
        return []

    if min_theta is None:
        min_theta = statistics.median(thetas)

    results = []
    for q in chain:
        abs_theta = abs(q.get("theta", 0))
        if abs_theta >= min_theta and q.get("bid", 0) > 0:
            results.append({
                **q,
                "scan_type": "high_theta",
                "abs_theta": round(abs_theta, 6),
            })

    return sorted(results, key=lambda x: x.get("abs_theta", 0), reverse=True)


def scan_earnings_plays(
    symbol: str,
    api,
    max_dte: int = 30,
) -> list[dict]:
    """Find options expiring near potential earnings dates.

    Looks for options with elevated IV in the near-term expirations,
    which often indicates upcoming earnings or catalysts.

    Args:
        symbol: Underlying ticker.
        api: PolygonLiveProvider instance.
        max_dte: Maximum days to expiry to scan.

    Returns:
        List of near-term high-IV options, sorted by expiry then IV.
    """
    chain = api.get_option_chain(symbol)
    if not chain:
        return []

    today = datetime.now()
    results = []

    for q in chain:
        expiry_str = q.get("expiry", "")
        if not expiry_str:
            continue
        try:
            expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
            dte = (expiry_dt - today).days
        except ValueError:
            continue

        if 0 < dte <= max_dte and q.get("iv", 0) > 0 and q.get("bid", 0) > 0:
            results.append({
                **q,
                "scan_type": "earnings",
                "dte": dte,
            })

    # Sort by expiry then IV descending
    return sorted(results, key=lambda x: (x.get("dte", 999), -x.get("iv", 0)))


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Calculate percentile rank of a value in a sorted list.

    Args:
        value: Value to rank.
        sorted_values: Sorted list of values.

    Returns:
        Percentile rank (0-100).
    """
    if not sorted_values:
        return 0.0
    count_below = sum(1 for v in sorted_values if v < value)
    return round(count_below / len(sorted_values) * 100, 1)
