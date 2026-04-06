"""
Zero-DTE Gamma Scalping Strategy for SPY
Author: Skyler Chan
Strategy: Trade SPY straddles with rapid delta-neutral adjustments via gamma scalping
"""

import os
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
import yfinance as yf
from typing import Dict, List, Tuple, Optional
import time

# ==================== API CONFIGURATION ====================
# TODO: Add your Alpaca API credentials here
# Get them from: https://alpaca.markets/
# Use PAPER trading keys first for testing!

ALPACA_API_KEY = "PKUFIUPLC47J5MOFKETQIW6QVC"
ALPACA_SECRET_KEY = "48UHojTJrYvsPfhtxXNkwYYnqoDWX7nLT3t2EiR3JYua"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"  # Paper trading URL

# ==================== STRATEGY PARAMETERS ====================
class StrategyConfig:
    """Configuration parameters for the gamma scalping strategy"""

    # Core parameters
    SYMBOL = "SPY"
    OPTION_UNDERLYING = "SPY"

    # Position sizing
    INITIAL_CAPITAL = 100000  # Starting capital
    CONTRACTS_PER_STRADDLE = 1  # Number of option contracts
    SHARES_PER_ADJUSTMENT = 100  # Shares to trade per delta hedge

    # Delta-neutral thresholds
    DELTA_THRESHOLD = 0.15  # Rehedge when net delta exceeds this
    GAMMA_TARGET = 0.05  # Target gamma exposure

    # Risk management
    MAX_LOSS_PER_DAY = 2000  # Max daily loss before stopping
    PROFIT_TARGET = 1500  # Daily profit target
    MAX_POSITION_SIZE = 500  # Max shares of underlying

    # Timing
    ENTRY_TIME = "09:35"  # Enter straddle after market open
    EXIT_TIME = "15:55"  # Exit before close (0-DTE expires at 4pm)
    REHEDGE_INTERVAL = 60  # Seconds between delta checks

    # Options selection
    ATM_STRIKE_RANGE = 0.5  # % range for selecting ATM options


# ==================== GREEKS CALCULATOR ====================
class GreeksCalculator:
    """Calculate option Greeks using Black-Scholes"""

    @staticmethod
    def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                     option_type: str) -> Dict[str, float]:
        """
        Calculate option price and Greeks

        Args:
            S: Current stock price
            K: Strike price
            T: Time to expiration (in years)
            r: Risk-free rate
            sigma: Implied volatility
            option_type: 'call' or 'put'

        Returns:
            Dictionary with price, delta, gamma, theta, vega
        """
        from scipy.stats import norm

        if T <= 0:
            # Option expired
            if option_type == 'call':
                return {
                    'price': max(S - K, 0),
                    'delta': 1.0 if S > K else 0.0,
                    'gamma': 0.0,
                    'theta': 0.0,
                    'vega': 0.0
                }
            else:
                return {
                    'price': max(K - S, 0),
                    'delta': -1.0 if S < K else 0.0,
                    'gamma': 0.0,
                    'theta': 0.0,
                    'vega': 0.0
                }

        # Black-Scholes formula
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if option_type == 'call':
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
        else:
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = -norm.cdf(-d1)

        # Greeks
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
        theta = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) -
                 r * K * np.exp(-r * T) * norm.cdf(d2 if option_type == 'call' else -d2))
        vega = S * norm.pdf(d1) * np.sqrt(T)

        return {
            'price': price,
            'delta': delta,
            'gamma': gamma,
            'theta': theta / 365,  # Daily theta
            'vega': vega / 100  # Vega per 1% vol change
        }

    @staticmethod
    def get_iv_from_market(option_price: float, S: float, K: float, T: float,
                          r: float, option_type: str) -> float:
        """
        Calculate implied volatility from option price using Newton-Raphson
        """
        # Initial guess
        sigma = 0.3
        max_iterations = 100
        tolerance = 0.0001

        for i in range(max_iterations):
            greeks = GreeksCalculator.black_scholes(S, K, T, r, sigma, option_type)
            price_diff = greeks['price'] - option_price

            if abs(price_diff) < tolerance:
                return sigma

            # Newton-Raphson update
            vega = greeks['vega']
            if vega == 0:
                break

            sigma -= price_diff / (vega * 100)
            sigma = max(0.01, min(sigma, 3.0))  # Keep sigma reasonable

        return sigma


