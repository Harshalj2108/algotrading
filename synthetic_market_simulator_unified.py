"""
synthetic_market_simulator_unified.py
======================================
Unified Synthetic Crypto Market Simulator — All 3 Phases

A single, self-contained file integrating every engine from Phases 1-3
into one cohesive simulator with a clean API.

Modes
-----
- ``phase1`` : GBM + regime switching + jumps + spread + fees + leverage
- ``phase2`` : Phase 1 + GARCH + volume + slippage + correlation + cascade
- ``phase3`` : Phase 2 + agents + adversarial + latency + dynamic liquidity
- ``full``   : Alias for phase3 (everything enabled)

Usage
-----
>>> from synthetic_market_simulator_unified import build_simulator
>>> sim = build_simulator(mode="full", initial_price=50_000, seed=42)
>>> sim.run(5_000)
>>> sim.summary()

Dependencies: numpy, matplotlib  (stdlib otherwise)
"""

from __future__ import annotations

import abc
import math
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.collections import LineCollection
from matplotlib.patches import Patch


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART I — FOUNDATION  (Phase 1 Core)                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 1 · Regime Configuration
# ===========================================================================

# 1 step = 1 simulated second
# σ calibrated so 1-second candles show visible movement (≈ 0.08 %/s normal vol)
REGIMES: Dict[str, Dict[str, float]] = {
    "bull":     {"mu":  0.000002, "sigma": 0.0008},
    "bear":     {"mu": -0.000002, "sigma": 0.0008},
    "high_vol": {"mu":  0.000000, "sigma": 0.0030},
    "low_vol":  {"mu":  0.000000, "sigma": 0.0002},
}

_REGIME_ORDER = ["bull", "bear", "high_vol", "low_vol"]

_TRANSITION_MATRIX: np.ndarray = np.array([
    [0.970, 0.010, 0.010, 0.010],
    [0.010, 0.970, 0.010, 0.010],
    [0.010, 0.010, 0.970, 0.010],
    [0.010, 0.010, 0.010, 0.970],
])


# ===========================================================================
# SECTION 2 · Phase 1 Parameter Dataclasses
# ===========================================================================

@dataclass
class JumpParams:
    """Parameters governing rare price discontinuities."""
    probability: float = 0.0003
    mean:        float = 0.0
    std:         float = 0.015
    min_price:   float = 0.1


@dataclass
class SpreadParams:
    """Bid-ask spread parameters."""
    base_spread:         float = 0.0005
    high_vol_multiplier: float = 3.0


@dataclass
class FeeParams:
    """Maker / taker fee model."""
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005


@dataclass
class LeverageParams:
    """Leverage and margin parameters."""
    leverage:            float = 10.0
    maintenance_margin:  float = 0.005


@dataclass
class LiquidationEvent:
    step:             int
    price_at_liq:     float
    margin_balance:   float
    side:             str


@dataclass
class TradeRecord:
    step:         int
    side:         str
    qty:          float
    exec_price:   float
    fee_paid:     float
    order_type:   str
    net_pnl:      float = 0.0


# ===========================================================================
# SECTION 3 · Core MarketSimulator (Phase 1)
# ===========================================================================

