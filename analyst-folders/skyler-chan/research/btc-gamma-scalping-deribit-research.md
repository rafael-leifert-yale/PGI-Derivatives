# Bitcoin Gamma Scalping on Deribit: Comprehensive Research Summary

*Research compiled: 2026-04-05*

---

## 1. How Gamma Scalping Works for Bitcoin Options on Deribit

### Core Mechanics

Gamma scalping involves buying options (going long gamma) and continuously delta-hedging the position as the underlying moves. The strategy profits when realized volatility exceeds implied volatility -- the gamma P&L from rebalancing exceeds the theta (time decay) cost of holding the options.

**Step-by-step process** ([Deribit Insights](https://insights.deribit.com/industry/how-to-use-delta-hedging-to-lock-up-profits/)):

1. **Buy ATM options** (straddle or strangle) to establish a long gamma position with near-zero delta
2. **Delta-hedge** using perpetual futures or spot BTC whenever delta drifts materially from zero
3. **Each hedge locks in profit**: When BTC rises, delta goes positive -- sell perps/spot to flatten. When BTC falls, delta goes negative -- buy perps/spot to flatten. This mechanically buys low and sells high.
4. **Repeat** throughout the option's life

**Key insight from Deribit Insights**: "According to experience, this strategy can recover about half of the premium if the market stays quiet, effectively making option buyers more robust, and increasing their chance of holding on and making a big hit when a major market move finally comes." ([Deribit Insights](https://insights.deribit.com/industry/how-to-use-delta-hedging-to-lock-up-profits/))

### Worked Example (from Deribit)

- BTC at $9,600. Trader buys a call with delta +0.40
- Shorts 0.40 BTC perpetual to neutralize delta
- BTC rallies to $10,400: Call delta rises to +0.90, perp delta stays -0.40. Net delta = +0.50. Average delta during move ~0.25, profit ~ 0.25 * $800 = $200
- Trader sells additional 0.50 perps to re-neutralize (now short 0.90 perps total)
- BTC falls back to $9,600: Call delta drops to +0.40, perp delta still -0.90. Net delta = -0.50. Profit ~ 0.25 * $800 = $200
- **Total gamma scalping P&L: $400** from a round-trip, vs $0 for an unhedged option holder

### The Fundamental P&L Equation

The daily P&L of a gamma scalp can be expressed as:

```
Daily P&L = 0.5 * Gamma * (Realized Move)^2 - Theta
```

Or equivalently:

```
Daily P&L = 0.5 * Gamma * S^2 * [(Realized Vol)^2 - (Implied Vol)^2] * dt
```

The strategy is profitable when **realized volatility > implied volatility** (the price paid for the options).

### Bid-Ask Spread Considerations for Crypto

The most actively traded BTC options on Deribit are those expiring in ~30 days, with bid-ask spreads around **0.0005 to 0.0015 BTC**. For ATM options this represents ~10% of premium; for OTM options it can reach 20%. Round-trip costs (open + close via market orders) can consume 20-40% of premium ([Deribit Insights](https://insights.deribit.com/industry/how-to-use-delta-hedging-to-lock-up-profits/)). This makes it critical to either hold options to expiration or use limit orders.

---

## 2. Deribit Options Product Specifications

### Contract Structure

- **Style**: European (exercised only at expiry)
- **Settlement**: Cash-settled in BTC (inverse) or USDC (linear). No physical delivery of the underlying.
- **Contract size**: 1 BTC per contract (inverse); varies for linear USDC options
- **Settlement currency**: BTC for inverse options, USDC for linear options
- **Settlement time**: All expirations occur at **08:00 UTC**
- **Delivery price**: 30-minute TWAP of the Deribit Index, calculated from 07:30 to 08:00 UTC (snapshot every 4 seconds)

([Deribit Support - Settlement](https://support.deribit.com/hc/en-us/articles/29734325712413-Settlement))

### Available Expirations (BTC Inverse Options)

| Timeframe | Number Available | Expiry Schedule |
|-----------|-----------------|-----------------|
| **Daily** | 1, 2, 3, 4 | Every day at 08:00 UTC |
| **Weekly** | 1, 2, 3 | Every Friday at 08:00 UTC |
| **Monthly** | 1, 2, 3 | Last Friday of each month at 08:00 UTC |
| **Quarterly** | 1, 2, 3, 4 | Last Friday of March, June, September, December at 08:00 UTC |

Overlapping expiries are not duplicated -- if a quarterly expiry falls on the last Friday of a month, no separate monthly/weekly/daily is created for that date. ([Deribit Support - Contract Introduction Policy](https://support.deribit.com/hc/en-us/articles/25944688876957-Contract-Introduction-Policy))

### Strike Price Availability

Deribit introduces options with **delta range of 0.1 to 0.9** for calls and **-0.1 to -0.9** for puts. Strike intervals are dynamic and vary based on time to expiry, recent volatility, underlying price, and liquidity. ([Deribit Support - Contract Introduction Policy](https://support.deribit.com/hc/en-us/articles/25944688876957-Contract-Introduction-Policy))

### Fee Structure

**Trading Fees** (Standard tier) ([Deribit Support - Fees](https://support.deribit.com/hc/en-us/articles/25944746248989-Fees)):

| Product | Maker Fee | Taker Fee |
|---------|-----------|-----------|
| **BTC Options** | 0.03% of underlying (0.0003 BTC), capped at 12.5% of option price | 0.03% of underlying (0.0003 BTC), capped at 12.5% of option price |
| **BTC Perpetual & Futures** | 0.00% (free) | 0.05% |
| **BTC Weekly Futures** | -0.01% (rebate) | 0.05% |

**Fee formula for options**: `MIN(0.0003 BTC, 0.125 * OptionPrice) * Amount`

**Delivery Fees at Expiry**:

| Product | Delivery Fee |
|---------|-------------|
| BTC Daily Options | 0% (exempt) |
| BTC Weekly Futures | 0% (exempt) |
| BTC Options (non-daily) | 0.015%, capped at 12.5% of option value |
| BTC Futures | 0.025% |

**Volume-based fee tiers** range from Standard to VIP 6. VIP 1 requires 100k USDC equity or $250k total equity. VIP 2+ requires $200M+ in 30-day volume.

**Option combo discount**: When a combo includes both buy and sell legs, the direction with the lower total fees has its fees zeroed out.

**No fees on**: Funding payments, holding positions overnight, leverage, API usage, spot trading.

---

## 3. Bitcoin Volatility Patterns

### General Volatility Profile

- BTC typically moves **2-3% per day** on average, with high standard deviation around that average ([Reddit r/CryptoCurrency](https://www.reddit.com/r/CryptoCurrency/comments/tyeq4o/))
- A 10% weekly drop is within normal fluctuation for BTC
- BTC annualized volatility has historically ranged from ~30% (quiet periods) to 100%+ (crisis periods)
- BTC volatility has been **declining secularly** as the asset matures and adoption grows

### Intraday Patterns

Academic research has documented clear intraday seasonality in BTC:

- **Volume and volatility follow a "reverse V-shaped pattern"** throughout the day, with peak activity during overlapping US/European trading hours ([ScienceDirect - Time-of-day periodicities](https://www.sciencedirect.com/science/article/abs/pii/S1544612319301904))
- **Highest volatility and volume** occurs during traditional market hours (roughly 13:00-21:00 UTC), correlating with US equity market activity ([Springer - Intraday evidence from centralized exchanges](https://link.springer.com/article/10.1007/s11156-024-01304-1))
- **Significant returns between 21:00 and 23:00 UTC** have been documented as a persistent anomaly ([QuantPedia](https://quantpedia.com/are-there-seasonal-intraday-or-overnight-anomalies-in-bitcoin/))
- **Sunday evenings (New York time)** show strongly positive intraday trend performance, with momentum continuing into Monday ([Concretum Group](https://concretumgroup.com/seasonality-in-bitcoin-intraday-trend-trading/))

### Weekend Volatility

- **Reduced liquidity** during weekends can exaggerate price swings
- **Implied volatility tends to fall** heading into weekends due to lower institutional participation
- **Realized volatility can spike** from macro events, social sentiment, or exchange-led activity during off-hours
- This IV/RV divergence creates opportunities for gamma scalpers who buy options when IV compresses pre-weekend ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/))

### Event-Driven Volatility

- US macroeconomic news (CPI, FOMC, NFP) significantly impacts BTC intraday volatility ([Taylor & Francis - Macroeconomic news and intraday seasonal volatility](https://www.tandfonline.com/doi/abs/10.1080/00036846.2023.2212970))
- Options expiry events (especially large quarterly expiries) can temporarily influence spot volatility
- "Vol shocks" in BTC have historically peaked around 1.45x the normal term structure level, as seen in March 2020, May 2021, June 2022, and November 2022 ([Deribit Insights / Amberdata](https://insights.deribit.com/industry/bitcoin-options-finding-edge-in-four-years-of-volatility-regimes/))

---

## 4. Deribit DVOL Index

### What DVOL Is

DVOL (Deribit Volatility Index) is the crypto equivalent of the VIX. It uses the implied volatility smile of relevant expiries to output a single number representing the **30-day annualized implied volatility** of BTC (or ETH). ([Deribit Insights - DVOL](https://insights.deribit.com/exchange-updates/dvol-deribit-implied-volatility-index/))

### Key Characteristics

- Calculated using the same variance-swap methodology as VIX (model-free implied volatility from the full options chain)
- Uses interpolation between the two nearest expiries bracketing 30 days
- Available as a tradable futures product (BTCDVOL and ETHDVOL futures)
- DVOL futures: Maker 0.00%, Taker 0.05%, Delivery fee 0.05%

### DVOL vs Realized Volatility (Variance Risk Premium)

The **Variance Risk Premium (VRP)** -- the spread between implied and realized volatility -- is a critical concept for gamma scalpers:

- **BTC implied volatility has consistently been higher than subsequently realized volatility** ([Fidelity Digital Assets](https://www.fidelitydigitalassets.com/research-and-insights/closer-look-bitcoins-volatility))
- This means sellers of options have historically had a structural edge, and gamma scalpers (who are long options) face a headwind from the VRP
- However, the VRP is **not constant** -- it varies significantly by regime, and there are periods where realized vol exceeds implied vol, creating profitable gamma scalping opportunities

**Research findings from Amberdata/Deribit Insights** ([Deribit Insights](https://insights.deribit.com/industry/bitcoin-options-finding-edge-in-four-years-of-volatility-regimes/)):
- BTC term structure is in **contango ~77.5% of the time** (longer-dated options have higher IV than shorter-dated)
- Backwardation occurs in reaction to volatility shocks and has a **mean duration of ~85 hours**
- Backwardation events typically peak around the 1.45 level before reverting

### Academic Research

- "The Bitcoin VIX and Its Variance Risk Premium" (PM Research) -- uses high-frequency Deribit data to construct a BTC VIX analog and documents a significant variance risk premium ([PM Research](https://www.pm-research.com/content/iijaltinv/23/4/84))
- Du (2025) proposes a model incorporating volatility-of-volatility (VOV) dynamics for crypto option pricing ([Wiley - Journal of Futures Markets](https://onlinelibrary.wiley.com/doi/10.1002/fut.70029))

---

## 5. Delta Hedging: Perpetuals vs Spot

### Using Perpetual Futures (Preferred Method on Deribit)

Perpetual futures are the primary hedging instrument for BTC options on Deribit because:

- **Highest liquidity** of any instrument on the platform
- **No expiry** -- no roll risk
- **Up to 50x leverage** -- capital efficient
- **Same settlement currency** (BTC for inverse) -- no cross-currency risk
- Deribit options Greeks and margin calculations reference the perpetual/futures price

**Advantages**:
- Deep order books, tight spreads
- Continuous trading 24/7
- Maker fee is 0% (free) on BTC perpetuals; taker fee only 0.05%
- Integrated margin with options on Deribit (portfolio margin available)

**Disadvantages**:
- **Funding rate exposure** (see Section 6)
- Mark price can deviate from index temporarily
- Liquidation risk if under-margined

### Using Spot

- Deribit now offers spot BTC trading with **0% maker and taker fees**
- No funding rate risk
- Simpler conceptually but less capital-efficient (no leverage)
- Hedging with spot on Deribit avoids basis risk entirely since the index references spot prices

### Hedging Frequency Considerations

From the MenthorQ guide ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/)):
- **Near-the-money options with high gamma** (3-7 day expiries) provide the most scalping opportunity
- Gamma is highest as expiry approaches -- hedging must be more frequent closer to expiration
- Common approaches: hedge at fixed time intervals (hourly, daily) or when delta exceeds a threshold (e.g., +/-0.10)
- More frequent hedging captures more realized vol but incurs more transaction costs
- Optimal frequency depends on the gamma/theta ratio and expected realized vol

---

## 6. Key Risks Specific to Crypto Gamma Scalping

### Funding Rate Risk

On Deribit, perpetual futures funding works as follows ([Deribit Support - Funding Specifications](https://support.deribit.com/hc/en-us/articles/31424939178397-Funding-Specifications)):

- **Expressed as an 8-hour rate** but calculated and paid **continuously** (updated every few seconds)
- **Formula**: `Funding Rate = Max(0.025%, Premium Rate) + Min(-0.025%, Premium Rate)` where `Premium Rate = ((Mark Price - Index) / Index) * 100%`
- **Damper**: If premium rate is within +/- 0.025%, funding rate is 0%
- **Cap**: BTC funding is capped at +/- 0.5% per 8 hours (1.5%/day max)
- **Direction**: When positive, longs pay shorts; when negative, shorts pay longs
- **No Deribit fees** on funding -- it's purely peer-to-peer

**Impact on gamma scalping**: If you are short BTC perpetuals as a hedge and funding is positive (common in bull markets), you *receive* funding. If funding is negative (bear markets or risk-off), you *pay* funding. This can meaningfully add to or subtract from gamma scalping P&L over multi-day holds.

**Example**: Holding 1 BTC short at 0.05% funding rate for 8 hours = receiving 0.0005 BTC (~$50 at $100k BTC).

### Liquidation Risk

- Deribit uses a **mark price** system (not last trade price) for liquidation
- Portfolio margin accounts benefit from cross-margining between options and futures
- Liquidation fee on BTC futures/perpetuals: **0.75%** total (includes extra fee going to insurance fund) ([Deribit Support - Fees](https://support.deribit.com/hc/en-us/articles/25944746248989-Fees))
- For options: liquidation fee capped at 25% of option value
- Standard margin: entire account balance used as collateral for positions in that asset
- Portfolio margin: risk-based model evaluating worst-case scenarios across the portfolio

### Basis Risk (Options vs Perpetuals)

- Options are priced using **mark price** derived from Deribit's model (not order book mid)
- Perpetuals trade freely and can deviate from the underlying index
- During high volatility, perpetual premium/discount can widen, causing the hedge to behave differently than expected
- Delta of expiring options decays linearly to zero over the last 30 minutes before 08:00 UTC expiry, which is a unique Deribit risk control ([Deribit Support - Settlement](https://support.deribit.com/hc/en-us/articles/29734325712413-Settlement))

### Inverse Contract (Quanto) Risk

For inverse (BTC-settled) options on Deribit:
- All P&L is denominated in BTC
- When BTC drops sharply, your BTC-denominated profits are worth less in USD terms
- This creates a convexity effect: short BTC perpetuals as a hedge deliver *fewer* USD profits in a crash than expected, because the BTC you earn is worth less
- This "quanto risk" or "coin-margined risk" is unique to crypto and must be modeled

### Volatility Crush Risk

- If IV compresses while holding long options, the position loses value even if realized vol is adequate
- Particularly relevant after known events (FOMC, halving) where IV often drops post-event
- Entering gamma scalps when IV is **low relative to historical realized vol** is critical

### Liquidity Risk

- Weekend and off-hours: crypto trades 24/7 but **liquidity is materially thinner** on weekends
- Options order books can thin out rapidly during volatility spikes
- ITM options are particularly illiquid -- bid-ask can be 0.0050 BTC or more; deep ITM may have no quotes ([Deribit Insights](https://insights.deribit.com/industry/how-to-use-delta-hedging-to-lock-up-profits/))

---

## 7. Optimal Timeframes for Gamma Scalping BTC

### Based on Volatility Profile

| Approach | Timeframe | Rationale |
|----------|-----------|-----------|
| **Daily/0-DTE** | Options expiring same day or next day | Highest gamma per unit of premium. Theta is maximum, requiring large realized moves. Best in high-vol regimes. |
| **3-7 Day (Weekly)** | Weekly options | Sweet spot: high gamma with more manageable theta. Most commonly used for gamma scalps in crypto. ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/)) |
| **14-30 Day** | Bi-weekly to monthly | Lower gamma but also lower theta drag. More forgiving. Allows surviving multi-day quiet periods. |
| **Weekend Scalps** | Buy Friday, scalp through weekend | Exploit IV compression going into weekends while realized vol stays elevated. Close Monday when liquidity returns. ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/)) |

### Practical Guidance

- **Entry timing**: Best to enter when IV is cheap relative to recent realized vol (e.g., BTC ATM IV at 35% vs 1-week realized at 45%) ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/))
- **Avoid**: High IV environments where premiums are expensive and breakeven realized vol is hard to achieve
- **Hedge frequency**: For weekly options, delta-hedging every 1-4 hours during active markets is common; more frequently (every 15-30 min) as expiry approaches
- **Consider calendar spreads**: Long short-dated, short long-dated to isolate gamma while reducing net theta cost

---

## 8. Deribit Settlement and Margin System

### Daily Settlement Process

([Deribit Support - Settlement](https://support.deribit.com/hc/en-us/articles/29734325712413-Settlement))

- **Time**: Every day at **08:00 UTC**
- **Process**: All session P&L on open futures and perpetuals is realized and transferred to available balance
- **Brief trading pause** during settlement (orders rejected with "settlement in progress")
- **Session P&L resets** to zero after settlement
- Profits are immediately usable as margin for derivatives after settlement
- Profits are only available for **withdrawal or transfer** after daily settlement completes
- **No fees** charged for daily settlement

### Delivery at Expiry

- **Delivery price**: 30-minute TWAP of Deribit Index (07:30-08:00 UTC), sampled every 4 seconds
- **Options**: European-style, automatically exercised if ITM. Cash-settled (no asset delivery)
- **Delta decay**: Expiring options/futures delta decays linearly to zero over last 30 minutes before 08:00 UTC -- this reduces pin risk
- **Insurance fund**: Covers negative balances from bankrupt accounts. If insufficient, socialized loss may apply (pro-rata to profitable traders in that session)

### Margin System

([Deribit Support - Margin types and usage](https://support.deribit.com/hc/en-us/articles/25944811317149-Margin-types-and-usage))

**Standard Margin**: Entire account balance used as margin for positions in that asset. Each position shares the same pool.

**Portfolio Margin**: Risk-based model that evaluates worst-case scenarios across the portfolio. Benefits:
- Cross-margining between options and futures
- Reduced margin requirements for hedged positions (e.g., long options + short perpetuals)
- Critical for gamma scalping as it recognizes the offsetting risk between options and delta hedges

**Cross Collateral**: Allows using ETH, USDC, or USDT equity as collateral for BTC positions (and vice versa). A collateral fee (default 0.05%/day) applies when a currency's equity goes negative. ([Deribit Support - Cross collateral](https://support.deribit.com/hc/en-us/articles/25944777203869-Cross-collateral-specifications))

---

## 9. Academic and Practitioner Research

### Key Papers and Sources

1. **"The Bitcoin VIX and Its Variance Risk Premium"** -- Acquires high-frequency Deribit option prices (15-min sampling) to construct a BTC implied volatility index. Documents a significant and persistent variance risk premium in BTC options, similar to equity markets. ([PM Research](https://www.pm-research.com/content/iijaltinv/23/4/84))

2. **"Pricing Cryptocurrency Options With Volatility of Volatility"** (Du, 2025) -- Proposes a novel option pricing model incorporating volatility-of-volatility (VOV) dynamics and an associated risk premium. Integrates realized variance with VOV for crypto options. ([Wiley Journal of Futures Markets](https://onlinelibrary.wiley.com/doi/10.1002/fut.70029))

3. **"Implied volatility estimation of bitcoin options and the stylized facts of option pricing"** (PMC, 2021) -- Studies IV estimation methods for BTC options on Deribit and documents key stylized facts of the BTC options market. ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC8418903/))

4. **"Risk Premia in the Bitcoin Market"** (arXiv, 2024) -- Explores cryptocurrency options-implied risk premia, noting that research on crypto derivatives is still in early stages. ([arXiv](https://arxiv.org/html/2410.15195v2))

5. **"Bitcoin Options: Finding Edge in Four Years of Volatility Regimes"** (Amberdata / Deribit Insights, 2023) -- Comprehensive analysis of BTC options from 2019-2022 covering term structure, spot/vol dynamics, VRP, and backtested strategies including systematic volatility trading and risk-reversal premium harvesting. ([Deribit Insights](https://insights.deribit.com/industry/bitcoin-options-finding-edge-in-four-years-of-volatility-regimes/))

6. **"Time-of-Day Periodicities of Trading Volume and Volatility in Bitcoin Exchange"** -- Documents reverse V-shaped intraday volatility and volume patterns correlated with traditional market hours. ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1544612319301904))

7. **"Bayesian Analysis of Bitcoin Volatility Using Minute-by-Minute Data"** (MDPI, 2025) -- Analyzes BTC/USDT pair volatility using 1-minute data with stochastic volatility models. ([MDPI](https://www.mdpi.com/2227-7390/13/16/2691))

8. **Glassnode: "Taker-Flow-Based Gamma Exposure"** (2025) -- Introduces a novel metric mapping BTC options gamma exposure across strike prices on Deribit, distinguishing positive (dealer long gamma) vs negative (dealer short gamma) zones. ([Glassnode](https://insights.glassnode.com/gamma-exposure/))

### Practitioner Resources

- **Greeks.live App**: Integrated with Deribit, offers "delta-hedging with one click" for manual gamma scalping ([Deribit Insights](https://insights.deribit.com/industry/how-to-use-delta-hedging-to-lock-up-profits/))
- **Deribit API**: Full programmatic access for automated delta hedging via REST and WebSocket APIs ([Deribit API Docs](https://docs.deribit.com/))
- **MenthorQ Dashboard**: Gamma models and realized volatility charts for crypto ([MenthorQ](https://menthorq.com/guide/gamma-scalping-in-crypto-markets/))
- **Amberdata Derivatives**: Professional-grade crypto volatility analytics used in the Deribit Insights research

---

## Key Takeaways for Strategy Design

1. **BTC's high absolute volatility (50-80% annualized typical)** makes it an attractive gamma scalping candidate compared to traditional assets
2. **The variance risk premium works against long gamma** on average -- entry timing based on IV/RV spread is critical
3. **Weekly options (3-7 DTE)** offer the best gamma-to-theta ratio for scalping
4. **Perpetual futures** are the preferred hedging instrument due to liquidity, but funding rate exposure must be monitored
5. **Weekend volatility dislocations** create a recurring opportunity: IV compresses pre-weekend while RV can spike
6. **Deribit's 08:00 UTC settlement** creates natural daily cycles to be aware of
7. **Portfolio margin** is strongly recommended to reduce capital requirements for the combined options + futures position
8. **Inverse (BTC-settled) contracts** introduce quanto risk that must be modeled -- USD P&L is non-linear in BTC terms
9. **Transaction costs** (0.03% per option trade + 0.05% per perp taker trade) can erode P&L with frequent rebalancing -- use maker orders where possible
10. **The BTC options market is still maturing** -- there remain structural inefficiencies that sophisticated gamma scalpers can exploit