# ==================== POSITION MANAGER ====================
class PositionManager:
    """Manage straddle position and delta hedging"""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.call_position = None
        self.put_position = None
        self.stock_position = 0  # Net shares of SPY for hedging
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0

    def calculate_portfolio_delta(self, spot_price: float, iv: float,
                                  time_to_expiry: float, risk_free_rate: float) -> float:
        """Calculate total portfolio delta including options and stock"""
        total_delta = 0.0

        # Add call delta
        if self.call_position:
            call_greeks = GreeksCalculator.black_scholes(
                spot_price, self.call_position['strike'], time_to_expiry,
                risk_free_rate, iv, 'call'
            )
            total_delta += call_greeks['delta'] * self.call_position['contracts'] * 100

        # Add put delta
        if self.put_position:
            put_greeks = GreeksCalculator.black_scholes(
                spot_price, self.put_position['strike'], time_to_expiry,
                risk_free_rate, iv, 'put'
            )
            total_delta += put_greeks['delta'] * self.put_position['contracts'] * 100

        # Add stock delta (1.0 per share)
        total_delta += self.stock_position

        return total_delta

    def calculate_portfolio_gamma(self, spot_price: float, iv: float,
                                  time_to_expiry: float, risk_free_rate: float) -> float:
        """Calculate total portfolio gamma"""
        total_gamma = 0.0

        if self.call_position:
            call_greeks = GreeksCalculator.black_scholes(
                spot_price, self.call_position['strike'], time_to_expiry,
                risk_free_rate, iv, 'call'
            )
            total_gamma += call_greeks['gamma'] * self.call_position['contracts'] * 100

        if self.put_position:
            put_greeks = GreeksCalculator.black_scholes(
                spot_price, self.put_position['strike'], time_to_expiry,
                risk_free_rate, iv, 'put'
            )
            total_gamma += put_greeks['gamma'] * self.put_position['contracts'] * 100

        return total_gamma

    def get_hedge_quantity(self, current_delta: float) -> int:
        """Determine how many shares to buy/sell to neutralize delta"""
        if abs(current_delta) < self.config.DELTA_THRESHOLD * 100:
            return 0  # Within threshold, no hedge needed

        # Calculate shares needed (negative delta means buy, positive means sell)
        shares_needed = -int(current_delta)

        # Limit to max position size
        if abs(self.stock_position + shares_needed) > self.config.MAX_POSITION_SIZE:
            shares_needed = np.sign(shares_needed) * (
                self.config.MAX_POSITION_SIZE - abs(self.stock_position)
            )

        return shares_needed