class MarketSimulator:
    """
    Phase 1 synthetic crypto market simulator.

    Active layers: regime switching, GBM stochastic, jump events,
    bid-ask spread, fee model, leverage & liquidation.
    """

    def __init__(
        self,
        initial_price:    float         = 100.0,
        seed:             Optional[int] = 42,
        initial_regime:   str           = "bull",
        enable_regime:      bool = True,
        enable_stochastic:  bool = True,
        enable_jumps:       bool = True,
        enable_spread:      bool = True,
        fee_params:      Optional[FeeParams]      = None,
        leverage_params: Optional[LeverageParams] = None,
        jump_params:     Optional[JumpParams]     = None,
        spread_params:   Optional[SpreadParams]   = None,
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.price: float = float(initial_price)
        self.price_history:    List[float] = [self.price]
        self.regime_history:   List[str]   = []
        self.spread_history:   List[float] = []
        self.jump_history:     List[bool]  = []
        self.t: int = 0

        assert initial_regime in REGIMES, f"Unknown regime '{initial_regime}'"
        self.regime: str = initial_regime
        self.regimes      = REGIMES
        self.enable_regime = enable_regime
        self.enable_stochastic = enable_stochastic
        self.enable_jumps = enable_jumps
        self.jump_params  = jump_params or JumpParams()
        self.enable_spread = enable_spread
        self.spread_params = spread_params or SpreadParams()
        self.fee_params  = fee_params
        self.trade_log:   List[TradeRecord] = []
        self.leverage_params      = leverage_params
        self.margin_balance:       Optional[float] = None
        self.open_position_qty:    float = 0.0
        self.open_position_side:   Optional[str]  = None
        self.open_position_entry:  Optional[float] = None
        self.liquidation_events:   List[LiquidationEvent] = []

        if self.enable_regime:
            self.regime_history.append(self.regime)

    def _get_regime_params(self) -> Tuple[float, float]:
        params = self.regimes[self.regime]
        return params["mu"], params["sigma"]

    def _switch_regime(self) -> None:
        idx = _REGIME_ORDER.index(self.regime)
        probs = _TRANSITION_MATRIX[idx]
        new_idx = self.rng.choice(len(_REGIME_ORDER), p=probs)
        self.regime = _REGIME_ORDER[new_idx]

    def _compute_spread(self) -> float:
        sp = self.spread_params
        multiplier = sp.high_vol_multiplier if self.regime == "high_vol" else 1.0
        return sp.base_spread * multiplier

    def step(self) -> float:
        self.t += 1
        if self.enable_regime:
            self._switch_regime()
            self.regime_history.append(self.regime)

        if self.enable_stochastic:
            mu, sigma = self._get_regime_params()
            shock = self.rng.normal(0.0, sigma)
            self.price *= (1.0 + mu + shock)

        jumped = False
        if self.enable_jumps:
            jp = self.jump_params
            if self.rng.random() < jp.probability:
                jump_size = self.rng.normal(jp.mean, jp.std)
                self.price *= (1.0 + jump_size)
                jumped = True

        self.price = max(self.price, self.jump_params.min_price)
        self.jump_history.append(jumped)
        self.price_history.append(self.price)

        half_spread = self._compute_spread() if self.enable_spread else 0.0
        self.spread_history.append(half_spread * 2 * self.price)

        if self.leverage_params is not None and self.open_position_qty != 0.0:
            self._check_liquidation()

        return self.price

    def run(self, n_steps: int) -> np.ndarray:
        for _ in range(n_steps):
            self.step()
        return np.array(self.price_history)

    def get_bid_ask(self) -> Tuple[float, float]:
        hs = self._compute_spread()
        bid = self.price * (1.0 - hs)
        ask = self.price * (1.0 + hs)
        return bid, ask

    def execute_trade(self, side: str, qty: float,
                      order_type: str = "taker") -> TradeRecord:
        bid, ask = self.get_bid_ask()
        exec_price = ask if side == "buy" else bid
        if self.fee_params is not None:
            fee_rate = (self.fee_params.taker_fee
                        if order_type == "taker" else self.fee_params.maker_fee)
        else:
            fee_rate = 0.0
        fee_paid = exec_price * qty * fee_rate
        record = TradeRecord(step=self.t, side=side, qty=qty,
                             exec_price=exec_price, fee_paid=fee_paid,
                             order_type=order_type)
        self.trade_log.append(record)
        return record

    def compute_net_pnl(self, buy_record: TradeRecord,
                        sell_record: TradeRecord) -> float:
        gross = (sell_record.exec_price - buy_record.exec_price) * buy_record.qty
        fees  = buy_record.fee_paid + sell_record.fee_paid
        net   = gross - fees
        sell_record.net_pnl = net
        return net

    def open_leveraged_position(self, side: str, qty: float,
                                margin_deposit: float,
                                order_type: str = "taker") -> TradeRecord:
        if self.leverage_params is None:
            raise RuntimeError("LeverageParams not set.")
        trade_side = "buy" if side == "long" else "sell"
        record = self.execute_trade(trade_side, qty, order_type)
        self.open_position_qty   = qty
        self.open_position_side  = side
        self.open_position_entry = record.exec_price
        self.margin_balance      = margin_deposit - record.fee_paid
        return record

    def close_leveraged_position(self,
                                 order_type: str = "taker") -> Tuple[TradeRecord, float]:
        if self.open_position_qty == 0.0:
            raise RuntimeError("No open position to close.")
        close_side = "sell" if self.open_position_side == "long" else "buy"
        record = self.execute_trade(close_side, self.open_position_qty, order_type)
        entry = self.open_position_entry
        exit_ = record.exec_price
        qty   = self.open_position_qty
        lp    = self.leverage_params
        if self.open_position_side == "long":
            gross_pnl = (exit_ - entry) * qty * lp.leverage
        else:
            gross_pnl = (entry - exit_) * qty * lp.leverage
        net_pnl = gross_pnl - record.fee_paid
        record.net_pnl = net_pnl
        self.open_position_qty   = 0.0
        self.open_position_side  = None
        self.open_position_entry = None
        self.margin_balance      = None
        return record, net_pnl

    def _check_liquidation(self) -> None:
        lp    = self.leverage_params
        entry = self.open_position_entry
        qty   = self.open_position_qty
        notional = entry * qty * lp.leverage
        if self.open_position_side == "long":
            upnl = (self.price - entry) * qty * lp.leverage
        else:
            upnl = (entry - self.price) * qty * lp.leverage
        equity = (self.margin_balance or 0.0) + upnl
        maint  = lp.maintenance_margin * notional
        if equity <= maint:
            event = LiquidationEvent(step=self.t, price_at_liq=self.price,
                                     margin_balance=equity,
                                     side=self.open_position_side)
            self.liquidation_events.append(event)
            self.open_position_qty   = 0.0
            self.open_position_side  = None
            self.open_position_entry = None
            self.margin_balance      = 0.0

    @property
    def log_returns(self) -> np.ndarray:
        p = np.array(self.price_history)
        return np.log(p[1:] / p[:-1])

    @property
    def regime_color_series(self) -> List[str]:
        _cmap = {"bull": "#2ecc71", "bear": "#e74c3c",
                 "high_vol": "#e67e22", "low_vol": "#3498db"}
        return [_cmap.get(r, "#aaaaaa") for r in self.regime_history]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART II — PHASE 2 ENGINES                                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 4 · Phase 2 Configuration
# ===========================================================================

@dataclass
class Phase2Config:
    """Master feature-flag + parameter container for Phase 2."""
    enable_garch_volatility:   bool = True
    enable_volume_model:       bool = True
    enable_slippage_model:     bool = True
    enable_correlated_assets:  bool = True
    enable_liquidation_cascade: bool = True

    garch_alpha0: float = 1e-6
    garch_alpha1: float = 0.10
    garch_beta:   float = 0.85

    volume_base:        float = 1_000.0
    volume_k:           float = 50.0
    volume_jump_mult:   float = 5.0
    volume_regime_mult: Dict[str, float] = field(default_factory=lambda: {
        "bull": 1.2, "bear": 1.3, "high_vol": 2.5, "low_vol": 0.5})

    slippage_size_factor:    float = 0.0001
    slippage_vol_multiplier: float = 10.0

    correlation_matrix: List[List[float]] = field(
        default_factory=lambda: [[1.0, 0.6], [0.6, 1.0]])
    n_assets: int = 2
    asset_names: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    asset_vol_scalars: List[float] = field(default_factory=lambda: [1.0, 1.4])

    cascade_threshold:    float = -0.05
    cascade_multiplier:   float = 1.5
    cascade_oi_fraction:  float = 0.10
    initial_open_interest: float = 10_000_000.0

    seed: Optional[int] = 42


# ===========================================================================
# SECTION 5 · Stress Test Config
# ===========================================================================

@dataclass
class StressTestConfig:
    """Parameterised friction overrides applied on top of simulation."""
    spread_multiplier:   float = 1.0
    vol_multiplier:      float = 1.0
    latency_steps:       int   = 0
    regime_duration_std: float = 0.0
    enabled:             bool  = False

    def apply_spread(self, half_spread: float) -> float:
        return half_spread * self.spread_multiplier if self.enabled else half_spread

    def apply_sigma(self, sigma: float) -> float:
        return sigma * self.vol_multiplier if self.enabled else sigma

    def regime_stay_override(self, base_prob: float) -> float:
        if not self.enabled or self.regime_duration_std <= 0:
            return base_prob
        noise = np.random.normal(0, self.regime_duration_std)
        return float(np.clip(base_prob + noise, 0.80, 0.99))


# ===========================================================================
# SECTION 6 · GARCH(1,1) Volatility Engine
# ===========================================================================

class GARCHVolatilityEngine:
    """GARCH(1,1): σ²_t = α₀ + α₁·r²_{t-1} + β·σ²_{t-1}"""

    def __init__(self, cfg: Phase2Config) -> None:
        self.alpha0 = cfg.garch_alpha0
        self.alpha1 = cfg.garch_alpha1
        self.beta   = cfg.garch_beta
        denom  = max(1 - self.alpha1 - self.beta, 1e-8)
        self.sigma2: float = self.alpha0 / denom
        self.history: List[float] = [math.sqrt(self.sigma2)]

    def update(self, last_return: float) -> float:
        self.sigma2 = (self.alpha0 + self.alpha1 * last_return ** 2
                       + self.beta * self.sigma2)
        sigma_t = math.sqrt(max(self.sigma2, 1e-12))
        self.history.append(sigma_t)
        return sigma_t

    @property
    def current_sigma(self) -> float:
        return math.sqrt(max(self.sigma2, 1e-12))

    def long_run_sigma(self) -> float:
        denom = max(1 - self.alpha1 - self.beta, 1e-8)
        return math.sqrt(self.alpha0 / denom)


# ===========================================================================
# SECTION 7 · Volume Simulation Engine
# ===========================================================================

class VolumeSimulationEngine:
    """V_t = base × regime_mult × (1 + k × |r_t|) × jump_boost × noise"""

    def __init__(self, cfg: Phase2Config, rng: np.random.Generator) -> None:
        self.base        = cfg.volume_base
        self.k           = cfg.volume_k
        self.jump_mult   = cfg.volume_jump_mult
        self.regime_mult = cfg.volume_regime_mult
        self.rng         = rng
        self.history:  List[float] = []

    def step(self, price_return: float, regime: str, jumped: bool) -> float:
        rm    = self.regime_mult.get(regime, 1.0)
        boost = self.jump_mult if jumped else 1.0
        noise = max(0.1, self.rng.normal(1.0, 0.20))
        vol   = self.base * rm * (1.0 + self.k * abs(price_return)) * boost * noise
        vol   = max(1.0, vol)
        self.history.append(vol)
        return vol


# ===========================================================================
# SECTION 8 · Dynamic Slippage Model
# ===========================================================================

class DynamicSlippageModel:
    """Slippage = vol × size_factor × order_size_usd"""

    def __init__(self, cfg: Phase2Config) -> None:
        self.size_factor = cfg.slippage_size_factor
        self.vol_mult    = cfg.slippage_vol_multiplier
        self.slippage_log: List[float] = []

    def compute(self, mid_price: float, side: str, order_size: float,
                spread_frac: float, sigma: float, jumped: bool = False) -> float:
        slippage = self.vol_mult * sigma * self.size_factor * order_size
        if jumped:
            slippage *= 3.0
        direction = +1.0 if side == "buy" else -1.0
        exec_price = mid_price * (1.0 + direction * (spread_frac + slippage))
        self.slippage_log.append(abs(exec_price - mid_price))
        return max(exec_price, 1e-6)

    def average_slippage_bps(self) -> float:
        if not self.slippage_log:
            return 0.0
        return float(np.mean(self.slippage_log) / 100 * 10_000)


# ===========================================================================
# SECTION 9 · Correlated Asset Engine
# ===========================================================================

class CorrelatedAssetEngine:
    """Cholesky-decomposed multi-asset correlated shocks."""

    def __init__(self, cfg: Phase2Config, rng: np.random.Generator) -> None:
        self.n           = cfg.n_assets
        self.names       = cfg.asset_names[:self.n]
        self.vol_scalars = np.array(cfg.asset_vol_scalars[:self.n])
        corr             = np.array(cfg.correlation_matrix, dtype=float)
        corr             = corr + np.eye(len(corr)) * 1e-8
        self.L           = np.linalg.cholesky(corr)
        self.rng         = rng
        self.cfg         = cfg
        self._prices:    List[float] = []

    def initialise_prices(self, primary_price: float,
                          secondary_ratio: float = 0.05) -> List[float]:
        prices = [primary_price * (secondary_ratio ** i) for i in range(self.n)]
        self._prices = prices
        return prices

    def correlated_shocks(self, sigma_primary: float) -> np.ndarray:
        z      = self.rng.standard_normal(self.n)
        eps    = self.L @ z
        return eps * sigma_primary * self.vol_scalars

    def step(self, mu: float, sigma_primary: float, jumped: bool,
             jump_size: float) -> List[float]:
        shocks = self.correlated_shocks(sigma_primary)
        new_prices = []
        for i, p in enumerate(self._prices):
            r = mu + shocks[i]
            if jumped:
                r += jump_size * self.vol_scalars[i]
            p_new = max(p * (1.0 + r), 1e-6)
            new_prices.append(p_new)
        self._prices = new_prices
        return new_prices

    @property
    def prices(self) -> List[float]:
        return list(self._prices)

    def rolling_correlation(self, returns_a: np.ndarray,
                            returns_b: np.ndarray,
                            window: int = 60) -> np.ndarray:
        n   = len(returns_a)
        out = np.full(n, np.nan)
        for i in range(window, n):
            ra = returns_a[i - window:i]
            rb = returns_b[i - window:i]
            if ra.std() > 0 and rb.std() > 0:
                out[i] = float(np.corrcoef(ra, rb)[0, 1])
        return out


# ===========================================================================
# SECTION 10 · Liquidation Cascade Engine
# ===========================================================================

@dataclass
class CascadeEvent:
    step:           int
    trigger_return: float
    oi_wiped:       float
    cascade_shock:  float
    price_after:    float


class LiquidationCascadeEngine:
    """Forced-liquidation cascade when large negative returns occur."""

    def __init__(self, cfg: Phase2Config) -> None:
        self.threshold    = cfg.cascade_threshold
        self.multiplier   = cfg.cascade_multiplier
        self.oi_fraction  = cfg.cascade_oi_fraction
        self.open_interest: float = cfg.initial_open_interest
        self.events:        List[CascadeEvent] = []
        self.oi_history:    List[float] = [self.open_interest]

    def step(self, price: float, step: int,
             price_return: float) -> Tuple[float, bool]:
        cascaded = False
        if price_return < self.threshold:
            oi_wiped = self.open_interest * self.oi_fraction
            self.open_interest = max(0.0, self.open_interest - oi_wiped)
            secondary = -min(abs(price_return) * self.multiplier, 0.20)
            new_price = price * (1.0 + secondary)
            cascaded  = True
            self.events.append(CascadeEvent(
                step=step, trigger_return=price_return, oi_wiped=oi_wiped,
                cascade_shock=secondary, price_after=new_price))
        else:
            new_price = price
            self.open_interest = min(self.open_interest * 1.0001,
                                     self.open_interest * 1.10)
        self.oi_history.append(self.open_interest)
        return new_price, cascaded


# ===========================================================================
# SECTION 11 · Risk Metrics
# ===========================================================================

class RiskMetrics:
    """Standard strategy-grade performance metrics."""

    @staticmethod
    def sharpe_ratio(returns: np.ndarray, risk_free: float = 0.0,
                     periods_per_year: int = 86_400) -> float:
        er  = returns - risk_free / periods_per_year
        std = er.std()
        if std == 0:
            return 0.0
        return float(er.mean() / std * math.sqrt(periods_per_year))

    @staticmethod
    def max_drawdown(equity_curve: np.ndarray) -> Tuple[float, int, int]:
        peak_idx = 0; max_dd = 0.0; trough_idx = 0
        running_peak = equity_curve[0]; running_peak_idx = 0
        for i, v in enumerate(equity_curve):
            if v > running_peak:
                running_peak = v; running_peak_idx = i
            dd = (running_peak - v) / max(running_peak, 1e-9)
            if dd > max_dd:
                max_dd = dd; peak_idx = running_peak_idx; trough_idx = i
        return float(max_dd), peak_idx, trough_idx

    @staticmethod
    def win_rate(trade_pnls: List[float]) -> float:
        if not trade_pnls:
            return 0.0
        return sum(1 for p in trade_pnls if p > 0) / len(trade_pnls)

    @staticmethod
    def trade_expectancy(trade_pnls: List[float]) -> Tuple[float, float, float]:
        if not trade_pnls:
            return 0.0, 0.0, 0.0
        wins   = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        avg_win  = float(np.mean(wins))  if wins   else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        wr       = len(wins) / len(trade_pnls)
        return wr * avg_win - (1 - wr) * abs(avg_loss), avg_win, avg_loss

    @staticmethod
    def value_at_risk(returns: np.ndarray, confidence: float = 0.95) -> float:
        return float(-np.percentile(returns, (1 - confidence) * 100))

    @classmethod
    def full_report(cls, prices: np.ndarray, trade_pnls: List[float] = None,
                    initial_equity: float = 10_000.0,
                    print_report: bool = True) -> Dict:
        returns    = np.diff(np.log(prices + 1e-12))
        equity     = initial_equity + np.cumsum(returns * initial_equity)
        equity     = np.insert(equity, 0, initial_equity)
        trade_pnls = trade_pnls or []

        sharpe         = cls.sharpe_ratio(returns)
        max_dd, pi, ti = cls.max_drawdown(equity)
        wr             = cls.win_rate(trade_pnls)
        expect, aw, al = cls.trade_expectancy(trade_pnls)
        var95          = cls.value_at_risk(returns, 0.95)
        total_return   = (prices[-1] - prices[0]) / prices[0]

        result = {
            "total_return_pct":  round(total_return * 100, 3),
            "sharpe_ratio":      round(sharpe, 4),
            "max_drawdown_pct":  round(max_dd * 100, 3),
            "max_dd_peak_step":  pi,
            "max_dd_trough_step": ti,
            "var_95_pct":        round(var95 * 100, 4),
        }
        if print_report:
            bar = "─" * 46
            print(f"\n{bar}")
            print(f"  RISK METRICS REPORT")
            print(bar)
            print(f"  Total return   : {result['total_return_pct']:>10.3f} %")
            print(f"  Sharpe ratio   : {result['sharpe_ratio']:>10.4f}")
            print(f"  Max drawdown   : {result['max_drawdown_pct']:>10.3f} %"
                  f"  (step {pi}→{ti})")
            print(f"  VaR 95 %       : {result['var_95_pct']:>10.4f} %")
            print(bar + "\n")
        return result


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART III — PHASE 3 ENGINES                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 12 · Phase 3 Configuration
# ===========================================================================

@dataclass
class AgentConfig:
    """Per-agent-type population & parameter configuration."""
    n_momentum:          int = 8
    n_mean_reversion:    int = 6
    n_market_maker:      int = 4
    n_noise:             int = 15
    n_liq_hunter:        int = 3

    mom_fast_window:     int   = 10
    mom_slow_window:     int   = 50
    mom_order_scale:     float = 5_000
    mom_position_limit:  float = 50_000

    mr_window:           int   = 60
    mr_k:                float = 2.0
    mr_order_scale:      float = 4_000
    mr_position_limit:   float = 40_000

    mm_order_scale:      float = 10_000
    mm_skew_factor:      float = 0.3
    mm_vol_retreat:      float = 2.0
    mm_position_limit:   float = 100_000

    noise_order_scale:   float = 1_000
    noise_bias:          float = 0.0

    lh_scan_range_pct:   float = 0.05
    lh_push_scale:       float = 15_000
    lh_position_limit:   float = 80_000


@dataclass
class LiquidityConfig:
    """Dynamic liquidity pool parameters."""
    baseline:       float = 5_000_000
    vol_sensitivity: float = 3.0
    jump_impact:    float = 0.80
    recovery_rate:  float = 0.005
    min_fraction:   float = 0.03
    cascade_impact: float = 0.90


@dataclass
class AdversarialConfig:
    """Adversarial stress engine parameters."""
    enabled:              bool  = True
    detection_window:     int   = 100
    trap_probability:     float = 0.15
    false_breakout_size:  float = 0.008
    trend_extension_mult: float = 1.5
    friction_sensitivity: float = 2.0
    consensus_threshold:  float = 0.5
    contrarian_push_frac: float = 0.012
    streak_break_length:  int   = 3
    streak_break_size:    float = 0.025
    mean_revert_noise:    float = 0.003


@dataclass
class LatencyConfig:
    """Latency model parameters."""
    enabled:         bool  = True
    min_delay_steps: int   = 0
    max_delay_steps: int   = 3
    info_delay_steps: int  = 1
    vol_delay_mult:  float = 2.0


@dataclass
class Phase3Config:
    """Master configuration for Phase 3."""
    agents:     AgentConfig      = field(default_factory=AgentConfig)
    liquidity:  LiquidityConfig  = field(default_factory=LiquidityConfig)
    adversarial: AdversarialConfig = field(default_factory=AdversarialConfig)
    latency:    LatencyConfig    = field(default_factory=LatencyConfig)
    p2:         Phase2Config     = field(default_factory=Phase2Config)
    price_impact_coeff:  float = 1.0
    use_emergent_regime: bool  = True
    regime_detect_window: int  = 30
    seed: Optional[int] = 42


# ===========================================================================
# SECTION 13 · Order & OrderBook
# ===========================================================================

@dataclass
class AgentOrder:
    """A single order emitted by an agent."""
    agent_id:     str
    agent_type:   str
    side:         str
    size_usd:     float
    delay_steps:  int = 0
    step_created: int = 0


class OrderBook:
    """Simplified order-flow aggregator."""

    def __init__(self) -> None:
        self.pending: List[AgentOrder] = []
        self.history_net_flow:  List[float] = [0.0]
        self.history_buy_vol:   List[float] = [0.0]
        self.history_sell_vol:  List[float] = [0.0]
        self.history_n_orders:  List[int]   = [0]

    def submit(self, order: AgentOrder) -> None:
        self.pending.append(order)

    def process(self, current_step: int, liquidity: float,
                price: float, impact_coeff: float) -> Tuple[float, List[AgentOrder]]:
        ready = [o for o in self.pending
                 if current_step - o.step_created >= o.delay_steps]
        self.pending = [o for o in self.pending
                        if current_step - o.step_created < o.delay_steps]

        buy_vol  = sum(o.size_usd for o in ready if o.side == 'buy')
        sell_vol = sum(o.size_usd for o in ready if o.side == 'sell')
        net_flow = buy_vol - sell_vol

        self.history_buy_vol.append(buy_vol)
        self.history_sell_vol.append(sell_vol)
        self.history_net_flow.append(net_flow)
        self.history_n_orders.append(len(ready))

        effective_liq = max(liquidity, 1.0)
        impact = impact_coeff * net_flow / effective_liq
        return float(np.clip(impact, -0.02, 0.02)), ready

    @property
    def net_flow(self) -> float:
        return self.history_net_flow[-1] if self.history_net_flow else 0.0


# ===========================================================================
# SECTION 14 · Agent Engine — Base + 5 Agent Types
# ===========================================================================

class BaseAgent(abc.ABC):
    """Abstract agent that observes price history and generates orders."""
    _counter: int = 0

    def __init__(self, agent_type: str, initial_capital: float,
                 position_limit: float, rng: np.random.Generator) -> None:
        BaseAgent._counter += 1
        self.id              = f"{agent_type}_{BaseAgent._counter}"
        self.agent_type      = agent_type
        self.capital         = initial_capital
        self.position        = 0.0
        self.position_limit  = position_limit
        self.rng             = rng
        self.pnl_history:    List[float] = [0.0]
        self.order_history:  List[float] = []
        self._realised_pnl   = 0.0
        self._entry_price    = 0.0

    @abc.abstractmethod
    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str,
                       step: int) -> Optional[AgentOrder]: ...

    def _can_order(self, size_usd: float) -> float:
        abs_size = min(abs(size_usd), self.capital * 0.5)
        if size_usd > 0:
            room = self.position_limit - self.position
            abs_size = min(abs_size, max(room, 0))
        else:
            room = self.position_limit + self.position
            abs_size = min(abs_size, max(room, 0))
        return abs_size

    def update_pnl(self, price: float, prev_price: float) -> None:
        if self.position != 0 and prev_price > 0:
            ret = (price - prev_price) / prev_price
            step_pnl = self.position * ret
            self.capital += step_pnl
            self._realised_pnl += step_pnl
        self.pnl_history.append(self._realised_pnl)

    def execute_fill(self, order: AgentOrder, exec_price: float) -> None:
        signed = order.size_usd if order.side == 'buy' else -order.size_usd
        if self.position * signed < 0:
            close_size = min(abs(signed), abs(self.position))
            if self._entry_price > 0:
                direction = 1.0 if self.position > 0 else -1.0
                pnl = direction * close_size * (exec_price - self._entry_price) / self._entry_price
                self.capital += pnl
        self.position += signed
        if abs(self.position) > 0:
            self._entry_price = exec_price
        else:
            self._entry_price = 0.0
        self.order_history.append(signed)


