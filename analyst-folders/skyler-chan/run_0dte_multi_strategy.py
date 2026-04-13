"""
0-DTE Multi-Strategy BTC Gamma Scalping Backtest
Tests 7 different gamma scalping strategies on BTC 0-DTE options using Deribit data.
Runs on US session (14:00-18:00 UTC) and Expiration window (06:00-08:00 UTC).

Author: Skyler Chan | PGI Derivatives
"""

import urllib.request
import json
import os
import sys
import numpy as np
import time as time_mod
from datetime import datetime, timedelta, timezone
from scipy.stats import norm
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
CONFIG = {
    'contracts': 1,
    'max_btc_position': 3.0,
    'lookback_days': 90,
    # Deribit fees
    'option_taker_fee': 0.0003,       # 0.03% of underlying
    'perp_taker_fee': 0.0005,         # 0.05% of trade value
    'perp_maker_fee': 0.0000,
    # Execution
    'option_spread_pct': 0.015,       # 1.5% of option mid
    'perp_spread_usd': 0.50,
    # Risk-free rate
    'r': 0.045,
}

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_cache_1min.json')

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
    all_ticks = []
    all_open = []
    all_high = []
    all_low = []
    all_close = []
    all_volume = []

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

        dt_start = datetime.fromtimestamp(ticks[0] / 1000, tz=timezone.utc)
        dt_end = datetime.fromtimestamp(ticks[-1] / 1000, tz=timezone.utc)
        print(f"    Chunk {chunk_num}: {len(ticks)} bars "
              f"({dt_start.strftime('%Y-%m-%d %H:%M')} - "
              f"{dt_end.strftime('%Y-%m-%d %H:%M')})", flush=True)

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
    all_data = []
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
            print(f"    DVOL chunk {chunk_num} failed: {e}, skipping", flush=True)
            cursor = chunk_end + 1
            continue

        data = result.get('data', [])
        if data:
            all_data.extend(data)
            print(f"    DVOL chunk {chunk_num}: {len(data)} readings", flush=True)
        cursor = chunk_end + 1

    return all_data


