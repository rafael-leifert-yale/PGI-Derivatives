# Zero-DTE Gamma Scalping Backtest System

**Author:** Skyler Chan
**Date:** April 5, 2026
**Version:** 1.0

---

## Executive Summary

This is an institutional-quality backtesting system for a zero days to expiration (0-DTE) gamma scalping strategy on SPY options. The system implements realistic execution modeling with bid-ask spreads, slippage, and transaction costs, providing defensible performance estimates.

### Performance Summary (March 2024 - April 2026)

| Metric | Value |
|--------|-------|
| **Trading Days Analyzed** | 343 |
| **Total P&L** | -$8,527.16 |
| **Average Daily P&L** | -$24.86 |
| **Median Daily P&L** | -$35.55 |
| **Sharpe Ratio** | -3.50 |
| **Win Rate** | 25.95% |
| **Best Day** | +$843.95 |
| **Worst Day** | -$428.14 |
| **Total Transaction Costs** | $2,105.92 |
| **Average Hedges/Day** | 14.6 |

**Key Finding:** The strategy was unprofitable during this period, likely due to persistent SPY uptrend with low realized volatility. Gamma scalping requires mean-reverting markets with realized volatility exceeding implied volatility.

---

## Strategy Description

### Core Concept

Zero-DTE gamma scalping exploits intraday price movements by maintaining a delta-neutral portfolio of options and stock. The strategy profits from realized volatility exceeding implied volatility.

### Mechanics

1. **Entry (9:31 AM):** Buy ATM straddle (1 call + 1 put at same strike)
2. **Monitoring:** Calculate portfolio delta every minute using Black-Scholes Greeks
3. **Hedging:** When |delta| > 15 shares, buy/sell SPY stock to neutralize delta
4. **Exit (3:55 PM):** Close all positions (sell straddle, flatten stock)

### Theoretical Profit Sources

- **Realized > Implied Vol:** Profit from re-hedging as price moves
- **Gamma Scalping:** Buy low (when delta shifts negative) / sell high (when delta shifts positive)
- **Time Decay Offset:** Intraday realized volatility offsets theta decay

### Risk Factors

- **Theta Decay:** Options lose value over time (especially rapid near expiry)
- **Trending Markets:** Large directional moves hurt if not hedged precisely
- **Execution Costs:** Frequent hedging incurs slippage and fees
- **Gap Risk:** Overnight gaps not captured (0-DTE mitigates this)

---

## System Architecture

### Component Overview

```
backtest/
├── data_engine.py       # Historical data fetching from Alpaca API
├── trading_engine.py    # Strategy logic + position management
├── execution_model.py   # Realistic fill simulation
├── orchestrator.py      # Main backtest loop
└── run_backtest.py      # Entry point script

utils/
└── greeks.py           # Black-Scholes pricing + Greeks calculation
```

### Data Flow

```
1. Orchestrator → DataEngine: Fetch SPY + option bars for date
2. DataEngine → Alpaca API: Request minute-level historical data
3. Alpaca API → DataEngine: Return bars (SPY, call, put)
4. DataEngine → Orchestrator: Validated, merged data
5. Orchestrator → TradingEngine: Initialize strategy
6. TradingEngine → ExecutionModel: Execute trades with realistic fills
7. ExecutionModel → TradingEngine: Return filled prices + costs
8. TradingEngine → Orchestrator: Daily P&L + trade log
9. Orchestrator: Aggregate results + calculate metrics
```

---

## Implementation Details

### 1. Data Engine (`data_engine.py`)

**Responsibilities:**
- Identify 0-DTE trading days (all weekdays since May 2023)
- Fetch SPY minute bars (9:30 AM - 4:00 PM)
- Construct OCC option symbols (e.g., `SPY240311C00511000`)
- Fetch option minute bars
- Validate data quality

**OCC Symbol Format:**
```
SPY + YYMMDD + (C/P) + 8-digit strike*1000
Example: SPY240311C00511000 = SPY call, March 11 2024, $511 strike
```

**Data Validation Checks:**
- SPY bars ≥ 380 (out of 390 expected)
- Call/put bars ≥ 300
- No invalid prices (≤ 0)
- Monotonic timestamps
- Reasonable price ranges

**Risk-Free Rate:** Fetched from 3-month T-Bill (^IRX) via yfinance

### 2. Trading Engine (`trading_engine.py`)

**Position Management:**
- Tracks option positions (calls/puts) and stock hedge position
- Maintains realized P&L and unrealized P&L
- Records all trades in detailed trade log

**Greeks Calculation:**
- Uses Black-Scholes model from `utils/greeks.py`
- Calculates portfolio-level delta, gamma, theta, vega
- Option delta multiplied by 100 (shares per contract)
- Stock delta = quantity (1 share = 1 delta)

