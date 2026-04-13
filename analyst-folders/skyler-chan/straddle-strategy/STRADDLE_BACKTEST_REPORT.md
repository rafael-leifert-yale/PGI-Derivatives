# ATM Straddle Backtest Report

**Strategy:** Long ATM Straddle with Delta-Based Rolling
**Period:** March 2024 - April 2026
**Tickers:** SPY, QQQ, MSTR, COIN
**Date:** April 12, 2026

---

## Strategy Description

Buy an ATM straddle with ~7 DTE and test three management approaches:

1. **Hold** -- buy, hold to expiry, repeat
2. **Roll Delta** -- roll (re-center the strike) when net delta exceeds +/-0.35 per contract, instead of hedging with stock
3. **Event Only** -- only enter straddles around macro events (FOMC, CPI, NFP, tariffs) and earnings

Events tested: FOMC decisions, CPI releases, NFP reports, tariff announcements (2025), and individual-name earnings for MSTR/COIN.

---

## Summary of Results

### SPY

| Mode | Trades | Total P&L | Avg P&L | Win Rate | Sharpe | Avg Rolls | Event P&L | Event WR |
|------|--------|-----------|---------|----------|--------|-----------|-----------|----------|
| Hold | 109 | -$33,039 | -$303 | 27.5% | -2.77 | 0 | -$1,581 (42 trades) | 35.7% |
| Roll Delta | 109 | -$37,166 | -$341 | 13.8% | -6.98 | 1.5 | -$15,156 (43 trades) | 14.0% |
| **Event Only** | **53** | **-$5,131** | **-$97** | **37.7%** | **-0.73** | **0** | **-$5,131** | **37.7%** |

**Takeaway:** Event-only is far superior for SPY. The event straddles within the hold/roll-delta modes actually performed well (35.7% WR, only -$38/trade avg) -- it's the non-event trades that crushed performance. Rolling actually hurt SPY because the steady trending moves keep triggering expensive rolls.

### QQQ

| Mode | Trades | Total P&L | Avg P&L | Win Rate | Sharpe | Avg Rolls | Event P&L | Event WR |
|------|--------|-----------|---------|----------|--------|-----------|-----------|----------|
| Hold | 109 | -$6,973 | -$64 | 38.5% | -0.56 | 0 | **+$7,029** (42 trades) | **54.8%** |
| Roll Delta | 109 | -$20,743 | -$190 | 31.2% | -3.30 | 1.8 | -$9,715 (43 trades) | 32.6% |
| **Event Only** | **53** | **+$3,927** | **+$74** | **49.1%** | **+0.58** | **0** | **+$3,927** | **49.1%** |

**Takeaway:** QQQ event-only is the standout -- **positive total P&L (+$3,927), 49% win rate, positive Sharpe**. Even within the hold mode, event trades generated +$7,029 with 54.8% WR while non-event trades lost -$14,002. QQQ's higher beta makes it a better straddle vehicle around catalysts.

### MSTR

| Mode | Trades | Total P&L | Avg P&L | Win Rate | Sharpe | Avg Rolls | Event P&L | Event WR |
|------|--------|-----------|---------|----------|--------|-----------|-----------|----------|
| Hold | 109 | -$98,606 | -$905 | 27.5% | -2.98 | 0 | -$29,830 (45 trades) | 33.3% |
| Roll Delta | 109 | -$98,885 | -$907 | 22.0% | -3.93 | 1.5 | -$37,006 (40 trades) | 22.5% |
| **Event Only** | **55** | **-$20,963** | **-$381** | **41.8%** | **-1.21** | **0** | **-$20,963** | **41.8%** |

**Takeaway:** MSTR straddles are expensive. IV is persistently high for this name, and the straddle cost outpaces realized moves most weeks. Event-only cuts losses by 4-5x vs continuous trading. The 41.8% win rate on events is decent but the losses when it doesn't move are large (avg loss -$1,722).

### COIN