class MomentumTrader(BaseAgent):
    """Fast/slow EMA crossover → trend-following orders."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        fast = cfg.mom_fast_window + rng.integers(-3, 4)
        slow = cfg.mom_slow_window + rng.integers(-10, 11)
        self.fast_w = max(3, fast)
        self.slow_w = max(self.fast_w + 5, slow)
        self.order_scale = cfg.mom_order_scale * (0.6 + rng.random() * 0.8)
        super().__init__("momentum", 50_000 + rng.random() * 50_000,
                         cfg.mom_position_limit, rng)

    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str, step: int) -> Optional[AgentOrder]:
        if len(price_history) < self.slow_w + 2:
            return None
        fast_ema = self._ema(price_history, self.fast_w)
        slow_ema = self._ema(price_history, self.slow_w)
        signal = (fast_ema - slow_ema) / max(current_price, 1e-9)
        raw_size = signal * self.order_scale * 100
        if abs(raw_size) < 50:
            return None
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 10:
            return None
        return AgentOrder(self.id, self.agent_type, side, size, step_created=step)

    @staticmethod
    def _ema(data: np.ndarray, span: int) -> float:
        alpha = 2.0 / (span + 1)
        ema = float(data[-span])
        for v in data[-span + 1:]:
            ema = alpha * float(v) + (1 - alpha) * ema
        return ema


class MeanReversionTrader(BaseAgent):
    """Z-score mean-reversion: buy oversold, sell overbought."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.window = max(20, cfg.mr_window + rng.integers(-10, 11))
        self.k = max(0.5, cfg.mr_k + rng.uniform(-0.5, 0.5))
        self.order_scale = cfg.mr_order_scale * (0.6 + rng.random() * 0.8)
        super().__init__("mean_reversion", 40_000 + rng.random() * 40_000,
                         cfg.mr_position_limit, rng)

    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str, step: int) -> Optional[AgentOrder]:
        if len(price_history) < self.window + 2:
            return None
        window = price_history[-self.window:]
        mu  = float(np.mean(window))
        std = float(np.std(window))
        if std < 1e-9:
            return None
        z = (current_price - mu) / std
        if abs(z) < self.k:
            return None
        raw_size = (-z / self.k) * self.order_scale
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 10:
            return None
        return AgentOrder(self.id, self.agent_type, side, size, step_created=step)


class MarketMakerAgent(BaseAgent):
    """Inventory-skewed two-sided quoting with vol retreat."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.order_scale = cfg.mm_order_scale * (0.7 + rng.random() * 0.6)
        self.skew_factor = cfg.mm_skew_factor
        self.vol_retreat = cfg.mm_vol_retreat
        self._base_sigma = 0.0008
        super().__init__("market_maker", 200_000 + rng.random() * 100_000,
                         cfg.mm_position_limit, rng)

    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str, step: int) -> Optional[AgentOrder]:
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        retreat = max(0.05, 1.0 / (1.0 + (vol_ratio - 1.0) * self.vol_retreat))
        base = self.order_scale * retreat
        inventory_frac = self.position / max(self.position_limit, 1.0)
        skew = -inventory_frac * self.skew_factor * base
        noise = self.rng.normal(0, base * 0.1)
        net_order = skew + noise
        if abs(net_order) < 50:
            return None
        side = 'buy' if net_order > 0 else 'sell'
        size = self._can_order(net_order)
        if size < 10:
            return None
        return AgentOrder(self.id, self.agent_type, side, size, step_created=step)


class NoiseTrader(BaseAgent):
    """Random orders for background market activity."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.order_scale = cfg.noise_order_scale * (0.5 + rng.random())
        self.bias = cfg.noise_bias + rng.uniform(-0.1, 0.1)
        super().__init__("noise", 10_000 + rng.random() * 20_000, 50_000, rng)

    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str, step: int) -> Optional[AgentOrder]:
        if self.rng.random() < 0.4:
            return None
        raw = self.rng.normal(self.bias, 1.0) * self.order_scale
        if abs(raw) < 10:
            return None
        side = 'buy' if raw > 0 else 'sell'
        size = self._can_order(raw)
        if size < 5:
            return None
        return AgentOrder(self.id, self.agent_type, side, size, step_created=step)


