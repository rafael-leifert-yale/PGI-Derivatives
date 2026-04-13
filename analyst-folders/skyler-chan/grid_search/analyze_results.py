"""
Results Analyzer
Load saved grid search results and produce comparison tables, heatmaps,
and identify optimal parameter combinations.

Usage:
    python analyze_results.py grid_results/grid_delta_*.json grid_results/grid_structure_*.json
    python analyze_results.py --all   # analyze all results in grid_results/
"""

import sys
import os
import json
import glob
import argparse
from typing import List, Dict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def load_results(filepaths: List[str]) -> Dict[str, dict]:
    """Load and merge results from multiple JSON files"""
    all_results = {}
    for fp in filepaths:
        with open(fp, 'r') as f:
            data = json.load(f)
        for name, result in data.items():
            all_results[name] = result
    return all_results


def rank_strategies(results: Dict[str, dict], sort_by: str = 'sharpe') -> List[tuple]:
    """Rank all strategies by a given metric"""
    ranked = []
    for name, result in results.items():
        m = result.get('metrics', {})
        if m:
            ranked.append((name, m))

    ranked.sort(key=lambda x: x[1].get(sort_by, -999), reverse=True)
    return ranked


def print_leaderboard(results: Dict[str, dict]):
    """Print full leaderboard across all sweeps"""
    print(f"\n{'='*130}")
    print("OVERALL LEADERBOARD (all sweeps combined)")
    print(f"{'='*130}")

    for metric, label in [('sharpe', 'Sharpe'), ('total_pnl', 'Total P&L'),
                           ('win_rate', 'Win Rate'), ('profit_factor', 'Profit Factor')]:
        ranked = rank_strategies(results, metric)
        print(f"\n--- Top 5 by {label} ---")
        print(f"{'Rank':<5} {'Strategy':<50} {label:>12} {'Sharpe':>8} {'Win%':>7} {'Total P&L':>12}")
        print("-" * 100)
        for i, (name, m) in enumerate(ranked[:5], 1):
            val = m.get(metric, 0)
            if metric in ('win_rate',):
                val_str = f"{val:.1%}"
            elif metric in ('sharpe', 'profit_factor'):
                val_str = f"{val:.2f}"
            else:
                val_str = f"${val:.2f}"
            print(f"{i:<5} {name:<50} {val_str:>12} "
                  f"{m.get('sharpe', 0):>8.2f} "
                  f"{m.get('win_rate', 0):>6.1%} "
                  f"${m.get('total_pnl', 0):>11.2f}")

    print(f"\n{'='*130}")


def extract_parameter_impact(results: Dict[str, dict]):
    """Analyze impact of each parameter dimension"""
    print(f"\n{'='*80}")
    print("PARAMETER IMPACT ANALYSIS")
    print(f"{'='*80}")

    # Group by delta threshold
    delta_groups = {}
    for name, result in results.items():
        m = result.get('metrics', {})
        cfg = result.get('config', {})
        if not m:
            continue

        dt = cfg.get('delta_threshold', 0.15)
        dt_shares = int(dt * 100)
        if dt_shares not in delta_groups:
            delta_groups[dt_shares] = []
        delta_groups[dt_shares].append(m)

    if delta_groups:
        print(f"\n--- Delta Threshold Impact ---")
        print(f"{'Threshold':>10} {'Avg Sharpe':>12} {'Avg Win%':>10} {'Avg P&L':>12} {'N':>5}")
        print("-" * 55)
        for dt in sorted(delta_groups.keys()):
            metrics_list = delta_groups[dt]
            avg_sharpe = sum(m['sharpe'] for m in metrics_list) / len(metrics_list)
            avg_wr = sum(m['win_rate'] for m in metrics_list) / len(metrics_list)
            avg_pnl = sum(m['avg_pnl'] for m in metrics_list) / len(metrics_list)
            print(f"{dt:>10} {avg_sharpe:>12.2f} {avg_wr:>9.1%} ${avg_pnl:>11.2f} {len(metrics_list):>5}")

    # Group by structure
    struct_groups = {}
    for name, result in results.items():
        m = result.get('metrics', {})
        cfg = result.get('config', {})
        if not m:
            continue
        struct = cfg.get('structure', 'straddle')
        sw = cfg.get('strangle_width', 0)
        key = f"{struct}(w={sw})" if struct == 'strangle' else 'straddle'
        if key not in struct_groups:
            struct_groups[key] = []
        struct_groups[key].append(m)

    if struct_groups:
        print(f"\n--- Structure Impact ---")
        print(f"{'Structure':>20} {'Avg Sharpe':>12} {'Avg Win%':>10} {'Avg P&L':>12} {'N':>5}")
        print("-" * 65)
        for struct in sorted(struct_groups.keys()):
            metrics_list = struct_groups[struct]
            avg_sharpe = sum(m['sharpe'] for m in metrics_list) / len(metrics_list)
            avg_wr = sum(m['win_rate'] for m in metrics_list) / len(metrics_list)
            avg_pnl = sum(m['avg_pnl'] for m in metrics_list) / len(metrics_list)
            print(f"{struct:>20} {avg_sharpe:>12.2f} {avg_wr:>9.1%} ${avg_pnl:>11.2f} {len(metrics_list):>5}")

    print(f"\n{'='*80}")


