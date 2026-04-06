"""
Trading Engine - Core Strategy Logic
Implements zero-DTE gamma scalping strategy
"""

import pandas as pd
import numpy as np
from datetime import datetime, time
from typing import Dict, List, Optional
import sys
sys.path.append('..')
from utils.greeks import GreeksCalculator
from backtest.execution_model import ExecutionModel


class Position:
    """Represents a trading position"""
    def __init__(self, symbol: str, asset_type: str, quantity: int,
                 entry_price: float, entry_time: datetime):
        self.symbol = symbol
        self.asset_type = asset_type  # 'call', 'put', 'stock'
        self.quantity = quantity
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.current_price = entry_price


class TradingEngine:
    """
    Implements gamma scalping strategy logic

    Strategy:
    1. Enter ATM straddle at 9:31 AM
    2. Monitor portfolio delta every minute
    3. Hedge when |delta| > threshold
    4. Exit all positions by 3:55 PM
    """

    def __init__(self, config: Dict):
        self.config = config
        self.positions = {
            'options': [],
            'stock': {'quantity': 0, 'avg_cost': 0.0}
        }
        self.trade_log = []
        self.pnl = {'realized': 0.0, 'unrealized': 0.0}
        self.execution_model = ExecutionModel(config)
        self.total_transaction_costs = 0.0  # Track cumulative costs

    def enter_straddle(self, call_price: float, put_price: float,
                      strike: float, timestamp: datetime,
                      call_symbol: str, put_symbol: str,
                      volume: int = 100) -> Dict:
        """Enter long straddle position with realistic execution"""
        contracts = self.config.get('contracts_per_straddle', 1)

        # Execute call purchase with realistic fills
        call_execution = self.execution_model.execute_trade(
            trade={
                'asset_type': 'option',
                'side': 'BUY',
                'quantity': contracts,
                'mid_price': call_price
            },
            market_data={
                'volume': volume,
                'current_time': timestamp.time()
            }
        )

        # Execute put purchase with realistic fills
        put_execution = self.execution_model.execute_trade(
            trade={
                'asset_type': 'option',
                'side': 'BUY',
                'quantity': contracts,
                'mid_price': put_price
            },
            market_data={
                'volume': volume,
                'current_time': timestamp.time()
            }
        )

        # Record filled prices
        call_filled = call_execution['executed_price']
        put_filled = put_execution['executed_price']

        # Create positions
        call_pos = Position(call_symbol, 'call', contracts, call_filled, timestamp)
        self.positions['options'].append(call_pos)

        put_pos = Position(put_symbol, 'put', contracts, put_filled, timestamp)
        self.positions['options'].append(put_pos)

        # Calculate total cost including slippage and fees
        call_cost = call_execution['total_cost']
        put_cost = put_execution['total_cost']
        total_cost = call_cost + put_cost
        total_fees = call_execution['transaction_cost'] + put_execution['transaction_cost']

        self.pnl['realized'] -= total_cost  # Cash outflow
        self.total_transaction_costs += total_fees

        trade = {
            'timestamp': timestamp,
            'action': 'ENTER_STRADDLE',
            'strike': strike,
            'call_mid': call_price,
            'put_mid': put_price,
            'call_filled': call_filled,
            'put_filled': put_filled,
            'contracts': contracts,
            'cost': total_cost,
            'transaction_costs': total_fees,
            'call_execution': call_execution,
            'put_execution': put_execution
        }
        self.trade_log.append(trade)

        return trade

    def calculate_portfolio_greeks(self, spot_price: float, time_to_expiry: float,
                                   risk_free_rate: float, iv: float) -> Dict:
        """Calculate total portfolio Greeks"""
        total_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0

        for position in self.positions['options']:
            if position.quantity == 0:
                continue

            option_type = position.asset_type
            # Extract strike from OCC symbol: SPY240311C00511000
            # Format: ROOT(3) + DATE(6) + TYPE(1) + STRIKE(8)
            symbol = position.symbol
            # Strike is last 8 characters, divided by 1000
            strike_str = symbol[-8:]
            strike = float(strike_str) / 1000

            try:
                greeks = GreeksCalculator.black_scholes(
                    S=spot_price,
                    K=strike,
                    T=time_to_expiry,
                    r=risk_free_rate,
                    sigma=iv,
                    option_type=option_type
                )

                delta_per_contract = greeks['delta'] * 100
                position_delta = delta_per_contract * position.quantity
                total_delta += position_delta
                total_gamma += greeks['gamma'] * 100 * position.quantity
                total_theta += greeks['theta'] * position.quantity
                total_vega += greeks['vega'] * position.quantity

            except Exception as e:
                print(f"  Warning: Error calculating Greeks: {e}")

        # Add stock delta
        total_delta += self.positions['stock']['quantity']

        return {
            'delta': total_delta,
            'gamma': total_gamma,
            'theta': total_theta,
            'vega': total_vega
        }

    def should_hedge(self, current_delta: float) -> bool:
        """Determine if hedge is needed"""
        threshold = self.config.get('delta_threshold', 0.15) * 100
        return abs(current_delta) > threshold

    def calculate_hedge_size(self, current_delta: float) -> int:
        """Calculate shares to trade for hedge"""
        if not self.should_hedge(current_delta):
            return 0

        # Neutralize delta
        shares = -round(current_delta)

        # Check position limits
        max_position = self.config.get('max_stock_position', 500)
        new_position = self.positions['stock']['quantity'] + shares

        if abs(new_position) > max_position:
            available = max_position - abs(self.positions['stock']['quantity'])
            shares = np.sign(shares) * min(abs(shares), available)

        return shares

    def execute_hedge(self, shares: int, spy_price: float, timestamp: datetime,
                     volume: int = 10000) -> Optional[Dict]:
        """Execute stock hedge with realistic execution"""
        if shares == 0:
            return None

        side = 'BUY' if shares > 0 else 'SELL'

        # Execute stock trade with realistic fills
        execution = self.execution_model.execute_trade(
            trade={
                'asset_type': 'stock',
                'side': side,
                'quantity': abs(shares),
                'mid_price': spy_price
            },
            market_data={
                'volume': volume,
                'current_time': timestamp.time()
            }
        )

        filled_price = execution['executed_price']
        transaction_cost = execution['transaction_cost']
        total_cost = execution['total_cost']

        # Update position
        old_qty = self.positions['stock']['quantity']
        old_cost = self.positions['stock']['avg_cost']

        new_qty = old_qty + shares
        if new_qty != 0:
            new_avg_cost = (old_qty * old_cost + shares * filled_price) / new_qty
        else:
            new_avg_cost = 0.0

        self.positions['stock']['quantity'] = new_qty
        self.positions['stock']['avg_cost'] = new_avg_cost

        # Apply realistic cost (including slippage + fees)
        if side == 'BUY':
            self.pnl['realized'] -= total_cost
        else:
            self.pnl['realized'] += total_cost

        self.total_transaction_costs += transaction_cost

        trade = {
            'timestamp': timestamp,
            'action': 'HEDGE',
            'side': side,
            'shares': abs(shares),
            'mid_price': spy_price,
            'filled_price': filled_price,
            'cost': total_cost,
            'transaction_cost': transaction_cost,
            'stock_position': new_qty,
            'execution': execution
        }
        self.trade_log.append(trade)

        return trade

    def close_all_positions(self, call_price: float, put_price: float,
                           spy_price: float, timestamp: datetime,
                           volume: int = 100) -> Dict:
        """Close all positions at end of day with realistic execution"""

        total_exit_costs = 0.0
        executions = []

        # Close options with realistic fills
        for position in self.positions['options']:
            if position.quantity > 0:
                option_type = position.asset_type
                execution = self.execution_model.execute_trade(
                    trade={
                        'asset_type': 'option',
                        'side': 'SELL',
                        'quantity': position.quantity,
                        'mid_price': position.current_price
                    },
                    market_data={
                        'volume': volume,
                        'current_time': timestamp.time()
                    }
                )

                proceeds = execution['total_cost']  # For sells, this is what we get
                self.pnl['realized'] += proceeds
                total_exit_costs += execution['transaction_cost']
                executions.append({'type': option_type, 'execution': execution})

        # Close stock with realistic fills
        if self.positions['stock']['quantity'] != 0:
            stock_qty = self.positions['stock']['quantity']
            side = 'SELL' if stock_qty > 0 else 'BUY'

            execution = self.execution_model.execute_trade(
                trade={
                    'asset_type': 'stock',
                    'side': side,
                    'quantity': abs(stock_qty),
                    'mid_price': spy_price
                },
                market_data={
                    'volume': 10000,
                    'current_time': timestamp.time()
                }
            )

            if side == 'SELL':
                self.pnl['realized'] += execution['total_cost']
            else:
                self.pnl['realized'] -= execution['total_cost']

            total_exit_costs += execution['transaction_cost']
            executions.append({'type': 'stock', 'execution': execution})

        self.total_transaction_costs += total_exit_costs

        trade = {
            'timestamp': timestamp,
            'action': 'CLOSE_ALL',
            'call_mid': call_price,
            'put_mid': put_price,
            'spy_mid': spy_price,
            'exit_costs': total_exit_costs,
            'final_pnl': self.pnl['realized'],
            'executions': executions
        }
        self.trade_log.append(trade)

        # Clear positions
        self.positions = {
            'options': [],
            'stock': {'quantity': 0, 'avg_cost': 0.0}
        }

        return trade

    def update_option_prices(self, call_price: float, put_price: float):
        """Update current option prices"""
        for position in self.positions['options']:
            if 'C' in position.symbol:
                position.current_price = call_price
            else:
                position.current_price = put_price

    def calculate_pnl(self, call_price: float, put_price: float, spy_price: float) -> Dict:
        """Calculate current P&L"""
        unrealized = 0.0

        # Options P&L
        for position in self.positions['options']:
            if position.quantity > 0:
                pnl_per_contract = (position.current_price - position.entry_price) * 100
                unrealized += pnl_per_contract * position.quantity

        # Stock P&L
        if self.positions['stock']['quantity'] != 0:
            stock_pnl = (spy_price - self.positions['stock']['avg_cost']) * self.positions['stock']['quantity']
            unrealized += stock_pnl

        self.pnl['unrealized'] = unrealized

        return {
            'realized': self.pnl['realized'],
            'unrealized': unrealized,
            'total': self.pnl['realized'] + unrealized
        }
