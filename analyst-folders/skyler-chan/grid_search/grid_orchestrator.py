"""
Grid Search Orchestrator
Extends the base orchestrator to support:
- Multi-strike option data fetching (for strangles and wings)
- Multi-DTE positions (hold across days)
- Configurable exit strategies
- Batch grid search execution with progress tracking
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
import warnings
import sys
import os
import json
import time as time_module

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backtest.data_engine import DataEngine
from grid_search.enhanced_engine import EnhancedTradingEngine
from grid_search.config import StrategyConfig
from utils.greeks import GreeksCalculator


class GridOrchestrator:
    """
    Runs a single StrategyConfig through the full backtest period.
    Handles multi-strike data fetching and multi-DTE holding.
    """

    def __init__(self, strategy_config: StrategyConfig):
        self.cfg = strategy_config
        self.engine_config = strategy_config.to_engine_config()
        self.data_engine = DataEngine(symbol=strategy_config.symbol,
                                       config=self.engine_config)
        self.results: List[dict] = []

    def run(self, start_date: str, end_date: str, quiet: bool = False) -> dict:
        """
        Run backtest for this strategy configuration.

        Returns:
            {config_name, config, metrics, daily_results}
        """
        if not quiet:
            print(f"\n--- {self.cfg.name} ---")

        if self.cfg.dte == 0:
            return self._run_0dte(start_date, end_date, quiet)
        else:
            return self._run_multi_dte(start_date, end_date, quiet)

    # ==================================================================
    # ZERO-DTE PATH
    # ==================================================================

    def _run_0dte(self, start_date: str, end_date: str, quiet: bool) -> dict:
        """Run 0-DTE strategy (enter and exit same day)"""
        trading_days = self.data_engine.get_0dte_trading_days(start_date, end_date)

        for i, date in enumerate(trading_days):
            if not quiet and i % 20 == 0:
                print(f"  Day {i+1}/{len(trading_days)}...")
            try:
                result = self._run_single_0dte_day(date)
                if result:
                    self.results.append(result)
            except Exception as e:
                if not quiet:
                    print(f"  Skip {date.date()}: {e}")

        return self._compile_results()

    def _run_single_0dte_day(self, date: datetime) -> Optional[dict]:
        """Simulate one 0-DTE trading day"""
        # Fetch multi-strike data
        data = self._fetch_multi_strike_data(date, date)
        if data is None:
            return None

        engine = EnhancedTradingEngine(self.engine_config)
        merged = data['merged']
        if merged is None or len(merged) < 50:
            return None

        # Entry on first bar
        entry_row = merged.iloc[0]
        entry_time = entry_row['timestamp']

        entry_result = engine.enter_position(
            spot=entry_row['stock_close'],
            atm_strike=data['atm_strike'],
            call_prices=self._row_to_call_prices(entry_row, data['call_strikes']),
            put_prices=self._row_to_put_prices(entry_row, data['put_strikes']),
            call_symbols=data['call_symbols'],
            put_symbols=data['put_symbols'],
            timestamp=entry_time,
            volume=int(entry_row.get('stock_volume', 100)),
        )

        if 'error' in entry_result:
            return None

        hedge_count = 0
        exit_reason = "eod"

        # Main simulation loop
        for idx, row in merged.iterrows():
            ts = row['timestamp']
            spot = row['stock_close']

            # Update prices
            option_prices = self._row_to_option_prices(row, data)
            engine.update_option_prices(option_prices)

            # Time to expiry
            market_close = date.replace(hour=16, minute=0)
            tte = max((market_close - ts).total_seconds() / (365.25 * 24 * 3600), 1e-6)

            # Greeks
            greeks = engine.calculate_portfolio_greeks(
                spot, tte, data.get('risk_free_rate', 0.045), 0.25)

            # Hedge check
            if engine.should_hedge(greeks['delta']):
                result = engine.execute_hedge(
                    greeks['delta'], spot, ts,
                    volume=int(row.get('stock_volume', 10000)))
                if result:
                    hedge_count += 1

            # Exit trigger check (non-EOD)
            minutes_held = int((ts - entry_time).total_seconds() / 60)
            current_pnl = engine.total_pnl(spot)
            should_exit, reason = engine.check_exit_trigger(ts, current_pnl, minutes_held)
            if should_exit:
                exit_reason = reason
                option_prices_exit = self._row_to_option_prices(row, data)
                engine.close_all_positions(option_prices_exit, spot, ts)
                break

        # EOD exit if still holding
        if engine.positions:
            last_row = merged.iloc[-1]
            option_prices_exit = self._row_to_option_prices(last_row, data)
            engine.close_all_positions(
                option_prices_exit, last_row['stock_close'], last_row['timestamp'])

        return {
            'date': date,
            'daily_pnl': engine.realized_pnl,
            'transaction_costs': engine.total_transaction_costs,
            'hedge_count': hedge_count,
            'exit_reason': exit_reason,
        }

    # ==================================================================
    # MULTI-DTE PATH
    # ==================================================================

    def _run_multi_dte(self, start_date: str, end_date: str, quiet: bool) -> dict:
        """
        Run multi-DTE strategy.
        Enter on day T with options expiring T+DTE, hold across days, exit per strategy.
        """
        dte = self.cfg.dte
        trading_days = self.data_engine.get_0dte_trading_days(start_date, end_date)

        # Group into non-overlapping trades: enter every DTE days
        i = 0
        trade_num = 0
        while i < len(trading_days) - dte:
            entry_date = trading_days[i]
            expiry_date = trading_days[min(i + dte, len(trading_days) - 1)]

            if not quiet and trade_num % 10 == 0:
                print(f"  Trade {trade_num+1}, entry {entry_date.date()}...")

            try:
                hold_days = trading_days[i:i + dte + 1]
                result = self._run_multi_day_trade(entry_date, expiry_date, hold_days)
                if result:
                    self.results.append(result)
                    trade_num += 1
            except Exception as e:
                if not quiet:
                    print(f"  Skip {entry_date.date()}: {e}")

            i += dte + 1  # Move to next non-overlapping entry

        return self._compile_results()

    def _run_multi_day_trade(self, entry_date: datetime, expiry_date: datetime,
                              hold_days: List[datetime]) -> Optional[dict]:
        """Simulate a multi-day gamma scalping trade"""
        # Fetch data for all hold days with the expiry-dated options
        engine = EnhancedTradingEngine(self.engine_config)
        hedge_count = 0
        exit_reason = "expiry"
        entry_time = None

        for day_idx, day in enumerate(hold_days):
            data = self._fetch_multi_strike_data(day, expiry_date)
            if data is None:
                continue

            merged = data['merged']
            if merged is None or len(merged) < 30:
                continue

            for idx, row in merged.iterrows():
                ts = row['timestamp']
                spot = row['stock_close']

                # Entry: first bar of first day
                if not engine.positions and day_idx == 0:
                    entry_time = ts
                    entry_result = engine.enter_position(
                        spot=spot,
                        atm_strike=data['atm_strike'],
                        call_prices=self._row_to_call_prices(row, data['call_strikes']),
                        put_prices=self._row_to_put_prices(row, data['put_strikes']),
                        call_symbols=data['call_symbols'],
                        put_symbols=data['put_symbols'],
                        timestamp=ts,
                        volume=int(row.get('stock_volume', 100)),
                    )
                    if 'error' in entry_result:
                        return None
                    continue

                if not engine.positions:
                    continue

                # Update prices
                option_prices = self._row_to_option_prices(row, data)
                engine.update_option_prices(option_prices)

                # Time to expiry
                expiry_close = expiry_date.replace(hour=16, minute=0)
                tte = max((expiry_close - ts).total_seconds() / (365.25 * 24 * 3600), 1e-6)

                greeks = engine.calculate_portfolio_greeks(
                    spot, tte, data.get('risk_free_rate', 0.045), 0.25)

                if engine.should_hedge(greeks['delta']):
                    result = engine.execute_hedge(
                        greeks['delta'], spot, ts,
                        volume=int(row.get('stock_volume', 10000)))
                    if result:
                        hedge_count += 1

                # Exit trigger
                if entry_time:
                    minutes_held = int((ts - entry_time).total_seconds() / 60)
                    current_pnl = engine.total_pnl(spot)
                    should_exit, reason = engine.check_exit_trigger(
                        ts, current_pnl, minutes_held)
                    if should_exit:
                        exit_reason = reason
                        option_prices_exit = self._row_to_option_prices(row, data)
                        engine.close_all_positions(option_prices_exit, spot, ts)
                        break

            # If exited mid-day, stop looping days
            if not engine.positions and day_idx > 0:
                break

        # Close at end if still holding
        if engine.positions and data and merged is not None and len(merged) > 0:
            last_row = merged.iloc[-1]
            option_prices_exit = self._row_to_option_prices(last_row, data)
            engine.close_all_positions(
                option_prices_exit, last_row['stock_close'], last_row['timestamp'])

        if entry_time is None:
            return None

        return {
            'date': entry_date,
            'daily_pnl': engine.realized_pnl,
            'transaction_costs': engine.total_transaction_costs,
            'hedge_count': hedge_count,
            'exit_reason': exit_reason,
            'hold_days': len(hold_days),
        }

    # ==================================================================
    # DATA HELPERS
    # ==================================================================

    def _fetch_multi_strike_data(self, trade_date: datetime,
                                  expiry_date: datetime) -> Optional[dict]:
        """
        Fetch stock + multiple option strikes for a given trade date.
        Options expire on expiry_date.
        Returns merged DataFrame and strike/symbol mappings.
        """
        try:
            stock_bars = self.data_engine.fetch_stock_bars(trade_date)
            if stock_bars.empty:
                return None

            open_price = stock_bars.iloc[1]['close'] if len(stock_bars) > 1 else stock_bars.iloc[0]['close']
            atm_strike = self.data_engine.find_atm_strike(open_price)

            # Determine all strikes we need
            width = self.cfg.strangle_width
            wing_w = self.cfg.wing_width if self.cfg.wings else 0
            max_offset = max(width, 0) + max(wing_w, 0)

            # Fetch strikes from ATM-max_offset to ATM+max_offset
            strike_range = range(int(atm_strike - max_offset - 2),
                                 int(atm_strike + max_offset + 3))
            # For SPY $1 strikes
            strikes_needed = [float(s) for s in strike_range]

            call_symbols = {}
            put_symbols = {}
            call_prices = {}
            put_prices = {}
            call_dfs = {}
            put_dfs = {}

            for strike in strikes_needed:
                csym = self.data_engine.construct_option_symbol(
                    self.cfg.symbol, expiry_date, strike, 'call')
                psym = self.data_engine.construct_option_symbol(
                    self.cfg.symbol, expiry_date, strike, 'put')
                call_symbols[strike] = csym
                put_symbols[strike] = psym

            # Fetch ATM + body strikes + wing strikes (only what we actually need)
            needed_call_strikes = set()
            needed_put_strikes = set()

            # Body
            if self.cfg.structure == 'straddle':
                needed_call_strikes.add(atm_strike)
                needed_put_strikes.add(atm_strike)
            else:
                needed_call_strikes.add(atm_strike + width)
                needed_put_strikes.add(atm_strike - width)
                # Also fetch ATM in case nearest-strike logic picks it
                needed_call_strikes.add(atm_strike)
                needed_put_strikes.add(atm_strike)

            # Wings
            if self.cfg.wings:
                body_call = atm_strike + width if self.cfg.structure == 'strangle' else atm_strike
                body_put = atm_strike - width if self.cfg.structure == 'strangle' else atm_strike
                needed_call_strikes.add(body_call + wing_w)
                needed_put_strikes.add(body_put - wing_w)

            # Fetch option data for needed strikes
            for strike in needed_call_strikes:
                strike = float(round(strike))
                sym = call_symbols.get(strike)
                if sym:
                    bars = self.data_engine.fetch_option_bars(sym, trade_date)
                    if not bars.empty:
                        call_dfs[strike] = bars
                        call_prices[strike] = bars.iloc[0]['close']

            for strike in needed_put_strikes:
                strike = float(round(strike))
                sym = put_symbols.get(strike)
                if sym:
                    bars = self.data_engine.fetch_option_bars(sym, trade_date)
                    if not bars.empty:
                        put_dfs[strike] = bars
                        put_prices[strike] = bars.iloc[0]['close']

            if not call_dfs or not put_dfs:
                return None

            # Merge into single DataFrame
            merged = self._merge_multi_strike(stock_bars, call_dfs, put_dfs)

            # Risk-free rate
            rfr = self.data_engine.get_risk_free_rate(trade_date)

            return {
                'merged': merged,
                'atm_strike': atm_strike,
                'call_strikes': sorted(call_dfs.keys()),
                'put_strikes': sorted(put_dfs.keys()),
                'call_symbols': call_symbols,
                'put_symbols': put_symbols,
                'call_prices': call_prices,
                'put_prices': put_prices,
                'risk_free_rate': rfr,
            }

        except Exception as e:
            return None

    def _merge_multi_strike(self, stock_bars: pd.DataFrame,
                             call_dfs: Dict[float, pd.DataFrame],
                             put_dfs: Dict[float, pd.DataFrame]) -> Optional[pd.DataFrame]:
        """Merge stock bars with all option strike DataFrames"""
        try:
            stock = stock_bars.copy()
            stock.columns = ['timestamp', 'stock_open', 'stock_high', 'stock_low',
                             'stock_close', 'stock_volume']

            merged = stock.copy()

            for strike, df in call_dfs.items():
                if df.empty:
                    continue
                prefix = f"call_{int(strike)}"
                cols = ['timestamp'] + [f'{prefix}_{c}' for c in df.columns if c != 'timestamp']
                temp = df.copy()
                temp.columns = ['timestamp'] + [f'{prefix}_{c}' for c in df.columns if c != 'timestamp']
                merged = merged.merge(temp[['timestamp', f'{prefix}_close']], on='timestamp', how='left')
                merged[f'{prefix}_close'] = merged[f'{prefix}_close'].ffill()

            for strike, df in put_dfs.items():
                if df.empty:
                    continue
                prefix = f"put_{int(strike)}"
                cols = ['timestamp'] + [f'{prefix}_{c}' for c in df.columns if c != 'timestamp']
                temp = df.copy()
                temp.columns = ['timestamp'] + [f'{prefix}_{c}' for c in df.columns if c != 'timestamp']
                merged = merged.merge(temp[['timestamp', f'{prefix}_close']], on='timestamp', how='left')
                merged[f'{prefix}_close'] = merged[f'{prefix}_close'].ffill()

            merged = merged.dropna()
            return merged

        except Exception:
            return None

    def _row_to_call_prices(self, row, call_strikes: List[float]) -> Dict[float, float]:
        """Extract call prices from a merged row"""
        prices = {}
        for strike in call_strikes:
            col = f"call_{int(strike)}_close"
            if col in row.index:
                prices[strike] = row[col]
        return prices

    def _row_to_put_prices(self, row, put_strikes: List[float]) -> Dict[float, float]:
        """Extract put prices from a merged row"""
        prices = {}
        for strike in put_strikes:
            col = f"put_{int(strike)}_close"
            if col in row.index:
                prices[strike] = row[col]
        return prices

    def _row_to_option_prices(self, row, data: dict) -> Dict[str, float]:
        """Build {symbol: price} map from a merged row for all held options"""
        prices = {}
        for strike in data['call_strikes']:
            col = f"call_{int(strike)}_close"
            sym = data['call_symbols'].get(strike)
            if col in row.index and sym:
                prices[sym] = row[col]
        for strike in data['put_strikes']:
            col = f"put_{int(strike)}_close"
            sym = data['put_symbols'].get(strike)
            if col in row.index and sym:
                prices[sym] = row[col]
        return prices

    # ==================================================================
    # RESULTS
    # ==================================================================

    def _compile_results(self) -> dict:
        """Compile backtest results into summary"""
        if not self.results:
            return {
                'config_name': self.cfg.name,
                'config': self.engine_config,
                'metrics': {},
                'daily_results': [],
            }

        pnls = [r['daily_pnl'] for r in self.results]
        costs = [r['transaction_costs'] for r in self.results]
        hedges = [r['hedge_count'] for r in self.results]

        std = np.std(pnls) if len(pnls) > 1 else 1.0
        metrics = {
            'total_trades': len(pnls),
            'total_pnl': sum(pnls),
            'avg_pnl': np.mean(pnls),
            'median_pnl': np.median(pnls),
            'std_pnl': std,
            'best_trade': max(pnls),
            'worst_trade': min(pnls),
            'win_rate': sum(1 for p in pnls if p > 0) / len(pnls),
            'sharpe': (np.mean(pnls) / std) * np.sqrt(252) if std > 0 else 0,
            'avg_transaction_costs': np.mean(costs),
            'avg_hedges': np.mean(hedges),
            'total_transaction_costs': sum(costs),
            'profit_factor': (
                sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0))
                if sum(p for p in pnls if p < 0) != 0 else float('inf')
            ),
        }

        return {
            'config_name': self.cfg.name,
            'config': self.engine_config,
            'metrics': metrics,
            'daily_results': self.results,
        }
