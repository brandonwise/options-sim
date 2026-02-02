#!/usr/bin/env python3
"""Download historical options data from Polygon.io or ThetaData.

Downloads data and saves it in the canonical CSV format for offline replay.

Usage:
    # Download from Polygon
    export POLYGON_API_KEY=your_key
    python scripts/download_data.py --source polygon --symbol SPY --date 2024-01-15

    # Download from ThetaData
    export THETADATA_API_KEY=your_key
    python scripts/download_data.py --source thetadata --symbol SPY --date 2024-01-15
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def download_polygon(
    symbol: str,
    start_date: str,
    end_date: str,
    output_dir: str,
) -> None:
    """Download data from Polygon.io."""
    from options_sim.data.polygon import PolygonDataProvider

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        print("Error: POLYGON_API_KEY environment variable required")
        sys.exit(1)

    provider = PolygonDataProvider(api_key=api_key)
    _download(provider, symbol, start_date, end_date, output_dir)


def download_thetadata(
    symbol: str,
    start_date: str,
    end_date: str,
    output_dir: str,
) -> None:
    """Download data from ThetaData."""
    from options_sim.data.thetadata import ThetaDataProvider

    provider = ThetaDataProvider()
    _download(provider, symbol, start_date, end_date, output_dir)


def _download(
    provider,
    symbol: str,
    start_date: str,
    end_date: str,
    output_dir: str,
) -> None:
    """Generic download using any DataProvider."""
    os.makedirs(output_dir, exist_ok=True)

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    all_rows = []
    underlying_rows = []
    current = start

    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        print(f"Downloading {symbol} for {current.strftime('%Y-%m-%d')}...")

        try:
            timestamp = current.replace(hour=12, minute=0)
            snapshot = provider.get_snapshot(symbol, timestamp)

            underlying_rows.append({
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol,
                "price": snapshot.underlying_price,
            })

            for quote in snapshot.chain:
                all_rows.append({
                    "timestamp": quote.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": quote.symbol,
                    "underlying": quote.underlying,
                    "strike": quote.strike,
                    "expiry": quote.expiry,
                    "option_type": quote.option_type,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "last": quote.last,
                    "volume": quote.volume,
                    "open_interest": quote.open_interest,
                    "iv": round(quote.iv, 6),
                    "delta": round(quote.delta, 6),
                    "gamma": round(quote.gamma, 6),
                    "theta": round(quote.theta, 6),
                    "vega": round(quote.vega, 6),
                })

            print(f"  Got {len(snapshot.chain)} contracts")
        except Exception as e:
            print(f"  Error: {e}")

        current += timedelta(days=1)

    # Write files
    if all_rows:
        fieldnames = [
            "timestamp", "symbol", "underlying", "strike", "expiry", "option_type",
            "bid", "ask", "last", "volume", "open_interest", "iv",
            "delta", "gamma", "theta", "vega",
        ]
        with open(os.path.join(output_dir, "options.csv"), "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)

    if underlying_rows:
        with open(os.path.join(output_dir, "underlying.csv"), "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price"])
            writer.writeheader()
            writer.writerows(underlying_rows)

    print(f"\n✅ Downloaded {len(all_rows)} quotes to {output_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download historical options data")
    parser.add_argument("--source", choices=["polygon", "thetadata"], required=True)
    parser.add_argument("--symbol", required=True, help="Underlying symbol")
    parser.add_argument("--start", "--date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (default: start + 5 days)")
    parser.add_argument("--output", "-o", help="Output directory")
    args = parser.parse_args()

    end_date = args.end
    if not end_date:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = (start + timedelta(days=7)).strftime("%Y-%m-%d")

    output = args.output or str(
        Path(__file__).parent.parent / "data" / args.symbol.lower()
    )

    if args.source == "polygon":
        download_polygon(args.symbol, args.start, end_date, output)
    else:
        download_thetadata(args.symbol, args.start, end_date, output)
