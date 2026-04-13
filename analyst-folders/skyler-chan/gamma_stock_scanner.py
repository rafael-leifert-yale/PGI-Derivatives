"""
Gamma Scalping Stock Scanner
Screens stocks for gamma scalping suitability, then backtests top candidates
across parameter grids (rebalance frequency, delta threshold, holding period).

Two phases:
  Phase 1 - Underlying screen (yfinance): realized vol, intraday range, liquidity
  Phase 2 - Backtest top candidates (Alpaca): actual P&L across parameter combos
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import json
import warnings
import os
import sys
import time as time_module

warnings.filterwarnings('ignore')

# ==================== PHASE 1: STOCK SCREENING ====================

# Broad universe of optionable, liquid stocks across sectors
SCAN_UNIVERSE = [
    # Mega-cap tech (high vol, liquid options)
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'NFLX', 'CRM',
    # ETFs (daily 0-DTE available for SPY/QQQ/IWM/DIA)
    'SPY', 'QQQ', 'IWM', 'DIA', 'XLF', 'XLE', 'ARKK', 'TQQQ', 'SOXL',
    # High-vol / meme / crypto-adjacent
    'MSTR', 'COIN', 'RIOT', 'MARA', 'GME', 'AMC', 'PLTR', 'SOFI', 'HOOD',
    # Biotech / pharma (event-driven vol)
    'MRNA', 'BNTX', 'XBI',
    # Financials
    'JPM', 'GS', 'BAC', 'C', 'MS',
    # Energy / commodities
    'XOM', 'CVX', 'OXY', 'USO', 'GLD', 'SLV',
    # Other high-beta / liquid options
    'BA', 'DIS', 'UBER', 'SNAP', 'SQ', 'SHOP', 'ROKU', 'NET', 'DKNG',
    # Chinese ADRs (high vol)
    'BABA', 'PDD', 'NIO', 'BIDU',
]


def screen_underlying(symbols: List[str], lookback_days: int = 90) -> pd.DataFrame:
    """
    Phase 1: Screen stocks on underlying characteristics that predict
    gamma scalping profitability.

    Key metrics:
    - realized_vol: annualized realized volatility (higher = more gamma P&L)
    - avg_daily_range_pct: average (high-low)/close (intraday movement)
    - avg_dollar_volume: avg daily $ volume (liquidity proxy)
    - vol_of_vol: std of rolling 5-day realized vol (regime changes = opportunity)
    - avg_abs_return: average absolute daily return
    - intraday_trend_ratio: how much of daily range translates to close-to-close
      (lower = more mean-reverting = better for gamma scalping)
    """
    print("=" * 70)
    print("PHASE 1: UNDERLYING STOCK SCREEN")
    print("=" * 70)
    print(f"Universe: {len(symbols)} symbols")
    print(f"Lookback: {lookback_days} days\n")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days + 30)  # extra buffer

    results = []

    for i, sym in enumerate(symbols, 1):
        try:
            print(f"  [{i}/{len(symbols)}] {sym}...", end=" ", flush=True)

            ticker = yf.Ticker(sym)
            hist = ticker.history(start=start_date, end=end_date, interval="1d")

            if hist.empty or len(hist) < 20:
                print("insufficient data")
                continue

            # Use last N trading days
            hist = hist.tail(lookback_days)

            close = hist['Close']
            high = hist['High']
            low = hist['Low']
            volume = hist['Volume']

            # Daily returns
            returns = close.pct_change().dropna()

            # 1. Annualized realized volatility
            realized_vol = returns.std() * np.sqrt(252)

            # 2. Average daily range as % of close
            daily_range_pct = ((high - low) / close).mean()

            # 3. Average dollar volume (liquidity)
            avg_dollar_volume = (close * volume).mean()

            # 4. Vol of vol (rolling 5-day vol, then std of that)
            rolling_vol = returns.rolling(5).std() * np.sqrt(252)
            vol_of_vol = rolling_vol.std()

            # 5. Average absolute return
            avg_abs_return = returns.abs().mean()

            # 6. Intraday trend ratio: |close-to-close| / (high-low)
            # Lower = more mean-reverting intraday = better for gamma scalping
            daily_range = high - low
            close_to_close = close.diff().abs()
            # Avoid division by zero
            valid_mask = daily_range > 0
            if valid_mask.sum() > 10:
                trend_ratio = (close_to_close[valid_mask] / daily_range[valid_mask]).mean()
            else:
                trend_ratio = np.nan

            # 7. Current price (for position sizing context)
            current_price = close.iloc[-1]

            # 8. Estimate option availability - check if options exist
            try:
                expirations = ticker.options
                has_weekly_options = len(expirations) > 6  # weeklies = many expiries
                nearest_expiry_days = None
                if expirations:
                    nearest = datetime.strptime(expirations[0], "%Y-%m-%d")
                    nearest_expiry_days = (nearest - datetime.now()).days
            except Exception:
                has_weekly_options = False
                nearest_expiry_days = None

            # 9. Straddle cost estimate (as % of stock price)
            # Rough estimate: ATM straddle ~ 0.8 * sigma * sqrt(T) * S
            # For 1-day (T=1/252): straddle_pct ~ 0.8 * daily_vol
            est_1d_straddle_pct = 0.8 * (realized_vol / np.sqrt(252))
            est_5d_straddle_pct = 0.8 * (realized_vol / np.sqrt(252)) * np.sqrt(5)

            # 10. Gamma scalping score components
            # Higher realized vol = more gamma P&L potential
            # Lower trend ratio = more mean-reversion = better scalping
            # Higher dollar volume = tighter spreads
            # Higher vol-of-vol = more regime opportunity

            results.append({
                'symbol': sym,
                'price': round(current_price, 2),
                'realized_vol': round(realized_vol, 4),
                'daily_range_pct': round(daily_range_pct, 4),
                'avg_dollar_volume_M': round(avg_dollar_volume / 1e6, 1),
                'vol_of_vol': round(vol_of_vol, 4),
                'avg_abs_return': round(avg_abs_return, 4),
                'trend_ratio': round(trend_ratio, 4) if not np.isnan(trend_ratio) else None,
                'has_weekly_options': has_weekly_options,
                'nearest_expiry_days': nearest_expiry_days,
                'est_1d_straddle_pct': round(est_1d_straddle_pct, 4),
                'est_5d_straddle_pct': round(est_5d_straddle_pct, 4),
            })

            print(f"vol={realized_vol:.1%}  range={daily_range_pct:.2%}  $vol={avg_dollar_volume/1e6:.0f}M")

        except Exception as e:
            print(f"error: {e}")
            continue

    df = pd.DataFrame(results)
    return df


def rank_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rank stocks by composite gamma scalping score.

    Score = weighted sum of normalized metrics:
      - realized_vol (35%): more vol = more gamma P&L
      - daily_range_pct (25%): wider intraday range = more scalping opportunity
      - liquidity_score (20%): higher $ volume = tighter option spreads
      - mean_reversion (10%): lower trend ratio = better for delta hedging
      - vol_of_vol (10%): higher = more regime shifts to exploit
    """
    if df.empty:
        return df

    scored = df.copy()

    # Normalize each metric to 0-100
    def norm(series):
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series([50] * len(series))
        return ((series - mn) / (mx - mn)) * 100

    scored['vol_score'] = norm(scored['realized_vol'])
    scored['range_score'] = norm(scored['daily_range_pct'])

    # Log-scale dollar volume (diminishing returns above a threshold)
    scored['liq_score'] = norm(np.log1p(scored['avg_dollar_volume_M']))

    # Mean reversion: INVERT trend ratio (lower is better)
    if scored['trend_ratio'].notna().sum() > 0:
        tr = scored['trend_ratio'].fillna(scored['trend_ratio'].median())
        scored['mr_score'] = 100 - norm(tr)
    else:
        scored['mr_score'] = 50

    scored['vov_score'] = norm(scored['vol_of_vol'])

    # Composite score
    scored['gamma_score'] = (
        scored['vol_score'] * 0.35 +
        scored['range_score'] * 0.25 +
        scored['liq_score'] * 0.20 +
        scored['mr_score'] * 0.10 +
        scored['vov_score'] * 0.10
    )

    # Sort descending
    scored = scored.sort_values('gamma_score', ascending=False).reset_index(drop=True)
    scored.index = scored.index + 1  # 1-indexed rank

    return scored


