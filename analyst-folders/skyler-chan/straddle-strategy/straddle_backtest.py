"""
ATM Straddle Backtest with Delta-Based Rolling
================================================
Strategy: Buy ATM straddle, hold through catalyst, roll to re-center
          when delta gets too skewed (instead of stock hedging).

Tests event-driven entries around earnings, FOMC, CPI etc.
Tickers: SPY, QQQ, MSTR, COIN

Uses yfinance for stock data + Black-Scholes for option pricing
with VIX-calibrated IV (or historical vol for individual names).
"""

import pandas as pd
import numpy as np
from scipy.stats import norm
from datetime import datetime, timedelta
import yfinance as yf
import json
import warnings
warnings.filterwarnings('ignore')


# ---------------------------------------------------------------------------
# Black-Scholes pricer (self-contained)
# ---------------------------------------------------------------------------

def bs_price(S, K, T, r, sigma, opt_type):
    """Black-Scholes price. T in years."""
    if T <= 0:
        return max(S - K, 0) if opt_type == 'call' else max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt_type == 'call':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S, K, T, r, sigma, opt_type):
    """Black-Scholes delta."""
    if T <= 0:
        if opt_type == 'call':
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    if opt_type == 'call':
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1.0


def bs_gamma(S, K, T, r, sigma):
    """Black-Scholes gamma (same for calls and puts)."""
    if T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def bs_theta(S, K, T, r, sigma, opt_type):
    """Black-Scholes theta (per calendar day)."""
    if T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    common = -S * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
    if opt_type == 'call':
        return (common - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        return (common + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365


def bs_vega(S, K, T, r, sigma):
    """Black-Scholes vega (per 1 vol point)."""
    if T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return S * norm.pdf(d1) * np.sqrt(T) / 100


def implied_vol_from_straddle(straddle_price, S, K, T, r,
                               lo=0.05, hi=3.0, tol=1e-5, maxiter=100):
    """Bisection IV solver from straddle price."""
    for _ in range(maxiter):
        mid = (lo + hi) / 2
        model_price = bs_price(S, K, T, r, mid, 'call') + bs_price(S, K, T, r, mid, 'put')
        if abs(model_price - straddle_price) < tol:
            return mid
        if model_price > straddle_price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Market events database
# ---------------------------------------------------------------------------

MARKET_EVENTS = {
    # 2024 FOMC meetings
    '2024-01-31': 'FOMC', '2024-03-20': 'FOMC', '2024-05-01': 'FOMC',
    '2024-06-12': 'FOMC', '2024-07-31': 'FOMC', '2024-09-18': 'FOMC',
    '2024-11-07': 'FOMC', '2024-12-18': 'FOMC',
    # 2025 FOMC meetings
    '2025-01-29': 'FOMC', '2025-03-19': 'FOMC', '2025-05-07': 'FOMC',
    '2025-06-18': 'FOMC', '2025-07-30': 'FOMC', '2025-09-17': 'FOMC',
    '2025-10-29': 'FOMC', '2025-12-17': 'FOMC',
    # 2026 FOMC meetings (projected)
    '2026-01-28': 'FOMC', '2026-03-18': 'FOMC',

    # 2024 CPI releases
    '2024-01-11': 'CPI', '2024-02-13': 'CPI', '2024-03-12': 'CPI',
    '2024-04-10': 'CPI', '2024-05-15': 'CPI', '2024-06-12': 'CPI',
    '2024-07-11': 'CPI', '2024-08-14': 'CPI', '2024-09-11': 'CPI',
    '2024-10-10': 'CPI', '2024-11-13': 'CPI', '2024-12-11': 'CPI',
    # 2025 CPI releases
    '2025-01-15': 'CPI', '2025-02-12': 'CPI', '2025-03-12': 'CPI',
    '2025-04-10': 'CPI', '2025-05-13': 'CPI', '2025-06-11': 'CPI',
    '2025-07-15': 'CPI', '2025-08-12': 'CPI', '2025-09-10': 'CPI',
    '2025-10-14': 'CPI', '2025-11-12': 'CPI', '2025-12-10': 'CPI',

    # 2024 NFP releases
    '2024-01-05': 'NFP', '2024-02-02': 'NFP', '2024-03-08': 'NFP',
    '2024-04-05': 'NFP', '2024-05-03': 'NFP', '2024-06-07': 'NFP',
    '2024-07-05': 'NFP', '2024-08-02': 'NFP', '2024-09-06': 'NFP',
    '2024-10-04': 'NFP', '2024-11-01': 'NFP', '2024-12-06': 'NFP',
    # 2025 NFP releases
    '2025-01-10': 'NFP', '2025-02-07': 'NFP', '2025-03-07': 'NFP',
    '2025-04-04': 'NFP', '2025-05-02': 'NFP', '2025-06-06': 'NFP',
    '2025-07-03': 'NFP', '2025-08-01': 'NFP', '2025-09-05': 'NFP',
    '2025-10-03': 'NFP', '2025-11-07': 'NFP', '2025-12-05': 'NFP',

    # Major tariff / trade-war dates (2025 cycle)
    '2025-02-01': 'TARIFF',  # US 10% tariff on China
    '2025-02-04': 'TARIFF',  # Mexico/Canada tariffs announced then paused
    '2025-03-04': 'TARIFF',  # 25% tariffs on Canada/Mexico take effect
    '2025-04-02': 'TARIFF',  # "Liberation Day" reciprocal tariffs announced
    '2025-04-09': 'TARIFF',  # 90-day pause on reciprocal tariffs (except China)
}

# Earnings dates for individual names (approximate)
EARNINGS_DATES = {
    'MSTR': [
        '2024-02-06', '2024-04-29', '2024-08-01', '2024-10-30',
        '2025-02-04', '2025-04-29', '2025-08-05', '2025-10-28',
    ],
    'COIN': [
        '2024-02-15', '2024-05-02', '2024-08-01', '2024-10-30',
        '2025-02-13', '2025-05-08', '2025-08-07', '2025-10-29',
    ],
}


def get_events_near_date(date, symbol, window_days=1):
    """Return list of events within +/- window_days of date."""
    events = []
    date_dt = pd.Timestamp(date)
    for event_date_str, event_type in MARKET_EVENTS.items():
        event_dt = pd.Timestamp(event_date_str)
        if abs((date_dt - event_dt).days) <= window_days:
            events.append((event_date_str, event_type))
    # Check earnings for individual names
    if symbol in EARNINGS_DATES:
        for earn_date_str in EARNINGS_DATES[symbol]:
            earn_dt = pd.Timestamp(earn_date_str)
            if abs((date_dt - earn_dt).days) <= window_days:
                events.append((earn_date_str, 'EARNINGS'))
    return events


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_daily_data(symbol, start, end):
    """Fetch daily OHLCV from yfinance."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end, auto_adjust=True)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def get_vix_data(start, end):
    """Fetch VIX daily close (used as IV proxy for SPY/QQQ)."""
    vix = yf.Ticker('^VIX')
    df = vix.history(start=start, end=end, auto_adjust=True)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df['Close'] / 100  # convert to decimal


def compute_historical_vol(prices, window=20):
    """Rolling historical vol (annualized) from daily close prices."""
    log_ret = np.log(prices / prices.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(252)


def estimate_iv(symbol, date, vix_series, hist_vol_series):
    """
    Estimate implied volatility for a symbol on a given date.
    - SPY/QQQ: use VIX (with small adjustment for QQQ)
    - MSTR/COIN: use historical vol * markup (crypto names trade at a premium)
    """
    if symbol == 'SPY':
        if date in vix_series.index:
            return vix_series.loc[date]
        # fallback: nearest VIX
        nearest = vix_series.index[vix_series.index.get_indexer([date], method='nearest')[0]]
        return vix_series.loc[nearest]
    elif symbol == 'QQQ':
        if date in vix_series.index:
            return vix_series.loc[date] * 1.10  # QQQ ~10% higher IV than SPY
        nearest = vix_series.index[vix_series.index.get_indexer([date], method='nearest')[0]]
        return vix_series.loc[nearest] * 1.10
    else:
        # MSTR, COIN — use historical vol with IV premium
        if date in hist_vol_series.index and not np.isnan(hist_vol_series.loc[date]):
            hv = hist_vol_series.loc[date]
            # IV typically trades at ~1.2-1.5x HV for high-vol names
            return hv * 1.3
        return 0.80  # default high vol for crypto-adjacent names


def find_atm_strike(price, symbol):
    """Round to nearest standard strike interval."""
    if symbol in ('SPY', 'QQQ'):
        return round(price)
    elif price >= 200:
        return round(price / 5) * 5
    elif price >= 25:
        return round(price / 2.5) * 2.5
    else:
        return round(price)


# ---------------------------------------------------------------------------
# Transaction cost model
# ---------------------------------------------------------------------------

def transaction_costs(num_contracts, action='open'):
    """
    Realistic option transaction costs per leg.
    action: 'open' or 'close'
    """
    commission = num_contracts * 0.65  # per contract
    if action == 'close':
        commission += num_contracts * 0.04  # OCC fee on sell
    return commission


def slippage_cost(mid_price, num_contracts, action='open'):
    """
    Estimate slippage: half the bid-ask spread.
    ATM straddles on liquid names: ~1-2% of mid per leg.
    """
    spread_pct = 0.015  # 1.5% of mid price
    slip_per_contract = mid_price * spread_pct * 0.5  # cross half the spread
    return slip_per_contract * num_contracts * 100  # * 100 shares/contract


# ---------------------------------------------------------------------------
# Straddle position
# ---------------------------------------------------------------------------

class StraddlePosition:
    """Tracks an open straddle with Greeks and P&L."""

    def __init__(self, symbol, strike, entry_date, expiry_date, entry_spot,
                 iv, r, num_contracts, entry_call_price, entry_put_price):
        self.symbol = symbol
        self.strike = strike
        self.entry_date = entry_date
        self.expiry_date = expiry_date
        self.entry_spot = entry_spot
        self.iv = iv
        self.r = r
        self.num_contracts = num_contracts
        self.entry_call_price = entry_call_price
        self.entry_put_price = entry_put_price
        self.entry_straddle_price = entry_call_price + entry_put_price
        self.entry_cost = self.entry_straddle_price * 100 * num_contracts

        # Transaction costs on entry
        self.total_costs = (
            transaction_costs(num_contracts * 2, 'open') +  # 2 legs
            slippage_cost(entry_call_price, num_contracts) +
            slippage_cost(entry_put_price, num_contracts)
        )

    def value_at(self, spot, date, iv=None):
        """Mark-to-market value of straddle."""
        T = max((self.expiry_date - date).days / 365, 0)
        vol = iv if iv is not None else self.iv
        call_px = bs_price(spot, self.strike, T, self.r, vol, 'call')
        put_px = bs_price(spot, self.strike, T, self.r, vol, 'put')
        return (call_px + put_px) * 100 * self.num_contracts

    def greeks_at(self, spot, date, iv=None):
        """Portfolio-level Greeks."""
        T = max((self.expiry_date - date).days / 365, 1e-6)
        vol = iv if iv is not None else self.iv
        call_d = bs_delta(spot, self.strike, T, self.r, vol, 'call')
        put_d = bs_delta(spot, self.strike, T, self.r, vol, 'put')
        g = bs_gamma(spot, self.strike, T, self.r, vol)
        call_t = bs_theta(spot, self.strike, T, self.r, vol, 'call')
        put_t = bs_theta(spot, self.strike, T, self.r, vol, 'put')
        v = bs_vega(spot, self.strike, T, self.r, vol)

        n = self.num_contracts
        return {
            'delta': (call_d + put_d) * 100 * n,
            'gamma': g * 2 * 100 * n,
            'theta': (call_t + put_t) * 100 * n,
            'vega': v * 2 * n,
            'call_delta': call_d,
            'put_delta': put_d,
            'T': T,
        }

    def pnl_at(self, spot, date, iv=None):
        """Unrealized P&L including entry costs."""
        mtm = self.value_at(spot, date, iv)
        return mtm - self.entry_cost - self.total_costs

    def close(self, spot, date, iv=None):
        """Close position, return realized P&L after all costs."""
        exit_value = self.value_at(spot, date, iv)
        T = max((self.expiry_date - date).days / 365, 0)
        vol = iv if iv is not None else self.iv
        call_px = bs_price(spot, self.strike, T, self.r, vol, 'call')
        put_px = bs_price(spot, self.strike, T, self.r, vol, 'put')

        exit_costs = (
            transaction_costs(self.num_contracts * 2, 'close') +
            slippage_cost(call_px, self.num_contracts) +
            slippage_cost(put_px, self.num_contracts)
        )
        self.total_costs += exit_costs
        return exit_value - self.entry_cost - self.total_costs


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

class StraddleBacktest:
    """
    Backtests ATM straddle strategy with optional delta-based rolling.

    Modes:
      1. 'hold'        — buy straddle, hold until exit_dte, close
      2. 'roll_delta'  — buy straddle, roll to new ATM strike when
                          |net delta| exceeds threshold
      3. 'event_only'  — only enter around known macro/earnings events
    """

    DEFAULT_CONFIG = {
        'num_contracts': 1,
        'entry_dte': 7,           # days to expiration at entry
        'exit_dte': 0,            # close at expiry (0) or earlier
        'hold_days': None,        # override: close after N calendar days
        'roll_delta_threshold': 0.35,  # |net delta per contract| to trigger roll
        'max_rolls': 5,           # max rolls per trade
        'mode': 'roll_delta',     # 'hold', 'roll_delta', 'event_only'
        'event_window': 1,        # days before event to enter
        'event_types': ['FOMC', 'CPI', 'NFP', 'EARNINGS', 'TARIFF'],
        'risk_free_rate': 0.045,
        'iv_markup': 1.0,         # multiplier on estimated IV (for sensitivity)
    }

    def __init__(self, symbol, config=None):
        self.symbol = symbol
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.trades = []
        self.daily_log = []

    def run(self, start_date='2024-03-01', end_date='2026-04-01'):
        """Run full backtest."""
        print(f"\n{'='*70}")
        print(f"  ATM STRADDLE BACKTEST: {self.symbol}")
        print(f"  Period: {start_date} to {end_date}")
        print(f"  Mode: {self.config['mode']}")
        print(f"  Entry DTE: {self.config['entry_dte']}")
        print(f"  Roll delta threshold: {self.config['roll_delta_threshold']}")
        print(f"{'='*70}\n")

        # Fetch data
        buffer_start = (pd.Timestamp(start_date) - timedelta(days=60)).strftime('%Y-%m-%d')
        price_data = fetch_daily_data(self.symbol, buffer_start, end_date)
        vix_data = get_vix_data(buffer_start, end_date)
        hist_vol = compute_historical_vol(price_data['Close'], window=20)

        # Filter to backtest period
        bt_dates = price_data.loc[start_date:end_date].index

        position = None
        trade_count = 0

        for i, date in enumerate(bt_dates):
            spot = price_data.loc[date, 'Close']
            iv = estimate_iv(self.symbol, date, vix_data, hist_vol) * self.config['iv_markup']
            r = self.config['risk_free_rate']

            # --- Check if we should close existing position ---
            if position is not None:
                days_held = (date - position.entry_date).days
                days_to_expiry = (position.expiry_date - date).days

                # Close conditions
                close_reason = None
                if days_to_expiry <= self.config['exit_dte']:
                    close_reason = 'expiry'
                elif self.config['hold_days'] and days_held >= self.config['hold_days']:
                    close_reason = 'hold_limit'

                if close_reason:
                    realized_pnl = position.close(spot, date, iv)
                    self.trades.append({
                        'symbol': self.symbol,
                        'entry_date': position.entry_date.strftime('%Y-%m-%d'),
                        'exit_date': date.strftime('%Y-%m-%d'),
                        'entry_strike': position.strike,
                        'entry_spot': position.entry_spot,
                        'exit_spot': spot,
                        'entry_iv': position.iv,
                        'exit_iv': iv,
                        'entry_straddle_price': position.entry_straddle_price,
                        'days_held': days_held,
                        'pnl': realized_pnl,
                        'total_costs': position.total_costs,
                        'close_reason': close_reason,
                        'rolls': getattr(position, '_roll_count', 0),
                        'events': get_events_near_date(position.entry_date, self.symbol, 2),
                    })
                    trade_count += 1
                    print(f"  [{trade_count}] {position.entry_date.strftime('%Y-%m-%d')} -> "
                          f"{date.strftime('%Y-%m-%d')} | K={position.strike} | "
                          f"P&L=${realized_pnl:+.2f} | reason={close_reason}")
                    position = None

            # --- Check if we should roll (re-center strike) ---
            if position is not None and self.config['mode'] == 'roll_delta':
                greeks = position.greeks_at(spot, date, iv)
                net_delta_per_contract = abs(greeks['delta']) / (position.num_contracts * 100)

                if (net_delta_per_contract > self.config['roll_delta_threshold']
                        and getattr(position, '_roll_count', 0) < self.config['max_rolls']):
                    # Close old position
                    realized_pnl_old = position.close(spot, date, iv)

                    # Open new ATM straddle with same expiry
                    new_strike = find_atm_strike(spot, self.symbol)
                    T_new = max((position.expiry_date - date).days / 365, 1e-6)
                    new_call = bs_price(spot, new_strike, T_new, r, iv, 'call')
                    new_put = bs_price(spot, new_strike, T_new, r, iv, 'put')

                    old_roll_count = getattr(position, '_roll_count', 0)
                    old_entry_date = position.entry_date
                    old_entry_spot = position.entry_spot
                    old_entry_iv = position.iv
                    old_cumulative_pnl = realized_pnl_old

                    position = StraddlePosition(
                        symbol=self.symbol,
                        strike=new_strike,
                        entry_date=date,
                        expiry_date=position.expiry_date,
                        entry_spot=spot,
                        iv=iv,
                        r=r,
                        num_contracts=self.config['num_contracts'],
                        entry_call_price=new_call,
                        entry_put_price=new_put,
                    )
                    position._roll_count = old_roll_count + 1
                    position._cumulative_roll_pnl = old_cumulative_pnl
                    position._original_entry_date = old_entry_date
                    position._original_entry_spot = old_entry_spot
                    position._original_entry_iv = old_entry_iv

                    print(f"    ROLL #{position._roll_count}: "
                          f"K={new_strike} | delta was {greeks['delta']:+.1f} | "
                          f"roll P&L so far ${old_cumulative_pnl:+.2f}")

            # --- Check if we should enter new position ---
            if position is None:
                should_enter = False

                if self.config['mode'] == 'event_only':
                    events = get_events_near_date(date, self.symbol,
                                                   self.config['event_window'])
                    matching = [e for e in events
                                if e[1] in self.config['event_types']]
                    should_enter = len(matching) > 0
                else:
                    # 'hold' or 'roll_delta': enter whenever we're flat
                    should_enter = True

                if should_enter:
                    strike = find_atm_strike(spot, self.symbol)
                    dte = self.config['entry_dte']
                    expiry = date + timedelta(days=dte)
                    T = dte / 365
                    call_px = bs_price(spot, strike, T, r, iv, 'call')
                    put_px = bs_price(spot, strike, T, r, iv, 'put')

                    position = StraddlePosition(
                        symbol=self.symbol,
                        strike=strike,
                        entry_date=date,
                        expiry_date=expiry,
                        entry_spot=spot,
                        iv=iv,
                        r=r,
                        num_contracts=self.config['num_contracts'],
                        entry_call_price=call_px,
                        entry_put_price=put_px,
                    )
                    position._roll_count = 0
                    position._cumulative_roll_pnl = 0.0

            # Daily log
            if position is not None:
                greeks = position.greeks_at(spot, date, iv)
                unrealized = position.pnl_at(spot, date, iv)
                self.daily_log.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'spot': spot,
                    'iv': iv,
                    'strike': position.strike,
                    'delta': greeks['delta'],
                    'gamma': greeks['gamma'],
                    'theta': greeks['theta'],
                    'vega': greeks['vega'],
                    'unrealized_pnl': unrealized,
                    'days_to_expiry': (position.expiry_date - date).days,
                })

        # Close any remaining position at end
        if position is not None:
            date = bt_dates[-1]
            spot = price_data.loc[date, 'Close']
            iv = estimate_iv(self.symbol, date, vix_data, hist_vol) * self.config['iv_markup']
            realized_pnl = position.close(spot, date, iv)
            self.trades.append({
                'symbol': self.symbol,
                'entry_date': position.entry_date.strftime('%Y-%m-%d'),
                'exit_date': date.strftime('%Y-%m-%d'),
                'entry_strike': position.strike,
                'entry_spot': position.entry_spot,
                'exit_spot': spot,
                'entry_iv': position.iv,
                'exit_iv': iv,
                'entry_straddle_price': position.entry_straddle_price,
                'days_held': (date - position.entry_date).days,
                'pnl': realized_pnl,
                'total_costs': position.total_costs,
                'close_reason': 'end_of_backtest',
                'rolls': getattr(position, '_roll_count', 0),
                'events': get_events_near_date(position.entry_date, self.symbol, 2),
            })

        return self._compute_summary()

    def _compute_summary(self):
        """Compute summary statistics."""
        if not self.trades:
            return {'symbol': self.symbol, 'total_trades': 0, 'total_pnl': 0}

        pnls = [t['pnl'] for t in self.trades]
        costs = [t['total_costs'] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        # Event vs non-event breakdown
        event_trades = [t for t in self.trades if t['events']]
        non_event_trades = [t for t in self.trades if not t['events']]
        event_pnls = [t['pnl'] for t in event_trades]
        non_event_pnls = [t['pnl'] for t in non_event_trades]

        summary = {
            'symbol': self.symbol,
            'mode': self.config['mode'],
            'entry_dte': self.config['entry_dte'],
            'roll_threshold': self.config['roll_delta_threshold'],
            'total_trades': len(pnls),
            'total_pnl': sum(pnls),
            'avg_pnl': np.mean(pnls),
            'median_pnl': np.median(pnls),
            'std_pnl': np.std(pnls),
            'sharpe': np.mean(pnls) / np.std(pnls) * np.sqrt(52) if np.std(pnls) > 0 else 0,
            'win_rate': len(wins) / len(pnls) if pnls else 0,
            'avg_win': np.mean(wins) if wins else 0,
            'avg_loss': np.mean(losses) if losses else 0,
            'best_trade': max(pnls),
            'worst_trade': min(pnls),
            'total_costs': sum(costs),
            'avg_rolls': np.mean([t['rolls'] for t in self.trades]),
            # Event breakdown
            'event_trades': len(event_trades),
            'event_total_pnl': sum(event_pnls) if event_pnls else 0,
            'event_avg_pnl': np.mean(event_pnls) if event_pnls else 0,
            'event_win_rate': len([p for p in event_pnls if p > 0]) / len(event_pnls) if event_pnls else 0,
            'non_event_trades': len(non_event_trades),
            'non_event_total_pnl': sum(non_event_pnls) if non_event_pnls else 0,
            'non_event_avg_pnl': np.mean(non_event_pnls) if non_event_pnls else 0,
        }

        return summary


# ---------------------------------------------------------------------------
# Multi-mode runner
# ---------------------------------------------------------------------------

def run_comparison(symbol, start='2024-03-01', end='2026-04-01'):
    """
    Run 3 modes for a symbol and compare:
      1. Hold (no rolling)
      2. Roll on delta
      3. Event-only with rolling
    """
    results = {}

    # Mode 1: Simple hold
    bt1 = StraddleBacktest(symbol, {'mode': 'hold', 'entry_dte': 7})
    results['hold'] = bt1.run(start, end)
    results['hold']['trades'] = bt1.trades

    # Mode 2: Roll on delta
    bt2 = StraddleBacktest(symbol, {'mode': 'roll_delta', 'entry_dte': 7,
                                     'roll_delta_threshold': 0.35})
    results['roll_delta'] = bt2.run(start, end)
    results['roll_delta']['trades'] = bt2.trades

    # Mode 3: Event-only with rolling
    bt3 = StraddleBacktest(symbol, {'mode': 'event_only', 'entry_dte': 7,
                                     'roll_delta_threshold': 0.35,
                                     'event_window': 1})
    results['event_only'] = bt3.run(start, end)
    results['event_only']['trades'] = bt3.trades

    return results


def print_summary(summary, label=''):
    """Pretty-print a backtest summary."""
    s = summary
    print(f"\n{'='*60}")
    print(f"  {s['symbol']} — {s.get('mode', label).upper()}")
    print(f"{'='*60}")
    print(f"  Total Trades:    {s['total_trades']}")
    print(f"  Total P&L:       ${s['total_pnl']:+,.2f}")
    print(f"  Avg P&L/trade:   ${s['avg_pnl']:+,.2f}")
    print(f"  Median P&L:      ${s['median_pnl']:+,.2f}")
    print(f"  Std Dev:         ${s['std_pnl']:,.2f}")
    print(f"  Sharpe (ann):    {s['sharpe']:+.2f}")
    print(f"  Win Rate:        {s['win_rate']:.1%}")
    print(f"  Avg Win:         ${s['avg_win']:+,.2f}")
    print(f"  Avg Loss:        ${s['avg_loss']:+,.2f}")
    print(f"  Best Trade:      ${s['best_trade']:+,.2f}")
    print(f"  Worst Trade:     ${s['worst_trade']:+,.2f}")
    print(f"  Total Costs:     ${s['total_costs']:,.2f}")
    print(f"  Avg Rolls/Trade: {s['avg_rolls']:.1f}")
    print(f"  ---")
    print(f"  Event Trades:    {s['event_trades']} | "
          f"P&L ${s['event_total_pnl']:+,.2f} | "
          f"Avg ${s['event_avg_pnl']:+,.2f} | "
          f"WR {s['event_win_rate']:.1%}")
    print(f"  Non-Event Trades: {s['non_event_trades']} | "
          f"P&L ${s['non_event_total_pnl']:+,.2f} | "
          f"Avg ${s['non_event_avg_pnl']:+,.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    SYMBOLS = ['SPY', 'QQQ', 'MSTR', 'COIN']
    START = '2024-03-01'
    END = '2026-04-01'

    all_results = {}
    for sym in SYMBOLS:
        print(f"\n\n{'#'*70}")
        print(f"#  {sym}")
        print(f"{'#'*70}")
        all_results[sym] = run_comparison(sym, START, END)

        for mode, summary in all_results[sym].items():
            print_summary(summary)

    # Save results (without non-serializable trade objects)
    output = {}
    for sym in SYMBOLS:
        output[sym] = {}
        for mode, data in all_results[sym].items():
            serializable = {k: v for k, v in data.items()
                           if k != 'trades'}
            serializable['trades'] = [
                {k: v for k, v in t.items() if k != 'events' or isinstance(v, (str, int, float, list))}
                for t in data.get('trades', [])
            ]
            # Convert numpy types
            for k, v in serializable.items():
                if isinstance(v, (np.floating, np.integer)):
                    serializable[k] = float(v)
            for t in serializable['trades']:
                for k, v in t.items():
                    if isinstance(v, (np.floating, np.integer)):
                        t[k] = float(v)
            output[sym][mode] = serializable

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    outfile = f'straddle_backtest_results_{timestamp}.json'
    with open(outfile, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {outfile}")
