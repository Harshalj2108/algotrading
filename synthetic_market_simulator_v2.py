"""
synthetic_market_simulator_v2.py
==================================
Phase 2 upgrade of the Synthetic Crypto Market Simulator.

New modules (each independently toggleable via Phase2Config):
  1. GARCH(1,1) Volatility Engine     – volatility clustering
  2. Volume Simulation Engine          – realistic volume dynamics
  3. Dynamic Slippage Model            – vol + size dependent execution cost
  4. Correlated Asset Engine           – Cholesky-decomposed multi-asset shocks
  5. Liquidation Cascade Engine        – forced liquidations amplify crashes

Support modules:
  6. Risk Metrics                      – Sharpe, drawdown, VaR, win-rate, expectancy
  7. Stress Testing Framework          – parameterised friction tests
  8. Phase2MarketSimulator             – master simulator integrating all above

All modules depend only on numpy + matplotlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.collections import LineCollection

# Re-use Phase 1 building blocks
from synthetic_market_simulator import (
    MarketSimulator,
    REGIMES,
    JumpParams,
    SpreadParams,
    FeeParams,
    LeverageParams,
    _REGIME_ORDER,
    _TRANSITION_MATRIX,
)


# ===========================================================================
# SECTION 1 · Phase 2 Configuration
# ===========================================================================

@dataclass
class Phase2Config:
    """Master feature-flag + parameter container for Phase 2."""

    # ── feature toggles ─────────────────────────────────────────────────────
    enable_garch_volatility:   bool = True
    enable_volume_model:       bool = True
    enable_slippage_model:     bool = True
    enable_correlated_assets:  bool = True
    enable_liquidation_cascade: bool = True

    # ── GARCH(1,1) params ───────────────────────────────────────────────────
    garch_alpha0: float = 1e-6      # long-run variance floor
    garch_alpha1: float = 0.10      # reaction to past shock^2
    garch_beta:   float = 0.85      # persistence of variance

    # ── Volume params ────────────────────────────────────────────────────────
    volume_base:        float = 1_000.0   # base units per candle
    volume_k:           float = 50.0      # sensitivity to |return|
    volume_jump_mult:   float = 5.0       # spike during jump
    volume_regime_mult: Dict[str, float] = field(default_factory=lambda: {
        "bull": 1.2, "bear": 1.3, "high_vol": 2.5, "low_vol": 0.5
    })

    # ── Slippage params ──────────────────────────────────────────────────────
    slippage_size_factor:    float = 0.0001   # per unit of order size $
    slippage_vol_multiplier: float = 10.0     # amplify slippage by realised vol

    # ── Correlation params ───────────────────────────────────────────────────
    # 2-asset correlation matrix (extensible)
    correlation_matrix: List[List[float]] = field(
        default_factory=lambda: [[1.0, 0.6], [0.6, 1.0]])
    n_assets: int = 2
    asset_names: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    # per-asset regime vol scalars (relative to BTC)
    asset_vol_scalars: List[float] = field(default_factory=lambda: [1.0, 1.4])

    # ── Liquidation cascade params ───────────────────────────────────────────
    cascade_threshold:    float = -0.05    # −5 % single-step return triggers cascade
    cascade_multiplier:   float = 1.5      # secondary shock = 1.5× primary shock
    cascade_oi_fraction:  float = 0.10     # 10 % of open interest wiped per cascade
    initial_open_interest: float = 10_000_000.0

    # ── Reproducibility ──────────────────────────────────────────────────────
    seed: Optional[int] = 42


# ===========================================================================
# SECTION 2 · Stress Test Config
# ===========================================================================

@dataclass
class StressTestConfig:
    """
    Parameterised friction overrides applied on top of simulation.

    All multipliers are relative (1.0 = no change).
    """
    spread_multiplier:   float = 1.0    # e.g. 2.0 = double spread
    vol_multiplier:      float = 1.0    # inflate all σ values
    latency_steps:       int   = 0      # order fill delayed by N steps
    regime_duration_std: float = 0.0    # add noise to regime stay-probability
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
# SECTION 3 · GARCH(1,1) Volatility Engine
# ===========================================================================

class GARCHVolatilityEngine:
    """
    GARCH(1,1) variance process.

        σ²_t = α₀ + α₁ · r²_{t-1} + β · σ²_{t-1}

    Maintains a rolling state so it can be called step-by-step.
    The resulting σ_t replaces the regime's base σ for that step.
    """

    def __init__(self, cfg: Phase2Config) -> None:
        self.alpha0 = cfg.garch_alpha0
        self.alpha1 = cfg.garch_alpha1
        self.beta   = cfg.garch_beta
        # Initialise variance at long-run mean: α₀ / (1 − α₁ − β)
        denom  = max(1 - self.alpha1 - self.beta, 1e-8)
        self.sigma2: float = self.alpha0 / denom
        self.history: List[float] = [math.sqrt(self.sigma2)]

    def update(self, last_return: float) -> float:
        """
        Feed the last log return, update σ² and return new σ.

        Parameters
        ----------
        last_return : r_{t-1}  (log-return, not percentage)

        Returns
        -------
        sigma_t : current conditional standard deviation
        """
        self.sigma2 = (
            self.alpha0
            + self.alpha1 * last_return ** 2
            + self.beta   * self.sigma2
        )
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
# SECTION 4 · Volume Simulation Engine
# ===========================================================================

class VolumeSimulationEngine:
    """
    Simulates realistic trading volume.

        V_t = base × regime_mult × (1 + k × |r_t|) × jump_boost × noise
    """

    def __init__(self, cfg: Phase2Config, rng: np.random.Generator) -> None:
        self.base          = cfg.volume_base
        self.k             = cfg.volume_k
        self.jump_mult     = cfg.volume_jump_mult
        self.regime_mult   = cfg.volume_regime_mult
        self.rng           = rng
        self.history: List[float] = []

    def step(self, price_return: float, regime: str, jumped: bool) -> float:
        """
        Compute volume for this step.

        Parameters
        ----------
        price_return : log-return for this step
        regime       : current regime name
        jumped       : True if a jump event fired this step

        Returns
        -------
        volume : simulated volume units
        """
        rm    = self.regime_mult.get(regime, 1.0)
        boost = self.jump_mult if jumped else 1.0
        noise = max(0.1, self.rng.normal(1.0, 0.20))
        vol   = self.base * rm * (1.0 + self.k * abs(price_return)) * boost * noise
        vol   = max(1.0, vol)
        self.history.append(vol)
        return vol


# ===========================================================================
# SECTION 5 · Dynamic Slippage Model
# ===========================================================================

class DynamicSlippageModel:
    """
    Computes execution price including slippage.

        exec_price = mid × (1 ± spread ± slippage)

    Slippage = vol × size_factor × order_size_usd
    Direction: +slippage for buys, −slippage for sells.
    """

    def __init__(self, cfg: Phase2Config) -> None:
        self.size_factor   = cfg.slippage_size_factor
        self.vol_mult      = cfg.slippage_vol_multiplier
        self.slippage_log: List[float] = []

    def compute(
        self,
        mid_price:    float,
        side:         str,          # 'buy' | 'sell'
        order_size:   float,        # in USD notional
        spread_frac:  float,        # half-spread fraction (e.g. 0.0005)
        sigma:        float,        # current σ from GARCH or regime
        jumped:       bool = False,
    ) -> float:
        """
        Return actual execution price accounting for spread + slippage.
        """
        slippage = self.vol_mult * sigma * self.size_factor * order_size
        if jumped:
            slippage *= 3.0           # triple slippage during jumps
        direction = +1.0 if side == "buy" else -1.0
        exec_price = mid_price * (1.0 + direction * (spread_frac + slippage))
        self.slippage_log.append(abs(exec_price - mid_price))
        return max(exec_price, 1e-6)

    def average_slippage_bps(self) -> float:
        """Mean slippage in basis points (of recent executions)."""
        if not self.slippage_log:
            return 0.0
        return float(np.mean(self.slippage_log) / 100 * 10_000)


# ===========================================================================
# SECTION 6 · Correlated Asset Engine
# ===========================================================================

class CorrelatedAssetEngine:
    """
    Generates correlated price shocks for multiple assets using Cholesky
    decomposition of a user-supplied correlation matrix.

    Workflow
    --------
    1.  Compute L = cholesky(corr_matrix)
    2.  Draw independent standard-normal vector z ~ N(0, I)
    3.  Correlated shocks  ε = L @ z
    4.  Scale each asset's shock by its regime σ

    Asset 0 is the primary asset whose prices feed the main chain.
    """

    def __init__(self, cfg: Phase2Config, rng: np.random.Generator) -> None:
        self.n            = cfg.n_assets
        self.names        = cfg.asset_names[:self.n]
        self.vol_scalars  = np.array(cfg.asset_vol_scalars[:self.n])
        corr              = np.array(cfg.correlation_matrix, dtype=float)
        # Ensure positive-definite
        corr = self._make_pd(corr)
        self.L            = np.linalg.cholesky(corr)
        self.rng          = rng
        self.cfg          = cfg

        # Price histories for all assets (index 0 = primary)
        self._prices: List[float] = []   # set by caller at init

    @staticmethod
    def _make_pd(mat: np.ndarray) -> np.ndarray:
        """Add small diagonal to guarantee positive-definiteness."""
        return mat + np.eye(len(mat)) * 1e-8

    def initialise_prices(self, primary_price: float,
                           secondary_ratio: float = 0.05) -> List[float]:
        """
        Set initial prices for all assets relative to primary.
        Returns list of initial prices.
        """
        prices = [primary_price * (secondary_ratio ** i) for i in range(self.n)]
        self._prices = prices
        return prices

    def correlated_shocks(self, sigma_primary: float) -> np.ndarray:
        """
        Draw a vector of correlated shocks.

        Returns
        -------
        shocks : array of shape (n,) containing per-asset return shocks
        """
        z       = self.rng.standard_normal(self.n)
        eps     = self.L @ z                         # correlated normals
        shocks  = eps * sigma_primary * self.vol_scalars
        return shocks

    def step(self, mu: float, sigma_primary: float,
             jumped: bool, jump_size: float) -> List[float]:
        """
        Advance all asset prices by one step.

        Returns
        -------
        new_prices : list of updated prices for each asset
        """
        shocks = self.correlated_shocks(sigma_primary)
        new_prices = []
        for i, p in enumerate(self._prices):
            r = mu + shocks[i]
            if jumped:
                # scale jump by vol-scalar so secondary assets mirror it
                r += jump_size * self.vol_scalars[i]
            p_new = max(p * (1.0 + r), 1e-6)
            new_prices.append(p_new)
        self._prices = new_prices
        return new_prices

    @property
    def prices(self) -> List[float]:
        return list(self._prices)

    def rolling_correlation(
        self,
        returns_a: np.ndarray,
        returns_b: np.ndarray,
        window: int = 60,
    ) -> np.ndarray:
        """Compute rolling Pearson correlation between two return series."""
        n   = len(returns_a)
        out = np.full(n, np.nan)
        for i in range(window, n):
            ra = returns_a[i - window:i]
            rb = returns_b[i - window:i]
            if ra.std() > 0 and rb.std() > 0:
                out[i] = float(np.corrcoef(ra, rb)[0, 1])
        return out


# ===========================================================================
# SECTION 7 · Liquidation Cascade Engine
# ===========================================================================

@dataclass
class CascadeEvent:
    step:           int
    trigger_return: float
    oi_wiped:       float
    cascade_shock:  float
    price_after:    float


class LiquidationCascadeEngine:
    """
    Tracks aggregate open interest (OI) and triggers forced-liquidation
    cascades when a large negative return occurs.

    Cascade logic:
      1. |return| > threshold  AND  return < 0
      2. Wipe `cascade_oi_fraction` of current OI
      3. Inject secondary negative price shock = cascade_multiplier * |return|
         (capped at 20 %)
      4. Log the event
    """

    def __init__(self, cfg: Phase2Config) -> None:
        self.threshold   = cfg.cascade_threshold        # e.g. −0.05
        self.multiplier  = cfg.cascade_multiplier       # e.g. 1.5
        self.oi_fraction = cfg.cascade_oi_fraction      # 10 %
        self.open_interest: float = cfg.initial_open_interest
        self.events:        List[CascadeEvent] = []
        self.oi_history:    List[float] = [self.open_interest]

    def step(self, price: float, step: int, price_return: float) -> Tuple[float, bool]:
        """
        Check if a cascade is triggered; return (modified_price, cascade_fired).

        Parameters
        ----------
        price        : mid-price after GBM + jump (before cascade)
        step         : current time step
        price_return : log-return for this step

        Returns
        -------
        (new_price, cascaded)
        """
        cascaded = False
        if price_return < self.threshold:
            oi_wiped       = self.open_interest * self.oi_fraction
            self.open_interest = max(0.0, self.open_interest - oi_wiped)
            # secondary shock (always negative, capped at 20 %)
            secondary = -min(abs(price_return) * self.multiplier, 0.20)
            new_price      = price * (1.0 + secondary)
            cascaded       = True
            evt = CascadeEvent(
                step           = step,
                trigger_return = price_return,
                oi_wiped       = oi_wiped,
                cascade_shock  = secondary,
                price_after    = new_price,
            )
            self.events.append(evt)
        else:
            new_price = price
            # OI slowly recovers between cascades
            self.open_interest = min(
                self.open_interest * 1.0001,
                self.open_interest * 1.10,
            )

        self.oi_history.append(self.open_interest)
        return new_price, cascaded


# ===========================================================================
# SECTION 8 · Risk Metrics Module
# ===========================================================================

class RiskMetrics:
    """
    Compute standard strategy-grade performance metrics from an equity curve
    and a list of closed trade PnL values.
    """

    @staticmethod
    def sharpe_ratio(
        returns:   np.ndarray,
        risk_free: float = 0.0,
        periods_per_year: int = 86_400,     # 1-second steps → annual
    ) -> float:
        """Annualised Sharpe ratio."""
        er  = returns - risk_free / periods_per_year
        std = er.std()
        if std == 0:
            return 0.0
        return float(er.mean() / std * math.sqrt(periods_per_year))

    @staticmethod
    def max_drawdown(equity_curve: np.ndarray) -> Tuple[float, int, int]:
        """
        Maximum drawdown and the indices of peak and trough.

        Returns
        -------
        (max_dd_fraction, peak_idx, trough_idx)
        """
        peak_idx   = 0
        max_dd     = 0.0
        trough_idx = 0
        running_peak = equity_curve[0]
        running_peak_idx = 0
        for i, v in enumerate(equity_curve):
            if v > running_peak:
                running_peak = v
                running_peak_idx = i
            dd = (running_peak - v) / max(running_peak, 1e-9)
            if dd > max_dd:
                max_dd     = dd
                peak_idx   = running_peak_idx
                trough_idx = i
        return float(max_dd), peak_idx, trough_idx

    @staticmethod
    def win_rate(trade_pnls: List[float]) -> float:
        """Fraction of winning trades (PnL > 0)."""
        if not trade_pnls:
            return 0.0
        wins = sum(1 for p in trade_pnls if p > 0)
        return wins / len(trade_pnls)

    @staticmethod
    def trade_expectancy(trade_pnls: List[float]) -> Tuple[float, float, float]:
        """
        Returns
        -------
        (expectancy, avg_win, avg_loss)
          expectancy  = win_rate × avg_win  −  loss_rate × |avg_loss|
        """
        if not trade_pnls:
            return 0.0, 0.0, 0.0
        wins   = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        avg_win  = float(np.mean(wins))  if wins   else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        wr       = len(wins) / len(trade_pnls)
        expect   = wr * avg_win - (1 - wr) * abs(avg_loss)
        return expect, avg_win, avg_loss

    @staticmethod
    def value_at_risk(returns: np.ndarray, confidence: float = 0.95) -> float:
        """
        Historical VaR at given confidence level.
        Returns the loss (positive number) not exceeded with `confidence` probability.
        """
        return float(-np.percentile(returns, (1 - confidence) * 100))

    @classmethod
    def full_report(
        cls,
        prices:       np.ndarray,
        trade_pnls:   List[float] = None,
        initial_equity: float     = 10_000.0,
        print_report: bool        = True,
    ) -> Dict:
        """
        Compute all metrics at once and optionally print a formatted report.

        Parameters
        ----------
        prices        : mid-price array (full simulation history)
        trade_pnls    : list of realised PnL per closed trade
        initial_equity: starting capital for equity curve construction

        Returns
        -------
        Dictionary with all metric values.
        """
        returns      = np.diff(np.log(prices + 1e-12))
        equity       = initial_equity + np.cumsum(returns * initial_equity)
        equity       = np.insert(equity, 0, initial_equity)
        trade_pnls   = trade_pnls or []

        sharpe         = cls.sharpe_ratio(returns)
        max_dd, pi, ti = cls.max_drawdown(equity)
        wr             = cls.win_rate(trade_pnls)
        expect, aw, al = cls.trade_expectancy(trade_pnls)
        var95          = cls.value_at_risk(returns, 0.95)
        total_return   = (prices[-1] - prices[0]) / prices[0]
        n_trades       = len(trade_pnls)

        result = {
            "total_return_pct":  round(total_return * 100, 3),
            "sharpe_ratio":      round(sharpe, 4),
            "max_drawdown_pct":  round(max_dd * 100, 3),
            "max_dd_peak_step":  pi,
            "max_dd_trough_step": ti,
            "win_rate_pct":      round(wr * 100, 2),
            "trade_expectancy":  round(expect, 6),
            "avg_win":           round(aw, 6),
            "avg_loss":          round(al, 6),
            "var_95_pct":        round(var95 * 100, 4),
            "n_trades":          n_trades,
        }

        if print_report:
            bar = "─" * 46
            print(f"\n{bar}")
            print("  RISK METRICS REPORT")
            print(bar)
            print(f"  Total return   : {result['total_return_pct']:>10.3f} %")
            print(f"  Sharpe ratio   : {result['sharpe_ratio']:>10.4f}")
            print(f"  Max drawdown   : {result['max_drawdown_pct']:>10.3f} %"
                  f"  (step {pi}→{ti})")
            print(f"  VaR 95 %       : {result['var_95_pct']:>10.4f} %")
            if n_trades:
                print(f"  Win rate       : {result['win_rate_pct']:>10.2f} %"
                      f"  ({n_trades} trades)")
                print(f"  Expectancy     : {result['trade_expectancy']:>10.6f}")
                print(f"  Avg win / loss : {result['avg_win']:>10.6f}"
                      f" / {result['avg_loss']:>10.6f}")
            print(bar + "\n")

        return result


# ===========================================================================
# SECTION 9 · Phase 2 Simulator – master class
# ===========================================================================

class Phase2MarketSimulator:
    """
    Wraps Phase 1 MarketSimulator and adds all Phase 2 engines.

    Feature flags in Phase2Config control which engines are active.
    All engines can run simultaneously or in isolation.

    Usage
    -----
    >>> cfg = Phase2Config(seed=42)
    >>> sim = Phase2MarketSimulator(initial_price=50_000, cfg=cfg)
    >>> sim.run(n_steps=5000)
    >>> sim.plot_all()
    >>> metrics = RiskMetrics.full_report(np.array(sim.prices))
    """

    def __init__(
        self,
        initial_price: float = 50_000.0,
        cfg:    Optional[Phase2Config]       = None,
        stress: Optional[StressTestConfig]   = None,
        jump_params:    Optional[JumpParams]  = None,
        spread_params:  Optional[SpreadParams]= None,
        fee_params:     Optional[FeeParams]   = None,
    ) -> None:

        self.cfg    = cfg    or Phase2Config()
        self.stress = stress or StressTestConfig()
        seed        = self.cfg.seed

        # ── Phase 1 core ─────────────────────────────────────────────────────
        self._p1 = MarketSimulator(
            initial_price   = initial_price,
            seed            = seed,
            initial_regime  = "bull",
            enable_regime   = True,
            enable_stochastic = True,
            enable_jumps    = True,
            enable_spread   = True,
            fee_params      = fee_params   or FeeParams(),
            jump_params     = jump_params  or JumpParams(),
            spread_params   = spread_params or SpreadParams(),
        )
        self._rng = self._p1.rng

        # ── Phase 2 engines ──────────────────────────────────────────────────
        c = self.cfg

        self.garch = GARCHVolatilityEngine(c) if c.enable_garch_volatility else None
        self.volume_engine = VolumeSimulationEngine(c, self._rng) if c.enable_volume_model else None
        self.slippage = DynamicSlippageModel(c) if c.enable_slippage_model else None

        self.corr_engine: Optional[CorrelatedAssetEngine] = None
        if c.enable_correlated_assets:
            self.corr_engine = CorrelatedAssetEngine(c, self._rng)
            self.corr_engine.initialise_prices(initial_price, secondary_ratio=0.05)

        self.cascade: Optional[LiquidationCascadeEngine] = None
        if c.enable_liquidation_cascade:
            self.cascade = LiquidationCascadeEngine(c)

        # ── State histories ──────────────────────────────────────────────────
        self.prices:     List[float] = [initial_price]
        self.returns:    List[float] = []
        self.sigmas:     List[float] = [self.garch.current_sigma if self.garch else
                                         REGIMES["bull"]["sigma"]]
        self.volumes:    List[float] = [c.volume_base]
        self.regimes:    List[str]   = ["bull"]
        self.jumps:      List[bool]  = [False]
        self.cascades:   List[bool]  = [False]

        # Correlated price histories: index 0 = primary, 1+ = secondaries
        self.corr_prices: List[List[float]] = (
            [[p] for p in self.corr_engine.prices] if self.corr_engine else []
        )

        self.t: int = 0

    # ── Core step ─────────────────────────────────────────────────────────────

    def step(self) -> float:
        """
        Advance simulation by one step integrating all Phase 2 engines.

        Order of operations
        -------------------
        1.  Regime switch (Phase 1 internal)
        2.  GARCH update  → σ_t
        3.  Stress-test σ override
        4.  GBM price update using σ_t
        5.  Jump event  (Phase 1 internal, but we capture jumped flag)
        6.  Liquidation cascade check
        7.  Correlated asset prices
        8.  Volume calculation
        9.  Record all histories

        Returns
        -------
        new mid-price for primary asset
        """
        self.t += 1

        prev_price = self._p1.price

        # ── 1. Let Phase 1 do regime switch + GBM + jump internally ──────────
        # We temporarily override σ by patching regime params if GARCH active
        if self.garch and len(self.returns) > 0:
            garch_sigma = self.garch.update(self.returns[-1])
            garch_sigma = self.stress.apply_sigma(garch_sigma)
            # Patch the current regime's sigma for this step only
            regime_name = self._p1.regime
            orig_sigma  = self._p1.regimes[regime_name]["sigma"]
            self._p1.regimes[regime_name]["sigma"] = garch_sigma
            new_price = self._p1.step()
            self._p1.regimes[regime_name]["sigma"] = orig_sigma  # restore
        else:
            new_price = self._p1.step()

        jumped  = self._p1.jump_history[-1] if self._p1.jump_history else False
        regime  = self._p1.regime
        ret     = (new_price - prev_price) / max(prev_price, 1e-9)

        # ── 5. Cascade check (modifies new_price) ────────────────────────────
        cascaded = False
        if self.cascade is not None:
            new_price, cascaded = self.cascade.step(new_price, self.t, ret)
            # If cascade fired, sync p1's price too
            self._p1.price = new_price
            # Recompute return after cascade
            ret = (new_price - prev_price) / max(prev_price, 1e-9)

        # ── 6. GARCH sigma retrieval (for output / slippage) ─────────────────
        if self.garch:
            sigma_t = self.garch.current_sigma
        else:
            sigma_t = REGIMES[regime]["sigma"]

        # ── 7. Correlated assets ─────────────────────────────────────────────
        if self.corr_engine is not None:
            mu    = REGIMES[regime]["mu"]
            jp    = self._p1.jump_params
            j_sz  = self._rng.normal(jp.mean, jp.std) if jumped else 0.0
            new_c_prices = self.corr_engine.step(mu, sigma_t, jumped, j_sz)
            for i, p in enumerate(new_c_prices):
                self.corr_prices[i].append(p)

        # ── 8. Volume ────────────────────────────────────────────────────────
        vol = 0.0
        if self.volume_engine is not None:
            vol = self.volume_engine.step(ret, regime, jumped)

        # ── 9. Record ─────────────────────────────────────────────────────────
        self.prices.append(new_price)
        self.returns.append(math.log(max(new_price / max(prev_price, 1e-9), 1e-9)))
        self.sigmas.append(sigma_t)
        self.volumes.append(vol if vol > 0 else self.cfg.volume_base)
        self.regimes.append(regime)
        self.jumps.append(jumped)
        self.cascades.append(cascaded)

        return new_price

    def run(self, n_steps: int) -> np.ndarray:
        """Run n_steps and return price array."""
        for _ in range(n_steps):
            self.step()
        return np.array(self.prices)

    # ── Slippage-aware execution ───────────────────────────────────────────────

    def execute_with_slippage(
        self,
        side:       str,
        order_size: float,
        order_type: str = "taker",
    ) -> Tuple[float, float]:
        """
        Execute a trade with dynamic slippage if enabled.

        Returns
        -------
        (exec_price, total_cost_usd)
        """
        mid    = self.prices[-1]
        sigma  = self.sigmas[-1]
        spread = self._p1.spread_params.base_spread
        jumped = self.jumps[-1]

        if self.slippage is not None:
            exec_price = self.slippage.compute(
                mid, side, order_size, spread, sigma, jumped)
        else:
            # Fall back to Phase 1 spread-only execution
            rec = self._p1.execute_trade(side, order_size / max(mid, 1e-9),
                                          order_type)
            exec_price = rec.exec_price

        fee_rate  = (self._p1.fee_params.taker_fee
                     if order_type == "taker"
                     else self._p1.fee_params.maker_fee)
        fee_paid  = exec_price * order_size * fee_rate
        total_cost = exec_price * order_size + fee_paid
        return exec_price, total_cost

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def price(self) -> float:
        return self.prices[-1]

    @property
    def regime(self) -> str:
        return self.regimes[-1]

    @property
    def n_cascade_events(self) -> int:
        return len(self.cascade.events) if self.cascade else 0


# ===========================================================================
# SECTION 10 · Plotting
# ===========================================================================

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


def plot_volatility(sim: Phase2MarketSimulator) -> plt.Figure:
    """Plot GARCH σ over time with regime colouring."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    t = np.arange(len(sim.prices))

    # Price
    prices = np.array(sim.prices)
    ax1.plot(t, prices, color="#26a69a", linewidth=0.6, label="Price")
    # Mark cascade events
    if sim.cascade:
        for ev in sim.cascade.events:
            ax1.axvline(ev.step, color="#f38720", alpha=0.4, linewidth=0.8)
    ax1.set_ylabel("Price", color=_TEXT)
    ax1.set_title("Price & GARCH Volatility", pad=6)
    ax1.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)

    # Sigma
    sigmas = np.array(sim.sigmas)
    ax2.plot(t, sigmas * 100, color="#c084fc", linewidth=0.6, label="σ_t (%)")
    lr = (sim.garch.long_run_sigma() * 100) if sim.garch else None
    if lr:
        ax2.axhline(lr, linestyle="--", color="#fb923c", linewidth=1,
                    label=f"Long-run σ = {lr:.3f}%")
    ax2.set_ylabel("Volatility (%)", color=_TEXT)
    ax2.set_xlabel("Step", color=_TEXT)
    ax2.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)

    _setup_dark(fig)
    fig.tight_layout()
    return fig


