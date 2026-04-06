"""
Test what historical data is actually available for backtesting
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

print("\n" + "="*70)
print("HISTORICAL DATA AVAILABILITY TEST")
print("="*70)

# Test 1: SPY Stock Data
print("\n1. SPY STOCK DATA")
print("-" * 70)

spy = yf.Ticker("SPY")

# Minute data
print("\n📊 Minute Data (1m interval):")
try:
    # Try to get last 7 days of minute data
    minute_data = spy.history(period="7d", interval="1m")
    print(f"   ✓ Available: {len(minute_data)} bars")
    print(f"   Date range: {minute_data.index[0]} to {minute_data.index[-1]}")
    print(f"   Limitation: ~7 days back maximum")
except Exception as e:
    print(f"   ✗ Error: {e}")

# 5-minute data
print("\n📊 5-Minute Data (5m interval):")
try:
    five_min_data = spy.history(period="60d", interval="5m")
    print(f"   ✓ Available: {len(five_min_data)} bars")
    print(f"   Date range: {five_min_data.index[0]} to {five_min_data.index[-1]}")
    print(f"   Limitation: ~60 days back maximum")
except Exception as e:
    print(f"   ✗ Error: {e}")

# Daily data
print("\n📊 Daily Data (1d interval):")
try:
    daily_data = spy.history(period="5y", interval="1d")
    print(f"   ✓ Available: {len(daily_data)} bars")
    print(f"   Date range: {daily_data.index[0]} to {daily_data.index[-1]}")
    print(f"   Limitation: Years of history available ✓")
except Exception as e:
    print(f"   ✗ Error: {e}")

# Test 2: Options Data
print("\n\n2. SPY OPTIONS DATA")
print("-" * 70)

# Available expirations
expirations = spy.options
print(f"\n📅 Available Expiration Dates: {len(expirations)}")
print(f"   Next few: {expirations[:5]}")
print(f"   Farthest out: {expirations[-1]}")

# Current option chain
print("\n📈 Current Option Chain Data:")
try:
    opt = spy.option_chain(expirations[0])
    calls = opt.calls
    puts = opt.puts

    print(f"   ✓ Calls: {len(calls)} strikes")
    print(f"   ✓ Puts: {len(puts)} strikes")
    print(f"\n   Available fields:")
    for col in calls.columns:
        print(f"      - {col}")

    # Check if we have volume/OI (indicates some history)
    sample_call = calls.iloc[len(calls)//2]
    print(f"\n   Sample ATM Call:")
    print(f"      Strike: ${sample_call['strike']}")
    print(f"      Last Price: ${sample_call['lastPrice']:.2f}")
    print(f"      Bid/Ask: ${sample_call['bid']:.2f} / ${sample_call['ask']:.2f}")
    print(f"      Volume: {sample_call['volume']}")
    print(f"      Open Interest: {sample_call['openInterest']}")
    print(f"      Implied Volatility: {sample_call['impliedVolatility']:.1%}")

except Exception as e:
    print(f"   ✗ Error: {e}")

# Test 3: Historical Options Prices
print("\n\n3. HISTORICAL OPTIONS PRICES (The Critical Test)")
print("-" * 70)

print("\n⚠️  CRITICAL LIMITATION:")
print("   yfinance does NOT provide historical intraday options prices")
print("   You CANNOT get: 'What was the call price at 10:30 AM yesterday?'")
print("\n   What this means for backtesting:")
print("   ✗ Can't replay actual option prices minute-by-minute")
print("   ✗ Can't get historical bid/ask spreads")
print("   ✗ Can't see how IV changed throughout the day")

# Test 4: Workarounds
print("\n\n4. BACKTESTING WORKAROUNDS")
print("-" * 70)

print("\n✅ SOLUTION 1: Black-Scholes Synthesis")
print("   • Use historical SPY prices (minute data available)")
print("   • Estimate IV from recent options (or use VIX)")
print("   • Calculate theoretical option prices using Black-Scholes")
print("   • Simulate P&L based on theoretical prices")
print("   • Pros: Can backtest any historical period")
print("   • Cons: Assumes efficient pricing, no bid/ask spread modeling")

print("\n✅ SOLUTION 2: End-of-Day Option Data")
print("   • Some services provide daily close prices for options")
print("   • Can backtest on daily timeframe (not intraday)")
print("   • Pros: Uses actual market prices")
print("   • Cons: Misses intraday gamma scalping opportunities")

print("\n✅ SOLUTION 3: Paid Data Providers")
print("   • OptionMetrics (expensive, institutional)")
print("   • CBOE DataShop (tick-level options data)")
print("   • Polygon.io (options data, $200+/mo)")
print("   • ThetaData (options historical data)")
print("   • Pros: Full historical granularity")
print("   • Cons: Expensive ($200-$1000+/month)")

print("\n✅ SOLUTION 4: Forward Testing Only")
print("   • Skip historical backtest")
print("   • Start collecting live data going forward")
print("   • Paper trade for 1-2 months to build track record")
print("   • Pros: Real market conditions, free")
print("   • Cons: Takes time, no historical validation")

# Test 5: What CAN we do?
print("\n\n5. REALISTIC BACKTESTING APPROACH")
print("-" * 70)

print("\n📋 Recommended Strategy:")
print("\n   PHASE 1: Theoretical Backtest (Now)")
print("   • Get 60 days of SPY 5-minute data")
print("   • Calculate theoretical option prices via Black-Scholes")
print("   • Simulate gamma scalping with synthetic options")
print("   • Validate strategy logic and risk management")
print("   • Optimize delta threshold and rehedge frequency")
print("\n   PHASE 2: Paper Trading (1-2 months)")
print("   • Connect Alpaca with secret key")
print("   • Run strategy live on paper account")
print("   • Collect real slippage and execution data")
print("   • Build confidence in live market conditions")
print("\n   PHASE 3: Small Live (After validation)")
print("   • Start with 1 contract positions")
print("   • Scale up gradually based on results")

print("\n" + "="*70)
print("CONCLUSION")
print("="*70)
print("""
For 0-DTE gamma scalping:

✓ SPY minute data: Available (7 days) / 5-min data (60 days)
✗ Historical intraday option prices: NOT available (free sources)
✓ Current option prices + IV: Available in real-time
✓ Black-Scholes simulation: Fully possible

Recommendation: Build a Black-Scholes simulator for theoretical backtest,
then validate with 1-2 months of paper trading before going live.
""")

print("="*70 + "\n")
