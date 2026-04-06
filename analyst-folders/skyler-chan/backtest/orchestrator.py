"""
Backtest Orchestrator - Main Loop
Coordinates all components and runs full backtest
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
import sys
sys.path.append('..')

from backtest.data_engine import DataEngine
from backtest.trading_engine import TradingEngine
from utils.greeks import GreeksCalculator


class BacktestOrchestrator:
    """
    Main backtest coordinator

    Runs day-by-day, minute-by-minute simulation of gamma scalping strategy
    """

    def __init__(self, config: Dict):
        self.config = config
        self.symbol = config.get('symbol', 'SPY')
        self.data_engine = DataEngine(symbol=self.symbol, config=config)
        self.results = []

    def run_backtest(self, start_date: str, end_date: str) -> Dict:
        """
        Run full backtest over date range

        Args:
            start_date: Start date 'YYYY-MM-DD'
            end_date: End date 'YYYY-MM-DD'

        Returns:
            Dictionary with results and metrics
        """
        print("\n" + "="*70)
        print("ZERO-DTE GAMMA SCALPING BACKTEST")
        print("="*70)
        print(f"Period: {start_date} to {end_date}")
        print(f"Underlying: {self.symbol}")
        print(f"Strategy: Long ATM Straddle + Delta-Neutral Hedging")
        print(f"Delta Threshold: ±{self.config.get('delta_threshold', 0.15)}")
        print("="*70 + "\n")

        # Get trading days
        trading_days = self.data_engine.get_0dte_trading_days(start_date, end_date)

        if len(trading_days) == 0:
            raise ValueError("No trading days found in range")

        print(f"Running backtest on {len(trading_days)} days...\n")

        # Run day-by-day
        successful_days = 0
        failed_days = 0

        for i, date in enumerate(trading_days, 1):
            print(f"\n[{i}/{len(trading_days)}] {date.strftime('%Y-%m-%d')}...")

            try:
                day_result = self.run_single_day(date)
                if day_result:
                    self.results.append(day_result)
                    successful_days += 1
                    print(f"  ✓ Daily P&L: ${day_result['daily_pnl']:.2f}")
                else:
                    failed_days += 1
                    print(f"  ✗ Skipped (data issues)")

            except Exception as e:
                print(f"  ✗ Error: {e}")
                failed_days += 1
                continue

        # Calculate summary metrics
        print("\n" + "="*70)
        print("BACKTEST COMPLETE")
        print("="*70)
        print(f"Successful days: {successful_days}")
        print(f"Failed days: {failed_days}")

        if successful_days > 0:
            metrics = self.calculate_metrics()
            return {
                'config': self.config,
                'trading_days': successful_days,
                'results': self.results,
                'metrics': metrics
            }
        else:
            return None

    def run_single_day(self, date: datetime) -> Optional[Dict]:
        """
        Run strategy for one trading day

        Returns:
            Dictionary with day results, or None if day should be skipped
        """
        # Fetch data
        try:
            data = self.data_engine.fetch_day_data(date)
        except Exception as e:
            print(f"  Data fetch failed: {e}")
            return None

        # Check data quality
        if not data['validation']['valid']:
            print(f"  Data validation failed: {data['validation']['severity']}")
            if data['validation']['severity'] == 'critical':
                return None  # Skip this day

        # Check if we have enough option data
        if len(data['call_bars']) < 50 or len(data['put_bars']) < 50:
            print(f"  Insufficient option data (call:{len(data['call_bars'])}, put:{len(data['put_bars'])})")
            return None

        # Initialize trading engine
        engine = TradingEngine(self.config)

        # Merge data (align timestamps)
        merged = self._merge_data(data)

        if merged is None or len(merged) < 100:
            print(f"  Insufficient merged data")
            return None

        # Simulate trading
        try:
            return self._simulate_day(engine, merged, data)
        except Exception as e:
            print(f"  Simulation error: {e}")
            return None

    def _merge_data(self, data: Dict) -> Optional[pd.DataFrame]:
        """Merge stock and option data on timestamp"""
        try:
            stock = data['stock_bars'].copy()
            stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low', 'stock_close', 'stock_volume']

            call = data['call_bars'].copy()
            if len(call) > 0:
                call.columns = ['timestamp', 'call_open', 'call_high', 'call_low', 'call_close',
                               'call_volume', 'call_trade_count', 'call_vwap']
            else:
                return None

            put = data['put_bars'].copy()
            if len(put) > 0:
                put.columns = ['timestamp', 'put_open', 'put_high', 'put_low', 'put_close',
                              'put_volume', 'put_trade_count', 'put_vwap']
            else:
                return None

            # Merge on timestamp
            merged = stock.merge(call, on='timestamp', how='left')
            merged = merged.merge(put, on='timestamp', how='left')

            # Forward-fill missing option prices
            merged['call_close'] = merged['call_close'].ffill()
            merged['put_close'] = merged['put_close'].ffill()

            # Drop rows with NaN (beginning of day)
            merged = merged.dropna()

            return merged

        except Exception as e:
            print(f"  Merge error: {e}")
            return None

    def _simulate_day(self, engine: TradingEngine, data: pd.DataFrame, day_data: Dict) -> Dict:
        """Simulate trading for one day"""
        date = day_data['date']
        strike = day_data['atm_strike']
        risk_free_rate = day_data['risk_free_rate']

        # Entry: First row after 9:30
        entry_row = data.iloc[0]
        entry_time = entry_row['timestamp']

        call_entry = entry_row['call_close']
        put_entry = entry_row['put_close']
        call_volume = entry_row.get('call_volume', 100)

        engine.enter_straddle(
            call_entry, put_entry, strike, entry_time,
            day_data['call_symbol'], day_data['put_symbol'],
            volume=call_volume
        )

        hedge_count = 0

        # Main loop: Monitor and hedge
        for idx, row in data.iterrows():
            timestamp = row['timestamp']
            stock_price = row['stock_close']
            call_price = row['call_close']
            put_price = row['put_close']

            # Update prices
            engine.update_option_prices(call_price, put_price)

            # Calculate time to expiry (in years)
            market_close = date.replace(hour=16, minute=0)
            time_left = (market_close - timestamp).total_seconds() / (365.25 * 24 * 3600)
            time_left = max(time_left, 1e-6)  # Avoid zero

            # Estimate IV (simple approach: use initial straddle price)
            iv = 0.25  # Simplified: assume 25% IV

            # Calculate Greeks
            greeks = engine.calculate_portfolio_greeks(stock_price, time_left, risk_free_rate, iv)

            # Check if hedge needed
            hedge_size = engine.calculate_hedge_size(greeks['delta'])

            if hedge_size != 0:
                stock_volume = row.get('stock_volume', 10000)
                engine.execute_hedge(hedge_size, stock_price, timestamp, volume=stock_volume)
                hedge_count += 1

        # Exit: Last row (before 4pm)
        exit_row = data.iloc[-1]
        exit_time = exit_row['timestamp']
        exit_call_volume = exit_row.get('call_volume', 100)

        engine.close_all_positions(
            exit_row['call_close'],
            exit_row['put_close'],
            exit_row['stock_close'],
            exit_time,
            volume=exit_call_volume
        )

        # Calculate final P&L
        final_pnl = engine.pnl['realized']
        total_costs = engine.total_transaction_costs

        return {
            'date': date,
            'daily_pnl': final_pnl,
            'daily_pnl_net': final_pnl,  # Already includes costs
            'transaction_costs': total_costs,
            'hedge_count': hedge_count,
            'entry_call': call_entry,
            'entry_put': put_entry,
            'exit_call': exit_row['call_close'],
            'exit_put': exit_row['put_close'],
            'trade_log': engine.trade_log
        }

    def calculate_metrics(self) -> Dict:
        """Calculate performance metrics"""
        if len(self.results) == 0:
            return {}

        pnls = [r['daily_pnl'] for r in self.results]

        metrics = {
            'total_days': len(pnls),
            'total_pnl': sum(pnls),
            'avg_daily_pnl': np.mean(pnls),
            'median_daily_pnl': np.median(pnls),
            'std_daily_pnl': np.std(pnls),
            'best_day': max(pnls),
            'worst_day': min(pnls),
            'winning_days': sum(1 for p in pnls if p > 0),
            'losing_days': sum(1 for p in pnls if p < 0),
            'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
            'sharpe_ratio': (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if np.std(pnls) > 0 else 0,
        }

        return metrics


# Main function for testing
def main():
    """Test backtest on small date range"""
    config = {
        'contracts_per_straddle': 1,
        'delta_threshold': 0.15,
        'max_stock_position': 500,
        'max_daily_loss': 2000,
        'profit_target': 1500
    }

    orchestrator = BacktestOrchestrator(config)

    # Test on March 2024 (1 month)
    results = orchestrator.run_backtest("2024-03-01", "2024-03-31")

    if results:
        print("\n" + "="*70)
        print("RESULTS")
        print("="*70)
        metrics = results['metrics']
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"{key}: {value:.2f}")
            else:
                print(f"{key}: {value}")


if __name__ == "__main__":
    main()
