"""
BTC 0-DTE Gamma Scalping Backtest - Deribit Data
Fetches BTC-PERPETUAL + DVOL from Deribit public API,
synthesizes option prices via Black-Scholes with market IV,
runs gamma scalping strategy for 1 year.

Author: Skyler Chan | PGI Derivatives
"""

import urllib.request
import json
import numpy as np
import time as time_mod
from datetime import datetime, timedelta
from scipy.stats import norm
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
CONFIG = {
    'contracts_per_straddle': 1,      # 1 BTC per contract
    'delta_threshold': 0.10,          # hedge when |delta| > 0.10 BTC
    'max_btc_position': 5.0,          # max hedge position in BTC
    'check_interval_min': 15,         # check delta every 15 min
    # Deribit fees
    'option_taker_fee': 0.0003,       # 0.03% of underlying
    'perp_taker_fee': 0.0005,         # 0.05% of trade value
    'perp_maker_fee': 0.0000,         # 0% maker
    # Execution
    'option_spread_pct': 0.02,        # 2% of option mid (conservative)
    'perp_spread_usd': 0.50,          # $0.50 spread on BTC-PERP
    # Trading window (UTC)
    'entry_hour': 8,                  # entry at 08:05 UTC (after settlement)
    'entry_minute': 5,
    'exit_hour': 7,                   # exit at 07:50 UTC (before next settlement)
    'exit_minute': 50,
}

# ============================================================
# DERIBIT API
# ============================================================
BASE_URL = 'https://www.deribit.com/api/v2/public'
_request_count = 0

def api_call(method, params={}):
    global _request_count
    url = f'{BASE_URL}/{method}?' + '&'.join(f'{k}={v}' for k, v in params.items())
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                _request_count += 1
                if _request_count % 10 == 0:
                    time_mod.sleep(0.5)  # rate limit
                return data.get('result', data)
        except Exception as e:
            if attempt < 2:
                time_mod.sleep(2)
            else:
                raise


def fetch_perp_candles(start_ms, end_ms, resolution='15'):
    """Fetch BTC-PERPETUAL candles in fixed-size time chunks.
    Uses 15-min resolution by default: 5000 bars * 15 min = ~52 days per chunk.
    For 365 days, needs ~7 chunks.
    """
    all_ticks = []
    all_open = []
    all_high = []
    all_low = []
    all_close = []
    all_volume = []

    res_minutes = int(resolution)
    chunk_ms = 4900 * res_minutes * 60 * 1000  # ~4900 bars per chunk (leave margin)

    cursor = start_ms
    chunk_num = 0
    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        chunk_num += 1

        result = api_call('get_tradingview_chart_data', {
            'instrument_name': 'BTC-PERPETUAL',
            'start_timestamp': cursor,
            'end_timestamp': chunk_end,
            'resolution': resolution
        })

        ticks = result.get('ticks', [])
        if not ticks:
            cursor = chunk_end + 1
            continue

        all_ticks.extend(ticks)
        all_open.extend(result['open'])
        all_high.extend(result['high'])
        all_low.extend(result['low'])
        all_close.extend(result['close'])
        all_volume.extend(result['volume'])

        print(f"    Chunk {chunk_num}: {len(ticks)} bars "
              f"({datetime.utcfromtimestamp(ticks[0]/1000).strftime('%Y-%m-%d')} - "
              f"{datetime.utcfromtimestamp(ticks[-1]/1000).strftime('%Y-%m-%d')})")

        cursor = ticks[-1] + 1
        if len(ticks) < 100:
            break  # caught up to current time

    return {
        'ticks': all_ticks,
        'open': all_open,
        'high': all_high,
        'low': all_low,
        'close': all_close,
        'volume': all_volume
    }


def fetch_dvol(start_ms, end_ms):
    """Fetch DVOL (BTC implied vol index) hourly in chunks"""
    all_data = []
    chunk_ms = 900 * 3600 * 1000  # ~900 hours per chunk
    cursor = start_ms

    chunk_num = 0
    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        chunk_num += 1

        result = api_call('get_volatility_index_data', {
            'currency': 'BTC',
            'start_timestamp': cursor,
            'end_timestamp': chunk_end,
            'resolution': '3600'
        })

        data = result.get('data', [])
        if not data:
            cursor = chunk_end + 1
            continue

        all_data.extend(data)
        cursor = data[-1][0] + 1

    return all_data


