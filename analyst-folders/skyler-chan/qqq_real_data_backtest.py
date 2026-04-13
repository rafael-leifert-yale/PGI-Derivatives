"""
QQQ Gamma Scalping Backtest — REAL OPTION DATA ONLY
No Black-Scholes pricing. Every option price comes from Alpaca market bars.

For each day:
1. Pull stock bars
2. Determine ATM strike from open price
3. Calculate target strikes for each delta bucket using BS (ONLY for strike selection)
4. Pull REAL option bars from Alpaca for each strike
5. Use actual market option prices for entry, exit, and mark-to-market
6. Compute delta from market-implied IV (updated each bar from real prices)
7. Hedge based on real delta, track real P&L
"""

import sys, os, warnings, json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
from scipy.optimize import brentq

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.data_engine import DataEngine
from backtest.execution_model import ExecutionModel
from utils.greeks import GreeksCalculator


def find_strike_for_delta(spot: float, target_delta: float, tte: float,
                           rfr: float, iv_guess: float, option_type: str) -> float:
    """Use BS ONLY to find what strike corresponds to a given delta. Not for pricing."""
    if target_delta >= 0.49:
        return round(spot)

    def delta_error(K):
        g = GreeksCalculator.black_scholes(spot, K, max(tte, 1e-6), rfr, max(iv_guess, 0.05), option_type)
        return abs(g['delta']) - target_delta

    if option_type == 'call':
        lo, hi = spot, spot * 1.3
    else:
        lo, hi = spot * 0.7, spot

    try:
        strike = brentq(delta_error, lo, hi, xtol=0.5)
        return round(strike)
    except:
        return round(spot)


def get_market_iv(option_price: float, spot: float, strike: float,
                   tte: float, rfr: float, option_type: str) -> float:
    """Back out IV from a real market option price. Used for delta estimation only."""
    if option_price < 0.01 or tte < 1e-8:
        return 0.25
    iv = GreeksCalculator.implied_volatility(option_price, spot, strike, tte, rfr, option_type)
    if iv is None or iv < 0.01 or iv > 5.0:
        return 0.25
    return iv


