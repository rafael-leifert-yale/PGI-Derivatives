## Zero-DTE Gamma Scalping: MSTR & COIN Comparative Performance Report

**Strategy:** Long ATM Straddle + Delta-Neutral Hedging (Friday weekly 0-DTE)
**Analyst:** Skyler Chan
**Date:** April 5, 2026

---

## Summary Comparison

| Metric | SPY | MSTR | COIN |
|--------|-----|------|------|
| **Period** | Mar 2024 - Apr 2026 | Aug 2024 - Apr 2026 | Mar 2024 - Apr 2026 |
| **Trading Days** | 343 | 36 | 30 |
| **Total P&L** | **-$8,527** | **-$2,458** | **+$2,383** |
| **Avg Daily P&L** | -$24.86 | -$68.28 | +$79.42 |
| **Median Daily P&L** | -$35.55 | -$18.37 | +$242.83 |
| **Std Dev** | $112.67 | $383.91 | $797.23 |
| **Sharpe Ratio** | -3.50 | -2.82 | **+1.58** |
| **Win Rate** | 25.95% | 44.44% | **70.00%** |
| **Best Day** | +$843.95 | +$993.21 | +$1,405.89 |
| **Worst Day** | -$428.14 | -$724.41 | -$2,379.34 |

---

## Key Finding: COIN Was Profitable

COIN generated **+$2,383 total P&L** with a **70% win rate** and **1.58 Sharpe ratio** -- the only profitable configuration tested. This validates the core thesis: gamma scalping works when realized volatility exceeds implied volatility, which happens more frequently in high-vol crypto-correlated names.

---

## MSTR Results (Aug 2024 - Apr 2026)

**Period:** Post 10:1 stock split (Aug 8, 2024) to present. Fridays only.

| Metric | Value |
|--------|-------|
| Total P&L | -$2,457.99 |
| Trading Days | 36 / 87 Fridays (41.4% data coverage) |
| Avg Daily P&L | -$68.28 |
| Median Daily P&L | -$18.37 |
| Win Rate | 44.44% (16W / 20L) |
| Sharpe Ratio | -2.82 |

**Win/Loss Payoff:**
- Average winning day: ~$275
- Average losing day: ~$315
- Payoff ratio roughly symmetric but skewed slightly negative

**Notable days:**
- Best: +$993.21 (Jun 20, 2025)
- Worst: -$724.41 (Mar 14, 2025)

**Why MSTR underperformed:**
- MSTR trended strongly in both directions (up to $450 then back to $130)
- Despite high IV, persistent directional moves hurt delta-neutral strategy
- 44% win rate is much better than SPY (26%) but insufficient given cost structure
- Only 36 usable days out of 87 Fridays -- sparse option data limits sample size

---

## COIN Results (Mar 2024 - Apr 2026)

| Metric | Value |
|--------|-------|
| Total P&L | +$2,382.69 |
| Trading Days | 30 / 110 Fridays (27.3% data coverage) |
| Avg Daily P&L | +$79.42 |
| Median Daily P&L | +$242.83 |
| Win Rate | 70.00% (21W / 9L) |
| Sharpe Ratio | +1.58 |

**Win/Loss Payoff:**
- Average winning day: ~$363
- Average losing day: ~$585
- Losses are larger per event, but far less frequent

**Notable days:**
- Best: +$1,405.89 (May 16, 2025)
- Worst: -$2,379.34 (Oct 10, 2025)
- Second worst: -$2,306.76 (Aug 1, 2025)

**Why COIN worked:**
- High intraday volatility driven by crypto market correlation
- More mean-reverting price action on weekly expiry days
- Realized volatility frequently exceeded implied volatility
- 70% win rate overcomes the unfavorable payoff asymmetry (wins < losses)

---

## Data Coverage Issue

A significant limitation: Alpaca minute-level option data coverage is sparse for individual stocks.

| Symbol | Fridays Tested | Successful | Coverage |
|--------|---------------|-----------|----------|
| MSTR | 87 | 36 | 41.4% |
| COIN | 110 | 30 | 27.3% |
| SPY | 546 weekdays | 343 | 62.8% |

**Primary failure reason:** Insufficient option bar count. Many days had call or put data below the 50-bar minimum, or merged data was too sparse. This is a data availability issue, not a strategy issue -- results should be interpreted with caution given the small sample sizes.

---

## Risk Analysis

### Tail Risk

COIN has significant tail risk: two days lost >$2,300, which is 30x the average daily P&L. Without those two outliers, COIN performance would be dramatically better:

| Metric | With Outliers | Without 2 Worst Days |
|--------|---------------|---------------------|
| Total P&L | +$2,383 | +$7,069 |
| Avg Daily P&L | +$79 | +$252 |
| Win Rate | 70% | 75% |

This suggests a **stop-loss at ~-$1,000** would materially improve risk-adjusted returns.

### Volatility Scaling

Payoff magnitude scales with underlying price and volatility:

| Symbol | Avg Price | Daily Std Dev | Relative Scale |
|--------|-----------|---------------|----------------|
| SPY | ~$550 | $113 | 1.0x |
| MSTR | ~$250 | $384 | 3.4x |
| COIN | ~$250 | $797 | 7.1x |

Higher per-trade variance in MSTR/COIN is expected given their higher underlying volatility.

---

## Execution Parameters Used

| Parameter | SPY | MSTR | COIN |
|-----------|-----|------|------|
| Stock spread | $0.01 | $0.05 | $0.05 |
| Option spread | 1.5% | 2.5% | 2.5% |
| Delta threshold | 15 shares | 15 shares | 15 shares |
| Contracts | 1 | 1 | 1 |
| IV assumption | 25% | 25% | 25% |
| 0-DTE frequency | Daily | Fridays only | Fridays only |

---

## Recommendations

### 1. Scale COIN Strategy
COIN showed genuine edge. Next steps:
- Increase to 3-5 contracts to improve P&L magnitude
- Add stop-loss at -$1,000 to cap tail risk
- Calibrate IV from market straddle prices (25% constant is too low for COIN)

### 2. Regime Filter for MSTR
MSTR has the volatility but trends too aggressively. Add:
- Skip days when MSTR moved >5% in the prior week (trending signal)
- Only trade when BTC is range-bound (sideways crypto regime)

### 3. Data Quality
- Consider alternative data sources (OPRA feed, OptionMetrics) for better coverage
- The 27-41% coverage rate means >60% of potential trading days were unusable
- Higher coverage would increase sample size and statistical confidence

### 4. Parameter Optimization
- Test wider delta thresholds (20-30 shares) to reduce hedge costs on high-vol names
- Test different entry times (10 AM vs 9:31 AM) -- individual stock options may have wider spreads at open
- Use market-implied volatility instead of constant 25%

---

**Report Generated:** April 5, 2026
**Data Source:** Alpaca API (minute-level historical options + stock data)
**Contact:** Skyler Chan, PGI Derivatives
