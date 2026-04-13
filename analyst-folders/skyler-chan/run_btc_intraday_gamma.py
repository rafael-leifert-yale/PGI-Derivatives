"""
BTC Intraday Gamma Scalping Backtest - 1-Minute Deribit Data
Aggressive short-session gamma scalping using 1-min BTC-PERPETUAL candles.
Runs 1-4 hour sessions across Asian/European/US windows.
Analyzes P&L by session type, IV regime, day of week, and hour.

Author: Skyler Chan | PGI Derivatives
"""

import urllib.request
import json
import os
import numpy as np
import time as time_mod
from datetime import datetime, timedelta
from scipy.stats import norm
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
CONFIG = {
    'contracts': 1,                   # 1 BTC notional per straddle
    'delta_threshold': 0.04,          # hedge when |delta| > 0.04 BTC (tight)
    'max_btc_position': 3.0,          # max hedge position
    'check_interval_min': 3,          # check delta every 3 minutes
    'session_length_hours': 4,        # each session is 4 hours
    'lookback_days': 90,              # fetch 90 days of data
    # Deribit fees
    'option_taker_fee': 0.0003,       # 0.03% of underlying
    'perp_taker_fee': 0.0005,         # 0.05% of trade value
    'perp_maker_fee': 0.0000,         # 0% maker
    # Execution
    'option_spread_pct': 0.015,       # 1.5% of option mid
    'perp_spread_usd': 0.50,          # $0.50 spread on BTC-PERP
    # Session definitions (UTC hours)
    'sessions': {
        'asian':    (0, 8),
        'european': (8, 16),
        'us':       (14, 22),
        'full_24h': (0, 24),
    },
}

# ============================================================
# DERIBIT API
# ============================================================
BASE_URL = 'https://www.deribit.com/api/v2/public'
_request_count = 0


def api_call(method, params=None):
    global _request_count
    if params is None:
        params = {}
    url = f'{BASE_URL}/{method}?' + '&'.join(f'{k}={v}' for k, v in params.items())
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                _request_count += 1
                if _request_count % 10 == 0:
                    time_mod.sleep(0.5)
                return data.get('result', data)
        except Exception as e:
            if attempt < 2:
                time_mod.sleep(2)
            else:
                raise