# ==================== GAMMA SCALPING STRATEGY ====================
class GammaScalpingStrategy:
    """Main strategy class for zero-DTE gamma scalping"""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.trading_client = None
        self.data_client = None
        self.position_manager = PositionManager(config)
        self.trade_log = []

    def initialize_api(self):
        """Initialize Alpaca API clients"""
        try:
            self.trading_client = TradingClient(
                ALPACA_API_KEY,
                ALPACA_SECRET_KEY,
                paper=True  # Use paper trading
            )
            self.data_client = StockHistoricalDataClient(
                ALPACA_API_KEY,
                ALPACA_SECRET_KEY
            )
            print("✓ Alpaca API initialized successfully")
            return True
        except Exception as e:
            print(f"✗ Error initializing Alpaca API: {e}")
            return False

    def get_current_price(self) -> Optional[float]:
        """Get current SPY price"""
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=self.config.SYMBOL)
            quote = self.data_client.get_stock_latest_quote(request)
            return quote[self.config.SYMBOL].ask_price
        except Exception as e:
            print(f"Error getting price: {e}")
            return None

    def find_atm_options(self, spot_price: float) -> Tuple[float, float]:
        """
        Find ATM call and put strikes for current SPY price

        Returns:
            Tuple of (call_strike, put_strike)
        """
        # SPY options typically have $1 or $0.50 strikes
        # Round to nearest strike
        atm_strike = round(spot_price)

        return atm_strike, atm_strike

    def get_option_chain(self, expiry_date: str) -> pd.DataFrame:
        """
        Get option chain from yfinance (Alpaca doesn't provide options data yet)

        Args:
            expiry_date: Expiration date in YYYY-MM-DD format
        """
        try:
            ticker = yf.Ticker(self.config.SYMBOL)
            options = ticker.option_chain(expiry_date)
            return options
        except Exception as e:
            print(f"Error fetching option chain: {e}")
            return None

    def enter_straddle(self, strike: float, call_price: float, put_price: float):
        """
        Enter long straddle position

        Note: This is a simulation - actual options trading through Alpaca
        requires additional setup and may not support all option types
        """
        self.position_manager.call_position = {
            'strike': strike,
            'contracts': self.config.CONTRACTS_PER_STRADDLE,
            'entry_price': call_price,
            'entry_time': datetime.now()
        }

        self.position_manager.put_position = {
            'strike': strike,
            'contracts': self.config.CONTRACTS_PER_STRADDLE,
            'entry_price': put_price,
            'entry_time': datetime.now()
        }

        cost = (call_price + put_price) * self.config.CONTRACTS_PER_STRADDLE * 100

        self.trade_log.append({
            'time': datetime.now(),
            'action': 'ENTER_STRADDLE',
            'strike': strike,
            'call_price': call_price,
            'put_price': put_price,
            'cost': cost
        })

        print(f"\n✓ Entered Straddle:")
        print(f"  Strike: ${strike}")
        print(f"  Call Price: ${call_price:.2f}")
        print(f"  Put Price: ${put_price:.2f}")
        print(f"  Total Cost: ${cost:.2f}")

    def execute_hedge_trade(self, shares: int, current_price: float):
        """
        Execute delta hedge by buying/selling SPY shares

        For simulation: tracks position
        For live trading: would execute market order through Alpaca
        """
        if shares == 0:
            return

        side = "BUY" if shares > 0 else "SELL"
        cost = shares * current_price

        # Update position
        self.position_manager.stock_position += shares
        self.position_manager.realized_pnl -= cost  # Cost of hedge

        self.trade_log.append({
            'time': datetime.now(),
            'action': 'HEDGE',
            'side': side,
            'shares': abs(shares),
            'price': current_price,
            'cost': cost
        })

        print(f"\n→ Hedge Trade:")
        print(f"  {side} {abs(shares)} shares @ ${current_price:.2f}")
        print(f"  Net Stock Position: {self.position_manager.stock_position} shares")

    def update_pnl(self, spot_price: float, call_price: float, put_price: float):
        """Calculate current P&L"""
        if not self.position_manager.call_position:
            return

        # Options P&L
        call_pnl = ((call_price - self.position_manager.call_position['entry_price']) *
                    self.config.CONTRACTS_PER_STRADDLE * 100)
        put_pnl = ((put_price - self.position_manager.put_position['entry_price']) *
                   self.config.CONTRACTS_PER_STRADDLE * 100)

        # Stock P&L (mark-to-market)
        # This is simplified - would need to track average cost per share
        stock_pnl = 0  # Realized P&L already tracked

        self.position_manager.unrealized_pnl = call_pnl + put_pnl
        total_pnl = self.position_manager.realized_pnl + self.position_manager.unrealized_pnl

        return total_pnl

    def run_simulation(self, date: str, price_data: pd.DataFrame):
        """
        Run backtest simulation for one trading day

        Args:
            date: Trading date in YYYY-MM-DD format
            price_data: DataFrame with columns ['time', 'price'] for minute-level SPY data
        """
        print(f"\n{'='*60}")
        print(f"GAMMA SCALPING SIMULATION - {date}")
        print(f"{'='*60}\n")

        # TODO: Implement full simulation logic
        # This would include:
        # 1. Get option chain at market open
        # 2. Enter straddle position
        # 3. Monitor delta throughout day
        # 4. Execute hedges when delta exceeds threshold
        # 5. Exit all positions before close
        # 6. Calculate daily P&L

        print("Simulation framework ready. Implement with real market data.")

    def run_live(self):
        """
        Run strategy in live/paper trading mode

        WARNING: This is a starting framework. Requires extensive testing
        and risk management before using with real money!
        """
        print("\n⚠️  LIVE TRADING MODE - USE WITH CAUTION ⚠️\n")

        if not self.initialize_api():
            return

        # TODO: Implement live trading logic
        # - Get today's 0-DTE expiration
        # - Fetch option chain
        # - Enter straddle at configured time
        # - Monitor and hedge continuously
        # - Exit before market close

        print("Live trading framework ready. Implement entry/exit logic.")


# ==================== MAIN ====================
def main():
    """Main entry point"""
    config = StrategyConfig()
    strategy = GammaScalpingStrategy(config)

    print("\n" + "="*60)
    print("ZERO-DTE GAMMA SCALPING STRATEGY")
    print("="*60)
    print(f"\nSymbol: {config.SYMBOL}")
    print(f"Strategy: Long Straddle + Delta-Neutral Hedging")
    print(f"Delta Threshold: ±{config.DELTA_THRESHOLD}")
    print(f"Contracts per Straddle: {config.CONTRACTS_PER_STRADDLE}")
    print(f"Initial Capital: ${config.INITIAL_CAPITAL:,.0f}")

    # Check if API keys are configured
    if ALPACA_API_KEY == "YOUR_API_KEY_HERE":
        print("\n⚠️  WARNING: Alpaca API keys not configured!")
        print("   Please add your API keys at the top of this file.")
        print("   Get keys from: https://alpaca.markets/\n")

    print("\n" + "="*60)
    print("Next Steps:")
    print("="*60)
    print("1. Add your Alpaca API keys (paper trading)")
    print("2. Implement simulation with historical data")
    print("3. Backtest strategy performance")
    print("4. Add risk management and position limits")
    print("5. Test in paper trading before going live")
    print("\n")


if __name__ == "__main__":
    main()
