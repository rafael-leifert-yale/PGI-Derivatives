"""
Call vs Put Gamma Scalping Comparison
Tests whether there's a P&L difference between gamma scalping with:
  1. Call-only (buy ATM call, hedge with stock)
  2. Put-only (buy ATM put, hedge with stock)
  3. Straddle (existing strategy - buy both)

Theory: Put-call parity implies identical gamma, so results should match.
Practice: Skew, spreads, and directional bias may cause divergence.
"""

import sys
import os
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, time as dtime
from typing import Dict, Optional

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.data_engine import DataEngine
from backtest.execution_model import ExecutionModel
from utils.greeks import GreeksCalculator


class SingleLegEngine:
    """Trading engine that supports call-only, put-only, or straddle modes."""

    def __init__(self, config: Dict, mode: str = 'straddle'):
        """
        mode: 'call', 'put', or 'straddle'
        """
        self.config = config
        self.mode = mode
        self.execution_model = ExecutionModel(config)
        self.positions = {'options': [], 'stock': {'quantity': 0, 'avg_cost': 0.0}}
        self.pnl = {'realized': 0.0, 'unrealized': 0.0}
        self.total_transaction_costs = 0.0
        self.trade_log = []

    def enter_position(self, call_price, put_price, strike, timestamp,
                       call_symbol, put_symbol, volume=100):
        """Enter position based on mode."""
        contracts = self.config.get('contracts_per_straddle', 1)
        total_cost = 0.0
        total_fees = 0.0

        if self.mode in ('call', 'straddle'):
            exec_call = self.execution_model.execute_trade(
                trade={'asset_type': 'option', 'side': 'BUY',
                       'quantity': contracts, 'mid_price': call_price},
                market_data={'volume': volume, 'current_time': timestamp.time()}
            )
            from backtest.trading_engine import Position
            self.positions['options'].append(
                Position(call_symbol, 'call', contracts, exec_call['executed_price'], timestamp)
            )
            total_cost += exec_call['total_cost']
            total_fees += exec_call['transaction_cost']

        if self.mode in ('put', 'straddle'):
            exec_put = self.execution_model.execute_trade(
                trade={'asset_type': 'option', 'side': 'BUY',
                       'quantity': contracts, 'mid_price': put_price},
                market_data={'volume': volume, 'current_time': timestamp.time()}
            )
            from backtest.trading_engine import Position
            self.positions['options'].append(
                Position(put_symbol, 'put', contracts, exec_put['executed_price'], timestamp)
            )
            total_cost += exec_put['total_cost']
            total_fees += exec_put['transaction_cost']

        self.pnl['realized'] -= total_cost
        self.total_transaction_costs += total_fees

    def calculate_portfolio_greeks(self, spot_price, time_to_expiry, risk_free_rate, iv):
        total_delta = 0.0
        total_gamma = 0.0

        for position in self.positions['options']:
            if position.quantity == 0:
                continue
            strike = float(position.symbol[-8:]) / 1000
            try:
                greeks = GreeksCalculator.black_scholes(
                    S=spot_price, K=strike, T=time_to_expiry,
                    r=risk_free_rate, sigma=iv, option_type=position.asset_type
                )
                total_delta += greeks['delta'] * 100 * position.quantity
                total_gamma += greeks['gamma'] * 100 * position.quantity
            except Exception:
                pass

        total_delta += self.positions['stock']['quantity']
        return {'delta': total_delta, 'gamma': total_gamma}

    def should_hedge(self, current_delta):
        threshold = self.config.get('delta_threshold', 0.15) * 100
        return abs(current_delta) > threshold

    def calculate_hedge_size(self, current_delta):
        if not self.should_hedge(current_delta):
            return 0
        shares = -round(current_delta)
        max_position = self.config.get('max_stock_position', 500)
        new_position = self.positions['stock']['quantity'] + shares
        if abs(new_position) > max_position:
            available = max_position - abs(self.positions['stock']['quantity'])
            shares = int(np.sign(shares) * min(abs(shares), available))
        return shares

    def execute_hedge(self, shares, spy_price, timestamp, volume=10000):
        if shares == 0:
            return None
        side = 'BUY' if shares > 0 else 'SELL'
        execution = self.execution_model.execute_trade(
            trade={'asset_type': 'stock', 'side': side,
                   'quantity': abs(shares), 'mid_price': spy_price},
            market_data={'volume': volume, 'current_time': timestamp.time()}
        )
        old_qty = self.positions['stock']['quantity']
        old_cost = self.positions['stock']['avg_cost']
        new_qty = old_qty + shares
        if new_qty != 0:
            new_avg_cost = (old_qty * old_cost + shares * execution['executed_price']) / new_qty
        else:
            new_avg_cost = 0.0
        self.positions['stock']['quantity'] = new_qty
        self.positions['stock']['avg_cost'] = new_avg_cost

        if side == 'BUY':
            self.pnl['realized'] -= execution['total_cost']
        else:
            self.pnl['realized'] += execution['total_cost']
        self.total_transaction_costs += execution['transaction_cost']
        return execution

    def update_option_prices(self, call_price, put_price):
        for position in self.positions['options']:
            if position.asset_type == 'call':
                position.current_price = call_price
            else:
                position.current_price = put_price

    def close_all_positions(self, call_price, put_price, spy_price, timestamp, volume=100):
        total_exit_costs = 0.0

        for position in self.positions['options']:
            if position.quantity > 0:
                execution = self.execution_model.execute_trade(
                    trade={'asset_type': 'option', 'side': 'SELL',
                           'quantity': position.quantity,
                           'mid_price': position.current_price},
                    market_data={'volume': volume, 'current_time': timestamp.time()}
                )
                self.pnl['realized'] += execution['total_cost']
                total_exit_costs += execution['transaction_cost']

        if self.positions['stock']['quantity'] != 0:
            stock_qty = self.positions['stock']['quantity']
            side = 'SELL' if stock_qty > 0 else 'BUY'
            execution = self.execution_model.execute_trade(
                trade={'asset_type': 'stock', 'side': side,
                       'quantity': abs(stock_qty), 'mid_price': spy_price},
                market_data={'volume': 10000, 'current_time': timestamp.time()}
            )
            if side == 'SELL':
                self.pnl['realized'] += execution['total_cost']
            else:
                self.pnl['realized'] -= execution['total_cost']
            total_exit_costs += execution['transaction_cost']

        self.total_transaction_costs += total_exit_costs
        self.positions = {'options': [], 'stock': {'quantity': 0, 'avg_cost': 0.0}}