**Hedge Logic:**
```python
if |portfolio_delta| > 15:
    shares_to_trade = -round(portfolio_delta)
    execute_hedge(shares_to_trade)
```

**Delta Neutrality:** Portfolio delta kept within ±15 shares

### 3. Execution Model (`execution_model.py`)

**Bid-Ask Spread Modeling:**

| Asset | Base Spread | Time-of-Day Multiplier | Liquidity Multiplier |
|-------|-------------|------------------------|----------------------|
| **SPY Stock** | $0.01 | 1.0x | N/A (highly liquid) |
| **SPY Options** | 1.5% of mid | 1.8x (open), 2.0x (close), 1.0x (mid-day) | 1.5x (<50 vol), 1.2x (<100 vol), 1.0x (≥100 vol) |

Minimum option spread: $0.05

**Slippage Modeling:**

*Stock (SPY):*
```python
impact = min(size_ratio * spread * 0.3, spread * 0.5)
executed_price = ask + impact  # for buys
executed_price = bid - impact  # for sells
```

*Options:*
```python
impact = min(size_ratio * spread * 0.5, spread * 0.8)
if order_size > 10:  # Large orders
    impact += (order_size - 10) * 0.02 * mid_price
```

**Transaction Costs:**

| Type | Cost |
|------|------|
| **Option Commission** | $0.65 per contract |
| **Stock Commission** | $0.00 (zero-commission broker) |
| **SEC Fee** | 0.00278% of sale proceeds (stocks only) |
| **FINRA TAF** | $0.000166 per share (capped at $7.27) |
| **OCC Fee** | $0.04 per contract (sell side only) |

### 4. Orchestrator (`orchestrator.py`)

**Main Loop:**
```python
for each trading day:
    1. Fetch data (SPY + call + put bars)
    2. Validate data quality
    3. Merge data on timestamp
    4. Initialize TradingEngine
    5. Enter straddle (9:31 AM)
    6. For each minute:
        a. Update option prices
        b. Calculate portfolio Greeks
        c. Check if hedge needed (|delta| > 15)
        d. Execute hedge if needed
    7. Close all positions (3:55 PM)
    8. Record daily P&L + trade log
```

**Metrics Calculated:**
- Total P&L
- Average/median daily P&L
- Standard deviation
- Best/worst day
- Win/loss counts
- Win rate
- Sharpe ratio (annualized: `(mean / std) * sqrt(252)`)

---

## Backtest Results & Analysis

### Full Period: March 2024 - April 2026

**Data Coverage:**
- Total weekdays: 546
- Successful days: 343 (62.8%)
- Failed days: 203 (37.2% - insufficient option liquidity)

**Performance:**
- **Total P&L:** -$8,527.16 (strategy lost money)
- **Average Daily P&L:** -$24.86
- **Median Daily P&L:** -$35.55 (worse than mean - left-skewed distribution)
- **Sharpe Ratio:** -3.50 (very poor risk-adjusted returns)
- **Win Rate:** 25.95% (strategy lost 74% of days)

**Distribution:**
- **Best Day:** +$843.95
- **Worst Day:** -$428.14
- **Std Dev:** $112.67 (high volatility in daily returns)

**Execution Costs:**
- **Total Transaction Costs:** $2,105.92
- **Average Daily Costs:** $6.14
- **Total Hedges:** 5,017 (14.6 per day average)
- **Costs as % of Losses:** 24.7% (significant drag on performance)

### Sample Month: March 2024

| Metric | Value |
|--------|-------|
| Trading Days | 13 |
| Total P&L | -$358.32 |
| Avg Daily P&L | -$27.56 |
| Win Rate | 15.38% |
| Sharpe Ratio | -17.17 |
| Total Costs | $59.73 |
| Total Hedges | 108 |

**Observation:** March 2024 was particularly bad (15% win rate), likely due to strong upward trend in SPY during that period.

---

## Why Did the Strategy Lose Money?

### 1. Market Regime Mismatch

**Gamma scalping profits when:**
- Realized volatility > Implied volatility
- Markets mean-revert intraday
- Price oscillates around strike

**March 2024 - April 2026 market conditions:**
- SPY in strong uptrend ($509 → $653, +28%)
- Low realized volatility relative to implied
- Persistent directional moves (not mean-reverting)

**Result:** Theta decay exceeded gamma scalping profits

### 2. Execution Costs

Transaction costs consumed 24.7% of losses:
- 14.6 hedges/day × $6.14 avg cost = significant drag
- High hedge frequency due to tight delta threshold (±15)
- Bid-ask spreads + slippage on every hedge