# ============================================================
# DATA CACHING
# ============================================================
def load_or_fetch_data(lookback_days):
    """Load data from cache if fresh, otherwise fetch from API and cache."""
    if os.path.exists(CACHE_FILE):
        file_age_hours = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(CACHE_FILE)) / 3600
        if file_age_hours < 24:
            print(f"Loading data from cache ({CACHE_FILE}), age: {file_age_hours:.1f}h", flush=True)
            with open(CACHE_FILE, 'r') as f:
                cached = json.load(f)
            return cached['perp_data'], cached['dvol_data']
        else:
            print(f"Cache is {file_age_hours:.1f}h old (>24h), fetching fresh data...", flush=True)
    else:
        print("No cache found, fetching fresh data from Deribit...", flush=True)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - lookback_days * 86400000

    print(f"  Fetching BTC-PERPETUAL 1-min candles ({lookback_days} days)...", flush=True)
    perp_data = fetch_perp_candles_1min(start_ms, now_ms)
    print(f"  Total: {len(perp_data['ticks']):,} candles", flush=True)

    print(f"  Fetching DVOL (implied volatility index)...", flush=True)
    dvol_data = fetch_dvol(start_ms, now_ms)
    print(f"  Fetched {len(dvol_data)} hourly DVOL readings", flush=True)

    # Save cache
    print(f"  Saving cache to {CACHE_FILE}...", flush=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump({'perp_data': perp_data, 'dvol_data': dvol_data}, f)
    print(f"  Cache saved.", flush=True)

    return perp_data, dvol_data


# ============================================================
# BLACK-SCHOLES
# ============================================================
def bs_price_and_greeks(S, K, T, r, sigma, option_type):
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
# EXECUTION MODEL
# ============================================================
def execute_option_trade(mid_price, side, spot_price, config):
    spread = mid_price * config['option_spread_pct']
    if side == 'BUY':
        fill = mid_price + spread / 2
    else:
        fill = mid_price - spread / 2
        fill = max(fill, 0)
    fee_usd = spot_price * config['option_taker_fee']
    return fill, fee_usd


def execute_perp_trade(price, size_btc, side, config):
    half_spread = config['perp_spread_usd'] / 2
    if side == 'BUY':
        fill = price + half_spread
    else:
        fill = price - half_spread
    trade_value = abs(size_btc) * fill
    fee = trade_value * config['perp_taker_fee']
    return fill, fee


# ============================================================
# REALIZED VOL
# ============================================================
def calc_realized_vol(prices, interval_seconds):
    if len(prices) < 2:
        return 0.0
    log_returns = np.diff(np.log(prices))
    if len(log_returns) == 0:
        return 0.0
    intervals_per_year = (365.25 * 24 * 3600) / interval_seconds
    return float(np.std(log_returns) * np.sqrt(intervals_per_year))


# ============================================================
# HEDGING ENGINE (shared across strategies)
# ============================================================
def delta_hedge_step(portfolio_delta, spot, btc_hedge_position, hedge_avg_cost,
                     hedge_pnl, hedge_count, total_fees, delta_threshold, config):
    """Attempt a delta hedge. Returns updated state tuple."""
    if abs(portfolio_delta) <= delta_threshold:
        return btc_hedge_position, hedge_avg_cost, hedge_pnl, hedge_count, total_fees

    hedge_size = -portfolio_delta
    new_pos = btc_hedge_position + hedge_size
    max_pos = config['max_btc_position']
    if abs(new_pos) > max_pos:
        if hedge_size > 0:
            hedge_size = max_pos - btc_hedge_position
        else:
            hedge_size = -max_pos - btc_hedge_position

    if abs(hedge_size) < 0.001:
        return btc_hedge_position, hedge_avg_cost, hedge_pnl, hedge_count, total_fees

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

    return btc_hedge_position, hedge_avg_cost, hedge_pnl, hedge_count, total_fees


def close_hedge(btc_hedge_position, hedge_avg_cost, hedge_pnl, total_fees, spot, config):
    """Close remaining hedge position. Returns updated hedge_pnl, total_fees."""
    if abs(btc_hedge_position) > 0.001:
        side = 'SELL' if btc_hedge_position > 0 else 'BUY'
        fill_price, fee = execute_perp_trade(spot, abs(btc_hedge_position), side, config)
        if btc_hedge_position > 0:
            hedge_pnl += btc_hedge_position * (fill_price - hedge_avg_cost)
        else:
            hedge_pnl += abs(btc_hedge_position) * (hedge_avg_cost - fill_price)
        total_fees += fee
    return hedge_pnl, total_fees


# ============================================================
# HELPER: compute expiry datetime for 0-DTE (next 08:00 UTC)
# ============================================================
def get_0dte_expiry(entry_dt):
    expiry_dt = entry_dt.replace(hour=8, minute=0, second=0, microsecond=0)
    if expiry_dt <= entry_dt:
        expiry_dt += timedelta(days=1)
    return expiry_dt


def time_to_expiry_years(dt, expiry_dt):
    T = (expiry_dt - dt).total_seconds() / (365.25 * 24 * 3600)
    return max(T, 1e-8)


# ============================================================
# STRATEGY 1: ATM Straddle (Baseline)
# ============================================================
def run_strategy_1(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 500) * 500
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    call_g = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'call')
    put_g = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'put')
    if call_g['price'] < 1 or put_g['price'] < 1:
        return None

    call_fill, call_fee = execute_option_trade(call_g['price'], 'BUY', spot_entry, config)
    put_fill, put_fee = execute_option_trade(put_g['price'], 'BUY', spot_entry, config)
    premium_paid = (call_fill + put_fill) * contracts
    total_fees = (call_fee + put_fee) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        cg = bs_price_and_greeks(spot, strike, T_now, r, iv, 'call')
        pg = bs_price_and_greeks(spot, strike, T_now, r, iv, 'put')
        straddle_gamma = (cg['gamma'] + pg['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * straddle_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        portfolio_delta = (cg['delta'] + pg['delta']) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            portfolio_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    # Exit
    exit_candle = session_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    call_exit = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'call')
    put_exit = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'put')
    cf, cfe = execute_option_trade(call_exit['price'], 'SELL', spot_exit, config)
    pf, pfe = execute_option_trade(put_exit['price'], 'SELL', spot_exit, config)
    exit_proceeds = (cf + pf) * contracts
    total_fees += (cfe + pfe) * contracts

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)

    option_pnl = exit_proceeds - premium_paid
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '1. ATM Straddle',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': premium_paid,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(session_candles),
    }