# ============================================================
# BLACK-SCHOLES
# ============================================================
def bs_price_and_greeks(S, K, T, r, sigma, option_type):
    """Black-Scholes price + Greeks. Returns dict."""
    if T <= 0:
        if option_type == 'call':
            intrinsic = max(S - K, 0)
            delta = 1.0 if S > K else 0.0
        else:
            intrinsic = max(K - S, 0)
            delta = -1.0 if S < K else 0.0
        return {'price': intrinsic, 'delta': delta, 'gamma': 0.0, 'theta': 0.0}

    if sigma <= 0.001:
        sigma = 0.001

    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)

    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))

    if option_type == 'call':
        theta = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) -
                 r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        theta = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) +
                 r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365

    return {'price': max(price, 0), 'delta': delta, 'gamma': gamma, 'theta': theta}


# ============================================================
# EXECUTION MODEL (DERIBIT)
# ============================================================
def execute_option_trade(mid_price, side, spot_price, config):
    """Execute option trade with spread + Deribit fees. Returns (fill_price, fee_usd)."""
    spread = mid_price * config['option_spread_pct']
    if side == 'BUY':
        fill = mid_price + spread / 2
    else:
        fill = mid_price - spread / 2
        fill = max(fill, 0)

    # Deribit option fee: 0.03% of underlying per contract
    fee_usd = spot_price * config['option_taker_fee']
    return fill, fee_usd


def execute_perp_trade(price, size_btc, side, config):
    """Execute BTC-PERPETUAL trade. Returns (fill_price, fee_usd)."""
    half_spread = config['perp_spread_usd'] / 2
    if side == 'BUY':
        fill = price + half_spread
    else:
        fill = price - half_spread

    trade_value = abs(size_btc) * fill
    fee = trade_value * config['perp_taker_fee']
    return fill, fee


