"""
Strategy Configuration & Parameter Grid
Defines all sweepable dimensions for SPY gamma scalping optimization
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from itertools import product


@dataclass
class StrategyConfig:
    """Single strategy configuration (one point in the parameter grid)"""

    # --- Structure ---
    structure: str = "straddle"          # "straddle" | "strangle"
    strangle_width: float = 0.0          # OTM distance in strike $ (0 = ATM straddle)
    # For strangles: how far OTM each leg is. E.g., 5 means call at ATM+5, put at ATM-5

    # --- Wings ---
    wings: bool = False                  # Whether to sell protective wings
    wing_width: float = 10.0             # How far OTM from the body strikes
    # Wings turn a straddle into iron butterfly, strangle into iron condor

    # --- DTE (days to expiration at entry) ---
    dte: int = 0                         # 0 = zero-DTE, 1-7 = multi-day hold

    # --- Delta hedging ---
    hedge_delta_threshold: int = 15      # Hedge when |portfolio delta| exceeds this (in shares)
    # Range to test: 1, 2, 3, 5, 7, 10, 15

    # --- Exit strategy ---
    exit_strategy: str = "eod"           # "eod" | "fixed_time" | "pnl_stop" | "dte_target"
    exit_time_minutes: int = 385         # Minutes after open to exit (for fixed_time)
    stop_loss: float = -500.0            # Exit if P&L drops below this
    take_profit: float = 500.0           # Exit if P&L exceeds this
    exit_dte: int = 0                    # For multi-DTE: exit when DTE reaches this

    # --- Shared defaults ---
    contracts_per_leg: int = 1
    max_stock_position: int = 500
    risk_free_rate: float = 0.045
    symbol: str = "SPY"

    @property
    def name(self) -> str:
        """Human-readable name for this config"""
        parts = []
        if self.structure == "straddle":
            parts.append("Straddle")
        else:
            parts.append(f"Strangle(w={self.strangle_width})")
        if self.wings:
            parts.append(f"+Wings({self.wing_width})")
        parts.append(f"{self.dte}DTE")
        parts.append(f"dH={self.hedge_delta_threshold}")
        if self.exit_strategy == "pnl_stop":
            parts.append(f"exit=SL{int(self.stop_loss)}_TP{int(self.take_profit)}")
        elif self.exit_strategy == "fixed_time":
            parts.append(f"exit={self.exit_time_minutes}min")
        else:
            parts.append(f"exit={self.exit_strategy}")
        return "_".join(parts)

    def to_engine_config(self) -> dict:
        """Convert to dict consumed by TradingEngine"""
        return {
            'symbol': self.symbol,
            'contracts_per_straddle': self.contracts_per_leg,
            'delta_threshold': self.hedge_delta_threshold / 100,  # Engine expects decimal
            'max_stock_position': self.max_stock_position,
            'max_daily_loss': abs(self.stop_loss),
            'profit_target': self.take_profit,
            'structure': self.structure,
            'strangle_width': self.strangle_width,
            'wings': self.wings,
            'wing_width': self.wing_width,
            'dte': self.dte,
            'exit_strategy': self.exit_strategy,
            'exit_time_minutes': self.exit_time_minutes,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'exit_dte': self.exit_dte,
        }


# ============================================================================
# PARAMETER GRID DEFINITIONS
# ============================================================================

# Each dimension that can be swept
PARAM_GRID = {
    # Structure: straddle vs strangles of varying width
    'structure_configs': [
        {"structure": "straddle", "strangle_width": 0},
        {"structure": "strangle", "strangle_width": 2},
        {"structure": "strangle", "strangle_width": 5},
        {"structure": "strangle", "strangle_width": 10},
        {"structure": "strangle", "strangle_width": 15},
        {"structure": "strangle", "strangle_width": 20},
    ],

    # Wings: none vs various widths
    'wing_configs': [
        {"wings": False, "wing_width": 0},
        {"wings": True, "wing_width": 5},
        {"wings": True, "wing_width": 10},
        {"wings": True, "wing_width": 20},
    ],

    # DTE at entry
    'dte_values': [0, 1, 2, 3, 5, 7],

    # Delta hedge thresholds (in shares of delta)
    'hedge_thresholds': [1, 2, 3, 5, 7, 10, 15],

    # Exit strategies
    'exit_configs': [
        {"exit_strategy": "eod"},
        {"exit_strategy": "pnl_stop", "stop_loss": -200, "take_profit": 200},
        {"exit_strategy": "pnl_stop", "stop_loss": -300, "take_profit": 300},
        {"exit_strategy": "pnl_stop", "stop_loss": -500, "take_profit": 500},
        {"exit_strategy": "fixed_time", "exit_time_minutes": 120},   # 2hr hold
        {"exit_strategy": "fixed_time", "exit_time_minutes": 210},   # 3.5hr hold
        {"exit_strategy": "fixed_time", "exit_time_minutes": 300},   # 5hr hold
    ],
}


def build_full_grid() -> List[StrategyConfig]:
    """
    Build every combination of all parameters.
    WARNING: This is ~7,056 combinations. Use focused grids for faster iteration.
    """
    configs = []
    for struct, wing, dte, threshold, exit_cfg in product(
        PARAM_GRID['structure_configs'],
        PARAM_GRID['wing_configs'],
        PARAM_GRID['dte_values'],
        PARAM_GRID['hedge_thresholds'],
        PARAM_GRID['exit_configs'],
    ):
        cfg = StrategyConfig(
            structure=struct['structure'],
            strangle_width=struct['strangle_width'],
            wings=wing['wings'],
            wing_width=wing['wing_width'],
            dte=dte,
            hedge_delta_threshold=threshold,
            exit_strategy=exit_cfg['exit_strategy'],
            stop_loss=exit_cfg.get('stop_loss', -500),
            take_profit=exit_cfg.get('take_profit', 500),
            exit_time_minutes=exit_cfg.get('exit_time_minutes', 385),
        )
        configs.append(cfg)
    return configs


# ============================================================================
# FOCUSED GRID PRESETS (run these individually for faster results)
# ============================================================================

def grid_delta_sweep() -> List[StrategyConfig]:
    """Sweep 1: Hold structure constant (straddle, 0DTE, EOD exit), vary delta threshold"""
    return [
        StrategyConfig(hedge_delta_threshold=t)
        for t in [1, 2, 3, 5, 7, 10, 15]
    ]


def grid_structure_sweep() -> List[StrategyConfig]:
    """Sweep 2: Hold delta=5, 0DTE. Test straddle vs strangles of varying width"""
    configs = [StrategyConfig(hedge_delta_threshold=5)]  # baseline straddle
    for w in [2, 5, 10, 15, 20]:
        configs.append(StrategyConfig(
            structure="strangle",
            strangle_width=w,
            hedge_delta_threshold=5,
        ))
    return configs


def grid_wings_sweep() -> List[StrategyConfig]:
    """Sweep 3: Test adding wings at various widths (straddle, delta=5, 0DTE)"""
    configs = [StrategyConfig(hedge_delta_threshold=5)]  # no wings baseline
    for w in [5, 10, 20]:
        configs.append(StrategyConfig(
            hedge_delta_threshold=5,
            wings=True,
            wing_width=w,
        ))
    return configs


def grid_dte_sweep() -> List[StrategyConfig]:
    """Sweep 4: Hold structure constant (straddle, delta=5), vary DTE"""
    return [
        StrategyConfig(dte=d, hedge_delta_threshold=5)
        for d in [0, 1, 2, 3, 5, 7]
    ]


def grid_exit_sweep() -> List[StrategyConfig]:
    """Sweep 5: Hold structure constant (straddle, delta=5, 0DTE), vary exit strategy"""
    configs = [
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="eod"),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="pnl_stop",
                       stop_loss=-200, take_profit=200),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="pnl_stop",
                       stop_loss=-300, take_profit=300),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="pnl_stop",
                       stop_loss=-500, take_profit=500),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="fixed_time",
                       exit_time_minutes=120),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="fixed_time",
                       exit_time_minutes=210),
        StrategyConfig(hedge_delta_threshold=5, exit_strategy="fixed_time",
                       exit_time_minutes=300),
    ]
    return configs


def grid_best_combo() -> List[StrategyConfig]:
    """
    Sweep 6: Cross-test the winning DTE (3) with all delta thresholds,
    structures, and exit strategies to find the true optimum.
    """
    configs = []
    # 3-DTE with all delta thresholds
    for dt in [1, 2, 3, 5, 7, 10, 15]:
        configs.append(StrategyConfig(dte=3, hedge_delta_threshold=dt))
    # 3-DTE with strangles
    for w in [2, 5, 10]:
        configs.append(StrategyConfig(dte=3, hedge_delta_threshold=5,
                                       structure="strangle", strangle_width=w))
    # 3-DTE with wings
    for ww in [5, 10, 20]:
        configs.append(StrategyConfig(dte=3, hedge_delta_threshold=5,
                                       wings=True, wing_width=ww))
    # 3-DTE with exit strategies
    configs.append(StrategyConfig(dte=3, hedge_delta_threshold=5,
                                   exit_strategy="pnl_stop",
                                   stop_loss=-300, take_profit=300))
    configs.append(StrategyConfig(dte=3, hedge_delta_threshold=5,
                                   exit_strategy="pnl_stop",
                                   stop_loss=-500, take_profit=500))
    # Also test 2-DTE (had highest win rate)
    for dt in [5, 10, 15]:
        configs.append(StrategyConfig(dte=2, hedge_delta_threshold=dt))
    return configs


GRID_PRESETS = {
    'delta':     grid_delta_sweep,
    'structure': grid_structure_sweep,
    'wings':     grid_wings_sweep,
    'dte':       grid_dte_sweep,
    'exit':      grid_exit_sweep,
    'best':      grid_best_combo,
    'full':      build_full_grid,
}