# ============================================================
# STRATEGY 2: OTM Strangle
# ============================================================
def run_strategy_2(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    call_strike = round((spot_entry * 1.03) / 500) * 500
    put_strike = round((spot_entry * 0.97) / 500) * 500
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    cg = bs_price_and_greeks(spot_entry, call_strike, T_entry, r, iv, 'call')
    pg = bs_price_and_greeks(spot_entry, put_strike, T_entry, r, iv, 'put')
    if cg['price'] < 0.10 or pg['price'] < 0.10:
        return None

    cf, cfe = execute_option_trade(cg['price'], 'BUY', spot_entry, config)
    pf, pfe = execute_option_trade(pg['price'], 'BUY', spot_entry, config)
    premium_paid = (cf + pf) * contracts
    total_fees = (cfe + pfe) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        cg2 = bs_price_and_greeks(spot, call_strike, T_now, r, iv, 'call')
        pg2 = bs_price_and_greeks(spot, put_strike, T_now, r, iv, 'put')
        total_gamma = (cg2['gamma'] + pg2['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * total_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        portfolio_delta = (cg2['delta'] + pg2['delta']) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            portfolio_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    exit_candle = session_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    ce = bs_price_and_greeks(spot_exit, call_strike, T_exit, r, iv, 'call')
    pe = bs_price_and_greeks(spot_exit, put_strike, T_exit, r, iv, 'put')
    cf2, cfe2 = execute_option_trade(ce['price'], 'SELL', spot_exit, config)
    pf2, pfe2 = execute_option_trade(pe['price'], 'SELL', spot_exit, config)
    exit_proceeds = (cf2 + pf2) * contracts
    total_fees += (cfe2 + pfe2) * contracts

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)

    option_pnl = exit_proceeds - premium_paid
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '2. OTM Strangle',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [put_strike, call_strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': premium_paid,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(session_candles),
    }


# ============================================================
# STRATEGY 3: Calendar Spread
# ============================================================
def run_strategy_3(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 500) * 500
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_daily = time_to_expiry_years(entry_dt, expiry_dt)
    T_weekly = T_daily + 7.0 / 365.25
    iv_weekly = iv  # Use DVOL as proxy for weekly IV

    # Price 4 legs
    dc = bs_price_and_greeks(spot_entry, strike, T_daily, r, iv, 'call')
    dp = bs_price_and_greeks(spot_entry, strike, T_daily, r, iv, 'put')
    wc = bs_price_and_greeks(spot_entry, strike, T_weekly, r, iv_weekly, 'call')
    wp = bs_price_and_greeks(spot_entry, strike, T_weekly, r, iv_weekly, 'put')

    if dc['price'] < 1 or dp['price'] < 1:
        return None

    # Buy daily (0-DTE), sell weekly (7-DTE)
    dc_fill, dc_fee = execute_option_trade(dc['price'], 'BUY', spot_entry, config)
    dp_fill, dp_fee = execute_option_trade(dp['price'], 'BUY', spot_entry, config)
    wc_fill, wc_fee = execute_option_trade(wc['price'], 'SELL', spot_entry, config)
    wp_fill, wp_fee = execute_option_trade(wp['price'], 'SELL', spot_entry, config)

    # Net premium: paid for daily, received for weekly
    premium_daily = (dc_fill + dp_fill) * contracts
    premium_weekly = (wc_fill + wp_fill) * contracts
    net_premium = premium_daily - premium_weekly  # positive = net debit
    total_fees = (dc_fee + dp_fee + wc_fee + wp_fee) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_d = time_to_expiry_years(current_dt, expiry_dt)
        T_w = T_d + 7.0 / 365.25
        dcg = bs_price_and_greeks(spot, strike, T_d, r, iv, 'call')
        dpg = bs_price_and_greeks(spot, strike, T_d, r, iv, 'put')
        wcg = bs_price_and_greeks(spot, strike, T_w, r, iv_weekly, 'call')
        wpg = bs_price_and_greeks(spot, strike, T_w, r, iv_weekly, 'put')

        # Net gamma: long daily gamma, short weekly gamma
        net_gamma = (dcg['gamma'] + dpg['gamma'] - wcg['gamma'] - wpg['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * net_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        # Net delta
        net_delta = ((dcg['delta'] + dpg['delta']) - (wcg['delta'] + wpg['delta'])) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            net_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    # Exit: close all 4 legs
    exit_candle = session_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_d_exit = time_to_expiry_years(exit_dt, expiry_dt)
    T_w_exit = T_d_exit + 7.0 / 365.25

    dce = bs_price_and_greeks(spot_exit, strike, T_d_exit, r, iv, 'call')
    dpe = bs_price_and_greeks(spot_exit, strike, T_d_exit, r, iv, 'put')
    wce = bs_price_and_greeks(spot_exit, strike, T_w_exit, r, iv_weekly, 'call')
    wpe = bs_price_and_greeks(spot_exit, strike, T_w_exit, r, iv_weekly, 'put')

    # Close: sell daily, buy back weekly
    dcf2, dcfe2 = execute_option_trade(dce['price'], 'SELL', spot_exit, config)
    dpf2, dpfe2 = execute_option_trade(dpe['price'], 'SELL', spot_exit, config)
    wcf2, wcfe2 = execute_option_trade(wce['price'], 'BUY', spot_exit, config)
    wpf2, wpfe2 = execute_option_trade(wpe['price'], 'BUY', spot_exit, config)

    exit_daily = (dcf2 + dpf2) * contracts
    exit_weekly = (wcf2 + wpf2) * contracts  # cost to buy back
    total_fees += (dcfe2 + dpfe2 + wcfe2 + wpfe2) * contracts

    # Option P&L: (sell daily - buy daily) + (sell weekly at entry - buy weekly at exit)
    option_pnl = (exit_daily - premium_daily) + (premium_weekly - exit_weekly)

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '3. Calendar Spread',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': net_premium,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(session_candles),
    }


# ============================================================
# STRATEGY 4: Ratio Backspread (Calls)
# ============================================================
def run_strategy_4(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    atm_strike = round(spot_entry / 500) * 500
    otm_strike = round((spot_entry * 1.02) / 500) * 500
    if otm_strike == atm_strike:
        otm_strike += 500

    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    atm_g = bs_price_and_greeks(spot_entry, atm_strike, T_entry, r, iv, 'call')
    otm_g = bs_price_and_greeks(spot_entry, otm_strike, T_entry, r, iv, 'call')

    if atm_g['price'] < 1:
        return None

    # Sell 1 ATM call, buy 2 OTM calls
    atm_fill, atm_fee = execute_option_trade(atm_g['price'], 'SELL', spot_entry, config)
    otm_fill, otm_fee = execute_option_trade(otm_g['price'], 'BUY', spot_entry, config)

    premium_received = atm_fill * contracts
    premium_paid_otm = otm_fill * 2 * contracts
    net_premium = premium_paid_otm - premium_received  # positive = net debit
    total_fees = (atm_fee + otm_fee * 2) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        ag = bs_price_and_greeks(spot, atm_strike, T_now, r, iv, 'call')
        og = bs_price_and_greeks(spot, otm_strike, T_now, r, iv, 'call')

        # Net gamma: -1*ATM + 2*OTM
        net_gamma = (-ag['gamma'] + 2 * og['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * net_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        # Net delta: -1*ATM_delta + 2*OTM_delta
        net_delta = (-ag['delta'] + 2 * og['delta']) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            net_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    exit_candle = session_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    ae = bs_price_and_greeks(spot_exit, atm_strike, T_exit, r, iv, 'call')
    oe = bs_price_and_greeks(spot_exit, otm_strike, T_exit, r, iv, 'call')

    # Close: buy back ATM, sell 2 OTM
    af2, afe2 = execute_option_trade(ae['price'], 'BUY', spot_exit, config)
    of2, ofe2 = execute_option_trade(oe['price'], 'SELL', spot_exit, config)
    total_fees += (afe2 + ofe2 * 2) * contracts

    # Option P&L
    # ATM: sold at atm_fill, buy back at af2 => atm_fill - af2
    # OTM: bought at otm_fill*2, sell at of2*2 => of2*2 - otm_fill*2
    option_pnl = ((atm_fill - af2) + (of2 * 2 - otm_fill * 2)) * contracts

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '4. Ratio Backspread',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [atm_strike, otm_strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': net_premium,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(session_candles),
    }


# ============================================================
# STRATEGY 5: Directional Gamma (Momentum-Filtered)
# ============================================================
def run_strategy_5(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    if len(session_candles) < 61:
        return None

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 500) * 500
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    # 60-min momentum: use price from 60 candles ago if available
    # We look at the first candle vs the candle 60 bars in (or use what we have)
    momentum_price = session_candles[0][4]  # open price
    current_price = session_candles[min(60, len(session_candles) - 1)][4]
    momentum_pct = (current_price - momentum_price) / momentum_price * 100

    if momentum_pct > 0.2:
        direction = 'call'
        label_suffix = ' (Call)'
    elif momentum_pct < -0.2:
        direction = 'put'
        label_suffix = ' (Put)'
    else:
        direction = 'straddle'
        label_suffix = ' (Straddle)'

    cg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'call')
    pg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'put')

    if direction == 'call':
        if cg['price'] < 1:
            return None
        cf, cfe = execute_option_trade(cg['price'], 'BUY', spot_entry, config)
        premium_paid = cf * contracts
        total_fees = cfe * contracts
        long_call = True
        long_put = False
    elif direction == 'put':
        if pg['price'] < 1:
            return None
        pf, pfe = execute_option_trade(pg['price'], 'BUY', spot_entry, config)
        premium_paid = pf * contracts
        total_fees = pfe * contracts
        long_call = False
        long_put = True
    else:
        if cg['price'] < 1 or pg['price'] < 1:
            return None
        cf, cfe = execute_option_trade(cg['price'], 'BUY', spot_entry, config)
        pf, pfe = execute_option_trade(pg['price'], 'BUY', spot_entry, config)
        premium_paid = (cf + pf) * contracts
        total_fees = (cfe + pfe) * contracts
        long_call = True
        long_put = True

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        cg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'call')
        pg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'put')

        total_gamma = 0
        if long_call:
            total_gamma += cg2['gamma']
        if long_put:
            total_gamma += pg2['gamma']
        total_gamma *= contracts

        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * total_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        portfolio_delta = btc_pos
        if long_call:
            portfolio_delta += cg2['delta'] * contracts
        if long_put:
            portfolio_delta += pg2['delta'] * contracts

        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            portfolio_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    exit_candle = session_candles[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    ce = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'call')
    pe = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'put')

    exit_proceeds = 0
    if long_call:
        cf2, cfe2 = execute_option_trade(ce['price'], 'SELL', spot_exit, config)
        exit_proceeds += cf2 * contracts
        total_fees += cfe2 * contracts
    if long_put:
        pf2, pfe2 = execute_option_trade(pe['price'], 'SELL', spot_exit, config)
        exit_proceeds += pf2 * contracts
        total_fees += pfe2 * contracts

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)

    option_pnl = exit_proceeds - premium_paid
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '5. Directional Gamma' + label_suffix,
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': premium_paid,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(session_candles),
    }


