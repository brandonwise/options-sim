---
name: options-sim
version: 0.1.0
description: Options trading simulation environment — historical replay with realistic execution and Greeks
triggers:
  - options
  - option chain
  - options simulator
  - options sim
  - trade options
  - black-scholes
  - greeks
  - iron condor
  - covered call
  - options strategy
requires:
  - python3
  - pip
setup: |
  cd projects/options-sim
  pip install -e ".[dev]"
  python scripts/generate_sample.py
---

# Options Trading Simulator

Simulates options trading with historical data replay, realistic execution models, and full Greeks calculation. Designed for AI agents to practice and test options strategies.

## Quick Start

```bash
# Generate sample data (first time only)
cd projects/options-sim && python scripts/generate_sample.py

# Start a simulation
options-sim start --symbol SPY --date 2024-01-15 --cash 100000

# View the option chain
options-sim chain SPY 2024-01-19

# Buy 10 call contracts
options-sim order buy SPY240119C00475000 10

# Advance time 30 minutes
options-sim step 30

# Check status
options-sim status

# Sell to close
options-sim order sell SPY240119C00475000 10

# View trade history
options-sim history
```

## Commands

### `options-sim start`
Initialize a new simulation session.

```bash
options-sim start --symbol SPY --date 2024-01-15 --cash 100000
options-sim start -s SPY -d 2024-01-15 -c 50000 --fill-model aggressive
options-sim start -s SPY -d 2024-01-15 --data /path/to/data/
```

**Options:**
- `--symbol, -s` — Underlying symbol (required)
- `--date, -d` — Start date YYYY-MM-DD (required)
- `--cash, -c` — Initial cash (default: 100,000)
- `--fill-model, -f` — Execution model: `midpoint`, `aggressive`, `passive`
- `--data` — Path to CSV data directory

### `options-sim status`
Full account state with positions, P&L, and portfolio Greeks.

### `options-sim chain [SYMBOL] [EXPIRY]`
Get the option chain. Optionally filter by expiry date.

```bash
options-sim chain                  # Full chain for simulation symbol
options-sim chain SPY 2024-01-19   # Specific expiry
```

### `options-sim order <side> <contract> <quantity> [--limit PRICE]`
Submit a buy or sell order.

```bash
options-sim order buy SPY240119C00475000 10
options-sim order sell SPY240119P00470000 5 --limit 2.00
```

- `side` — `buy` or `sell`
- `contract` — OCC option symbol
- `quantity` — Number of contracts
- `--limit` — Limit price (optional; omit for market order)

### `options-sim step [MINUTES]`
Advance the simulation clock (default: 15 minutes).

```bash
options-sim step         # 15 minutes
options-sim step 60      # 1 hour
options-sim step 390     # Full trading day
```

### `options-sim positions`
List all open positions with current P&L and Greeks.

### `options-sim account`
Account summary: cash, portfolio value, P&L, returns.

### `options-sim history`
Complete trade history with timestamps and prices.

### `options-sim export <FILE>`
Export full results to JSON file.

```bash
options-sim export results.json
```

### `options-sim reset`
Clear the current session.

## OCC Symbol Format

Options use OCC symbology: `{UNDERLYING}{YYMMDD}{C/P}{STRIKE*1000}`

Examples:
- `SPY240119C00475000` → SPY Jan 19 2024 $475 Call
- `SPY240119P00470000` → SPY Jan 19 2024 $470 Put
- `AAPL240216C00185000` → AAPL Feb 16 2024 $185 Call

## Understanding Greeks

| Greek | Measures | Long Call | Long Put |
|-------|----------|-----------|----------|
| **Delta** | Price sensitivity to underlying | 0 to +1 | -1 to 0 |
| **Gamma** | Delta's rate of change | Always + | Always + |
| **Theta** | Daily time decay | Negative | Negative |
| **Vega** | Sensitivity to IV changes | Positive | Positive |
| **Rho** | Sensitivity to interest rates | Positive | Negative |

**Portfolio Greeks** are aggregated across all positions. Delta is multiplied by quantity × 100 (option multiplier).

## Fill Models

- **midpoint** (default) — Fill at (bid + ask) / 2. Balanced.
- **aggressive** — Buy at ask, sell at bid. Realistic market orders.
- **passive** — Buy at bid, sell at ask. Optimistic / limit orders.

Large orders (>10% of daily volume) incur slippage.

## Strategy Examples

### Covered Call (Synthetic)
```bash
# Long deep ITM call + short OTM call
options-sim order buy SPY240119C00460000 1    # Deep ITM call
options-sim order sell SPY240119C00480000 1   # OTM call
```

### Iron Condor
```bash
# Sell OTM put spread + sell OTM call spread
options-sim order sell SPY240119P00470000 5   # Sell put
options-sim order buy SPY240119P00465000 5    # Buy lower put
options-sim order sell SPY240119C00480000 5   # Sell call
options-sim order buy SPY240119C00485000 5    # Buy higher call
```

### Bull Put Spread
```bash
options-sim order sell SPY240119P00475000 10  # Sell higher put
options-sim order buy SPY240119P00470000 10   # Buy lower put
```

### Long Straddle
```bash
options-sim order buy SPY240119C00475000 5    # Buy ATM call
options-sim order buy SPY240119P00475000 5    # Buy ATM put
```

## Data Setup

### Option 1: Sample Data (Testing)
```bash
python scripts/generate_sample.py
```
Generates 5 days of synthetic SPY data. No API key needed.

### Option 2: Polygon.io (Live/Historical)
```bash
export POLYGON_API_KEY=your_key_here
python scripts/download_data.py --source polygon --symbol SPY --start 2024-01-15
```

### Option 3: Local CSV
Place CSV files in `data/` with columns:
```
timestamp, symbol, underlying, strike, expiry, option_type, bid, ask, last, volume, open_interest, iv, delta, gamma, theta, vega
```

### Option 4: ThetaData
```bash
export THETADATA_API_KEY=your_key_here
python scripts/download_data.py --source thetadata --symbol SPY --start 2024-01-15
```

## Output Format

All commands output JSON for easy parsing:

```json
{
  "timestamp": "2024-01-15T10:00:00",
  "symbol": "SPY",
  "underlying_price": 475.23,
  "account": {
    "cash": 95000.00,
    "portfolio_value": 5200.00,
    "total_value": 100200.00,
    "total_return_pct": 0.2,
    "realized_pnl": 0.0,
    "unrealized_pnl": 200.00
  },
  "positions": [...],
  "portfolio_greeks": {
    "delta": 500.0,
    "gamma": 20.0,
    "theta": -50.0,
    "vega": 150.0
  }
}
```