# ==================== PHASE 2: PARAMETER GRID BACKTEST ====================

def run_parameter_grid_backtest(symbols: List[str], date_range: Tuple[str, str],
                                 output_dir: str) -> pd.DataFrame:
    """
    Phase 2: Backtest top candidates across parameter grids using Alpaca data.

    Parameter grid:
    - delta_threshold: [0.10, 0.15, 0.20, 0.30]  (when to rehedge)
    - rebalance_interval: [1, 5, 15, 30, 60] minutes (how often to check)
    - contracts: [1] (normalize to 1 contract for comparison)

    For each symbol x parameter combo, run the backtest and record metrics.
    """
    print("\n" + "=" * 70)
    print("PHASE 2: PARAMETER GRID BACKTEST")
    print("=" * 70)

    # Import the existing backtest infrastructure
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backtest.data_engine import DataEngine
    from backtest.trading_engine import TradingEngine
    from utils.greeks import GreeksCalculator

    # Parameter grid
    delta_thresholds = [0.10, 0.15, 0.20, 0.30]
    rebalance_intervals = [1, 5, 15, 30, 60]  # minutes between delta checks

    start_date, end_date = date_range
    all_results = []

    for sym_idx, symbol in enumerate(symbols, 1):
        print(f"\n{'='*50}")
        print(f"[{sym_idx}/{len(symbols)}] BACKTESTING: {symbol}")
        print(f"{'='*50}")

        # Initialize data engine for this symbol
        try:
            data_config = {}
            # Set strike interval for non-ETF symbols
            if symbol not in ('SPY', 'QQQ', 'IWM', 'DIA'):
                # Rough heuristic for strike intervals
                data_config['strike_interval'] = 5  # Most stocks use $5 strikes

            engine = DataEngine(symbol=symbol, config=data_config)
            trading_days = engine.get_0dte_trading_days(start_date, end_date)

            if len(trading_days) == 0:
                print(f"  No 0-DTE days found for {symbol}")
                continue

            # Limit to avoid excessive API calls - sample up to 10 days
            if len(trading_days) > 10:
                # Sample evenly across the range
                indices = np.linspace(0, len(trading_days) - 1, 10, dtype=int)
                trading_days = [trading_days[i] for i in indices]
                print(f"  Sampled {len(trading_days)} days from range")

        except Exception as e:
            print(f"  Failed to init data engine: {e}")
            continue

        # Pre-fetch all day data (reuse across parameter combos)
        day_data_cache = {}
        for day in trading_days:
            try:
                day_data = engine.fetch_day_data(day)
                if day_data['validation']['valid'] or day_data['validation']['severity'] != 'critical':
                    day_data_cache[day] = day_data
            except Exception as e:
                print(f"  Skip {day.date()}: {e}")
                continue

        if len(day_data_cache) < 3:
            print(f"  Only {len(day_data_cache)} valid days - skipping {symbol}")
            continue

        print(f"  {len(day_data_cache)} valid trading days cached")

        # Run parameter grid
        for delta_thresh in delta_thresholds:
            for rebal_interval in rebalance_intervals:
                config = {
                    'symbol': symbol,
                    'contracts_per_straddle': 1,
                    'delta_threshold': delta_thresh,
                    'max_stock_position': 500,
                    'max_daily_loss': 5000,
                    'profit_target': 5000,
                    'rebalance_interval': rebal_interval,
                }

                combo_pnls = []
                combo_hedges = []
                combo_costs = []

                for day, day_data in day_data_cache.items():
                    try:
                        result = _simulate_day_with_interval(
                            config, day_data, rebal_interval
                        )
                        if result:
                            combo_pnls.append(result['daily_pnl'])
                            combo_hedges.append(result['hedge_count'])
                            combo_costs.append(result['transaction_costs'])
                    except Exception:
                        continue

                if len(combo_pnls) < 3:
                    continue

                pnls = np.array(combo_pnls)
                total_pnl = pnls.sum()
                avg_pnl = pnls.mean()
                std_pnl = pnls.std()
                sharpe = (avg_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0
                win_rate = (pnls > 0).mean()
                avg_hedges = np.mean(combo_hedges)
                avg_costs = np.mean(combo_costs)

                result_row = {
                    'symbol': symbol,
                    'delta_threshold': delta_thresh,
                    'rebalance_min': rebal_interval,
                    'days_tested': len(combo_pnls),
                    'total_pnl': round(total_pnl, 2),
                    'avg_daily_pnl': round(avg_pnl, 2),
                    'std_daily_pnl': round(std_pnl, 2),
                    'sharpe': round(sharpe, 2),
                    'win_rate': round(win_rate, 4),
                    'best_day': round(pnls.max(), 2),
                    'worst_day': round(pnls.min(), 2),
                    'avg_hedges_per_day': round(avg_hedges, 1),
                    'avg_transaction_costs': round(avg_costs, 2),
                    'pnl_after_costs': round(avg_pnl - avg_costs, 2),
                }
                all_results.append(result_row)

                status = "+" if avg_pnl > 0 else "-"
                print(f"  {status} dt={delta_thresh:.2f} rb={rebal_interval:2d}min  "
                      f"avg=${avg_pnl:>8.2f}  sharpe={sharpe:>5.2f}  "
                      f"win={win_rate:.0%}  hedges={avg_hedges:.0f}")

    results_df = pd.DataFrame(all_results)
    return results_df


def _simulate_day_with_interval(config: Dict, day_data: Dict,
                                 rebalance_interval: int) -> Dict:
    """
    Run single-day simulation with specific rebalance interval.
    Reuses existing TradingEngine but only checks delta every N minutes.
    """
    from backtest.trading_engine import TradingEngine
    from utils.greeks import GreeksCalculator

    # Merge data
    stock = day_data['stock_bars'].copy()
    stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low', 'stock_close', 'stock_volume']

    call = day_data['call_bars'].copy()
    if len(call) == 0:
        return None
    call_cols = ['timestamp', 'call_open', 'call_high', 'call_low', 'call_close',
                 'call_volume', 'call_trade_count', 'call_vwap']
    if len(call.columns) == len(call_cols):
        call.columns = call_cols
    else:
        return None

    put = day_data['put_bars'].copy()
    if len(put) == 0:
        return None
    put_cols = ['timestamp', 'put_open', 'put_high', 'put_low', 'put_close',
                'put_volume', 'put_trade_count', 'put_vwap']
    if len(put.columns) == len(put_cols):
        put.columns = put_cols
    else:
        return None

    merged = stock.merge(call, on='timestamp', how='left')
    merged = merged.merge(put, on='timestamp', how='left')
    merged['call_close'] = merged['call_close'].ffill()
    merged['put_close'] = merged['put_close'].ffill()
    merged = merged.dropna()

    if len(merged) < 50:
        return None

    # Initialize engine
    engine = TradingEngine(config)
    date = day_data['date']
    strike = day_data['atm_strike']
    risk_free_rate = day_data['risk_free_rate']

    # Enter straddle
    entry_row = merged.iloc[0]
    call_entry = entry_row['call_close']
    put_entry = entry_row['put_close']
    call_volume = entry_row.get('call_volume', 100)

    engine.enter_straddle(
        call_entry, put_entry, strike, entry_row['timestamp'],
        day_data['call_symbol'], day_data['put_symbol'],
        volume=int(call_volume) if not np.isnan(call_volume) else 100
    )

    hedge_count = 0
    last_check_idx = 0

    # Main loop - only check every rebalance_interval minutes
    for idx, (_, row) in enumerate(merged.iterrows()):
        # Only check delta every N minutes
        if idx - last_check_idx < rebalance_interval and idx > 0:
            # Still update option prices for mark-to-market
            engine.update_option_prices(row['call_close'], row['put_close'])
            continue

        last_check_idx = idx
        timestamp = row['timestamp']
        stock_price = row['stock_close']
        call_price = row['call_close']
        put_price = row['put_close']

        engine.update_option_prices(call_price, put_price)

        # Time to expiry
        market_close = date.replace(hour=16, minute=0)
        time_left = (market_close - timestamp).total_seconds() / (365.25 * 24 * 3600)
        time_left = max(time_left, 1e-6)

        iv = 0.25  # Simplified IV assumption

        greeks = engine.calculate_portfolio_greeks(stock_price, time_left, risk_free_rate, iv)
        hedge_size = engine.calculate_hedge_size(greeks['delta'])

        if hedge_size != 0:
            stock_volume = row.get('stock_volume', 10000)
            vol = int(stock_volume) if not np.isnan(stock_volume) else 10000
            engine.execute_hedge(hedge_size, stock_price, timestamp, volume=vol)
            hedge_count += 1

    # Exit
    exit_row = merged.iloc[-1]
    exit_call_vol = exit_row.get('call_volume', 100)
    vol = int(exit_call_vol) if not np.isnan(exit_call_vol) else 100

    engine.close_all_positions(
        exit_row['call_close'], exit_row['put_close'],
        exit_row['stock_close'], exit_row['timestamp'],
        volume=vol
    )

    return {
        'daily_pnl': engine.pnl['realized'],
        'hedge_count': hedge_count,
        'transaction_costs': engine.total_transaction_costs,
    }


# ==================== MAIN SCANNER ====================

def main():
    """Run full gamma scalping stock scanner"""
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scan_results')
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- PHASE 1: Screen underlying stocks ----
    print("\n" + "#" * 70)
    print("# GAMMA SCALPING STOCK SCANNER")
    print(f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 70)

    screen_df = screen_underlying(SCAN_UNIVERSE, lookback_days=90)

    if screen_df.empty:
        print("No stocks passed screening!")
        return

    # Rank candidates
    ranked = rank_candidates(screen_df)

    # Save Phase 1 results
    phase1_path = os.path.join(output_dir, f'phase1_screen_{timestamp}.csv')
    ranked.to_csv(phase1_path)
    print(f"\nPhase 1 results saved to: {phase1_path}")

    # Display top 20
    print("\n" + "=" * 70)
    print("TOP 20 GAMMA SCALPING CANDIDATES (Phase 1 Screen)")
    print("=" * 70)
    display_cols = ['symbol', 'price', 'realized_vol', 'daily_range_pct',
                    'avg_dollar_volume_M', 'trend_ratio', 'vol_of_vol',
                    'has_weekly_options', 'gamma_score']
    print(ranked[display_cols].head(20).to_string())

    # ---- PHASE 2: Backtest top candidates ----
    # Take top 8 candidates for backtesting
    # Prioritize symbols with daily 0-DTE (SPY, QQQ, IWM, DIA) + top scorers
    daily_0dte = {'SPY', 'QQQ', 'IWM', 'DIA'}
    top_symbols = ranked['symbol'].tolist()

    # Ensure daily-0DTE ETFs are included if they scored reasonably
    backtest_symbols = []
    for sym in top_symbols:
        if sym in daily_0dte:
            backtest_symbols.append(sym)
    # Add remaining top scorers (non-ETF get Friday-only 0-DTE)
    for sym in top_symbols:
        if sym not in backtest_symbols and len(backtest_symbols) < 8:
            backtest_symbols.append(sym)

    print(f"\n\nPhase 2 backtest candidates: {backtest_symbols}")

    # Backtest period: last 2 months
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=60)

    backtest_results = run_parameter_grid_backtest(
        backtest_symbols,
        (start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")),
        output_dir
    )

    if backtest_results.empty:
        print("No backtest results produced!")
        return

    # Save Phase 2 results
    phase2_path = os.path.join(output_dir, f'phase2_backtest_{timestamp}.csv')
    backtest_results.to_csv(phase2_path, index=False)
    print(f"\nPhase 2 results saved to: {phase2_path}")

    # ---- ANALYSIS: Find best symbol x parameter combos ----
    print("\n" + "=" * 70)
    print("BEST PARAMETER COMBINATIONS (by Sharpe ratio)")
    print("=" * 70)

    # Best combo per symbol
    best_per_symbol = backtest_results.loc[
        backtest_results.groupby('symbol')['sharpe'].idxmax()
    ].sort_values('sharpe', ascending=False)

    print("\nBest config per symbol:")
    print(best_per_symbol[['symbol', 'delta_threshold', 'rebalance_min',
                            'avg_daily_pnl', 'sharpe', 'win_rate',
                            'avg_hedges_per_day', 'avg_transaction_costs']].to_string(index=False))

    # Overall top 10 combos
    top10 = backtest_results.nlargest(10, 'sharpe')
    print("\n\nTop 10 combos overall:")
    print(top10[['symbol', 'delta_threshold', 'rebalance_min',
                  'avg_daily_pnl', 'sharpe', 'win_rate',
                  'avg_hedges_per_day', 'pnl_after_costs']].to_string(index=False))

    # ---- Summary statistics by parameter ----
    print("\n" + "=" * 70)
    print("PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 70)

    # By delta threshold
    print("\nAvg Sharpe by Delta Threshold:")
    dt_group = backtest_results.groupby('delta_threshold').agg({
        'sharpe': 'mean', 'win_rate': 'mean', 'avg_hedges_per_day': 'mean',
        'avg_transaction_costs': 'mean'
    }).round(3)
    print(dt_group.to_string())

    # By rebalance interval
    print("\nAvg Sharpe by Rebalance Interval (minutes):")
    rb_group = backtest_results.groupby('rebalance_min').agg({
        'sharpe': 'mean', 'win_rate': 'mean', 'avg_hedges_per_day': 'mean',
        'avg_transaction_costs': 'mean'
    }).round(3)
    print(rb_group.to_string())

    # Save full analysis
    analysis = {
        'scan_timestamp': timestamp,
        'phase1_universe_size': len(SCAN_UNIVERSE),
        'phase1_passed': len(screen_df),
        'phase2_symbols_tested': len(backtest_symbols),
        'phase2_total_combos': len(backtest_results),
        'best_per_symbol': best_per_symbol.to_dict('records'),
        'top10_combos': top10.to_dict('records'),
        'param_sensitivity': {
            'by_delta_threshold': dt_group.to_dict(),
            'by_rebalance_interval': rb_group.to_dict(),
        }
    }

    analysis_path = os.path.join(output_dir, f'scan_analysis_{timestamp}.json')
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2, default=str)

    print(f"\n\nFull analysis saved to: {analysis_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
