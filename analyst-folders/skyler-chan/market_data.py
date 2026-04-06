"""
Market Data Module for Gamma Scalping Strategy
Fetches SPY prices and options data from multiple sources
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yfinance as yf
from typing import Optional, Tuple, Dict
import warnings
warnings.filterwarnings('ignore')

try:
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import (
        StockBarsRequest, StockLatestQuoteRequest,
        OptionBarsRequest, OptionChainRequest
    )
    from alpaca.data.timeframe import TimeFrame
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("⚠️  Alpaca libraries not available. Using yfinance only.")


class MarketDataProvider:
    """
    Multi-source market data provider
    Falls back to yfinance if Alpaca credentials not available
    """

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.use_alpaca = False

        # Try to initialize Alpaca clients
        if ALPACA_AVAILABLE and api_key and secret_key and secret_key != "YOUR_SECRET_KEY_HERE":
            try:
                self.stock_client = StockHistoricalDataClient(api_key, secret_key)
                self.option_client = OptionHistoricalDataClient(api_key, secret_key)
                self.use_alpaca = True
                print("✓ Using Alpaca Market Data API")
            except Exception as e:
                print(f"⚠️  Alpaca connection failed: {e}")
                print("   Falling back to yfinance")
                self.use_alpaca = False
        else:
            print("✓ Using yfinance (free Yahoo Finance data)")

    def get_spy_price(self, symbol: str = "SPY") -> Optional[float]:
        """Get current SPY price"""
        if self.use_alpaca:
            try:
                request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                quote = self.stock_client.get_stock_latest_quote(request)
                return quote[symbol].ask_price
            except Exception as e:
                print(f"Alpaca price fetch error: {e}")

        # Fallback to yfinance
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                return data['Close'].iloc[-1]
        except Exception as e:
            print(f"yfinance price fetch error: {e}")

        return None

    def get_historical_bars(self, symbol: str, start: datetime, end: datetime,
                           timeframe: str = "1Min") -> pd.DataFrame:
        """
        Get historical price bars for backtesting

        Args:
            symbol: Stock symbol (e.g., "SPY")
            start: Start datetime
            end: End datetime
            timeframe: Bar timeframe ("1Min", "5Min", "1Hour", "1Day")

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if self.use_alpaca:
            try:
                # Map timeframe string to Alpaca TimeFrame
                tf_map = {
                    "1Min": TimeFrame.Minute,
                    "5Min": TimeFrame(5, "Min"),
                    "1Hour": TimeFrame.Hour,
                    "1Day": TimeFrame.Day
                }
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf_map.get(timeframe, TimeFrame.Minute),
                    start=start,
                    end=end
                )
                bars = self.stock_client.get_stock_bars(request)
                df = bars.df
                df.reset_index(inplace=True)
                df.columns = ['symbol', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'trade_count', 'vwap']
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
            except Exception as e:
                print(f"Alpaca bars fetch error: {e}, falling back to yfinance")

        # Fallback to yfinance
        try:
            ticker = yf.Ticker(symbol)

            # Convert timeframe to yfinance format
            interval_map = {
                "1Min": "1m",
                "5Min": "5m",
                "1Hour": "1h",
                "1Day": "1d"
            }
            interval = interval_map.get(timeframe, "1m")

            # Use start/end for better precision
            data = ticker.history(interval=interval, start=start, end=end)

            if data.empty:
                print(f"⚠️  No data returned for {symbol}")
                return pd.DataFrame()

            df = data.reset_index()
            df.columns = [col.lower() for col in df.columns]

            # Handle datetime column naming
            if 'datetime' in df.columns:
                df.rename(columns={'datetime': 'timestamp'}, inplace=True)
            elif 'date' in df.columns:
                df.rename(columns={'date': 'timestamp'}, inplace=True)

            return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        except Exception as e:
            print(f"yfinance bars fetch error: {e}")
            return pd.DataFrame()

    def get_option_chain(self, symbol: str, expiry_date: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Get option chain for a specific expiration date

        Args:
            symbol: Stock symbol (e.g., "SPY")
            expiry_date: Expiration date in YYYY-MM-DD format

        Returns:
            Tuple of (calls_df, puts_df)
        """
        if self.use_alpaca:
            try:
                # Note: Alpaca options data requires higher tier subscription
                print("⚠️  Alpaca options data requires Options Data subscription")
                print("   Falling back to yfinance")
            except Exception as e:
                print(f"Alpaca options fetch error: {e}")

        # Use yfinance for options (free)
        try:
            ticker = yf.Ticker(symbol)

            # Get available expiration dates
            expirations = ticker.options
            if expiry_date not in expirations:
                print(f"⚠️  Expiry date {expiry_date} not available")
                print(f"   Available dates: {expirations[:5]}")
                # Use closest expiration
                expiry_date = expirations[0]

            opt_chain = ticker.option_chain(expiry_date)
            calls = opt_chain.calls
            puts = opt_chain.puts

            return calls, puts

        except Exception as e:
            print(f"Error fetching option chain: {e}")
            return pd.DataFrame(), pd.DataFrame()

    def get_atm_strike(self, symbol: str = "SPY") -> float:
        """Find the at-the-money strike price"""
        current_price = self.get_spy_price(symbol)
        if current_price is None:
            return None

        # Round to nearest dollar (SPY options typically have $1 strikes)
        atm_strike = round(current_price)
        return atm_strike

    def get_0dte_expiry(self) -> str:
        """
        Get today's expiration date if it's a Friday (or closest expiry)

        Returns:
            Expiration date string in YYYY-MM-DD format
        """
        today = datetime.now()

        # SPY has daily expirations now, but let's focus on regular Friday expiries
        # or daily 0-DTE options
        try:
            ticker = yf.Ticker("SPY")
            expirations = ticker.options

            # Convert to datetime objects
            expiry_dates = [datetime.strptime(d, "%Y-%m-%d") for d in expirations]

            # Find today's expiry or nearest future expiry
            today_date = today.date()
            same_day_expiry = [d for d in expiry_dates if d.date() == today_date]

            if same_day_expiry:
                return same_day_expiry[0].strftime("%Y-%m-%d")
            else:
                # Get nearest expiry
                future_expiries = [d for d in expiry_dates if d.date() >= today_date]
                if future_expiries:
                    nearest = min(future_expiries)
                    return nearest.strftime("%Y-%m-%d")

        except Exception as e:
            print(f"Error finding 0-DTE expiry: {e}")

        # Default to today if all else fails
        return today.strftime("%Y-%m-%d")

    def get_risk_free_rate(self) -> float:
        """
        Get current risk-free rate (approximated using 3-month T-Bill)

        Returns:
            Annual risk-free rate as decimal (e.g., 0.05 for 5%)
        """
        try:
            # Use ^IRX (13-week Treasury Bill)
            ticker = yf.Ticker("^IRX")
            data = ticker.history(period="5d")
            if not data.empty:
                # ^IRX is in percent, convert to decimal
                rate = data['Close'].iloc[-1] / 100
                return rate
        except Exception as e:
            print(f"Error fetching risk-free rate: {e}")

        # Default to 4.5% if fetch fails
        return 0.045

    def calculate_time_to_expiry(self, expiry_date: str) -> float:
        """
        Calculate time to expiration in years

        Args:
            expiry_date: Expiration date in YYYY-MM-DD format

        Returns:
            Time to expiry in years (e.g., 0.00274 for 1 day)
        """
        expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
        now = datetime.now()

        # Market closes at 4:00 PM ET
        expiry = expiry.replace(hour=16, minute=0, second=0)

        time_diff = expiry - now
        years = time_diff.total_seconds() / (365.25 * 24 * 3600)

        return max(years, 0.0)  # Can't be negative


# ==================== CONVENIENCE FUNCTIONS ====================

def test_market_data(api_key: str = None, secret_key: str = None):
    """Test market data connection and fetch sample data"""
    print("\n" + "="*60)
    print("MARKET DATA CONNECTION TEST")
    print("="*60 + "\n")

    provider = MarketDataProvider(api_key, secret_key)

    # Test 1: Current price
    print("Test 1: Fetching current SPY price...")
    price = provider.get_spy_price()
    if price:
        print(f"✓ Current SPY Price: ${price:.2f}")
    else:
        print("✗ Failed to fetch price")

    # Test 2: ATM strike
    print("\nTest 2: Finding ATM strike...")
    strike = provider.get_atm_strike()
    if strike:
        print(f"✓ ATM Strike: ${strike:.0f}")
    else:
        print("✗ Failed to find ATM strike")

    # Test 3: Historical data
    print("\nTest 3: Fetching historical daily data...")
    end = datetime.now()
    start = end - timedelta(days=7)
    bars = provider.get_historical_bars("SPY", start, end, "1Day")
    if not bars.empty:
        print(f"✓ Fetched {len(bars)} minute bars")
        print(f"  Latest bar: {bars.iloc[-1]['timestamp']} - ${bars.iloc[-1]['close']:.2f}")
    else:
        print("✗ Failed to fetch historical data")

    # Test 4: Option chain
    print("\nTest 4: Fetching option chain...")
    expiry = provider.get_0dte_expiry()
    print(f"  Using expiry: {expiry}")
    calls, puts = provider.get_option_chain("SPY", expiry)
    if not calls.empty:
        print(f"✓ Fetched {len(calls)} calls and {len(puts)} puts")

        # Show ATM options
        atm_call = calls[calls['strike'] == strike]
        atm_put = puts[puts['strike'] == strike]

        if not atm_call.empty:
            print(f"\n  ATM Call (${strike}):")
            print(f"    Last Price: ${atm_call.iloc[0]['lastPrice']:.2f}")
            print(f"    Bid: ${atm_call.iloc[0]['bid']:.2f}")
            print(f"    Ask: ${atm_call.iloc[0]['ask']:.2f}")
            print(f"    IV: {atm_call.iloc[0]['impliedVolatility']:.1%}")

        if not atm_put.empty:
            print(f"\n  ATM Put (${strike}):")
            print(f"    Last Price: ${atm_put.iloc[0]['lastPrice']:.2f}")
            print(f"    Bid: ${atm_put.iloc[0]['bid']:.2f}")
            print(f"    Ask: ${atm_put.iloc[0]['ask']:.2f}")
            print(f"    IV: {atm_put.iloc[0]['impliedVolatility']:.1%}")
    else:
        print("✗ Failed to fetch option chain")

    # Test 5: Risk-free rate
    print("\nTest 5: Fetching risk-free rate...")
    rate = provider.get_risk_free_rate()
    print(f"✓ Risk-Free Rate: {rate:.2%}")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60 + "\n")


if __name__ == "__main__":
    # Test with no credentials (uses yfinance)
    test_market_data()
