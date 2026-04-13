# QQQ Gamma Scalping: Delta-Based Strangles + Intraday Timing

**Date:** 2026-04-10
**Period:** Feb 4 - Apr 9, 2026 (17 trading days)
**Config:** delta threshold 0.30, rebalance every minute, 1-lot

---

## 1. Strangle by Delta: The 25-Delta Strangle is the Sweet Spot

| Delta | Avg P&L | Win Rate | Sharpe | Avg Cost | Strike Width | Hedges/Day | Gamma | Gamma/$ |
|-------|---------|----------|--------|----------|-------------|------------|-------|---------|
| 5d | +$70 | 71% | 13.2 | $10 | $7.4 | 3.5 | 9.4 | 94.4 |
| 10d | +$104 | 94% | 26.3 | $24 | $5.7 | 4.8 | 16.4 | 69.6 |
| 15d | +$110 | 94% | 23.8 | $38 | $4.6 | 5.4 | 21.4 | 57.1 |
| **25d** | **+$124** | **94%** | **27.4** | **$68** | **$3.1** | **6.4** | **28.4** | **41.5** |
| 35d | +$131 | 94% | 22.5 | $107 | $1.8 | 7.1 | 32.9 | 30.8 |
| 50d (straddle) | +$99 | 86% | 16.1 | $280 | $0 | 7.8 | 21.6 | 7.7 |

**The 25-delta strangle has the highest Sharpe ratio (27.4)** and a 94% win rate across 17 days. It costs $68 to enter (vs $280 for a straddle), generates 6.4 hedges/day, and averages +$124/day.

The straddle (50d) actually makes money in this sample (+$99/day), but with lower Sharpe (16.1) and more variance because you're paying $280 in theta -- almost 4x more than the 25d strangle -- for less incremental gamma.

The 5-delta strangle is the most gamma-efficient ($94 of gamma per dollar of cost) but has the lowest win rate (71%) because the options are so far OTM that they sometimes expire worthless before triggering any hedges.

### Why does the 25-delta beat the straddle?

The 25d strangle puts your call $3 above ATM and your put $3 below ATM. On a $590 stock, that's call at $593 and put at $587. These options still have meaningful gamma (28.4 vs 21.6 for the straddle) because on 0-DTE with 2-3 hours to expiry, even $3 OTM options are close enough to ATM for gamma to be significant.

But they cost 75% less. The theta you pay is $68 instead of $280. So the bar for hedge profits to cover theta drops dramatically.

---

## 2. Where Do the Gains Come From During the Day?

### P&L by session third

| Delta | First Third | Middle Third | Last Third |
|-------|-----------|-------------|-----------|
| 5d | $42 (51%) | $23 (28%) | $16 (20%) |
| 10d | $54 (47%) | $31 (27%) | $30 (26%) |
| 15d | $59 (48%) | $38 (31%) | $25 (21%) |
| **25d** | **$85 (62%)** | **$38 (27%)** | **$15 (11%)** |
| 35d | $83 (58%) | $36 (25%) | $25 (17%) |
| 50d | $70 (61%) | $30 (26%) | $14 (13%) |

**The first third of the session generates ~50-62% of all P&L.** The middle third adds another ~25-30%. The last third contributes only 10-20%.

This is counterintuitive -- I said earlier that "gamma explodes in the last hour." And it does. But here's what's actually happening:

### The P&L curve tells the real story

For the 25-delta strangle, minute by minute:

| Minutes to Close | Cumulative P&L | Option P&L | Hedge P&L |
|-----------------|---------------|-----------|----------|
| 150 min (start) | -$5 | $0 | -$5 |
| 120 min | +$56 | +$30 | +$26 |
| 90 min | +$92 | +$22 | +$69 |
| 60 min | +$94 | +$49 | +$46 |
| 45 min | +$115 | +$103 | +$11 |
| 30 min | +$130 | +$150 | -$20 |
| 15 min | +$133 | +$151 | -$18 |
| 0 min (close) | +$132 | +$151 | -$20 |

**What's happening:**
- **First 60 minutes:** Hedge P&L is the engine. The options oscillate, delta shifts trigger hedges, and you lock in scalping profits. The options themselves are roughly flat (theta and gamma offset).
- **Last 30 minutes:** Option P&L takes over. As expiry approaches, the surviving option leg (whichever side QQQ moved toward) retains intrinsic value while theta has fully decayed. But hedge P&L actually goes NEGATIVE in the last 30 minutes -- the rapid gamma changes make hedging less effective (you're chasing delta that's moving too fast).

