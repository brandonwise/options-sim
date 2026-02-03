"""CLI interface for the options simulator.

Supports both historical replay mode (original) and live market data mode.
State is persisted between calls via session files.

Usage — Historical replay:
    options-sim start --symbol SPY --date 2024-01-15 --cash 100000
    options-sim step 30
    options-sim chain SPY 2024-01-19
    options-sim order buy SPY240119C00470000 10 --limit 2.50
    options-sim positions
    options-sim account
    options-sim history
    options-sim export results.json

Usage — Live mode:
    options-sim start --live --cash 100000
    options-sim quote NVDA
    options-sim chain NVDA 2024-02-16 --strikes 5
    options-sim order buy NVDA240216C00875000 2
    options-sim positions
    options-sim account
    options-sim history
    options-sim scan NVDA --high-iv
    options-sim export --format json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SESSION_DIR = Path.home() / ".options-sim"
SESSION_FILE = SESSION_DIR / "session.json"
LIVE_SESSION_FILE = SESSION_DIR / "live-session.json"


def _get_data_provider(data_source: str | None = None):
    """Create appropriate data provider based on source."""
    from options_sim.data.csv_loader import CsvDataProvider

    # Check for data source argument or session state
    if data_source:
        path = Path(data_source)
        if path.exists():
            return CsvDataProvider(path)

    # Check for sample data
    pkg_dir = Path(__file__).parent.parent.parent
    sample_dir = pkg_dir / "data" / "sample"
    if sample_dir.exists():
        return CsvDataProvider(sample_dir)

    # Check for env-configured data dir
    data_dir = os.environ.get("OPTIONS_SIM_DATA")
    if data_dir and Path(data_dir).exists():
        return CsvDataProvider(Path(data_dir))

    # Try Polygon if API key available
    polygon_key = os.environ.get("POLYGON_API_KEY")
    if polygon_key:
        from options_sim.data.polygon import PolygonDataProvider

        return PolygonDataProvider(api_key=polygon_key)

    raise RuntimeError(
        "No data source available. Either:\n"
        "  1. Generate sample data: python scripts/generate_sample.py\n"
        "  2. Set OPTIONS_SIM_DATA=/path/to/csv/files\n"
        "  3. Set POLYGON_API_KEY for live data\n"
        "  4. Use --data /path/to/data with the start command"
    )


def _is_live_session() -> bool:
    """Check if a live session exists."""
    return LIVE_SESSION_FILE.exists()


def _is_historical_session() -> bool:
    """Check if a historical session exists."""
    return SESSION_FILE.exists()


def _load_session() -> dict | None:
    """Load historical session state from disk."""
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None


def _save_session(state: dict) -> None:
    """Save historical session state to disk."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _get_simulator(data_source: str | None = None):
    """Get simulator, restoring from session if available."""
    from options_sim.engine import OptionsSimulator

    provider = _get_data_provider(data_source)
    sim = OptionsSimulator(data_provider=provider)

    session = _load_session()
    if session:
        sim.load_state(session)

    return sim


def _get_live_engine(initial_cash: float = 100000.0):
    """Get live engine, resuming session if it exists."""
    from options_sim.live_engine import LiveEngine

    engine = LiveEngine(initial_cash=initial_cash)
    if LIVE_SESSION_FILE.exists():
        engine.resume()
    return engine


def _output(data: dict | list) -> None:
    """Print structured output as JSON."""
    print(json.dumps(data, indent=2, default=str))


# ─── Start ─────────────────────────────────────────────────────

def cmd_start(args: argparse.Namespace) -> None:
    """Start a new simulation (historical or live)."""
    if args.live:
        _cmd_start_live(args)
    else:
        _cmd_start_historical(args)


def _cmd_start_historical(args: argparse.Namespace) -> None:
    """Start a new historical simulation."""
    from options_sim.engine import OptionsSimulator

    if not args.symbol or not args.date:
        _output({"error": "Historical mode requires --symbol and --date"})
        sys.exit(1)

    provider = _get_data_provider(args.data)
    sim = OptionsSimulator(
        data_provider=provider,
        initial_cash=args.cash,
        fill_model=args.fill_model,
    )

    result = sim.start(symbol=args.symbol, start_date=args.date)
    _save_session(sim.to_state())
    _output(result)


def _cmd_start_live(args: argparse.Namespace) -> None:
    """Start a new live trading session."""
    from options_sim.live_engine import LiveEngine

    engine = LiveEngine(initial_cash=args.cash)
    result = engine.start()
    _output(result)


# ─── Quote ─────────────────────────────────────────────────────

def cmd_quote(args: argparse.Namespace) -> None:
    """Get a live stock quote."""
    from options_sim.data.polygon_live import PolygonLiveProvider

    api = PolygonLiveProvider()
    quote = api.get_stock_quote(args.symbol.upper())
    _output(quote)


# ─── Status ────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    """Get current simulation/session status."""
    if _is_live_session():
        engine = _get_live_engine()
        _output(engine.get_status())
    elif _is_historical_session():
        sim = _get_simulator()
        _output(sim.get_status())
    else:
        _output({"error": "No active session. Start one with: options-sim start"})
        sys.exit(1)


