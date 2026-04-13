"""
0-DTE Edge Finder
Exhaustive search for a profitable zero-DTE gamma scalping configuration.
Tests extreme parameters the standard grid didn't cover.
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator


def build_0dte_extreme_grid():
    """
    Test extreme and creative 0-DTE configurations:
    - Very high delta thresholds (20-50): barely hedge, let gamma run
    - Late entry via fixed_time exit combos (enter at open, exit after 2hr = morning scalp)
    - Tight P&L stops that cut losers fast
    - Strangles that are cheap to enter (less theta to overcome)
    """
    configs = []

    # --- AXIS 1: Very high delta thresholds (almost no hedging) ---
    for dt in [20, 25, 30, 50, 100]:
        configs.append(StrategyConfig(
            hedge_delta_threshold=dt,
            exit_strategy="eod",
        ))

    # --- AXIS 2: Morning-only scalps (enter at open, exit after N minutes) ---
    # Hypothesis: morning volatility is highest, capture gamma before theta kicks in
    for minutes in [30, 60, 90, 120]:
        for dt in [5, 15, 30]:
            configs.append(StrategyConfig(
                hedge_delta_threshold=dt,
                exit_strategy="fixed_time",
                exit_time_minutes=minutes,
            ))

    # --- AXIS 3: Tight asymmetric stops (small loss, let winners run to EOD) ---
    # Only stop loss, no take profit (set TP very high)
    for sl in [-50, -100, -150]:
        for dt in [5, 15, 30]:
            configs.append(StrategyConfig(
                hedge_delta_threshold=dt,
                exit_strategy="pnl_stop",
                stop_loss=sl,
                take_profit=5000,  # effectively no TP
            ))

    # --- AXIS 4: Cheap strangles with high delta threshold ---
    # Cheap entry = less theta to overcome
    for width in [5, 10, 15]:
        for dt in [15, 30, 50]:
            configs.append(StrategyConfig(
                structure="strangle",
                strangle_width=width,
                hedge_delta_threshold=dt,
                exit_strategy="eod",
            ))

    # --- AXIS 5: Straddle + wide wings + high delta threshold ---
    # Collect wing premium to offset theta, barely hedge
    for dt in [15, 30, 50]:
        for ww in [10, 20]:
            configs.append(StrategyConfig(
                hedge_delta_threshold=dt,
                wings=True,
                wing_width=ww,
                exit_strategy="eod",
            ))

    # --- AXIS 6: Morning strangle scalps ---
    for width in [5, 10]:
        for minutes in [60, 120]:
            configs.append(StrategyConfig(
                structure="strangle",
                strangle_width=width,
                hedge_delta_threshold=15,
                exit_strategy="fixed_time",
                exit_time_minutes=minutes,
            ))

    return configs


def run_0dte_search(start_date: str, end_date: str, period_name: str = ""):
    """Run the full 0-DTE edge search on a given period"""
    configs = build_0dte_extreme_grid()

    print(f"\n{'='*70}")
    print(f"0-DTE EDGE FINDER {period_name}")
    print(f"{'='*70}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Configurations: {len(configs)}")
    print(f"{'='*70}\n")

    all_results = {}
    profitable = []

    for i, cfg in enumerate(configs):
        print(f"[{i+1}/{len(configs)}] {cfg.name}...", end=" ", flush=True)

        try:
            orchestrator = GridOrchestrator(cfg)
            result = orchestrator.run(start_date, end_date, quiet=True)

            if result and result.get('metrics'):
                m = result['metrics']
                pnl = m.get('total_pnl', 0)
                sharpe = m.get('sharpe', -999)
                wr = m.get('win_rate', 0)
                trades = m.get('total_trades', 0)

                status = "+" if pnl > 0 else "-"
                print(f"{status} P&L=${pnl:>8.2f} Sharpe={sharpe:>6.2f} WR={wr:.0%} "
                      f"Trades={trades} Hedges={m.get('avg_hedges', 0):.0f}")

                all_results[cfg.name] = result
                if pnl > 0:
                    profitable.append((cfg.name, m))
            else:
                print("NO DATA")
        except Exception as e:
            print(f"ERROR: {e}")

    # Report
    print(f"\n{'='*70}")
    print(f"RESULTS: {period_name}")
    print(f"{'='*70}")
    print(f"Total configs tested: {len(configs)}")
    print(f"Profitable configs:   {len(profitable)}")

    if profitable:
        print(f"\n--- PROFITABLE 0-DTE STRATEGIES ---")
        profitable.sort(key=lambda x: x[1].get('sharpe', 0), reverse=True)

        print(f"{'Strategy':<55} {'P&L':>10} {'Sharpe':>8} {'Win%':>7} {'PF':>7} {'Trades':>7}")
        print("-" * 100)
        for name, m in profitable:
            print(f"{name:<55} ${m['total_pnl']:>9.2f} {m['sharpe']:>8.2f} "
                  f"{m['win_rate']:>6.1%} {m['profit_factor']:>7.2f} {m['total_trades']:>7}")
    else:
        print("\nNo profitable configurations found in this period.")

    # Also show top 10 by Sharpe regardless
    print(f"\n--- TOP 10 BY SHARPE (all) ---")
    sorted_all = sorted(
        [(n, r['metrics']) for n, r in all_results.items() if r.get('metrics')],
        key=lambda x: x[1].get('sharpe', -999), reverse=True
    )
    print(f"{'Strategy':<55} {'P&L':>10} {'Sharpe':>8} {'Win%':>7} {'AvgPnL':>10}")
    print("-" * 95)
    for name, m in sorted_all[:10]:
        print(f"{name:<55} ${m['total_pnl']:>9.2f} {m['sharpe']:>8.2f} "
              f"{m['win_rate']:>6.1%} ${m['avg_pnl']:>9.2f}")

    # Save results
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'grid_results')
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(output_dir, f"0dte_edge_{period_name.replace(' ','_')}_{ts}.json")

    serializable = {}
    for name, result in all_results.items():
        if result:
            serializable[name] = {
                'config_name': result.get('config_name', name),
                'config': result.get('config', {}),
                'metrics': result.get('metrics', {}),
            }
    with open(filepath, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\nSaved: {filepath}")

    return all_results, profitable


if __name__ == '__main__':
    # Test across multiple market regimes
    periods = [
        ("2024-06-01", "2024-08-31", "summer2024"),      # original period (low vol, trending)
        ("2024-08-01", "2024-10-31", "fall2024"),         # includes Aug vol spike
        ("2024-01-01", "2024-03-31", "q1_2024"),          # different regime
        ("2024-03-01", "2024-05-31", "spring2024"),       # yet another regime
    ]

    all_profitable = {}

    for start, end, name in periods:
        results, profitable = run_0dte_search(start, end, name)
        for pname, metrics in profitable:
            if pname not in all_profitable:
                all_profitable[pname] = []
            all_profitable[pname].append((name, metrics))

    # Strategies profitable in multiple periods
    print(f"\n\n{'='*70}")
    print("STRATEGIES PROFITABLE ACROSS MULTIPLE PERIODS")
    print(f"{'='*70}")

    multi_period = {k: v for k, v in all_profitable.items() if len(v) >= 2}
    if multi_period:
        for name, periods_data in sorted(multi_period.items(),
                                          key=lambda x: len(x[1]), reverse=True):
            print(f"\n{name} - profitable in {len(periods_data)} periods:")
            for period_name, m in periods_data:
                print(f"  {period_name}: P&L=${m['total_pnl']:.2f} "
                      f"Sharpe={m['sharpe']:.2f} WR={m['win_rate']:.0%}")
    else:
        print("No strategy was profitable in 2+ periods.")

    # Best single-period performers
    print(f"\n{'='*70}")
    print("BEST SINGLE-PERIOD PERFORMERS")
    print(f"{'='*70}")
    for name, periods_data in sorted(all_profitable.items(),
                                      key=lambda x: max(m['sharpe'] for _, m in x[1]),
                                      reverse=True)[:10]:
        best = max(periods_data, key=lambda x: x[1]['sharpe'])
        print(f"{name}: {best[0]} Sharpe={best[1]['sharpe']:.2f} "
              f"P&L=${best[1]['total_pnl']:.2f} WR={best[1]['win_rate']:.0%}")
