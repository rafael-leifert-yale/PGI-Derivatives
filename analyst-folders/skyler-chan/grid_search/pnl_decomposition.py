"""
P&L Decomposition for Gamma Scalping Backtest
Breaks down each trade into:
  a) Option intrinsic value change (straddle payoff at expiry vs. entry cost)
  b) Hedging P&L (profit/loss from all delta-hedge share trades)
  c) Transaction costs (commissions + spread + slippage on everything)
  d) Net P&L (should reconcile to a + b - c)

Runs 1 representative config (plain straddle dH=5) over a short window
to diagnose WHERE the money goes.
"""

import sys
import os
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from grid_search.config import StrategyConfig
from grid_search.grid_orchestrator import GridOrchestrator
from grid_search.enhanced_engine import EnhancedTradingEngine
from backtest.data_engine import DataEngine
from backtest.execution_model import ExecutionModel
from datetime import datetime, timedelta
import numpy as np


def flush(*args, **kwargs):
    print(*args, **kwargs, flush=True)


def decompose_single_trade(entry_date, expiry_date, hold_days, config):
    """
    Run a single trade and decompose its P&L into components.

    Returns dict with full breakdown or None if trade fails.
    """
    engine_config = config.to_engine_config()
    data_engine = DataEngine(symbol='SPY', config=engine_config)

    # We need to replicate the GridOrchestrator logic but instrument it
    orch = GridOrchestrator(config)

    engine = EnhancedTradingEngine(engine_config)
    hedge_count = 0
    entry_time = None

    # Track component P&Ls
    option_entry_cost = 0.0       # total premium paid for straddle (mid prices * 100)
    option_exit_proceeds = 0.0    # total premium received at close (mid prices * 100)
    hedge_cash_flows = []         # list of (shares, fill_price, side) for each hedge
    stock_position_at_exit = 0    # shares held at exit
    total_txn_costs = 0.0         # all fees/spread/slippage

    # Track entry/exit mid prices separately from execution
    entry_call_mid = None
    entry_put_mid = None
    exit_call_mid = None
    exit_put_mid = None
    entry_spot = None
    exit_spot = None
    call_strike = None
    put_strike = None

    for day_idx, day in enumerate(hold_days):
        data = orch._fetch_multi_strike_data(day, expiry_date)
        if data is None:
            continue

        merged = data['merged']
        if merged is None or len(merged) < 30:
            continue

        for idx, row in merged.iterrows():
            ts = row['timestamp']
            spot = row['stock_close']

            # ----- ENTRY -----
            if not engine.positions and day_idx == 0:
                entry_time = ts
                entry_spot = spot

                call_prices = orch._row_to_call_prices(row, data['call_strikes'])
                put_prices = orch._row_to_put_prices(row, data['put_strikes'])

                # Record mid prices before execution
                atm = data['atm_strike']
                # For straddle, both legs at ATM
                nearest_call_strike = min(call_prices.keys(), key=lambda s: abs(s - atm))
                nearest_put_strike = min(put_prices.keys(), key=lambda s: abs(s - atm))
                entry_call_mid = call_prices[nearest_call_strike]
                entry_put_mid = put_prices[nearest_put_strike]
                call_strike = nearest_call_strike
                put_strike = nearest_put_strike
                option_entry_cost = (entry_call_mid + entry_put_mid) * 100  # 1 contract each

                entry_result = engine.enter_position(
                    spot=spot,
                    atm_strike=atm,
                    call_prices=call_prices,
                    put_prices=put_prices,
                    call_symbols=data['call_symbols'],
                    put_symbols=data['put_symbols'],
                    timestamp=ts,
                    volume=int(row.get('stock_volume', 100)),
                )
                if 'error' in entry_result:
                    return None

                # Record entry transaction costs (spread + slippage on options)
                for t in entry_result.get('body_trades', []):
                    total_txn_costs += t['fees']
                    # Also track the spread/slippage cost:
                    # filled_price vs mid_price, times 100
                    spread_cost = abs(t['filled_price'] - t['mid_price']) * 100
                    total_txn_costs += spread_cost

                continue

            if not engine.positions:
                continue

            # Update prices
            option_prices = orch._row_to_option_prices(row, data)
            engine.update_option_prices(option_prices)

            # Time to expiry
            expiry_close = expiry_date.replace(hour=16, minute=0)
            tte = max((expiry_close - ts).total_seconds() / (365.25 * 24 * 3600), 1e-6)

            greeks = engine.calculate_portfolio_greeks(
                spot, tte, data.get('risk_free_rate', 0.045), 0.25)

            # ----- HEDGE -----
            if engine.should_hedge(greeks['delta']):
                pre_stock_qty = engine.stock_quantity
                result = engine.execute_hedge(
                    greeks['delta'], spot, ts,
                    volume=int(row.get('stock_volume', 10000)))
                if result:
                    hedge_count += 1
                    hedge_cash_flows.append({
                        'side': result['side'],
                        'shares': result['shares'],
                        'filled_price': result['filled_price'],
                        'fees': result['fees'],
                        'spot_mid': spot,
                        'timestamp': ts,
                    })

        # Track last data/merged for exit
        last_data = data
        last_merged = merged

    # ----- EXIT -----
    if engine.positions and last_data and last_merged is not None and len(last_merged) > 0:
        last_row = last_merged.iloc[-1]
        exit_spot = last_row['stock_close']

        # Record exit mid prices
        for strike in last_data['call_strikes']:
            col = f"call_{int(strike)}_close"
            if col in last_row.index and strike == call_strike:
                exit_call_mid = last_row[col]
        for strike in last_data['put_strikes']:
            col = f"put_{int(strike)}_close"
            if col in last_row.index and strike == put_strike:
                exit_put_mid = last_row[col]

        if exit_call_mid is None:
            # fallback: use engine's current price
            for pos in engine.positions:
                if pos.asset_type == 'call':
                    exit_call_mid = pos.current_price
                elif pos.asset_type == 'put':
                    exit_put_mid = pos.current_price

        option_exit_proceeds = ((exit_call_mid or 0) + (exit_put_mid or 0)) * 100

        # Record stock position before close
        stock_position_at_exit = engine.stock_quantity
        stock_avg_cost_at_exit = engine.stock_avg_cost

        option_prices_exit = orch._row_to_option_prices(last_row, last_data)
        engine.close_all_positions(
            option_prices_exit, exit_spot, last_row['timestamp'])

    if entry_time is None:
        return None

    # ============================================================
    # DECOMPOSITION
    # ============================================================

    # (a) Option P&L at mid prices (no execution costs)
    # This is what you'd get if you just bought the straddle and held to expiry
    # with no hedging and no transaction costs
    option_pnl_mid = option_exit_proceeds - option_entry_cost

    # (b) Hedging P&L
    # Each hedge: buy shares at filled_price, later sell (or the reverse).
    # The engine tracks realized_pnl for everything combined.
    # We can compute hedge P&L as: total realized - option-only realized
    # But let's compute it directly from cash flows.
    hedge_buy_cost = 0.0   # total $ spent buying shares
    hedge_sell_proceeds = 0.0  # total $ received selling shares
    hedge_fees = 0.0

    for h in hedge_cash_flows:
        if h['side'] == 'BUY':
            hedge_buy_cost += h['shares'] * h['filled_price']
        else:
            hedge_sell_proceeds += h['shares'] * h['filled_price']
        hedge_fees += h['fees']

    # If we had a stock position at exit, that got closed too
    # The close adds: stock_position_at_exit * exit_fill_price to proceeds (or cost)
    # We need to account for the final stock close
    if stock_position_at_exit > 0:
        # Was long, sold at exit
        hedge_sell_proceeds += stock_position_at_exit * exit_spot  # approx (ignoring slippage on close)
    elif stock_position_at_exit < 0:
        # Was short, bought to cover
        hedge_buy_cost += abs(stock_position_at_exit) * exit_spot

    hedge_pnl_gross = hedge_sell_proceeds - hedge_buy_cost  # before fees on hedges

    # (c) Transaction costs - use the engine's tracked total
    # This includes: option entry spread+slippage, option exit spread+slippage,
    # hedge commissions+spread+slippage, regulatory fees
    total_engine_costs = engine.total_transaction_costs

    # The engine's realized_pnl already accounts for execution prices (including spread)
    # So net P&L = engine.realized_pnl
    net_pnl = engine.realized_pnl

    # Better decomposition using the engine's own accounting:
    # Net P&L = option_pnl_at_execution_prices + hedge_pnl_at_execution_prices
    # The difference between mid-price P&L and execution-price P&L = transaction costs

    return {
        'entry_date': entry_date.strftime('%Y-%m-%d'),
        'expiry_date': expiry_date.strftime('%Y-%m-%d'),
        'entry_spot': entry_spot,
        'exit_spot': exit_spot,
        'spot_move': exit_spot - entry_spot if exit_spot and entry_spot else 0,
        'spot_move_pct': ((exit_spot - entry_spot) / entry_spot * 100) if exit_spot and entry_spot else 0,
        'call_strike': call_strike,
        'put_strike': put_strike,
        'entry_call_mid': entry_call_mid,
        'entry_put_mid': entry_put_mid,
        'exit_call_mid': exit_call_mid,
        'exit_put_mid': exit_put_mid,
        'straddle_entry_cost': option_entry_cost,
        'straddle_exit_value': option_exit_proceeds,
        'option_pnl_mid': option_pnl_mid,
        'hedge_count': hedge_count,
        'hedge_pnl_gross': hedge_pnl_gross,
        'hedge_fees': hedge_fees,
        'total_txn_costs': total_engine_costs,
        'net_pnl': net_pnl,
        'stock_position_at_exit': stock_position_at_exit,
    }