def fetch_perp_candles_1min(start_ms, end_ms):
    """Fetch BTC-PERPETUAL 1-min candles in chunks.
    Max 5000 bars per call = ~3.47 days of 1-min data.
    For 90 days, needs ~26 chunks.
    """
    all_ticks = []
    all_open = []
    all_high = []
    all_low = []
    all_close = []
    all_volume = []

    # 4900 bars * 1 min * 60s * 1000ms = ~3.4 days per chunk
    chunk_ms = 4900 * 60 * 1000
    cursor = start_ms
    chunk_num = 0

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        chunk_num += 1

        result = api_call('get_tradingview_chart_data', {
            'instrument_name': 'BTC-PERPETUAL',
            'start_timestamp': cursor,
            'end_timestamp': chunk_end,
            'resolution': '1'
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

        dt_start = datetime.utcfromtimestamp(ticks[0] / 1000)
        dt_end = datetime.utcfromtimestamp(ticks[-1] / 1000)
        print(f"    Chunk {chunk_num}: {len(ticks)} bars "
              f"({dt_start.strftime('%Y-%m-%d %H:%M')} - "
              f"{dt_end.strftime('%Y-%m-%d %H:%M')})")

        cursor = ticks[-1] + 1
        if len(ticks) < 100:
            break

    return {
        'ticks': all_ticks,
        'open': all_open,
        'high': all_high,
        'low': all_low,
        'close': all_close,
        'volume': all_volume
    }


def fetch_dvol(start_ms, end_ms):
    """Fetch DVOL (BTC implied vol index) hourly in chunks."""
    all_data = []
    # Use 30-day chunks; always advance cursor by chunk_ms to avoid infinite loop
    chunk_ms = 30 * 24 * 3600 * 1000
    cursor = start_ms

    chunk_num = 0
    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        chunk_num += 1

        try:
            result = api_call('get_volatility_index_data', {
                'currency': 'BTC',
                'start_timestamp': cursor,
                'end_timestamp': chunk_end,
                'resolution': '3600'
            })
        except Exception as e:
            print(f"    DVOL chunk {chunk_num} failed: {e}, skipping")
            cursor = chunk_end + 1
            continue

        data = result.get('data', [])
        if data:
            all_data.extend(data)
            print(f"    DVOL chunk {chunk_num}: {len(data)} readings")

        # Always advance by chunk size
        cursor = chunk_end + 1

    return all_data


# ============================================================
# BLACK-SCHOLES
# ============================================================
def bs_price_and_greeks(S, K, T, r, sigma, option_type):
    """Black-Scholes price + Greeks."""
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
    """Execute option trade with spread + Deribit fees."""
    spread = mid_price * config['option_spread_pct']
    if side == 'BUY':
        fill = mid_price + spread / 2
    else:
        fill = mid_price - spread / 2
        fill = max(fill, 0)

    fee_usd = spot_price * config['option_taker_fee']
    return fill, fee_usd


def execute_perp_trade(price, size_btc, side, config):
    """Execute BTC-PERPETUAL trade."""
    half_spread = config['perp_spread_usd'] / 2
    if side == 'BUY':
        fill = price + half_spread
    else:
        fill = price - half_spread

    trade_value = abs(size_btc) * fill
    fee = trade_value * config['perp_taker_fee']
    return fill, fee


# ============================================================
# REALIZED VOL CALCULATION
# ============================================================
def calc_realized_vol(prices, interval_seconds):
    """Calculate annualized realized vol from a price series.
    Uses log returns with the given sampling interval.
    """
    if len(prices) < 2:
        return 0.0
    log_returns = np.diff(np.log(prices))
    if len(log_returns) == 0:
        return 0.0
    intervals_per_year = (365.25 * 24 * 3600) / interval_seconds
    return float(np.std(log_returns) * np.sqrt(intervals_per_year))


# ============================================================
# STRATEGY: INTRADAY GAMMA SCALPING SESSION
# ============================================================
def run_session(session_candles, iv_at_entry, session_label, config):
    """
    Run gamma scalping for a short intraday session (1-4 hours).

    session_candles: list of (timestamp_ms, open, high, low, close, volume)
    iv_at_entry: annualized IV from DVOL (decimal)
    session_label: string label for this session

    Returns dict with session results or None.
    """
    check_interval = config['check_interval_min']
    min_candles = max(10, check_interval * 3)
    if len(session_candles) < min_candles:
        return None

    r = 0.045  # risk-free rate

    # Entry
    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 500) * 500  # nearest $500

    entry_ts = entry_candle[0] / 1000
    entry_dt = datetime.utcfromtimestamp(entry_ts)

    # Expiry: next 08:00 UTC (Deribit daily settlement)
    expiry_dt = entry_dt.replace(hour=8, minute=0, second=0, microsecond=0)
    if expiry_dt <= entry_dt:
        expiry_dt += timedelta(days=1)
    T_entry = (expiry_dt - entry_dt).total_seconds() / (365.25 * 24 * 3600)
    if T_entry < 1e-6:
        T_entry = 1.0 / (365.25 * 24)  # minimum 1 hour

    sigma = iv_at_entry

    # Price ATM straddle
    call_greeks = bs_price_and_greeks(spot_entry, strike, T_entry, r, sigma, 'call')
    put_greeks = bs_price_and_greeks(spot_entry, strike, T_entry, r, sigma, 'put')

    call_mid = call_greeks['price']
    put_mid = put_greeks['price']

    if call_mid < 1 or put_mid < 1:
        return None

    contracts = config['contracts']
    call_fill, call_fee = execute_option_trade(call_mid, 'BUY', spot_entry, config)
    put_fill, put_fee = execute_option_trade(put_mid, 'BUY', spot_entry, config)

    entry_cost = (call_fill + put_fill) * contracts
    total_fees = (call_fee + put_fee) * contracts

    # State
    btc_hedge_position = 0.0
    hedge_avg_cost = 0.0
    hedge_count = 0
    hedge_pnl = 0.0

    # Track prices for realized vol
    price_series = []

    # Theoretical gamma P&L accumulator
    gamma_pnl_theoretical = 0.0
    prev_spot = spot_entry

    # Main loop: check every check_interval_min minutes
    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.utcfromtimestamp(ts_ms / 1000)

        price_series.append(spot)

        # Theoretical gamma P&L: 0.5 * gamma * (dS)^2
        T_now = (expiry_dt - current_dt).total_seconds() / (365.25 * 24 * 3600)
        T_now = max(T_now, 1e-8)
        call_g = bs_price_and_greeks(spot, strike, T_now, r, sigma, 'call')
        put_g = bs_price_and_greeks(spot, strike, T_now, r, sigma, 'put')
        straddle_gamma = (call_g['gamma'] + put_g['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theoretical += 0.5 * straddle_gamma * dS**2
        prev_spot = spot

        # Only check delta at check intervals
        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        # Portfolio delta
        portfolio_delta = (call_g['delta'] + put_g['delta']) * contracts + btc_hedge_position

        # Hedge if needed
        if abs(portfolio_delta) > config['delta_threshold']:
            hedge_size = -portfolio_delta

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

                if btc_hedge_position == 0:
                    hedge_avg_cost = fill_price
                elif np.sign(btc_hedge_position) == np.sign(btc_hedge_position + hedge_size):
                    total_val = btc_hedge_position * hedge_avg_cost + hedge_size * fill_price
                    hedge_avg_cost = total_val / (btc_hedge_position + hedge_size)
                else:
                    closed = min(abs(hedge_size), abs(btc_hedge_position))
                    if btc_hedge_position > 0:
                        hedge_pnl += closed * (fill_price - hedge_avg_cost)
                    else:
                        hedge_pnl += closed * (hedge_avg_cost - fill_price)

                    remaining = btc_hedge_position + hedge_size
                    if abs(remaining) > 0.001 and np.sign(remaining) != np.sign(btc_hedge_position):
                        hedge_avg_cost = fill_price

                btc_hedge_position += hedge_size
                total_fees += fee
                hedge_count += 1

    # Exit: close at last candle
    exit_candle = session_candles[-1]
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

    # Close hedge
    if abs(btc_hedge_position) > 0.001:
        side = 'SELL' if btc_hedge_position > 0 else 'BUY'
        fill_price, fee = execute_perp_trade(spot_exit, abs(btc_hedge_position), side, config)

        if btc_hedge_position > 0:
            hedge_pnl += btc_hedge_position * (fill_price - hedge_avg_cost)
        else:
            hedge_pnl += abs(btc_hedge_position) * (hedge_avg_cost - fill_price)

        total_fees += fee

    # P&L
    straddle_pnl = exit_proceeds - entry_cost
    total_pnl = straddle_pnl + hedge_pnl - total_fees

    # Realized vol for this session (1-min intervals = 60s)
    rv = calc_realized_vol(price_series, 60.0)

    # Session duration
    duration_min = len(session_candles)

    return {
        'label': session_label,
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'entry_hour': entry_dt.hour,
        'spot_entry': spot_entry,
        'spot_exit': spot_exit,
        'strike': strike,
        'iv': sigma,
        'realized_vol': rv,
        'rv_iv_ratio': rv / sigma if sigma > 0 else 0,
        'straddle_entry': entry_cost,
        'straddle_exit': exit_proceeds,
        'straddle_pnl': straddle_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': total_pnl,
        'gamma_pnl_theoretical': gamma_pnl_theoretical,
        'hedge_count': hedge_count,
        'bars': len(session_candles),
        'duration_min': duration_min,
        'spot_move_pct': (spot_exit - spot_entry) / spot_entry * 100,
        'spot_abs_move_pct': abs(spot_exit - spot_entry) / spot_entry * 100,
    }


# ============================================================
# SESSION BUILDER
# ============================================================
def build_sessions(perp_data, session_def, session_length_hours):
    """
    Build non-overlapping session windows from 1-min candle data.

    session_def: (start_hour, end_hour) in UTC
    session_length_hours: length of each sub-session

    Returns list of lists of candle tuples.
    """
    start_hour, end_hour = session_def

    # Index candles by (date, hour, minute) for fast lookup
    candle_by_time = {}
    for i, ts in enumerate(perp_data['ticks']):
        dt = datetime.utcfromtimestamp(ts / 1000)
        key = (dt.year, dt.month, dt.day, dt.hour, dt.minute)
        candle_by_time[key] = (
            ts,
            perp_data['open'][i],
            perp_data['high'][i],
            perp_data['low'][i],
            perp_data['close'][i],
            perp_data['volume'][i],
        )

    # Find all unique dates in data
    dates = set()
    for ts in perp_data['ticks']:
        dt = datetime.utcfromtimestamp(ts / 1000)
        dates.add(dt.date())
    dates = sorted(dates)

    sessions = []

    for d in dates:
        # Generate sub-session start times within this session window
        if end_hour <= 24:
            hour = start_hour
            while hour + session_length_hours <= end_hour:
                session_start = datetime(d.year, d.month, d.day, hour, 0)
                session_end = session_start + timedelta(hours=session_length_hours)

                # Collect candles in this window
                candles = []
                cursor = session_start
                while cursor < session_end:
                    key = (cursor.year, cursor.month, cursor.day, cursor.hour, cursor.minute)
                    if key in candle_by_time:
                        candles.append(candle_by_time[key])
                    cursor += timedelta(minutes=1)

                if len(candles) >= 30:
                    sessions.append(candles)

                hour += session_length_hours

    return sessions


# ============================================================
# ANALYTICS
# ============================================================
def print_breakdown(label, results, key_fn):
    """Print P&L breakdown grouped by key_fn."""
    groups = defaultdict(list)
    for r in results:
        k = key_fn(r)
        groups[k].append(r)

    print(f"\n--- {label} ---")
    print(f"{'Group':<16} {'Sessions':>8} {'Total P&L':>12} {'Avg P&L':>10} "
          f"{'WinRate':>8} {'Avg RV':>8} {'Avg IV':>8} {'Avg Hdgs':>8}")
    print("-" * 90)

    for k in sorted(groups.keys(), key=str):
        grp = groups[k]
        pnls = [r['total_pnl'] for r in grp]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
        avg_rv = np.mean([r['realized_vol'] for r in grp])
        avg_iv = np.mean([r['iv'] for r in grp])
        avg_hdg = np.mean([r['hedge_count'] for r in grp])
        print(f"{str(k):<16} {len(grp):>8} ${sum(pnls):>+10,.0f} ${np.mean(pnls):>+9,.0f} "
              f"{wr:>7.0%} {avg_rv:>7.0%} {avg_iv:>7.0%} {avg_hdg:>7.1f}")


def iv_regime(iv):
    """Classify IV into regime buckets."""
    if iv < 0.30:
        return 'low (<30%)'
    elif iv < 0.50:
        return 'med (30-50%)'
    elif iv < 0.70:
        return 'high (50-70%)'
    else:
        return 'vhigh (>70%)'


# ============================================================
# MAIN BACKTEST
# ============================================================
def main():
    print("=" * 70)
    print("BTC INTRADAY GAMMA SCALPING - 1-MIN CANDLES")
    print("=" * 70)
    print(f"Strategy: Long ATM Straddle + Aggressive Delta Hedging (BTC-PERP)")
    print(f"Delta Threshold: +/-{CONFIG['delta_threshold']} BTC")
    print(f"Check Interval: {CONFIG['check_interval_min']} min (1-min candles)")
    print(f"Session Length: {CONFIG['session_length_hours']}h windows")
    print(f"Strike Rounding: nearest $500")
    print(f"Lookback: {CONFIG['lookback_days']} days")
    print(f"Sessions: {', '.join(CONFIG['sessions'].keys())}")
    print(f"IV Source: Deribit DVOL")
    print()

    # --- STEP 1: Fetch data ---
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = now_ms - CONFIG['lookback_days'] * 86400000

    print(f"[1/3] Fetching BTC-PERPETUAL 1-min candles ({CONFIG['lookback_days']} days)...")
    print(f"  Expecting ~{CONFIG['lookback_days'] * 1440:,} bars in ~{CONFIG['lookback_days'] // 3 + 1} chunks")
    perp_data = fetch_perp_candles_1min(start_ms, now_ms)
    n_bars = len(perp_data['ticks'])
    print(f"  Total: {n_bars:,} candles")
    if perp_data['ticks']:
        first_dt = datetime.utcfromtimestamp(perp_data['ticks'][0] / 1000)
        last_dt = datetime.utcfromtimestamp(perp_data['ticks'][-1] / 1000)
        print(f"  Range: {first_dt.strftime('%Y-%m-%d %H:%M')} to {last_dt.strftime('%Y-%m-%d %H:%M')}")

    print(f"\n[2/3] Fetching DVOL (implied volatility index)...")
    dvol_data = fetch_dvol(start_ms, now_ms)
    print(f"  Fetched {len(dvol_data)} hourly DVOL readings")

    # Build DVOL lookup: timestamp -> IV (decimal)
    dvol_lookup = {}
    dvol_timestamps = []
    for entry in dvol_data:
        ts = entry[0]
        iv_close = entry[4]
        dvol_lookup[ts] = iv_close / 100
        dvol_timestamps.append(ts)
    dvol_timestamps.sort()

    def get_nearest_dvol(target_ms):
        """Binary search for nearest DVOL reading."""
        if not dvol_timestamps:
            return 0.50
        lo, hi = 0, len(dvol_timestamps) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if dvol_timestamps[mid] < target_ms:
                lo = mid + 1
            else:
                hi = mid
        # Check lo and lo-1
        best_ts = dvol_timestamps[lo]
        if lo > 0:
            prev_ts = dvol_timestamps[lo - 1]
            if abs(prev_ts - target_ms) < abs(best_ts - target_ms):
                best_ts = prev_ts
        iv = dvol_lookup.get(best_ts, 0.50)
        return iv if iv > 0.05 else 0.50

    # --- STEP 2: Build sessions and run ---
    print(f"\n[3/3] Building sessions and running backtest...")

    all_results = []
    session_count = 0
    skipped = 0

    for session_name, session_def in CONFIG['sessions'].items():
        print(f"\n  Building {session_name} sessions ({session_def[0]:02d}:00-{session_def[1]:02d}:00 UTC, "
              f"{CONFIG['session_length_hours']}h windows)...")

        sessions = build_sessions(perp_data, session_def, CONFIG['session_length_hours'])
        print(f"    Found {len(sessions)} sessions")

        for idx, session_candles in enumerate(sessions):
            entry_ts_ms = session_candles[0][0]
            iv = get_nearest_dvol(entry_ts_ms)

            result = run_session(session_candles, iv, session_name, CONFIG)

            if result:
                all_results.append(result)
                session_count += 1

                if session_count % 10 == 0:
                    cum_pnl = sum(r['total_pnl'] for r in all_results)
                    print(f"    [{session_count} sessions] {result['entry_time']} | "
                          f"P&L: ${result['total_pnl']:+,.0f} | "
                          f"Cum: ${cum_pnl:+,.0f} | "
                          f"IV: {result['iv']:.0%} | RV: {result['realized_vol']:.0%}")
            else:
                skipped += 1

    # --- STEP 3: Results ---
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS - INTRADAY GAMMA SCALPING")
    print("=" * 70)

    if not all_results:
        print("No results generated.")
        return

    pnls = [r['total_pnl'] for r in all_results]
    straddle_pnls = [r['straddle_pnl'] for r in all_results]
    hedge_pnls = [r['hedge_pnl'] for r in all_results]
    fees = [r['total_fees'] for r in all_results]
    hedges = [r['hedge_count'] for r in all_results]
    ivs = [r['iv'] for r in all_results]
    rvs = [r['realized_vol'] for r in all_results]
    gamma_theo = [r['gamma_pnl_theoretical'] for r in all_results]

    total_pnl = sum(pnls)
    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls)
    sharpe_session = (avg_pnl / std_pnl) * np.sqrt(len(pnls)) if std_pnl > 0 else 0
    # Annualize: assume ~4 sessions per day, ~365 days
    sessions_per_year = 4 * 365
    sharpe_ann = (avg_pnl / std_pnl) * np.sqrt(sessions_per_year) if std_pnl > 0 else 0
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls)

    max_dd = 0
    peak = 0
    cum = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    print(f"\nPeriod: {all_results[0]['date']} to {all_results[-1]['date']}")
    print(f"Total Sessions: {len(all_results)} (skipped: {skipped})")
    print(f"Session Length: {CONFIG['session_length_hours']}h | Check Interval: {CONFIG['check_interval_min']}min")
    print(f"Delta Threshold: {CONFIG['delta_threshold']} BTC")
    print()

    print("--- P&L Summary ---")
    print(f"Total P&L:          ${total_pnl:+,.2f}")
    print(f"Avg Session P&L:    ${avg_pnl:+,.2f}")
    print(f"Median Session P&L: ${np.median(pnls):+,.2f}")
    print(f"Std Session P&L:    ${std_pnl:,.2f}")
    print(f"Best Session:       ${max(pnls):+,.2f}")
    print(f"Worst Session:      ${min(pnls):+,.2f}")
    print(f"Max Drawdown:       ${max_dd:,.2f}")
    print()

    print("--- Performance Metrics ---")
    print(f"Win Rate:           {win_rate:.1%}")
    print(f"Sharpe (annualized):{sharpe_ann:+.2f}")
    losses = sum(p for p in pnls if p < 0)
    gains = sum(p for p in pnls if p > 0)
    print(f"Profit Factor:      {gains / abs(losses):.2f}" if losses != 0 else "Profit Factor: inf")
    if any(p > 0 for p in pnls):
        print(f"Avg Win:            ${np.mean([p for p in pnls if p > 0]):+,.2f}")
    if any(p < 0 for p in pnls):
        print(f"Avg Loss:           ${np.mean([p for p in pnls if p < 0]):+,.2f}")
    print()

    print("--- P&L Decomposition ---")
    print(f"Straddle P&L:       ${sum(straddle_pnls):+,.2f}")
    print(f"Hedge P&L:          ${sum(hedge_pnls):+,.2f}")
    print(f"Gamma P&L (theo):   ${sum(gamma_theo):+,.2f}")
    print(f"Total Fees:         ${sum(fees):,.2f}")
    print(f"Avg Fees/Session:   ${np.mean(fees):,.2f}")
    print()

    print("--- Hedging Stats ---")
    print(f"Avg Hedges/Session: {np.mean(hedges):.1f}")
    print(f"Total Hedges:       {sum(hedges)}")
    print()

    print("--- Volatility ---")
    print(f"Avg IV (DVOL):      {np.mean(ivs):.1%}")
    print(f"Avg Realized Vol:   {np.mean(rvs):.1%}")
    print(f"Avg RV/IV Ratio:    {np.mean([r['rv_iv_ratio'] for r in all_results]):.2f}")
    print(f"  RV > IV sessions: {sum(1 for r in all_results if r['rv_iv_ratio'] > 1)}/{len(all_results)}")
    print()

    # --- Breakdowns ---
    print_breakdown("P&L by Session Type", all_results, lambda r: r['label'])
    print_breakdown("P&L by IV Regime", all_results, lambda r: iv_regime(r['iv']))
    print_breakdown("P&L by Day of Week", all_results, lambda r: r['day_of_week'])
    print_breakdown("P&L by Entry Hour (UTC)", all_results, lambda r: f"{r['entry_hour']:02d}:00")

    # RV/IV analysis
    print("\n--- P&L by RV/IV Ratio ---")
    def rv_iv_bucket(r):
        ratio = r['rv_iv_ratio']
        if ratio < 0.8:
            return 'RV<<IV (<0.8)'
        elif ratio < 1.0:
            return 'RV<IV (0.8-1.0)'
        elif ratio < 1.2:
            return 'RV~IV (1.0-1.2)'
        else:
            return 'RV>>IV (>1.2)'
    print_breakdown("P&L by RV/IV Bucket", all_results, rv_iv_bucket)

    # Monthly
    print("\n--- Monthly Breakdown ---")
    monthly = defaultdict(list)
    for r in all_results:
        month = r['date'][:7]
        monthly[month].append(r['total_pnl'])

    print(f"{'Month':<10} {'Sessions':>8} {'P&L':>12} {'Avg':>10} {'WinRate':>8}")
    print("-" * 55)
    for month in sorted(monthly.keys()):
        mpnls = monthly[month]
        mwin = sum(1 for p in mpnls if p > 0) / len(mpnls)
        print(f"{month:<10} {len(mpnls):>8} ${sum(mpnls):>+10,.0f} ${np.mean(mpnls):>+9,.0f} {mwin:>7.0%}")

    print("\n" + "=" * 70)

    # --- Save results to JSON ---
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(
        output_dir,
        f'btc_intraday_gamma_results_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'
    )

    # Aggregate stats by session type
    session_type_stats = {}
    for sname in CONFIG['sessions']:
        s_results = [r for r in all_results if r['label'] == sname]
        if s_results:
            s_pnls = [r['total_pnl'] for r in s_results]
            session_type_stats[sname] = {
                'sessions': len(s_results),
                'total_pnl': sum(s_pnls),
                'avg_pnl': float(np.mean(s_pnls)),
                'std_pnl': float(np.std(s_pnls)),
                'win_rate': sum(1 for p in s_pnls if p > 0) / len(s_pnls),
                'avg_hedges': float(np.mean([r['hedge_count'] for r in s_results])),
                'avg_iv': float(np.mean([r['iv'] for r in s_results])),
                'avg_rv': float(np.mean([r['realized_vol'] for r in s_results])),
                'avg_rv_iv_ratio': float(np.mean([r['rv_iv_ratio'] for r in s_results])),
            }

    save_data = {
        'config': {k: v for k, v in CONFIG.items() if k != 'sessions'},
        'session_definitions': {k: list(v) for k, v in CONFIG['sessions'].items()},
        'metrics': {
            'total_pnl': total_pnl,
            'avg_session_pnl': float(avg_pnl),
            'std_session_pnl': float(std_pnl),
            'sharpe_annualized': float(sharpe_ann),
            'win_rate': win_rate,
            'max_drawdown': max_dd,
            'total_fees': sum(fees),
            'total_sessions': len(all_results),
            'total_hedges': sum(hedges),
            'avg_iv': float(np.mean(ivs)),
            'avg_rv': float(np.mean(rvs)),
            'gamma_pnl_theoretical': float(sum(gamma_theo)),
        },
        'session_type_stats': session_type_stats,
        'session_results': [{
            'label': r['label'],
            'entry_time': r['entry_time'],
            'exit_time': r['exit_time'],
            'date': r['date'],
            'day_of_week': r['day_of_week'],
            'entry_hour': r['entry_hour'],
            'total_pnl': r['total_pnl'],
            'straddle_pnl': r['straddle_pnl'],
            'hedge_pnl': r['hedge_pnl'],
            'gamma_pnl_theoretical': r['gamma_pnl_theoretical'],
            'fees': r['total_fees'],
            'hedge_count': r['hedge_count'],
            'spot_entry': r['spot_entry'],
            'spot_exit': r['spot_exit'],
            'strike': r['strike'],
            'iv': r['iv'],
            'realized_vol': r['realized_vol'],
            'rv_iv_ratio': r['rv_iv_ratio'],
            'spot_move_pct': r['spot_move_pct'],
            'duration_min': r['duration_min'],
        } for r in all_results]
    }

    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()
