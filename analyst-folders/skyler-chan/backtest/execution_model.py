"""
Execution Model - Realistic Fill Simulation
Models bid-ask spreads, slippage, and transaction costs
"""

from typing import Dict, Tuple
from datetime import time
import numpy as np


class ExecutionModel:
    """
    Realistic execution simulation for options and stock

    Models:
    - Bid-ask spreads (time-of-day dependent)
    - Slippage (volume-based market impact)
    - Transaction costs (commissions, fees, SEC charges)
    """

    # Commission rates (can be zeroed via config['zero_costs'])
    OPTION_COMMISSION = 0.01  # $ per contract (institutional rate)
    STOCK_COMMISSION = 0.00    # $ per share (zero-commission broker)

    # Regulatory fees
    SEC_FEE_RATE = 0.0000278    # Per dollar of sale proceeds (2024 rate)
    FINRA_TAF_RATE = 0.000166   # Per share (2024 rate)
    OCC_FEE = 0.04             # Per contract (sell side)

    def __init__(self, config: Dict = None):
        self.config = config or {}

    def calculate_bid_ask_spread(self, mid_price: float, asset_type: str,
                                 volume: int, current_time: time) -> Tuple[float, float]:
        """
        Model bid-ask spread based on asset type, liquidity, and time of day

        Args:
            mid_price: Midpoint price
            asset_type: 'stock' or 'option'
            volume: Recent volume (for liquidity assessment)
            current_time: Time of day

        Returns:
            (bid_price, ask_price)
        """

        if asset_type == 'stock':
            spread = self.config.get('stock_spread', 0.01)
            bid = mid_price - spread / 2
            ask = mid_price + spread / 2
            return bid, ask

        elif asset_type == 'option':
            base_spread_pct = self.config.get('option_spread_pct', 0.015)

            # Adjust for time of day (wider at open/close)
            if current_time < time(10, 0):
                time_mult = 1.8  # Wider at open
            elif current_time > time(15, 30):
                time_mult = 2.0  # Wider near close
            else:
                time_mult = 1.0  # Normal mid-day

            # Adjust for liquidity (volume)
            if volume < 50:
                liquidity_mult = 1.5
            elif volume < 100:
                liquidity_mult = 1.2
            else:
                liquidity_mult = 1.0

            # Calculate spread
            spread_pct = base_spread_pct * time_mult * liquidity_mult
            spread_dollars = mid_price * spread_pct

            # Minimum spread of $0.05 (skip if zero-cost mode)
            if base_spread_pct > 0:
                spread_dollars = max(spread_dollars, 0.05)

            bid = mid_price - spread_dollars / 2
            ask = mid_price + spread_dollars / 2

            return bid, ask

        else:
            raise ValueError(f"Unknown asset_type: {asset_type}")

    def calculate_slippage(self, order_size: int, side: str, bid: float,
                          ask: float, volume: int, asset_type: str) -> float:
        """
        Calculate slippage based on order size and market conditions

        Args:
            order_size: Number of contracts/shares
            side: 'BUY' or 'SELL'
            bid, ask: Bid and ask prices
            volume: Recent trading volume
            asset_type: 'stock' or 'option'

        Returns:
            Executed price (including slippage)
        """

        # Base execution at bid/ask
        if side == 'BUY':
            base_price = ask
        else:
            base_price = bid

        spread = ask - bid
        mid = (bid + ask) / 2

        # Calculate market impact based on order size relative to volume
        if volume > 0:
            size_ratio = order_size / max(volume, 1)
        else:
            size_ratio = 1.0  # Assume high impact if no volume data

        # Slippage increases with size ratio
        if asset_type == 'stock':
            # SPY is very liquid - minimal slippage
            impact = min(size_ratio * spread * 0.3, spread * 0.5)
        else:
            # Options less liquid - more slippage
            impact = min(size_ratio * spread * 0.5, spread * 0.8)

        # Additional slippage for large OPTION orders only
        if asset_type == 'option' and order_size > 10:
            additional_slippage = (order_size - 10) * 0.02 * mid
            impact += additional_slippage

        # Apply slippage
        if side == 'BUY':
            executed_price = base_price + impact
        else:
            executed_price = base_price - impact

        return executed_price

    def calculate_transaction_costs(self, trade: Dict) -> float:
        """
        Calculate all transaction costs

        Args:
            trade: Dictionary with:
                - asset_type: 'stock' or 'option'
                - side: 'BUY' or 'SELL'
                - quantity: Number of contracts/shares
                - price: Executed price

        Returns:
            Total transaction cost in dollars
        """

        total_cost = 0.0
        asset_type = trade['asset_type']
        side = trade['side']
        quantity = trade['quantity']
        price = trade['price']

        # Zero-cost mode: skip all fees
        if self.config.get('zero_costs', False):
            return 0.0

        if asset_type == 'option':
            # Option commission
            commission = quantity * self.OPTION_COMMISSION
            total_cost += commission

            # OCC fee (sell side only)
            if side == 'SELL':
                occ_fee = quantity * self.OCC_FEE
                total_cost += occ_fee

        elif asset_type == 'stock':
            # Stock commission (zero for most brokers)
            commission = quantity * self.STOCK_COMMISSION
            total_cost += commission

            # Calculate proceeds/cost
            trade_value = abs(quantity * price)

            # SEC fee (sell side only)
            if side == 'SELL':
                sec_fee = trade_value * self.SEC_FEE_RATE
                total_cost += sec_fee

            # FINRA TAF (both sides, but typically minimal)
            taf = min(abs(quantity) * self.FINRA_TAF_RATE, 7.27)  # Capped at $7.27
            total_cost += taf

        return total_cost

    def execute_trade(self, trade: Dict, market_data: Dict) -> Dict:
        """
        Execute a trade with realistic fill simulation

        Args:
            trade: Dict with:
                - asset_type: 'stock' or 'option'
                - side: 'BUY' or 'SELL'
                - quantity: Contracts/shares
                - mid_price: Midpoint price
            market_data: Dict with:
                - volume: Recent volume
                - current_time: Time of day

        Returns:
            Execution details with:
                - executed_price: Fill price
                - bid, ask: Spread
                - slippage: Slippage amount
                - transaction_cost: Fees and commissions
                - total_cost: executed_price ± transaction_cost
        """

        asset_type = trade['asset_type']
        side = trade['side']
        quantity = trade['quantity']
        mid_price = trade['mid_price']
        volume = market_data.get('volume', 100)
        current_time = market_data.get('current_time', time(12, 0))

        # Calculate bid-ask spread
        bid, ask = self.calculate_bid_ask_spread(
            mid_price, asset_type, volume, current_time
        )

        # Calculate executed price with slippage
        executed_price = self.calculate_slippage(
            quantity, side, bid, ask, volume, asset_type
        )

        # Calculate transaction costs
        transaction_cost = self.calculate_transaction_costs({
            'asset_type': asset_type,
            'side': side,
            'quantity': quantity,
            'price': executed_price
        })

        # Calculate slippage amount
        if side == 'BUY':
            slippage = executed_price - ask
        else:
            slippage = bid - executed_price

        # Total cost (price + fees)
        # For options: multiply by 100 (shares per contract)
        multiplier = 100 if asset_type == 'option' else 1

        if side == 'BUY':
            # Cost = (price + fees) × quantity
            total_cost = (executed_price * multiplier + transaction_cost / quantity) * quantity
        else:
            # Proceeds = (price - fees) × quantity
            total_cost = (executed_price * multiplier - transaction_cost / quantity) * quantity

        return {
            'executed_price': executed_price,
            'bid': bid,
            'ask': ask,
            'spread': ask - bid,
            'slippage': slippage,
            'transaction_cost': transaction_cost,
            'total_cost': total_cost,
            'multiplier': multiplier
        }