# ============================================================
# STRATEGY 6: Short Gamma (Vol Mean Reversion)
# ============================================================
def run_strategy_6(session_candles, iv, config):
    r = config['r']
    contracts = config['contracts']
    check_interval = 3
    delta_threshold = 0.03

    entry_candle = session_candles[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 500) * 500
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    cg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'call')
    pg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'put')
    if cg['price'] < 1 or pg['price'] < 1:
        return None

    # SELL straddle: receive premium
    cf, cfe = execute_option_trade(cg['price'], 'SELL', spot_entry, config)
    pf, pfe = execute_option_trade(pg['price'], 'SELL', spot_entry, config)
    premium_collected = (cf + pf) * contracts
    total_fees = (cfe + pfe) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry
    early_exit = False
    max_loss = 2.0 * premium_collected

    for i, candle in enumerate(session_candles):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        cg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'call')
        pg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'put')

        # Short gamma: negative
        net_gamma = -(cg2['gamma'] + pg2['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * net_gamma * dS**2
        prev_spot = spot

        # Check unrealized loss (cost to buy back - premium collected)
        current_buyback = (cg2['price'] + pg2['price']) * contracts
        # We need to add spread for buyback
        unrealized_option_pnl = premium_collected - current_buyback
        unrealized_total = unrealized_option_pnl + hedge_pnl - total_fees
        if unrealized_total < -max_loss:
            early_exit = True
            # Close at this candle
            break

        if i % check_interval != 0 and i != len(session_candles) - 1:
            continue

        # Portfolio delta (short straddle: negate option deltas)
        portfolio_delta = -(cg2['delta'] + pg2['delta']) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            portfolio_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    # Exit
    if early_exit:
        exit_idx = len(price_series) - 1
        exit_candle = session_candles[exit_idx]
    else:
        exit_candle = session_candles[-1]

    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    ce = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'call')
    pe = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'put')

    # Buy back straddle
    cf2, cfe2 = execute_option_trade(ce['price'], 'BUY', spot_exit, config)
    pf2, pfe2 = execute_option_trade(pe['price'], 'BUY', spot_exit, config)
    buyback_cost = (cf2 + pf2) * contracts
    total_fees += (cfe2 + pfe2) * contracts

    option_pnl = premium_collected - buyback_cost

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '6. Short Gamma',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': -premium_collected,  # negative = collected
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(price_series),
    }


