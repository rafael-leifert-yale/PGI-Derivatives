"""
Data Engine - Historical Data Fetching and Validation
Fetches SPY and option data from Alpaca for backtesting
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from typing import List, Dict, Tuple, Optional
import yfinance as yf
import warnings

# Alpaca imports
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionBarsRequest
from alpaca.data.timeframe import TimeFrame

# Credentials
ALPACA_API_KEY = "PKUFIUPLC47J5MOFKETQIW6QVC"
ALPACA_SECRET_KEY = "48UHojTJrYvsPfhtxXNkwYYnqoDWX7nLT3t2EiR3JYua"


class DataEngine:
    """
    Fetches and validates historical market data for backtesting

    Responsibilities:
    - Identify 0-DTE trading days
    - Fetch SPY minute bars
    - Construct option symbols
    - Fetch option minute bars
    - Validate data quality
    - Handle missing data
    """

    def __init__(self):
        """Initialize Alpaca clients"""
        try:
            self.stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            self.option_client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            print("✓ Alpaca clients initialized")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Alpaca clients: {e}")

    def get_0dte_trading_days(self, start_date: str, end_date: str) -> List[datetime]:
        """
        Identify all 0-DTE option expiry days in date range

        SPY 0-DTE History:
        - Before Dec 2022: Only Fridays
        - Dec 2022 - May 2023: Monday, Wednesday, Friday
        - After May 2023: Every trading day (Mon-Fri)

        Args:
            start_date: Start date in 'YYYY-MM-DD' format
            end_date: End date in 'YYYY-MM-DD' format

        Returns:
            List of datetime objects for 0-DTE trading days
        """
        print(f"\nIdentifying 0-DTE trading days from {start_date} to {end_date}...")

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # For historical dates (before today), assume every weekday is a 0-DTE day
        # This is accurate for dates after May 2023 when SPY went to daily expiries
        # We'll handle holidays/market closures when we try to fetch data
        trading_days = []
        current = start_dt

        while current <= end_dt:
            # Skip weekends (Monday=0, Friday=4, Saturday=5, Sunday=6)
            if current.weekday() < 5:
                trading_days.append(current)

            current += timedelta(days=1)

        print(f"✓ Found {len(trading_days)} 0-DTE trading days")

        if len(trading_days) == 0:
            warnings.warn("No 0-DTE trading days found in range!")

        return trading_days

    def _is_market_open(self, date: datetime) -> bool:
        """
        Check if market was open on given date

        Uses SPY data availability as proxy for market open
        """
        try:
            # Try to fetch one bar for that day
            start = date.replace(hour=9, minute=30)
            end = date.replace(hour=16, minute=0)

            request = StockBarsRequest(
                symbol_or_symbols="SPY",
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=1
            )

            bars = self.stock_client.get_stock_bars(request)
            return bars.df is not None and not bars.df.empty

        except:
            return False

    def fetch_spy_bars(self, date: datetime) -> pd.DataFrame:
        """
        Fetch SPY minute bars for a trading day

        Args:
            date: Trading date

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume

        Expected: ~390 bars (9:30 AM - 4:00 PM)
        """
        start = date.replace(hour=9, minute=30, second=0, microsecond=0)
        end = date.replace(hour=16, minute=0, second=0, microsecond=0)

        try:
            request = StockBarsRequest(
                symbol_or_symbols="SPY",
                timeframe=TimeFrame.Minute,
                start=start,
                end=end
            )

            bars = self.stock_client.get_stock_bars(request)
            df = bars.df.reset_index()

            # Clean column names
            df.columns = [col.lower() for col in df.columns]

            # Select relevant columns
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()

            # Remove timezone for consistency
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)

            return df

        except Exception as e:
            raise RuntimeError(f"Error fetching SPY bars for {date.date()}: {e}")

    def construct_option_symbol(self, root: str, expiry: datetime,
                                strike: float, option_type: str) -> str:
        """
        Construct OCC option symbol format

        Format: ROOT + YYMMDD + (C/P) + STRIKE*1000 (8 digits)
        Example: SPY240315C00520000 = SPY Call, March 15 2024, $520 strike

        Args:
            root: Underlying symbol (e.g., "SPY")
            expiry: Expiration date
            strike: Strike price (e.g., 520.0)
            option_type: 'call' or 'put'

        Returns:
            OCC symbol string
        """
        expiry_code = expiry.strftime("%y%m%d")  # YYMMDD
        side = 'C' if option_type.lower() == 'call' else 'P'
        strike_code = f"{int(strike * 1000):08d}"  # 8 digits, padded

        symbol = f"{root}{expiry_code}{side}{strike_code}"
        return symbol

    def fetch_option_bars(self, symbol: str, date: datetime) -> pd.DataFrame:
        """
        Fetch option minute bars for a trading day

        Args:
            symbol: OCC option symbol (e.g., "SPY240315C00520000")
            date: Trading date

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, trade_count, vwap

        Note: Options may have fewer bars than SPY due to lower liquidity
        """
        start = date.replace(hour=9, minute=30, second=0, microsecond=0)
        end = date.replace(hour=16, minute=0, second=0, microsecond=0)

        try:
            request = OptionBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end
            )

            bars = self.option_client.get_option_bars(request)

            if bars.df is None or bars.df.empty:
                warnings.warn(f"No data for option {symbol} on {date.date()}")
                return pd.DataFrame()

            df = bars.df.reset_index()

            # Clean column names
            df.columns = [col.lower() for col in df.columns]

            # Select relevant columns
            expected_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trade_count', 'vwap']
            available_cols = [col for col in expected_cols if col in df.columns]
            df = df[available_cols].copy()

            # Remove timezone
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)

            return df

        except Exception as e:
            warnings.warn(f"Error fetching option bars for {symbol}: {e}")
            return pd.DataFrame()

    def find_atm_strike(self, spy_price: float) -> float:
        """
        Find ATM strike nearest to current SPY price

        SPY options have $1 strikes for near-the-money

        Args:
            spy_price: Current SPY price

        Returns:
            ATM strike price (rounded to nearest dollar)
        """
        return round(spy_price)

    def get_risk_free_rate(self, date: datetime) -> float:
        """
        Get risk-free rate for a specific date

        Uses 3-month T-Bill rate (^IRX) as proxy

        Args:
            date: Date to fetch rate for

        Returns:
            Annual risk-free rate as decimal (e.g., 0.045 for 4.5%)
        """
        try:
            # Fetch ^IRX (13-week T-Bill)
            ticker = yf.Ticker("^IRX")

            # Get data around the target date
            start = date - timedelta(days=7)
            end = date + timedelta(days=1)

            data = ticker.history(start=start, end=end)

            if not data.empty:
                # Use closest available rate
                rate = data['Close'].iloc[-1] / 100  # Convert from percent to decimal
                return rate
            else:
                warnings.warn(f"No ^IRX data for {date.date()}, using default 4.5%")
                return 0.045

        except Exception as e:
            warnings.warn(f"Error fetching risk-free rate: {e}. Using default 4.5%")
            return 0.045

    def validate_data(self, spy_bars: pd.DataFrame, call_bars: pd.DataFrame,
                     put_bars: pd.DataFrame, date: datetime) -> Dict[str, any]:
        """
        Comprehensive data quality validation

        Args:
            spy_bars: SPY minute bars
            call_bars: Call option minute bars
            put_bars: Put option minute bars
            date: Trading date

        Returns:
            Dictionary with:
                - valid: bool (True if data passes all checks)
                - issues: List[str] (list of issues found)
                - severity: 'none' | 'minor' | 'major' | 'critical'
        """
        issues = []
        severity = 'none'

        # 1. Completeness checks
        expected_spy_bars = 390  # 9:30 AM - 4:00 PM
        if len(spy_bars) < 380:
            issues.append(f"Missing SPY bars: {len(spy_bars)}/390")
            severity = 'minor' if len(spy_bars) > 350 else 'major'

        if len(call_bars) < 300:
            issues.append(f"Low call bar count: {len(call_bars)}")
            severity = max(severity, 'minor')

        if len(put_bars) < 300:
            issues.append(f"Low put bar count: {len(put_bars)}")
            severity = max(severity, 'minor')

        # 2. Price sanity checks
        if not spy_bars.empty:
            spy_close = spy_bars['close']
            spy_range = spy_close.max() - spy_close.min()
            spy_pct_range = spy_range / spy_close.mean()

            if spy_pct_range > 0.10:  # >10% intraday move
                issues.append(f"Large SPY move: {spy_pct_range:.1%}")
                severity = max(severity, 'minor')  # May be valid (volatile day)

            # Check for zero or negative prices
            if (spy_close <= 0).any():
                issues.append("Invalid SPY prices (<=0)")
                severity = 'critical'

        # 3. Option price sanity
        if not call_bars.empty:
            call_close = call_bars['close']
            if (call_close < 0.01).any():
                issues.append("Very low call prices (<$0.01)")
                severity = max(severity, 'minor')

            if (call_close > spy_bars['close'].mean() * 0.3).any():
                issues.append("Suspiciously high call prices (>30% of SPY)")
                severity = max(severity, 'major')

        # 4. Timestamp validation
        if not spy_bars.empty:
            spy_times = spy_bars['timestamp']
            if not spy_times.is_monotonic_increasing:
                issues.append("Non-monotonic timestamps in SPY data")
                severity = 'critical'

        # 5. Volume/liquidity checks
        if not call_bars.empty and 'volume' in call_bars.columns:
            avg_call_volume = call_bars['volume'].mean()
            if avg_call_volume < 5:
                issues.append(f"Very low call volume: {avg_call_volume:.1f}/min")
                severity = max(severity, 'minor')

        # Determine if data is valid
        valid = severity not in ['critical', 'major']

        return {
            'valid': valid,
            'issues': issues,
            'severity': severity,
            'spy_bars': len(spy_bars),
            'call_bars': len(call_bars),
            'put_bars': len(put_bars)
        }

    def fetch_day_data(self, date: datetime) -> Dict[str, any]:
        """
        Fetch all data needed for one trading day

        Args:
            date: Trading date (must be a valid 0-DTE expiry)

        Returns:
            Dictionary with:
                - date: datetime
                - spy_bars: DataFrame
                - call_symbol: str
                - put_symbol: str
                - call_bars: DataFrame
                - put_bars: DataFrame
                - atm_strike: float
                - risk_free_rate: float
                - validation: Dict (from validate_data)
        """
        print(f"\nFetching data for {date.strftime('%Y-%m-%d')}...")

        try:
            # 1. Fetch SPY bars
            spy_bars = self.fetch_spy_bars(date)
            print(f"  SPY bars: {len(spy_bars)}")

            if spy_bars.empty:
                raise ValueError("No SPY data available")

            # 2. Determine ATM strike (use 9:31 AM price)
            open_price = spy_bars.iloc[1]['close'] if len(spy_bars) > 1 else spy_bars.iloc[0]['close']
            atm_strike = self.find_atm_strike(open_price)
            print(f"  SPY price at open: ${open_price:.2f}, ATM strike: ${atm_strike}")

            # 3. Construct option symbols
            call_symbol = self.construct_option_symbol("SPY", date, atm_strike, "call")
            put_symbol = self.construct_option_symbol("SPY", date, atm_strike, "put")
            print(f"  Call: {call_symbol}")
            print(f"  Put: {put_symbol}")

            # 4. Fetch option bars
            call_bars = self.fetch_option_bars(call_symbol, date)
            put_bars = self.fetch_option_bars(put_symbol, date)
            print(f"  Call bars: {len(call_bars)}, Put bars: {len(put_bars)}")

            # 5. Get risk-free rate
            risk_free_rate = self.get_risk_free_rate(date)
            print(f"  Risk-free rate: {risk_free_rate:.2%}")

            # 6. Validate data
            validation = self.validate_data(spy_bars, call_bars, put_bars, date)
            print(f"  Validation: {validation['severity'].upper()} - {len(validation['issues'])} issues")

            if not validation['valid']:
                print(f"    Issues: {validation['issues']}")

            return {
                'date': date,
                'spy_bars': spy_bars,
                'call_symbol': call_symbol,
                'put_symbol': put_symbol,
                'call_bars': call_bars,
                'put_bars': put_bars,
                'atm_strike': atm_strike,
                'risk_free_rate': risk_free_rate,
                'validation': validation
            }

        except Exception as e:
            print(f"  ✗ Error: {e}")
            raise RuntimeError(f"Failed to fetch data for {date.date()}: {e}")


# Testing function
def test_data_engine():
    """Test data engine with a known date"""
    print("="*70)
    print("DATA ENGINE TEST")
    print("="*70)

    engine = DataEngine()

    # Test with March 15, 2024 (known to have data)
    test_date = datetime(2024, 3, 15)

    try:
        data = engine.fetch_day_data(test_date)

        print("\n" + "="*70)
        print("TEST PASSED")
        print("="*70)
        print(f"\nFetched data for {test_date.date()}:")
        print(f"  SPY bars: {len(data['spy_bars'])}")
        print(f"  Call bars: {len(data['call_bars'])}")
        print(f"  Put bars: {len(data['put_bars'])}")
        print(f"  ATM strike: ${data['atm_strike']}")
        print(f"  Validation: {data['validation']}")

        return True

    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        return False


if __name__ == "__main__":
    test_data_engine()