| Mode | Trades | Total P&L | Avg P&L | Win Rate | Sharpe | Avg Rolls | Event P&L | Event WR |
|------|--------|-----------|---------|----------|--------|-----------|-----------|----------|
| Hold | 109 | -$96,639 | -$887 | 25.7% | -2.99 | 0 | -$13,886 (44 trades) | 38.6% |
| Roll Delta | 109 | -$97,912 | -$898 | 17.4% | -5.29 | 1.4 | -$35,958 (45 trades) | 15.6% |
| Event Only | 54 | -$28,485 | -$528 | 37.0% | -2.02 | 0 | -$28,485 | 37.0% |

**Takeaway:** Similar to MSTR -- straddles on high-vol names are expensive. Event-only reduces the bleed but doesn't turn it profitable. However, event trades within the hold mode show significantly better performance (-$316/trade, 38.6% WR) vs non-event (-$1,273/trade). The problem is the IV premium baked into these names.

---

## Key Findings

### 1. Event-only is the right framework

Across all four names, **event trades consistently outperform non-event trades**. The strongest signal:

| Symbol | Event Avg P&L (Hold mode) | Non-Event Avg P&L (Hold mode) | Difference |
|--------|--------------------------|-------------------------------|------------|
| SPY | -$38 | -$470 | +$432 |
| QQQ | **+$167** | -$209 | +$376 |
| MSTR | -$663 | -$1,075 | +$412 |
| COIN | -$316 | -$1,273 | +$957 |

Events create the realized volatility that straddles need to overcome theta.

### 2. Rolling hurts more than it helps

Delta-based rolling consistently produced **worse results** than simple hold-to-expiry. The re-centering costs (closing + opening new straddle = 4 option transactions) eat into any benefit. With a 7-day straddle, there isn't enough time for the roll to pay off before theta decay resumes on the new position.

Rolling might work better with longer-dated straddles (30+ DTE) where the time value is larger relative to transaction costs.

### 3. QQQ is the best vehicle

QQQ event-only is the **only configuration that made money** (+$3,927, Sharpe +0.58). This makes sense:
- Higher beta than SPY = bigger moves on macro events
- Still liquid enough for tight spreads
- IV is slightly elevated vs SPY but not as expensive as single-stock options

### 4. High-vol single names are theta traps

MSTR and COIN straddles are extremely expensive because IV is persistently high (80-120%+). Even large moves often don't exceed the priced-in vol. You're paying for vol that's already reflected in the premium.

---

## Recommendations

### Immediate next steps

1. **Focus on QQQ event-only straddles** -- the only profitable configuration. Refine the event calendar and entry timing (day before vs day of).

2. **Test longer DTE (14-21 days)** -- 7 DTE is aggressive for theta decay. Longer-dated straddles give more time for the event to play out and reduce the per-day theta burden.

3. **Add VIX level filter** -- enter straddles when VIX is relatively low (< 20), meaning implied vol is cheap. Avoid when VIX is already elevated (events already priced in).

4. **Test entry timing** -- entering 2-3 days before the event (when IV hasn't fully expanded) vs 1 day before may improve results.

### Research extensions

5. **IV percentile filter** -- only buy straddles when IV rank is below 30th percentile for that name. This targets cheap vol.

6. **Strangles instead of straddles** -- OTM strangles reduce cost basis at the expense of needing a bigger move. Worth testing for MSTR/COIN where ATM straddles are prohibitively expensive.

7. **Earnings-only for single names** -- macro events don't move MSTR/COIN the way earnings do. An earnings-only straddle (enter 1 day before, exit day after) is the classic play.

8. **Realized vs implied vol spread** -- track the RV-IV spread over time and only enter when RV has been exceeding IV (momentum in vol).

---

## Methodology Notes

- **Option pricing:** Black-Scholes with VIX-calibrated IV for SPY/QQQ, historical vol * 1.3x markup for MSTR/COIN
- **Transaction costs:** $0.65/contract commission + OCC fees + 1.5% bid-ask slippage model
- **Strike selection:** Nearest standard strike to current price
- **Data source:** yfinance daily close prices
- **Risk-free rate:** 4.5% (constant)
- **Limitation:** Uses daily close-to-close (not intraday). Actual entry/exit timing would differ.
