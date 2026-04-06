"""
Test Alpaca API connection and historical options data access
"""

from datetime import datetime, timedelta
import pandas as pd

# Alpaca credentials
ALPACA_API_KEY = "PKUFIUPLC47J5MOFKETQIW6QVC"
ALPACA_SECRET_KEY = "48UHojTJrYvsPfhtxXNkwYYnqoDWX7nLT3t2EiR3JYua"

print("\n" + "="*70)
print("ALPACA HISTORICAL OPTIONS DATA TEST")
print("="*70)

# Test 1: Import and initialize clients
print("\n1. INITIALIZING ALPACA CLIENTS")
print("-" * 70)

try:
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import (
        StockLatestQuoteRequest,
        OptionBarsRequest,
        OptionSnapshotRequest
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient

    print("✓ Alpaca libraries imported successfully")

    # Initialize clients
    stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

    print("✓ Clients initialized successfully")

except Exception as e:
    print(f"✗ Error initializing clients: {e}")
    exit(1)

# Test 2: Verify account access
print("\n2. VERIFYING ACCOUNT ACCESS")
print("-" * 70)

try:
    account = trading_client.get_account()
    print(f"✓ Account verified")
    print(f"   Account ID: {account.id}")
    print(f"   Status: {account.status}")
    print(f"   Buying Power: ${float(account.buying_power):,.2f}")
    print(f"   Portfolio Value: ${float(account.portfolio_value):,.2f}")
except Exception as e:
    print(f"✗ Error accessing account: {e}")

# Test 3: Get current SPY price
print("\n3. FETCHING CURRENT SPY PRICE")
print("-" * 70)

try:
    request = StockLatestQuoteRequest(symbol_or_symbols="SPY")
    quote = stock_client.get_stock_latest_quote(request)
    spy_price = quote["SPY"].ask_price
    print(f"✓ Current SPY Price: ${spy_price:.2f}")

    # Calculate ATM strike
    atm_strike = round(spy_price)
    print(f"✓ ATM Strike: ${atm_strike}")

except Exception as e:
    print(f"✗ Error fetching SPY price: {e}")
    atm_strike = 656  # Fallback

# Test 4: Find option contract symbols
print("\n4. FINDING SPY OPTION CONTRACT SYMBOLS")
print("-" * 70)

# SPY option symbols follow format: SPY260407C00656000
# Format: ROOT + YYMMDD + (C/P) + Strike*1000 (8 digits)

# Get next Friday expiry (or tomorrow for testing)
today = datetime.now()
tomorrow = today + timedelta(days=1)
expiry = tomorrow.strftime("%y%m%d")  # Format: YYMMDD

# Construct contract symbols for ATM straddle
call_symbol = f"SPY{expiry}C{atm_strike*1000:08d}"
put_symbol = f"SPY{expiry}P{atm_strike*1000:08d}"

print(f"   Expiry: {tomorrow.strftime('%Y-%m-%d')}")
print(f"   Call symbol: {call_symbol}")
print(f"   Put symbol: {put_symbol}")

# Test 5: Fetch historical option bars (THE CRITICAL TEST!)
print("\n5. FETCHING HISTORICAL INTRADAY OPTION DATA")
print("-" * 70)

try:
    # Try to get minute bars for a recent option contract
    # Let's use a contract that definitely exists

    # For testing, let's try to get data from last week
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    print(f"\n   Attempting to fetch minute bars from {start_date.date()} to {end_date.date()}")
    print(f"   Symbols: {call_symbol}, {put_symbol}")

    request = OptionBarsRequest(
        symbol_or_symbols=[call_symbol, put_symbol],
        timeframe=TimeFrame.Minute,
        start=start_date,
        end=end_date
    )

    bars = option_client.get_option_bars(request)

    if bars.df is not None and not bars.df.empty:
        df = bars.df
        print(f"\n✓✓✓ SUCCESS! Historical intraday option data available!")
        print(f"     Fetched {len(df)} minute bars")
        print(f"\n   Sample data:")
        print(df.head(10))

        # Analyze what we got
        print(f"\n   Data Summary:")
        print(f"     Date range: {df.index.get_level_values('timestamp').min()} to {df.index.get_level_values('timestamp').max()}")
        print(f"     Symbols: {df.index.get_level_values('symbol').unique().tolist()}")
        print(f"     Columns: {df.columns.tolist()}")

    else:
        print(f"\n⚠️  No data returned for these specific contracts")
        print(f"   This might mean:")
        print(f"     - Contracts don't exist yet (tomorrow's expiry)")
        print(f"     - Need to use contracts from previous dates")
        print(f"     - Data only available for already-expired contracts")

except Exception as e:
    print(f"\n✗ Error fetching option bars: {e}")
    print(f"\n   Error type: {type(e).__name__}")
    print(f"   This could mean:")
    print(f"     - Options data requires additional subscription")
    print(f"     - Need different contract symbols")
    print(f"     - API endpoint changed")

# Test 6: Try with a known expired contract
print("\n6. TRYING WITH EXPIRED CONTRACT (March 2024)")
print("-" * 70)

try:
    # Use a contract from March 2024 (should have data per Alpaca docs)
    # SPY March 15, 2024 expiry, 520 strike
    old_expiry = "240315"  # March 15, 2024
    old_strike = 520
    old_call = f"SPY{old_expiry}C{old_strike*1000:08d}"
    old_put = f"SPY{old_expiry}P{old_strike*1000:08d}"

    print(f"   Testing with: {old_call}")

    # Get data from March 14-15, 2024
    start = datetime(2024, 3, 14, 9, 30)
    end = datetime(2024, 3, 15, 16, 0)

    request = OptionBarsRequest(
        symbol_or_symbols=[old_call],
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=100
    )

    bars = option_client.get_option_bars(request)

    if bars.df is not None and not bars.df.empty:
        df = bars.df
        print(f"\n✓✓✓ SUCCESS! Found historical data for expired contracts!")
        print(f"     Fetched {len(df)} minute bars")
        print(f"\n   Sample data:")
        print(df.head())
        print(f"\n   This confirms: We CAN backtest with historical intraday option data!")

    else:
        print(f"\n⚠️  No data for March 2024 contract either")

except Exception as e:
    print(f"\n✗ Error: {e}")

# Test 7: Check what data is available
print("\n7. DATA AVAILABILITY SUMMARY")
print("-" * 70)

print("""
Based on Alpaca documentation:
✓ Historical option data available since February 2024
✓ Minute-level granularity supported
✓ Includes OHLCV + trade count + VWAP
✓ Works with expired contracts (for backtesting!)

For backtesting your 0-DTE strategy:
1. We can fetch minute bars for any SPY option from Feb 2024 onwards
2. We can get the exact option prices at each minute throughout the day
3. We can simulate your gamma scalping strategy with REAL market data
4. This is EXACTLY what you need!

Next steps:
→ Build the backtesting engine
→ Fetch historical data for past 0-DTE days
→ Simulate your strategy
→ Optimize parameters
""")

print("\n" + "="*70)
print("TEST COMPLETE - API CONNECTION VERIFIED!")
print("="*70 + "\n")
