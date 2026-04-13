# QQQ Gamma Scalping Deep Dive

**Date:** 2026-04-10
**Period:** Feb 9 - Apr 9, 2026 (10 sampled trading days)
**Config:** 1-lot ATM straddle, delta threshold 0.30, rebalance every minute

---

## The Trade Structure

Every morning at 9:31 AM, you buy a 0-DTE (same-day expiry) ATM straddle on QQQ:
- **Buy 1 ATM call** (expires today at 4:00 PM)
- **Buy 1 ATM put** (same strike, same expiry)

This gives you a **long gamma** position -- you own convexity. Your portfolio delta starts near zero (call delta ~+50, put delta ~-50), but as QQQ moves, delta drifts.

Then you **scalp the gamma**: every minute, check if your portfolio delta has drifted beyond +/-30 shares. If it has, hedge by trading QQQ shares to bring delta back toward zero. Every time QQQ whipsaws (goes up, you sell shares; goes down, you buy shares), you lock in small profits on the stock hedges.

At 3:55 PM, close everything.

**You're essentially buying insurance (the straddle) that pays you every time the market moves, then harvesting that movement through stock trades.**

---

## How It Made Money: The P&L Anatomy

### Total: +$1,592.69 across 10 days ($159/day avg)

The P&L has two components:

| Component | Avg/Day | Role |
|-----------|---------|------|
| Straddle P&L | **-$159.80** | The cost of gamma -- theta decay eats the straddle |
| Hedge scalping P&L | **+$319.07** | Profits from buying low / selling high on QQQ shares |
| Transaction costs | -$5.61 | Negligible on QQQ |
| **Net** | **+$159.27** | Scalping profits > theta cost |

**The straddle almost always loses money** (avg -$160/day). That's the price of admission -- you're paying theta to own gamma. The entire strategy hinges on whether your hedging profits exceed that theta cost.

---

## Day-by-Day Results

| Date | QQQ Range | Range % | Straddle P&L | Hedges | Daily P&L | Result |
|------|-----------|---------|-------------|--------|-----------|--------|
| Feb 9 | $605.89-$614.02 | 1.34% | -$163 | 16 | **+$68.92** | WIN |
| Feb 13 | $596.94-$604.79 | 1.31% | -$162 | 31 | **+$768.87** | WIN |
| Feb 20 | $600.43-$609.86 | 1.57% | -$222 | 23 | **+$1,003.72** | WIN |
| Feb 27 | $602.55-$607.52 | 0.82% | -$364 | 1 | -$4.93 | LOSS |
| Mar 6 | $598.84-$605.27 | 1.07% | -$445 | 1 | -$0.73 | LOSS |
| Mar 12 | $597.71-$603.76 | 1.00% | +$1 | 2 | -$224.83 | LOSS |
| Mar 19 | $587.13-$593.05 | 1.01% | -$401 | 12 | **+$79.36** | WIN |
| Mar 26 | $580.21-$584.36 | 0.71% | -$178 | 14 | **+$48.17** | WIN |
| Apr 2 | $572.17-$585.10 | 2.25% | +$223 | 6 | -$61.64 | LOSS |
| Apr 9 | $603.04-$608.75 | 0.94% | +$113 | 8 | -$84.22 | LOSS |

### The Big Winner: Feb 20 (+$1,003.72)

QQQ had a $9.43 range (1.57%) with heavy oscillation. The straddle lost $222 to theta, but 23 hedge trades scalped over $1,200 in profits as QQQ bounced between $600-$610 repeatedly. The strategy bought shares near $604 and sold near $607 over and over.

### The Big Winner: Feb 13 (+$768.87)

QQQ ranged $7.85 (1.31%) with extreme choppiness. 31 hedge trades -- the most of any day. The stock oscillated in a $597-$605 band, with the strategy systematically buying dips and selling rips. Each round-trip captured $1-2/share across 30-100 share blocks.

### The Losers: Feb 27, Mar 6 (near breakeven)

Only 1 hedge each -- QQQ moved directionally without oscillating. The straddle bled theta, but the single hedge roughly offset it. Net P&L was essentially zero (-$5, -$1).

### The Worst Day: Mar 12 (-$224.83)

Straddle actually broke even (+$1), but the 2 hedge trades lost money. QQQ drifted down steadily -- the strategy bought shares early, held them as QQQ fell, and closed at a loss.

---

## Why QQQ Works and Others Don't

### 1. Penny-wide option spreads
QQQ ATM 0-DTE options trade with $0.01-0.05 bid-ask spreads. The straddle entry costs ~$5-8 with under $0.10 of slippage total. For MSTR or SOXL, option spreads are $0.20-0.50 -- that's $40-100 of edge destroyed before you even start.

### 2. Daily 0-DTE availability
QQQ has options expiring every single trading day. MSTR/RIOT/MARA only have Friday weekly expiries, giving you ~4 shots per month vs 20+ for QQQ.

### 3. High gamma near ATM on 0-DTE
With hours to expiry, gamma is extremely concentrated near the strike. A $1 move in QQQ causes a ~30-50 delta swing, triggering profitable hedge trades. Longer-dated options have flatter gamma -- less to scalp.

### 4. Enough volatility to overcome theta
QQQ's 18% realized vol translates to ~$6-8 daily range on a ~$600 stock. That's enough oscillation to generate $200-400 in hedge profits, while the straddle costs $500-900 but only loses ~$160 to theta on average (because the straddle retains intrinsic value from directional moves).

### 5. Massive underlying liquidity
QQQ trades $37B/day in volume. Stock hedges fill at the midpoint with negligible slippage. Total transaction costs averaged $5.61/day -- less than 4% of average P&L.

---

## The Key Insight: It's Not About Direction

The strategy doesn't care which way QQQ goes. It cares about **oscillation frequency**.

- **Choppy days (lots of back-and-forth)**: High hedge count, high P&L. Feb 13 had 31 hedges and made $769.
- **Trending days (one-directional)**: Low hedge count, low/negative P&L. Feb 27 had 1 hedge and lost $5.
- **Correlation between hedges and P&L**: More hedges = more scalping opportunities = more profit.

The strategy is essentially **short trend, long mean-reversion**. It profits when QQQ oscillates around the strike and loses when QQQ trends away without looking back.

---

## Risk Profile

| Metric | Value |
|--------|-------|
| Win rate | 50% (5/10 days) |
| Avg win | +$393.81 |
| Avg loss | -$75.27 |
| Best day | +$1,003.72 |
| Worst day | -$224.83 |
| Win/loss ratio | 5.2x |
| Max capital at risk | ~$900 (straddle cost) + margin for shares |
| Sharpe ratio | 6.71 (annualized) |
| Avg daily transaction costs | $5.61 |

The skew is strongly positive: wins are ~5x larger than losses. The worst day was only -$225, while the best day was +$1,004. This is because:
- Losses are capped at roughly the straddle cost (~$500-900)
- But the strategy often recovers partial straddle value + hedge profits
- Big oscillation days produce outsized winners

---

## Capital Requirements

Per 1-lot straddle:
- **Straddle cost**: ~$500-900/day (this is your max loss on the options)
- **Stock margin**: Need to hold up to ~100 shares of QQQ (~$60,000 notional, but only ~$30,000 margin at 50% Reg T)
- **Practical account size**: ~$50,000 minimum to run 1-lot comfortably with margin

Scaling: each additional lot multiplies P&L linearly. 5 lots = ~$800/day avg on a $250K account.