# ============================================================
# STRATEGY 7: Expiration Scalp (Last 2 Hours)
# ============================================================
def run_strategy_7(session_candles, iv, config):
    """
    Enters at 06:00 UTC, exits at 07:50 UTC.
    session_candles should cover the 06:00-08:00 window.
    We trim to 06:00-07:50 (110 minutes).
    """
    r = config['r']
    contracts = config['contracts']
    check_interval = 1  # every minute
    delta_threshold = 0.03

    # Filter candles to 06:00-07:50
    filtered = []
    for candle in session_candles:
        dt = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
        if dt.hour == 6 or (dt.hour == 7 and dt.minute <= 50):
            filtered.append(candle)

    if len(filtered) < 20:
        return None

    entry_candle = filtered[0]
    spot_entry = entry_candle[4]
    strike = round(spot_entry / 250) * 250  # tighter: nearest $250
    entry_dt = datetime.fromtimestamp(entry_candle[0] / 1000, tz=timezone.utc)
    expiry_dt = get_0dte_expiry(entry_dt)
    T_entry = time_to_expiry_years(entry_dt, expiry_dt)

    cg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'call')
    pg = bs_price_and_greeks(spot_entry, strike, T_entry, r, iv, 'put')

    # Options may be very cheap near expiry, accept lower threshold
    if cg['price'] < 0.01 or pg['price'] < 0.01:
        return None

    cf, cfe = execute_option_trade(cg['price'], 'BUY', spot_entry, config)
    pf, pfe = execute_option_trade(pg['price'], 'BUY', spot_entry, config)
    premium_paid = (cf + pf) * contracts
    total_fees = (cfe + pfe) * contracts

    btc_pos = 0.0
    hedge_avg = 0.0
    hedge_pnl = 0.0
    hedge_count = 0
    price_series = []
    gamma_pnl_theo = 0.0
    prev_spot = spot_entry

    for i, candle in enumerate(filtered):
        ts_ms, o, h, l, c, vol = candle
        spot = c
        current_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        price_series.append(spot)

        T_now = time_to_expiry_years(current_dt, expiry_dt)
        cg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'call')
        pg2 = bs_price_and_greeks(spot, strike, T_now, r, iv, 'put')

        total_gamma = (cg2['gamma'] + pg2['gamma']) * contracts
        dS = spot - prev_spot
        gamma_pnl_theo += 0.5 * total_gamma * dS**2
        prev_spot = spot

        if i % check_interval != 0 and i != len(filtered) - 1:
            continue

        portfolio_delta = (cg2['delta'] + pg2['delta']) * contracts + btc_pos
        btc_pos, hedge_avg, hedge_pnl, hedge_count, total_fees = delta_hedge_step(
            portfolio_delta, spot, btc_pos, hedge_avg, hedge_pnl, hedge_count,
            total_fees, delta_threshold, config)

    exit_candle = filtered[-1]
    spot_exit = exit_candle[4]
    exit_dt = datetime.fromtimestamp(exit_candle[0] / 1000, tz=timezone.utc)
    T_exit = time_to_expiry_years(exit_dt, expiry_dt)

    ce = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'call')
    pe = bs_price_and_greeks(spot_exit, strike, T_exit, r, iv, 'put')
    cf2, cfe2 = execute_option_trade(ce['price'], 'SELL', spot_exit, config)
    pf2, pfe2 = execute_option_trade(pe['price'], 'SELL', spot_exit, config)
    exit_proceeds = (cf2 + pf2) * contracts
    total_fees += (cfe2 + pfe2) * contracts

    hedge_pnl, total_fees = close_hedge(btc_pos, hedge_avg, hedge_pnl, total_fees, spot_exit, config)

    option_pnl = exit_proceeds - premium_paid
    rv = calc_realized_vol(price_series, 60.0)

    return {
        'strategy_name': '7. Expiration Scalp',
        'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M'),
        'exit_time': exit_dt.strftime('%Y-%m-%d %H:%M'),
        'date': entry_dt.strftime('%Y-%m-%d'),
        'day_of_week': entry_dt.strftime('%A'),
        'spot_entry': spot_entry, 'spot_exit': spot_exit,
        'strikes': [strike],
        'iv': iv, 'realized_vol': rv,
        'rv_iv_ratio': rv / iv if iv > 0 else 0,
        'premium_paid': premium_paid,
        'option_pnl': option_pnl,
        'hedge_pnl': hedge_pnl,
        'total_fees': total_fees,
        'total_pnl': option_pnl + hedge_pnl - total_fees,
        'hedge_count': hedge_count,
        'gamma_pnl_theoretical': gamma_pnl_theo,
        'duration_min': len(filtered),
    }


