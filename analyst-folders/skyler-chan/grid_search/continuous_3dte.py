"""
Continuous 3-DTE Gamma Scalping
Enter a NEW trade every single trading day (overlapping positions).
Test with ultra-tight delta thresholds (1, 2, 3 shares).
No cherry-picking - always in the market.
"""

import sys
import os
import json
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator
from grid_search.enhanced_engine import EnhancedTradingEngine
from backtest.data_engine import DataEngine
from datetime import datetime, timedelta
import numpy as np


def flush(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def run_continuous(start_date, end_date, config, quiet=False):
    """
    Enter a new 3-DTE trade EVERY trading day.
    Each trade is independent - run in parallel, aggregate results.
    """
    data_engine = DataEngine(symbol='SPY', config=config.to_engine_config())
    trading_days = data_engine.get_0dte_trading_days(start_date, end_date)

    dte = config.dte
    results = []
    skipped = 0

    for i, entry_date in enumerate(trading_days):
        # Find the expiry date (dte trading days later)
        expiry_idx = i + dte
        if expiry_idx >= len(trading_days):
            break

        expiry_date = trading_days[expiry_idx]
        hold_days = trading_days[i:expiry_idx + 1]

        if not quiet and i % 20 == 0:
            flush(f'  Trade {i+1}, entry {entry_date.date()}, '
                  f'expiry {expiry_date.date()}...')

        try:
            orch = GridOrchestrator(config)
            result = orch._run_multi_day_trade(entry_date, expiry_date, hold_days)
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as e:
            skipped += 1

    if not results:
        return None

    pnls = [r['daily_pnl'] for r in results]
    costs = [r['transaction_costs'] for r in results]
    hedges = [r['hedge_count'] for r in results]
    std = np.std(pnls) if len(pnls) > 1 else 1.0

    return {
        'config_name': config.name,
        'total_trades': len(results),
        'skipped': skipped,
        'total_pnl': sum(pnls),
        'avg_pnl': np.mean(pnls),
        'median_pnl': np.median(pnls),
        'std_pnl': std,
        'best_trade': max(pnls),
        'worst_trade': min(pnls),
        'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
        'sharpe': (np.mean(pnls) / std) * np.sqrt(252) if std > 0 else 0,
        'profit_factor': (
            sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0))
            if sum(p for p in pnls if p < 0) != 0 else float('inf')
        ),
        'avg_hedges': np.mean(hedges),
        'avg_costs': np.mean(costs),
        'total_costs': sum(costs),
        'pnls': pnls,
    }