def run_comparison(start_date: str, end_date: str):
    """Run all three modes and compare."""

    config = {
        'contracts_per_straddle': 1,
        'delta_threshold': 0.15,
        'max_stock_position': 500,
        'max_daily_loss': 2000,
        'profit_target': 1500,
    }

    data_engine = DataEngine(symbol='SPY', config=config)
    trading_days = data_engine.get_0dte_trading_days(start_date, end_date)

    print(f"\n{'='*70}")
    print("CALL vs PUT vs STRADDLE GAMMA SCALPING COMPARISON")
    print(f"{'='*70}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Trading days to test: {len(trading_days)}")
    print(f"{'='*70}\n")

    results = {'call': [], 'put': [], 'straddle': []}

    for i, date in enumerate(trading_days, 1):
        print(f"\n[{i}/{len(trading_days)}] {date.strftime('%Y-%m-%d')}...")

        # Fetch data once for all three modes
        try:
            data = data_engine.fetch_day_data(date)
        except Exception as e:
            print(f"  Data fetch failed: {e}")
            continue

        if not data['validation']['valid'] and data['validation']['severity'] == 'critical':
            print(f"  Skipped (critical data issue)")
            continue

        # Merge data
        try:
            stock = data['stock_bars'].copy()
            stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low',
                             'stock_close', 'stock_volume']
            call = data['call_bars'].copy()
            if len(call) == 0:
                print(f"  No call data")
                continue
            call.columns = ['timestamp', 'call_open', 'call_high', 'call_low',
                            'call_close', 'call_volume', 'call_trade_count', 'call_vwap']
            put = data['put_bars'].copy()
            if len(put) == 0:
                print(f"  No put data")
                continue
            put.columns = ['timestamp', 'put_open', 'put_high', 'put_low',
                           'put_close', 'put_volume', 'put_trade_count', 'put_vwap']
            merged = stock.merge(call, on='timestamp', how='left')
            merged = merged.merge(put, on='timestamp', how='left')
            merged['call_close'] = merged['call_close'].ffill()
            merged['put_close'] = merged['put_close'].ffill()
            merged = merged.dropna()
        except Exception as e:
            print(f"  Merge error: {e}")
            continue

        if len(merged) < 100:
            print(f"  Insufficient merged data ({len(merged)} bars)")
            continue

        strike = data['atm_strike']
        rfr = data['risk_free_rate']

        # Run each mode
        for mode in ['call', 'put', 'straddle']:
            try:
                engine = SingleLegEngine(config, mode=mode)

                entry_row = merged.iloc[0]
                entry_time = entry_row['timestamp']
                call_volume = entry_row.get('call_volume', 100)

                engine.enter_position(
                    entry_row['call_close'], entry_row['put_close'],
                    strike, entry_time,
                    data['call_symbol'], data['put_symbol'],
                    volume=call_volume
                )

                # Immediate delta hedge after entry
                market_close = date.replace(hour=16, minute=0)
                t_left = max((market_close - entry_time).total_seconds() / (365.25 * 24 * 3600), 1e-6)
                greeks = engine.calculate_portfolio_greeks(
                    entry_row['stock_close'], t_left, rfr, 0.25)
                hedge_size = engine.calculate_hedge_size(greeks['delta'])
                if hedge_size != 0:
                    engine.execute_hedge(hedge_size, entry_row['stock_close'],
                                         entry_time, volume=10000)

                hedge_count = 1 if hedge_size != 0 else 0

                # Main loop
                for idx, row in merged.iterrows():
                    timestamp = row['timestamp']
                    stock_price = row['stock_close']
                    engine.update_option_prices(row['call_close'], row['put_close'])

                    t_left = max((market_close - timestamp).total_seconds() / (365.25 * 24 * 3600), 1e-6)
                    greeks = engine.calculate_portfolio_greeks(stock_price, t_left, rfr, 0.25)
                    hedge_size = engine.calculate_hedge_size(greeks['delta'])
                    if hedge_size != 0:
                        engine.execute_hedge(hedge_size, stock_price, timestamp,
                                             volume=row.get('stock_volume', 10000))
                        hedge_count += 1

                # Close
                exit_row = merged.iloc[-1]
                engine.close_all_positions(
                    exit_row['call_close'], exit_row['put_close'],
                    exit_row['stock_close'], exit_row['timestamp'],
                    volume=exit_row.get('call_volume', 100)
                )

                results[mode].append({
                    'date': date.strftime('%Y-%m-%d'),
                    'pnl': engine.pnl['realized'],
                    'costs': engine.total_transaction_costs,
                    'hedges': hedge_count,
                })

            except Exception as e:
                print(f"  {mode} error: {e}")
                continue

        # Print daily summary
        day_results = {}
        for mode in ['call', 'put', 'straddle']:
            if results[mode] and results[mode][-1]['date'] == date.strftime('%Y-%m-%d'):
                day_results[mode] = results[mode][-1]['pnl']
        if day_results:
            parts = [f"{m}: ${p:.2f}" for m, p in day_results.items()]
            print(f"  {' | '.join(parts)}")

    # Final comparison
    print_comparison(results, start_date, end_date)
    save_results(results, start_date, end_date)
    return results