def plot_volume(sim: Phase2MarketSimulator) -> plt.Figure:
    """Plot price + volume, highlighting jump and cascade bars."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                                    gridspec_kw={"height_ratios": [2, 1]})
    t      = np.arange(len(sim.prices))
    prices = np.array(sim.prices)
    vols   = np.array(sim.volumes)

    ax1.plot(t, prices, color="#26a69a", linewidth=0.5)
    ax1.set_ylabel("Price", color=_TEXT)
    ax1.set_title("Price & Volume Dynamics", pad=6)

    # colour volume bars: red = sell, green = buy, orange = jump, purple = cascade
    colours = []
    for i, (j, c) in enumerate(zip(sim.jumps, sim.cascades)):
        if c:       colours.append("#f38720")
        elif j:     colours.append("#c084fc")
        elif i < len(sim.returns) and sim.returns[i] >= 0:
            colours.append("#26a69a80")
        else:       colours.append("#ef535080")

    ax2.bar(t, vols, color=colours, width=1.0, align="center")
    ax2.set_ylabel("Volume", color=_TEXT)
    ax2.set_xlabel("Step", color=_TEXT)

    _setup_dark(fig)
    fig.tight_layout()
    return fig


def plot_correlated_assets(sim: Phase2MarketSimulator) -> plt.Figure:
    """Plot all correlated asset prices + rolling correlation."""
    if not sim.corr_engine or not sim.corr_prices:
        print("Correlated assets disabled.")
        return None

    n   = sim.cfg.n_assets
    fig = plt.figure(figsize=(14, 9))
    gs  = gridspec.GridSpec(n + 1, 1, hspace=0.35)
    colours = ["#26a69a", "#60a5fa", "#f472b6", "#fb923c"]
    axes = []

    for i in range(n):
        ax = fig.add_subplot(gs[i], sharex=axes[0] if axes else None)
        t  = np.arange(len(sim.corr_prices[i]))
        ax.plot(t, sim.corr_prices[i], color=colours[i % len(colours)],
                linewidth=0.6, label=sim.cfg.asset_names[i])
        ax.set_ylabel("Price", color=_TEXT)
        ax.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)
        axes.append(ax)

    # Rolling correlation between asset 0 and 1
    ax_corr = fig.add_subplot(gs[n], sharex=axes[0])
    if n >= 2:
        r0 = np.diff(np.log(np.maximum(sim.corr_prices[0], 1e-9)))
        r1 = np.diff(np.log(np.maximum(sim.corr_prices[1], 1e-9)))
        roll = sim.corr_engine.rolling_correlation(r0, r1, window=60)
        t2   = np.arange(len(roll))
        ax_corr.plot(t2, roll, color="#f59e0b", linewidth=0.7,
                     label="60-step rolling corr")
        target = sim.cfg.correlation_matrix[0][1]
        ax_corr.axhline(target, linestyle="--", color="#ef4444",
                        linewidth=1, label=f"Target ρ = {target:.2f}")
        ax_corr.set_ylim(-1.1, 1.1)
        ax_corr.set_ylabel("Correlation", color=_TEXT)
        ax_corr.set_xlabel("Step", color=_TEXT)
        ax_corr.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)
    axes[0].set_title("Correlated Asset Prices + Rolling Correlation", pad=6)

    _setup_dark(fig)
    return fig


def plot_equity_curve(
    prices:         np.ndarray,
    initial_equity: float = 10_000.0,
    cascade_steps:  Optional[List[int]] = None,
) -> plt.Figure:
    """Plot synthetic equity curve derived from price returns."""
    returns = np.diff(np.log(np.maximum(prices, 1e-9)))
    equity  = initial_equity * np.exp(np.cumsum(returns))
    equity  = np.insert(equity, 0, initial_equity)
    t       = np.arange(len(equity))

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(t, equity, color="#26a69a", linewidth=0.7, label="Equity")
    # Drawdown shading
    running_max = np.maximum.accumulate(equity)
    ax.fill_between(t, equity, running_max, color="#ef5350", alpha=0.15,
                    label="Drawdown")
    if cascade_steps:
        for cs in cascade_steps:
            ax.axvline(cs, color="#f38720", alpha=0.5, linewidth=0.7)
    ax.axhline(initial_equity, linestyle="--", color="#787b86",
               linewidth=0.8, label=f"Equity start ${initial_equity:,.0f}")
    ax.set_title("Equity Curve", pad=6)
    ax.set_ylabel("Equity ($)", color=_TEXT)
    ax.set_xlabel("Step", color=_TEXT)
    ax.legend(fontsize=8, facecolor=_DARK, labelcolor=_TEXT)
    _setup_dark(fig)
    fig.tight_layout()
    return fig


def plot_summary(sim: Phase2MarketSimulator) -> plt.Figure:
    """5-panel summary: price, σ, volume, OI, equity."""
    prices = np.array(sim.prices)
    n_row  = 5
    fig, axes = plt.subplots(n_row, 1, figsize=(14, 16), sharex=True,
                              gridspec_kw={"height_ratios": [3, 2, 2, 2, 2]})
    t = np.arange(len(prices))

    # 1. Price
    axes[0].plot(t, prices, color="#26a69a", linewidth=0.5)
    if sim.cascade:
        for ev in sim.cascade.events:
            axes[0].axvline(ev.step, color="#f38720", alpha=0.35, linewidth=0.8)
    axes[0].set_ylabel("Price", color=_TEXT)
    axes[0].set_title(
        f"Phase 2 Simulation Summary  —  "
        f"GARCH={'✓' if sim.garch else '✕'}  "
        f"VOL={'✓' if sim.volume_engine else '✕'}  "
        f"SLIP={'✓' if sim.slippage else '✕'}  "
        f"CORR={'✓' if sim.corr_engine else '✕'}  "
        f"CASCADE={'✓' if sim.cascade else '✕'}",
        pad=6, fontsize=9,
    )

    # 2. Volatility
    if sim.garch:
        axes[1].plot(t, np.array(sim.sigmas) * 100, color="#c084fc", linewidth=0.5)
        lr = sim.garch.long_run_sigma() * 100
        axes[1].axhline(lr, linestyle="--", color="#fb923c", linewidth=1,
                        label=f"Long-run σ = {lr:.3f}%")
        axes[1].legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)
    axes[1].set_ylabel("σ (%)", color=_TEXT)

    # 3. Volume
    vols = np.array(sim.volumes)
    axes[2].bar(t, vols, color="#3b82f680", width=1.0)
    axes[2].set_ylabel("Volume", color=_TEXT)

    # 4. Open Interest
    if sim.cascade:
        oi = np.array(sim.cascade.oi_history[:len(t)])
        axes[3].plot(np.arange(len(oi)), oi / 1e6, color="#f59e0b", linewidth=0.6)
        axes[3].set_ylabel("OI (M)", color=_TEXT)
        for ev in sim.cascade.events:
            axes[3].axvline(ev.step, color="#ef5350", alpha=0.4, linewidth=0.8)
    else:
        axes[3].text(0.5, 0.5, "Cascade engine disabled",
                     ha="center", va="center", color="#787b86",
                     transform=axes[3].transAxes)

    # 5. Equity
    log_ret = np.diff(np.log(np.maximum(prices, 1e-9)))
    equity  = 10_000 * np.exp(np.cumsum(log_ret))
    equity  = np.insert(equity, 0, 10_000.0)
    axes[4].plot(np.arange(len(equity)), equity, color="#26a69a", linewidth=0.6)
    running_max = np.maximum.accumulate(equity)
    axes[4].fill_between(np.arange(len(equity)), equity, running_max,
                          color="#ef5350", alpha=0.15)
    axes[4].set_ylabel("Equity ($)", color=_TEXT)
    axes[4].set_xlabel("Step", color=_TEXT)

    _setup_dark(fig)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    return fig


# ===========================================================================
# SECTION 11 · Convenience factory + demo
# ===========================================================================

def build_phase2_simulator(
    initial_price: float = 50_000.0,
    n_assets: int = 2,
    seed: int = 42,
    enable_all: bool = True,
    stress: Optional[StressTestConfig] = None,
) -> Phase2MarketSimulator:
    """Return a ready-to-run Phase2MarketSimulator with sensible defaults."""
    cfg = Phase2Config(
        enable_garch_volatility    = enable_all,
        enable_volume_model        = enable_all,
        enable_slippage_model      = enable_all,
        enable_correlated_assets   = enable_all,
        enable_liquidation_cascade = enable_all,
        n_assets                   = n_assets,
        asset_names                = ["BTC", "ETH", "SOL", "BNB"][:n_assets],
        asset_vol_scalars          = [1.0, 1.4, 2.0, 1.2][:n_assets],
        correlation_matrix         = _default_corr(n_assets),
        seed                       = seed,
    )
    return Phase2MarketSimulator(initial_price=initial_price, cfg=cfg, stress=stress)


def _default_corr(n: int) -> List[List[float]]:
    """Build a plausible n×n correlation matrix for crypto assets."""
    base = 0.65
    mat  = [[1.0 if i == j else base * (0.9 ** abs(i - j))
             for j in range(n)] for i in range(n)]
    return mat


def run_demo(n_steps: int = 5_000, seed: int = 7) -> Phase2MarketSimulator:
    """
    Full Phase 2 demonstration.

    Runs 5000 steps with all engines enabled.
    Prints risk metrics and shows all plots.
    """
    print("=" * 60)
    print("  Synthetic Crypto Market Simulator — Phase 2 Demo")
    print("=" * 60)

    sim = build_phase2_simulator(initial_price=50_000.0, n_assets=2, seed=seed)
    sim.run(n_steps=n_steps)

    prices = np.array(sim.prices)

    print(f"\n  Steps run      : {n_steps:,}")
    print(f"  Start price    : ${prices[0]:,.2f}")
    print(f"  End price      : ${prices[-1]:,.2f}")
    print(f"  Cascade events : {sim.n_cascade_events}")
    print(f"  Jump events    : {sum(sim.jumps)}")
    print(f"  GARCH σ final  : {sim.sigmas[-1]*100:.4f}%")
    print(f"  Long-run σ     : {sim.garch.long_run_sigma()*100:.4f}%")

    # Validate GARCH clustering
    sigmas   = np.array(sim.sigmas)
    acf_lag1 = float(np.corrcoef(sigmas[:-1], sigmas[1:])[0, 1])
    print(f"\n  σ autocorrelation (lag-1): {acf_lag1:.4f}  "
          f"{'✓ clustering confirmed' if acf_lag1 > 0.3 else '⚠ low clustering'}")

    # Validate correlated assets
    if sim.corr_engine and len(sim.corr_prices) >= 2:
        r0 = np.diff(np.log(np.maximum(sim.corr_prices[0], 1e-9)))
        r1 = np.diff(np.log(np.maximum(sim.corr_prices[1], 1e-9)))
        actual_corr = float(np.corrcoef(r0, r1)[0, 1])
        target_corr = sim.cfg.correlation_matrix[0][1]
        print(f"\n  Asset correlation  —  "
              f"Target: {target_corr:.2f}  |  Actual: {actual_corr:.3f}  "
              f"{'✓' if abs(actual_corr - target_corr) < 0.15 else '⚠'}")

    # Validate slippage
    if sim.slippage and sim.slippage.slippage_log:
        avg_slip = np.mean(sim.slippage.slippage_log)
        print(f"  Avg slippage       : ${avg_slip:.4f}")

    # Risk metrics
    RiskMetrics.full_report(prices)

    # Plots
    fig1 = plot_summary(sim)
    fig2 = plot_volatility(sim)
    fig3 = plot_volume(sim)
    fig4 = plot_correlated_assets(sim)
    cascade_steps = [e.step for e in sim.cascade.events] if sim.cascade else []
    fig5 = plot_equity_curve(prices, cascade_steps=cascade_steps)

    plt.show()
    return sim


# ===========================================================================
# SECTION 12 · Entry point
# ===========================================================================

if __name__ == "__main__":
    run_demo(n_steps=5_000, seed=7)