# ============================================================
# SESSION BUILDER
# ============================================================
def build_sessions(perp_data, start_hour, end_hour):
    """Build session windows from candle data. Each day gets one session."""
    candle_by_time = {}
    for i, ts in enumerate(perp_data['ticks']):
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        key = (dt.year, dt.month, dt.day, dt.hour, dt.minute)
        candle_by_time[key] = (
            ts,
            perp_data['open'][i],
            perp_data['high'][i],
            perp_data['low'][i],
            perp_data['close'][i],
            perp_data['volume'][i],
        )

    dates = set()
    for ts in perp_data['ticks']:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        dates.add(dt.date())
    dates = sorted(dates)

    sessions = []
    for d in dates:
        session_start = datetime(d.year, d.month, d.day, start_hour, 0, tzinfo=timezone.utc)
        session_end = datetime(d.year, d.month, d.day, end_hour, 0, tzinfo=timezone.utc)

        candles = []
        cursor = session_start
        while cursor < session_end:
            key = (cursor.year, cursor.month, cursor.day, cursor.hour, cursor.minute)
            if key in candle_by_time:
                candles.append(candle_by_time[key])
            cursor += timedelta(minutes=1)

        if len(candles) >= 20:
            sessions.append(candles)

    return sessions


# ============================================================
# DVOL LOOKUP
# ============================================================
def build_dvol_lookup(dvol_data):
    dvol_lookup = {}
    dvol_timestamps = []
    for entry in dvol_data:
        ts = entry[0]
        iv_close = entry[4]
        dvol_lookup[ts] = iv_close / 100
        dvol_timestamps.append(ts)
    dvol_timestamps.sort()

    def get_nearest_dvol(target_ms):
        if not dvol_timestamps:
            return 0.50
        lo, hi = 0, len(dvol_timestamps) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if dvol_timestamps[mid] < target_ms:
                lo = mid + 1
            else:
                hi = mid
        best_ts = dvol_timestamps[lo]
        if lo > 0:
            prev_ts = dvol_timestamps[lo - 1]
            if abs(prev_ts - target_ms) < abs(best_ts - target_ms):
                best_ts = prev_ts
        iv = dvol_lookup.get(best_ts, 0.50)
        return iv if iv > 0.05 else 0.50

    return get_nearest_dvol


# ============================================================
# ANALYTICS
# ============================================================
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


