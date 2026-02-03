"""Tests for CLI live mode commands.

Tests the CLI argument parsing and command routing for live mode.
Uses mocked engines/APIs — no real network calls.
"""

from __future__ import annotations

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from options_sim.cli import build_parser, cmd_quote, cmd_reset, cmd_scan


class TestCLIParser:
    """Tests for CLI argument parsing."""

    def test_start_live_flag(self):
        """--live flag is parsed."""
        parser = build_parser()
        args = parser.parse_args(["start", "--live", "--cash", "50000"])
        assert args.live is True
        assert args.cash == 50000

    def test_start_historical(self):
        """Historical start requires --symbol and --date."""
        parser = build_parser()
        args = parser.parse_args(["start", "--symbol", "SPY", "--date", "2024-01-15"])
        assert args.symbol == "SPY"
        assert args.date == "2024-01-15"
        assert args.live is False

    def test_quote_command(self):
        """Quote command parses symbol."""
        parser = build_parser()
        args = parser.parse_args(["quote", "NVDA"])
        assert args.symbol == "NVDA"

    def test_chain_with_strikes(self):
        """Chain with --strikes flag."""
        parser = build_parser()
        args = parser.parse_args(["chain", "AAPL", "2024-01-19", "--strikes", "5"])
        assert args.symbol == "AAPL"
        assert args.expiry == "2024-01-19"
        assert args.strikes == 5

    def test_scan_high_iv(self):
        """Scan --high-iv flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "NVDA", "--high-iv"])
        assert args.symbol == "NVDA"
        assert args.high_iv is True

    def test_scan_unusual_volume(self):
        """Scan --unusual-volume flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "SPY", "--unusual-volume"])
        assert args.unusual_volume is True

    def test_scan_earnings(self):
        """Scan --earnings flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "SPY", "--earnings"])
        assert args.earnings is True

    def test_scan_limit(self):
        """Scan --limit flag."""
        parser = build_parser()
        args = parser.parse_args(["scan", "AAPL", "--high-iv", "--limit", "10"])
        assert args.limit == 10

    def test_export_with_format(self):
        """Export --format flag."""
        parser = build_parser()
        args = parser.parse_args(["export", "--format", "json"])
        assert args.format == "json"

    def test_order_command(self):
        """Order command parses all args."""
        parser = build_parser()
        args = parser.parse_args(["order", "buy", "SPY240119C00475000", "10", "--limit", "2.50"])
        assert args.side == "buy"
        assert args.contract == "SPY240119C00475000"
        assert args.quantity == 10
        assert args.limit == 2.50


class TestCLIReset:
    """Tests for reset command."""

    def test_reset_clears_sessions(self, tmp_path):
        """Reset clears session files."""
        # Create mock session files
        session = tmp_path / "session.json"
        live_session = tmp_path / "live-session.json"
        session.write_text("{}")
        live_session.write_text("{}")

        with patch("options_sim.cli.SESSION_FILE", session), \
             patch("options_sim.cli.LIVE_SESSION_FILE", live_session):
            parser = build_parser()
            args = parser.parse_args(["reset"])

            # Capture output
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                cmd_reset(args)
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            result = json.loads(output)
            assert "Cleared" in result["status"]
            assert not session.exists()
            assert not live_session.exists()