# ─── Chain ─────────────────────────────────────────────────────

def cmd_chain(args: argparse.Namespace) -> None:
    """Get option chain (live or historical)."""
    if _is_live_session():
        _cmd_chain_live(args)
    elif _is_historical_session():
        _cmd_chain_historical(args)
    else:
        # Default to live if no session
        _cmd_chain_live(args)


def _cmd_chain_historical(args: argparse.Namespace) -> None:
    """Get chain from historical simulator."""
    sim = _get_simulator()
    _output(sim.get_chain(symbol=args.symbol, expiry=args.expiry))


def _cmd_chain_live(args: argparse.Namespace) -> None:
    """Get chain from live API."""
    from options_sim.data.polygon_live import PolygonLiveProvider

    if not args.symbol:
        _output({"error": "Symbol required for chain command"})
        sys.exit(1)

    api = PolygonLiveProvider()
    symbol = args.symbol.upper()
    underlying_price = api.get_underlying_price(symbol)
    chain = api.get_option_chain(
        symbol,
        expiry=args.expiry,
        strikes_around_atm=args.strikes,
    )

    expiries = sorted(set(q["expiry"] for q in chain if q.get("expiry")))

    _output({
        "underlying": symbol,
        "underlying_price": underlying_price,
        "expiries": expiries,
        "chain": chain,
        "count": len(chain),
    })


# ─── Order ─────────────────────────────────────────────────────

def cmd_order(args: argparse.Namespace) -> None:
    """Submit an order."""
    if _is_live_session():
        engine = _get_live_engine()
        result = engine.submit_order(
            contract=args.contract,
            side=args.side,
            quantity=args.quantity,
            limit_price=args.limit,
        )
        _output(result)
    elif _is_historical_session():
        sim = _get_simulator()
        result = sim.submit_order(
            contract=args.contract,
            side=args.side,
            quantity=args.quantity,
            limit_price=args.limit,
        )
        _save_session(sim.to_state())
        _output(result)
    else:
        _output({"error": "No active session. Start one first."})
        sys.exit(1)


# ─── Step ──────────────────────────────────────────────────────

def cmd_step(args: argparse.Namespace) -> None:
    """Advance simulation time (historical mode only)."""
    if _is_live_session():
        _output({"error": "Step not available in live mode — time is real."})
        sys.exit(1)

    sim = _get_simulator()
    result = sim.step(minutes=args.minutes)
    _save_session(sim.to_state())
    _output(result)


# ─── Positions ─────────────────────────────────────────────────

def cmd_positions(args: argparse.Namespace) -> None:
    """Get current positions."""
    if _is_live_session():
        engine = _get_live_engine()
        _output(engine.get_positions())
    elif _is_historical_session():
        sim = _get_simulator()
        _output(sim.get_positions())
    else:
        _output({"error": "No active session."})
        sys.exit(1)


# ─── Account ───────────────────────────────────────────────────

def cmd_account(args: argparse.Namespace) -> None:
    """Get account summary."""
    if _is_live_session():
        engine = _get_live_engine()
        _output(engine.get_account())
    elif _is_historical_session():
        sim = _get_simulator()
        _output(sim.get_account())
    else:
        _output({"error": "No active session."})
        sys.exit(1)


# ─── History ───────────────────────────────────────────────────

def cmd_history(args: argparse.Namespace) -> None:
    """Get trade history."""
    if _is_live_session():
        engine = _get_live_engine()
        _output(engine.get_history())
    elif _is_historical_session():
        sim = _get_simulator()
        _output(sim.get_history())
    else:
        _output({"error": "No active session."})
        sys.exit(1)


# ─── Export ────────────────────────────────────────────────────

def cmd_export(args: argparse.Namespace) -> None:
    """Export simulation/session results."""
    output_path = Path(args.output) if args.output else None

    if _is_live_session():
        engine = _get_live_engine()
        result = {
            "mode": "live",
            "status": engine.get_status(),
            "trade_history": engine.get_history(),
            "portfolio": engine.get_positions(),
        }
    elif _is_historical_session():
        sim = _get_simulator()
        result = {
            "mode": "historical",
            "status": sim.get_status(),
            "trade_history": sim.get_history(),
            "portfolio": sim.get_positions(),
        }
    else:
        _output({"error": "No active session."})
        sys.exit(1)

    fmt = getattr(args, "format", "json") or "json"

    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        _output({"exported": str(output_path), "format": fmt})
    else:
        _output(result)


# ─── Scan ──────────────────────────────────────────────────────