### 3. Negative Skew

74% losing days with median loss -$35.55 suggests:
- Small consistent losses from theta decay
- Occasional large gains from volatile days (best day: +$843.95)
- But not enough volatile days to offset cumulative theta bleed

### 4. Delta Threshold Sensitivity

Using ±15 shares as hedge threshold means:
- Frequent small hedges (costly)
- May hedge too aggressively, locking in losses
- Wider threshold (e.g., ±30) might reduce costs but increase delta risk

---

## Usage Guide

### Prerequisites

```bash
pip install pandas numpy scipy alpaca-py yfinance
```

### Configuration

Edit `config` dictionary in `run_backtest.py`:

```python
config = {
    'contracts_per_straddle': 1,        # Number of straddles to enter
    'delta_threshold': 0.15,            # Hedge when |delta| > 15 shares
    'max_stock_position': 500,          # Max stock hedge position
    'max_daily_loss': 2000,             # Stop-loss (not implemented yet)
    'profit_target': 1500               # Take-profit (not implemented yet)
}
```

### Running a Backtest

**Single Day:**
```python
from backtest.orchestrator import BacktestOrchestrator

orchestrator = BacktestOrchestrator(config)
results = orchestrator.run_backtest("2024-03-11", "2024-03-11")
```

**Date Range:**
```python
results = orchestrator.run_backtest("2024-03-01", "2024-03-31")
```

**Full Historical Period:**
```python
results = orchestrator.run_backtest("2024-03-01", "2026-04-05")
```

### Output Files

Results saved to JSON:
```
backtest_results_full_YYYYMMDD_HHMMSS.json
```

**JSON Structure:**
```json
{
  "config": {...},
  "period": {"start": "...", "end": "..."},
  "metrics": {
    "total_days": 343,
    "total_pnl": -8527.16,
    "sharpe_ratio": -3.50,
    ...
  },
  "execution_metrics": {
    "total_transaction_costs": 2105.92,
    "total_hedges": 5017,
    ...
  },
  "daily_results": [
    {
      "date": "2024-03-11",
      "daily_pnl": 29.96,
      "transaction_costs": 5.79,
      "hedge_count": 17
    },
    ...
  ]
}
```

---

## Code Quality & Validation

### Rigorous Design Principles

1. **No Magic Numbers:** All parameters configurable
2. **Defensive Validation:** Data quality checks at every step
3. **Edge Case Handling:**
   - Expired options (T ≤ 0): Return intrinsic value
   - Missing data: Skip day rather than fabricate
   - Very small T: Warning + graceful degradation
4. **Realistic Execution:** Bid-ask spreads, slippage, fees modeled conservatively
5. **Audit Trail:** Every trade logged with full execution details

### Tested Components

✅ **Black-Scholes Greeks:** Validated against known values
✅ **Option Symbol Parsing:** Strike extraction tested with real symbols
✅ **Execution Model:** Slippage bug fixed (stock vs option order size)
✅ **Data Merging:** Handles missing bars with forward-fill
✅ **Position Tracking:** P&L reconciliation verified

### Known Limitations

1. **Data Coverage:** Only 62.8% of days have sufficient option liquidity
   - Many 0-DTE strikes trade infrequently
   - Alpaca data may be incomplete for low-volume options

2. **Simplified IV:** Uses constant 25% IV instead of calculating from market prices
   - Real strategy would calibrate IV from straddle price
   - Affects Greeks accuracy, especially near expiry

3. **No Pin Risk:** Doesn't model assignment risk at expiry
   - SPY options are cash-settled (no physical delivery)
   - But early assignment possible for American options

4. **No Stop-Loss/Take-Profit:** Configured but not implemented
   - All trades run full day (9:31 AM - 3:55 PM)
   - Real strategy might exit early on large moves

5. **Single Contract:** Backtest uses 1 straddle
   - Real strategy might scale based on portfolio size
   - Transaction costs would be lower on per-contract basis at scale

---

## Future Improvements

### High Priority

1. **Implied Volatility Calibration**
   - Calculate IV from market straddle price at entry
   - Use market IV for Greeks instead of constant 25%
   - Track IV changes intraday

2. **Stop-Loss & Take-Profit**
   - Implement max daily loss exit
   - Implement profit target exit
   - Measure impact on Sharpe ratio

3. **Parameter Optimization**
   - Grid search over delta thresholds (10, 15, 20, 30)
   - Test different entry times (9:31 vs 9:45 vs 10:00)
   - Test different exit times (3:30 vs 3:55 vs 4:00)

4. **Strike Selection**
   - Test OTM straddles (reduce cost, less gamma)
   - Test strangles (sell OTM, reduce cost)
   - Test iron condors (defined risk)

