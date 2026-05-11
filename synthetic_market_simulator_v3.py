"""
synthetic_market_simulator_v3.py
=================================
Phase 3 – Agent-Based Adversarial Research Environment

Builds on Phase 1 (GBM, regime, jumps, spread, fees, leverage) and
Phase 2 (GARCH, volume, slippage, correlation, cascade, risk metrics).

New modules:
  1.  AgentEngine            – 5 heterogeneous agent types that generate orders
  2.  DynamicLiquidityEngine – volatility/jump-reactive liquidity pool
  3.  EmergentRegimeDetector – regime inferred from data, not random switching
  4.  AdversarialStressEngine– detects + punishes naïve alpha
  5.  LatencyModel           – stochastic execution delay + stale information
  6.  Phase3MarketSimulator  – master orchestrator

Success criteria:
  ✓ Market behaviour emerges from agent interaction
  ✓ Liquidity collapses during stress
  ✓ Strategy performance degrades under adversarial mode
  ✓ Simulator can destroy naïve alpha

Dependencies: numpy, matplotlib  (stdlib otherwise)
"""

from __future__ import annotations

import math
import abc
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ── Phase 1 + Phase 2 imports ────────────────────────────────────────────────
from synthetic_market_simulator import (
    MarketSimulator,
    REGIMES,
    JumpParams,
    SpreadParams,
    FeeParams,
    LeverageParams,
    _REGIME_ORDER,
)

from synthetic_market_simulator_v2 import (
    Phase2Config,
    StressTestConfig,
    GARCHVolatilityEngine,
    VolumeSimulationEngine,
    DynamicSlippageModel,
    CorrelatedAssetEngine,
    LiquidationCascadeEngine,
    RiskMetrics,
    Phase2MarketSimulator,
)


# ===========================================================================
# SECTION 1 · Phase 3 Configuration
# ===========================================================================

@dataclass
class AgentConfig:
    """Per-agent-type population & parameter configuration."""

    # ── Population counts ─────────────────────────────────────────────────
    n_momentum:          int = 8
    n_mean_reversion:    int = 6
    n_market_maker:      int = 4
    n_noise:             int = 15
    n_liq_hunter:        int = 3

    # ── Momentum agent ────────────────────────────────────────────────────
    mom_fast_window:     int   = 10     # fast EMA span
    mom_slow_window:     int   = 50     # slow EMA span
    mom_order_scale:     float = 5_000  # max order size $ at full signal
    mom_position_limit:  float = 50_000 # max notional per agent

    # ── Mean-reversion agent ──────────────────────────────────────────────
    mr_window:           int   = 60     # lookback for rolling mean/std
    mr_k:                float = 2.0    # z-score threshold for signal
    mr_order_scale:      float = 4_000
    mr_position_limit:   float = 40_000

    # ── Market-maker agent ────────────────────────────────────────────────
    mm_order_scale:      float = 10_000 # base notional per side
    mm_skew_factor:      float = 0.3    # how much inventory skews quotes
    mm_vol_retreat:       float = 2.0   # vol multiplier that halves quoting
    mm_position_limit:   float = 100_000

    # ── Noise agent ───────────────────────────────────────────────────────
    noise_order_scale:   float = 1_000  # max random order $
    noise_bias:          float = 0.0    # slight directional bias

    # ── Liquidation hunter agent ──────────────────────────────────────────
    lh_scan_range_pct:   float = 0.05   # scan ±5% around current price
    lh_push_scale:       float = 15_000 # order size when pushing
    lh_position_limit:   float = 80_000


@dataclass
class LiquidityConfig:
    """Dynamic liquidity pool parameters."""
    baseline:           float = 5_000_000    # baseline depth in $
    vol_sensitivity:    float = 3.0          # how fast liquidity drops with vol
    jump_impact:        float = 0.80         # fraction of liquidity that vanishes on jump
    recovery_rate:      float = 0.005        # per-step mean-reversion speed
    min_fraction:       float = 0.03         # floor = 3% of baseline
    cascade_impact:     float = 0.90         # 90% drop during cascade


@dataclass
class AdversarialConfig:
    """Adversarial stress engine parameters."""
    enabled:              bool  = True
    detection_window:     int   = 100       # steps to evaluate strategy profitability
    trap_probability:     float = 0.15      # chance of injecting a trap per detection
    false_breakout_size:  float = 0.008     # max size of false breakout (fraction)
    trend_extension_mult: float = 1.5       # how much to extend trends vs mean-rev
    friction_sensitivity: float = 2.0       # spread multiplier per unit profitability
    consensus_threshold:  float = 0.5       # fraction of agents agreeing triggers contrarian push
    contrarian_push_frac: float = 0.012     # max price push against consensus
    streak_break_length:  int   = 3         # break N consecutive same-direction steps
    streak_break_size:    float = 0.025     # push size against streak (must exceed sigma)
    mean_revert_noise:    float = 0.003     # persistent mean-reversion noise amplitude


@dataclass
class LatencyConfig:
    """Latency model parameters."""
    enabled:            bool  = True
    min_delay_steps:    int   = 0       # minimum execution delay
    max_delay_steps:    int   = 3       # maximum execution delay
    info_delay_steps:   int   = 1       # how stale agent observations are
    vol_delay_mult:     float = 2.0     # high vol → longer delays


@dataclass
class Phase3Config:
    """Master configuration for Phase 3."""

    # ── Sub-configs ───────────────────────────────────────────────────────
    agents:     AgentConfig      = field(default_factory=AgentConfig)
    liquidity:  LiquidityConfig  = field(default_factory=LiquidityConfig)
    adversarial: AdversarialConfig = field(default_factory=AdversarialConfig)
    latency:    LatencyConfig    = field(default_factory=LatencyConfig)

    # ── Phase 2 pass-through ─────────────────────────────────────────────
    p2:         Phase2Config     = field(default_factory=Phase2Config)

    # ── Global ────────────────────────────────────────────────────────────
    price_impact_coeff:  float = 1.0     # scales order-flow → price impact
    use_emergent_regime: bool  = True    # if False, keep Markov switching
    regime_detect_window: int  = 30      # lookback for regime inference

    seed: Optional[int] = 42


# ===========================================================================
# SECTION 2 · Order & OrderBook
# ===========================================================================

@dataclass
class AgentOrder:
    """A single order emitted by an agent."""
    agent_id:     str
    agent_type:   str
    side:         str       # 'buy' | 'sell'
    size_usd:     float     # notional $ amount
    delay_steps:  int = 0   # how many steps until this order hits the book
    step_created: int = 0


class OrderBook:
    """
    Simplified order-flow aggregator.

    Collects all agent orders each step, computes net imbalance,
    and translates that into a price impact proportional to 1/liquidity.
    """

    def __init__(self) -> None:
        self.pending: List[AgentOrder] = []
        self.history_net_flow:  List[float] = [0.0]
        self.history_buy_vol:   List[float] = [0.0]
        self.history_sell_vol:  List[float] = [0.0]
        self.history_n_orders:  List[int]   = [0]

    def submit(self, order: AgentOrder) -> None:
        """Add an order to the pending queue."""
        self.pending.append(order)

    def process(self, current_step: int, liquidity: float,
                price: float, impact_coeff: float) -> Tuple[float, List[AgentOrder]]:
        """
        Process all pending orders whose delay has expired.

        Returns
        -------
        (price_impact, filled_orders)
          price_impact : fractional price change from net order flow
          filled_orders: list of orders that were executed this step
        """
        ready   = [o for o in self.pending if current_step - o.step_created >= o.delay_steps]
        self.pending = [o for o in self.pending if current_step - o.step_created < o.delay_steps]

        buy_vol  = sum(o.size_usd for o in ready if o.side == 'buy')
        sell_vol = sum(o.size_usd for o in ready if o.side == 'sell')
        net_flow = buy_vol - sell_vol

        self.history_buy_vol.append(buy_vol)
        self.history_sell_vol.append(sell_vol)
        self.history_net_flow.append(net_flow)
        self.history_n_orders.append(len(ready))

        # Price impact: Δp/p = impact_coeff × net_flow / liquidity
        # When all agents align (~$100K flow), impact ≈ 2% of $5M liquidity
        effective_liq = max(liquidity, 1.0)
        impact = impact_coeff * net_flow / effective_liq

        # Clamp impact to ±2% per step to prevent death spirals
        return float(np.clip(impact, -0.02, 0.02)), ready

    @property
    def net_flow(self) -> float:
        return self.history_net_flow[-1] if self.history_net_flow else 0.0