def cmd_scan(args: argparse.Namespace) -> None:
    """Scan for options opportunities."""
    from options_sim.data.polygon_live import PolygonLiveProvider
    from options_sim.scanner import (
        scan_earnings_plays,
        scan_high_iv,
        scan_high_theta,
        scan_near_money,
        scan_unusual_volume,
    )

    api = PolygonLiveProvider()
    symbol = args.symbol.upper()

    if args.high_iv:
        results = scan_high_iv(symbol, api, expiry=args.expiry)
        scan_type = "high_iv"
    elif args.unusual_volume:
        results = scan_unusual_volume(symbol, api, expiry=args.expiry)
        scan_type = "unusual_volume"
    elif args.high_theta:
        results = scan_high_theta(symbol, api, expiry=args.expiry)
        scan_type = "high_theta"
    elif args.near_money:
        results = scan_near_money(symbol, api, expiry=args.expiry)
        scan_type = "near_money"
    elif args.earnings:
        results = scan_earnings_plays(symbol, api)
        scan_type = "earnings"
    else:
        # Default: near-money scan
        results = scan_near_money(symbol, api, expiry=args.expiry)
        scan_type = "near_money"

    _output({
        "symbol": symbol,
        "scan_type": scan_type,
        "results": results[:args.limit] if args.limit else results,
        "total_matches": len(results),
    })


# ─── Reset ─────────────────────────────────────────────────────

def cmd_reset(args: argparse.Namespace) -> None:
    """Reset/clear sessions."""
    cleared = []
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        cleared.append("historical")
    if LIVE_SESSION_FILE.exists():
        LIVE_SESSION_FILE.unlink()
        cleared.append("live")
    if cleared:
        _output({"status": f"Cleared: {', '.join(cleared)} session(s)"})
    else:
        _output({"status": "No active sessions to clear"})


# ─── Parser ────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="options-sim",
        description="Options Trading Simulation Environment — Historical Replay & Live Mode",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start
    p_start = subparsers.add_parser("start", help="Start a new session")
    p_start.add_argument("--live", action="store_true", help="Start in live market data mode")
    p_start.add_argument("--symbol", "-s", help="Underlying symbol (required for historical)")
    p_start.add_argument("--date", "-d", help="Start date YYYY-MM-DD (required for historical)")
    p_start.add_argument("--cash", "-c", type=float, default=100000, help="Initial cash (default: 100000)")
    p_start.add_argument("--fill-model", "-f", default="midpoint", choices=["midpoint", "aggressive", "passive"])
    p_start.add_argument("--data", help="Path to data directory (historical mode)")
    p_start.set_defaults(func=cmd_start)

    # quote (live only)
    p_quote = subparsers.add_parser("quote", help="Get a live stock quote")
    p_quote.add_argument("symbol", help="Stock ticker (e.g. AAPL)")
    p_quote.set_defaults(func=cmd_quote)

    # status
    p_status = subparsers.add_parser("status", help="Get session status")
    p_status.set_defaults(func=cmd_status)

    # chain
    p_chain = subparsers.add_parser("chain", help="Get option chain")
    p_chain.add_argument("symbol", nargs="?", help="Underlying symbol")
    p_chain.add_argument("expiry", nargs="?", help="Expiry date (YYYY-MM-DD)")
    p_chain.add_argument("--strikes", type=int, help="N strikes around ATM")
    p_chain.set_defaults(func=cmd_chain)

    # order
    p_order = subparsers.add_parser("order", help="Submit an order")
    p_order.add_argument("side", choices=["buy", "sell"], help="Order side")
    p_order.add_argument("contract", help="OCC option symbol")
    p_order.add_argument("quantity", type=int, help="Number of contracts")
    p_order.add_argument("--limit", type=float, help="Limit price")
    p_order.set_defaults(func=cmd_order)

    # step (historical only)
    p_step = subparsers.add_parser("step", help="Advance simulation time (historical)")
    p_step.add_argument("minutes", type=int, nargs="?", default=15, help="Minutes (default: 15)")
    p_step.set_defaults(func=cmd_step)

    # positions
    p_pos = subparsers.add_parser("positions", help="Get current positions")
    p_pos.set_defaults(func=cmd_positions)

    # account
    p_acct = subparsers.add_parser("account", help="Get account summary")
    p_acct.set_defaults(func=cmd_account)

    # history
    p_hist = subparsers.add_parser("history", help="Get trade history")
    p_hist.set_defaults(func=cmd_history)

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan for options opportunities")
    p_scan.add_argument("symbol", help="Underlying symbol")
    p_scan.add_argument("--high-iv", action="store_true", help="High implied volatility")
    p_scan.add_argument("--unusual-volume", action="store_true", help="Unusual volume vs OI")
    p_scan.add_argument("--high-theta", action="store_true", help="High theta decay")
    p_scan.add_argument("--near-money", action="store_true", help="Near-the-money contracts")
    p_scan.add_argument("--earnings", action="store_true", help="Near-term elevated IV")
    p_scan.add_argument("--expiry", help="Filter by expiry date")
    p_scan.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p_scan.set_defaults(func=cmd_scan)

    # export
    p_export = subparsers.add_parser("export", help="Export results")
    p_export.add_argument("output", nargs="?", help="Output file path")
    p_export.add_argument("--format", choices=["json"], default="json", help="Export format")
    p_export.set_defaults(func=cmd_export)

    # reset
    p_reset = subparsers.add_parser("reset", help="Clear all sessions")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except RuntimeError as e:
        _output({"error": str(e)})
        sys.exit(1)
    except Exception as e:
        _output({"error": str(e), "type": type(e).__name__})
        sys.exit(1)


if __name__ == "__main__":
    main()