# Testing function
def test_execution_model():
    """Test execution model with sample trades"""
    print("="*70)
    print("EXECUTION MODEL TEST")
    print("="*70)

    model = ExecutionModel()

    # Test 1: Buy SPY call option
    print("\nTest 1: Buy 1 SPY call @ $5.00")
    trade = {
        'asset_type': 'option',
        'side': 'BUY',
        'quantity': 1,
        'mid_price': 5.00
    }
    market_data = {
        'volume': 100,
        'current_time': time(10, 30)
    }

    result = model.execute_trade(trade, market_data)
    print(f"  Executed Price: ${result['executed_price']:.4f}")
    print(f"  Bid/Ask: ${result['bid']:.4f} / ${result['ask']:.4f}")
    print(f"  Spread: ${result['spread']:.4f}")
    print(f"  Slippage: ${result['slippage']:.4f}")
    print(f"  Transaction Cost: ${result['transaction_cost']:.2f}")
    print(f"  Total Cost: ${result['total_cost']:.2f}")

    # Test 2: Sell 50 shares of SPY
    print("\nTest 2: Sell 50 shares SPY @ $656.00")
    trade = {
        'asset_type': 'stock',
        'side': 'SELL',
        'quantity': 50,
        'mid_price': 656.00
    }
    market_data = {
        'volume': 10000,
        'current_time': time(14, 0)
    }

    result = model.execute_trade(trade, market_data)
    print(f"  Executed Price: ${result['executed_price']:.4f}")
    print(f"  Bid/Ask: ${result['bid']:.4f} / ${result['ask']:.4f}")
    print(f"  Spread: ${result['spread']:.4f}")
    print(f"  Slippage: ${result['slippage']:.4f}")
    print(f"  Transaction Cost: ${result['transaction_cost']:.2f}")
    print(f"  Total Proceeds: ${result['total_cost']:.2f}")

    print("\n" + "="*70)
    print("TEST COMPLETE")
    print("="*70)


if __name__ == "__main__":
    test_execution_model()