def generate_report(results: Dict[str, dict], output_path: str):
    """Generate a markdown report of all results"""
    ranked = rank_strategies(results, 'sharpe')

    lines = [
        f"# SPY Gamma Scalping Grid Search Results",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total strategies tested: {len(ranked)}",
        "",
        "## Top 10 Strategies (by Sharpe Ratio)",
        "",
        "| Rank | Strategy | Sharpe | Win Rate | Avg P&L | Total P&L | Profit Factor | Avg Hedges |",
        "|------|----------|--------|----------|---------|-----------|---------------|------------|",
    ]

    for i, (name, m) in enumerate(ranked[:10], 1):
        lines.append(
            f"| {i} | {name} | {m.get('sharpe', 0):.2f} | "
            f"{m.get('win_rate', 0):.1%} | ${m.get('avg_pnl', 0):.2f} | "
            f"${m.get('total_pnl', 0):.2f} | {m.get('profit_factor', 0):.2f} | "
            f"{m.get('avg_hedges', 0):.1f} |"
        )

    lines.extend([
        "",
        "## Bottom 5 Strategies (worst Sharpe)",
        "",
        "| Rank | Strategy | Sharpe | Win Rate | Avg P&L | Total P&L |",
        "|------|----------|--------|----------|---------|-----------|",
    ])

    for i, (name, m) in enumerate(ranked[-5:], len(ranked) - 4):
        lines.append(
            f"| {i} | {name} | {m.get('sharpe', 0):.2f} | "
            f"{m.get('win_rate', 0):.1%} | ${m.get('avg_pnl', 0):.2f} | "
            f"${m.get('total_pnl', 0):.2f} |"
        )

    lines.extend([
        "",
        "## All Strategies (sorted by Sharpe)",
        "",
        "| Strategy | Sharpe | Win% | Avg P&L | Total P&L | PF | Hedges | Costs |",
        "|----------|--------|------|---------|-----------|-----|--------|-------|",
    ])

    for name, m in ranked:
        lines.append(
            f"| {name} | {m.get('sharpe', 0):.2f} | "
            f"{m.get('win_rate', 0):.1%} | ${m.get('avg_pnl', 0):.2f} | "
            f"${m.get('total_pnl', 0):.2f} | {m.get('profit_factor', 0):.2f} | "
            f"{m.get('avg_hedges', 0):.1f} | ${m.get('avg_transaction_costs', 0):.2f} |"
        )

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))

    print(f"\nReport saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Analyze grid search results')
    parser.add_argument('files', nargs='*', help='Result JSON files to analyze')
    parser.add_argument('--all', action='store_true',
                        help='Analyze all files in grid_results/')
    parser.add_argument('--report', type=str, default=None,
                        help='Output markdown report path')
    args = parser.parse_args()

    results_dir = os.path.join(os.path.dirname(__file__), '..', 'grid_results')

    if args.all:
        files = sorted(glob.glob(os.path.join(results_dir, 'grid_*.json')))
    else:
        files = args.files

    if not files:
        print("No result files found. Run grid search first:")
        print("  python run_grid.py --sweep delta --start 2024-06-01 --end 2024-08-31")
        return

    print(f"Loading {len(files)} result file(s)...")
    results = load_results(files)
    print(f"Total strategies: {len(results)}")

    print_leaderboard(results)
    extract_parameter_impact(results)

    report_path = args.report or os.path.join(results_dir, 'GRID_SEARCH_REPORT.md')
    generate_report(results, report_path)


if __name__ == '__main__':
    main()