def print_strategy_details(name, results):
    """Print detailed analytics for one strategy."""
    if not results:
        return

    pnls = [r['total_pnl'] for r in results]
    total = sum(pnls)
    avg = np.mean(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

    # Monthly breakdown
    print(f"\n  Monthly Breakdown:")
    monthly = defaultdict(list)
    for r in results:
        month = r['date'][:7]
        monthly[month].append(r['total_pnl'])

    print(f"  {'Month':<10} {'Sess':>6} {'P&L':>12} {'Avg':>10} {'Win%':>7}")
    print(f"  {'-'*50}")
    for month in sorted(monthly.keys()):
        mpnls = monthly[month]
        mwin = sum(1 for p in mpnls if p > 0) / len(mpnls)
        print(f"  {month:<10} {len(mpnls):>6} ${sum(mpnls):>+10,.0f} ${np.mean(mpnls):>+9,.0f} {mwin:>6.0%}")

    # Best/worst
    best = max(results, key=lambda x: x['total_pnl'])
    worst = min(results, key=lambda x: x['total_pnl'])
    print(f"\n  Best session:  {best['entry_time']} | P&L: ${best['total_pnl']:+,.0f} | "
          f"Spot: ${best['spot_entry']:,.0f} -> ${best['spot_exit']:,.0f}")
    print(f"  Worst session: {worst['entry_time']} | P&L: ${worst['total_pnl']:+,.0f} | "
          f"Spot: ${worst['spot_entry']:,.0f} -> ${worst['spot_exit']:,.0f}")

    # RV/IV bucket breakdown
    print(f"\n  RV/IV Bucket Breakdown:")
    buckets = defaultdict(list)
    for r in results:
        b = rv_iv_bucket(r)
        buckets[b].append(r['total_pnl'])

    print(f"  {'Bucket':<20} {'Sess':>6} {'P&L':>12} {'Avg':>10} {'Win%':>7}")
    print(f"  {'-'*60}")
    for b in ['RV<<IV (<0.8)', 'RV<IV (0.8-1.0)', 'RV~IV (1.0-1.2)', 'RV>>IV (>1.2)']:
        if b in buckets:
            bpnls = buckets[b]
            bwin = sum(1 for p in bpnls if p > 0) / len(bpnls)
            print(f"  {b:<20} {len(bpnls):>6} ${sum(bpnls):>+10,.0f} ${np.mean(bpnls):>+9,.0f} {bwin:>6.0%}")


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("0-DTE MULTI-STRATEGY BTC GAMMA SCALPING BACKTEST")
    print("=" * 70)
    print(f"Lookback: {CONFIG['lookback_days']} days")
    print(f"Windows: US (14:00-18:00 UTC), Expiration (06:00-08:00 UTC)")
    print(f"Strategies: 7")
    print()

    # --- STEP 1: Load/fetch data ---
    print("[1/3] Loading data...", flush=True)
    perp_data, dvol_data = load_or_fetch_data(CONFIG['lookback_days'])
    n_bars = len(perp_data['ticks'])
    print(f"  {n_bars:,} candles loaded", flush=True)
    if perp_data['ticks']:
        first_dt = datetime.fromtimestamp(perp_data['ticks'][0] / 1000, tz=timezone.utc)
        last_dt = datetime.fromtimestamp(perp_data['ticks'][-1] / 1000, tz=timezone.utc)
        print(f"  Range: {first_dt.strftime('%Y-%m-%d %H:%M')} to {last_dt.strftime('%Y-%m-%d %H:%M')}")

    get_dvol = build_dvol_lookup(dvol_data)
    print(f"  {len(dvol_data)} DVOL readings", flush=True)

    # --- STEP 2: Build sessions ---
    print(f"\n[2/3] Building sessions...", flush=True)

    us_sessions = build_sessions(perp_data, 14, 18)
    exp_sessions = build_sessions(perp_data, 6, 8)  # 06:00-08:00 for Strategy 7
    # For strategies 1-6 on expiration window, use 04:00-08:00
    exp_full_sessions = build_sessions(perp_data, 4, 8)

    print(f"  US sessions (14-18 UTC): {len(us_sessions)}")
    print(f"  Expiration sessions (04-08 UTC): {len(exp_full_sessions)}")
    print(f"  Expiration scalp sessions (06-08 UTC): {len(exp_sessions)}")

    # --- STEP 3: Run strategies ---
    print(f"\n[3/3] Running strategies...", flush=True)

    # Strategy dispatch: (function, name, list_of_sessions, window_label)
    STRATEGIES = [
        (run_strategy_1, '1. ATM Straddle'),
        (run_strategy_2, '2. OTM Strangle'),
        (run_strategy_3, '3. Calendar Spread'),
        (run_strategy_4, '4. Ratio Backspread'),
        (run_strategy_5, '5. Directional Gamma'),
        (run_strategy_6, '6. Short Gamma'),
    ]

    # Collect all results by strategy name
    all_results = defaultdict(list)
    total_count = 0

    for strat_fn, strat_name in STRATEGIES:
        for window_label, sessions in [('US', us_sessions), ('EXP', exp_full_sessions)]:
            for idx, session_candles in enumerate(sessions):
                entry_ts_ms = session_candles[0][0]
                iv = get_dvol(entry_ts_ms)
                try:
                    result = strat_fn(session_candles, iv, CONFIG)
                except Exception:
                    result = None

                if result:
                    result['window'] = window_label
                    all_results[strat_name].append(result)
                    total_count += 1

                    if total_count % 20 == 0:
                        print(f"    [{total_count} sessions processed] "
                              f"Latest: {strat_name} {window_label} {result['date']}",
                              flush=True)

    # Strategy 7: only on expiration window (06:00-08:00)
    for idx, session_candles in enumerate(exp_sessions):
        entry_ts_ms = session_candles[0][0]
        iv = get_dvol(entry_ts_ms)
        try:
            result = run_strategy_7(session_candles, iv, CONFIG)
        except Exception:
            result = None

        if result:
            result['window'] = 'EXP'
            all_results['7. Expiration Scalp'].append(result)
            total_count += 1

            if total_count % 20 == 0:
                print(f"    [{total_count} sessions processed] "
                      f"Latest: 7. Expiration Scalp EXP {result['date']}", flush=True)

    print(f"\n  Total sessions processed: {total_count}", flush=True)

    # --- RESULTS ---
    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON - 0-DTE BTC GAMMA SCALPING")
    print("=" * 100)

    header = (f"{'Strategy':<22} | {'Sessions':>8} | {'Total P&L':>12} | {'Avg P&L':>10} | "
              f"{'Win%':>6} | {'Avg Fees':>10} | {'Hedges/Sess':>11}")
    print(header)
    print("-" * len(header))

    strategy_order = [
        '1. ATM Straddle',
        '2. OTM Strangle',
        '3. Calendar Spread',
        '4. Ratio Backspread',
        '5. Directional Gamma',
        '6. Short Gamma',
        '7. Expiration Scalp',
    ]

    for sname in strategy_order:
        results = all_results.get(sname, [])
        if not results:
            print(f"{sname:<22} | {'N/A':>8} |")
            continue

        pnls = [r['total_pnl'] for r in results]
        fees = [r['total_fees'] for r in results]
        hedges = [r['hedge_count'] for r in results]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

        print(f"{sname:<22} | {len(results):>8} | ${sum(pnls):>+10,.0f} | ${np.mean(pnls):>+9,.0f} | "
              f"{win_rate:>5.0%} | ${np.mean(fees):>9,.0f} | {np.mean(hedges):>10.1f}")

    # Detailed breakdown per strategy
    for sname in strategy_order:
        results = all_results.get(sname, [])
        if not results:
            continue

        print(f"\n{'='*70}")
        print(f"DETAILS: {sname}")
        print(f"{'='*70}")

        pnls = [r['total_pnl'] for r in results]
        print(f"  Sessions: {len(results)}")
        print(f"  Total P&L: ${sum(pnls):+,.0f}")
        print(f"  Avg P&L: ${np.mean(pnls):+,.0f}")
        print(f"  Median P&L: ${np.median(pnls):+,.0f}")
        print(f"  Std P&L: ${np.std(pnls):,.0f}")
        print(f"  Avg IV: {np.mean([r['iv'] for r in results]):.1%}")
        print(f"  Avg RV: {np.mean([r['realized_vol'] for r in results]):.1%}")

        # Window breakdown
        for wl in ['US', 'EXP']:
            wresults = [r for r in results if r.get('window') == wl]
            if wresults:
                wpnls = [r['total_pnl'] for r in wresults]
                wwin = sum(1 for p in wpnls if p > 0) / len(wpnls)
                print(f"  {wl} window: {len(wresults)} sessions, "
                      f"P&L: ${sum(wpnls):+,.0f}, Avg: ${np.mean(wpnls):+,.0f}, Win: {wwin:.0%}")

        print_strategy_details(sname, results)

    print("\n" + "=" * 70)

    # --- Save results ---
    output_dir = os.path.dirname(os.path.abspath(__file__))
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'0dte_multi_strategy_results_{timestamp}.json')

    save_data = {
        'config': CONFIG,
        'windows': {'US': '14:00-18:00 UTC', 'EXP': '04:00-08:00 UTC', 'EXP_SCALP': '06:00-08:00 UTC'},
        'strategies': {},
    }

    for sname in strategy_order:
        results = all_results.get(sname, [])
        if not results:
            continue
        pnls = [r['total_pnl'] for r in results]
        save_data['strategies'][sname] = {
            'summary': {
                'sessions': len(results),
                'total_pnl': sum(pnls),
                'avg_pnl': float(np.mean(pnls)),
                'std_pnl': float(np.std(pnls)),
                'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
                'avg_fees': float(np.mean([r['total_fees'] for r in results])),
                'avg_hedges': float(np.mean([r['hedge_count'] for r in results])),
                'avg_iv': float(np.mean([r['iv'] for r in results])),
                'avg_rv': float(np.mean([r['realized_vol'] for r in results])),
            },
            'sessions': [{
                'strategy_name': r['strategy_name'],
                'entry_time': r['entry_time'],
                'exit_time': r['exit_time'],
                'date': r['date'],
                'day_of_week': r['day_of_week'],
                'spot_entry': r['spot_entry'],
                'spot_exit': r['spot_exit'],
                'strikes': r['strikes'],
                'iv': r['iv'],
                'realized_vol': r['realized_vol'],
                'rv_iv_ratio': r['rv_iv_ratio'],
                'premium_paid': r['premium_paid'],
                'option_pnl': r['option_pnl'],
                'hedge_pnl': r['hedge_pnl'],
                'total_fees': r['total_fees'],
                'total_pnl': r['total_pnl'],
                'hedge_count': r['hedge_count'],
                'gamma_pnl_theoretical': r['gamma_pnl_theoretical'],
                'duration_min': r['duration_min'],
                'window': r.get('window', ''),
            } for r in results]
        }

    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()
