"""CLI interface for the options simulator.

Provides a command-line interface for AI agents and humans to interact
with the simulation. State is persisted between calls via session files.

Usage:
    options-sim start --symbol SPY --date 2024-01-15 --cash 100000
    options-sim status
    options-sim chain SPY 2024-01-19
    options-sim order buy SPY240119C00470000 10 --limit 2.50
    options-sim step 30
    options-sim positions
    options-sim account
    options-sim history
    options-sim export results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SESSION_DIR = Path.home() / ".options-sim"
SESSION_FILE = SESSION_DIR / "session.json"


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


def _load_session() -> dict | None:
    """Load session state from disk."""
    if SESSION_FILE.exists():
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None


def _save_session(state: dict) -> None:
    """Save session state to disk."""
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


def _output(data: dict | list) -> None:
    """Print structured output as JSON."""
    print(json.dumps(data, indent=2, default=str))


def cmd_start(args: argparse.Namespace) -> None:
    """Start a new simulation."""
    from options_sim.engine import OptionsSimulator

    provider = _get_data_provider(args.data)
    sim = OptionsSimulator(
        data_provider=provider,
        initial_cash=args.cash,
        fill_model=args.fill_model,
    )

    result = sim.start(symbol=args.symbol, start_date=args.date)
    _save_session(sim.to_state())
    _output(result)


def cmd_status(args: argparse.Namespace) -> None:
    """Get current simulation status."""
    sim = _get_simulator()
    _output(sim.get_status())


def cmd_chain(args: argparse.Namespace) -> None:
    """Get option chain."""
    sim = _get_simulator()
    _output(sim.get_chain(symbol=args.symbol, expiry=args.expiry))


def cmd_order(args: argparse.Namespace) -> None:
    """Submit an order."""
    sim = _get_simulator()
    result = sim.submit_order(
        contract=args.contract,
        side=args.side,
        quantity=args.quantity,
        limit_price=args.limit,
    )
    _save_session(sim.to_state())
    _output(result)


def cmd_step(args: argparse.Namespace) -> None:
    """Advance simulation time."""
    sim = _get_simulator()
    result = sim.step(minutes=args.minutes)
    _save_session(sim.to_state())
    _output(result)


def cmd_positions(args: argparse.Namespace) -> None:
    """Get current positions."""
    sim = _get_simulator()
    _output(sim.get_positions())


def cmd_account(args: argparse.Namespace) -> None:
    """Get account summary."""
    sim = _get_simulator()
    _output(sim.get_account())


def cmd_history(args: argparse.Namespace) -> None:
    """Get trade history."""
    sim = _get_simulator()
    _output(sim.get_history())


def cmd_export(args: argparse.Namespace) -> None:
    """Export simulation results."""
    sim = _get_simulator()
    result = {
        "status": sim.get_status(),
        "trade_history": sim.get_history(),
        "portfolio": sim.get_positions(),
    }
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps({"exported": str(output_path), "trades": len(sim.trade_history)}))


def cmd_reset(args: argparse.Namespace) -> None:
    """Reset/clear the current session."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    print(json.dumps({"status": "session cleared"}))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="options-sim",
        description="Options Trading Simulation Environment",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start
    p_start = subparsers.add_parser("start", help="Start a new simulation")
    p_start.add_argument("--symbol", "-s", required=True, help="Underlying symbol (e.g. SPY)")
    p_start.add_argument("--date", "-d", required=True, help="Start date (YYYY-MM-DD)")
    p_start.add_argument("--cash", "-c", type=float, default=100000, help="Initial cash (default: 100000)")
    p_start.add_argument("--fill-model", "-f", default="midpoint", choices=["midpoint", "aggressive", "passive"])
    p_start.add_argument("--data", help="Path to data directory or file")
    p_start.set_defaults(func=cmd_start)

    # status
    p_status = subparsers.add_parser("status", help="Get simulation status")
    p_status.set_defaults(func=cmd_status)

    # chain
    p_chain = subparsers.add_parser("chain", help="Get option chain")
    p_chain.add_argument("symbol", nargs="?", help="Underlying symbol")
    p_chain.add_argument("expiry", nargs="?", help="Expiry date (YYYY-MM-DD)")
    p_chain.set_defaults(func=cmd_chain)

    # order
    p_order = subparsers.add_parser("order", help="Submit an order")
    p_order.add_argument("side", choices=["buy", "sell"], help="Order side")
    p_order.add_argument("contract", help="OCC option symbol")
    p_order.add_argument("quantity", type=int, help="Number of contracts")
    p_order.add_argument("--limit", type=float, help="Limit price")
    p_order.set_defaults(func=cmd_order)

    # step
    p_step = subparsers.add_parser("step", help="Advance simulation time")
    p_step.add_argument("minutes", type=int, nargs="?", default=15, help="Minutes to advance (default: 15)")
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

    # export
    p_export = subparsers.add_parser("export", help="Export results to JSON")
    p_export.add_argument("output", help="Output file path")
    p_export.set_defaults(func=cmd_export)

    # reset
    p_reset = subparsers.add_parser("reset", help="Clear current session")
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
