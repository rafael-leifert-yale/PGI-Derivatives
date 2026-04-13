"""
QQQ Gamma Scalping: Delta-Based Strangles + Intraday P&L Timing
- Constructs strangles by option delta (5d, 10d, 15d, 25d, 35d, 50d)
- Tracks cumulative P&L minute by minute to see WHERE gains come from
- Analyzes which time windows contribute most to P&L
"""

import sys, os, json, warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
from scipy.stats import norm
from scipy.optimize import brentq

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.data_engine import DataEngine
from backtest.execution_model import ExecutionModel
from utils.greeks import GreeksCalculator


def find_strike_by_delta(spot: float, target_delta: float, tte: float,
                          rfr: float, iv: float, option_type: str,
                          strike_interval: float = 1.0) -> float:
    """
    Find the strike that gives the target delta.

    Args:
        spot: current underlying price
        target_delta: absolute delta target (e.g. 0.25 for 25-delta)
        tte: time to expiry in years
        rfr: risk-free rate
        iv: implied volatility
        option_type: 'call' or 'put'
        strike_interval: round to nearest interval ($1 for QQQ)

    Returns:
        strike price rounded to nearest interval
    """
    # For calls: delta decreases as strike increases (ATM~0.5, OTM->0)
    # For puts: |delta| decreases as strike decreases (ATM~0.5, OTM->0)

    def delta_error(K):
        greeks = GreeksCalculator.black_scholes(spot, K, tte, rfr, iv, option_type)
        return abs(greeks['delta']) - target_delta

    # Search bounds
    if option_type == 'call':
        # Call delta decreases with higher strike
        lo = spot * 0.9
        hi = spot * 1.2
    else:
        # Put |delta| decreases with lower strike
        lo = spot * 0.8
        hi = spot * 1.1

    try:
        strike = brentq(delta_error, lo, hi, xtol=0.01)
        # Round to nearest strike interval
        strike = round(strike / strike_interval) * strike_interval
        return strike
    except:
        # Fallback: ATM
        return round(spot / strike_interval) * strike_interval