def print_comparison(results, start_date, end_date):
    """Print comparison table."""
    print(f"\n\n{'='*70}")
    print("COMPARISON RESULTS")
    print(f"Period: {start_date} to {end_date}")
    print(f"{'='*70}\n")

    header = f"{'Metric':<30} {'Call-Only':>12} {'Put-Only':>12} {'Straddle':>12}"
    print(header)
    print("-" * len(header))

    for mode in ['call', 'put', 'straddle']:
        if not results[mode]:
            print(f"  No results for {mode}")
            return

    metrics = {}
    for mode in ['call', 'put', 'straddle']:
        pnls = [r['pnl'] for r in results[mode]]
        costs = [r['costs'] for r in results[mode]]
        hedges = [r['hedges'] for r in results[mode]]
        metrics[mode] = {
            'days': len(pnls),
            'total_pnl': sum(pnls),
            'avg_pnl': np.mean(pnls) if pnls else 0,
            'median_pnl': np.median(pnls) if pnls else 0,
            'std_pnl': np.std(pnls) if pnls else 0,
            'best': max(pnls) if pnls else 0,
            'worst': min(pnls) if pnls else 0,
            'win_rate': sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0,
            'sharpe': (np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if pnls and np.std(pnls) > 0 else 0,
            'total_costs': sum(costs),
            'avg_hedges': np.mean(hedges) if hedges else 0,
        }

    rows = [
        ('Trading Days', 'days', 'd'),
        ('Total P&L', 'total_pnl', '$'),
        ('Avg Daily P&L', 'avg_pnl', '$'),
        ('Median Daily P&L', 'median_pnl', '$'),
        ('Std Dev', 'std_pnl', '$'),
        ('Best Day', 'best', '$'),
        ('Worst Day', 'worst', '$'),
        ('Win Rate', 'win_rate', '%'),
        ('Sharpe Ratio', 'sharpe', 'f'),
        ('Total Txn Costs', 'total_costs', '$'),
        ('Avg Hedges/Day', 'avg_hedges', 'f'),
    ]

    for label, key, fmt in rows:
        vals = []
        for mode in ['call', 'put', 'straddle']:
            v = metrics[mode][key]
            if fmt == '$':
                vals.append(f"${v:>10,.2f}")
            elif fmt == '%':
                vals.append(f"{v:>11.1%}")
            elif fmt == 'd':
                vals.append(f"{int(v):>12}")
            else:
                vals.append(f"{v:>12.2f}")
        print(f"{label:<30} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    # Correlation analysis
    print(f"\n{'='*70}")
    print("DAILY P&L CORRELATION")
    print(f"{'='*70}\n")

    # Align dates across modes
    call_dict = {r['date']: r['pnl'] for r in results['call']}
    put_dict = {r['date']: r['pnl'] for r in results['put']}
    straddle_dict = {r['date']: r['pnl'] for r in results['straddle']}

    common_dates = sorted(set(call_dict.keys()) & set(put_dict.keys()) & set(straddle_dict.keys()))
    if len(common_dates) >= 5:
        call_pnls = [call_dict[d] for d in common_dates]
        put_pnls = [put_dict[d] for d in common_dates]
        straddle_pnls = [straddle_dict[d] for d in common_dates]

        corr_cp = np.corrcoef(call_pnls, put_pnls)[0, 1]
        corr_cs = np.corrcoef(call_pnls, straddle_pnls)[0, 1]
        corr_ps = np.corrcoef(put_pnls, straddle_pnls)[0, 1]

        print(f"Call vs Put:      {corr_cp:.4f}")
        print(f"Call vs Straddle: {corr_cs:.4f}")
        print(f"Put vs Straddle:  {corr_ps:.4f}")

        # Call minus put spread
        spread = [c - p for c, p in zip(call_pnls, put_pnls)]
        print(f"\nCall - Put daily spread:")
        print(f"  Mean:   ${np.mean(spread):.2f}")
        print(f"  Median: ${np.median(spread):.2f}")
        print(f"  Std:    ${np.std(spread):.2f}")
        print(f"  t-stat: {np.mean(spread) / (np.std(spread) / np.sqrt(len(spread))):.2f}" if np.std(spread) > 0 else "  t-stat: N/A")
    else:
        print(f"  Only {len(common_dates)} common dates - insufficient for correlation")


def save_results(results, start_date, end_date):
    """Save results to JSON."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output = {
        'period': {'start': start_date, 'end': end_date},
        'timestamp': timestamp,
        'call': results['call'],
        'put': results['put'],
        'straddle': results['straddle'],
    }
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f'call_vs_put_results_{timestamp}.json')
    with open(outpath, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {outpath}")


if __name__ == '__main__':
    # Past year: April 2025 - April 2026
    run_comparison("2025-04-01", "2026-04-04")
