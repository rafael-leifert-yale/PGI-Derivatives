"""
Ultra-Short 0-DTE Morning Scalp Finder
Tests very short hold periods (5-45 min) to capture opening volatility burst
while minimizing theta exposure.
"""

import sys
import os
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator


def flush_print(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def build_ultra_short_configs():
    configs = []
    # Ultra-short morning scalps: 5-45 min hold
    for minutes in [5, 10, 15, 20, 25, 30, 45]:
        for dt in [10, 15, 25, 50, 100]:
            configs.append(StrategyConfig(
                hedge_delta_threshold=dt,
                exit_strategy='fixed_time',
                exit_time_minutes=minutes,
            ))

    # Straddle + wings(20) with short holds
    for minutes in [10, 15, 20, 30, 45]:
        configs.append(StrategyConfig(
            hedge_delta_threshold=50,
            wings=True, wing_width=20,
            exit_strategy='fixed_time',
            exit_time_minutes=minutes,
        ))

    # Strangle(5) ultra-short (cheap entry)
    for minutes in [10, 15, 20, 30]:
        for dt in [15, 50]:
            configs.append(StrategyConfig(
                structure='strangle', strangle_width=5,
                hedge_delta_threshold=dt,
                exit_strategy='fixed_time',
                exit_time_minutes=minutes,
            ))
    return configs


def main():
    configs = build_ultra_short_configs()
    flush_print(f'Testing {len(configs)} ultra-short hold configs...\n')

    periods = [
        ('Summer24', '2024-06-01', '2024-08-31'),
        ('Q1_2024', '2024-01-01', '2024-03-31'),
    ]

    all_profitable = {}

    for period_name, start, end in periods:
        flush_print(f'\n{"="*70}')
        flush_print(f'{period_name}: {start} to {end}')
        flush_print(f'{"="*70}')

        results = []
        for i, cfg in enumerate(configs):
            try:
                orch = GridOrchestrator(cfg)
                r = orch.run(start, end, quiet=True)
                if r and r.get('metrics'):
                    m = r['metrics']
                    tag = '+' if m['total_pnl'] > 0 else '-'
                    results.append((cfg.name, m))
                    flush_print(f'  [{i+1}/{len(configs)}] {cfg.name}: {tag} ${m["total_pnl"]:.2f} Sharpe={m["sharpe"]:.2f} WR={m["win_rate"]:.0%}')
            except Exception as e:
                flush_print(f'  [{i+1}/{len(configs)}] {cfg.name}: ERROR {e}')

        # Sort by Sharpe
        results.sort(key=lambda x: x[1].get('sharpe', -999), reverse=True)

        profitable = [r for r in results if r[1]['total_pnl'] > 0]
        flush_print(f'\nProfitable: {len(profitable)}/{len(results)}')

        for name, m in profitable:
            if name not in all_profitable:
                all_profitable[name] = []
            all_profitable[name].append((period_name, m))

        flush_print(f'\n--- TOP 15 by Sharpe ({period_name}) ---')
        flush_print(f'{"Strategy":<55} {"P&L":>10} {"Sharpe":>8} {"Win%":>7} {"Trades":>7} {"Hedges":>8}')
        flush_print('-' * 95)
        for name, m in results[:15]:
            tag = ' ***' if m['total_pnl'] > 0 else ''
            flush_print(f'{name:<50} ${m["total_pnl"]:>9.2f} {m["sharpe"]:>8.2f} {m["win_rate"]:>6.1%} {m["total_trades"]:>7} {m["avg_hedges"]:>8.1f}{tag}')

    # Cross-period winners
    flush_print(f'\n\n{"="*70}')
    flush_print('STRATEGIES PROFITABLE IN BOTH PERIODS')
    flush_print(f'{"="*70}')

    multi = {k: v for k, v in all_profitable.items() if len(v) >= 2}
    if multi:
        for name, pd_list in sorted(multi.items(), key=lambda x: sum(m['total_pnl'] for _, m in x[1]), reverse=True):
            flush_print(f'\n{name}:')
            total = 0
            for pn, m in pd_list:
                flush_print(f'  {pn}: P&L=${m["total_pnl"]:.2f} Sharpe={m["sharpe"]:.2f} WR={m["win_rate"]:.0%}')
                total += m['total_pnl']
            flush_print(f'  COMBINED P&L: ${total:.2f}')
    else:
        flush_print('None found.')

    flush_print('\nDone.')


if __name__ == '__main__':
    main()
