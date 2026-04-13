"""
Grid Search Runner
Main entry point for running parameter sweeps.

Usage:
    python run_grid.py --sweep delta --start 2024-03-01 --end 2024-06-30
    python run_grid.py --sweep structure --start 2024-03-01 --end 2024-06-30
    python run_grid.py --sweep all    # runs all focused sweeps sequentially
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import GRID_PRESETS, StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator


def run_sweep(sweep_name: str, start_date: str, end_date: str,
              quiet: bool = False) -> dict:
    """Run a named sweep and return all results"""

    if sweep_name not in GRID_PRESETS:
        print(f"Unknown sweep: {sweep_name}")
        print(f"Available: {list(GRID_PRESETS.keys())}")
        return {}

    configs = GRID_PRESETS[sweep_name]()
    print(f"\n{'='*70}")
    print(f"GRID SEARCH: {sweep_name.upper()} SWEEP")
    print(f"{'='*70}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Configurations to test: {len(configs)}")
    print(f"{'='*70}\n")

    all_results = {}

    for i, cfg in enumerate(configs):
        print(f"\n[{i+1}/{len(configs)}] Testing: {cfg.name}")
        print("-" * 50)

        orchestrator = GridOrchestrator(cfg)
        result = orchestrator.run(start_date, end_date, quiet=quiet)

        if result and result.get('metrics'):
            m = result['metrics']
            print(f"  Total P&L:  ${m['total_pnl']:>10.2f}")
            print(f"  Avg P&L:    ${m['avg_pnl']:>10.2f}")
            print(f"  Win Rate:   {m['win_rate']:>10.1%}")
            print(f"  Sharpe:     {m['sharpe']:>10.2f}")
            print(f"  Trades:     {m['total_trades']:>10}")
        else:
            print("  No results (data issues)")

        all_results[cfg.name] = result

    return all_results


def print_comparison_table(all_results: dict):
    """Print a formatted comparison table of all strategies"""
    print(f"\n{'='*120}")
    print("STRATEGY COMPARISON")
    print(f"{'='*120}")

    header = f"{'Strategy':<45} {'Total P&L':>10} {'Avg P&L':>10} {'Win%':>7} {'Sharpe':>8} {'PF':>7} {'Trades':>7} {'AvgHedge':>9} {'AvgCost':>9}"
    print(header)
    print("-" * 120)

    # Sort by Sharpe ratio descending
    sorted_results = sorted(
        all_results.items(),
        key=lambda x: x[1].get('metrics', {}).get('sharpe', -999),
        reverse=True
    )

    for name, result in sorted_results:
        m = result.get('metrics', {})
        if not m:
            print(f"{name:<45} {'NO DATA':>10}")
            continue

        print(f"{name:<45} "
              f"${m['total_pnl']:>9.2f} "
              f"${m['avg_pnl']:>9.2f} "
              f"{m['win_rate']:>6.1%} "
              f"{m['sharpe']:>8.2f} "
              f"{m['profit_factor']:>7.2f} "
              f"{m['total_trades']:>7} "
              f"{m['avg_hedges']:>9.1f} "
              f"${m['avg_transaction_costs']:>8.2f}")

    print(f"{'='*120}")

    # Highlight best
    if sorted_results and sorted_results[0][1].get('metrics'):
        best = sorted_results[0]
        print(f"\nBest strategy (by Sharpe): {best[0]}")
        m = best[1]['metrics']
        print(f"  Sharpe: {m['sharpe']:.2f} | Win Rate: {m['win_rate']:.1%} | "
              f"Avg P&L: ${m['avg_pnl']:.2f} | Total P&L: ${m['total_pnl']:.2f}")


def save_results(all_results: dict, sweep_name: str):
    """Save results to JSON"""
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'grid_results')
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"grid_{sweep_name}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    # Make JSON-serializable
    serializable = {}
    for name, result in all_results.items():
        if result:
            s = {
                'config_name': result.get('config_name', name),
                'config': result.get('config', {}),
                'metrics': result.get('metrics', {}),
                'daily_results': [
                    {k: str(v) if isinstance(v, datetime) else v
                     for k, v in day.items() if k != 'trade_log'}
                    for day in result.get('daily_results', [])
                ],
            }
            serializable[name] = s

    with open(filepath, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)

    print(f"\nResults saved to: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description='SPY Gamma Scalping Grid Search')
    parser.add_argument('--sweep', type=str, default='delta',
                        choices=list(GRID_PRESETS.keys()) + ['all'],
                        help='Which parameter sweep to run')
    parser.add_argument('--start', type=str, default='2024-06-01',
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default='2024-08-31',
                        help='End date (YYYY-MM-DD)')
    parser.add_argument('--quiet', action='store_true',
                        help='Reduce output verbosity')
    args = parser.parse_args()

    print(f"\nSPY GAMMA SCALPING GRID SEARCH")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.sweep == 'all':
        # Run all focused sweeps (skip 'full' - too many combos)
        for sweep_name in ['delta', 'structure', 'wings', 'exit']:
            all_results = run_sweep(sweep_name, args.start, args.end, args.quiet)
            if all_results:
                print_comparison_table(all_results)
                save_results(all_results, sweep_name)
    else:
        all_results = run_sweep(args.sweep, args.start, args.end, args.quiet)
        if all_results:
            print_comparison_table(all_results)
            save_results(all_results, args.sweep)


if __name__ == '__main__':
    main()
