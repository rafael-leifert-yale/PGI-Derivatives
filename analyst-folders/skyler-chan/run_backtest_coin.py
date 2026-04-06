"""
COIN Zero-DTE Gamma Scalping Backtest
Same strategy as SPY backtest, applied to Coinbase (COIN)
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.orchestrator import BacktestOrchestrator


def main():
    """Run COIN backtest"""

    config = {
        'symbol': 'COIN',
        'contracts_per_straddle': 1,
        'delta_threshold': 0.15,
        'max_stock_position': 500,
        'max_daily_loss': 2000,
        'profit_target': 1500,
        # COIN has wider spreads than SPY
        'stock_spread': 0.05,
        'option_spread_pct': 0.025,
    }

    print("\n" + "="*70)
    print("ZERO-DTE GAMMA SCALPING BACKTEST - COIN")
    print("="*70)
    print(f"Author: Skyler Chan")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    orchestrator = BacktestOrchestrator(config)

    # Full period: March 2024 - April 2026
    results = orchestrator.run_backtest("2024-03-01", "2026-04-05")

    if results:
        print("\n" + "="*70)
        print("PERFORMANCE METRICS - COIN")
        print("="*70)

        metrics = results['metrics']
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"{key:.<30} {value:>15.2f}")
            else:
                print(f"{key:.<30} {value:>15}")

        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"backtest_results_COIN_{timestamp}.json"
        )

        serializable = {
            'config': config,
            'trading_days': results['trading_days'],
            'metrics': metrics,
            'daily_results': []
        }

        for r in results['results']:
            serializable['daily_results'].append({
                'date': r['date'].strftime('%Y-%m-%d'),
                'daily_pnl': r['daily_pnl'],
                'transaction_costs': r['transaction_costs'],
                'hedge_count': r['hedge_count'],
                'entry_call': r['entry_call'],
                'entry_put': r['entry_put'],
                'exit_call': r['exit_call'],
                'exit_put': r['exit_put']
            })

        with open(output_file, 'w') as f:
            json.dump(serializable, f, indent=2)

        print(f"\nResults saved to: {output_file}")
        print("\n" + "="*70)
        print("BACKTEST COMPLETE")
        print("="*70)

        return results
    else:
        print("\nBacktest failed - no results generated")
        return None


if __name__ == "__main__":
    main()