**The early session is where you earn your money through hedging. The late session is where the option position either pays off or not -- it's more binary and less "scalpable."**

---

## 3. When Do Hedges Happen?

### Hedge distribution by minutes to close

| Time Window | 50d (straddle) | 25d strangle | 10d strangle |
|------------|---------------|-------------|-------------|
| 0-15 min | 9% | 6% | 10% |
| 15-30 min | 8% | 2% | 11% |
| 30-60 min | 17% | 19% | 26% |
| **60-90 min** | **30%** | **34%** | **27%** |
| 90-120 min | 11% | 17% | 12% |
| 120-180 min | 25% | 21% | 12% |

**Most hedges happen 60-90 minutes before close** across all structures. This is the "sweet zone" where gamma is high enough to trigger delta breaches but not so extreme that delta oscillates too rapidly to hedge effectively.

The straddle has more hedges spread throughout (25% in the 120-180 min window) because ATM options have meaningful gamma even early. The 10-delta strangle concentrates hedges in the 30-90 min window because the far-OTM options only develop significant gamma as expiry approaches.

---

## 4. Peak and Trough Timing

| Delta | Avg Peak P&L | Peak Time | Avg Trough P&L | Trough Time |
|-------|-------------|-----------|----------------|-------------|
| 50d | +$120 | 15:57 | -$10 | 14:30 |
| 25d | +$148 | 15:48 | -$7 | 14:30 |
| 10d | +$122 | 15:34 | -$9 | 14:30 |

**The trough happens at almost exactly 14:30 on every structure.** This is when the option data first becomes available in our sample, so it reflects the immediate mark-to-market hit of paying the entry premium plus initial slippage. After 14:30, the P&L curve goes steadily upward.

**Peak P&L hits 15:30-16:00** -- confirming you should hold to the end. The 25d peaks 10 minutes earlier than the straddle because its gamma profile peaks slightly before the 50d's.

---

## 5. What Separates Wins from Losses?

### 25-delta strangle: 16 wins / 1 loss

| Metric | WIN (16 days) | LOSS (1 day) |
|--------|-------------|------------|
| Avg P&L | +$132 | -$8 |
| Hedges/day | 6.6 | 4.0 |
| Entry cost | $69 | $53 |
| First third P&L | +$88 | +$27 |
| Middle third P&L | +$41 | -$15 |
| Last third P&L | +$17 | -$9 |

The single loss day (Feb 25) had:
- 4 hedges (below average)
- Middle and last third both negative -- QQQ trending without oscillation
- But even this loss was only -$8 (trivial)

### 5-delta strangle: 12 wins / 5 losses

| Metric | WIN (12 days) | LOSS (5 days) |
|--------|-------------|------------|
| Avg P&L | +$106 | -$18 |
| **Hedges/day** | **4.7** | **0.6** |
| First third P&L | +$59 | -$1 |

**The pattern is clear: 0-1 hedges = loss. 3+ hedges = win.** When the 5d strangle loses, it's because the far-OTM options never develop enough gamma to trigger hedges. The position just bleeds the small premium to theta.

---

## 6. Recommendations

### Best setup: 25-delta strangle on QQQ 0-DTE

- **Call:** 25-delta OTM (~$3 above ATM)
- **Put:** 25-delta OTM (~$3 below ATM)
- **Cost:** ~$68 per 1-lot (vs $280 for straddle)
- **Expected P&L:** +$124/day, 94% win rate, Sharpe 27
- **Hedge frequency:** ~6 trades/day
- **Exit:** Hold until 15:55

### Why not the 10-delta?

It's close (+$104/day, 94% win rate, Sharpe 26), but the 25d gives you more gamma and therefore more hedge opportunities. The extra $44 in cost buys you significantly more gamma (28.4 vs 16.4) and the Sharpe is slightly better. The 10d is a reasonable alternative if you want to minimize capital at risk.

### Why not the 35-delta?

Higher avg P&L (+$131) but lower Sharpe (22.5). The extra gamma isn't worth the extra theta cost. You start running into the same problem as the straddle -- paying too much for gamma you don't fully utilize.

### Timing rules

1. **Enter as early as possible** -- the first third generates 60%+ of P&L
2. **Hold until 15:55** -- gamma peaks late, but hedge effectiveness drops in last 15 min
3. **If 0-1 hedges by midpoint of session, consider cutting early** -- the day is likely a low-oscillation trending day where you'll bleed theta
