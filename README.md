# options-sim

Options trading simulation environment for AI agents — historical replay, realistic execution, Greeks calculation.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## What is this?

`options-sim` is a Python library and CLI tool that simulates options trading with:

- **Historical replay** — step through market data minute-by-minute
- **Realistic execution** — multiple fill models (midpoint, aggressive, passive) with slippage
- **Full Greeks** — Black-Scholes pricing with delta, gamma, theta, vega, rho
- **Multiple data sources** — CSV files, Polygon.io, ThetaData, or synthetic data
- **Portfolio tracking** — positions, P&L, margin, aggregate Greeks
- **AI-agent friendly** — JSON output, CLI interface, session persistence

Built for AI agents to practice and test options strategies in a sandboxed environment.

## Quick Start

```bash
# Install
pip install -e .

# Generate sample data
python scripts/generate_sample.py

# Start a simulation
options-sim start --symbol SPY --date 2024-01-15 --cash 100000

# Get the option chain
options-sim chain SPY 2024-01-19

# Buy 10 ATM call contracts
options-sim order buy SPY240119C00475000 10

# Advance time 30 minutes
options-sim step 30

# Check your positions
options-sim positions

# Check account
options-sim account
```

## Installation

```bash
# Minimal (pure Python BSM, CSV data only)
pip install -e .

# With Polygon.io support
pip install -e ".[polygon]"

# With py_vollib for faster pricing
pip install -e ".[vollib]"

# Everything
pip install -e ".[all]"

# Development
pip install -e ".[dev]"
```

### Dependencies

**Runtime (minimal):**
- `numpy>=1.24.0`
- `pandas>=2.0.0`

**Optional:**
- `py_vollib>=1.0.0` — Faster Black-Scholes (pure Python fallback included)
- `requests>=2.28.0` — For Polygon.io and ThetaData API access
- `pyarrow>=12.0.0` — For Parquet file support

## Architecture

```
┌─────────────────────────────────────────┐
│              CLI Interface              │
│         (options-sim commands)           │
├─────────────────────────────────────────┤
│           Simulation Engine             │
│   (time-stepping, order management)     │
├──────────┬──────────┬───────────────────┤
│ Portfolio│ Execution│    Pricing        │
│ (P&L,    │ (fill    │  (BSM, Greeks,   │
│  Greeks) │  models) │   IV solver)     │
├──────────┴──────────┴───────────────────┤
│             Data Layer                  │
│  (CSV, Polygon.io, ThetaData, Mock)    │
└─────────────────────────────────────────┘
```

### Core Components

- **`engine.py`** — `OptionsSimulator` orchestrates the simulation
- **`portfolio.py`** — Position tracking, P&L, aggregate Greeks
- **`pricing.py`** — Pure Python Black-Scholes with all Greeks
- **`execution.py`** — Fill models with slippage and liquidity checks
- **`data/`** — Pluggable data providers (CSV, Polygon, ThetaData)
- **`cli.py`** — Command-line interface with session persistence

## Data Sources

### Sample Data (No API Key Needed)

```bash
python scripts/generate_sample.py
```

Generates 5 days of synthetic SPY options data with realistic:
- Bid/ask spreads (tighter ATM, wider OTM)
- Volume patterns (highest near ATM)
- Greeks from BSM pricing
- Volatility smile

### Polygon.io

```bash
export POLYGON_API_KEY=your_key_here
python scripts/download_data.py --source polygon --symbol SPY --start 2024-01-15
```

### ThetaData

```bash
export THETADATA_API_KEY=your_key_here
python scripts/download_data.py --source thetadata --symbol SPY --start 2024-01-15
```

### Custom CSV

Place CSV files in any directory with columns:

```
timestamp, symbol, underlying, strike, expiry, option_type,
bid, ask, last, volume, open_interest, iv, delta, gamma, theta, vega
```

Then start with:

```bash
options-sim start --symbol SPY --date 2024-01-15 --data /path/to/csv/
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `options-sim start` | Start a new simulation |
| `options-sim status` | Full account and market state |
| `options-sim chain [SYM] [EXP]` | Get option chain |
| `options-sim order <side> <contract> <qty>` | Submit order |
| `options-sim step [minutes]` | Advance simulation time |
| `options-sim positions` | List open positions |
| `options-sim account` | Account summary |
| `options-sim history` | Trade history |
| `options-sim export <file>` | Export results to JSON |
| `options-sim reset` | Clear session |

All commands output JSON for easy machine parsing.

## Python API

```python
from options_sim.engine import OptionsSimulator
from options_sim.data.csv_loader import CsvDataProvider

# Set up data
provider = CsvDataProvider("data/sample/")
sim = OptionsSimulator(provider, initial_cash=100000, fill_model="midpoint")

# Start
sim.start("SPY", "2024-01-15")

# Get chain
chain = sim.get_chain(expiry="2024-01-19")

# Trade
result = sim.submit_order("SPY240119C00475000", "buy", 10)

# Advance time
sim.step(30)

# Check status
status = sim.get_status()
print(f"P&L: ${status['account']['unrealized_pnl']:.2f}")
```

## Fill Models

| Model | Buy At | Sell At | Use Case |
|-------|--------|---------|----------|
| `midpoint` | (bid+ask)/2 | (bid+ask)/2 | Balanced default |
| `aggressive` | ask | bid | Realistic market orders |
| `passive` | bid | ask | Optimistic / limit fills |

Orders >10% of daily volume incur additional slippage.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## License

MIT
