"""
Main Entry Point for Backtesting System
Run this to execute the backtest
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.orchestrator import BacktestOrchestrator
from datetime import datetime


def main():
    """Run backtest"""

    # Configuration
    config = {
        'contracts_per_straddle': 1,
        'delta_threshold': 0.15,
        'max_stock_position': 500,
        'max_daily_loss': 2000,
        'profit_target': 1500
    }

    print("\n" + "="*70)
    print("ZERO-DTE GAMMA SCALPING BACKTEST SYSTEM")
    print("="*70)
    print(f"Author: Skyler Chan")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    # Create orchestrator
    orchestrator = BacktestOrchestrator(config)

    # Test on March 2024 (small sample first)
    print("Running test backtest on March 2024...")
    results = orchestrator.run_backtest("2024-03-01", "2024-03-31")

    if results:
        print("\n" + "="*70)
        print("PERFORMANCE METRICS")
        print("="*70)

        metrics = results['metrics']
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"{key:.<30} {value:>15.2f}")
            else:
                print(f"{key:.<30} {value:>15}")

        print("\n" + "="*70)
        print("BACKTEST COMPLETE")
        print("="*70)

        return results
    else:
        print("\n✗ Backtest failed - no results generated")
        return None


if __name__ == "__main__":
    main()