class LiquidationHunter(BaseAgent):
    """Predatory trader that pushes price toward liquidation clusters."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.scan_range = cfg.lh_scan_range_pct
        self.push_scale = cfg.lh_push_scale * (0.6 + rng.random() * 0.8)
        super().__init__("liq_hunter", 100_000 + rng.random() * 100_000,
                         cfg.lh_position_limit, rng)
        self._target_dir: float = 0.0
        self._cooldown: int = 0

    def generate_order(self, price_history: np.ndarray, current_price: float,
                       sigma: float, regime: str, step: int) -> Optional[AgentOrder]:
        if self._cooldown > 0:
            self._cooldown -= 1
            return None
        if len(price_history) < 50:
            return None
        recent_low  = float(np.min(price_history[-50:]))
        recent_high = float(np.max(price_history[-50:]))
        dist_to_low  = (current_price - recent_low) / current_price
        dist_to_high = (recent_high - current_price) / current_price
        if dist_to_low < self.scan_range * 0.5 and dist_to_low > 0.005:
            self._target_dir = -1.0
        elif dist_to_high < self.scan_range * 0.5 and dist_to_high > 0.005:
            self._target_dir = 1.0
        else:
            self._target_dir = self.rng.choice([-1.0, 1.0])
        recent_ret = (current_price - float(price_history[-10])) / float(price_history[-10])
        if recent_ret * self._target_dir < -0.001:
            return None
        raw_size = self._target_dir * self.push_scale
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 50:
            return None
        self._cooldown = self.rng.integers(3, 15)
        return AgentOrder(self.id, self.agent_type, side, size, step_created=step)


class AgentEngine:
    """Creates and manages all agent populations."""

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator) -> None:
        self.agents: List[BaseAgent] = []
        self.cfg = cfg
        for i in range(cfg.n_momentum):
            self.agents.append(MomentumTrader(cfg, rng, variant=i))
        for i in range(cfg.n_mean_reversion):
            self.agents.append(MeanReversionTrader(cfg, rng, variant=i))
        for i in range(cfg.n_market_maker):
            self.agents.append(MarketMakerAgent(cfg, rng, variant=i))
        for i in range(cfg.n_noise):
            self.agents.append(NoiseTrader(cfg, rng, variant=i))
        for i in range(cfg.n_liq_hunter):
            self.agents.append(LiquidationHunter(cfg, rng, variant=i))
        self.n_agents = len(self.agents)

    def generate_all_orders(self, price_history: np.ndarray,
                            current_price: float, sigma: float,
                            regime: str, step: int) -> List[AgentOrder]:
        orders = []
        for agent in self.agents:
            order = agent.generate_order(price_history, current_price,
                                         sigma, regime, step)
            if order is not None:
                orders.append(order)
        return orders

    def update_all_pnl(self, price: float, prev_price: float) -> None:
        for agent in self.agents:
            agent.update_pnl(price, prev_price)

    def get_consensus(self) -> Tuple[float, Dict[str, float]]:
        type_sums:  Dict[str, float] = {}
        type_counts: Dict[str, int]  = {}
        total_signed = 0.0
        n = 0
        for agent in self.agents:
            if agent.order_history:
                last = agent.order_history[-1]
                total_signed += (1.0 if last > 0 else -1.0 if last < 0 else 0.0)
                n += 1
                t = agent.agent_type
                type_sums[t]  = type_sums.get(t, 0) + last
                type_counts[t] = type_counts.get(t, 0) + 1
        consensus = total_signed / max(n, 1)
        type_biases = {t: type_sums[t] / max(type_counts[t], 1) for t in type_sums}
        return consensus, type_biases

    def get_pnl_by_type(self) -> Dict[str, float]:
        sums: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for a in self.agents:
            t = a.agent_type
            sums[t]   = sums.get(t, 0) + a._realised_pnl
            counts[t] = counts.get(t, 0) + 1
        return {t: sums[t] / max(counts[t], 1) for t in sums}

    def get_agent_stats(self) -> Dict[str, Dict]:
        stats = defaultdict(lambda: {"count": 0, "total_pnl": 0.0,
                                     "total_capital": 0.0, "total_position": 0.0})
        for a in self.agents:
            s = stats[a.agent_type]
            s["count"] += 1
            s["total_pnl"]      += a._realised_pnl
            s["total_capital"]   += a.capital
            s["total_position"]  += a.position
        result = {}
        for t, s in stats.items():
            n = s["count"]
            result[t] = {"count": n, "avg_pnl": s["total_pnl"] / n,
                         "avg_capital": s["total_capital"] / n,
                         "avg_position": s["total_position"] / n}
        return result


# ===========================================================================
# SECTION 15 · Dynamic Liquidity Engine
# ===========================================================================

class DynamicLiquidityEngine:
    """Volatility/jump/cascade-reactive liquidity model."""

    def __init__(self, cfg: LiquidityConfig) -> None:
        self.cfg = cfg
        self.current = cfg.baseline
        self.history: List[float] = [cfg.baseline]
        self._base_sigma = 0.0008

    def step(self, sigma: float, jumped: bool, cascaded: bool) -> float:
        cfg = self.cfg
        baseline = cfg.baseline
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        vol_factor = max(cfg.min_fraction,
                         1.0 / (1.0 + cfg.vol_sensitivity * max(vol_ratio - 1.0, 0.0)))
        jump_factor = (1.0 - cfg.jump_impact) if jumped else 1.0
        casc_factor = (1.0 - cfg.cascade_impact) if cascaded else 1.0
        target = baseline * vol_factor * jump_factor * casc_factor
        if target < self.current:
            self.current = target
        else:
            self.current += cfg.recovery_rate * (baseline - self.current)
        self.current = max(self.current, baseline * cfg.min_fraction)
        self.history.append(self.current)
        return self.current

    @property
    def fraction(self) -> float:
        return self.current / max(self.cfg.baseline, 1.0)


# ===========================================================================
# SECTION 16 · Emergent Regime Detector
# ===========================================================================

class EmergentRegimeDetector:
    """Infers market regime from observable data — no random switching."""

    def __init__(self, window: int = 30) -> None:
        self.window          = max(10, window)
        self.vol_history:    List[float] = []
        self.flow_history:   List[float] = []
        self.regime_history: List[str]   = ["bull"]
        self._warmup_done    = False

    def detect(self, returns: List[float], net_flow: float,
               prices: List[float]) -> str:
        self.flow_history.append(net_flow)
        if len(returns) < self.window:
            regime = self.regime_history[-1]
            self.regime_history.append(regime)
            return regime

        recent_returns = np.array(returns[-self.window:])
        vol = float(np.std(recent_returns))
        self.vol_history.append(vol)

        if len(self.vol_history) < self.window:
            vol_pct = 50.0
        else:
            arr = np.array(self.vol_history)
            vol_pct = float(np.searchsorted(np.sort(arr), vol) / len(arr) * 100)

        if len(prices) > self.window:
            momentum = (prices[-1] - prices[-self.window]) / max(prices[-self.window], 1e-9)
        else:
            momentum = 0.0

        recent_flow = self.flow_history[-min(len(self.flow_history), self.window):]
        avg_flow = float(np.mean(recent_flow))

        if vol_pct > 80:
            regime = "high_vol"
        elif vol_pct < 20:
            regime = "low_vol"
        elif momentum > 0.001 and avg_flow > 0:
            regime = "bull"
        elif momentum < -0.001 and avg_flow < 0:
            regime = "bear"
        elif momentum > 0.0005:
            regime = "bull"
        elif momentum < -0.0005:
            regime = "bear"
        else:
            regime = "low_vol"

        self.regime_history.append(regime)
        return regime


# ===========================================================================
# SECTION 17 · Adversarial Stress Engine
# ===========================================================================

class AdversarialStressEngine:
    """
    Detects profitable strategy patterns and actively degrades their alpha.

    Mechanisms: false breakout injection, trend extension, friction
    escalation, consensus contrarian, streak-breaking, mean-reversion noise.
    """

    def __init__(self, cfg: AdversarialConfig,
                 rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.rng = rng
        self.history_perturbation: List[float] = [0.0]
        self.history_friction:     List[float] = [1.0]
        self.trap_events:          List[Dict]  = []
        self._false_breakout_ttl:  int = 0
        self._breakout_reversal:   float = 0.0
        self._recent_returns:      List[float] = []

    def step(self, agent_pnl_by_type: Dict[str, float],
             consensus: float, momentum_signal: float, mr_signal: float,
             current_price: float, step: int,
             last_return: float = 0.0) -> Tuple[float, float]:
        self._recent_returns.append(last_return)
        if len(self._recent_returns) > 50:
            self._recent_returns = self._recent_returns[-50:]

        if not self.cfg.enabled:
            self.history_perturbation.append(0.0)
            self.history_friction.append(1.0)
            return 0.0, 1.0

        perturbation = 0.0
        friction     = 1.0

        # ── Streak-breaking ──────────────────────────────────────────────
        n = self.cfg.streak_break_length
        if len(self._recent_returns) >= n:
            tail = self._recent_returns[-n:]
            streak_strength = sum(abs(r) for r in tail) / n
            if all(r > 0 for r in tail):
                push = max(self.cfg.streak_break_size, streak_strength * 1.5)
                perturbation -= push * self.rng.uniform(0.8, 1.2)
                self.trap_events.append({"step": step, "type": "streak_break",
                                         "direction": -1})
            elif all(r < 0 for r in tail):
                push = max(self.cfg.streak_break_size, streak_strength * 1.5)
                perturbation += push * self.rng.uniform(0.8, 1.2)
                self.trap_events.append({"step": step, "type": "streak_break",
                                         "direction": 1})

        # ── Mean-reversion noise ─────────────────────────────────────────
        if len(self._recent_returns) >= 2 and last_return != 0:
            perturbation -= math.copysign(
                self.cfg.mean_revert_noise * self.rng.uniform(0.5, 1.5),
                last_return)

        # ── False breakout ───────────────────────────────────────────────
        if self._false_breakout_ttl > 0:
            perturbation += self._breakout_reversal
            self._false_breakout_ttl -= 1
        else:
            mom_pnl = agent_pnl_by_type.get("momentum", 0.0)
            if mom_pnl > 0 and self.rng.random() < self.cfg.trap_probability:
                direction = 1.0 if momentum_signal > 0 else -1.0
                breakout = direction * self.rng.uniform(
                    0.001, self.cfg.false_breakout_size)
                perturbation += breakout
                self._breakout_reversal = -breakout * 0.6
                self._false_breakout_ttl = self.rng.integers(2, 6)
                self.trap_events.append({"step": step, "type": "false_breakout",
                                         "direction": direction, "size": breakout})

        # ── Trend extension vs mean-reversion ────────────────────────────
        mr_pnl = agent_pnl_by_type.get("mean_reversion", 0.0)
        if mr_pnl > 0 and abs(mr_signal) > 0:
            trend_dir = -1.0 if mr_signal > 0 else 1.0
            extension = trend_dir * self.rng.uniform(
                0, self.cfg.false_breakout_size * self.cfg.trend_extension_mult)
            perturbation += extension * self.cfg.trap_probability

        # ── Friction escalation ──────────────────────────────────────────
        total_pnl = sum(agent_pnl_by_type.values())
        if total_pnl > 0:
            friction = 1.0 + min(total_pnl / 10_000 * self.cfg.friction_sensitivity, 5.0)
        else:
            friction = max(1.0 + total_pnl / 50_000, 0.5)

        # ── Consensus contrarian ─────────────────────────────────────────
        if abs(consensus) > self.cfg.consensus_threshold:
            push = -consensus * self.cfg.contrarian_push_frac * self.rng.uniform(0.5, 1.0)
            perturbation += push
            if self.rng.random() < 0.1:
                self.trap_events.append({"step": step, "type": "consensus_contrarian",
                                         "consensus": consensus, "push": push})

        self.history_perturbation.append(perturbation)
        self.history_friction.append(friction)
        return perturbation, friction

    @property
    def n_traps(self) -> int:
        return len(self.trap_events)


# ===========================================================================
# SECTION 18 · Latency Model
# ===========================================================================

class LatencyModel:
    """Stochastic execution delay + stale information."""

    def __init__(self, cfg: LatencyConfig, rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.rng = rng
        self._base_sigma = 0.0008

    def execution_delay(self, sigma: float) -> int:
        if not self.cfg.enabled:
            return 0
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        scaled_max = int(self.cfg.max_delay_steps *
                         min(vol_ratio * self.cfg.vol_delay_mult, 5.0))
        scaled_max = max(scaled_max, self.cfg.min_delay_steps)
        return int(self.rng.integers(self.cfg.min_delay_steps, scaled_max + 1))

    def delayed_price_history(self, price_history: np.ndarray) -> np.ndarray:
        if not self.cfg.enabled or self.cfg.info_delay_steps <= 0:
            return price_history
        d = self.cfg.info_delay_steps
        if len(price_history) <= d:
            return price_history
        return price_history[:-d]

    def slippage_multiplier(self, sigma: float) -> float:
        if not self.cfg.enabled:
            return 1.0
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        return 1.0 + 0.2 * max(vol_ratio - 1.0, 0.0)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART IV — UNIFIED MARKET SIMULATOR                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 19 · Unified Configuration
# ===========================================================================

@dataclass
class UnifiedConfig:
    """
    Master configuration controlling which phases are active.

    Modes
    -----
    phase1 : GBM, regime, jumps, spread, fees, leverage
    phase2 : + GARCH, volume, slippage, correlation, cascade
    phase3 : + agents, adversarial, latency, dynamic liquidity
    full   : alias for phase3
    """
    mode: str = "full"   # "phase1", "phase2", "phase3", "full"

    # Phase 1
    initial_price:  float = 50_000.0
    initial_regime: str   = "bull"
    jump_params:    JumpParams    = field(default_factory=JumpParams)
    spread_params:  SpreadParams  = field(default_factory=SpreadParams)
    fee_params:     FeeParams     = field(default_factory=FeeParams)
    leverage_params: Optional[LeverageParams] = None

    # Phase 2
    p2: Phase2Config = field(default_factory=Phase2Config)
    stress: StressTestConfig = field(default_factory=StressTestConfig)

    # Phase 3
    p3: Phase3Config = field(default_factory=Phase3Config)

    seed: Optional[int] = 42

    @property
    def phase2_enabled(self) -> bool:
        return self.mode in ("phase2", "phase3", "full")

    @property
    def phase3_enabled(self) -> bool:
        return self.mode in ("phase3", "full")


# ===========================================================================
# SECTION 20 · Unified Market Simulator
# ===========================================================================

class UnifiedMarketSimulator:
    """
    All-in-one simulator integrating Phases 1–3.

    Set ``mode`` to control which engines are active:
    - ``phase1``: Basic GBM + regime + jumps + spread + fees + leverage
    - ``phase2``: Phase 1 + GARCH + volume + slippage + correlation + cascade
    - ``phase3`` / ``full``: Phase 2 + agents + adversarial + latency + liquidity
    """

    def __init__(self, cfg: Optional[UnifiedConfig] = None) -> None:
        self.cfg = cfg or UnifiedConfig()
        c = self.cfg
        seed = c.seed

        self.rng = np.random.default_rng(seed)

        p2_cfg = c.p2
        p2_cfg.seed = seed

        # ── Phase 1 core ─────────────────────────────────────────────────
        use_emergent = c.phase3_enabled and c.p3.use_emergent_regime
        self._p1 = MarketSimulator(
            initial_price    = c.initial_price,
            seed             = seed,
            initial_regime   = c.initial_regime,
            enable_regime    = not use_emergent,
            enable_stochastic = True,
            enable_jumps     = True,
            enable_spread    = True,
            fee_params       = c.fee_params,
            jump_params      = c.jump_params,
            spread_params    = c.spread_params,
        )

        # ── Phase 2 engines ──────────────────────────────────────────────
        if c.phase2_enabled:
            if c.phase3_enabled:
                p2_cfg.cascade_threshold = -0.08  # gentler for agent environment
            self.garch = (GARCHVolatilityEngine(p2_cfg)
                          if p2_cfg.enable_garch_volatility else None)
            self.vol_eng = (VolumeSimulationEngine(p2_cfg, self.rng)
                            if p2_cfg.enable_volume_model else None)
            self.slippage = (DynamicSlippageModel(p2_cfg)
                             if p2_cfg.enable_slippage_model else None)
            self.cascade = (LiquidationCascadeEngine(p2_cfg)
                            if p2_cfg.enable_liquidation_cascade else None)
            self.corr_engine: Optional[CorrelatedAssetEngine] = None
            if p2_cfg.enable_correlated_assets:
                self.corr_engine = CorrelatedAssetEngine(p2_cfg, self.rng)
                self.corr_engine.initialise_prices(c.initial_price, 0.05)
        else:
            self.garch = None
            self.vol_eng = None
            self.slippage = None
            self.cascade = None
            self.corr_engine = None

        self.stress = c.stress

        # ── Phase 3 engines ──────────────────────────────────────────────
        p3 = c.p3
        if c.phase3_enabled:
            self.agent_engine    = AgentEngine(p3.agents, self.rng)
            self.order_book      = OrderBook()
            self.liquidity_eng   = DynamicLiquidityEngine(p3.liquidity)
            self.regime_detector = (EmergentRegimeDetector(p3.regime_detect_window)
                                    if p3.use_emergent_regime else None)
            self.adversarial     = AdversarialStressEngine(p3.adversarial, self.rng)
            self.latency_model   = LatencyModel(p3.latency, self.rng)
            self._agent_map: Dict[str, BaseAgent] = {
                a.id: a for a in self.agent_engine.agents}
            self._cascade_cooldown: int = 0
            self._cascade_cooldown_period: int = 20
        else:
            self.agent_engine    = None
            self.order_book      = None
            self.liquidity_eng   = None
            self.regime_detector = None
            self.adversarial     = None
            self.latency_model   = None
            self._agent_map      = {}
            self._cascade_cooldown = 0
            self._cascade_cooldown_period = 20

        # ── State histories ──────────────────────────────────────────────
        self.prices:     List[float] = [c.initial_price]
        self.returns:    List[float] = []
        self.sigmas:     List[float] = [self.garch.current_sigma if self.garch
                                         else REGIMES[c.initial_regime]["sigma"]]
        self.volumes:    List[float] = [p2_cfg.volume_base]
        self.regimes:    List[str]   = [c.initial_regime]
        self.jumps:      List[bool]  = [False]
        self.cascades:   List[bool]  = [False]
        self.liquidity:  List[float] = [p3.liquidity.baseline
                                         if c.phase3_enabled else 0.0]
        self.agent_impacts:       List[float] = [0.0]
        self.adversarial_shocks:  List[float] = [0.0]
        self.friction_history:    List[float] = [1.0]

        self.corr_prices: List[List[float]] = (
            [[p] for p in self.corr_engine.prices] if self.corr_engine else [])

        self.t: int = 0

    # ── Core step (unified) ──────────────────────────────────────────────

    def step(self) -> float:
        """Advance simulation by one step.  Returns new mid-price."""
        self.t += 1
        prev_price = self.prices[-1]
        c  = self.cfg
        p3 = c.p3

        # ── 1. Regime ────────────────────────────────────────────────────
        if c.phase3_enabled and p3.use_emergent_regime:
            regime = self.regimes[-1]  # updated at end
        else:
            self._p1._switch_regime()
            regime = self._p1.regime

        # ── 2. GARCH volatility ──────────────────────────────────────────
        if self.garch and self.returns:
            sigma = self.garch.update(self.returns[-1])
            sigma = self.stress.apply_sigma(sigma)
        else:
            sigma = REGIMES[regime]["sigma"]

        # ── 3. GBM shock ────────────────────────────────────────────────
        mu = REGIMES[regime]["mu"]
        gbm_shock = self.rng.normal(0.0, sigma)

        # ── 4. Jump event ────────────────────────────────────────────────
        jumped = False
        jump_size = 0.0
        jp = self._p1.jump_params
        if self.rng.random() < jp.probability:
            jump_size = self.rng.normal(jp.mean, jp.std)
            jumped = True

        # ── 5-7. Agent / order processing (Phase 3 only) ────────────────
        agent_impact = 0.0
        adv_shock = 0.0
        friction = 1.0

        if c.phase3_enabled and self.agent_engine is not None:
            # 5. Agent observation with latency
            price_arr = np.array(self.prices)
            obs_prices = self.latency_model.delayed_price_history(price_arr)

            # 6. Agent order generation
            orders = self.agent_engine.generate_all_orders(
                obs_prices, prev_price, sigma, regime, self.t)
            for order in orders:
                delay = self.latency_model.execution_delay(sigma)
                order.delay_steps = delay
                self.order_book.submit(order)

            # 7. Order book → price impact
            current_liq = self.liquidity_eng.current
            agent_impact, filled_orders = self.order_book.process(
                self.t, current_liq, prev_price, p3.price_impact_coeff)

            # 7b. Execute fills on agents
            for order in filled_orders:
                agent = self._agent_map.get(order.agent_id)
                if agent is not None:
                    agent.execute_fill(order, prev_price)

            # 8. Adversarial perturbation
            pnl_by_type = self.agent_engine.get_pnl_by_type()
            consensus, type_biases = self.agent_engine.get_consensus()
            mom_signal = type_biases.get("momentum", 0.0)
            mr_signal  = type_biases.get("mean_reversion", 0.0)
            adv_shock, friction = self.adversarial.step(
                pnl_by_type, consensus, mom_signal, mr_signal,
                prev_price, self.t,
                last_return=self.returns[-1] if self.returns else 0.0)

        # ── Combine all price effects ────────────────────────────────────
        combined_return = mu + gbm_shock + agent_impact + adv_shock
        if jumped:
            combined_return += jump_size

        # Clamp return (Phase 3 only, to prevent death spirals)
        if c.phase3_enabled:
            combined_return = float(np.clip(combined_return, -0.03, 0.03))

        new_price = prev_price * (1.0 + combined_return)
        new_price = max(new_price, jp.min_price)

        # ── Apply friction to spread ─────────────────────────────────────
        if self.stress.enabled:
            friction *= self.stress.spread_multiplier

        # ── 9. Liquidation cascade ───────────────────────────────────────
        cascaded = False
        ret = (new_price - prev_price) / max(prev_price, 1e-9)
        if c.phase3_enabled and self._cascade_cooldown > 0:
            self._cascade_cooldown -= 1
        if self.cascade is not None:
            if c.phase3_enabled and self._cascade_cooldown > 0:
                pass  # cooldown active, skip cascade
            else:
                new_price, cascaded = self.cascade.step(new_price, self.t, ret)
                if cascaded and c.phase3_enabled:
                    self._cascade_cooldown = self._cascade_cooldown_period
                ret = (new_price - prev_price) / max(prev_price, 1e-9)

        # ── 10. Dynamic liquidity (Phase 3) ──────────────────────────────
        liq = 0.0
        if c.phase3_enabled and self.liquidity_eng is not None:
            liq = self.liquidity_eng.step(sigma, jumped, cascaded)

        # ── 11. Correlated assets ────────────────────────────────────────
        if self.corr_engine is not None:
            j_sz = jump_size if jumped else 0.0
            new_c_prices = self.corr_engine.step(mu, sigma, jumped, j_sz)
            for i, p in enumerate(new_c_prices):
                self.corr_prices[i].append(p)

        # ── 12. Volume ───────────────────────────────────────────────────
        vol = 0.0
        if self.vol_eng is not None:
            vol = self.vol_eng.step(ret, regime, jumped)

        # ── 13. Emergent regime detection (Phase 3) ──────────────────────
        if c.phase3_enabled and self.regime_detector is not None:
            net_flow = self.order_book.net_flow if self.order_book else 0.0
            regime = self.regime_detector.detect(
                self.returns + [math.log(max(new_price / max(prev_price, 1e-9), 1e-9))],
                net_flow,
                self.prices + [new_price])

        # ── 14. Agent PnL update (Phase 3) ──────────────────────────────
        if c.phase3_enabled and self.agent_engine is not None:
            self.agent_engine.update_all_pnl(new_price, prev_price)

        # ── 15. Record ───────────────────────────────────────────────────
        log_ret = math.log(max(new_price / max(prev_price, 1e-9), 1e-9))
        self.prices.append(new_price)
        self.returns.append(log_ret)
        self.sigmas.append(sigma)
        self.volumes.append(vol if vol > 0 else self.cfg.p2.volume_base)
        self.regimes.append(regime)
        self.jumps.append(jumped)
        self.cascades.append(cascaded)
        self.liquidity.append(liq)
        self.agent_impacts.append(agent_impact)
        self.adversarial_shocks.append(adv_shock)
        self.friction_history.append(friction)

        self._p1.price = new_price
        return new_price

    def run(self, n_steps: int) -> np.ndarray:
        """Run simulation for n_steps."""
        for _ in range(n_steps):
            self.step()
        return np.array(self.prices)

    # ── Slippage-aware execution ─────────────────────────────────────────

    def execute_with_slippage(self, side: str, order_size: float,
                              order_type: str = "taker") -> Tuple[float, float]:
        mid    = self.prices[-1]
        sigma  = self.sigmas[-1]
        spread = self._p1.spread_params.base_spread
        jumped = self.jumps[-1]
        if self.slippage is not None:
            exec_price = self.slippage.compute(mid, side, order_size,
                                               spread, sigma, jumped)
        else:
            rec = self._p1.execute_trade(side, order_size / max(mid, 1e-9),
                                          order_type)
            exec_price = rec.exec_price
        fee_rate = (self._p1.fee_params.taker_fee if order_type == "taker"
                    else self._p1.fee_params.maker_fee) if self._p1.fee_params else 0.0
        fee_paid   = exec_price * order_size * fee_rate
        total_cost = exec_price * order_size + fee_paid
        return exec_price, total_cost

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def price(self) -> float:
        return self.prices[-1]

    @property
    def regime(self) -> str:
        return self.regimes[-1]

    @property
    def n_cascade_events(self) -> int:
        return len(self.cascade.events) if self.cascade else 0

    @property
    def n_adversarial_traps(self) -> int:
        return self.adversarial.n_traps if self.adversarial else 0

    @property
    def current_liquidity_pct(self) -> float:
        if self.liquidity_eng is not None:
            return self.liquidity_eng.fraction * 100
        return 0.0

    @property
    def mode(self) -> str:
        return self.cfg.mode

    # ── Summary ──────────────────────────────────────────────────────────

    def summary(self) -> Dict:
        """Print and return a comprehensive simulation summary."""
        prices = np.array(self.prices)
        n = len(prices) - 1
        c = self.cfg

        bar = "═" * 62
        print(f"\n{bar}")
        print(f"  UNIFIED SIMULATOR SUMMARY  —  mode: {c.mode}")
        print(bar)

        print(f"\n  Steps          : {n:,}")
        print(f"  Start price    : ${prices[0]:,.2f}")
        print(f"  End price      : ${prices[-1]:,.2f}")
        print(f"  Total return   : {(prices[-1]/prices[0]-1)*100:+.2f}%")

        # Phase 1 stats
        print(f"\n  ── Phase 1 ──────────────────────────────────────")
        print(f"  Jump events    : {sum(self.jumps)}")
        rc = Counter(self.regimes)
        total = len(self.regimes)
        for r in _REGIME_ORDER:
            print(f"    {r:10s} : {rc.get(r,0)/total*100:5.1f}%")

        # Phase 2 stats
        if c.phase2_enabled:
            print(f"\n  ── Phase 2 ──────────────────────────────────────")
            if self.garch:
                print(f"  GARCH σ final  : {self.sigmas[-1]*100:.4f}%")
                print(f"  Long-run σ     : {self.garch.long_run_sigma()*100:.4f}%")
            print(f"  Cascade events : {self.n_cascade_events}")
            if self.corr_engine and len(self.corr_prices) >= 2:
                r0 = np.diff(np.log(np.maximum(self.corr_prices[0], 1e-9)))
                r1 = np.diff(np.log(np.maximum(self.corr_prices[1], 1e-9)))
                if len(r0) > 10 and len(r1) > 10:
                    mn = min(len(r0), len(r1))
                    actual_corr = float(np.corrcoef(r0[:mn], r1[:mn])[0, 1])
                    target_corr = c.p2.correlation_matrix[0][1]
                    print(f"  Asset corr     : target={target_corr:.2f}"
                          f"  actual={actual_corr:.3f}")

        # Phase 3 stats
        if c.phase3_enabled and self.agent_engine is not None:
            print(f"\n  ── Phase 3 ──────────────────────────────────────")
            print(f"  Agents         : {self.agent_engine.n_agents}")
            print(f"  Adv traps      : {self.n_adversarial_traps}")
            print(f"  Liquidity now  : {self.current_liquidity_pct:.1f}%"
                  f" of baseline")
            print(f"\n  Agent performance:")
            for t_name, stats in self.agent_engine.get_agent_stats().items():
                print(f"    {t_name:20s} : avg PnL ${stats['avg_pnl']:>10,.2f}"
                      f"   capital ${stats['avg_capital']:>10,.2f}")

        # Risk metrics
        metrics = RiskMetrics.full_report(prices)
        print(bar + "\n")

        return {
            "mode": c.mode,
            "n_steps": n,
            "start_price": prices[0],
            "end_price": prices[-1],
            "metrics": metrics,
        }


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART V — VALIDATION                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 21 · Phase 1 Validation
# ===========================================================================

class Phase1Validator:
    """Statistical checks for Phase 1 simulation quality."""

    def __init__(self, sim: UnifiedMarketSimulator) -> None:
        self.sim = sim

    def check_fat_tails(self, threshold: float = 3.0) -> Dict:
        returns = np.array(self.sim.returns)
        if len(returns) < 50:
            return {"excess_kurtosis": 0, "fat_tails_detected": False}
        n = len(returns); mu = returns.mean(); sigma = returns.std(ddof=1)
        if sigma == 0:
            return {"excess_kurtosis": 0, "fat_tails_detected": False}
        excess_kurt = (np.sum((returns - mu) ** 4) / n) / (sigma ** 4) - 3.0
        return {"excess_kurtosis": float(excess_kurt),
                "threshold": threshold,
                "fat_tails_detected": bool(excess_kurt > threshold)}

    def check_volatility_clustering(self) -> Dict:
        ret = np.array(self.sim.returns)
        sq  = ret ** 2
        mean = sq.mean(); var = sq.var()
        if var == 0:
            return {"autocorr_sq_lag1": 0.0, "clustering_detected": False}
        autocorr = float(np.mean((sq[:-1] - mean) * (sq[1:] - mean)) / var)
        return {"autocorr_sq_lag1": autocorr,
                "clustering_detected": bool(autocorr > 0.05)}

    def check_spread_widening(self) -> Dict:
        spreads = np.array(self.sim._p1.spread_history)
        regimes = self.sim._p1.regime_history
        n = min(len(spreads), len(regimes))
        if n < 10:
            return {"spread_widens": False}
        spreads = spreads[:n]; regimes = regimes[:n]
        hv_mask = np.array([r == "high_vol" for r in regimes])
        other = spreads[~hv_mask]; hv = spreads[hv_mask]
        mean_hv    = float(hv.mean())    if hv.size    > 0 else float("nan")
        mean_other = float(other.mean()) if other.size > 0 else float("nan")
        ratio = mean_hv / mean_other if mean_other > 0 else float("nan")
        return {"mean_spread_high_vol": mean_hv, "mean_spread_normal": mean_other,
                "spread_ratio": ratio,
                "spread_widens": bool(ratio > 1.5) if not np.isnan(ratio) else False}

    def run_all(self) -> Dict:
        ft = self.check_fat_tails()
        vc = self.check_volatility_clustering()
        sw = self.check_spread_widening()
        bar = "═" * 55
        print(f"\n{bar}")
        print("  PHASE 1 VALIDATION")
        print(bar)
        print(f"  Fat tails      : kurtosis={ft['excess_kurtosis']:.2f}"
              f"  {'✓' if ft['fat_tails_detected'] else '✗'}")
        print(f"  Vol clustering : ACF={vc['autocorr_sq_lag1']:.4f}"
              f"  {'✓' if vc['clustering_detected'] else '✗'}")
        sw_str = f"ratio={sw.get('spread_ratio','N/A')}"
        print(f"  Spread widen   : {sw_str}"
              f"  {'✓' if sw.get('spread_widens') else '✗'}")
        print(bar + "\n")
        return {"fat_tails": ft, "vol_clustering": vc, "spread_widening": sw}


# ===========================================================================
# SECTION 22 · Phase 3 Validation
# ===========================================================================

class Phase3Validator:
    """Validates Phase 3 success criteria."""

    def __init__(self, sim: UnifiedMarketSimulator) -> None:
        self.sim = sim

    def validate_emergent_behaviour(self) -> Dict:
        impacts = np.array(self.sim.agent_impacts[1:])
        returns = np.array(self.sim.returns)
        n = min(len(impacts), len(returns))
        if n < 50:
            return {"status": "insufficient_data"}
        impacts, returns = impacts[:n], returns[:n]
        corr = float(np.corrcoef(impacts, returns)[0, 1])
        return {"agent_return_correlation": round(corr, 4),
                "r_squared": round(corr ** 2, 4),
                "emergent": bool(abs(corr) > 0.1),
                "status": "✓ emergent" if abs(corr) > 0.1 else "✗ not emergent"}

    def validate_liquidity_collapse(self) -> Dict:
        liq = np.array(self.sim.liquidity)
        baseline = self.sim.cfg.p3.liquidity.baseline
        min_liq = float(np.min(liq)); min_idx = int(np.argmin(liq))
        min_frac = min_liq / max(baseline, 1.0)
        collapsed = min_frac < 0.30
        return {"min_fraction_pct": round(min_frac * 100, 2), "min_step": min_idx,
                "collapsed": bool(collapsed),
                "status": "✓ collapses" if collapsed else "✗ no collapse"}

    def validate_adversarial_degradation(self) -> Dict:
        if self.sim.agent_engine is None:
            return {"status": "agents disabled"}
        stats = self.sim.agent_engine.get_agent_stats()
        mom_pnl = stats.get("momentum", {}).get("avg_pnl", 0)
        mr_pnl  = stats.get("mean_reversion", {}).get("avg_pnl", 0)
        alpha_degraded = (mom_pnl <= 0) or (mr_pnl <= 0)
        return {"momentum_avg_pnl": round(mom_pnl, 2),
                "mean_reversion_avg_pnl": round(mr_pnl, 2),
                "adversarial_traps": self.sim.n_adversarial_traps,
                "alpha_degraded": bool(alpha_degraded),
                "status": "✓ alpha degraded" if alpha_degraded else "✗ alpha survives"}

    def validate_naive_alpha_destruction(self) -> Dict:
        prices = np.array(self.sim.prices)
        if len(prices) < 200:
            return {"status": "insufficient_data"}
        n_consec = 5
        position = 0.0; equity = 10_000.0
        equity_curve = [equity]
        returns_arr = np.diff(prices) / prices[:-1]
        for i in range(n_consec, len(returns_arr)):
            recent = returns_arr[i - n_consec:i]
            if all(r > 0 for r in recent):    position = 1.0
            elif all(r < 0 for r in recent):  position = -1.0
            else:                              position = 0.0
            equity += position * returns_arr[i] * equity
            equity_curve.append(equity)
        eq = np.array(equity_curve)
        total_return = (eq[-1] - eq[0]) / eq[0]
        log_rets = np.diff(np.log(np.maximum(eq, 1e-9)))
        sharpe = float(np.mean(log_rets) / max(np.std(log_rets), 1e-12)
                       * math.sqrt(86400))
        destroyed = sharpe < 0.5 or total_return < 0.01
        return {"naive_total_return_pct": round(total_return * 100, 3),
                "naive_sharpe": round(sharpe, 4),
                "destroyed": bool(destroyed),
                "status": "✓ naïve destroyed" if destroyed else "✗ naïve survived"}

    def full_validation(self, print_report: bool = True) -> Dict:
        r1 = self.validate_emergent_behaviour()
        r2 = self.validate_liquidity_collapse()
        r3 = self.validate_adversarial_degradation()
        r4 = self.validate_naive_alpha_destruction()
        passed = sum(1 for r in [r1, r2, r3, r4]
                     if r.get("emergent") or r.get("collapsed")
                     or r.get("alpha_degraded") or r.get("destroyed"))
        if print_report:
            bar = "═" * 58
            print(f"\n{bar}")
            print("  PHASE 3 VALIDATION REPORT")
            print(bar)
            print(f"  1. Emergent      : {r1.get('status')}  (R²={r1.get('r_squared','N/A')})")
            print(f"  2. Liquidity     : {r2.get('status')}  (min={r2.get('min_fraction_pct','N/A')}%)")
            print(f"  3. Adversarial   : {r3.get('status')}  (traps={r3.get('adversarial_traps',0)})")
            print(f"  4. Naïve alpha   : {r4.get('status')}  (Sharpe={r4.get('naive_sharpe','N/A')})")
            print(f"\n  PASSED {passed} / 4 criteria")
            print(bar + "\n")
        return {"emergent": r1, "liquidity": r2, "adversarial": r3,
                "naive_alpha": r4, "passed": passed}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART VI — VISUALIZATION                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_DARK = "#131722"
_GRID = "#1e222d"
_TEXT = "#d1d4dc"


def _setup_dark(fig: plt.Figure) -> None:
    fig.patch.set_facecolor(_DARK)
    for ax in fig.axes:
        ax.set_facecolor(_DARK)
        ax.tick_params(colors=_TEXT, labelsize=8)
        ax.xaxis.label.set_color(_TEXT)
        ax.yaxis.label.set_color(_TEXT)
        ax.title.set_color(_TEXT)
        for sp in ax.spines.values():
            sp.set_edgecolor(_GRID)
        ax.grid(True, color=_GRID, linewidth=0.5, linestyle="--")


# ===========================================================================
# SECTION 23 · Phase 1 Plots
# ===========================================================================

def plot_phase1(sim: UnifiedMarketSimulator) -> plt.Figure:
    """4-panel Phase 1 diagnostic: price, returns, distribution, rolling vol."""
    prices  = np.array(sim.prices)
    returns = np.array(sim.returns)
    T = len(prices)

    _bgcmap = {"bull": "#d5f5e3", "bear": "#fadbd8",
               "high_vol": "#fdebd0", "low_vol": "#d6eaf8"}

    fig, axes = plt.subplots(4, 1, figsize=(15, 12))
    fig.suptitle("Phase 1 — Regime + GBM + Jumps", fontsize=12, fontweight="bold")
    ax1, ax2, ax3, ax4 = axes

    # Price with regime shading
    regimes = sim.regimes
    if regimes:
        prev_r, seg_s = regimes[0], 0
        for i, r in enumerate(regimes[1:], start=1):
            if r != prev_r:
                ax1.axvspan(seg_s, i, color=_bgcmap.get(prev_r, "#eee"),
                            alpha=0.55, lw=0)
                prev_r, seg_s = r, i
        ax1.axvspan(seg_s, T, color=_bgcmap.get(prev_r, "#eee"), alpha=0.55, lw=0)
    ax1.plot(range(T), prices, color="#2c3e50", lw=0.8)
    jump_idx = np.where(sim.jumps)[0]
    if jump_idx.size:
        ax1.scatter(jump_idx, prices[jump_idx], color="#e74c3c", s=18,
                    zorder=5, marker="^")
    ax1.set_ylabel("Price"); ax1.set_title("Mid-Price", fontsize=9)
    patches = [Patch(fc=_bgcmap[r], ec="gray", label=r) for r in _REGIME_ORDER]
    ax1.legend(handles=patches, fontsize=7, ncol=4, loc="upper left")

    # Returns
    ax2.plot(range(len(returns)), returns, color="#2980b9", lw=0.5, alpha=0.8)
    ax2.axhline(0, color="#95a5a6", lw=0.7, ls="--")
    ax2.set_ylabel("Log Return"); ax2.set_title("Log Returns", fontsize=9)

    # Distribution
    if len(returns) > 10:
        mu_r, std_r = returns.mean(), returns.std()
        bins = min(120, max(20, len(returns) // 40))
        ax3.hist(returns, bins=bins, density=True, color="#3498db", alpha=0.5,
                 label="Simulated")
        xr = np.linspace(returns.min(), returns.max(), 400)
        ax3.plot(xr,
                 np.exp(-0.5 * ((xr - mu_r) / std_r) ** 2) / (std_r * np.sqrt(2 * np.pi)),
                 color="#e74c3c", lw=1.5, label="Normal fit")
        ax3.legend(fontsize=8)
    ax3.set_title("Return Distribution vs Normal", fontsize=9)

    # Rolling vol
    win = 50
    if len(returns) >= win:
        rvol = [returns[max(0, i - win):i].std() for i in range(win, len(returns) + 1)]
        ax4.plot(range(win, len(returns) + 1), rvol, color="#8e44ad", lw=0.8)
    ax4.set_title("Rolling 50-Step Volatility", fontsize=9)
    ax4.set_xlabel("Step")

    fig.tight_layout()
    return fig


# ===========================================================================
# SECTION 24 · Phase 2 Plots
# ===========================================================================

def plot_phase2_summary(sim: UnifiedMarketSimulator) -> plt.Figure:
    """5-panel Phase 2: price, σ, volume, OI, equity."""
    prices = np.array(sim.prices)
    fig, axes = plt.subplots(5, 1, figsize=(14, 16), sharex=True,
                              gridspec_kw={"height_ratios": [3, 2, 2, 2, 2]})
    t = np.arange(len(prices))

    # 1. Price
    axes[0].plot(t, prices, color="#26a69a", linewidth=0.5)
    if sim.cascade:
        for ev in sim.cascade.events:
            axes[0].axvline(ev.step, color="#f38720", alpha=0.35, linewidth=0.8)
    axes[0].set_ylabel("Price")
    garch_on = "✓" if sim.garch else "✕"
    vol_on   = "✓" if sim.vol_eng else "✕"
    slip_on  = "✓" if sim.slippage else "✕"
    corr_on  = "✓" if sim.corr_engine else "✕"
    casc_on  = "✓" if sim.cascade else "✕"
    axes[0].set_title(
        f"Phase 2 Summary  —  GARCH={garch_on}  VOL={vol_on}  "
        f"SLIP={slip_on}  CORR={corr_on}  CASCADE={casc_on}", pad=6, fontsize=9)

    # 2. Volatility
    sigmas = np.array(sim.sigmas[:len(t)])
    axes[1].plot(t[:len(sigmas)], sigmas * 100, color="#c084fc", linewidth=0.5)
    if sim.garch:
        lr = sim.garch.long_run_sigma() * 100
        axes[1].axhline(lr, linestyle="--", color="#fb923c", linewidth=1,
                        label=f"Long-run σ = {lr:.3f}%")
        axes[1].legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)
    axes[1].set_ylabel("σ (%)")

    # 3. Volume
    vols = np.array(sim.volumes[:len(t)])
    axes[2].bar(t[:len(vols)], vols, color="#3b82f680", width=1.0)
    axes[2].set_ylabel("Volume")

    # 4. Open Interest
    if sim.cascade:
        oi = np.array(sim.cascade.oi_history[:len(t)])
        axes[3].plot(np.arange(len(oi)), oi / 1e6, color="#f59e0b", linewidth=0.6)
        axes[3].set_ylabel("OI (M)")
    else:
        axes[3].text(0.5, 0.5, "Cascade disabled", ha="center", va="center",
                     color="#787b86", transform=axes[3].transAxes)

    # 5. Equity
    log_ret = np.diff(np.log(np.maximum(prices, 1e-9)))
    equity  = 10_000 * np.exp(np.cumsum(log_ret))
    equity  = np.insert(equity, 0, 10_000.0)
    axes[4].plot(np.arange(len(equity)), equity, color="#26a69a", linewidth=0.6)
    running_max = np.maximum.accumulate(equity)
    axes[4].fill_between(np.arange(len(equity)), equity, running_max,
                          color="#ef5350", alpha=0.15)
    axes[4].set_ylabel("Equity ($)")
    axes[4].set_xlabel("Step")

    _setup_dark(fig)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    return fig


def plot_correlated_assets(sim: UnifiedMarketSimulator) -> Optional[plt.Figure]:
    """Plot all correlated asset prices + rolling correlation."""
    if not sim.corr_engine or not sim.corr_prices:
        return None
    n_a = sim.cfg.p2.n_assets
    fig = plt.figure(figsize=(14, 9))
    gs  = gridspec.GridSpec(n_a + 1, 1, hspace=0.35)
    colours = ["#26a69a", "#60a5fa", "#f472b6", "#fb923c"]
    axes = []
    for i in range(n_a):
        ax = fig.add_subplot(gs[i], sharex=axes[0] if axes else None)
        t  = np.arange(len(sim.corr_prices[i]))
        ax.plot(t, sim.corr_prices[i], color=colours[i % len(colours)],
                linewidth=0.6, label=sim.cfg.p2.asset_names[i])
        ax.set_ylabel("Price")
        ax.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)
        axes.append(ax)
    ax_corr = fig.add_subplot(gs[n_a], sharex=axes[0])
    if n_a >= 2:
        r0 = np.diff(np.log(np.maximum(sim.corr_prices[0], 1e-9)))
        r1 = np.diff(np.log(np.maximum(sim.corr_prices[1], 1e-9)))
        roll = sim.corr_engine.rolling_correlation(r0, r1, window=60)
        ax_corr.plot(np.arange(len(roll)), roll, color="#f59e0b", linewidth=0.7,
                     label="60-step rolling corr")
        target = sim.cfg.p2.correlation_matrix[0][1]
        ax_corr.axhline(target, linestyle="--", color="#ef4444", linewidth=1,
                        label=f"Target ρ = {target:.2f}")
        ax_corr.set_ylim(-1.1, 1.1)
        ax_corr.set_ylabel("Correlation")
        ax_corr.set_xlabel("Step")
        ax_corr.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)
    axes[0].set_title("Correlated Asset Prices", pad=6)
    _setup_dark(fig)
    return fig


# ===========================================================================
# SECTION 25 · Phase 3 Plots
# ===========================================================================

def plot_phase3_summary(sim: UnifiedMarketSimulator) -> plt.Figure:
    """8-panel Phase 3: price, regime, σ, liquidity, flow, adv, volume, agent PnL."""
    prices = np.array(sim.prices)
    n = len(prices)
    t = np.arange(n)

    fig, axes = plt.subplots(8, 1, figsize=(16, 26), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1, 2, 2, 2, 2, 2, 2]})

    # 1. Price
    ax = axes[0]
    ax.plot(t, prices, color="#26a69a", linewidth=0.5)
    if sim.cascade:
        for ev in sim.cascade.events:
            ax.axvline(ev.step, color="#f38720", alpha=0.4, linewidth=0.8)
    for i, j in enumerate(sim.jumps):
        if j and i < n:
            ax.plot(i, prices[i], 'v', color="#c084fc", markersize=3, alpha=0.7)
    ax.set_ylabel("Price")
    n_agents = sim.agent_engine.n_agents if sim.agent_engine else 0
    ax.set_title(
        f"Phase 3 Agent-Based Simulator  —  "
        f"Agents={n_agents}  Traps={sim.n_adversarial_traps}  "
        f"Cascades={sim.n_cascade_events}  Jumps={sum(sim.jumps)}",
        pad=8, fontsize=10)

    # 2. Regime
    ax = axes[1]
    regime_colors = {"bull": "#26a69a", "bear": "#ef5350",
                     "high_vol": "#f38720", "low_vol": "#3b82f6"}
    for i in range(1, len(sim.regimes)):
        c = regime_colors.get(sim.regimes[i], "#787b86")
        ax.axvspan(i - 1, i, facecolor=c, alpha=0.4, linewidth=0)
    ax.set_ylabel("Regime"); ax.set_yticks([])
    patches = [Patch(facecolor=c, label=r, alpha=0.6)
               for r, c in regime_colors.items()]
    ax.legend(handles=patches, fontsize=7, loc="upper right",
              facecolor=_DARK, labelcolor=_TEXT, ncol=4)

    # 3. Volatility
    ax = axes[2]
    sigmas = np.array(sim.sigmas[:n])
    ax.plot(t[:len(sigmas)], sigmas * 100, color="#c084fc", linewidth=0.5)
    if sim.garch:
        lr = sim.garch.long_run_sigma() * 100
        ax.axhline(lr, linestyle="--", color="#fb923c", linewidth=1,
                    label=f"Long-run σ = {lr:.3f}%")
        ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)
    ax.set_ylabel("σ (%)")

    # 4. Liquidity
    ax = axes[3]
    liq = np.array(sim.liquidity[:n])
    baseline = sim.cfg.p3.liquidity.baseline
    ax.fill_between(t[:len(liq)], 0, liq / 1e6, color="#3b82f6", alpha=0.3)
    ax.plot(t[:len(liq)], liq / 1e6, color="#3b82f6", linewidth=0.6)
    ax.axhline(baseline / 1e6, linestyle="--", color="#787b86", linewidth=0.8,
               label=f"Baseline {baseline/1e6:.1f}M")
    ax.set_ylabel("Liquidity ($M)")
    ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)

    # 5. Net order flow
    ax = axes[4]
    if sim.order_book:
        flow = np.array(sim.order_book.history_net_flow[:n])
        colours = ['#26a69a' if v >= 0 else '#ef5350' for v in flow]
        ax.bar(t[:len(flow)], flow, color=colours, width=1.0)
    ax.set_ylabel("Net Flow ($)")

    # 6. Adversarial perturbation
    ax = axes[5]
    adv = np.array(sim.adversarial_shocks[:n])
    ax.plot(t[:len(adv)], adv * 100, color="#ef5350", linewidth=0.5, alpha=0.8)
    ax.axhline(0, color="#787b86", linewidth=0.5)
    if sim.adversarial:
        for trap in sim.adversarial.trap_events:
            s = trap["step"]
            if s < n:
                ax.axvline(s, color="#f38720", alpha=0.3, linewidth=0.7)
    ax.set_ylabel("Adv shock (%)")

    # 7. Volume
    ax = axes[6]
    vols = np.array(sim.volumes[:n])
    ax.bar(t[:len(vols)], vols, color="#3b82f680", width=1.0)
    ax.set_ylabel("Volume")

    # 8. Agent PnL by type
    ax = axes[7]
    if sim.agent_engine:
        type_colors = {"momentum": "#f59e0b", "mean_reversion": "#60a5fa",
                       "market_maker": "#34d399", "noise": "#787b86",
                       "liq_hunter": "#ef5350"}
        type_pnl_series: Dict[str, np.ndarray] = {}
        for agent in sim.agent_engine.agents:
            t_name = agent.agent_type
            pnl_arr = np.array(agent.pnl_history)
            if t_name not in type_pnl_series:
                type_pnl_series[t_name] = np.zeros(len(pnl_arr))
            n_min = min(len(type_pnl_series[t_name]), len(pnl_arr))
            type_pnl_series[t_name][:n_min] += pnl_arr[:n_min]
        for t_name, pnl_arr in type_pnl_series.items():
            count = sum(1 for a in sim.agent_engine.agents
                        if a.agent_type == t_name)
            ax.plot(np.arange(len(pnl_arr)), pnl_arr / max(count, 1),
                    color=type_colors.get(t_name, "#787b86"),
                    linewidth=0.8, label=t_name)
    ax.axhline(0, color="#787b86", linewidth=0.5)
    ax.set_ylabel("Avg PnL ($)"); ax.set_xlabel("Step")
    ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT, ncol=5)

    _setup_dark(fig)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    return fig


def plot_agent_performance(sim: UnifiedMarketSimulator) -> Optional[plt.Figure]:
    """Bar chart of final PnL per agent type."""
    if sim.agent_engine is None:
        return None
    stats = sim.agent_engine.get_agent_stats()
    types = list(stats.keys())
    avg_pnls = [stats[t]["avg_pnl"] for t in types]
    colors = {"momentum": "#f59e0b", "mean_reversion": "#60a5fa",
              "market_maker": "#34d399", "noise": "#787b86",
              "liq_hunter": "#ef5350"}
    bar_colors = [colors.get(t, "#787b86") for t in types]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(types, avg_pnls, color=bar_colors, edgecolor="#2a2e39")
    for bar, val in zip(bars, avg_pnls):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"${val:,.0f}", ha="center",
                va="bottom" if val >= 0 else "top",
                color=_TEXT, fontsize=9)
    ax.axhline(0, color="#787b86", linewidth=0.8)
    ax.set_ylabel("Avg PnL ($)"); ax.set_title("Agent Performance by Type", pad=6)
    _setup_dark(fig)
    fig.tight_layout()
    return fig


# ===========================================================================
# SECTION 26 · Unified Plotting Entry Point
# ===========================================================================

def plot_all(sim: UnifiedMarketSimulator) -> List[plt.Figure]:
    """Generate all relevant plots based on active mode."""
    figs = []
    mode = sim.cfg.mode

    # Phase 1 always available
    figs.append(plot_phase1(sim))

    # Phase 2
    if sim.cfg.phase2_enabled:
        figs.append(plot_phase2_summary(sim))
        fig_corr = plot_correlated_assets(sim)
        if fig_corr:
            figs.append(fig_corr)

    # Phase 3
    if sim.cfg.phase3_enabled:
        figs.append(plot_phase3_summary(sim))
        fig_agent = plot_agent_performance(sim)
        if fig_agent:
            figs.append(fig_agent)

    return figs


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART VII — CANDLE AGGREGATION & LIVE CHART                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 27 · Candle Aggregator
# ===========================================================================

def _format_sim_time(seconds: int) -> str:
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600)  // 60
    s = seconds % 60
    if d > 0:   return f"{d}d {h:02d}h {m:02d}m {s:02d}s"
    if h > 0:   return f"{h:02d}h {m:02d}m {s:02d}s"
    if m > 0:   return f"{m:02d}m {s:02d}s"
    return f"{s}s"


class CandleAggregator:
    """Aggregates price ticks into OHLC candles."""

    def __init__(self, steps_per_candle: int) -> None:
        self.spc    = steps_per_candle
        self.candles: List[Tuple[int, float, float, float, float]] = []
        self._open: Optional[float] = None
        self._high: Optional[float] = None
        self._low:  Optional[float] = None
        self._last: Optional[float] = None
        self._start: int = 0
        self._count: int = 0

    def push(self, step: int, price: float) -> Optional[Tuple]:
        if self._open is None:
            self._open = price; self._high = price
            self._low  = price; self._last = price
            self._start = step; self._count = 1
        else:
            if price > self._high: self._high = price
            if price < self._low:  self._low  = price
            self._last = price; self._count += 1
        if self._count >= self.spc:
            candle = (self._start, self._open, self._high, self._low, self._last)
            self.candles.append(candle)
            self._open = None; self._high = None
            self._low  = None; self._last = None
            self._count = 0;   self._start = step + 1
            return candle
        return None

    @property
    def current(self) -> Optional[Tuple]:
        if self._open is None:
            return None
        return (self._start, self._open, self._high, self._low, self._last)

    @property
    def progress(self) -> float:
        return self._count / self.spc if self.spc > 0 else 0.0


# ===========================================================================
# SECTION 28 · Live 7-Timeframe Chart
# ===========================================================================

def _style_candle_ax(ax: "plt.Axes") -> None:
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#55557a", labelsize=6, length=2, width=0.5)
    ax.yaxis.set_tick_params(right=True, left=False, labelright=True, labelleft=False)
    for sp in ax.spines.values():
        sp.set_edgecolor("#1e1e35")


def _draw_candles(ax, completed, current, max_display, tf_label):
    ax.cla(); ax.set_facecolor("#0d1117")
    disp    = list(completed[-max_display:])
    is_live = [False] * len(disp)
    if current is not None:
        disp.append(current); is_live.append(True)
    if not disp:
        ax.set_title(tf_label, fontsize=8, color="#444466", pad=2)
        _style_candle_ax(ax); return
    n_c    = len(disp)
    opens  = np.array([c[1] for c in disp], dtype=float)
    highs  = np.array([c[2] for c in disp], dtype=float)
    lows   = np.array([c[3] for c in disp], dtype=float)
    closes = np.array([c[4] for c in disp], dtype=float)
    bull = closes >= opens; live = np.array(is_live, dtype=bool)
    xs   = np.arange(n_c, dtype=float)
    segs = [[(float(i), lows[i]), (float(i), highs[i])] for i in range(n_c)]
    wcols = []
    for i in range(n_c):
        if live[i]:
            wcols.append("#1a6b65" if bull[i] else "#7b2525")
        else:
            wcols.append("#26a69a" if bull[i] else "#ef5350")
    lc = LineCollection(segs, colors=wcols, linewidths=0.8, zorder=2)
    ax.add_collection(lc)
    body_lo = np.minimum(opens, closes)
    body_hi = np.maximum(opens, closes)
    heights = np.maximum(body_hi - body_lo, (highs - lows) * 0.1 + 1e-12)
    def _b(mask, color, alpha=1.0):
        if mask.any():
            ax.bar(xs[mask], heights[mask], bottom=body_lo[mask],
                   width=0.65, color=color, linewidth=0, zorder=3, alpha=alpha)
    _b(bull & ~live, "#26a69a"); _b(~bull & ~live, "#ef5350")
    _b(bull & live,  "#1a8a80", 0.65); _b(~bull & live, "#8b3030", 0.65)
    pr = highs.max() - lows.min()
    margin = max(pr * 0.08, highs.max() * 0.0005, 1e-6)
    ax.set_ylim(lows.min() - margin, highs.max() + margin)
    ax.set_xlim(-0.5, n_c + 0.5)
    ax.axhline(closes[-1], color="#cccccc", linewidth=0.35, alpha=0.45, linestyle="--")
    n_closed = len(completed[-max_display:])
    ax.set_title(f"[ {tf_label} ]  {n_closed} closed  ·  {closes[-1]:.4f}",
                 fontsize=8, color="#9999bb", pad=3, loc="left")
    _style_candle_ax(ax)


class LiveSimulationPlot:
    """Real-time 7-timeframe candlestick chart driven by UnifiedMarketSimulator."""

    TIMEFRAMES = [
        ("1s",  1,     120), ("1m",  60,    90), ("5m",  300,   60),
        ("15m", 900,   40),  ("30m", 1800,  30), ("1h",  3600,  24),
        ("1d",  86400, 14),
    ]
    _REGIME_COL = {"bull": "#2ecc71", "bear": "#e74c3c",
                   "high_vol": "#e67e22", "low_vol": "#3498db"}

    def __init__(self, sim: UnifiedMarketSimulator,
                 interval_ms: int = 450) -> None:
        self.sim = sim
        self.interval_ms = interval_ms
        self._anim = None
        self.aggregators = {tf: CandleAggregator(spc)
                            for tf, spc, _ in self.TIMEFRAMES}
        self.fig = plt.figure(figsize=(18, 13), facecolor="#0d1117")
        gs_ = gridspec.GridSpec(4, 2, figure=self.fig, hspace=0.52, wspace=0.15)
        positions = [(0, 0), (0, 1), (1, 0), (1, 1),
                     (2, 0), (2, 1), (3, 0)]
        self.ax = {}
        for (tf, spc, _), pos in zip(self.TIMEFRAMES, positions):
            r, col = pos
            self.ax[tf] = self.fig.add_subplot(gs_[r, col])
        self.ax_stats = self.fig.add_subplot(gs_[3, 1])
        self.ax_stats.set_facecolor("#0d1117"); self.ax_stats.axis("off")
        self._stats_text = self.ax_stats.text(
            0.06, 0.94, "Initialising...",
            transform=self.ax_stats.transAxes,
            va="top", fontsize=9, color="#aabbdd",
            fontfamily="monospace", linespacing=1.6)

    def _update(self, _frame):
        sim = self.sim
        sim.step()
        price = sim.price; step = sim.t
        for tf, _, _ in self.TIMEFRAMES:
            self.aggregators[tf].push(step, price)
        for tf, _, max_c in self.TIMEFRAMES:
            _draw_candles(self.ax[tf], self.aggregators[tf].candles,
                          self.aggregators[tf].current, max_c, tf)
        n_jumps = sum(sim.jumps)
        lines = [
            "====== LIVE STATS ======", "",
            f"Mode      {sim.mode:>14}",
            f"Sim Time  {_format_sim_time(step):>14}",
            f"Step      {step:>14,}",
            f"Price     {price:>14.4f}",
            f"Regime    {sim.regime:>14}",
            f"Jumps     {n_jumps:>14}",
        ]
        if sim.cfg.phase3_enabled:
            lines.append(f"Agents    {sim.agent_engine.n_agents:>14}")
            lines.append(f"Traps     {sim.n_adversarial_traps:>14}")
            lines.append(f"Liq %     {sim.current_liquidity_pct:>13.1f}%")
        lines.append(""); lines.append("Closed candles:")
        for tf, _, _ in self.TIMEFRAMES:
            n_c = len(self.aggregators[tf].candles)
            pct = self.aggregators[tf].progress * 100
            lines.append(f"  {tf:<4} {n_c:>6}  ({pct:4.1f}% open)")
        self._stats_text.set_text("\n".join(lines))
        rc = self._REGIME_COL.get(sim.regime, "#ffffff")
        self.fig.suptitle(
            f"UNIFIED SIMULATOR [{sim.mode.upper()}]  |  "
            f"{_format_sim_time(step)}  |  Price: {price:.4f}  |  "
            f"Regime: {sim.regime.upper()}",
            fontsize=10, fontweight="bold", color=rc, y=0.997)

    def start(self):
        """Launch the live animation.  Blocks until window is closed."""
        from matplotlib.animation import FuncAnimation
        self._anim = FuncAnimation(self.fig, self._update,
                                   interval=self.interval_ms,
                                   cache_frame_data=False, save_count=0)
        plt.show()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  PART VIII — FACTORY FUNCTIONS & DEMO                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ===========================================================================
# SECTION 29 · Factory Functions
# ===========================================================================

def _default_corr(n: int) -> List[List[float]]:
    base = 0.65
    return [[1.0 if i == j else base * (0.9 ** abs(i - j))
             for j in range(n)] for i in range(n)]


def build_simulator(
    mode: str = "full",
    initial_price: float = 50_000.0,
    seed: int = 42,
    adversarial: bool = True,
    emergent_regime: bool = True,
    stress: Optional[StressTestConfig] = None,
    n_assets: int = 2,
) -> UnifiedMarketSimulator:
    """
    Create a ready-to-run UnifiedMarketSimulator.

    Parameters
    ----------
    mode       : "phase1", "phase2", "phase3", or "full"
    initial_price : starting mid-price
    seed       : RNG seed
    adversarial: enable adversarial engine (phase3/full only)
    emergent_regime: enable emergent regime detection (phase3/full only)
    stress     : optional StressTestConfig
    n_assets   : number of correlated assets (phase2+ only)
    """
    p2_cfg = Phase2Config(
        enable_garch_volatility    = mode != "phase1",
        enable_volume_model        = mode != "phase1",
        enable_slippage_model      = mode != "phase1",
        enable_correlated_assets   = mode != "phase1",
        enable_liquidation_cascade = mode != "phase1",
        n_assets      = n_assets,
        asset_names   = ["BTC", "ETH", "SOL", "BNB"][:n_assets],
        asset_vol_scalars = [1.0, 1.4, 2.0, 1.2][:n_assets],
        correlation_matrix = _default_corr(n_assets),
        seed = seed,
    )
    p3_cfg = Phase3Config(
        agents     = AgentConfig(),
        liquidity  = LiquidityConfig(),
        adversarial = AdversarialConfig(enabled=adversarial),
        latency    = LatencyConfig(enabled=True),
        p2         = p2_cfg,
        use_emergent_regime = emergent_regime,
        seed = seed,
    )
    cfg = UnifiedConfig(
        mode           = mode,
        initial_price  = initial_price,
        p2             = p2_cfg,
        p3             = p3_cfg,
        stress         = stress or StressTestConfig(),
        seed           = seed,
    )
    return UnifiedMarketSimulator(cfg)


# ===========================================================================
# SECTION 30 · Unified Demo
# ===========================================================================

def run_demo(n_steps: int = 5_000, seed: int = 42,
             mode: str = "full") -> UnifiedMarketSimulator:
    """
    Comprehensive demonstration running all phases.

    Parameters
    ----------
    n_steps : simulation length
    seed    : RNG seed
    mode    : "phase1", "phase2", "phase3", or "full"
    """
    print("=" * 62)
    print(f"  Unified Synthetic Crypto Market Simulator")
    print(f"  Mode: {mode.upper()}  |  Steps: {n_steps:,}  |  Seed: {seed}")
    print("=" * 62)

    sim = build_simulator(mode=mode, initial_price=50_000.0, seed=seed,
                          adversarial=True, emergent_regime=True)

    if sim.agent_engine:
        print(f"\n  Agents created: {sim.agent_engine.n_agents}")
        for t, s in sim.agent_engine.get_agent_stats().items():
            print(f"    {t:20s} × {s['count']}")

    print(f"\n  Running {n_steps:,} steps...")
    sim.run(n_steps=n_steps)

    # Summary
    sim.summary()

    # Phase 1 validation
    p1v = Phase1Validator(sim)
    p1v.run_all()

    # Phase 3 validation (if applicable)
    if sim.cfg.phase3_enabled:
        p3v = Phase3Validator(sim)
        p3v.full_validation(print_report=True)

    # Plots
    print("  Generating plots...")
    plot_all(sim)
    plt.show()
    return sim


def run_comparison(n_steps: int = 3_000, seed: int = 42) -> None:
    """Compare adversarial ON vs OFF side by side."""
    print("=" * 62)
    print("  ADVERSARIAL COMPARISON TEST")
    print("=" * 62)

    print("\n  [1/2] Running WITHOUT adversarial...")
    sim_clean = build_simulator(mode="full", seed=seed, adversarial=False)
    sim_clean.run(n_steps)

    print("  [2/2] Running WITH adversarial...")
    sim_adv = build_simulator(mode="full", seed=seed, adversarial=True)
    sim_adv.run(n_steps)

    bar = "─" * 58
    print(f"\n{bar}")
    print(f"  {'Agent Type':<22} {'PnL (clean)':>12} {'PnL (adv)':>12} {'Δ':>10}")
    print(bar)

    stats_clean = sim_clean.agent_engine.get_agent_stats()
    stats_adv   = sim_adv.agent_engine.get_agent_stats()
    for t_name in ["momentum", "mean_reversion", "market_maker", "noise", "liq_hunter"]:
        pnl_c = stats_clean.get(t_name, {}).get("avg_pnl", 0)
        pnl_a = stats_adv.get(t_name, {}).get("avg_pnl", 0)
        print(f"  {t_name:<22} ${pnl_c:>10,.2f}  ${pnl_a:>10,.2f}"
              f"  ${pnl_a - pnl_c:>8,.2f}")
    print(bar)
    print(f"  Adversarial traps: {sim_adv.n_adversarial_traps}")
    print(f"  ✓ Complete\n")


def run_progressive_demo(seed: int = 42) -> None:
    """
    Demonstrates all 3 phases sequentially:
      Phase 1 → Phase 2 → Phase 3 (Full)
    """
    print("\n" + "▓" * 62)
    print("  PROGRESSIVE PHASE DEMONSTRATION")
    print("▓" * 62)

    for mode, steps in [("phase1", 3_000), ("phase2", 3_000), ("full", 5_000)]:
        print(f"\n{'─' * 62}")
        print(f"  PHASE: {mode.upper()}  —  {steps:,} steps")
        print(f"{'─' * 62}")
        sim = build_simulator(mode=mode, initial_price=50_000.0, seed=seed)
        sim.run(steps)
        sim.summary()

        if mode == "phase1":
            Phase1Validator(sim).run_all()
        elif mode in ("phase3", "full"):
            Phase3Validator(sim).full_validation()

    print("\n  ✓ Progressive demo complete\n")


# ===========================================================================
# SECTION 31 · Entry Point
# ===========================================================================

if __name__ == "__main__":
    run_demo(n_steps=5_000, seed=42, mode="full")