# ===========================================================================
# SECTION 3 · Agent Engine – Base + 5 Agent Types
# ===========================================================================

class BaseAgent(abc.ABC):
    """
    Abstract agent that observes price history and generates orders.

    Each agent has:
      - Capital (USD balance)
      - Position (signed notional: +long, −short)
      - Realised PnL
      - Unique ID
    """

    _counter: int = 0

    def __init__(self, agent_type: str, initial_capital: float,
                 position_limit: float, rng: np.random.Generator) -> None:
        BaseAgent._counter += 1
        self.id              = f"{agent_type}_{BaseAgent._counter}"
        self.agent_type      = agent_type
        self.capital         = initial_capital
        self.position        = 0.0          # signed notional $
        self.position_limit  = position_limit
        self.rng             = rng
        self.pnl_history:    List[float] = [0.0]
        self.order_history:  List[float] = []   # signed order sizes
        self._realised_pnl   = 0.0
        self._entry_price    = 0.0

    @abc.abstractmethod
    def generate_order(self, price_history: np.ndarray,
                       current_price: float,
                       sigma: float, regime: str,
                       step: int) -> Optional[AgentOrder]:
        """
        Decide on an order for this step.

        Returns None if no order, or an AgentOrder.
        """
        ...

    def _can_order(self, size_usd: float) -> float:
        """Clamp order to position limit and available capital."""
        abs_size = min(abs(size_usd), self.capital * 0.5)
        # Enforce position limit
        if size_usd > 0:  # buy
            room = self.position_limit - self.position
            abs_size = min(abs_size, max(room, 0))
        else:              # sell
            room = self.position_limit + self.position
            abs_size = min(abs_size, max(room, 0))
        return abs_size

    def update_pnl(self, price: float, prev_price: float) -> None:
        """Mark-to-market PnL update."""
        if self.position != 0 and prev_price > 0:
            ret = (price - prev_price) / prev_price
            step_pnl = self.position * ret   # position is signed notional
            self.capital += step_pnl
            self._realised_pnl += step_pnl
        self.pnl_history.append(self._realised_pnl)

    def execute_fill(self, order: AgentOrder, exec_price: float) -> None:
        """Update position and entry after a fill."""
        signed = order.size_usd if order.side == 'buy' else -order.size_usd
        if self.position * signed < 0:
            # Closing: realise PnL from entry to exec
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


# ── Momentum Trader ───────────────────────────────────────────────────────────

class MomentumTrader(BaseAgent):
    """
    Follows trends using fast/slow EMA crossover.

    Signal = fast_ema − slow_ema,  normalised by price.
    Generates a buy when signal > 0, sell when signal < 0.
    Order size scales with signal magnitude.
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        # Slight parameter variation per instance
        fast = cfg.mom_fast_window + rng.integers(-3, 4)
        slow = cfg.mom_slow_window + rng.integers(-10, 11)
        self.fast_w = max(3, fast)
        self.slow_w = max(self.fast_w + 5, slow)
        self.order_scale = cfg.mom_order_scale * (0.6 + rng.random() * 0.8)
        super().__init__(
            agent_type="momentum",
            initial_capital=50_000 + rng.random() * 50_000,
            position_limit=cfg.mom_position_limit,
            rng=rng,
        )

    def generate_order(self, price_history: np.ndarray,
                       current_price: float, sigma: float,
                       regime: str, step: int) -> Optional[AgentOrder]:
        if len(price_history) < self.slow_w + 2:
            return None
        fast_ema = self._ema(price_history, self.fast_w)
        slow_ema = self._ema(price_history, self.slow_w)
        signal = (fast_ema - slow_ema) / max(current_price, 1e-9)
        # Scale and clamp
        raw_size = signal * self.order_scale * 100
        if abs(raw_size) < 50:
            return None
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 10:
            return None
        return AgentOrder(agent_id=self.id, agent_type=self.agent_type,
                          side=side, size_usd=size, step_created=step)

    @staticmethod
    def _ema(data: np.ndarray, span: int) -> float:
        """Compute EMA of last `span` data points, return final value."""
        alpha = 2.0 / (span + 1)
        ema = float(data[-span])
        for v in data[-span + 1:]:
            ema = alpha * float(v) + (1 - alpha) * ema
        return ema


# ── Mean-Reversion Trader ─────────────────────────────────────────────────────

class MeanReversionTrader(BaseAgent):
    """
    Trades toward the rolling mean using z-score.

    When price is k standard deviations above mean → sell.
    When price is k standard deviations below mean → buy.
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.window = cfg.mr_window + rng.integers(-10, 11)
        self.window = max(20, self.window)
        self.k = cfg.mr_k + rng.uniform(-0.5, 0.5)
        self.k = max(0.5, self.k)
        self.order_scale = cfg.mr_order_scale * (0.6 + rng.random() * 0.8)
        super().__init__(
            agent_type="mean_reversion",
            initial_capital=40_000 + rng.random() * 40_000,
            position_limit=cfg.mr_position_limit,
            rng=rng,
        )

    def generate_order(self, price_history: np.ndarray,
                       current_price: float, sigma: float,
                       regime: str, step: int) -> Optional[AgentOrder]:
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
        # Overbought → sell, oversold → buy
        signal = -z  # negative z-score = buy signal
        raw_size = (signal / self.k) * self.order_scale
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 10:
            return None
        return AgentOrder(agent_id=self.id, agent_type=self.agent_type,
                          side=side, size_usd=size, step_created=step)


# ── Market Maker ──────────────────────────────────────────────────────────────

class MarketMakerAgent(BaseAgent):
    """
    Provides two-sided liquidity, capturing the spread.

    Net order depends on inventory skew and volatility:
    - Neutral inventory → balanced bids/asks (net ≈ 0)
    - Long inventory → biased toward selling
    - High volatility → reduces quoting size (retreat)
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.order_scale  = cfg.mm_order_scale * (0.7 + rng.random() * 0.6)
        self.skew_factor  = cfg.mm_skew_factor
        self.vol_retreat  = cfg.mm_vol_retreat
        self._base_sigma: float = 0.0008
        super().__init__(
            agent_type="market_maker",
            initial_capital=200_000 + rng.random() * 100_000,
            position_limit=cfg.mm_position_limit,
            rng=rng,
        )

    def generate_order(self, price_history: np.ndarray,
                       current_price: float, sigma: float,
                       regime: str, step: int) -> Optional[AgentOrder]:
        # Volatility retreat: reduce size when vol is elevated
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        retreat = max(0.05, 1.0 / (1.0 + (vol_ratio - 1.0) * self.vol_retreat))

        base = self.order_scale * retreat

        # Inventory skew: pull toward flat
        inventory_frac = self.position / max(self.position_limit, 1.0)
        skew = -inventory_frac * self.skew_factor * base

        # Random noise component
        noise = self.rng.normal(0, base * 0.1)

        net_order = skew + noise  # positive = buy, negative = sell
        if abs(net_order) < 50:
            return None

        side = 'buy' if net_order > 0 else 'sell'
        size = self._can_order(net_order)
        if size < 10:
            return None
        return AgentOrder(agent_id=self.id, agent_type=self.agent_type,
                          side=side, size_usd=size, step_created=step)


# ── Noise Trader ──────────────────────────────────────────────────────────────

class NoiseTrader(BaseAgent):
    """
    Places random orders each step.  Provides background activity.
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.order_scale = cfg.noise_order_scale * (0.5 + rng.random())
        self.bias = cfg.noise_bias + rng.uniform(-0.1, 0.1)
        super().__init__(
            agent_type="noise",
            initial_capital=10_000 + rng.random() * 20_000,
            position_limit=50_000,
            rng=rng,
        )

    def generate_order(self, price_history: np.ndarray,
                       current_price: float, sigma: float,
                       regime: str, step: int) -> Optional[AgentOrder]:
        # Skip with 40% probability for realism
        if self.rng.random() < 0.4:
            return None
        raw = self.rng.normal(self.bias, 1.0) * self.order_scale
        if abs(raw) < 10:
            return None
        side = 'buy' if raw > 0 else 'sell'
        size = self._can_order(raw)
        if size < 5:
            return None
        return AgentOrder(agent_id=self.id, agent_type=self.agent_type,
                          side=side, size_usd=size, step_created=step)