def main():
    start = '2024-03-01'
    end = '2024-12-31'  # 10 months for real sample size

    flush(f'\n{"="*70}')
    flush(f'CONTINUOUS 3-DTE GAMMA SCALPING')
    flush(f'{"="*70}')
    flush(f'Period: {start} to {end}')
    flush(f'Entry: EVERY trading day (overlapping positions)')
    flush(f'Hold: 3 trading days, exit EOD on expiry')
    flush(f'{"="*70}\n')

    # Configs to test: ultra-tight to loose delta thresholds
    # Plus the winning structures from prior tests
    configs = [
        # Ultra-tight scalping (your idea)
        StrategyConfig(dte=3, hedge_delta_threshold=1),   # hedge on every 1-share move
        StrategyConfig(dte=3, hedge_delta_threshold=2),   # hedge on every 2-share move
        StrategyConfig(dte=3, hedge_delta_threshold=3),   # hedge on every 3-share move

        # Moderate
        StrategyConfig(dte=3, hedge_delta_threshold=5),
        StrategyConfig(dte=3, hedge_delta_threshold=10),
        StrategyConfig(dte=3, hedge_delta_threshold=15),

        # Winner from prior test: straddle + wings(20)
        StrategyConfig(dte=3, hedge_delta_threshold=1, wings=True, wing_width=20),
        StrategyConfig(dte=3, hedge_delta_threshold=2, wings=True, wing_width=20),
        StrategyConfig(dte=3, hedge_delta_threshold=3, wings=True, wing_width=20),
        StrategyConfig(dte=3, hedge_delta_threshold=5, wings=True, wing_width=20),

        # Strangle variations with tight scalping
        StrategyConfig(dte=3, hedge_delta_threshold=1, structure='strangle', strangle_width=5),
        StrategyConfig(dte=3, hedge_delta_threshold=2, structure='strangle', strangle_width=5),
        StrategyConfig(dte=3, hedge_delta_threshold=3, structure='strangle', strangle_width=5),
        StrategyConfig(dte=3, hedge_delta_threshold=5, structure='strangle', strangle_width=5),
    ]

    flush(f'Configs to test: {len(configs)}')
    flush(f'Each enters EVERY trading day - no gaps, no cherry-picking\n')

    all_results = []

    for i, cfg in enumerate(configs):
        flush(f'\n[{i+1}/{len(configs)}] {cfg.name}')
        flush(f'  Delta threshold: {cfg.hedge_delta_threshold} shares '
              f'(hedge when delta moves {cfg.hedge_delta_threshold}+ shares)')
        flush(f'  {"="*50}')

        result = run_continuous(start, end, cfg, quiet=False)

        if result:
            m = result
            tag = '+' if m['total_pnl'] > 0 else '-'
            flush(f'\n  {tag} Total P&L:    ${m["total_pnl"]:>10.2f}')
            flush(f'  {tag} Avg P&L:      ${m["avg_pnl"]:>10.2f}')
            flush(f'  {tag} Trades:       {m["total_trades"]:>10}')
            flush(f'  {tag} Win Rate:     {m["win_rate"]:>9.1%}')
            flush(f'  {tag} Sharpe:       {m["sharpe"]:>10.2f}')
            flush(f'  {tag} Profit Factor:{m["profit_factor"]:>10.2f}')
            flush(f'  {tag} Avg Hedges:   {m["avg_hedges"]:>10.1f}')
            flush(f'  {tag} Avg Cost:     ${m["avg_costs"]:>9.2f}')
            flush(f'  {tag} Best Trade:   ${m["best_trade"]:>10.2f}')
            flush(f'  {tag} Worst Trade:  ${m["worst_trade"]:>10.2f}')
            flush(f'  {tag} Skipped:      {m["skipped"]:>10}')
            all_results.append(result)
        else:
            flush(f'  NO RESULTS')

    # Final comparison table
    flush(f'\n\n{"="*130}')
    flush(f'FINAL RANKINGS - CONTINUOUS 3-DTE (enter every day, {start} to {end})')
    flush(f'{"="*130}')

    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    flush(f'\n{"Rank":<5} {"Strategy":<50} {"Total P&L":>11} {"Avg P&L":>10} '
          f'{"Sharpe":>8} {"Win%":>7} {"PF":>7} {"Trades":>7} '
          f'{"AvgHedge":>9} {"AvgCost":>9} {"Best":>10} {"Worst":>10}')
    flush('-' * 150)

    for i, m in enumerate(all_results, 1):
        tag = '***' if m['total_pnl'] > 0 else '   '
        flush(f'{i:<5} {m["config_name"]:<50} '
              f'${m["total_pnl"]:>10.2f} ${m["avg_pnl"]:>9.2f} '
              f'{m["sharpe"]:>8.2f} {m["win_rate"]:>6.1%} '
              f'{m["profit_factor"]:>7.2f} {m["total_trades"]:>7} '
              f'{m["avg_hedges"]:>9.1f} ${m["avg_costs"]:>8.2f} '
              f'${m["best_trade"]:>9.2f} ${m["worst_trade"]:>9.2f} {tag}')

    # Summary stats
    profitable = [m for m in all_results if m['total_pnl'] > 0]
    flush(f'\nProfitable: {len(profitable)}/{len(all_results)}')

    if profitable:
        flush(f'\nBest strategy:')
        best = profitable[0]
        flush(f'  {best["config_name"]}')
        flush(f'  ${best["total_pnl"]:.2f} total over {best["total_trades"]} trades')
        flush(f'  ${best["avg_pnl"]:.2f} avg per trade')
        flush(f'  {best["win_rate"]:.1%} win rate, Sharpe {best["sharpe"]:.2f}')
        flush(f'  {best["avg_hedges"]:.0f} hedges/trade, ${best["avg_costs"]:.2f} cost/trade')

    # Save
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'grid_results')
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(output_dir, f'continuous_3dte_{ts}.json')

    serializable = []
    for m in all_results:
        s = {k: v for k, v in m.items() if k != 'pnls'}
        s['pnl_series'] = m.get('pnls', [])
        serializable.append(s)
    with open(filepath, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    flush(f'\nSaved: {filepath}')
    flush('Done.')


if __name__ == '__main__':
    main()
