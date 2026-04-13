"""
Conditional Gamma Scalping Backtest
====================================
Only enter long gamma when the HAR-RV model forecasts
realized vol > implied vol (i.e., when options are "cheap").

Compares:
1. UNCONDITIONAL: enter every day (the naive approach — confirmed -EV)
2. CONDITIONAL: enter only on days the model says to trade
3. INVERTED: enter only on days the model says NOT to trade (sanity check)

If the model has real signal, #2 should beat #1, and #3 should be worst.
"""

import sys
import os
import json
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator
from grid_search.vol_forecast import VolForecaster
from backtest.data_engine import DataEngine
from datetime import datetime, timedelta
import numpy as np
import pandas as pd


def flush(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def run_filtered(trading_days_all, trade_dates_set, config, label, quiet=False):
    """
    Run gamma scalping only on specified dates.

    Args:
        trading_days_all: all trading days (for expiry calculation)
        trade_dates_set: set of dates to actually enter trades
        config: StrategyConfig
        label: name for this run
    """
    data_engine = DataEngine(symbol='SPY', config=config.to_engine_config())
    dte = config.dte
    results = []
    skipped = 0
    filtered_out = 0

    for i, entry_date in enumerate(trading_days_all):
        expiry_idx = i + dte
        if expiry_idx >= len(trading_days_all):
            break

        # Check if this date passes the filter
        entry_key = entry_date.date() if hasattr(entry_date, 'date') else entry_date
        if entry_key not in trade_dates_set:
            filtered_out += 1
            continue

        expiry_date = trading_days_all[expiry_idx]
        hold_days = trading_days_all[i:expiry_idx + 1]

        if not quiet and len(results) % 10 == 0 and len(results) > 0:
            flush(f'    [{label}] Trade {len(results)+1}, '
                  f'entry {entry_date.date() if hasattr(entry_date, "date") else entry_date}...')

        try:
            orch = GridOrchestrator(config)
            result = orch._run_multi_day_trade(entry_date, expiry_date, hold_days)
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception:
            skipped += 1

    if not results:
        return None

    pnls = [r['daily_pnl'] for r in results]
    costs = [r['transaction_costs'] for r in results]
    hedges = [r['hedge_count'] for r in results]
    std = np.std(pnls) if len(pnls) > 1 else 1.0

    return {
        'label': label,
        'config_name': config.name,
        'total_trades': len(results),
        'filtered_out': filtered_out,
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


def print_result(m):
    if m is None:
        flush('    NO RESULTS')
        return
    tag = '+' if m['total_pnl'] > 0 else '-'
    flush(f'    {tag} Total P&L:     ${m["total_pnl"]:>10.2f}')
    flush(f'    {tag} Trades:        {m["total_trades"]:>10} '
          f'(filtered out: {m["filtered_out"]}, skipped: {m["skipped"]})')
    flush(f'    {tag} Avg P&L:       ${m["avg_pnl"]:>10.2f}')
    flush(f'    {tag} Win Rate:      {m["win_rate"]:>9.1%}')
    flush(f'    {tag} Sharpe:        {m["sharpe"]:>10.2f}')
    flush(f'    {tag} Profit Factor: {m["profit_factor"]:>10.2f}')
    flush(f'    {tag} Avg Hedges:    {m["avg_hedges"]:>10.1f}')
    flush(f'    {tag} Best Trade:    ${m["best_trade"]:>10.2f}')
    flush(f'    {tag} Worst Trade:   ${m["worst_trade"]:>10.2f}')


def main():
    start = '2024-03-01'
    end = '2024-12-31'

    flush(f'\n{"="*70}')
    flush(f'CONDITIONAL GAMMA SCALPING (HAR-RV VOL FORECAST)')
    flush(f'{"="*70}')
    flush(f'Period: {start} to {end}')
    flush(f'Strategy: 3-DTE ATM straddle, delta-hedge')
    flush(f'{"="*70}\n')

    # ---------------------------------------------------------------
    # STEP 1: Build vol forecast signals
    # ---------------------------------------------------------------
    flush('[1/4] Building HAR-RV volatility forecast model...')
    forecaster = VolForecaster(symbol='SPY', forecast_horizon=3)
    forecaster.fetch_data(end_date='2025-01-01')
    forecaster.compute_realized_vol()

    flush('  Generating trade signals...')
    signals = forecaster.generate_signals(start, end)

    total_days = len(signals)
    trade_signals = signals[signals['should_trade']]
    no_trade_signals = signals[~signals['should_trade']]

    flush(f'\n  Signal Summary:')
    flush(f'    Total trading days:  {total_days}')
    flush(f'    TRADE signals:       {len(trade_signals)} ({len(trade_signals)/total_days:.1%})')
    strong = (signals['confidence'] == 'STRONG').sum()
    moderate = (signals['confidence'] == 'MODERATE').sum()
    flush(f'      STRONG:            {strong}')
    flush(f'      MODERATE:          {moderate}')
    flush(f'    NO-TRADE signals:    {len(no_trade_signals)} ({len(no_trade_signals)/total_days:.1%})')

    if not trade_signals.empty:
        flush(f'\n    On TRADE days:')
        flush(f'      Avg forecast RV:   {trade_signals["forecast_rv"].mean():.1f}%')
        flush(f'      Avg current IV:    {trade_signals["current_iv"].mean():.1f}%')
        flush(f'      Avg edge (RV-IV):  {trade_signals["rv_minus_iv"].mean():+.1f} vol pts')

    if not no_trade_signals.empty:
        flush(f'    On NO-TRADE days:')
        flush(f'      Avg forecast RV:   {no_trade_signals["forecast_rv"].mean():.1f}%')
        flush(f'      Avg current IV:    {no_trade_signals["current_iv"].mean():.1f}%')
        flush(f'      Avg edge (RV-IV):  {no_trade_signals["rv_minus_iv"].mean():+.1f} vol pts')

    # Build date sets
    trade_dates = set()
    no_trade_dates = set()
    all_dates_set = set()

    for _, row in signals.iterrows():
        d = pd.Timestamp(row['date']).date()
        all_dates_set.add(d)
        if row['should_trade']:
            trade_dates.add(d)
        else:
            no_trade_dates.add(d)

    # ---------------------------------------------------------------
    # STEP 2: Get trading days from Alpaca
    # ---------------------------------------------------------------
    flush(f'\n[2/4] Fetching trading days from Alpaca...')
    dummy_config = StrategyConfig(dte=3, hedge_delta_threshold=5)
    data_engine = DataEngine(symbol='SPY', config=dummy_config.to_engine_config())
    trading_days = data_engine.get_0dte_trading_days(start, end)

    # Convert trading_days to date objects for matching
    trading_day_dates = [d.date() for d in trading_days]

    # ---------------------------------------------------------------
    # STEP 3: Run backtests with multiple configs
    # ---------------------------------------------------------------
    configs = [
        StrategyConfig(dte=3, hedge_delta_threshold=5),
        StrategyConfig(dte=3, hedge_delta_threshold=10),
        StrategyConfig(dte=3, hedge_delta_threshold=3),
        StrategyConfig(dte=3, hedge_delta_threshold=5, structure='strangle', strangle_width=5),
    ]

    all_results = []

    for cfg_idx, config in enumerate(configs):
        flush(f'\n[3/4] Config {cfg_idx+1}/{len(configs)}: {config.name}')
        flush(f'{"="*60}')

        # A) UNCONDITIONAL: every day
        flush(f'\n  (A) UNCONDITIONAL (every day)')
        uncond_result = run_filtered(
            trading_days, all_dates_set, config,
            f'UNCOND_{config.name}', quiet=False
        )
        print_result(uncond_result)

        # B) CONDITIONAL: only trade days (forecast RV > IV)
        flush(f'\n  (B) CONDITIONAL (forecast RV > IV)')
        cond_result = run_filtered(
            trading_days, trade_dates, config,
            f'COND_{config.name}', quiet=False
        )
        print_result(cond_result)

        # C) INVERTED: only no-trade days (sanity check — should be worse)
        flush(f'\n  (C) INVERTED (forecast RV < IV — should be worst)')
        inv_result = run_filtered(
            trading_days, no_trade_dates, config,
            f'INV_{config.name}', quiet=False
        )
        print_result(inv_result)

        # Compare
        flush(f'\n  --- Comparison for {config.name} ---')
        for label, res in [('UNCONDITIONAL', uncond_result),
                           ('CONDITIONAL', cond_result),
                           ('INVERTED', inv_result)]:
            if res:
                tag = '***' if res['total_pnl'] > 0 else '   '
                flush(f'  {label:<15} | {res["total_trades"]:>4} trades | '
                      f'${res["total_pnl"]:>10.2f} total | '
                      f'${res["avg_pnl"]:>8.2f} avg | '
                      f'{res["win_rate"]:>5.1%} win | '
                      f'Sharpe {res["sharpe"]:>6.2f} | '
                      f'PF {res["profit_factor"]:>5.2f} {tag}')

        if uncond_result and cond_result:
            all_results.append({
                'config': config.name,
                'uncond': uncond_result,
                'cond': cond_result,
                'inv': inv_result,
            })

    # ---------------------------------------------------------------
    # STEP 4: Final summary
    # ---------------------------------------------------------------
    flush(f'\n\n{"="*100}')
    flush(f'FINAL SUMMARY: DOES THE VOL FORECAST ADD EDGE?')
    flush(f'{"="*100}')

    flush(f'\n{"Config":<45} {"Mode":<15} {"Trades":>7} {"Total P&L":>12} '
          f'{"Avg P&L":>10} {"Win%":>7} {"Sharpe":>8} {"PF":>7}')
    flush('-' * 115)

    for combo in all_results:
        for mode, res in [('UNCOND', combo['uncond']),
                          ('COND', combo['cond']),
                          ('INVERTED', combo['inv'])]:
            if res:
                tag = ' ***' if res['total_pnl'] > 0 else ''
                flush(f'{combo["config"]:<45} {mode:<15} {res["total_trades"]:>7} '
                      f'${res["total_pnl"]:>11.2f} ${res["avg_pnl"]:>9.2f} '
                      f'{res["win_rate"]:>6.1%} {res["sharpe"]:>8.2f} '
                      f'{res["profit_factor"]:>7.2f}{tag}')
        flush('')

    # Verdict
    flush(f'\n{"="*70}')
    flush('VERDICT:')
    for combo in all_results:
        u = combo['uncond']
        c = combo['cond']
        v = combo.get('inv')
        if u and c:
            improvement = c['avg_pnl'] - u['avg_pnl']
            flush(f'\n  {combo["config"]}:')
            flush(f'    Unconditional avg P&L:  ${u["avg_pnl"]:.2f} '
                  f'({u["total_trades"]} trades)')
            flush(f'    Conditional avg P&L:    ${c["avg_pnl"]:.2f} '
                  f'({c["total_trades"]} trades)')
            if v:
                flush(f'    Inverted avg P&L:       ${v["avg_pnl"]:.2f} '
                      f'({v["total_trades"]} trades)')
            flush(f'    Improvement:            ${improvement:+.2f}/trade')
            if c['total_pnl'] > 0 and u['total_pnl'] <= 0:
                flush(f'    *** MODEL FLIPPED SIGN: unprofitable -> PROFITABLE ***')
            elif c['avg_pnl'] > u['avg_pnl']:
                flush(f'    Model improved avg P&L by ${improvement:.2f}/trade')
            else:
                flush(f'    Model did NOT improve results')

    flush(f'\n{"="*70}')

    # Save
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'grid_results')
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Save signals
    signals_path = os.path.join(output_dir, f'vol_signals_{ts}.csv')
    signals.to_csv(signals_path, index=False)
    flush(f'\nSignals saved: {signals_path}')

    # Save results
    results_path = os.path.join(output_dir, f'conditional_backtest_{ts}.json')
    serializable = []
    for combo in all_results:
        entry = {'config': combo['config']}
        for key in ['uncond', 'cond', 'inv']:
            r = combo.get(key)
            if r:
                entry[key] = {k: v for k, v in r.items() if k != 'pnls'}
                entry[key]['pnl_series'] = r.get('pnls', [])
        serializable.append(entry)

    with open(results_path, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    flush(f'Results saved: {results_path}')
    flush('\nDone.')


if __name__ == '__main__':
    main()