# ============================================================
# STRATEGY: GAMMA SCALPING
# ============================================================
def run_single_day(day_candles, iv_at_entry, day_date, config):
    """
    Run gamma scalping for one 24h session (08:05 to 07:50 UTC).

    day_candles: list of (timestamp_ms, open, high, low, close, volume)
    iv_at_entry: annualized IV from DVOL (as decimal, e.g. 0.50)

    Returns dict with day results or None.
    """
    if len(day_candles) < 20:
        return None

    r = 0.045  # risk-free rate

    # Get entry price
    entry_candle = day_candles[0]
    spot_entry = entry_candle[4]  # close price
    strike = round(spot_entry / 1000) * 1000  # nearest $1000

    # Time to expiry: from entry to next 08:00 UTC
    entry_ts = entry_candle[0] / 1000
    entry_dt = datetime.utcfromtimestamp(entry_ts)
    # Expiry is next day 08:00 UTC
    expiry_dt = (entry_dt.replace(hour=8, minute=0, second=0) + timedelta(days=1))
    T_entry = (expiry_dt - entry_dt).total_seconds() / (365.25 * 24 * 3600)

    sigma = iv_at_entry

    # Price ATM straddle at entry
    call_greeks = bs_price_and_greeks(spot_entry, strike, T_entry, r, sigma, 'call')
    put_greeks = bs_price_and_greeks(spot_entry, strike, T_entry, r, sigma, 'put')

    call_mid = call_greeks['price']
    put_mid = put_greeks['price']

    if call_mid < 1 or put_mid < 1:
        return None  # prices too low, skip

    # Execute entry
    contracts = config['contracts_per_straddle']
    call_fill, call_fee = execute_option_trade(call_mid, 'BUY', spot_entry, config)
    put_fill, put_fee = execute_option_trade(put_mid, 'BUY', spot_entry, config)

    entry_cost = (call_fill + put_fill) * contracts  # USD cost for straddle
    total_fees = (call_fee + put_fee) * contracts

    # State
    btc_hedge_position = 0.0  # BTC held for hedging
    hedge_avg_cost = 0.0
    hedge_count = 0
    hedge_pnl = 0.0

    # Main loop
    for i, candle in enumerate(day_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.utcfromtimestamp(ts_ms / 1000)

        # Time to expiry
        T = (expiry_dt - current_dt).total_seconds() / (365.25 * 24 * 3600)
        T = max(T, 1e-8)

        # Reprice options
        call_g = bs_price_and_greeks(spot, strike, T, r, sigma, 'call')
        put_g = bs_price_and_greeks(spot, strike, T, r, sigma, 'put')

        # Portfolio delta (in BTC): call_delta + put_delta + hedge
        # Each contract is for 1 BTC
        portfolio_delta = (call_g['delta'] + put_g['delta']) * contracts + btc_hedge_position

        # Check if hedge needed
        if abs(portfolio_delta) > config['delta_threshold']:
            hedge_size = -portfolio_delta  # neutralize

            # Position limit
            new_pos = btc_hedge_position + hedge_size
            max_pos = config['max_btc_position']
            if abs(new_pos) > max_pos:
                if hedge_size > 0:
                    hedge_size = max_pos - btc_hedge_position
                else:
                    hedge_size = -max_pos - btc_hedge_position

            if abs(hedge_size) > 0.001:
                side = 'BUY' if hedge_size > 0 else 'SELL'
                fill_price, fee = execute_perp_trade(spot, abs(hedge_size), side, config)

                # Track hedge P&L via avg cost
                if btc_hedge_position == 0:
                    hedge_avg_cost = fill_price
                elif np.sign(btc_hedge_position) == np.sign(btc_hedge_position + hedge_size):
                    # Adding to position
                    total_val = btc_hedge_position * hedge_avg_cost + hedge_size * fill_price
                    hedge_avg_cost = total_val / (btc_hedge_position + hedge_size)
                else:
                    # Reducing or flipping position
                    # Realize P&L on closed portion
                    closed = min(abs(hedge_size), abs(btc_hedge_position))
                    if btc_hedge_position > 0:
                        hedge_pnl += closed * (fill_price - hedge_avg_cost)
                    else:
                        hedge_pnl += closed * (hedge_avg_cost - fill_price)

                    remaining = btc_hedge_position + hedge_size
                    if abs(remaining) > 0.001 and np.sign(remaining) != np.sign(btc_hedge_position):
                        hedge_avg_cost = fill_price  # new direction

                btc_hedge_position += hedge_size
                total_fees += fee
                hedge_count += 1

    # Exit: close everything at last candle
    exit_candle = day_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.utcfromtimestamp(exit_candle[0] / 1000)

    T_exit = (expiry_dt - exit_dt).total_seconds() / (365.25 * 24 * 3600)
    T_exit = max(T_exit, 1e-8)

    call_exit = bs_price_and_greeks(spot_exit, strike, T_exit, r, sigma, 'call')
    put_exit = bs_price_and_greeks(spot_exit, strike, T_exit, r, sigma, 'put')

    call_exit_fill, call_exit_fee = execute_option_trade(call_exit['price'], 'SELL', spot_exit, config)
    put_exit_fill, put_exit_fee = execute_option_trade(put_exit['price'], 'SELL', spot_exit, config)

    exit_proceeds = (call_exit_fill + put_exit_fill) * contracts
    total_fees += (call_exit_fee + put_exit_fee) * contracts

    # Close hedge position
    if abs(btc_hedge_position) > 0.001:
        side = 'SELL' if btc_hedge_position > 0 else 'BUY'
        fill_price, fee = execute_perp_trade(spot_exit, abs(btc_hedge_position), side, config)

        if btc_hedge_position > 0:
            hedge_pnl += btc_hedge_position * (fill_price - hedge_avg_cost)
        else:
            hedge_pnl += abs(btc_hedge_position) * (hedge_avg_cost - fill_price)

        total_fees += fee

    # P&L
    straddle_pnl = exit_proceeds - entry_cost  # option P&L
    total_pnl = straddle_pnl + hedge_pnl - total_fees

    return {
        'date': day_date,
        'spot_entry': spot_entry,
        'spot_exit': spot_exit,
        'strike': strike,
        'iv': sigma,
        'straddle_entry': entry_cost,
        'straddle_exit': exit_proceeds,
        'straddle_pnl': straddle_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': total_pnl,
        'hedge_count': hedge_count,
        'bars': len(day_candles),
        'spot_move_pct': (spot_exit - spot_entry) / spot_entry * 100,
    }


# ============================================================
# MAIN BACKTEST
# ============================================================
def main():
    print("=" * 70)
    print("BTC 0-DTE GAMMA SCALPING BACKTEST - DERIBIT")
    print("=" * 70)
    print(f"Strategy: Long ATM Straddle + Delta-Neutral Hedging (BTC-PERP)")
    print(f"Delta Threshold: +/-{CONFIG['delta_threshold']} BTC")
    print(f"Check Interval: {CONFIG['check_interval_min']}min (15-min candles)")
    print(f"Window: 08:05 - 07:50 UTC (24h sessions)")
    print(f"IV Source: Deribit DVOL (market implied volatility)")
    print()

    # --- STEP 1: Fetch data ---
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    year_ago_ms = now_ms - 365 * 86400000

    print("[1/3] Fetching BTC-PERPETUAL 15-min candles (1 year)...")
    perp_data = fetch_perp_candles(year_ago_ms, now_ms, resolution='15')
    print(f"  Fetched {len(perp_data['ticks'])} candles")
    if perp_data['ticks']:
        first_dt = datetime.utcfromtimestamp(perp_data['ticks'][0] / 1000)
        last_dt = datetime.utcfromtimestamp(perp_data['ticks'][-1] / 1000)
        print(f"  Range: {first_dt.strftime('%Y-%m-%d')} to {last_dt.strftime('%Y-%m-%d')}")

    print("\n[2/3] Fetching DVOL (implied volatility index)...")
    dvol_data = fetch_dvol(year_ago_ms, now_ms)
    print(f"  Fetched {len(dvol_data)} hourly DVOL readings")

    # Build DVOL lookup: timestamp -> IV
    dvol_lookup = {}
    for entry in dvol_data:
        ts = entry[0]
        iv_close = entry[4]  # close
        dvol_lookup[ts] = iv_close / 100  # convert to decimal

    # --- STEP 2: Organize into daily sessions ---
    print("\n[3/3] Running backtest...")

    # Group candles by trading day (08:00 to 08:00 UTC)
    daily_sessions = defaultdict(list)
    for i, ts in enumerate(perp_data['ticks']):
        dt = datetime.utcfromtimestamp(ts / 1000)
        # Session date: if before 08:00, belongs to previous day's session
        if dt.hour < 8:
            session_date = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            session_date = dt.strftime('%Y-%m-%d')

        # Only include candles in trading window (08:05 to 07:50)
        session_start = dt.replace(hour=8, minute=5, second=0)
        if dt.hour < 8:
            # Early morning - before 07:50 cutoff
            if dt.hour < 7 or (dt.hour == 7 and dt.minute <= 50):
                daily_sessions[session_date].append(
                    (ts, perp_data['open'][i], perp_data['high'][i],
                     perp_data['low'][i], perp_data['close'][i], perp_data['volume'][i])
                )
        else:
            # After 08:05
            if dt.hour > 8 or (dt.hour == 8 and dt.minute >= 5):
                daily_sessions[session_date].append(
                    (ts, perp_data['open'][i], perp_data['high'][i],
                     perp_data['low'][i], perp_data['close'][i], perp_data['volume'][i])
                )

    sorted_dates = sorted(daily_sessions.keys())
    print(f"  Trading sessions: {len(sorted_dates)}")

    # --- Run day by day ---
    results = []
    skipped = 0

    for day_idx, date_str in enumerate(sorted_dates):
        candles = daily_sessions[date_str]
        if len(candles) < 50:
            skipped += 1
            continue

        # Find nearest DVOL reading for this day
        target_ts = int(datetime.strptime(date_str, '%Y-%m-%d').replace(hour=8).timestamp() * 1000)
        nearest_dvol = None
        min_diff = float('inf')
        for dvol_ts, dvol_iv in dvol_lookup.items():
            diff = abs(dvol_ts - target_ts)
            if diff < min_diff:
                min_diff = diff
                nearest_dvol = dvol_iv

        if nearest_dvol is None or nearest_dvol < 0.05:
            nearest_dvol = 0.50  # fallback

        # Run strategy
        result = run_single_day(candles, nearest_dvol, date_str, CONFIG)

        if result:
            results.append(result)
            if (day_idx + 1) % 30 == 0:
                cum_pnl = sum(r['total_pnl'] for r in results)
                print(f"  [{day_idx+1}/{len(sorted_dates)}] {date_str} | "
                      f"Day P&L: ${result['total_pnl']:+,.0f} | "
                      f"Cum P&L: ${cum_pnl:+,.0f} | "
                      f"IV: {result['iv']:.0%}")
        else:
            skipped += 1

    # --- STEP 3: Results ---
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    if not results:
        print("No results generated.")
        return

    pnls = [r['total_pnl'] for r in results]
    straddle_pnls = [r['straddle_pnl'] for r in results]
    hedge_pnls = [r['hedge_pnl'] for r in results]
    fees = [r['total_fees'] for r in results]
    hedges = [r['hedge_count'] for r in results]
    ivs = [r['iv'] for r in results]
    moves = [abs(r['spot_move_pct']) for r in results]

    total_pnl = sum(pnls)
    avg_daily = np.mean(pnls)
    std_daily = np.std(pnls)
    sharpe = (avg_daily / std_daily) * np.sqrt(365) if std_daily > 0 else 0
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
    max_dd = 0
    peak = 0
    cum = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    print(f"\nPeriod: {results[0]['date']} to {results[-1]['date']}")
    print(f"Trading Days: {len(results)} (skipped: {skipped})")
    print()

    print("--- P&L Summary ---")
    print(f"Total P&L:          ${total_pnl:+,.2f}")
    print(f"Avg Daily P&L:      ${avg_daily:+,.2f}")
    print(f"Median Daily P&L:   ${np.median(pnls):+,.2f}")
    print(f"Std Daily P&L:      ${std_daily:,.2f}")
    print(f"Best Day:           ${max(pnls):+,.2f}")
    print(f"Worst Day:          ${min(pnls):+,.2f}")
    print(f"Max Drawdown:       ${max_dd:,.2f}")
    print()

    print("--- Performance Metrics ---")
    print(f"Win Rate:           {win_rate:.1%}")
    print(f"Sharpe Ratio:       {sharpe:.2f}")
    print(f"Profit Factor:      {sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0)):.2f}" if sum(p for p in pnls if p < 0) != 0 else "Profit Factor: inf")
    print(f"Avg Win:            ${np.mean([p for p in pnls if p > 0]):+,.2f}" if any(p > 0 for p in pnls) else "")
    print(f"Avg Loss:           ${np.mean([p for p in pnls if p < 0]):+,.2f}" if any(p < 0 for p in pnls) else "")
    print()

    print("--- P&L Decomposition ---")
    print(f"Straddle P&L:       ${sum(straddle_pnls):+,.2f}")
    print(f"Hedge P&L:          ${sum(hedge_pnls):+,.2f}")
    print(f"Total Fees:         ${sum(fees):,.2f}")
    print(f"Avg Fees/Day:       ${np.mean(fees):,.2f}")
    print()

    print("--- Hedging Stats ---")
    print(f"Avg Hedges/Day:     {np.mean(hedges):.1f}")
    print(f"Total Hedges:       {sum(hedges)}")
    print()

    print("--- Market Context ---")
    print(f"Avg DVOL (IV):      {np.mean(ivs):.1%}")
    print(f"Avg Daily |Move|:   {np.mean(moves):.2f}%")
    print(f"BTC Start:          ${results[0]['spot_entry']:,.0f}")
    print(f"BTC End:            ${results[-1]['spot_exit']:,.0f}")
    print(f"BTC Return:         {(results[-1]['spot_exit'] / results[0]['spot_entry'] - 1):.1%}")
    print()

    # Monthly breakdown
    print("--- Monthly Breakdown ---")
    monthly = defaultdict(list)
    for r in results:
        month = r['date'][:7]
        monthly[month].append(r['total_pnl'])

    print(f"{'Month':<10} {'Days':>5} {'P&L':>12} {'Avg':>10} {'WinRate':>8}")
    print("-" * 50)
    for month in sorted(monthly.keys()):
        mpnls = monthly[month]
        mwin = sum(1 for p in mpnls if p > 0) / len(mpnls)
        print(f"{month:<10} {len(mpnls):>5} ${sum(mpnls):>+10,.0f} ${np.mean(mpnls):>+9,.0f} {mwin:>7.0%}")

    print("\n" + "=" * 70)

    # Save results to JSON
    import os
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(output_dir, f'btc_backtest_results_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json')

    save_results = {
        'config': CONFIG,
        'metrics': {
            'total_pnl': total_pnl,
            'avg_daily_pnl': avg_daily,
            'std_daily_pnl': std_daily,
            'sharpe_ratio': sharpe,
            'win_rate': win_rate,
            'max_drawdown': max_dd,
            'total_fees': sum(fees),
            'trading_days': len(results),
            'total_hedges': sum(hedges),
            'avg_iv': float(np.mean(ivs)),
        },
        'daily_results': [{
            'date': r['date'],
            'total_pnl': r['total_pnl'],
            'straddle_pnl': r['straddle_pnl'],
            'hedge_pnl': r['hedge_pnl'],
            'fees': r['total_fees'],
            'hedge_count': r['hedge_count'],
            'spot_entry': r['spot_entry'],
            'spot_exit': r['spot_exit'],
            'strike': r['strike'],
            'iv': r['iv'],
            'spot_move_pct': r['spot_move_pct'],
        } for r in results]
    }

    with open(output_file, 'w') as f:
        json.dump(save_results, f, indent=2)
    print(f"Results saved to: {output_file}")


if __name__ == '__main__':
    main()