def run_day_with_pnl_curve(config: Dict, merged: pd.DataFrame, day_data: Dict,
                            target_delta: float) -> Optional[Dict]:
    """
    Run one day with delta-based strangle, tracking P&L every minute.

    Args:
        config: strategy config
        merged: minute-bar data
        day_data: from DataEngine
        target_delta: absolute delta for each leg (0.50 = straddle, 0.25 = 25d strangle, etc.)
    """
    execution_model = ExecutionModel(config)
    date = day_data['date']
    rfr = day_data['risk_free_rate']

    if len(merged) < 30:
        return None

    entry_row = merged.iloc[0]
    entry_stock = entry_row['stock_close']
    entry_time = entry_row['timestamp']

    market_close = date.replace(hour=16, minute=0)
    tte_entry = (market_close - entry_time).total_seconds() / (365.25 * 24 * 3600)
    tte_entry = max(tte_entry, 1e-6)

    # Estimate IV from ATM option prices
    atm_strike = day_data['atm_strike']
    atm_call_price = entry_row['call_close']
    try:
        iv = GreeksCalculator.get_iv_from_market(
            atm_call_price, entry_stock, atm_strike, tte_entry, rfr, 'call'
        )
    except:
        iv = 0.25
    iv = max(0.05, min(iv, 3.0))

    # Find strikes by delta
    if target_delta >= 0.49:
        # Straddle: both legs ATM
        call_strike = atm_strike
        put_strike = atm_strike
    else:
        call_strike = find_strike_by_delta(entry_stock, target_delta, tte_entry, rfr, iv, 'call')
        put_strike = find_strike_by_delta(entry_stock, target_delta, tte_entry, rfr, iv, 'put')

    # Price the legs at entry
    call_entry_greeks = GreeksCalculator.black_scholes(
        entry_stock, call_strike, tte_entry, rfr, iv, 'call'
    )
    put_entry_greeks = GreeksCalculator.black_scholes(
        entry_stock, put_strike, tte_entry, rfr, iv, 'put'
    )

    call_entry_price = call_entry_greeks['price']
    put_entry_price = put_entry_greeks['price']

    if call_entry_price < 0.005 or put_entry_price < 0.005:
        return None

    # Actual entry deltas (after rounding to nearest strike)
    actual_call_delta = round(call_entry_greeks['delta'], 3)
    actual_put_delta = round(put_entry_greeks['delta'], 3)
    entry_gamma = round((call_entry_greeks['gamma'] + put_entry_greeks['gamma']) * 100, 4)
    entry_theta = round((call_entry_greeks['theta']) + put_entry_greeks['theta'], 4)

    straddle_cost = (call_entry_price + put_entry_price) * 100

    # Execute entry with slippage
    call_exec = execution_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'BUY', 'quantity': 1, 'mid_price': call_entry_price},
        market_data={'volume': 100, 'current_time': entry_time.time()}
    )
    put_exec = execution_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'BUY', 'quantity': 1, 'mid_price': put_entry_price},
        market_data={'volume': 100, 'current_time': entry_time.time()}
    )

    # Tracking
    stock_position = 0
    realized_cash = -(call_exec['total_cost'] + put_exec['total_cost'])
    total_costs = call_exec['transaction_cost'] + put_exec['transaction_cost']
    hedge_count = 0

    delta_threshold = config.get('delta_threshold', 0.30) * 100

    # Minute-by-minute P&L tracking
    pnl_curve = []  # (minutes_from_entry, cumulative_pnl, component_breakdown)
    hedge_times = []

    for idx, (_, row) in enumerate(merged.iterrows()):
        timestamp = row['timestamp']
        stock_price = row['stock_close']

        tte = (market_close - timestamp).total_seconds() / (365.25 * 24 * 3600)
        tte = max(tte, 1e-6)

        minutes_to_close = (market_close - timestamp).total_seconds() / 60

        # Current option values
        call_greeks = GreeksCalculator.black_scholes(stock_price, call_strike, tte, rfr, iv, 'call')
        put_greeks = GreeksCalculator.black_scholes(stock_price, put_strike, tte, rfr, iv, 'put')

        call_value = call_greeks['price']
        put_value = put_greeks['price']
        option_value = (call_value + put_value) * 100

        # Portfolio delta
        option_delta = (call_greeks['delta'] + put_greeks['delta']) * 100
        portfolio_delta = option_delta + stock_position
        portfolio_gamma = (call_greeks['gamma'] + put_greeks['gamma']) * 100

        # Mark-to-market P&L
        # Options unrealized = current value - cost paid
        # Stock unrealized = position * (current - avg_cost) -- simplified as realized_cash tracks all trades
        # Total MTM = realized_cash + current_option_value + stock_position * stock_price
        stock_mtm = stock_position * stock_price
        total_mtm = realized_cash + option_value + stock_mtm

        # Decompose: option P&L vs hedge P&L
        option_pnl_component = option_value - straddle_cost
        hedge_pnl_component = total_mtm - option_pnl_component  # everything else

        pnl_curve.append({
            'minutes_from_entry': idx,
            'minutes_to_close': round(minutes_to_close, 1),
            'time': timestamp.strftime('%H:%M'),
            'stock_price': round(stock_price, 2),
            'cumulative_pnl': round(total_mtm, 2),
            'option_pnl': round(option_pnl_component, 2),
            'hedge_pnl': round(hedge_pnl_component, 2),
            'portfolio_delta': round(portfolio_delta, 1),
            'portfolio_gamma': round(portfolio_gamma, 4),
            'call_value': round(call_value, 4),
            'put_value': round(put_value, 4),
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
                vol = int(row.get('stock_volume', 10000)) if not np.isnan(row.get('stock_volume', 10000)) else 10000

                execution = execution_model.execute_trade(
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
                hedge_times.append({
                    'minute': idx,
                    'time': timestamp.strftime('%H:%M'),
                    'side': side,
                    'shares': abs(shares_needed),
                    'price': round(execution['executed_price'], 2),
                    'stock_pos': stock_position,
                    'minutes_to_close': round(minutes_to_close, 1),
                })

    # Close everything
    exit_row = merged.iloc[-1]
    exit_stock = exit_row['stock_close']
    exit_time = exit_row['timestamp']
    exit_tte = (market_close - exit_time).total_seconds() / (365.25 * 24 * 3600)
    exit_tte = max(exit_tte, 1e-6)

    call_exit = GreeksCalculator.black_scholes(exit_stock, call_strike, exit_tte, rfr, iv, 'call')
    put_exit = GreeksCalculator.black_scholes(exit_stock, put_strike, exit_tte, rfr, iv, 'put')

    # Sell options
    call_sell = execution_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'SELL', 'quantity': 1, 'mid_price': call_exit['price']},
        market_data={'volume': 100, 'current_time': exit_time.time()}
    )
    put_sell = execution_model.execute_trade(
        trade={'asset_type': 'option', 'side': 'SELL', 'quantity': 1, 'mid_price': put_exit['price']},
        market_data={'volume': 100, 'current_time': exit_time.time()}
    )
    realized_cash += call_sell['total_cost'] + put_sell['total_cost']
    total_costs += call_sell['transaction_cost'] + put_sell['transaction_cost']

    # Close stock
    if stock_position != 0:
        side = 'SELL' if stock_position > 0 else 'BUY'
        exec_stock = execution_model.execute_trade(
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

    # Compute time-window P&L contributions
    curve_df = pd.DataFrame(pnl_curve)
    total_minutes = len(curve_df)

    # Split into thirds
    third = total_minutes // 3
    if third > 0 and total_minutes > 3:
        first_third_pnl = curve_df.iloc[third]['cumulative_pnl'] - curve_df.iloc[0]['cumulative_pnl']
        mid_third_pnl = curve_df.iloc[2*third]['cumulative_pnl'] - curve_df.iloc[third]['cumulative_pnl']
        last_third_pnl = curve_df.iloc[-1]['cumulative_pnl'] - curve_df.iloc[2*third]['cumulative_pnl']
    else:
        first_third_pnl = mid_third_pnl = last_third_pnl = 0

    # Also compute minute-by-minute P&L changes
    curve_df['pnl_change'] = curve_df['cumulative_pnl'].diff().fillna(0)

    # Peak and trough
    peak_pnl = curve_df['cumulative_pnl'].max()
    trough_pnl = curve_df['cumulative_pnl'].min()
    peak_minute = curve_df.loc[curve_df['cumulative_pnl'].idxmax(), 'time']
    trough_minute = curve_df.loc[curve_df['cumulative_pnl'].idxmin(), 'time']

    return {
        'date': date.strftime('%Y-%m-%d'),
        'weekday': date.strftime('%A'),
        'target_delta': target_delta,
        'call_strike': call_strike,
        'put_strike': put_strike,
        'actual_call_delta': actual_call_delta,
        'actual_put_delta': actual_put_delta,
        'strike_width': call_strike - put_strike,
        'entry_gamma': entry_gamma,
        'entry_theta': entry_theta,
        'iv': round(iv, 4),
        'call_entry_price': round(call_entry_price, 4),
        'put_entry_price': round(put_entry_price, 4),
        'straddle_cost': round(straddle_cost, 2),
        'hedge_count': hedge_count,
        'transaction_costs': round(total_costs, 2),
        'daily_pnl': round(final_pnl, 2),
        'first_third_pnl': round(first_third_pnl, 2),
        'mid_third_pnl': round(mid_third_pnl, 2),
        'last_third_pnl': round(last_third_pnl, 2),
        'peak_pnl': round(peak_pnl, 2),
        'trough_pnl': round(trough_pnl, 2),
        'peak_time': peak_minute,
        'trough_time': trough_minute,
        'total_minutes': total_minutes,
        'pnl_curve': curve_df.to_dict('records'),
        'hedge_times': hedge_times,
    }


def main():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scan_results')
    os.makedirs(output_dir, exist_ok=True)

    config = {
        'delta_threshold': 0.30,
        'max_stock_position': 500,
        'stock_spread': 0.01,
        'option_spread_pct': 0.015,
    }

    data_engine = DataEngine(symbol='QQQ', config={})

    # Get as many days as possible
    start_date = '2026-02-01'
    end_date = '2026-04-09'
    trading_days = data_engine.get_0dte_trading_days(start_date, end_date)

    # Sample 20 days
    if len(trading_days) > 20:
        indices = np.linspace(0, len(trading_days) - 1, 20, dtype=int)
        trading_days = [trading_days[i] for i in indices]

    # Fetch all data
    print("Fetching data...")
    day_cache = {}
    for day in trading_days:
        try:
            data = data_engine.fetch_day_data(day)
            if data['validation']['severity'] == 'critical':
                continue

            stock = data['stock_bars'].copy()
            stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low',
                             'stock_close', 'stock_volume']

            call = data['call_bars'].copy()
            put = data['put_bars'].copy()
            if len(call) == 0 or len(put) == 0:
                continue

            call_cols = ['timestamp', 'call_open', 'call_high', 'call_low', 'call_close',
                         'call_volume', 'call_trade_count', 'call_vwap']
            put_cols = ['timestamp', 'put_open', 'put_high', 'put_low', 'put_close',
                        'put_volume', 'put_trade_count', 'put_vwap']

            if len(call.columns) != len(call_cols) or len(put.columns) != len(put_cols):
                continue

            call.columns = call_cols
            put.columns = put_cols

            merged = stock.merge(call, on='timestamp', how='left')
            merged = merged.merge(put, on='timestamp', how='left')
            merged['call_close'] = merged['call_close'].ffill()
            merged['put_close'] = merged['put_close'].ffill()
            merged = merged.dropna()

            if len(merged) >= 50:
                day_cache[day] = (merged, data)
                print(f"  {day.date()} OK ({len(merged)} bars)")
        except Exception as e:
            print(f"  {day.date()} SKIP: {e}")

    print(f"\n{len(day_cache)} days cached\n")

    # Delta targets to test
    delta_targets = [0.05, 0.10, 0.15, 0.25, 0.35, 0.50]

    all_results = []
    all_curves = {}  # {(delta, date): curve_df}

    for delta in delta_targets:
        delta_label = f"{int(delta*100)}d"
        print(f"\n{'='*60}")
        print(f"TESTING {delta_label} STRANGLE (delta={delta})")
        print(f"{'='*60}")

        for day, (merged, data) in day_cache.items():
            result = run_day_with_pnl_curve(config, merged, data, delta)
            if result:
                # Store curve separately
                curve = result.pop('pnl_curve')
                hedge_detail = result.pop('hedge_times')
                all_results.append(result)
                all_curves[(delta, day.strftime('%Y-%m-%d'))] = (curve, hedge_detail)

                status = "+" if result['daily_pnl'] > 0 else "-"
                print(f"  {status} {result['date']} K={result['put_strike']}/{result['call_strike']} "
                      f"cost=${result['straddle_cost']:>6.2f} "
                      f"pnl=${result['daily_pnl']:>8.2f} "
                      f"hedges={result['hedge_count']} "
                      f"1st/mid/last=${result['first_third_pnl']:>6.0f}/${result['mid_third_pnl']:>6.0f}/${result['last_third_pnl']:>6.0f}")

    df = pd.DataFrame(all_results)
    df.to_csv(os.path.join(output_dir, 'qqq_delta_strangle_results.csv'), index=False)

    # ==================== ANALYSIS ====================
    print("\n\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    # 1. Delta-based structure comparison
    print("\n--- STRANGLE BY DELTA ---")
    delta_group = df.groupby('target_delta').agg({
        'daily_pnl': ['mean', 'std', 'count'],
        'straddle_cost': 'mean',
        'strike_width': 'mean',
        'hedge_count': 'mean',
        'entry_gamma': 'mean',
        'entry_theta': 'mean',
        'transaction_costs': 'mean',
    }).round(2)
    delta_group.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_cost', 'avg_width',
                           'avg_hedges', 'avg_gamma', 'avg_theta', 'avg_txn']
    delta_group['sharpe'] = (delta_group['avg_pnl'] / delta_group['std_pnl'] * np.sqrt(252)).round(2)
    delta_group['win_rate'] = df.groupby('target_delta')['daily_pnl'].apply(
        lambda x: (x > 0).mean()
    ).round(3)
    delta_group['gamma_per_dollar'] = (delta_group['avg_gamma'] / delta_group['avg_cost'] * 100).round(4)
    print(delta_group.to_string())

    # 2. Intraday timing: where does P&L come from?
    print("\n\n--- INTRADAY P&L TIMING (averaged across all days) ---")

    for delta in delta_targets:
        delta_label = f"{int(delta*100)}d"
        sub = df[df['target_delta'] == delta]
        if len(sub) == 0:
            continue

        avg_first = sub['first_third_pnl'].mean()
        avg_mid = sub['mid_third_pnl'].mean()
        avg_last = sub['last_third_pnl'].mean()
        total = avg_first + avg_mid + avg_last

        if total != 0:
            pct_first = avg_first / abs(total) * 100
            pct_mid = avg_mid / abs(total) * 100
            pct_last = avg_last / abs(total) * 100
        else:
            pct_first = pct_mid = pct_last = 0

        print(f"\n  {delta_label} strangle (avg total P&L: ${total:.2f}):")
        print(f"    First third:  ${avg_first:>8.2f}  ({pct_first:>+6.1f}%)")
        print(f"    Middle third: ${avg_mid:>8.2f}  ({pct_mid:>+6.1f}%)")
        print(f"    Last third:   ${avg_last:>8.2f}  ({pct_last:>+6.1f}%)")

    # 3. Aggregate P&L curves: average across days for each delta
    print("\n\n--- AVERAGE CUMULATIVE P&L CURVE (by minutes to close) ---")
    print("(How P&L evolves during the session)")

    for delta in [0.50, 0.25, 0.10]:
        delta_label = f"{int(delta*100)}d"
        curves_for_delta = []

        for (d, date_str), (curve, _) in all_curves.items():
            if d == delta:
                cdf = pd.DataFrame(curve)
                # Normalize by minutes_to_close for alignment
                curves_for_delta.append(cdf[['minutes_to_close', 'cumulative_pnl', 'option_pnl', 'hedge_pnl']])

        if not curves_for_delta:
            continue

        # Bin by 10-minute intervals of minutes_to_close
        print(f"\n  {delta_label} strangle avg P&L curve:")
        print(f"  {'Min to Close':>12s} {'Cum P&L':>10s} {'Option P&L':>12s} {'Hedge P&L':>12s}")

        all_points = pd.concat(curves_for_delta)
        all_points['time_bucket'] = (all_points['minutes_to_close'] // 15).astype(int) * 15

        bucket_avg = all_points.groupby('time_bucket').agg({
            'cumulative_pnl': 'mean',
            'option_pnl': 'mean',
            'hedge_pnl': 'mean',
        }).sort_index(ascending=False).round(2)

        for bucket, row in bucket_avg.iterrows():
            bar_len = int(abs(row['cumulative_pnl']) / 10)
            bar_char = '+' if row['cumulative_pnl'] >= 0 else '-'
            bar = bar_char * min(bar_len, 40)
            print(f"  {bucket:>8.0f} min    ${row['cumulative_pnl']:>8.2f}    ${row['option_pnl']:>8.2f}    ${row['hedge_pnl']:>8.2f}  |{bar}")

    # 4. Hedge timing analysis
    print("\n\n--- WHEN DO HEDGES HAPPEN? ---")
    all_hedge_minutes = []
    for (delta, date_str), (curve, hedges) in all_curves.items():
        for h in hedges:
            all_hedge_minutes.append({
                'delta': delta,
                'minutes_to_close': h['minutes_to_close'],
                'time': h['time'],
            })

    if all_hedge_minutes:
        hdf = pd.DataFrame(all_hedge_minutes)
        for delta in [0.50, 0.25, 0.10]:
            sub = hdf[hdf['delta'] == delta]
            if len(sub) == 0:
                continue
            delta_label = f"{int(delta*100)}d"
            print(f"\n  {delta_label}: {len(sub)} total hedges across all days")

            # Bucket by time-to-close
            sub['bucket'] = pd.cut(sub['minutes_to_close'],
                                    bins=[0, 15, 30, 60, 90, 120, 180, 300],
                                    labels=['0-15m', '15-30m', '30-60m', '60-90m',
                                           '90-120m', '120-180m', '180-300m'])
            counts = sub['bucket'].value_counts().sort_index()
            for bucket, count in counts.items():
                pct = count / len(sub) * 100
                bar = '#' * int(pct / 2)
                print(f"    {str(bucket):>10s}: {count:>3d} ({pct:>5.1f}%)  {bar}")

    # 5. Win vs Loss analysis by delta
    print("\n\n--- WIN vs LOSS BREAKDOWN BY DELTA ---")
    for delta in delta_targets:
        delta_label = f"{int(delta*100)}d"
        sub = df[df['target_delta'] == delta]
        wins = sub[sub['daily_pnl'] > 0]
        losses = sub[sub['daily_pnl'] <= 0]

        if len(sub) < 3:
            continue

        print(f"\n  {delta_label} strangle: {len(wins)}W / {len(losses)}L")
        if len(wins) > 0:
            print(f"    WIN  avg: ${wins['daily_pnl'].mean():>8.2f}  hedges={wins['hedge_count'].mean():.1f}  "
                  f"cost=${wins['straddle_cost'].mean():.0f}  "
                  f"1st/mid/last: ${wins['first_third_pnl'].mean():.0f}/${wins['mid_third_pnl'].mean():.0f}/${wins['last_third_pnl'].mean():.0f}")
        if len(losses) > 0:
            print(f"    LOSS avg: ${losses['daily_pnl'].mean():>8.2f}  hedges={losses['hedge_count'].mean():.1f}  "
                  f"cost=${losses['straddle_cost'].mean():.0f}  "
                  f"1st/mid/last: ${losses['first_third_pnl'].mean():.0f}/${losses['mid_third_pnl'].mean():.0f}/${losses['last_third_pnl'].mean():.0f}")

    # 6. Peak/trough timing
    print("\n\n--- PEAK AND TROUGH TIMING ---")
    print("(When does P&L hit its highest and lowest point during the day?)")
    for delta in [0.50, 0.25, 0.10]:
        delta_label = f"{int(delta*100)}d"
        sub = df[df['target_delta'] == delta]
        if len(sub) == 0:
            continue

        print(f"\n  {delta_label}:")
        print(f"    Avg peak P&L: ${sub['peak_pnl'].mean():.2f} at avg time {sub['peak_time'].mode().iloc[0] if len(sub) > 0 else 'N/A'}")
        print(f"    Avg trough P&L: ${sub['trough_pnl'].mean():.2f} at avg time {sub['trough_time'].mode().iloc[0] if len(sub) > 0 else 'N/A'}")

        # List each day
        for _, row in sub.iterrows():
            print(f"      {row['date']}: peak=${row['peak_pnl']:>7.2f} @{row['peak_time']}  "
                  f"trough=${row['trough_pnl']:>7.2f} @{row['trough_time']}  "
                  f"final=${row['daily_pnl']:>7.2f}")

    print("\n\nDone.")


if __name__ == "__main__":
    main()