# ── Liquidation Hunter ────────────────────────────────────────────────────────

class LiquidationHunter(BaseAgent):
    """
    Predatory trader that detects clusters of liquidation levels and
    pushes price toward them to trigger cascades.

    Maintains a model of where leveraged positions are likely liquidated
    based on recent price action + known leverage ratios.
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator,
                 variant: int = 0) -> None:
        self.scan_range = cfg.lh_scan_range_pct
        self.push_scale = cfg.lh_push_scale * (0.6 + rng.random() * 0.8)
        super().__init__(
            agent_type="liq_hunter",
            initial_capital=100_000 + rng.random() * 100_000,
            position_limit=cfg.lh_position_limit,
            rng=rng,
        )
        self._target_dir: float = 0.0
        self._cooldown: int = 0

    def generate_order(self, price_history: np.ndarray,
                       current_price: float, sigma: float,
                       regime: str, step: int) -> Optional[AgentOrder]:
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        if len(price_history) < 50:
            return None

        # Estimate liquidation cluster zones from recent volatility
        # Assume leveraged longs cluster at price * (1 - 1/leverage + buffer)
        # For 10× leverage, liq ≈ price * 0.905
        # For 20× leverage, liq ≈ price * 0.955
        recent_low  = float(np.min(price_history[-50:]))
        recent_high = float(np.max(price_history[-50:]))

        # Estimate density of liquidation levels below and above
        # (Variables kept as conceptual documentation of hunter logic)
        _liq_below = current_price * (1 - self.scan_range)
        _liq_above = current_price * (1 + self.scan_range)

        dist_to_low  = (current_price - recent_low) / current_price
        dist_to_high = (recent_high - current_price) / current_price

        # If we're close to recent low (many longs vulnerable below)
        # → push price down
        if dist_to_low < self.scan_range * 0.5 and dist_to_low > 0.005:
            self._target_dir = -1.0
        elif dist_to_high < self.scan_range * 0.5 and dist_to_high > 0.005:
            self._target_dir = 1.0
        else:
            # Slight randomised hunting
            self._target_dir = self.rng.choice([-1.0, 1.0])

        # Only push if momentum is already partial in that direction
        recent_ret = (current_price - float(price_history[-10])) / float(price_history[-10])
        alignment = recent_ret * self._target_dir
        if alignment < -0.001:
            # Going wrong way; wait
            return None

        raw_size = self._target_dir * self.push_scale
        side = 'buy' if raw_size > 0 else 'sell'
        size = self._can_order(raw_size)
        if size < 50:
            return None

        self._cooldown = self.rng.integers(3, 15)
        return AgentOrder(agent_id=self.id, agent_type=self.agent_type,
                          side=side, size_usd=size, step_created=step)


class AgentEngine:
    """
    Creates and manages all agent populations.
    """

    def __init__(self, cfg: AgentConfig, rng: np.random.Generator) -> None:
        self.agents: List[BaseAgent] = []
        self.cfg = cfg

        # Instantiate populations
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
        """Ask every agent for an order this step."""
        orders = []
        for agent in self.agents:
            order = agent.generate_order(
                price_history, current_price, sigma, regime, step)
            if order is not None:
                orders.append(order)
        return orders

    def update_all_pnl(self, price: float, prev_price: float) -> None:
        """Mark-to-market all agents."""
        for agent in self.agents:
            agent.update_pnl(price, prev_price)

    def get_consensus(self) -> Tuple[float, Dict[str, float]]:
        """
        Compute order consensus: fraction of agents that agree on direction.

        Returns
        -------
        (consensus_strength, type_biases)
          consensus_strength: float in [-1, 1],  +1 = all buy,  -1 = all sell
          type_biases: dict mapping agent_type → average signed order
        """
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
        type_biases = {t: type_sums[t] / max(type_counts[t], 1)
                       for t in type_sums}
        return consensus, type_biases

    def get_pnl_by_type(self) -> Dict[str, float]:
        """Average cumulative PnL per agent type."""
        sums:   Dict[str, float] = {}
        counts: Dict[str, int]   = {}
        for a in self.agents:
            t = a.agent_type
            sums[t]   = sums.get(t, 0) + a._realised_pnl
            counts[t] = counts.get(t, 0) + 1
        return {t: sums[t] / max(counts[t], 1) for t in sums}

    def get_agent_stats(self) -> Dict[str, Dict]:
        """Aggregate stats per agent type."""
        from collections import defaultdict
        stats = defaultdict(lambda: {"count": 0, "total_pnl": 0.0,
                                     "total_capital": 0.0, "total_position": 0.0})
        for a in self.agents:
            s = stats[a.agent_type]
            s["count"] += 1
            s["total_pnl"]      += a._realised_pnl
            s["total_capital"]   += a.capital
            s["total_position"]  += a.position
        # Compute averages
        result = {}
        for t, s in stats.items():
            n = s["count"]
            result[t] = {
                "count":        n,
                "avg_pnl":      s["total_pnl"] / n,
                "avg_capital":  s["total_capital"] / n,
                "avg_position": s["total_position"] / n,
            }
        return result


# ===========================================================================
# SECTION 4 · Dynamic Liquidity Engine
# ===========================================================================

class DynamicLiquidityEngine:
    """
    Models aggregate market liquidity that reacts to volatility, jumps,
    and cascades.

    Behaviour:
      - Liquidity decreases with elevated volatility
      - Liquidity crashes during jump events
      - Liquidity crashes harder during liquidation cascades
      - Liquidity slowly recovers toward baseline via mean-reversion
    """

    def __init__(self, cfg: LiquidityConfig) -> None:
        self.cfg = cfg
        self.current = cfg.baseline
        self.history: List[float] = [cfg.baseline]
        self._base_sigma = 0.0008   # reference "normal" vol

    def step(self, sigma: float, jumped: bool, cascaded: bool) -> float:
        """
        Update liquidity for this step.

        Parameters
        ----------
        sigma    : current vol from GARCH/regime
        jumped   : jump event this step
        cascaded : liquidation cascade this step

        Returns
        -------
        current liquidity depth ($)
        """
        cfg = self.cfg
        baseline = cfg.baseline

        # ── Volatility impact ────────────────────────────────────────────
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        vol_factor = max(cfg.min_fraction,
                         1.0 / (1.0 + cfg.vol_sensitivity * max(vol_ratio - 1.0, 0.0)))

        # ── Jump shock ───────────────────────────────────────────────────
        jump_factor = (1.0 - cfg.jump_impact) if jumped else 1.0

        # ── Cascade shock ────────────────────────────────────────────────
        casc_factor = (1.0 - cfg.cascade_impact) if cascaded else 1.0

        # ── Target liquidity ─────────────────────────────────────────────
        target = baseline * vol_factor * jump_factor * casc_factor

        # ── Mean-reversion toward target (or baseline if calm) ────────────
        # When stressed, snap toward lower target; when calm, slowly recover
        if target < self.current:
            # Fast drop
            self.current = target
        else:
            # Slow recovery
            self.current += cfg.recovery_rate * (baseline - self.current)

        # Floor
        self.current = max(self.current, baseline * cfg.min_fraction)
        self.history.append(self.current)
        return self.current

    @property
    def fraction(self) -> float:
        """Current liquidity as a fraction of baseline."""
        return self.current / max(self.cfg.baseline, 1.0)


# ===========================================================================
# SECTION 5 · Emergent Regime Detector
# ===========================================================================

class EmergentRegimeDetector:
    """
    Infers market regime from observable data — no random switching.

    Features used:
      1. Rolling realised volatility
      2. Net order flow direction
      3. Price momentum (simple return over window)

    Regime classification:
      - high_vol  : volatility > 80th percentile of history
      - low_vol   : volatility < 20th percentile
      - bull      : positive momentum + positive flow
      - bear      : negative momentum + negative flow
      - sideways  : mixed signals (maps to 'low_vol' for compatibility)
    """

    def __init__(self, window: int = 30) -> None:
        self.window         = max(10, window)
        self.vol_history:   List[float] = []
        self.flow_history:  List[float] = []
        self.regime_history: List[str]  = ["bull"]
        self._warmup_done   = False

    def detect(self, returns: List[float], net_flow: float,
               prices: List[float]) -> str:
        """
        Classify current regime.

        Parameters
        ----------
        returns  : all log-return history
        net_flow : latest net order flow
        prices   : all price history

        Returns
        -------
        regime : one of 'bull', 'bear', 'high_vol', 'low_vol'
        """
        self.flow_history.append(net_flow)

        if len(returns) < self.window:
            regime = self.regime_history[-1]
            self.regime_history.append(regime)
            return regime

        # ── Realised vol ─────────────────────────────────────────────────
        recent_returns = np.array(returns[-self.window:])
        vol = float(np.std(recent_returns))
        self.vol_history.append(vol)

        # ── Vol percentile (need history) ────────────────────────────────
        if len(self.vol_history) < self.window:
            vol_pct = 50.0
        else:
            arr = np.array(self.vol_history)
            vol_pct = float(np.searchsorted(np.sort(arr), vol) / len(arr) * 100)

        # ── Momentum ─────────────────────────────────────────────────────
        if len(prices) > self.window:
            momentum = (prices[-1] - prices[-self.window]) / max(prices[-self.window], 1e-9)
        else:
            momentum = 0.0

        # ── Net flow direction ───────────────────────────────────────────
        recent_flow = self.flow_history[-min(len(self.flow_history), self.window):]
        avg_flow = float(np.mean(recent_flow))

        # ── Classification ───────────────────────────────────────────────
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
            # Sideways → map to low_vol for Phase 1 compatibility
            regime = "low_vol"

        self.regime_history.append(regime)
        return regime


# ===========================================================================
# SECTION 6 · Adversarial Stress Engine
# ===========================================================================

class AdversarialStressEngine:
    """
    Detects profitable strategy patterns and actively works to
    degrade their alpha.

    Mechanisms:
      1. **False breakout injection** – momentarily pushes price to trigger
         momentum entries, then reverses
      2. **Trend extension** – when mean-reversion is profitable, extends
         the current trend to hurt those positions
      3. **Friction escalation** – widens spread and slippage when agents
         are collectively profitable
      4. **Consensus contrarian** – when most agents agree on direction,
         push price the opposite way

    All effects are injected as a fractional price perturbation that gets
    added to the GBM step.
    """

    def __init__(self, cfg: AdversarialConfig,
                 rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.rng = rng
        self.history_perturbation: List[float] = [0.0]
        self.history_friction:     List[float] = [1.0]   # spread multiplier
        self.trap_events:          List[Dict]  = []
        self._false_breakout_ttl:  int = 0
        self._breakout_reversal:   float = 0.0
        self._recent_returns:      List[float] = []  # for streak detection

    def step(self, agent_pnl_by_type: Dict[str, float],
             consensus: float,
             momentum_signal: float,
             mr_signal: float,
             current_price: float,
             step: int,
             last_return: float = 0.0) -> Tuple[float, float]:
        """
        Compute adversarial perturbation and friction multiplier.

        Parameters
        ----------
        agent_pnl_by_type : avg PnL per agent type
        consensus         : [-1, 1] order direction consensus
        momentum_signal   : avg momentum agent order (signed)
        mr_signal         : avg mean-reversion agent order (signed)
        current_price     : current mid-price
        step              : current time step

        Returns
        -------
        (price_perturbation, spread_multiplier)
          price_perturbation : additive fractional shock
          spread_multiplier  : multiplier for spread this step (≥ 1.0)
        """
        # Track recent returns for streak detection
        self._recent_returns.append(last_return)
        if len(self._recent_returns) > 50:
            self._recent_returns = self._recent_returns[-50:]

        if not self.cfg.enabled:
            self.history_perturbation.append(0.0)
            self.history_friction.append(1.0)
            return 0.0, 1.0

        perturbation = 0.0
        friction     = 1.0

        # ── 0. Streak-breaking (price-based pattern detection) ───────────
        n = self.cfg.streak_break_length
        if len(self._recent_returns) >= n:
            tail = self._recent_returns[-n:]
            streak_strength = sum(abs(r) for r in tail) / n
            if all(r > 0 for r in tail):
                # Break bullish streak — push proportional to streak strength
                push = max(self.cfg.streak_break_size, streak_strength * 1.5)
                perturbation -= push * self.rng.uniform(0.8, 1.2)
                self.trap_events.append({
                    "step": step, "type": "streak_break", "direction": -1,
                })
            elif all(r < 0 for r in tail):
                # Break bearish streak
                push = max(self.cfg.streak_break_size, streak_strength * 1.5)
                perturbation += push * self.rng.uniform(0.8, 1.2)
                self.trap_events.append({
                    "step": step, "type": "streak_break", "direction": 1,
                })

        # ── 0b. Persistent mean-reversion noise ─────────────────────────
        if len(self._recent_returns) >= 2 and last_return != 0:
            # Push against last return direction to create choppiness
            perturbation -= math.copysign(
                self.cfg.mean_revert_noise * self.rng.uniform(0.5, 1.5),
                last_return
            )

        # ── 1. False breakout ────────────────────────────────────────────
        if self._false_breakout_ttl > 0:
            # Reversal phase: push price back
            perturbation += self._breakout_reversal
            self._false_breakout_ttl -= 1
        else:
            # Check if momentum is profitable → consider creating a trap
            mom_pnl = agent_pnl_by_type.get("momentum", 0.0)
            if mom_pnl > 0 and self.rng.random() < self.cfg.trap_probability:
                # Create false breakout in the direction momentum expects
                direction = 1.0 if momentum_signal > 0 else -1.0
                breakout = direction * self.rng.uniform(0.001, self.cfg.false_breakout_size)
                perturbation += breakout
                # Schedule reversal for next 2-5 steps
                self._breakout_reversal = -breakout * 0.6
                self._false_breakout_ttl = self.rng.integers(2, 6)
                self.trap_events.append({
                    "step": step, "type": "false_breakout",
                    "direction": direction, "size": breakout,
                })

        # ── 2. Trend extension vs mean-reversion ────────────────────────
        mr_pnl = agent_pnl_by_type.get("mean_reversion", 0.0)
        if mr_pnl > 0 and abs(mr_signal) > 0:
            # Mean-rev is profiting → extend the trend to hurt it
            trend_dir = -1.0 if mr_signal > 0 else 1.0  # opposite of where MR is betting
            extension = trend_dir * self.rng.uniform(0, self.cfg.false_breakout_size
                                                     * self.cfg.trend_extension_mult)
            perturbation += extension * self.cfg.trap_probability

        # ── 3. Friction escalation ───────────────────────────────────────
        total_pnl = sum(agent_pnl_by_type.values())
        if total_pnl > 0:
            friction = 1.0 + min(total_pnl / 10_000 * self.cfg.friction_sensitivity, 5.0)
        else:
            friction = max(1.0 + total_pnl / 50_000, 0.5)  # reduce friction when losing

        # ── 4. Consensus contrarian ──────────────────────────────────────
        if abs(consensus) > self.cfg.consensus_threshold:
            # Push against the crowd
            push = -consensus * self.cfg.contrarian_push_frac * self.rng.uniform(0.5, 1.0)
            perturbation += push
            if self.rng.random() < 0.1:
                self.trap_events.append({
                    "step": step, "type": "consensus_contrarian",
                    "consensus": consensus, "push": push,
                })

        self.history_perturbation.append(perturbation)
        self.history_friction.append(friction)
        return perturbation, friction

    @property
    def n_traps(self) -> int:
        return len(self.trap_events)


# ===========================================================================
# SECTION 7 · Latency Model
# ===========================================================================

class LatencyModel:
    """
    Adds realistic execution and information delays.

    1. **Execution delay**: Orders don't fill instantly; delay ∈ [min, max]
       increases with volatility.
    2. **Information delay**: Agents observe price from N steps ago.
    3. **Vol-sensitive slippage amplification**: Stacks on top of Phase 2
       DynamicSlippageModel.
    """

    def __init__(self, cfg: LatencyConfig, rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.rng = rng
        self._base_sigma = 0.0008

    def execution_delay(self, sigma: float) -> int:
        """
        Compute stochastic execution delay for this step.

        Higher volatility → longer delays.
        """
        if not self.cfg.enabled:
            return 0
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        scaled_max = int(self.cfg.max_delay_steps *
                         min(vol_ratio * self.cfg.vol_delay_mult, 5.0))
        scaled_max = max(scaled_max, self.cfg.min_delay_steps)
        return int(self.rng.integers(self.cfg.min_delay_steps, scaled_max + 1))

    def delayed_price_history(self, price_history: np.ndarray) -> np.ndarray:
        """
        Return a stale price history for agent observation.

        Agents see prices from `info_delay_steps` steps ago.
        """
        if not self.cfg.enabled or self.cfg.info_delay_steps <= 0:
            return price_history
        d = self.cfg.info_delay_steps
        if len(price_history) <= d:
            return price_history
        return price_history[:-d]

    def slippage_multiplier(self, sigma: float) -> float:
        """Additional slippage factor from latency-induced stale quotes."""
        if not self.cfg.enabled:
            return 1.0
        vol_ratio = sigma / max(self._base_sigma, 1e-9)
        return 1.0 + 0.2 * max(vol_ratio - 1.0, 0.0)


# ===========================================================================
# SECTION 8 · Phase 3 Market Simulator – Master Orchestrator
# ===========================================================================

class Phase3MarketSimulator:
    """
    Agent-based adversarial market simulator.

    Step sequence:
      1.  Phase 1 regime switch (or emergent detection)
      2.  GARCH volatility update
      3.  GBM base price shock
      4.  Jump event
      5.  Agent observation (with latency)
      6.  Agent order generation
      7.  Order book processing → price impact
      8.  Adversarial perturbation
      9.  Liquidation cascade check
      10. Dynamic liquidity update
      11. Correlated asset prices
      12. Volume calculation
      13. Emergent regime detection
      14. PnL update for all agents
      15. Record all state

    Price formation:
      new_price = old_price × (1 + mu + gbm_shock + agent_impact + adversarial_shock)
    """

    def __init__(
        self,
        initial_price: float = 50_000.0,
        cfg:    Optional[Phase3Config]       = None,
        stress: Optional[StressTestConfig]   = None,
    ) -> None:
        self.cfg    = cfg    or Phase3Config()
        self.stress = stress or StressTestConfig()

        seed = self.cfg.seed
        self.rng = np.random.default_rng(seed)

        c = self.cfg

        # ── Phase 1 core (used for GBM / jumps / spread internals) ────────
        p2_cfg = c.p2
        p2_cfg.seed = seed

        self._p1 = MarketSimulator(
            initial_price    = initial_price,
            seed             = seed,
            initial_regime   = "bull",
            enable_regime    = not c.use_emergent_regime,  # disable Markov if emergent
            enable_stochastic = True,
            enable_jumps     = True,
            enable_spread    = True,
            fee_params       = FeeParams(),
            jump_params      = JumpParams(),
            spread_params    = SpreadParams(),
        )

        # ── Phase 2 engines ──────────────────────────────────────────────
        # Override cascade threshold for agent-based environment
        p2_cfg.cascade_threshold = -0.08  # less sensitive than -0.05
        self.garch   = GARCHVolatilityEngine(p2_cfg) if p2_cfg.enable_garch_volatility else None
        self.vol_eng = VolumeSimulationEngine(p2_cfg, self.rng) if p2_cfg.enable_volume_model else None
        self.slippage = DynamicSlippageModel(p2_cfg) if p2_cfg.enable_slippage_model else None
        self.cascade  = LiquidationCascadeEngine(p2_cfg) if p2_cfg.enable_liquidation_cascade else None
        self.corr_engine: Optional[CorrelatedAssetEngine] = None
        if p2_cfg.enable_correlated_assets:
            self.corr_engine = CorrelatedAssetEngine(p2_cfg, self.rng)
            self.corr_engine.initialise_prices(initial_price, secondary_ratio=0.05)

        # ── Phase 3 engines ──────────────────────────────────────────────
        self.agent_engine    = AgentEngine(c.agents, self.rng)
        self.order_book      = OrderBook()
        self.liquidity_eng   = DynamicLiquidityEngine(c.liquidity)
        self.regime_detector = EmergentRegimeDetector(c.regime_detect_window) if c.use_emergent_regime else None
        self.adversarial     = AdversarialStressEngine(c.adversarial, self.rng)
        self.latency_model   = LatencyModel(c.latency, self.rng)

        # Agent lookup for fast fill routing
        self._agent_map: Dict[str, BaseAgent] = {
            a.id: a for a in self.agent_engine.agents
        }

        # Cascade cooldown tracker
        self._cascade_cooldown: int = 0
        self._cascade_cooldown_period: int = 20  # min steps between cascades

        # ── State histories ──────────────────────────────────────────────
        self.prices:     List[float] = [initial_price]
        self.returns:    List[float] = []
        self.sigmas:     List[float] = [self.garch.current_sigma if self.garch
                                         else REGIMES["bull"]["sigma"]]
        self.volumes:    List[float] = [p2_cfg.volume_base]
        self.regimes:    List[str]   = ["bull"]
        self.jumps:      List[bool]  = [False]
        self.cascades:   List[bool]  = [False]
        self.liquidity:  List[float] = [c.liquidity.baseline]
        self.agent_impacts: List[float] = [0.0]
        self.adversarial_shocks: List[float] = [0.0]
        self.friction_history: List[float] = [1.0]

        # Correlated price histories
        self.corr_prices: List[List[float]] = (
            [[p] for p in self.corr_engine.prices] if self.corr_engine else []
        )

        self.t: int = 0

    # ── Core step ─────────────────────────────────────────────────────────

    def step(self) -> float:
        """
        Advance simulation by one step.

        Returns the new mid-price.
        """
        self.t += 1
        prev_price = self.prices[-1]
        c = self.cfg

        # ── 1. Regime ─────────────────────────────────────────────────────
        if not c.use_emergent_regime:
            # Markov switching via Phase 1
            self._p1._switch_regime()
            regime = self._p1.regime
        else:
            regime = self.regimes[-1]  # will be updated at end of step

        # ── 2. GARCH volatility ──────────────────────────────────────────
        if self.garch and self.returns:
            sigma = self.garch.update(self.returns[-1])
            sigma = self.stress.apply_sigma(sigma)
        else:
            sigma = REGIMES[regime]["sigma"]

        # ── 3. GBM shock ─────────────────────────────────────────────────
        mu = REGIMES[regime]["mu"]
        gbm_shock = self.rng.normal(0.0, sigma)

        # ── 4. Jump event ─────────────────────────────────────────────────
        jumped = False
        jump_size = 0.0
        jp = self._p1.jump_params
        if self.rng.random() < jp.probability:
            jump_size = self.rng.normal(jp.mean, jp.std)
            jumped = True

        # ── 5. Agent observation (with latency) ──────────────────────────
        price_arr = np.array(self.prices)
        obs_prices = self.latency_model.delayed_price_history(price_arr)

        # ── 6. Agent order generation ─────────────────────────────────────
        orders = self.agent_engine.generate_all_orders(
            obs_prices, prev_price, sigma, regime, self.t)

        # Apply execution delay
        for order in orders:
            delay = self.latency_model.execution_delay(sigma)
            order.delay_steps = delay
            self.order_book.submit(order)

        # ── 7. Order book → price impact ──────────────────────────────────
        current_liq = self.liquidity_eng.current
        agent_impact, filled_orders = self.order_book.process(
            self.t, current_liq, prev_price, c.price_impact_coeff)

        # ── 7b. Execute fills on agents ───────────────────────────────────
        for order in filled_orders:
            agent = self._agent_map.get(order.agent_id)
            if agent is not None:
                agent.execute_fill(order, prev_price)

        # ── 8. Adversarial perturbation ───────────────────────────────────
        pnl_by_type = self.agent_engine.get_pnl_by_type()
        consensus, type_biases = self.agent_engine.get_consensus()
        mom_signal = type_biases.get("momentum", 0.0)
        mr_signal  = type_biases.get("mean_reversion", 0.0)

        adv_shock, friction = self.adversarial.step(
            pnl_by_type, consensus, mom_signal, mr_signal, prev_price, self.t,
            last_return=self.returns[-1] if self.returns else 0.0)

        # ── Combine all price effects ─────────────────────────────────────
        combined_return = mu + gbm_shock + agent_impact + adv_shock
        if jumped:
            combined_return += jump_size

        # Clamp combined return to ±3% to prevent death spirals
        combined_return = float(np.clip(combined_return, -0.03, 0.03))

        new_price = prev_price * (1.0 + combined_return)
        new_price = max(new_price, jp.min_price)

        # ── Apply friction to spread ──────────────────────────────────────
        if self.stress.enabled:
            friction *= self.stress.spread_multiplier

        # ── 9. Liquidation cascade (with cooldown) ────────────────────────
        cascaded = False
        ret = (new_price - prev_price) / max(prev_price, 1e-9)
        if self._cascade_cooldown > 0:
            self._cascade_cooldown -= 1
        if self.cascade is not None and self._cascade_cooldown == 0:
            new_price, cascaded = self.cascade.step(new_price, self.t, ret)
            if cascaded:
                self._cascade_cooldown = self._cascade_cooldown_period
            ret = (new_price - prev_price) / max(prev_price, 1e-9)

        # ── 10. Dynamic liquidity ─────────────────────────────────────────
        liq = self.liquidity_eng.step(sigma, jumped, cascaded)

        # ── 11. Correlated assets ─────────────────────────────────────────
        if self.corr_engine is not None:
            j_sz = jump_size if jumped else 0.0
            new_c_prices = self.corr_engine.step(mu, sigma, jumped, j_sz)
            for i, p in enumerate(new_c_prices):
                self.corr_prices[i].append(p)

        # ── 12. Volume ────────────────────────────────────────────────────
        vol = 0.0
        if self.vol_eng is not None:
            vol = self.vol_eng.step(ret, regime, jumped)

        # ── 13. Emergent regime detection ─────────────────────────────────
        if self.regime_detector is not None:
            net_flow = self.order_book.net_flow
            regime = self.regime_detector.detect(
                self.returns + [math.log(max(new_price / max(prev_price, 1e-9), 1e-9))],
                net_flow,
                self.prices + [new_price],
            )

        # ── 14. Agent PnL update ─────────────────────────────────────────
        self.agent_engine.update_all_pnl(new_price, prev_price)

        # ── 15. Record ────────────────────────────────────────────────────
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

        # Sync p1's price for any downstream usage
        self._p1.price = new_price

        return new_price

    def run(self, n_steps: int) -> np.ndarray:
        """Run simulation for n_steps."""
        for _ in range(n_steps):
            self.step()
        return np.array(self.prices)

    # ── Properties ────────────────────────────────────────────────────────

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
        return self.adversarial.n_traps

    @property
    def current_liquidity_pct(self) -> float:
        return self.liquidity_eng.fraction * 100


# ===========================================================================
# SECTION 9 · Validation Framework
# ===========================================================================

class Phase3Validator:
    """
    Validates that Phase 3 meets the four success criteria:
      1. Market behaviour emerges from agents
      2. Liquidity collapses during stress
      3. Strategy performance degrades under adversarial mode
      4. Simulator can destroy naïve alpha
    """

    def __init__(self, sim: Phase3MarketSimulator) -> None:
        self.sim = sim

    def validate_emergent_behaviour(self) -> Dict:
        """
        Check that agent order flow correlates with price changes,
        confirming price emerges from agent interaction.
        """
        impacts = np.array(self.sim.agent_impacts[1:])
        returns = np.array(self.sim.returns)
        n = min(len(impacts), len(returns))
        impacts, returns = impacts[:n], returns[:n]

        if n < 50:
            return {"status": "insufficient_data", "n": n}

        corr = float(np.corrcoef(impacts, returns)[0, 1])
        # Agent impact should explain some variance in returns
        r_squared = corr ** 2

        return {
            "agent_return_correlation": round(corr, 4),
            "r_squared": round(r_squared, 4),
            "emergent": bool(abs(corr) > 0.1),
            "status": "✓ emergent" if abs(corr) > 0.1 else "✗ not emergent",
        }

    def validate_liquidity_collapse(self) -> Dict:
        """
        Verify that liquidity drops significantly during stress events
        (jumps and cascades).
        """
        liq = np.array(self.sim.liquidity)
        baseline = self.sim.cfg.liquidity.baseline

        # Find minimum liquidity and when it occurred
        min_liq = float(np.min(liq))
        min_idx = int(np.argmin(liq))
        min_frac = min_liq / max(baseline, 1.0)

        # Compute avg liquidity during cascade steps
        cascade_liq = [liq[i] for i in range(len(liq))
                       if i < len(self.sim.cascades) and self.sim.cascades[i]]
        avg_cascade_liq = float(np.mean(cascade_liq)) if cascade_liq else baseline
        avg_normal_liq  = float(np.mean(liq))

        collapsed = min_frac < 0.30  # dropped below 30% of baseline

        return {
            "min_liquidity":   round(min_liq, 0),
            "min_fraction_pct": round(min_frac * 100, 2),
            "min_step":         min_idx,
            "avg_cascade_liq":  round(avg_cascade_liq, 0),
            "avg_normal_liq":   round(avg_normal_liq, 0),
            "collapsed":        bool(collapsed),
            "status":           "✓ collapses" if collapsed else "✗ no collapse",
        }

    def validate_adversarial_degradation(self) -> Dict:
        """
        Check that strategy profitability is degraded by adversarial mode.

        We compare per-type PnL to detect if alpha-seeking agents
        (momentum, mean-reversion) are underperforming.
        """
        stats = self.sim.agent_engine.get_agent_stats()
        adv_traps = self.sim.n_adversarial_traps

        mom_pnl = stats.get("momentum", {}).get("avg_pnl", 0)
        mr_pnl  = stats.get("mean_reversion", {}).get("avg_pnl", 0)
        mm_pnl  = stats.get("market_maker", {}).get("avg_pnl", 0)
        noise_pnl = stats.get("noise", {}).get("avg_pnl", 0)
        lh_pnl  = stats.get("liq_hunter", {}).get("avg_pnl", 0)

        # Adversarial is working if alpha-seekers are negative or near zero
        alpha_degraded = (mom_pnl <= 0) or (mr_pnl <= 0)

        return {
            "momentum_avg_pnl":       round(mom_pnl, 2),
            "mean_reversion_avg_pnl": round(mr_pnl, 2),
            "market_maker_avg_pnl":   round(mm_pnl, 2),
            "noise_avg_pnl":          round(noise_pnl, 2),
            "liq_hunter_avg_pnl":     round(lh_pnl, 2),
            "adversarial_traps":      adv_traps,
            "alpha_degraded":         bool(alpha_degraded),
            "status": "✓ alpha degraded" if alpha_degraded else "✗ alpha survives",
        }

    def validate_naive_alpha_destruction(self) -> Dict:
        """
        Test a naïve momentum strategy against the simulated data
        and verify it fails (negative Sharpe or negative total return).
        """
        prices = np.array(self.sim.prices)
        if len(prices) < 200:
            return {"status": "insufficient_data"}

        # Simple momentum strategy: buy after N consecutive up-steps, sell after N down
        n_consec = 5
        position = 0.0  # 0 = flat, 1 = long, -1 = short
        equity = 10_000.0
        equity_curve = [equity]
        returns_arr = np.diff(prices) / prices[:-1]

        for i in range(n_consec, len(returns_arr)):
            recent = returns_arr[i - n_consec:i]
            if all(r > 0 for r in recent):
                position = 1.0
            elif all(r < 0 for r in recent):
                position = -1.0
            else:
                position = 0.0
            step_pnl = position * returns_arr[i] * equity
            equity += step_pnl
            equity_curve.append(equity)

        eq = np.array(equity_curve)
        total_return = (eq[-1] - eq[0]) / eq[0]
        log_rets = np.diff(np.log(np.maximum(eq, 1e-9)))
        sharpe = float(np.mean(log_rets) / max(np.std(log_rets), 1e-12)
                       * math.sqrt(86400))

        destroyed = sharpe < 0.5 or total_return < 0.01

        return {
            "naive_total_return_pct": round(total_return * 100, 3),
            "naive_sharpe":           round(sharpe, 4),
            "final_equity":           round(eq[-1], 2),
            "destroyed":              bool(destroyed),
            "status": "✓ naïve alpha destroyed" if destroyed
                      else "✗ naïve alpha survived",
        }

    def full_validation(self, print_report: bool = True) -> Dict:
        """Run all four validation checks."""
        r1 = self.validate_emergent_behaviour()
        r2 = self.validate_liquidity_collapse()
        r3 = self.validate_adversarial_degradation()
        r4 = self.validate_naive_alpha_destruction()

        results = {
            "emergent_behaviour":    r1,
            "liquidity_collapse":    r2,
            "adversarial_degradation": r3,
            "naive_alpha_destruction": r4,
        }

        passed = sum(1 for r in [r1, r2, r3, r4]
                     if r.get("emergent") or r.get("collapsed")
                     or r.get("alpha_degraded") or r.get("destroyed"))

        results["total_passed"] = passed
        results["total_tests"]  = 4

        if print_report:
            bar = "═" * 58
            print(f"\n{bar}")
            print("  PHASE 3 VALIDATION REPORT")
            print(bar)

            print(f"\n  1. Emergent Behaviour")
            print(f"     Agent↔Return correlation : {r1.get('agent_return_correlation', 'N/A')}")
            print(f"     R² explained variance    : {r1.get('r_squared', 'N/A')}")
            print(f"     Result: {r1.get('status', 'N/A')}")

            print(f"\n  2. Liquidity Collapse")
            print(f"     Min liquidity fraction   : {r2.get('min_fraction_pct', 'N/A')}%")
            print(f"     Min at step              : {r2.get('min_step', 'N/A')}")
            print(f"     Avg cascade liquidity    : ${r2.get('avg_cascade_liq', 'N/A'):,.0f}")
            print(f"     Result: {r2.get('status', 'N/A')}")

            print(f"\n  3. Adversarial Degradation")
            print(f"     Momentum avg PnL         : ${r3.get('momentum_avg_pnl', 'N/A'):,.2f}")
            print(f"     Mean-Rev avg PnL         : ${r3.get('mean_reversion_avg_pnl', 'N/A'):,.2f}")
            print(f"     Market-Maker avg PnL     : ${r3.get('market_maker_avg_pnl', 'N/A'):,.2f}")
            print(f"     Noise avg PnL            : ${r3.get('noise_avg_pnl', 'N/A'):,.2f}")
            print(f"     Liq-Hunter avg PnL       : ${r3.get('liq_hunter_avg_pnl', 'N/A'):,.2f}")
            print(f"     Adversarial traps fired  : {r3.get('adversarial_traps', 0)}")
            print(f"     Result: {r3.get('status', 'N/A')}")

            print(f"\n  4. Naïve Alpha Destruction")
            print(f"     Naïve momentum return    : {r4.get('naive_total_return_pct', 'N/A')}%")
            print(f"     Naïve Sharpe ratio       : {r4.get('naive_sharpe', 'N/A')}")
            print(f"     Result: {r4.get('status', 'N/A')}")

            print(f"\n  {bar}")
            print(f"  PASSED {passed} / 4 criteria")
            print(f"  {bar}\n")

        return results


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


def plot_phase3_summary(sim: Phase3MarketSimulator) -> plt.Figure:
    """
    8-panel Phase 3 summary:
      1. Price + cascade markers
      2. Emergent regime shading
      3. GARCH volatility
      4. Liquidity depth
      5. Agent net order flow
      6. Adversarial perturbation
      7. Volume
      8. Agent PnL by type
    """
    prices = np.array(sim.prices)
    n = len(prices)
    t = np.arange(n)

    fig, axes = plt.subplots(8, 1, figsize=(16, 26), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1, 2, 2, 2, 2, 2, 2]})

    # ── 1. Price ──────────────────────────────────────────────────────────
    ax = axes[0]
    ax.plot(t, prices, color="#26a69a", linewidth=0.5)
    if sim.cascade:
        for ev in sim.cascade.events:
            ax.axvline(ev.step, color="#f38720", alpha=0.4, linewidth=0.8)
    # Jump markers
    for i, j in enumerate(sim.jumps):
        if j and i < n:
            ax.plot(i, prices[i], 'v', color="#c084fc", markersize=3, alpha=0.7)
    ax.set_ylabel("Price", color=_TEXT)
    engines_str = (
        f"Agents={sim.agent_engine.n_agents}  "
        f"Traps={sim.n_adversarial_traps}  "
        f"Cascades={sim.n_cascade_events}  "
        f"Jumps={sum(sim.jumps)}"
    )
    ax.set_title(f"Phase 3 Agent-Based Simulator  —  {engines_str}", pad=8, fontsize=10)

    # ── 2. Regime ─────────────────────────────────────────────────────────
    ax = axes[1]
    regime_colors = {"bull": "#26a69a", "bear": "#ef5350",
                     "high_vol": "#f38720", "low_vol": "#3b82f6"}
    for i in range(1, len(sim.regimes)):
        c = regime_colors.get(sim.regimes[i], "#787b86")
        ax.axvspan(i - 1, i, facecolor=c, alpha=0.4, linewidth=0)
    ax.set_ylabel("Regime", color=_TEXT)
    ax.set_yticks([])
    # Legend patches
    from matplotlib.patches import Patch
    patches = [Patch(facecolor=c, label=r, alpha=0.6)
               for r, c in regime_colors.items()]
    ax.legend(handles=patches, fontsize=7, loc="upper right",
              facecolor=_DARK, labelcolor=_TEXT, ncol=4)

    # ── 3. Volatility ────────────────────────────────────────────────────
    ax = axes[2]
    sigmas = np.array(sim.sigmas[:n])
    ax.plot(t[:len(sigmas)], sigmas * 100, color="#c084fc", linewidth=0.5)
    if sim.garch:
        lr = sim.garch.long_run_sigma() * 100
        ax.axhline(lr, linestyle="--", color="#fb923c", linewidth=1,
                    label=f"Long-run σ = {lr:.3f}%")
        ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)
    ax.set_ylabel("σ (%)", color=_TEXT)

    # ── 4. Liquidity ─────────────────────────────────────────────────────
    ax = axes[3]
    liq = np.array(sim.liquidity[:n])
    baseline = sim.cfg.liquidity.baseline
    ax.fill_between(t[:len(liq)], 0, liq / 1e6, color="#3b82f6", alpha=0.3)
    ax.plot(t[:len(liq)], liq / 1e6, color="#3b82f6", linewidth=0.6)
    ax.axhline(baseline / 1e6, linestyle="--", color="#787b86",
               linewidth=0.8, label=f"Baseline {baseline/1e6:.1f}M")
    ax.set_ylabel("Liquidity ($M)", color=_TEXT)
    ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT)

    # ── 5. Net order flow ────────────────────────────────────────────────
    ax = axes[4]
    flow = np.array(sim.order_book.history_net_flow[:n])
    colours = ['#26a69a' if v >= 0 else '#ef5350' for v in flow]
    ax.bar(t[:len(flow)], flow, color=colours, width=1.0)
    ax.set_ylabel("Net Flow ($)", color=_TEXT)

    # ── 6. Adversarial perturbation ──────────────────────────────────────
    ax = axes[5]
    adv = np.array(sim.adversarial_shocks[:n])
    ax.plot(t[:len(adv)], adv * 100, color="#ef5350", linewidth=0.5, alpha=0.8)
    ax.axhline(0, color="#787b86", linewidth=0.5)
    # Mark trap events
    for trap in sim.adversarial.trap_events:
        s = trap["step"]
        if s < n:
            ax.axvline(s, color="#f38720", alpha=0.3, linewidth=0.7)
    ax.set_ylabel("Adv shock (%)", color=_TEXT)

    # ── 7. Volume ────────────────────────────────────────────────────────
    ax = axes[6]
    vols = np.array(sim.volumes[:n])
    ax.bar(t[:len(vols)], vols, color="#3b82f680", width=1.0)
    ax.set_ylabel("Volume", color=_TEXT)

    # ── 8. Agent PnL by type ─────────────────────────────────────────────
    ax = axes[7]
    type_colors = {"momentum": "#f59e0b", "mean_reversion": "#60a5fa",
                   "market_maker": "#34d399", "noise": "#787b86",
                   "liq_hunter": "#ef5350"}
    # Aggregate PnL history per type
    type_pnl_series: Dict[str, List[float]] = {}
    for agent in sim.agent_engine.agents:
        t_name = agent.agent_type
        if t_name not in type_pnl_series:
            type_pnl_series[t_name] = np.zeros(len(agent.pnl_history))
        pnl_arr = np.array(agent.pnl_history)
        n_min = min(len(type_pnl_series[t_name]), len(pnl_arr))
        type_pnl_series[t_name][:n_min] += pnl_arr[:n_min]

    for t_name, pnl_arr in type_pnl_series.items():
        # average per agent
        count = sum(1 for a in sim.agent_engine.agents if a.agent_type == t_name)
        ax.plot(np.arange(len(pnl_arr)), pnl_arr / max(count, 1),
                color=type_colors.get(t_name, "#787b86"),
                linewidth=0.8, label=t_name)
    ax.axhline(0, color="#787b86", linewidth=0.5)
    ax.set_ylabel("Avg PnL ($)", color=_TEXT)
    ax.set_xlabel("Step", color=_TEXT)
    ax.legend(fontsize=7, facecolor=_DARK, labelcolor=_TEXT, ncol=5)

    _setup_dark(fig)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    return fig


def plot_liquidity_stress(sim: Phase3MarketSimulator) -> plt.Figure:
    """Focus plot: liquidity vs price during cascade/jump events."""
    prices = np.array(sim.prices)
    liq    = np.array(sim.liquidity)
    n = min(len(prices), len(liq))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    t = np.arange(n)

    ax1.plot(t, prices[:n], color="#26a69a", linewidth=0.5)
    ax1.set_ylabel("Price", color=_TEXT)
    ax1.set_title("Liquidity Stress Analysis", pad=6)

    ax2.fill_between(t, 0, liq[:n] / 1e6, color="#3b82f6", alpha=0.25)
    ax2.plot(t, liq[:n] / 1e6, color="#3b82f6", linewidth=0.6)

    # Highlight stress periods (liquidity < 50% baseline)
    thresh = sim.cfg.liquidity.baseline * 0.5
    stress_mask = liq[:n] < thresh
    if np.any(stress_mask):
        ax2.fill_between(t, 0, liq[:n] / 1e6,
                         where=stress_mask, color="#ef5350", alpha=0.3)
    ax2.axhline(sim.cfg.liquidity.baseline / 1e6, linestyle="--",
                color="#787b86", linewidth=0.8)
    ax2.set_ylabel("Liquidity ($M)", color=_TEXT)
    ax2.set_xlabel("Step", color=_TEXT)

    _setup_dark(fig)
    fig.tight_layout()
    return fig


def plot_agent_performance(sim: Phase3MarketSimulator) -> plt.Figure:
    """Bar chart of final PnL per agent, grouped by type."""
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
                f"${val:,.0f}", ha="center", va="bottom" if val >= 0 else "top",
                color=_TEXT, fontsize=9)
    ax.axhline(0, color="#787b86", linewidth=0.8)
    ax.set_ylabel("Avg PnL ($)", color=_TEXT)
    ax.set_title("Agent Performance by Type", pad=6)

    _setup_dark(fig)
    fig.tight_layout()
    return fig


# ===========================================================================
# SECTION 11 · Convenience Factory + Demo
# ===========================================================================

def build_phase3_simulator(
    initial_price: float = 50_000.0,
    seed: int = 42,
    adversarial: bool = True,
    emergent_regime: bool = True,
    stress: Optional[StressTestConfig] = None,
) -> Phase3MarketSimulator:
    """Create a ready-to-run Phase 3 simulator with sensible defaults."""
    p2_cfg = Phase2Config(
        enable_garch_volatility    = True,
        enable_volume_model        = True,
        enable_slippage_model      = True,
        enable_correlated_assets   = True,
        enable_liquidation_cascade = True,
        n_assets      = 2,
        asset_names   = ["BTC", "ETH"],
        asset_vol_scalars = [1.0, 1.4],
        correlation_matrix = [[1.0, 0.6], [0.6, 1.0]],
        seed = seed,
    )
    cfg = Phase3Config(
        agents     = AgentConfig(),
        liquidity  = LiquidityConfig(),
        adversarial = AdversarialConfig(enabled=adversarial),
        latency    = LatencyConfig(enabled=True),
        p2         = p2_cfg,
        use_emergent_regime = emergent_regime,
        seed       = seed,
    )
    return Phase3MarketSimulator(initial_price=initial_price, cfg=cfg, stress=stress)


def run_demo(n_steps: int = 5_000, seed: int = 42) -> Phase3MarketSimulator:
    """
    Full Phase 3 demonstration.

    Runs simulation, prints all stats, validates all criteria, shows plots.
    """
    print("=" * 60)
    print("  Synthetic Crypto Market Simulator — Phase 3 Demo")
    print("  Agent-Based Adversarial Research Environment")
    print("=" * 60)

    sim = build_phase3_simulator(initial_price=50_000.0, seed=seed,
                                  adversarial=True, emergent_regime=True)
    print(f"\n  Agents created: {sim.agent_engine.n_agents}")
    for t, s in sim.agent_engine.get_agent_stats().items():
        print(f"    {t:20s} × {s['count']}")

    print(f"\n  Running {n_steps:,} steps...")
    sim.run(n_steps=n_steps)

    prices = np.array(sim.prices)
    print(f"\n  Steps run      : {n_steps:,}")
    print(f"  Start price    : ${prices[0]:,.2f}")
    print(f"  End price      : ${prices[-1]:,.2f}")
    print(f"  Cascade events : {sim.n_cascade_events}")
    print(f"  Jump events    : {sum(sim.jumps)}")
    print(f"  Adv traps      : {sim.n_adversarial_traps}")
    print(f"  GARCH σ final  : {sim.sigmas[-1]*100:.4f}%")
    print(f"  Liquidity now  : {sim.current_liquidity_pct:.1f}% of baseline")

    # Regime distribution
    from collections import Counter
    rc = Counter(sim.regimes)
    total = len(sim.regimes)
    print(f"\n  Regime distribution (emergent):")
    for r in ["bull", "bear", "high_vol", "low_vol"]:
        pct = rc.get(r, 0) / total * 100
        print(f"    {r:10s} : {pct:5.1f}%")

    # Agent performance
    print(f"\n  Agent performance:")
    for t_name, stats in sim.agent_engine.get_agent_stats().items():
        print(f"    {t_name:20s} : avg PnL ${stats['avg_pnl']:>10,.2f}"
              f"   avg capital ${stats['avg_capital']:>10,.2f}"
              f"   avg pos ${stats['avg_position']:>10,.0f}")

    # Risk metrics
    RiskMetrics.full_report(prices)

    # Phase 3 validation
    validator = Phase3Validator(sim)
    validator.full_validation(print_report=True)

    # Plots
    print("  Generating plots...")
    _ = plot_phase3_summary(sim)
    _ = plot_liquidity_stress(sim)
    _ = plot_agent_performance(sim)

    plt.show()
    return sim


def run_comparison(n_steps: int = 3_000, seed: int = 42) -> None:
    """
    Run the same simulation with adversarial ON vs OFF to demonstrate
    alpha destruction.
    """
    print("=" * 60)
    print("  ADVERSARIAL COMPARISON TEST")
    print("=" * 60)

    # ── Run WITHOUT adversarial ──────────────────────────────────────────
    print("\n  [1/2] Running WITHOUT adversarial engine...")
    sim_clean = build_phase3_simulator(
        initial_price=50_000.0, seed=seed, adversarial=False)
    sim_clean.run(n_steps)

    # ── Run WITH adversarial ─────────────────────────────────────────────
    print("  [2/2] Running WITH adversarial engine...")
    sim_adv = build_phase3_simulator(
        initial_price=50_000.0, seed=seed, adversarial=True)
    sim_adv.run(n_steps)

    # ── Compare ──────────────────────────────────────────────────────────
    bar = "─" * 58
    print(f"\n{bar}")
    print(f"  {'Agent Type':<22} {'PnL (clean)':>12} {'PnL (adv)':>12} {'Δ':>10}")
    print(bar)

    stats_clean = sim_clean.agent_engine.get_agent_stats()
    stats_adv   = sim_adv.agent_engine.get_agent_stats()

    for t_name in ["momentum", "mean_reversion", "market_maker", "noise", "liq_hunter"]:
        pnl_c = stats_clean.get(t_name, {}).get("avg_pnl", 0)
        pnl_a = stats_adv.get(t_name, {}).get("avg_pnl", 0)
        delta  = pnl_a - pnl_c
        print(f"  {t_name:<22} ${pnl_c:>10,.2f}  ${pnl_a:>10,.2f}  ${delta:>8,.2f}")

    print(bar)
    print(f"  Adversarial traps: {sim_adv.n_adversarial_traps}")
    print("  ✓ Comparison complete\n")


# ===========================================================================
# SECTION 12 · Entry Point
# ===========================================================================

if __name__ == "__main__":
    run_demo(n_steps=5_000, seed=42)