def run_day_real(data_engine: DataEngine, exec_model: ExecutionModel,
                  config: Dict, day: datetime, target_delta: float) -> Optional[Dict]:
    """
    Run one day using ONLY real market data from Alpaca.

    Steps:
    1. Fetch stock bars
    2. Find target strikes
    3. Fetch REAL option bars for those strikes
    4. Merge and simulate with real prices
    """
    market_close = day.replace(hour=16, minute=0)
    rfr = 0.04  # approximate; we'll refine if needed

    # Step 1: Fetch stock bars
    try:
        stock_bars = data_engine.fetch_stock_bars(day)
        if stock_bars.empty or len(stock_bars) < 50:
            return None
    except:
        return None

    # Step 2: Determine strikes
    # Use opening price for strike selection
    open_price = stock_bars.iloc[1]['close'] if len(stock_bars) > 1 else stock_bars.iloc[0]['close']
    atm_strike = round(open_price)

    # Estimate IV from ATM straddle cost (rough guess for strike selection only)
    entry_time_approx = stock_bars.iloc[0]['timestamp']
    tte_approx = (market_close - entry_time_approx).total_seconds() / (365.25 * 24 * 3600)
    iv_guess = 0.30  # Just for finding strikes; we'll use market IV for everything else

    if target_delta >= 0.49:
        call_strike = atm_strike
        put_strike = atm_strike
    else:
        call_strike = find_strike_for_delta(open_price, target_delta, tte_approx, rfr, iv_guess, 'call')
        put_strike = find_strike_for_delta(open_price, target_delta, tte_approx, rfr, iv_guess, 'put')

    # Step 3: Fetch REAL option bars from Alpaca
    call_symbol = data_engine.construct_option_symbol('QQQ', day, call_strike, 'call')
    put_symbol = data_engine.construct_option_symbol('QQQ', day, put_strike, 'put')

    try:
        call_bars = data_engine.fetch_option_bars(call_symbol, day)
        put_bars = data_engine.fetch_option_bars(put_symbol, day)
    except:
        return None

    if call_bars.empty or put_bars.empty:
        return None

    if len(call_bars) < 20 or len(put_bars) < 20:
        return None

    # Step 4: Merge stock + call + put on timestamp
    stock = stock_bars.copy()
    stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low', 'stock_close', 'stock_volume']

    call = call_bars.copy()
    call_expected = ['timestamp', 'call_open', 'call_high', 'call_low', 'call_close']
    extra_call = [f'call_col{i}' for i in range(len(call.columns) - len(call_expected))]
    call.columns = call_expected + extra_call

    put = put_bars.copy()
    put_expected = ['timestamp', 'put_open', 'put_high', 'put_low', 'put_close']
    extra_put = [f'put_col{i}' for i in range(len(put.columns) - len(put_expected))]
    put.columns = put_expected + extra_put

    merged = stock[['timestamp', 'stock_close', 'stock_volume']].merge(
        call[['timestamp', 'call_close']], on='timestamp', how='inner'
    ).merge(
        put[['timestamp', 'put_close']], on='timestamp', how='inner'
    )

    # Forward fill and drop NaN
    merged = merged.sort_values('timestamp').reset_index(drop=True)
    merged = merged.dropna()

    if len(merged) < 20:
        return None

    # Step 5: Simulate trading with REAL prices
    entry_row = merged.iloc[0]
    entry_stock = entry_row['stock_close']
    entry_call_price = entry_row['call_close']
    entry_put_price = entry_row['put_close']
    entry_time = entry_row['timestamp']

    if entry_call_price < 0.01 or entry_put_price < 0.01:
        return None

    # Entry cost (real market prices + execution model slippage)
    call_exec = exec_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'BUY', 'quantity': 1, 'mid_price': entry_call_price},
        market_data={'volume': 100, 'current_time': entry_time.time()}
    )
    put_exec = exec_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'BUY', 'quantity': 1, 'mid_price': entry_put_price},
        market_data={'volume': 100, 'current_time': entry_time.time()}
    )

    realized_cash = -(call_exec['total_cost'] + put_exec['total_cost'])
    total_costs = call_exec['transaction_cost'] + put_exec['transaction_cost']
    straddle_cost_paid = call_exec['total_cost'] + put_exec['total_cost']

    stock_position = 0
    hedge_count = 0
    delta_threshold = config.get('delta_threshold', 0.30) * 100

    # P&L curve
    pnl_curve = []
    hedge_log = []

    for idx, (_, row) in enumerate(merged.iterrows()):
        timestamp = row['timestamp']
        stock_price = row['stock_close']
        call_price = row['call_close']  # REAL market price
        put_price = row['put_close']    # REAL market price

        tte = (market_close - timestamp).total_seconds() / (365.25 * 24 * 3600)
        tte = max(tte, 1e-6)
        minutes_to_close = (market_close - timestamp).total_seconds() / 60

        # Compute delta from MARKET-IMPLIED IV (updated each bar)
        call_iv = get_market_iv(call_price, stock_price, call_strike, tte, rfr, 'call')
        put_iv = get_market_iv(put_price, stock_price, put_strike, tte, rfr, 'put')

        call_greeks = GreeksCalculator.black_scholes(stock_price, call_strike, tte, rfr, call_iv, 'call')
        put_greeks = GreeksCalculator.black_scholes(stock_price, put_strike, tte, rfr, put_iv, 'put')

        option_delta = (call_greeks['delta'] + put_greeks['delta']) * 100
        portfolio_delta = option_delta + stock_position

        # Mark-to-market using REAL prices
        option_value = (call_price + put_price) * 100  # REAL market value
        stock_mtm = stock_position * stock_price
        total_mtm = realized_cash + option_value + stock_mtm

        pnl_curve.append({
            'minute': idx,
            'time': timestamp.strftime('%H:%M'),
            'minutes_to_close': round(minutes_to_close, 1),
            'stock_price': round(stock_price, 2),
            'call_price': round(call_price, 4),
            'put_price': round(put_price, 4),
            'call_iv': round(call_iv, 4),
            'put_iv': round(put_iv, 4),
            'portfolio_delta': round(portfolio_delta, 1),
            'cumulative_pnl': round(total_mtm, 2),
            'option_value': round(option_value, 2),
        })

        # Hedge check
        if abs(portfolio_delta) > delta_threshold:
            shares_needed = -round(portfolio_delta)
            max_pos = config.get('max_stock_position', 500)
            if abs(stock_position + shares_needed) > max_pos:
                available = max_pos - abs(stock_position)
                shares_needed = int(np.sign(shares_needed) * min(abs(shares_needed), available))

            if shares_needed != 0:
                side = 'BUY' if shares_needed > 0 else 'SELL'
                vol = int(row['stock_volume']) if not np.isnan(row['stock_volume']) else 10000

                execution = exec_model.execute_trade(
                    trade={'asset_type': 'stock', 'side': side,
                           'quantity': abs(shares_needed), 'mid_price': stock_price},
                    market_data={'volume': vol, 'current_time': timestamp.time()}
                )

                if side == 'BUY':
                    realized_cash -= execution['total_cost']
                else:
                    realized_cash += execution['total_cost']

                total_costs += execution['transaction_cost']
                stock_position += shares_needed
                hedge_count += 1
                hedge_log.append({
                    'time': timestamp.strftime('%H:%M'),
                    'min_to_close': round(minutes_to_close, 1),
                    'side': side,
                    'shares': abs(shares_needed),
                    'price': round(execution['executed_price'], 2),
                    'stock_pos': stock_position,
                })

    # Exit: sell options at REAL market price, close stock
    exit_row = merged.iloc[-1]
    exit_call = exit_row['call_close']
    exit_put = exit_row['put_close']
    exit_stock = exit_row['stock_close']
    exit_time = exit_row['timestamp']

    call_sell = exec_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'SELL', 'quantity': 1, 'mid_price': exit_call},
        market_data={'volume': 100, 'current_time': exit_time.time()}
    )
    put_sell = exec_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'SELL', 'quantity': 1, 'mid_price': exit_put},
        market_data={'volume': 100, 'current_time': exit_time.time()}
    )
    realized_cash += call_sell['total_cost'] + put_sell['total_cost']
    total_costs += call_sell['transaction_cost'] + put_sell['transaction_cost']

    if stock_position != 0:
        side = 'SELL' if stock_position > 0 else 'BUY'
        exec_stock = exec_model.execute_trade(
            trade={'asset_type': 'stock', 'side': side,
                   'quantity': abs(stock_position), 'mid_price': exit_stock},
            market_data={'volume': 10000, 'current_time': exit_time.time()}
        )
        if side == 'SELL':
            realized_cash += exec_stock['total_cost']
        else:
            realized_cash -= exec_stock['total_cost']
        total_costs += exec_stock['transaction_cost']

    final_pnl = realized_cash

    # Option-only P&L (what straddle did ignoring hedges)
    option_pnl = (exit_call + exit_put - entry_call_price - entry_put_price) * 100

    # Time-window analysis
    curve_df = pd.DataFrame(pnl_curve)
    n = len(curve_df)
    third = n // 3
    if third > 1:
        first_pnl = curve_df.iloc[third]['cumulative_pnl'] - curve_df.iloc[0]['cumulative_pnl']
        mid_pnl = curve_df.iloc[2*third]['cumulative_pnl'] - curve_df.iloc[third]['cumulative_pnl']
        last_pnl = curve_df.iloc[-1]['cumulative_pnl'] - curve_df.iloc[2*third]['cumulative_pnl']
    else:
        first_pnl = mid_pnl = last_pnl = 0

    return {
        'date': day.strftime('%Y-%m-%d'),
        'weekday': day.strftime('%A'),
        'target_delta': target_delta,
        'call_strike': call_strike,
        'put_strike': put_strike,
        'call_symbol': call_symbol,
        'put_symbol': put_symbol,
        'entry_call': round(entry_call_price, 2),
        'entry_put': round(entry_put_price, 2),
        'entry_cost': round(straddle_cost_paid, 2),
        'exit_call': round(exit_call, 2),
        'exit_put': round(exit_put, 2),
        'option_pnl': round(option_pnl, 2),
        'hedge_count': hedge_count,
        'transaction_costs': round(total_costs, 2),
        'daily_pnl': round(final_pnl, 2),
        'first_third_pnl': round(first_pnl, 2),
        'mid_third_pnl': round(mid_pnl, 2),
        'last_third_pnl': round(last_pnl, 2),
        'bars': n,
        'time_range': f"{merged.iloc[0]['timestamp'].strftime('%H:%M')}-{merged.iloc[-1]['timestamp'].strftime('%H:%M')}",
        'qqq_open': round(merged.iloc[0]['stock_close'], 2),
        'qqq_close': round(merged.iloc[-1]['stock_close'], 2),
        'qqq_range': round(merged['stock_close'].max() - merged['stock_close'].min(), 2),
        'hedge_log': hedge_log,
    }


