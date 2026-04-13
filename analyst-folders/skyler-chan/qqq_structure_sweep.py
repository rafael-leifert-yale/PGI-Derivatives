"""
QQQ Gamma Scalping: Structure & Timing Sweep
Tests straddles vs strangles, different entry/exit times,
and analyzes what drives wins vs losses.
"""

import sys, os, json, warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
from scipy import stats

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.data_engine import DataEngine
from backtest.trading_engine import TradingEngine, Position
from backtest.execution_model import ExecutionModel
from utils.greeks import GreeksCalculator


class StructureSweepEngine:
    """
    Extended trading engine that supports strangles and flexible entry/exit times.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.execution_model = ExecutionModel(config)

    def run_day(self, merged: pd.DataFrame, day_data: Dict,
                strangle_width: float, entry_minutes: int, exit_minutes: int) -> Optional[Dict]:
        """
        Run one day with specific structure and timing.

        Args:
            merged: merged minute-bar DataFrame
            day_data: dict from DataEngine.fetch_day_data
            strangle_width: 0 = straddle, N = put is N below ATM, call is N above
            entry_minutes: minutes after 9:30 to enter (e.g. 1 = 9:31, 30 = 10:00)
            exit_minutes: minutes before 16:00 to exit (e.g. 5 = 15:55, 60 = 15:00)
        """
        date = day_data['date']
        base_strike = day_data['atm_strike']
        rfr = day_data['risk_free_rate']

        call_strike = base_strike + strangle_width
        put_strike = base_strike - strangle_width

        # Filter to entry/exit window
        market_open = date.replace(hour=9, minute=30)
        entry_time = market_open + timedelta(minutes=entry_minutes)
        exit_time = date.replace(hour=16, minute=0) - timedelta(minutes=exit_minutes)

        window = merged[
            (merged['timestamp'] >= entry_time) &
            (merged['timestamp'] <= exit_time)
        ].copy()

        if len(window) < 20:
            return None

        # For strangles, we need to estimate option prices at different strikes
        # We'll use Black-Scholes to adjust from ATM prices
        # This is approximate but captures the key dynamics

        entry_row = window.iloc[0]
        entry_stock = entry_row['stock_close']
        entry_time_ts = entry_row['timestamp']

        # Time to expiry at entry
        market_close_dt = date.replace(hour=16, minute=0)
        tte_entry = (market_close_dt - entry_time_ts).total_seconds() / (365.25 * 24 * 3600)
        tte_entry = max(tte_entry, 1e-6)

        # Estimate IV from ATM straddle price
        atm_call_price = entry_row['call_close']
        atm_put_price = entry_row['put_close']

        # Back out IV from ATM call
        try:
            iv = GreeksCalculator.get_iv_from_market(
                atm_call_price, entry_stock, base_strike, tte_entry, rfr, 'call'
            )
        except:
            iv = 0.25

        iv = max(0.05, min(iv, 3.0))

        # Price the strangle legs using BS
        if strangle_width == 0:
            call_entry_price = atm_call_price
            put_entry_price = atm_put_price
        else:
            call_greeks = GreeksCalculator.black_scholes(
                entry_stock, call_strike, tte_entry, rfr, iv, 'call'
            )
            put_greeks = GreeksCalculator.black_scholes(
                entry_stock, put_strike, tte_entry, rfr, iv, 'put'
            )
            call_entry_price = call_greeks['price']
            put_entry_price = put_greeks['price']

        if call_entry_price < 0.01 or put_entry_price < 0.01:
            return None

        straddle_cost = (call_entry_price + put_entry_price) * 100

        # Initialize tracking
        stock_position = 0
        stock_avg_cost = 0.0
        realized_pnl = -straddle_cost  # pay for the straddle
        total_costs = 0.0
        hedge_count = 0
        hedge_details = []

        # Track intraday metrics
        stock_prices = []
        deltas_over_time = []
        max_stock_pos = 0

        delta_threshold = self.config.get('delta_threshold', 0.30) * 100

        for idx, (_, row) in enumerate(window.iterrows()):
            timestamp = row['timestamp']
            stock_price = row['stock_close']
            stock_prices.append(stock_price)

            # Time to expiry
            tte = (market_close_dt - timestamp).total_seconds() / (365.25 * 24 * 3600)
            tte = max(tte, 1e-6)

            # Calculate portfolio delta
            call_greeks = GreeksCalculator.black_scholes(
                stock_price, call_strike, tte, rfr, iv, 'call'
            )
            put_greeks = GreeksCalculator.black_scholes(
                stock_price, put_strike, tte, rfr, iv, 'put'
            )

            option_delta = (call_greeks['delta'] + put_greeks['delta']) * 100
            portfolio_delta = option_delta + stock_position
            deltas_over_time.append(portfolio_delta)

            option_gamma = (call_greeks['gamma'] + put_greeks['gamma']) * 100
            option_theta = (call_greeks['theta'] + put_greeks['theta'])

            # Check if hedge needed
            if abs(portfolio_delta) > delta_threshold:
                shares_needed = -round(portfolio_delta)
                max_pos = self.config.get('max_stock_position', 500)
                if abs(stock_position + shares_needed) > max_pos:
                    available = max_pos - abs(stock_position)
                    shares_needed = int(np.sign(shares_needed) * min(abs(shares_needed), available))

                if shares_needed != 0:
                    side = 'BUY' if shares_needed > 0 else 'SELL'
                    vol = int(row.get('stock_volume', 10000))

                    execution = self.execution_model.execute_trade(
                        trade={'asset_type': 'stock', 'side': side,
                               'quantity': abs(shares_needed), 'mid_price': stock_price},
                        market_data={'volume': vol, 'current_time': timestamp.time()}
                    )

                    filled_price = execution['executed_price']
                    cost = execution['transaction_cost']

                    if side == 'BUY':
                        realized_pnl -= execution['total_cost']
                    else:
                        realized_pnl += execution['total_cost']

                    total_costs += cost
                    stock_position += shares_needed
                    max_stock_pos = max(max_stock_pos, abs(stock_position))
                    hedge_count += 1

                    hedge_details.append({
                        'time': timestamp.strftime('%H:%M'),
                        'side': side,
                        'shares': abs(shares_needed),
                        'price': round(filled_price, 2),
                        'stock_pos': stock_position,
                        'delta_before': round(portfolio_delta, 1),
                    })

        # Exit: price options at exit
        exit_row = window.iloc[-1]
        exit_stock = exit_row['stock_close']
        exit_time_ts = exit_row['timestamp']
        tte_exit = (market_close_dt - exit_time_ts).total_seconds() / (365.25 * 24 * 3600)
        tte_exit = max(tte_exit, 1e-6)

        call_exit = GreeksCalculator.black_scholes(
            exit_stock, call_strike, tte_exit, rfr, iv, 'call'
        )
        put_exit = GreeksCalculator.black_scholes(
            exit_stock, put_strike, tte_exit, rfr, iv, 'put'
        )

        call_exit_price = call_exit['price']
        put_exit_price = put_exit['price']
        straddle_exit_value = (call_exit_price + put_exit_price) * 100

        # Close options
        realized_pnl += straddle_exit_value

        # Close stock position
        if stock_position != 0:
            side = 'SELL' if stock_position > 0 else 'BUY'
            execution = self.execution_model.execute_trade(
                trade={'asset_type': 'stock', 'side': side,
                       'quantity': abs(stock_position), 'mid_price': exit_stock},
                market_data={'volume': 10000, 'current_time': exit_time_ts.time()}
            )
            if side == 'SELL':
                realized_pnl += execution['total_cost']
            else:
                realized_pnl -= execution['total_cost']
            total_costs += execution['transaction_cost']

        # Compute analytics
        prices = np.array(stock_prices)
        returns = np.diff(prices) / prices[:-1]

        # Oscillation metric: sum of absolute minute-to-minute moves / range
        total_path_length = np.sum(np.abs(np.diff(prices)))
        price_range = prices.max() - prices.min()
        oscillation_ratio = total_path_length / price_range if price_range > 0 else 0

        # Trend metric: |close - open| / range
        trend_ratio = abs(prices[-1] - prices[0]) / price_range if price_range > 0 else 0

        # Autocorrelation of returns (negative = mean-reverting)
        if len(returns) > 10:
            autocorr = np.corrcoef(returns[:-1], returns[1:])[0, 1]
        else:
            autocorr = 0

        return {
            'date': date.strftime('%Y-%m-%d'),
            'weekday': date.strftime('%A'),
            'strangle_width': strangle_width,
            'entry_time': entry_time.strftime('%H:%M'),
            'exit_time': exit_time.strftime('%H:%M'),
            'holding_minutes': len(window),
            # Prices
            'qqq_open': round(prices[0], 2),
            'qqq_close': round(prices[-1], 2),
            'qqq_high': round(prices.max(), 2),
            'qqq_low': round(prices.min(), 2),
            'qqq_range': round(price_range, 2),
            'qqq_range_pct': round(price_range / prices[0] * 100, 3),
            'qqq_move': round(prices[-1] - prices[0], 2),
            'qqq_move_pct': round((prices[-1] - prices[0]) / prices[0] * 100, 3),
            # Structure
            'call_strike': call_strike,
            'put_strike': put_strike,
            'call_entry': round(call_entry_price, 2),
            'put_entry': round(put_entry_price, 2),
            'straddle_cost': round(straddle_cost, 2),
            'call_exit': round(call_exit_price, 2),
            'put_exit': round(put_exit_price, 2),
            'straddle_exit': round(straddle_exit_value, 2),
            'straddle_pnl': round(straddle_exit_value - straddle_cost, 2),
            'iv_estimate': round(iv, 4),
            # Hedging
            'hedge_count': hedge_count,
            'max_stock_position': max_stock_pos,
            'transaction_costs': round(total_costs, 2),
            # P&L
            'daily_pnl': round(realized_pnl, 2),
            # Market microstructure
            'oscillation_ratio': round(oscillation_ratio, 2),
            'trend_ratio': round(trend_ratio, 4),
            'return_autocorr': round(autocorr, 4),
            'realized_vol_intraday': round(returns.std() * np.sqrt(252 * 390) * 100, 2),
            'total_path_length': round(total_path_length, 2),
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

    engine = StructureSweepEngine(config)
    data_engine = DataEngine(symbol='QQQ', config={})

    # Get trading days - use more days for statistical power
    start_date = '2026-02-01'
    end_date = '2026-04-09'
    trading_days = data_engine.get_0dte_trading_days(start_date, end_date)

    # Sample 15 days evenly
    if len(trading_days) > 15:
        indices = np.linspace(0, len(trading_days) - 1, 15, dtype=int)
        trading_days = [trading_days[i] for i in indices]

    print(f"Testing {len(trading_days)} trading days\n")

    # Parameter grid
    strangle_widths = [0, 1, 2, 3, 5]  # 0=straddle, N=OTM by $N each side
    entry_offsets = [1, 15, 30, 60]      # minutes after 9:30
    exit_offsets = [5, 30, 60, 90, 120]  # minutes before 16:00

    # Pre-fetch and merge all day data
    print("Fetching data for all days...")
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

            if len(merged) >= 100:
                day_cache[day] = (merged, data)
                print(f"  {day.date()} OK ({len(merged)} bars)")

        except Exception as e:
            print(f"  {day.date()} SKIP: {e}")
            continue

    print(f"\n{len(day_cache)} days cached\n")

    # Run sweep
    all_results = []
    total_combos = len(strangle_widths) * len(entry_offsets) * len(exit_offsets)
    combo_num = 0

    for sw in strangle_widths:
        for entry_off in entry_offsets:
            for exit_off in exit_offsets:
                combo_num += 1
                label = f"w={sw} entry=+{entry_off}m exit=-{exit_off}m"

                day_pnls = []
                for day, (merged, data) in day_cache.items():
                    result = engine.run_day(merged, data, sw, entry_off, exit_off)
                    if result:
                        all_results.append(result)
                        day_pnls.append(result['daily_pnl'])

                if day_pnls:
                    avg = np.mean(day_pnls)
                    status = "+" if avg > 0 else "-"
                    print(f"  [{combo_num}/{total_combos}] {label}  "
                          f"avg=${avg:>8.2f}  n={len(day_pnls)}")

    df = pd.DataFrame(all_results)
    df.to_csv(os.path.join(output_dir, 'qqq_structure_sweep.csv'), index=False)

    # ==================== ANALYSIS ====================
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    # 1. Structure comparison: straddle vs strangle
    print("\n--- STRADDLE VS STRANGLE ---")
    struct_group = df.groupby('strangle_width').agg({
        'daily_pnl': ['mean', 'std', 'count'],
        'straddle_cost': 'mean',
        'straddle_pnl': 'mean',
        'hedge_count': 'mean',
        'transaction_costs': 'mean',
    }).round(2)
    struct_group.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_cost',
                            'avg_straddle_pnl', 'avg_hedges', 'avg_txn_costs']
    struct_group['sharpe'] = (struct_group['avg_pnl'] / struct_group['std_pnl'] * np.sqrt(252)).round(2)
    struct_group['win_rate'] = df.groupby('strangle_width')['daily_pnl'].apply(
        lambda x: (x > 0).mean()
    ).round(3)
    print(struct_group.to_string())

    # 2. Entry time comparison
    print("\n\n--- ENTRY TIME ---")
    entry_group = df.groupby('entry_time').agg({
        'daily_pnl': ['mean', 'std', 'count'],
        'hedge_count': 'mean',
        'holding_minutes': 'mean',
    }).round(2)
    entry_group.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_hedges', 'avg_holding']
    entry_group['sharpe'] = (entry_group['avg_pnl'] / entry_group['std_pnl'] * np.sqrt(252)).round(2)
    entry_group['win_rate'] = df.groupby('entry_time')['daily_pnl'].apply(
        lambda x: (x > 0).mean()
    ).round(3)
    print(entry_group.to_string())

    # 3. Exit time comparison
    print("\n\n--- EXIT TIME ---")
    exit_group = df.groupby('exit_time').agg({
        'daily_pnl': ['mean', 'std', 'count'],
        'hedge_count': 'mean',
        'holding_minutes': 'mean',
    }).round(2)
    exit_group.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_hedges', 'avg_holding']
    exit_group['sharpe'] = (exit_group['avg_pnl'] / exit_group['std_pnl'] * np.sqrt(252)).round(2)
    exit_group['win_rate'] = df.groupby('exit_time')['daily_pnl'].apply(
        lambda x: (x > 0).mean()
    ).round(3)
    print(exit_group.to_string())

    # 4. What drives P&L? Regression analysis
    print("\n\n--- WHAT DRIVES P&L (straddle only, entry=9:31) ---")
    baseline = df[(df['strangle_width'] == 0) & (df['entry_time'] == '09:31')].copy()

    if len(baseline) > 5:
        features = ['qqq_range_pct', 'oscillation_ratio', 'trend_ratio',
                     'return_autocorr', 'realized_vol_intraday', 'hedge_count',
                     'straddle_cost', 'iv_estimate']

        print(f"\nCorrelation with daily P&L (n={len(baseline)}):")
        for feat in features:
            if feat in baseline.columns and baseline[feat].std() > 0:
                corr = baseline[feat].corr(baseline['daily_pnl'])
                print(f"  {feat:30s}  r = {corr:+.3f}")

        # Win vs loss comparison
        wins = baseline[baseline['daily_pnl'] > 0]
        losses = baseline[baseline['daily_pnl'] <= 0]

        print(f"\nWIN days ({len(wins)}) vs LOSS days ({len(losses)}):")
        for feat in ['qqq_range_pct', 'oscillation_ratio', 'trend_ratio',
                      'return_autocorr', 'hedge_count', 'straddle_cost', 'iv_estimate']:
            if feat in baseline.columns:
                w_mean = wins[feat].mean() if len(wins) > 0 else 0
                l_mean = losses[feat].mean() if len(losses) > 0 else 0
                print(f"  {feat:30s}  WIN={w_mean:>8.3f}  LOSS={l_mean:>8.3f}")

    # 5. Best and worst combos
    print("\n\n--- TOP 10 COMBOS (by avg P&L) ---")
    combo_stats = df.groupby(['strangle_width', 'entry_time', 'exit_time']).agg({
        'daily_pnl': ['mean', 'std', 'count'],
        'hedge_count': 'mean',
        'straddle_cost': 'mean',
    }).round(2)
    combo_stats.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_hedges', 'avg_cost']
    combo_stats['sharpe'] = (combo_stats['avg_pnl'] / combo_stats['std_pnl'] * np.sqrt(252)).round(2)
    combo_stats = combo_stats[combo_stats['n'] >= 5]  # minimum sample
    top10 = combo_stats.nlargest(10, 'avg_pnl')
    print(top10.to_string())

    print("\n\n--- BOTTOM 10 COMBOS (worst avg P&L) ---")
    bottom10 = combo_stats.nsmallest(10, 'avg_pnl')
    print(bottom10.to_string())

    # 6. Holding period analysis
    print("\n\n--- HOLDING PERIOD VS P&L ---")
    baseline_all_struct = df[df['strangle_width'] == 0].copy()
    if len(baseline_all_struct) > 0:
        hp_group = baseline_all_struct.groupby('holding_minutes').agg({
            'daily_pnl': ['mean', 'std'],
            'hedge_count': 'mean',
        }).round(2)
        # Bin by holding period ranges
        baseline_all_struct['hold_bucket'] = pd.cut(
            baseline_all_struct['holding_minutes'],
            bins=[0, 120, 200, 300, 400],
            labels=['<2hr', '2-3.3hr', '3.3-5hr', '5-6.5hr']
        )
        bucket_stats = baseline_all_struct.groupby('hold_bucket', observed=True).agg({
            'daily_pnl': ['mean', 'std', 'count'],
            'hedge_count': 'mean',
        }).round(2)
        bucket_stats.columns = ['avg_pnl', 'std_pnl', 'n', 'avg_hedges']
        bucket_stats['sharpe'] = (bucket_stats['avg_pnl'] / bucket_stats['std_pnl'] * np.sqrt(252)).round(2)
        bucket_stats['win_rate'] = baseline_all_struct.groupby('hold_bucket', observed=True)['daily_pnl'].apply(
            lambda x: (x > 0).mean()
        ).round(3)
        print(bucket_stats.to_string())

    # 7. Day-by-day detail for straddle baseline
    print("\n\n--- DAY-BY-DAY: STRADDLE, ENTRY 9:31, EXIT 15:55 ---")
    day_detail = df[
        (df['strangle_width'] == 0) &
        (df['entry_time'] == '09:31') &
        (df['exit_time'] == '15:55')
    ].sort_values('date')

    if len(day_detail) > 0:
        print(day_detail[[
            'date', 'weekday', 'qqq_range', 'qqq_range_pct', 'qqq_move',
            'straddle_cost', 'straddle_pnl', 'hedge_count',
            'oscillation_ratio', 'trend_ratio', 'return_autocorr',
            'daily_pnl'
        ]].to_string(index=False))

    print("\n\nDone.")


if __name__ == "__main__":
    main()
