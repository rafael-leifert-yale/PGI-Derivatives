# Gamma Scalping Edge Research: How Professional Quants Actually Make Money

**Date:** 2026-04-12

---

## Executive Summary

A naive gamma scalping strategy (buy ATM straddle, mechanically delta-hedge) loses money on SPY approximately 70-85% of the time because implied volatility systematically exceeds realized volatility due to the **variance risk premium (VRP)** ([Alpha Architect](https://alphaarchitect.com/the-variance-risk-premium-is-pervasive/)). Professional traders profit from gamma scalping not by running it unconditionally, but through a combination of: (1) volatility forecasting models that identify when RV will exceed IV, (2) regime filters that restrict trading to favorable environments, (3) structural edges from bid-ask spread capture and flow information, and (4) cross-asset and relative value approaches like dispersion trading. This document synthesizes findings from academic literature, practitioner resources, and quantitative finance forums into actionable recommendations for our backtest.

---

## 1. Why Naive Gamma Scalping Loses Money: The Variance Risk Premium

### The Core Problem

The variance risk premium (VRP) is the persistent tendency for option-implied volatility to exceed subsequent realized volatility. This premium exists because investors are willing to pay for downside protection -- they are buying insurance against tail events. Selling that insurance (being short vol / short gamma) collects this premium. Buying straddles and gamma scalping is the opposite side of this trade -- you are *paying* the insurance premium.

Key findings on VRP magnitude:

- **SPY/SPX:** Implied vol exceeds realized vol approximately 70-85% of the time. The VRP averages roughly 3-4 vol points (e.g., IV of 16% when RV realizes at 12-13%) ([Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/))
- **Cross-asset pervasiveness:** Fallon, Park, and Yu (2015) found shorting volatility produces Sharpe ratios of 0.6 (equities), 0.5 (fixed income), 0.5 (currencies), and 1.5 (commodities). A diversified VRP strategy achieved a 1.0 Sharpe ratio ([Alpha Architect](https://alphaarchitect.com/the-variance-risk-premium-is-pervasive/))
- **Risk-based explanation:** The VRP is compensation for bearing left-tail risk. It shows up in bad times (when risky assets perform poorly), which is why it cannot be arbitraged away ([Alpha Architect](https://alphaarchitect.com/the-variance-risk-premium-is-pervasive/))
- Peter Carr and Liuren Wu's seminal paper at NYU found that "the at-the-money Black-Scholes implied volatility is an efficient, although biased, forecast of the subsequent realized volatility" -- the bias being systematically too high ([NYU Engineering](https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf))

### Implication for Our Backtest

**A gamma scalp without a volatility forecast is simply paying the VRP.** You are on the wrong side of a well-documented risk premium. The edge must come from identifying the ~15-30% of the time when realized vol will exceed implied vol, and only trading then.

---

## 2. Volatility Forecasting Models: The Real Edge

Professional vol traders do not buy straddles and hope -- they use quantitative volatility forecasts to identify when options are cheap (IV < forecasted RV). The key models:

### 2.1 HAR-RV (Heterogeneous Autoregressive Realized Volatility)

The HAR model, proposed by Fulvio Corsi (2009), has become "the workhorse of the volatility forecasting literature on account of its simplicity and generally good forecasting performance" ([Portfolio Optimizer](https://portfoliooptimizer.io/blog/volatility-forecasting-har-model/)).

**How it works:** Forecasts tomorrow's realized variance as a weighted combination of three time horizons:

```
RV_forecast = beta_0 + beta_d * RV_daily + beta_w * RV_weekly + beta_m * RV_monthly
```

Where:
- `RV_daily` = yesterday's realized variance (from intraday returns)
- `RV_weekly` = average RV over past 5 trading days
- `RV_monthly` = average RV over past 22 trading days

The model captures the "Heterogeneous Market Hypothesis" -- that markets are composed of agents with different trading frequencies (daily, weekly, monthly), each generating different volatility components ([Medium - Simone Menaldo](https://medium.com/@simomenaldo/realized-volatility-and-har-models-a-new-paradigm-for-volatility-forecasting-4a660f2530f3)).

**Key extensions:**
- **HAR-J:** Adds a jump component (RV minus bipower variation) to capture discontinuous price moves ([Medium - Simone Menaldo](https://medium.com/@simomenaldo/realized-volatility-and-har-models-a-new-paradigm-for-volatility-forecasting-4a660f2530f3))
- **LHAR (Log HAR):** Applies the model to log(RV) to stabilize variance and ensure positive forecasts
- **HAR-GARCH:** Combines HAR with GARCH to capture short-term clustering

**Practical note:** You do not need intraday tick data to use HAR. Clements and Preve showed that daily range-based variance estimators (Parkinson, Garman-Klass, Rogers-Satchell) can substitute for realized variance with good results ([Portfolio Optimizer](https://portfoliooptimizer.io/blog/volatility-forecasting-har-model/)).

### 2.2 GARCH Family

GARCH(1,1) remains useful but has known limitations: it assumes stationarity and homogeneity, and "often falls short in capturing the complex, multi-layered dynamics of market volatility" ([Medium - Simone Menaldo](https://medium.com/@simomenaldo/realized-volatility-and-har-models-a-new-paradigm-for-volatility-forecasting-4a660f2530f3)). EGARCH adds leverage effects (vol rises more after down moves than up moves of the same magnitude).

### 2.3 Regime-Switching Models

A 2025 Columbia paper (Blake, Gandhi, Jakkula) demonstrated that regime-switching HAR models significantly outperform standard HAR, especially during market transitions. Their coefficient-based soft regime clustering algorithm "outperformed all other models, including the baseline autoregressive model, during all time periods" including pre-COVID, during COVID, and post-COVID ([arXiv:2510.03236](https://arxiv.org/html/2510.03236v1)).

Key insight: **Volatility forecasting with fixed coefficients breaks down during regime changes.** A two-regime model (low vol / high vol) with Markov transition probabilities captures the structural breaks that matter most for gamma scalping profitability.

### 2.4 Rough Volatility

A 2025 paper on arXiv explored "options-driven realized volatility forecasting: information gains via rough volatility model," showing that incorporating the roughness parameter (Hurst exponent H ~ 0.1 for equity vol, much less than the 0.5 of Brownian motion) improves forecasts ([arXiv:2604.02743](https://arxiv.org/abs/2604.02743)). Rough vol models capture the empirical finding that vol-of-vol is very high at short horizons and decays slowly.

### 2.5 The Practitioner Approach (Sinclair)

Euan Sinclair's *Volatility Trading* (Wiley) describes a practical quantitative framework: build a volatility cone (percentile ranks of historical vol at various lookback windows), compare current IV to the cone, and trade when IV is at an extreme relative to your RV forecast. The key insight is that **the edge is in the forecast, not the execution** ([Amazon - Volatility Trading](https://www.amazon.com/Volatility-Trading-Website-Euan-Sinclair/dp/1118347137)).

---

## 3. Regime Filters and Conditional Entry: When to Trade

### 3.1 VIX Term Structure as a Regime Signal

The VIX futures term structure is one of the most powerful regime indicators:

- **Contango (normal):** VIX futures > spot VIX. This is the normal state (~80% of the time). It signals complacency and means the VRP is intact -- selling vol wins, buying vol loses. **Do NOT gamma scalp in contango** ([Macrosynergy](https://macrosynergy.com/research/vix-term-structure-as-a-trading-signal/))
- **Backwardation (inverted):** Spot VIX > VIX futures. This signals panic and capitulation. Critically, "when the VIX futures are in backwardation, the subsequent future return of S&P500 is positive" -- this is a contrarian buy signal. Fassas and Hourvouliades (2018) found the negative slope coefficient was statistically significant while the positive slope coefficient was not ([Macrosynergy](https://macrosynergy.com/research/vix-term-structure-as-a-trading-signal/))
- **For gamma scalping:** Backwardation means spot vol is elevated and expected to decline. Paradoxically, this is often when RV is running hot enough to justify long gamma, BUT IV is also elevated. The opportunity is more nuanced -- it occurs when the VIX curve transitions from backwardation back toward contango (the "vol compression" phase where RV is still elevated but IV is coming down)

### 3.2 The IV-RV Spread as Entry Signal

The most direct signal: **only enter long gamma positions when your forecasted RV exceeds current IV.** This is the core of professional vol trading.

Practical implementation from [Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/):
- Enter gamma scalping when realized volatility is running above implied volatility
- Historical vol above the current IV level, upcoming catalysts, or negative dealer gamma environments all favor the strategy
- Stop gamma scalping when realized vol drops below implied vol for multiple consecutive days
- Exit if cumulative gamma P&L is not keeping pace with theta after 7-10 days
- Exit when position reaches 14-21 DTE and theta acceleration makes the strategy uneconomical

### 3.3 Dealer Gamma Exposure (GEX)

When aggregate dealer gamma is negative, market makers' hedging amplifies moves (they sell into declines, buy into rallies), which increases realized volatility. When dealer gamma is positive, their hedging suppresses realized vol ([Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/)).

**Actionable filter:** Gamma scalping works better in negative dealer gamma environments because the hedging feedback loop amplifies realized moves, increasing the probability that RV > IV.

### 3.4 Leverage Effect / Asymmetry Filter

The leverage effect (Black, 1976) means volatility increases more after down moves than up moves of the same magnitude. The HAR model can be extended to capture this:

```
RV_forecast = ... + gamma_leverage * |r_t| * I(r_t < 0)
```

A statistically significant positive coefficient on this term means that negative returns predict higher future volatility ([Medium - Simone Menaldo](https://medium.com/@simomenaldo/realized-volatility-and-har-models-a-new-paradigm-for-volatility-forecasting-4a660f2530f3)). **After sharp down days, the probability of RV > IV increases materially.**

---

## 4. How Market Makers Actually Profit

### 4.1 The Bid-Ask Spread Edge

Market makers' primary edge is structural, not directional. They earn the bid-ask spread on every transaction, which provides a cushion against hedging costs. A market maker who sells an option at the ask price and hedges has effectively sold vol at a premium to their theoretical fair value ([AlgoTradingLib](https://algotradinglib.com/en/pedia/o/options_market_making.html)).

For retail gamma scalpers, this edge is reversed -- you PAY the spread on both the option entry and every delta hedge. With SPY penny-wide spreads, friction is approximately:
- Bid-ask slippage: ~$0.01/share per hedge x 6 hedges/day x 20 shares = $1.20/day
- For a straddle with $14/day theta, this adds ~8-10% to your breakeven RV requirement ([Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/))

### 4.2 Flow Information

Market makers see order flow before the public. If they observe heavy put buying from institutional accounts, they can anticipate increased realized vol. Firms like Citadel Securities are "a leading market maker across a broad array of fixed income and equity products...trading approximately 35% of all U.S.-listed equity options volume" ([Citadel Securities](https://www.citadelsecurities.com/what-we-do/options/)). This flow information is a massive, unquantifiable edge that retail traders cannot replicate.

### 4.3 Speed and Technology

Professional market makers use algorithmic systems to hedge continuously with minimal latency. Their effective hedging frequency is far higher than the 4-8x/day a manual trader achieves. More hedges = closer to the theoretical continuous-hedging P&L, with lower path dependency ([AlgoTradingLib](https://algotradinglib.com/en/pedia/o/options_market_making.html)).

---

## 5. Optimal Delta Hedging: Frequency and Method

### 5.1 The Hedging Frequency Tradeoff

From the Hoggard, Whalley, and Wilmott (1994) model cited on [Quant StackExchange](https://quant.stackexchange.com/questions/75788/optimal-delta-hedging-frequency-when-gamma-scalping):

```
E[P&L] = 0.5 * Gamma * S^2 * ((sigma_r^2 - sigma_i^2) * dt - a * sigma_r * sqrt(2/(pi * dt^3)))
```

Where `a` is the transaction cost fraction and `dt` is the hedging interval. Key insights:
- As hedging interval approaches 0, costs approach infinity (the `sqrt(2/(pi*dt^3))` term dominates)
- As hedging interval approaches infinity, you lose gamma P&L from under-hedging
- **There is a Sharpe-optimal hedging frequency that balances gamma capture vs. transaction costs**

### 5.2 Fixed-Time vs. Fixed-Delta Threshold

- **Fixed-time (every 30-60 min):** Captures most intraday vol with manageable costs. Professional market makers typically hedge 4-8x/day ([Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/))
- **Fixed-delta threshold (+/- 10-20 deltas):** Hedges more during volatile sessions, less during quiet ones. Research by Taleb suggests thresholds outperform fixed-time in trending markets
- **Hybrid (recommended):** Fixed-time intervals as baseline with delta thresholds as override for large moves

### 5.3 The Breakeven Formula

```
Daily breakeven move = sqrt(2 * Theta / (Gamma * 100))
```

For SPY ATM straddle (30 DTE, VIX 16): breakeven daily move ~ $1.76, or ~0.33% daily, or ~5.2% annualized ([Volatility Box](https://volatilitybox.com/research/gamma-scalping-explained/)). The critical insight: **gamma P&L scales with the SQUARE of the move** (`0.5 * Gamma * Move^2`), making the strategy inherently long convexity. One $4 move generates 4x the P&L of a $2 move.

---

## 6. Advanced Strategies: Beyond Simple Gamma Scalping

### 6.1 Dispersion Trading

Dispersion trading -- selling index vol and buying single-stock vol (or vice versa) -- exploits the "correlation risk premium." The index implied vol includes an implicit correlation assumption that tends to be too high, because index options embed additional demand from portfolio hedgers. This creates a structural edge for selling index straddles and buying component straddles ([Reddit r/quant](https://www.reddit.com/r/quant/comments/1nmxdef/almost_everything_you_wanted_to_know_about/)).

There are two main styles:
1. **Vol-of-vol / vega dispersion:** Trade the spread between index IV and weighted-average component IV. Profits when implied correlation is overpriced.
2. **Gamma dispersion:** Delta-hedge both legs and profit from the difference in realized correlations vs. implied.

### 6.2 Skew Trading for Long Gamma + Theta

A sophisticated approach from professional vol traders: sell expensive downside puts (high IV due to skew) and buy ATM calls (lower IV). If skew is pronounced enough, you can be simultaneously **long gamma and collecting theta** ([Reddit r/options](https://www.reddit.com/r/options/comments/ckylbz/is_it_possible_to_be_long_gamma_and_theta/)). The risk: a sharp down move crushes you on the short puts (you are short volga/vanna).

### 6.3 Conditional Gamma Scalping with Vol Forecast

The most implementable professional approach for our backtest:

1. **Build a vol forecast** (HAR-RV or GARCH-based)
2. **Compare forecast to current IV** at trade inception
3. **Only enter long gamma when forecast RV > IV by a threshold** (e.g., 2+ vol points)
4. **Use regime filters:** VIX term structure slope, recent RV trend, dealer GEX
5. **Size based on edge magnitude:** Larger positions when forecast edge is larger
6. **Exit rules:** Stop if cumulative gamma P&L < theta after 7-10 days; hard exit at 14-21 DTE

---

## 7. The Academic Literature: Key Papers

| Paper | Key Finding | Source |
|-------|-------------|--------|
| Carr & Wu (2009), "Variance Risk Premia" | VRP is pervasive across assets; ATM IV is biased upward forecast of RV | [NYU Engineering](https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf) |
| Corsi (2009), HAR-RV Model | Simple 3-component model (daily/weekly/monthly RV) captures long memory of vol | [Portfolio Optimizer](https://portfoliooptimizer.io/blog/volatility-forecasting-har-model/) |
| Fallon, Park & Yu (2015) | Diversified VRP strategy achieves 1.0 Sharpe; profitable across equities, FI, FX, commodities | [Alpha Architect](https://alphaarchitect.com/the-variance-risk-premium-is-pervasive/) |
| Lu (Imperial College), "Harvesting VRP" | Delta-hedged option selling profitable under stochastic vol; variance swaps more efficient | [Imperial College](https://www.imperial.ac.uk/media/imperial-college/faculty-of-natural-sciences/department-of-mathematics/math-finance/Shibo_Lu_01210524.pdf) |
| Fassas & Hourvouliades (2018) | VIX backwardation predicts positive SPX returns (contrarian signal); contango has no predictive power | [Macrosynergy](https://macrosynergy.com/research/vix-term-structure-as-a-trading-signal/) |
| Blake, Gandhi, Jakkula (2025) | Regime-switching HAR models outperform fixed HAR across all market periods | [arXiv:2510.03236](https://arxiv.org/html/2510.03236v1) |
| Hoggard, Whalley, Wilmott (1994) | Optimal discrete hedging frequency balances gamma capture vs. transaction costs | [Quant StackExchange](https://quant.stackexchange.com/questions/75788/optimal-delta-hedging-frequency-when-gamma-scalping) |
| Sepp (SSRN) | Sharpe ratio optimization for discrete delta-hedging under transaction costs | [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1865998) |
| Sinclair, *Volatility Trading* (Wiley) | Practical vol forecasting framework; vol cones; position sizing by Kelly criterion | [Wiley](https://www.wiley.com/en-us/Volatility+Trading,+++Website,+2nd+Edition-p-9781118416723) |

---

## 8. Actionable Recommendations for Our Backtest

Based on this research, here are the specific changes that would give our gamma scalping backtest an actual edge:

### 8.1 Add a Volatility Forecast Module (Priority 1)

Implement a HAR-RV model using daily OHLC data (no need for intraday):

```python
# Using Parkinson range-based variance proxy
parkinson_var = (1/(4*log(2))) * (log(high/low))^2

# HAR components
RV_daily = parkinson_var[t]
RV_weekly = mean(parkinson_var[t-4:t+1])  # 5-day average
RV_monthly = mean(parkinson_var[t-21:t+1])  # 22-day average

# Forecast
RV_forecast = beta_0 + beta_d * RV_daily + beta_w * RV_weekly + beta_m * RV_monthly
```

Train on rolling 252-day windows. Annualize the forecast and compare to current ATM IV. **Only enter trades when RV_forecast > IV by at least 2 vol points.**

### 8.2 Add Regime Filter (Priority 2)

Implement a simple two-regime filter:
- **Compute the VIX term structure slope** (use VIX9D vs VIX, or VIX vs VIX3M if available; otherwise proxy with short-dated vs longer-dated IV on the underlying)
- **Flag backwardation regimes** (short-term IV > long-term IV)
- **Compute a rolling 5-day RV / 20-day RV ratio** as a vol momentum signal
- Only enter long gamma when: (a) vol forecast > IV, AND (b) at least one regime signal is favorable (backwardation, or rising RV momentum)

### 8.3 Improve Delta Hedging Logic (Priority 3)

Replace fixed-interval hedging with a **hybrid threshold system:**
- Baseline: hedge every 60 minutes (or end-of-bar in backtest)
- Override: hedge immediately if delta exceeds +/- 15 from neutral
- This captures large moves without overtrading in quiet markets

### 8.4 Add Exit Rules Based on Cumulative P&L (Priority 4)

- Track cumulative gamma P&L vs. cumulative theta cost daily
- If after 7 trading days, cumulative gamma P&L < 50% of cumulative theta, close the position (the vol forecast was wrong)
- Hard exit at 14 DTE regardless (theta acceleration makes continuation uneconomical)

### 8.5 Consider Underlying Selection (Priority 5)

SPY is the hardest underlying to gamma scalp profitably because:
- It has the tightest option pricing (smallest VRP overstatement)
- Dealer hedging in positive GEX suppresses realized vol
- The VRP is most consistent here, meaning options are most efficiently priced

Better candidates for long gamma:
- **High-beta single names** (MSTR, COIN) where IV mispricings are larger and RV is more volatile
- **QQQ** during tech-driven vol regimes
- **Small cap ETFs (IWM)** where hedging flow is less sophisticated
- Any underlying approaching a known catalyst (earnings, FOMC) where RV typically spikes

### 8.6 Consider Short Gamma as Default, Long Gamma as Exception

The most honest take from the research: **the default profitable vol strategy is short gamma (selling options, delta-hedging).** This works 70-85% of the time because of the VRP. Long gamma should be treated as the conditional exception -- deployed only when your vol forecast model signals a VRP inversion (forecasted RV > IV).

A more robust backtest architecture would be:
- **Baseline:** Short gamma (sell straddles, delta-hedge) -- collects VRP
- **Override to long gamma:** When vol forecast signals RV > IV AND regime filters confirm
- **Flat / no position:** When signals are ambiguous

---

## 9. Summary: The Professional Edge Stack

| Layer | What Professionals Have | What We Can Implement |
|-------|------------------------|----------------------|
| **Vol Forecast** | HAR-RV, GARCH, proprietary models, ML | HAR-RV with range-based proxies |
| **Regime Detection** | Real-time dealer GEX, flow data, VIX curve | VIX term structure proxy, RV momentum |
| **Conditional Entry** | Only trade when IV < forecast RV | Same -- this is the core edge |
| **Bid-Ask Edge** | Earn spread on every trade | Cannot replicate; accept as cost |
| **Flow Information** | See institutional order flow | Cannot replicate |
| **Speed** | Microsecond hedging, continuous | Discrete hedging, 4-8x/day |
| **Capital Efficiency** | Portfolio margin, cross-margining | Limited |
| **Diversification** | Multi-asset VRP (FX, rates, commodities) | Can diversify across underlyings |

The bottom line: we can implement layers 1-3 (vol forecast, regime detection, conditional entry) in our backtest. These are the layers that transform gamma scalping from a -EV proposition into a conditional +EV strategy. The remaining layers (spread capture, flow, speed) are structural advantages we cannot replicate but also do not need if our forecast model is good enough to offset them.