### Medium Priority

5. **Regime Detection**
   - Identify trending vs mean-reverting days
   - Only trade on favorable days (e.g., high IV, low trend)
   - Use VIX, ATR, or realized vol as filters

6. **Multi-Contract Scaling**
   - Test 2, 3, 5, 10 straddles
   - Model position limits and buying power
   - Calculate breakeven contract count given fixed costs

7. **Sensitivity Analysis**
   - Greeks sensitivity to IV changes
   - P&L sensitivity to SPY price moves
   - Cost sensitivity to hedge frequency

### Low Priority

8. **Alternative Underlyings**
   - Test on QQQ, IWM, other liquid ETFs
   - Compare single-name stocks (AAPL, TSLA)
   - Test on high-IV underlyings (COIN, NVDA)

9. **Intraday Entry/Exit**
   - Test entering at different times (10 AM, 11 AM)
   - Test holding overnight (not 0-DTE anymore)
   - Test exiting early on large P&L moves

10. **Machine Learning**
    - Predict profitable days using features (VIX, trend, volume)
    - Classify market regimes (trending, mean-reverting, volatile)
    - Optimize hedge timing using RL

---

## Technical Details

### Greeks Calculation (Black-Scholes)

**Call Option:**
```
d1 = (ln(S/K) + (r + 0.5*σ²)*T) / (σ*√T)
d2 = d1 - σ*√T

Price = S*N(d1) - K*e^(-rT)*N(d2)
Delta = N(d1)
Gamma = N'(d1) / (S*σ*√T)
Theta = (-S*N'(d1)*σ/(2*√T) - r*K*e^(-rT)*N(d2)) / 365
Vega = S*N'(d1)*√T / 100
```

**Put Option:**
```
Price = K*e^(-rT)*N(-d2) - S*N(-d1)
Delta = -N(-d1)
Theta = (-S*N'(d1)*σ/(2*√T) + r*K*e^(-rT)*N(-d2)) / 365
```

Where:
- `S` = Spot price
- `K` = Strike price
- `T` = Time to expiry (years)
- `r` = Risk-free rate
- `σ` = Implied volatility
- `N(x)` = Standard normal CDF
- `N'(x)` = Standard normal PDF

### Portfolio Delta Calculation

```python
total_delta = 0

for each option position:
    greeks = black_scholes(S, K, T, r, σ, option_type)
    option_delta = greeks['delta'] * 100  # Shares per contract
    position_delta = option_delta * quantity
    total_delta += position_delta

# Add stock hedge delta
total_delta += stock_quantity

return total_delta
```

### Hedge Size Calculation

```python
if abs(total_delta) > delta_threshold:
    shares = -round(total_delta)  # Neutralize delta

    # Check position limits
    new_position = current_stock_qty + shares
    if abs(new_position) > max_stock_position:
        shares = sign(shares) * (max_stock_position - abs(current_stock_qty))

    execute_hedge(shares)
```

---

## Conclusion

This backtesting system provides a rigorous, defensible analysis of a zero-DTE gamma scalping strategy on SPY. The results show that:

1. **Strategy was unprofitable** during March 2024 - April 2026 (-$8,527 total P&L)
2. **Market regime matters:** Trending markets with low realized volatility are unfavorable for gamma scalping
3. **Execution costs are significant:** $2,106 in costs over 343 days (24.7% of losses)
4. **Realistic modeling is critical:** Bid-ask spreads, slippage, and fees dramatically impact profitability

**Key Takeaway:** Zero-DTE gamma scalping is not a universally profitable strategy. Success depends on:
- Favorable market conditions (high realized vol, mean-reversion)
- Precise risk management (optimal delta threshold, stop-losses)
- Efficient execution (minimize hedging costs)
- Regime awareness (only trade on suitable days)

The system is production-ready for further research, parameter optimization, and regime-based strategy development.

---

## Appendix: File Structure

```
PGI-Derivatives/analyst-folders/skyler-chan/
├── backtest/
│   ├── data_engine.py          # Data fetching + validation
│   ├── trading_engine.py       # Strategy logic + positions
│   ├── execution_model.py      # Realistic fill simulation
│   ├── orchestrator.py         # Main backtest loop
│   └── run_backtest.py         # Entry point
├── utils/
│   └── greeks.py               # Black-Scholes + Greeks
├── config.env                  # Alpaca API credentials
├── backtest_march_2024.json    # March 2024 results
├── backtest_results_full_*.json # Full backtest results
└── BACKTEST_DOCUMENTATION.md   # This file
```

---

**Last Updated:** April 5, 2026
**Contact:** Skyler Chan (skyler.chan@pgi-derivatives.example)
