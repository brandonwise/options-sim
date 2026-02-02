#!/usr/bin/env python3
"""Generate synthetic sample data for testing.

Creates realistic-looking SPY options data with:
- 5 trading days of 1-minute bars
- Multiple strikes around ATM
- Realistic bid/ask spreads
- Volume patterns (higher near ATM)
- Greeks calculated via BSM

Output: data/sample/ directory with CSV files.
"""

from __future__ import annotations

import csv
import math
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from options_sim.pricing import calculate_greeks, black_scholes_price


def generate_sample_data(
    output_dir: str | None = None,
    underlying: str = "SPY",
    base_price: float = 475.0,
    start_date: str = "2024-01-15",
    num_days: int = 5,
    risk_free_rate: float = 0.05,
) -> None:
    """Generate synthetic options data.

    Args:
        output_dir: Output directory (default: data/sample/).
        underlying: Underlying symbol.
        base_price: Starting underlying price.
        start_date: First trading day.
        num_days: Number of trading days.
        risk_free_rate: Annual risk-free rate.
    """
    if output_dir is None:
        output_dir = str(Path(__file__).parent.parent / "data" / "sample")

    os.makedirs(output_dir, exist_ok=True)

    start = datetime.strptime(start_date, "%Y-%m-%d")

    # Generate expiration dates (weekly Fridays)
    expiries = []
    d = start
    for _ in range(4):
        # Find next Friday
        days_ahead = 4 - d.weekday()  # Friday = 4
        if days_ahead <= 0:
            days_ahead += 7
        friday = d + timedelta(days=days_ahead)
        expiries.append(friday.strftime("%Y-%m-%d"))
        d = friday + timedelta(days=1)

    # Strike range: +/- 5% from base price, $1 increments
    strikes = []
    low = int(base_price * 0.95)
    high = int(base_price * 1.05)
    for s in range(low, high + 1):
        strikes.append(float(s))

    # Generate underlying price path (random walk with drift)
    random.seed(42)  # Reproducible
    all_rows = []
    underlying_prices = []

    current_price = base_price
    trading_day = start

    for day_num in range(num_days):
        # Skip weekends
        while trading_day.weekday() >= 5:
            trading_day += timedelta(days=1)

        # Daily drift and volatility
        daily_drift = random.gauss(0.0001, 0.002)  # Slight upward bias
        intraday_vol = 0.001  # 0.1% per minute

        market_open = trading_day.replace(hour=9, minute=30, second=0)
        market_close = trading_day.replace(hour=16, minute=0, second=0)

        # Generate 1-minute bars
        t = market_open
        while t <= market_close:
            # Random walk for underlying
            current_price *= 1 + random.gauss(daily_drift / 390, intraday_vol)
            current_price = round(current_price, 2)

            underlying_prices.append({
                "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": underlying,
                "price": current_price,
            })

            # Generate options for each strike/expiry/type
            for expiry in expiries:
                expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
                T = max((expiry_dt - t).total_seconds() / (365.25 * 86400), 1e-10)

                if T < 0:
                    continue  # Skip expired

                for strike in strikes:
                    for opt_type in ("call", "put"):
                        # Base IV: smile shape
                        moneyness = current_price / strike
                        base_iv = 0.18 + 0.05 * (moneyness - 1.0) ** 2
                        # Add some randomness
                        iv = base_iv + random.gauss(0, 0.005)
                        iv = max(iv, 0.05)

                        # Calculate theoretical price and Greeks
                        greeks = calculate_greeks(
                            S=current_price,
                            K=strike,
                            T=T,
                            r=risk_free_rate,
                            sigma=iv,
                            option_type=opt_type,
                        )

                        theo_price = greeks.price
                        if theo_price < 0.01:
                            continue  # Skip nearly worthless

                        # Realistic spread: wider for OTM, tighter for ATM
                        atm_dist = abs(current_price - strike) / current_price
                        spread_pct = 0.02 + atm_dist * 0.15  # 2% base, wider OTM
                        spread = max(theo_price * spread_pct, 0.01)
                        spread = min(spread, theo_price * 0.5)  # Cap at 50%

                        bid = round(max(theo_price - spread / 2, 0.01), 2)
                        ask = round(theo_price + spread / 2, 2)
                        last = round(theo_price + random.gauss(0, spread * 0.1), 2)
                        last = max(last, 0.01)

                        # Volume: higher near ATM, lower OTM
                        base_vol = max(1, int(5000 * math.exp(-10 * atm_dist ** 2)))
                        volume = max(1, int(base_vol * random.uniform(0.3, 1.7)))

                        # Open interest
                        oi = max(10, int(volume * random.uniform(5, 50)))

                        # OCC symbol: SPY240119C00470000
                        exp_str = expiry_dt.strftime("%y%m%d")
                        type_char = "C" if opt_type == "call" else "P"
                        strike_int = int(strike * 1000)
                        occ_symbol = f"{underlying}{exp_str}{type_char}{strike_int:08d}"

                        all_rows.append({
                            "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
                            "symbol": occ_symbol,
                            "underlying": underlying,
                            "strike": strike,
                            "expiry": expiry,
                            "option_type": opt_type,
                            "bid": bid,
                            "ask": ask,
                            "last": last,
                            "volume": volume,
                            "open_interest": oi,
                            "iv": round(iv, 6),
                            "delta": round(greeks.delta, 6),
                            "gamma": round(greeks.gamma, 6),
                            "theta": round(greeks.theta, 6),
                            "vega": round(greeks.vega, 6),
                        })

            # Advance by 15 minutes (not every minute, to keep file size sane)
            t += timedelta(minutes=15)

        trading_day += timedelta(days=1)
        print(f"  Day {day_num + 1}/{num_days} complete ({len(all_rows)} option rows)")

    # Write options data
    options_file = os.path.join(output_dir, "options.csv")
    fieldnames = [
        "timestamp", "symbol", "underlying", "strike", "expiry", "option_type",
        "bid", "ask", "last", "volume", "open_interest", "iv",
        "delta", "gamma", "theta", "vega",
    ]
    with open(options_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    # Write underlying prices
    underlying_file = os.path.join(output_dir, "underlying.csv")
    with open(underlying_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price"])
        writer.writeheader()
        writer.writerows(underlying_prices)

    print(f"\n✅ Generated {len(all_rows)} option quotes across {num_days} days")
    print(f"   Strikes: {len(strikes)} ({strikes[0]:.0f} - {strikes[-1]:.0f})")
    print(f"   Expiries: {len(expiries)} ({expiries[0]} - {expiries[-1]})")
    print(f"   Output: {output_dir}/")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate synthetic options data")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--symbol", default="SPY", help="Underlying symbol")
    parser.add_argument("--price", type=float, default=475.0, help="Starting price")
    parser.add_argument("--date", default="2024-01-15", help="Start date")
    parser.add_argument("--days", type=int, default=5, help="Number of trading days")
    args = parser.parse_args()

    generate_sample_data(
        output_dir=args.output,
        underlying=args.symbol,
        base_price=args.price,
        start_date=args.date,
        num_days=args.days,
    )
