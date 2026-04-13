"""
Enhanced Trading Engine
Extends the base TradingEngine to support:
- Strangles (OTM calls + puts at specified width)
- Wing overlays (sell further OTM options to cap risk / collect premium)
- Variable delta hedging thresholds
- P&L-based and time-based exit triggers
"""

import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.greeks import GreeksCalculator
from backtest.execution_model import ExecutionModel


class Position:
    """Represents a trading position"""
    def __init__(self, symbol: str, asset_type: str, quantity: int,
                 entry_price: float, entry_time: datetime, strike: float = 0.0,
                 role: str = "body"):
        self.symbol = symbol
        self.asset_type = asset_type  # 'call', 'put', 'stock'
        self.quantity = quantity       # positive = long, negative = short
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.current_price = entry_price
        self.strike = strike
        self.role = role               # "body" (main position) or "wing" (protective)


class EnhancedTradingEngine:
    """
    Gamma scalping engine supporting straddles, strangles, wings,
    variable delta thresholds, and multiple exit strategies.
    """

    def __init__(self, config: dict):
        self.config = config
        self.positions: List[Position] = []
        self.stock_quantity: int = 0
        self.stock_avg_cost: float = 0.0
        self.trade_log: List[dict] = []
        self.realized_pnl: float = 0.0
        self.total_transaction_costs: float = 0.0
        self.execution_model = ExecutionModel(config)
        self.entry_time: Optional[datetime] = None
        self.peak_pnl: float = 0.0
        self.trough_pnl: float = 0.0

    # ------------------------------------------------------------------
    # ENTRY: straddle, strangle, or with wings
    # ------------------------------------------------------------------

    def enter_position(self, spot: float, atm_strike: float,
                       call_prices: Dict[float, float],
                       put_prices: Dict[float, float],
                       call_symbols: Dict[float, str],
                       put_symbols: Dict[float, str],
                       timestamp: datetime,
                       volume: int = 100) -> dict:
        """
        Enter the configured structure.

        Args:
            spot: current underlying price
            atm_strike: ATM strike
            call_prices: {strike: mid_price} for available calls
            put_prices: {strike: mid_price} for available puts
            call_symbols: {strike: OCC_symbol} for calls
            put_symbols: {strike: OCC_symbol} for puts
            timestamp: entry time
            volume: option volume for execution model
        """
        self.entry_time = timestamp
        structure = self.config.get('structure', 'straddle')
        width = self.config.get('strangle_width', 0)

        # Determine body strikes
        if structure == 'straddle' or width == 0:
            call_strike = atm_strike
            put_strike = atm_strike
        else:
            # Strangle: call OTM by width, put OTM by width
            call_strike = atm_strike + width
            put_strike = atm_strike - width

        # Find nearest available strikes
        call_strike = self._nearest_strike(call_strike, call_prices)
        put_strike = self._nearest_strike(put_strike, put_prices)

        if call_strike is None or put_strike is None:
            return {'error': 'Could not find strikes for body'}

        # Enter body legs (long)
        body_trades = []
        for strike, prices, symbols, opt_type in [
            (call_strike, call_prices, call_symbols, 'call'),
            (put_strike, put_prices, put_symbols, 'put'),
        ]:
            mid = prices.get(strike)
            sym = symbols.get(strike)
            if mid is None or sym is None:
                return {'error': f'No price/symbol for {opt_type} strike {strike}'}

            trade = self._execute_option_trade(
                sym, opt_type, strike, 'BUY',
                self.config.get('contracts_per_straddle', 1),
                mid, timestamp, volume, role="body"
            )
            body_trades.append(trade)

        # Enter wings (short) if configured
        wing_trades = []
        if self.config.get('wings', False):
            wing_w = self.config.get('wing_width', 10)
            wing_call_strike = call_strike + wing_w
            wing_put_strike = put_strike - wing_w

            wing_call_strike = self._nearest_strike(wing_call_strike, call_prices)
            wing_put_strike = self._nearest_strike(wing_put_strike, put_prices)

            if wing_call_strike and wing_put_strike:
                for strike, prices, symbols, opt_type in [
                    (wing_call_strike, call_prices, call_symbols, 'call'),
                    (wing_put_strike, put_prices, put_symbols, 'put'),
                ]:
                    mid = prices.get(strike)
                    sym = symbols.get(strike)
                    if mid is not None and sym is not None:
                        trade = self._execute_option_trade(
                            sym, opt_type, strike, 'SELL',
                            self.config.get('contracts_per_straddle', 1),
                            mid, timestamp, volume, role="wing"
                        )
                        wing_trades.append(trade)

        entry_record = {
            'timestamp': timestamp,
            'action': 'ENTER',
            'structure': structure,
            'call_strike': call_strike,
            'put_strike': put_strike,
            'body_trades': body_trades,
            'wing_trades': wing_trades,
        }
        self.trade_log.append(entry_record)
        return entry_record

    def _execute_option_trade(self, symbol: str, opt_type: str, strike: float,
                               side: str, quantity: int, mid_price: float,
                               timestamp: datetime, volume: int,
                               role: str = "body") -> dict:
        """Execute a single option leg trade"""
        execution = self.execution_model.execute_trade(
            trade={'asset_type': 'option', 'side': side,
                   'quantity': quantity, 'mid_price': mid_price},
            market_data={'volume': volume, 'current_time': timestamp.time()}
        )

        filled = execution['executed_price']
        qty = quantity if side == 'BUY' else -quantity

        pos = Position(symbol, opt_type, qty, filled, timestamp,
                       strike=strike, role=role)
        self.positions.append(pos)

        # P&L impact
        cost = execution['total_cost']
        if side == 'BUY':
            self.realized_pnl -= cost
        else:
            self.realized_pnl += cost

        self.total_transaction_costs += execution['transaction_cost']

        return {
            'symbol': symbol, 'type': opt_type, 'side': side,
            'strike': strike, 'quantity': quantity, 'role': role,
            'mid_price': mid_price, 'filled_price': filled,
            'cost': cost, 'fees': execution['transaction_cost'],
        }

    # ------------------------------------------------------------------
    # GREEKS
    # ------------------------------------------------------------------

    def calculate_portfolio_greeks(self, spot: float, time_to_expiry: float,
                                    risk_free_rate: float, iv: float) -> dict:
        """Calculate aggregate portfolio Greeks"""
        total = {'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}

        for pos in self.positions:
            if pos.quantity == 0:
                continue
            try:
                greeks = GreeksCalculator.black_scholes(
                    S=spot, K=pos.strike, T=time_to_expiry,
                    r=risk_free_rate, sigma=iv,
                    option_type=pos.asset_type
                )
                sign = 1 if pos.quantity > 0 else -1
                n = abs(pos.quantity)
                total['delta'] += greeks['delta'] * 100 * n * sign
                total['gamma'] += greeks['gamma'] * 100 * n * sign
                total['theta'] += greeks['theta'] * n * sign
                total['vega'] += greeks['vega'] * n * sign
            except Exception:
                pass

        # Add stock delta
        total['delta'] += self.stock_quantity
        return total

    # ------------------------------------------------------------------
    # HEDGING
    # ------------------------------------------------------------------

    def should_hedge(self, delta: float) -> bool:
        threshold = self.config.get('delta_threshold', 0.15) * 100
        return abs(delta) > threshold

    def execute_hedge(self, delta: float, spy_price: float,
                      timestamp: datetime, volume: int = 10000) -> Optional[dict]:
        """Hedge to neutralize delta"""
        if not self.should_hedge(delta):
            return None

        shares = -round(delta)
        max_pos = self.config.get('max_stock_position', 500)
        new_pos = self.stock_quantity + shares
        if abs(new_pos) > max_pos:
            available = max_pos - abs(self.stock_quantity)
            shares = int(np.sign(shares) * min(abs(shares), available))

        if shares == 0:
            return None

        side = 'BUY' if shares > 0 else 'SELL'
        execution = self.execution_model.execute_trade(
            trade={'asset_type': 'stock', 'side': side,
                   'quantity': abs(shares), 'mid_price': spy_price},
            market_data={'volume': volume, 'current_time': timestamp.time()}
        )

        filled = execution['executed_price']
        old_qty = self.stock_quantity
        old_cost = self.stock_avg_cost
        new_qty = old_qty + shares
        if new_qty != 0:
            self.stock_avg_cost = (old_qty * old_cost + shares * filled) / new_qty
        else:
            self.stock_avg_cost = 0.0
        self.stock_quantity = new_qty

        if side == 'BUY':
            self.realized_pnl -= execution['total_cost']
        else:
            self.realized_pnl += execution['total_cost']
        self.total_transaction_costs += execution['transaction_cost']

        trade = {
            'timestamp': timestamp, 'action': 'HEDGE', 'side': side,
            'shares': abs(shares), 'filled_price': filled,
            'stock_position': new_qty,
            'fees': execution['transaction_cost'],
        }
        self.trade_log.append(trade)
        return trade

    # ------------------------------------------------------------------
    # EXIT
    # ------------------------------------------------------------------

    def check_exit_trigger(self, timestamp: datetime, current_pnl: float,
                           minutes_since_entry: int) -> Tuple[bool, str]:
        """
        Check if any exit condition is met.

        Returns:
            (should_exit, reason)
        """
        strategy = self.config.get('exit_strategy', 'eod')

        if strategy == 'pnl_stop':
            sl = self.config.get('stop_loss', -500)
            tp = self.config.get('take_profit', 500)
            if current_pnl <= sl:
                return True, f"stop_loss({sl})"
            if current_pnl >= tp:
                return True, f"take_profit({tp})"

        elif strategy == 'fixed_time':
            target_minutes = self.config.get('exit_time_minutes', 385)
            if minutes_since_entry >= target_minutes:
                return True, f"fixed_time({target_minutes}min)"

        # EOD exit is handled by the orchestrator (last bar)
        return False, ""

    def close_all_positions(self, option_prices: Dict[str, float],
                            spy_price: float, timestamp: datetime,
                            volume: int = 100) -> dict:
        """Close all positions with realistic execution"""
        exit_costs = 0.0
        executions = []

        for pos in self.positions:
            if pos.quantity == 0:
                continue
            mid = option_prices.get(pos.symbol, pos.current_price)
            side = 'SELL' if pos.quantity > 0 else 'BUY'
            execution = self.execution_model.execute_trade(
                trade={'asset_type': 'option', 'side': side,
                       'quantity': abs(pos.quantity), 'mid_price': mid},
                market_data={'volume': volume, 'current_time': timestamp.time()}
            )
            if side == 'SELL':
                self.realized_pnl += execution['total_cost']
            else:
                self.realized_pnl -= execution['total_cost']
            exit_costs += execution['transaction_cost']
            executions.append({'symbol': pos.symbol, 'side': side,
                               'execution': execution})

        # Close stock
        if self.stock_quantity != 0:
            side = 'SELL' if self.stock_quantity > 0 else 'BUY'
            execution = self.execution_model.execute_trade(
                trade={'asset_type': 'stock', 'side': side,
                       'quantity': abs(self.stock_quantity), 'mid_price': spy_price},
                market_data={'volume': 10000, 'current_time': timestamp.time()}
            )
            if side == 'SELL':
                self.realized_pnl += execution['total_cost']
            else:
                self.realized_pnl -= execution['total_cost']
            exit_costs += execution['transaction_cost']
            executions.append({'type': 'stock', 'side': side, 'execution': execution})

        self.total_transaction_costs += exit_costs
        self.positions = []
        self.stock_quantity = 0
        self.stock_avg_cost = 0.0

        trade = {
            'timestamp': timestamp, 'action': 'CLOSE_ALL',
            'exit_costs': exit_costs, 'final_pnl': self.realized_pnl,
        }
        self.trade_log.append(trade)
        return trade

    def update_option_prices(self, option_prices: Dict[str, float]):
        """Update current prices for all held options"""
        for pos in self.positions:
            if pos.symbol in option_prices:
                pos.current_price = option_prices[pos.symbol]

    def calculate_unrealized_pnl(self, spy_price: float) -> float:
        """Calculate mark-to-market unrealized P&L"""
        unrealized = 0.0
        for pos in self.positions:
            if pos.quantity == 0:
                continue
            pnl_per = (pos.current_price - pos.entry_price) * 100
            if pos.quantity < 0:
                pnl_per = -pnl_per
            unrealized += pnl_per * abs(pos.quantity)

        if self.stock_quantity != 0:
            unrealized += (spy_price - self.stock_avg_cost) * self.stock_quantity

        return unrealized

    def total_pnl(self, spy_price: float) -> float:
        return self.realized_pnl + self.calculate_unrealized_pnl(spy_price)

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _nearest_strike(target: float, prices: Dict[float, float]) -> Optional[float]:
        """Find the nearest available strike to target"""
        if not prices:
            return None
        available = sorted(prices.keys())
        nearest = min(available, key=lambda s: abs(s - target))
        return nearest