def main():
    start = '2024-06-01'
    end = '2024-08-31'

    config = StrategyConfig(dte=3, hedge_delta_threshold=5)

    flush(f'\n{"="*80}')
    flush(f'P&L DECOMPOSITION: {config.name}')
    flush(f'{"="*80}')
    flush(f'Period: {start} to {end}')
    flush(f'Structure: ATM straddle, 3-DTE, hedge when |delta| > {config.hedge_delta_threshold} shares')
    flush(f'{"="*80}\n')

    engine_config = config.to_engine_config()
    data_engine = DataEngine(symbol='SPY', config=engine_config)
    trading_days = data_engine.get_0dte_trading_days(start, end)

    dte = config.dte
    results = []
    skipped = 0

    for i, entry_date in enumerate(trading_days):
        expiry_idx = i + dte
        if expiry_idx >= len(trading_days):
            break

        expiry_date = trading_days[expiry_idx]
        hold_days = trading_days[i:expiry_idx + 1]

        if i % 10 == 0:
            flush(f'Trade {i+1}, entry {entry_date.date()}, expiry {expiry_date.date()}...')

        try:
            result = decompose_single_trade(entry_date, expiry_date, hold_days, config)
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
            flush(f'  ERROR on {entry_date.date()}: {e}')

    if not results:
        flush('No results!')
        return

    # ============================================================
    # TRADE-BY-TRADE TABLE
    # ============================================================
    flush(f'\n\n{"="*140}')
    flush(f'TRADE-BY-TRADE P&L DECOMPOSITION ({len(results)} trades, {skipped} skipped)')
    flush(f'{"="*140}')

    flush(f'\n{"Entry":<12} {"Expiry":<12} {"Spot":<8} {"Move":<8} {"Move%":<7} '
          f'{"Strad$":<9} {"OptPnL":<9} {"Hedges":<7} {"HedgePnL":<10} '
          f'{"TxnCost":<9} {"NetPnL":<10}')
    flush('-' * 140)

    for r in results:
        flush(f'{r["entry_date"]:<12} {r["expiry_date"]:<12} '
              f'${r["entry_spot"]:>6.0f} '
              f'{"+" if r["spot_move"]>=0 else ""}{r["spot_move"]:>5.1f} '
              f'{r["spot_move_pct"]:>5.1f}% '
              f'${r["straddle_entry_cost"]:>7.0f} '
              f'${r["option_pnl_mid"]:>7.0f} '
              f'{r["hedge_count"]:>5} '
              f'${r["hedge_pnl_gross"]:>8.0f} '
              f'${r["total_txn_costs"]:>7.1f} '
              f'${r["net_pnl"]:>8.0f}')

    # ============================================================
    # AGGREGATE STATISTICS
    # ============================================================
    flush(f'\n\n{"="*80}')
    flush(f'AGGREGATE DECOMPOSITION')
    flush(f'{"="*80}')

    total_option_pnl = sum(r['option_pnl_mid'] for r in results)
    total_hedge_pnl = sum(r['hedge_pnl_gross'] for r in results)
    total_txn = sum(r['total_txn_costs'] for r in results)
    total_net = sum(r['net_pnl'] for r in results)
    total_straddle_cost = sum(r['straddle_entry_cost'] for r in results)
    n = len(results)

    flush(f'\nTotal trades:              {n}')
    flush(f'Total straddle premium:    ${total_straddle_cost:>12,.0f}')
    flush(f'')
    flush(f'(a) Option P&L (mid):      ${total_option_pnl:>12,.0f}  '
          f'(avg ${total_option_pnl/n:>8,.0f}/trade)  '
          f'-- straddle payoff minus premium')
    flush(f'(b) Hedge P&L (gross):     ${total_hedge_pnl:>12,.0f}  '
          f'(avg ${total_hedge_pnl/n:>8,.0f}/trade)  '
          f'-- profit from share trading')
    flush(f'(c) Transaction costs:     ${total_txn:>12,.0f}  '
          f'(avg ${total_txn/n:>8,.0f}/trade)  '
          f'-- spread + slippage + fees')
    flush(f'')
    flush(f'Net P&L (engine):          ${total_net:>12,.0f}  '
          f'(avg ${total_net/n:>8,.0f}/trade)')

    # Decomposition as % of total loss
    total_loss = abs(total_net) if total_net != 0 else 1
    flush(f'\n--- Loss Attribution ---')
    flush(f'Option P&L (theta decay):  ${total_option_pnl:>10,.0f}  '
          f'({abs(total_option_pnl)/total_loss*100:>5.1f}% of net loss)')
    flush(f'Hedge drag (buy hi/sell lo):${total_hedge_pnl:>9,.0f}  '
          f'({abs(total_hedge_pnl)/total_loss*100:>5.1f}% of net loss)')
    flush(f'Transaction costs:         ${total_txn:>10,.0f}  '
          f'({abs(total_txn)/total_loss*100:>5.1f}% of net loss)')

    # Win/loss analysis
    winners = [r for r in results if r['net_pnl'] > 0]
    losers = [r for r in results if r['net_pnl'] <= 0]

    flush(f'\n--- Winners vs Losers ---')
    flush(f'Winners: {len(winners)}/{n} ({len(winners)/n*100:.0f}%)')
    if winners:
        flush(f'  Avg net P&L:     ${np.mean([w["net_pnl"] for w in winners]):>8,.0f}')
        flush(f'  Avg option P&L:  ${np.mean([w["option_pnl_mid"] for w in winners]):>8,.0f}')
        flush(f'  Avg hedge P&L:   ${np.mean([w["hedge_pnl_gross"] for w in winners]):>8,.0f}')
        flush(f'  Avg |spot move|: {np.mean([abs(w["spot_move"]) for w in winners]):>8.2f}')
        flush(f'  Avg hedges:      {np.mean([w["hedge_count"] for w in winners]):>8.1f}')

    flush(f'Losers:  {len(losers)}/{n} ({len(losers)/n*100:.0f}%)')
    if losers:
        flush(f'  Avg net P&L:     ${np.mean([l["net_pnl"] for l in losers]):>8,.0f}')
        flush(f'  Avg option P&L:  ${np.mean([l["option_pnl_mid"] for l in losers]):>8,.0f}')
        flush(f'  Avg hedge P&L:   ${np.mean([l["hedge_pnl_gross"] for l in losers]):>8,.0f}')
        flush(f'  Avg |spot move|: {np.mean([abs(l["spot_move"]) for l in losers]):>8.2f}')
        flush(f'  Avg hedges:      {np.mean([l["hedge_count"] for l in losers]):>8.1f}')

    # Move size analysis
    flush(f'\n--- Spot Move vs Straddle Cost ---')
    avg_straddle = np.mean([r['straddle_entry_cost'] for r in results])
    avg_move = np.mean([abs(r['spot_move']) for r in results])
    avg_move_dollar = avg_move * 100  # value per 100 shares
    flush(f'Avg straddle cost (premium paid): ${avg_straddle:>8,.0f}')
    flush(f'Avg absolute spot move (3-day):   ${avg_move:>8.2f} = ${avg_move_dollar:>6.0f} per 100sh')
    flush(f'Breakeven needs move of:          ${avg_straddle/100:>8.2f} (straddle cost / 100)')
    flush(f'Ratio (move / breakeven):         {avg_move / (avg_straddle/100):.2f}x')

    # Theta analysis
    flush(f'\n--- Theta Decay Analysis ---')
    option_pnl_as_pct_of_premium = total_option_pnl / total_straddle_cost * 100
    flush(f'Total premium paid:    ${total_straddle_cost:>10,.0f}')
    flush(f'Total option P&L:      ${total_option_pnl:>10,.0f}')
    flush(f'Premium retained:      {100 + option_pnl_as_pct_of_premium:.1f}%')
    flush(f'  (i.e., on average {abs(option_pnl_as_pct_of_premium):.1f}% of premium is lost to theta)')

    avg_entry_straddle_price = np.mean([r['entry_call_mid'] + r['entry_put_mid'] for r in results if r['entry_call_mid'] and r['entry_put_mid']])
    avg_exit_straddle_price = np.mean([(r['exit_call_mid'] or 0) + (r['exit_put_mid'] or 0) for r in results])
    flush(f'Avg straddle entry price: ${avg_entry_straddle_price:.2f}')
    flush(f'Avg straddle exit price:  ${avg_exit_straddle_price:.2f}')
    flush(f'Avg decay:                ${avg_entry_straddle_price - avg_exit_straddle_price:.2f} '
          f'({(avg_entry_straddle_price - avg_exit_straddle_price)/avg_entry_straddle_price*100:.0f}%)')

    # Hedge efficiency
    flush(f'\n--- Hedge Efficiency ---')
    total_hedges = sum(r['hedge_count'] for r in results)
    flush(f'Total hedge trades:    {total_hedges}')
    flush(f'Avg hedges per trade:  {total_hedges/n:.1f}')
    if total_hedges > 0:
        flush(f'Hedge P&L per hedge:   ${total_hedge_pnl/total_hedges:>8.2f}')
    flush(f'Total hedge P&L:       ${total_hedge_pnl:>10,.0f}')
    flush(f'Hedge P&L as % of option loss: '
          f'{total_hedge_pnl/abs(total_option_pnl)*100:.1f}%' if total_option_pnl != 0 else 'N/A')

    flush(f'\n{"="*80}')
    flush(f'DIAGNOSIS SUMMARY')
    flush(f'{"="*80}')

    # Determine primary cause
    components = {
        'Theta decay (option P&L < 0)': total_option_pnl,
        'Hedge drag (hedge P&L < 0)': total_hedge_pnl,
        'Transaction costs': -total_txn,
    }

    flush(f'\nThe ${abs(total_net):,.0f} total loss breaks down as:')
    for name, val in sorted(components.items(), key=lambda x: x[1]):
        if val < 0:
            flush(f'  {name}: ${val:>10,.0f} ({abs(val)/total_loss*100:.0f}% of loss)')
    for name, val in sorted(components.items(), key=lambda x: x[1]):
        if val >= 0:
            flush(f'  {name}: ${val:>10,.0f} (OFFSET, reduces loss)')

    flush(f'\nDone.')


if __name__ == '__main__':
    main()