def main():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scan_results')
    os.makedirs(output_dir, exist_ok=True)

    base_config = {
        'max_stock_position': 500,
        'stock_spread': 0.00,       # ZERO spread
        'option_spread_pct': 0.00,  # ZERO spread
        'zero_costs': True,         # NO fees at all
    }

    data_engine = DataEngine(symbol='QQQ', config={})

    # Get trading days — full range, no sampling
    trading_days = data_engine.get_0dte_trading_days('2025-06-01', '2026-04-11')

    # Test hedge thresholds: ±1 share with ZERO costs
    hedge_thresholds = [0.01]
    # Focus on 25d (best delta from prior run) + 10d for comparison
    delta_targets = [0.25, 0.10]

    all_results = []

    for threshold in hedge_thresholds:
        shares_label = int(threshold * 100)
        config = {**base_config, 'delta_threshold': threshold}
        exec_model = ExecutionModel(config)

        for delta in delta_targets:
            delta_label = f"{int(delta*100)}d"
            print(f"\n{'='*70}")
            print(f"TESTING {delta_label} STRANGLE — HEDGE EVERY ±{shares_label} SHARES")
            print(f"{'='*70}")

            for day in trading_days:
                result = run_day_real(data_engine, exec_model, config, day, delta)

                if result is None:
                    print(f"  {day.date()} SKIP (no data for strikes)")
                    continue

                result['hedge_threshold'] = shares_label
                hedges_str = json.dumps(result.pop('hedge_log'))
                all_results.append(result)

                status = "+" if result['daily_pnl'] > 0 else "-"
                print(f"  {status} {result['date']} "
                      f"entry=${result['entry_cost']:>6.0f} "
                      f"option_pnl=${result['option_pnl']:>7.2f} "
                      f"hedges={result['hedge_count']:>2d} "
                      f"pnl=${result['daily_pnl']:>8.2f} "
                      f"({result['bars']} bars {result['time_range']})")

    df = pd.DataFrame(all_results)
    df.to_csv(os.path.join(output_dir, 'qqq_real_data_results.csv'), index=False)

    # === ANALYSIS ===
    print("\n\n" + "=" * 80)
    print("RESULTS — REAL OPTION DATA")
    print("=" * 80)

    if df.empty:
        print("No results!")
        return

    # By threshold + delta
    print("\n--- BY HEDGE THRESHOLD & DELTA ---")
    for threshold in hedge_thresholds:
        shares_label = int(threshold * 100)
        print(f"\n  === ±{shares_label} SHARES ===")
        for delta in delta_targets:
            sub = df[(df['target_delta'] == delta) & (df['hedge_threshold'] == shares_label)]
            if len(sub) == 0:
                continue
            delta_label = f"{int(delta*100)}d"
            pnls = sub['daily_pnl']
            avg = pnls.mean()
            std = pnls.std()
            sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0
            win_rate = (pnls > 0).mean()
            print(f"    {delta_label}: n={len(sub)}  avg=${avg:>8.2f}  std=${std:>8.2f}  "
                  f"sharpe={sharpe:>6.2f}  win={win_rate:.0%}")
            print(f"      avg_cost=${sub['entry_cost'].mean():.0f}  "
                  f"avg_hedges={sub['hedge_count'].mean():.1f}  "
                  f"avg_costs=${sub['transaction_costs'].mean():.2f}  "
                  f"best=${pnls.max():.2f}  worst=${pnls.min():.2f}")

    # Summary comparison table
    print("\n\n--- SUMMARY: AVG DAILY P&L BY THRESHOLD x DELTA ---")
    print(f"  {'Threshold':>10s}", end="")
    for delta in delta_targets:
        print(f"  {int(delta*100)}d", end="")
    print()
    for threshold in hedge_thresholds:
        shares_label = int(threshold * 100)
        print(f"  ±{shares_label:>2d} shares ", end="")
        for delta in delta_targets:
            sub = df[(df['target_delta'] == delta) & (df['hedge_threshold'] == shares_label)]
            if len(sub) > 0:
                print(f"  ${sub['daily_pnl'].mean():>7.2f}", end="")
            else:
                print(f"  {'N/A':>8s}", end="")
        print()

    print(f"\n  {'Threshold':>10s}", end="")
    for delta in delta_targets:
        print(f"  {int(delta*100)}d", end="")
    print("   (hedges/day)")
    for threshold in hedge_thresholds:
        shares_label = int(threshold * 100)
        print(f"  ±{shares_label:>2d} shares ", end="")
        for delta in delta_targets:
            sub = df[(df['target_delta'] == delta) & (df['hedge_threshold'] == shares_label)]
            if len(sub) > 0:
                print(f"  {sub['hedge_count'].mean():>8.1f}", end="")
            else:
                print(f"  {'N/A':>8s}", end="")
        print()

    print(f"\n  {'Threshold':>10s}", end="")
    for delta in delta_targets:
        print(f"  {int(delta*100)}d", end="")
    print("   (win rate)")
    for threshold in hedge_thresholds:
        shares_label = int(threshold * 100)
        print(f"  ±{shares_label:>2d} shares ", end="")
        for delta in delta_targets:
            sub = df[(df['target_delta'] == delta) & (df['hedge_threshold'] == shares_label)]
            if len(sub) > 0:
                wr = (sub['daily_pnl'] > 0).mean()
                print(f"  {wr:>7.0%} ", end="")
            else:
                print(f"  {'N/A':>8s}", end="")
        print()

    print("\n\nDone.")


if __name__ == "__main__":
    main()
