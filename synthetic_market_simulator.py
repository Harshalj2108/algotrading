"""
synthetic_market_simulator.py
==============================
Modular Synthetic Crypto Market Simulator

Development Roadmap (each layer is independently functional):
  Step 1 – Minimal Market Class        : flat price + time stepping
  Step 2 – Regime Switching            : Markov-style bull/bear/high_vol/low_vol
  Step 3 – Stochastic Price (GBM)      : multiplicative drift + Gaussian shock
  Step 4 – Jump Events                 : rare flash-crash / pump spikes
  Step 5 – Spread Simulation           : bid/ask spread widens in high-vol
  Step 6 – Fee Model                   : maker / taker fees → net PnL
  Step 7 – Leverage & Liquidation      : margin balance, forced liquidation

Dependencies: numpy, matplotlib (stdlib only otherwise)
"""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.collections import LineCollection
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 · Regime Configuration
# ──────────────────────────────────────────────────────────────────────────────

# 1 step = 1 simulated second
# σ calibrated so 1-second candles show visible movement (≈ 0.08 %/s normal vol)
REGIMES: Dict[str, Dict[str, float]] = {
    "bull":     {"mu":  0.000002, "sigma": 0.0008},   # uptrend,   normal vol
    "bear":     {"mu": -0.000002, "sigma": 0.0008},   # downtrend, normal vol
    "high_vol": {"mu":  0.000000, "sigma": 0.0030},   # spike vol  (3×)
    "low_vol":  {"mu":  0.000000, "sigma": 0.0002},   # quiet market
}

# Markov transition matrix  (rows = from, cols = to)
# Order: bull, bear, high_vol, low_vol
_REGIME_ORDER = ["bull", "bear", "high_vol", "low_vol"]

# Each row sums to 1.  Staying probability is ~99 %; switch budget ~1 %.
_TRANSITION_MATRIX: np.ndarray = np.array([
    #  bull   bear  h_vol  l_vol
    [0.970, 0.010, 0.010, 0.010],   # from bull
    [0.010, 0.970, 0.010, 0.010],   # from bear
    [0.010, 0.010, 0.970, 0.010],   # from high_vol
    [0.010, 0.010, 0.010, 0.970],   # from low_vol
])


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 · Jump Parameters
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class JumpParams:
    """Parameters governing rare price discontinuities (Step 4)."""
    probability: float = 0.0003         # 0.03 %/s  → ~26 jumps/simulated-day
    mean:        float = 0.0            # jump centred at zero (symmetric)
    std:         float = 0.015          # ~1.5 % standard jump size
    min_price:   float = 0.1            # hard floor; price never drops below this


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 · Spread Parameters
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SpreadParams:
    """Bid-ask spread parameters (Step 5)."""
    base_spread:         float = 0.0005  # 0.05 % of mid-price in normal regimes
    high_vol_multiplier: float = 3.0     # spread ×3 in high_vol regime


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 · Fee Parameters
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FeeParams:
    """Maker / taker fee model (Step 6)."""
    maker_fee: float = 0.0002   # 0.02 %
    taker_fee: float = 0.0005   # 0.05 %


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5 · Leverage / Liquidation Parameters
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LeverageParams:
    """Leverage and margin parameters (Step 7)."""
    leverage:            float = 10.0   # e.g., 10× leverage
    maintenance_margin:  float = 0.005  # 0.5 % of notional → liquidation level


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 6 · Liquidation Event Record
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LiquidationEvent:
    step:             int
    price_at_liq:     float
    margin_balance:   float
    side:             str    # "long" or "short"


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 7 · Trade Record
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    step:         int
    side:         str          # "buy" or "sell"
    qty:          float
    exec_price:   float        # includes spread
    fee_paid:     float
    order_type:   str          # "maker" or "taker"
    net_pnl:      float = 0.0  # filled in on close


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 8 · Core MarketSimulator
# ──────────────────────────────────────────────────────────────────────────────

class MarketSimulator:
    """
    Synthetic crypto market simulator.

    Layers are all active simultaneously; individual features can be toggled
    with the boolean flags passed to __init__.

    Parameters
    ----------
    initial_price   : Starting mid-price.
    seed            : RNG seed for reproducibility.
    initial_regime  : One of 'bull', 'bear', 'high_vol', 'low_vol'.
    enable_regime   : Toggle Step 2 (regime switching).
    enable_stochastic: Toggle Step 3 (GBM shocks).
    enable_jumps    : Toggle Step 4 (jump events).
    enable_spread   : Toggle Step 5 (bid-ask spread).
    fee_params      : FeeParams dataclass (Step 6); None = no fees.
    leverage_params : LeverageParams dataclass (Step 7); None = no leverage.
    jump_params     : JumpParams dataclass; None = defaults used.
    spread_params   : SpreadParams dataclass; None = defaults used.
    """

    def __init__(
        self,
        initial_price:    float         = 100.0,
        seed:             Optional[int] = 42,
        initial_regime:   str           = "bull",
        # feature flags
        enable_regime:      bool = True,
        enable_stochastic:  bool = True,
        enable_jumps:       bool = True,
        enable_spread:      bool = True,
        # parameter objects
        fee_params:      Optional[FeeParams]      = None,
        leverage_params: Optional[LeverageParams] = None,
        jump_params:     Optional[JumpParams]     = None,
        spread_params:   Optional[SpreadParams]   = None,
    ) -> None:

        # ── RNG ──────────────────────────────────────────────────────────────
        self.rng = np.random.default_rng(seed)

        # ── Step 1 · Core state ──────────────────────────────────────────────
        self.price: float = float(initial_price)
        self.price_history:    List[float] = [self.price]
        self.regime_history:   List[str]   = []
        self.spread_history:   List[float] = []
        self.jump_history:     List[bool]  = []      # True if a jump occurred
        self.t: int = 0

        # ── Step 2 · Regime ───────────────────────────────────────────────────
        assert initial_regime in REGIMES, f"Unknown regime '{initial_regime}'"
        self.regime: str = initial_regime
        self.regimes      = REGIMES
        self.enable_regime = enable_regime

        # ── Step 3 · Stochastic ───────────────────────────────────────────────
        self.enable_stochastic = enable_stochastic

        # ── Step 4 · Jumps ────────────────────────────────────────────────────
        self.enable_jumps = enable_jumps
        self.jump_params  = jump_params or JumpParams()

        # ── Step 5 · Spread ───────────────────────────────────────────────────
        self.enable_spread = enable_spread
        self.spread_params = spread_params or SpreadParams()

        # ── Step 6 · Fees ─────────────────────────────────────────────────────
        self.fee_params  = fee_params     # None → fees disabled
        self.trade_log:   List[TradeRecord] = []

        # ── Step 7 · Leverage / Liquidation ──────────────────────────────────
        self.leverage_params      = leverage_params   # None → leverage disabled
        self.margin_balance:       Optional[float] = None
        self.open_position_qty:    float = 0.0
        self.open_position_side:   Optional[str]  = None
        self.open_position_entry:  Optional[float] = None
        self.liquidation_events:   List[LiquidationEvent] = []

        # Record initial regime
        if self.enable_regime:
            self.regime_history.append(self.regime)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_regime_params(self) -> Tuple[float, float]:
        """Return (mu, sigma) for the current regime."""
        params = self.regimes[self.regime]
        return params["mu"], params["sigma"]

    def _switch_regime(self) -> None:
        """Markov transition step."""
        idx = _REGIME_ORDER.index(self.regime)
        probs = _TRANSITION_MATRIX[idx]
        new_idx = self.rng.choice(len(_REGIME_ORDER), p=probs)
        self.regime = _REGIME_ORDER[new_idx]

    def _compute_spread(self) -> float:
        """Return the current half-spread (as a fraction of price)."""
        sp = self.spread_params
        multiplier = sp.high_vol_multiplier if self.regime == "high_vol" else 1.0
        return sp.base_spread * multiplier

    # ── Step 1 · Core step ───────────────────────────────────────────────────

    def step(self) -> float:
        """
        Advance simulation by one time unit.

        Returns the new mid-price after all effects are applied.
        Sequence of operations:
          1. Regime switch (Step 2)
          2. Stochastic price update – GBM (Step 3)
          3. Jump event (Step 4)
          4. Price floor enforcement
          5. Record spread (Step 5)
          6. Leverage / liquidation check (Step 7)
        """
        self.t += 1

        # ── 2 · Regime switch ─────────────────────────────────────────────────
        if self.enable_regime:
            self._switch_regime()
            self.regime_history.append(self.regime)

        # ── 3 · GBM price update ─────────────────────────────────────────────
        if self.enable_stochastic:
            mu, sigma = self._get_regime_params()
            shock = self.rng.normal(0.0, sigma)
            self.price *= (1.0 + mu + shock)
        # (if stochastic disabled price stays flat → Step 1 behaviour)

        # ── 4 · Jump event ───────────────────────────────────────────────────
        jumped = False
        if self.enable_jumps:
            jp = self.jump_params
            if self.rng.random() < jp.probability:
                jump_size = self.rng.normal(jp.mean, jp.std)
                self.price *= (1.0 + jump_size)
                jumped = True

        # ── Price floor ───────────────────────────────────────────────────────
        self.price = max(self.price, self.jump_params.min_price)

        # Record
        self.jump_history.append(jumped)
        self.price_history.append(self.price)

        # ── 5 · Spread ────────────────────────────────────────────────────────
        half_spread = self._compute_spread() if self.enable_spread else 0.0
        self.spread_history.append(half_spread * 2 * self.price)  # absolute spread

        # ── 7 · Leverage check ───────────────────────────────────────────────
        if self.leverage_params is not None and self.open_position_qty != 0.0:
            self._check_liquidation()

        return self.price

    def run(self, n_steps: int) -> np.ndarray:
        """Run simulation for *n_steps* steps and return price array."""
        for _ in range(n_steps):
            self.step()
        return np.array(self.price_history)

    # ── Step 5 · Spread helpers ──────────────────────────────────────────────

    def get_bid_ask(self) -> Tuple[float, float]:
        """Return (bid_price, ask_price) based on current spread."""
        hs = self._compute_spread()
        bid = self.price * (1.0 - hs)
        ask = self.price * (1.0 + hs)
        return bid, ask

    # ── Step 6 · Trade execution with fees ───────────────────────────────────

    def execute_trade(
        self,
        side:       str,
        qty:        float,
        order_type: str = "taker",
    ) -> TradeRecord:
        """
        Simulate a trade with spread and fee deduction.

        Parameters
        ----------
        side       : 'buy' or 'sell'
        qty        : number of units
        order_type : 'maker' or 'taker'

        Returns
        -------
        TradeRecord with exec_price and fee_paid filled in.
        """
        bid, ask = self.get_bid_ask()
        exec_price = ask if side == "buy" else bid

        if self.fee_params is not None:
            fee_rate = (
                self.fee_params.taker_fee
                if order_type == "taker"
                else self.fee_params.maker_fee
            )
        else:
            fee_rate = 0.0

        fee_paid = exec_price * qty * fee_rate

        record = TradeRecord(
            step=self.t,
            side=side,
            qty=qty,
            exec_price=exec_price,
            fee_paid=fee_paid,
            order_type=order_type,
        )
        self.trade_log.append(record)
        return record

    def compute_net_pnl(
        self,
        buy_record:  TradeRecord,
        sell_record: TradeRecord,
    ) -> float:
        """
        Calculate net PnL for a round-trip trade after fees.

        Returns gross_pnl - total_fees.
        """
        gross = (sell_record.exec_price - buy_record.exec_price) * buy_record.qty
        fees  = buy_record.fee_paid + sell_record.fee_paid
        net   = gross - fees
        sell_record.net_pnl = net
        return net

    # ── Step 7 · Leverage / Margin / Liquidation ─────────────────────────────

    def open_leveraged_position(
        self,
        side:           str,
        qty:            float,
        margin_deposit: float,
        order_type:     str = "taker",
    ) -> TradeRecord:
        """
        Open a leveraged position.

        Parameters
        ----------
        side           : 'long' or 'short'
        qty            : contract quantity
        margin_deposit : collateral deposited (in base currency)
        order_type     : 'maker' or 'taker'
        """
        if self.leverage_params is None:
            raise RuntimeError("LeverageParams not set. Pass leverage_params to constructor.")

        trade_side = "buy" if side == "long" else "sell"
        record = self.execute_trade(trade_side, qty, order_type)

        self.open_position_qty   = qty
        self.open_position_side  = side
        self.open_position_entry = record.exec_price
        self.margin_balance      = margin_deposit - record.fee_paid

        return record

    def close_leveraged_position(
        self,
        order_type: str = "taker",
    ) -> Tuple[TradeRecord, float]:
        """
        Close the open leveraged position and return (record, net_pnl).
        """
        if self.open_position_qty == 0.0:
            raise RuntimeError("No open position to close.")

        close_side = "sell" if self.open_position_side == "long" else "buy"
        record = self.execute_trade(close_side, self.open_position_qty, order_type)

        # PnL on notional
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

        # Reset position
        self.open_position_qty   = 0.0
        self.open_position_side  = None
        self.open_position_entry = None
        self.margin_balance      = None

        return record, net_pnl

    def _check_liquidation(self) -> None:
        """Liquidate if equity <= maintenance margin × notional."""
        lp    = self.leverage_params
        entry = self.open_position_entry
        qty   = self.open_position_qty
        notional = entry * qty * lp.leverage

        # Unrealised PnL
        if self.open_position_side == "long":
            upnl = (self.price - entry) * qty * lp.leverage
        else:
            upnl = (entry - self.price) * qty * lp.leverage

        equity = (self.margin_balance or 0.0) + upnl
        maint  = lp.maintenance_margin * notional

        if equity <= maint:
            event = LiquidationEvent(
                step=self.t,
                price_at_liq=self.price,
                margin_balance=equity,
                side=self.open_position_side,
            )
            self.liquidation_events.append(event)
            # Wipe position
            self.open_position_qty   = 0.0
            self.open_position_side  = None
            self.open_position_entry = None
            self.margin_balance      = 0.0

    # ── Derived series ────────────────────────────────────────────────────────

    @property
    def log_returns(self) -> np.ndarray:
        """Log-return series of the simulated price history."""
        p = np.array(self.price_history)
        return np.log(p[1:] / p[:-1])

    @property
    def regime_color_series(self) -> List[str]:
        """Map each regime to a display colour."""
        _cmap = {
            "bull":     "#2ecc71",
            "bear":     "#e74c3c",
            "high_vol": "#e67e22",
            "low_vol":  "#3498db",
        }
        return [_cmap.get(r, "#aaaaaa") for r in self.regime_history]


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 9 · Validation & Visualisation
# ──────────────────────────────────────────────────────────────────────────────

class SimulationValidator:
    """
    Runs a suite of statistical checks and produces a comprehensive
    diagnostic plot from a completed MarketSimulator run.
    """

    def __init__(self, sim: MarketSimulator) -> None:
        self.sim = sim

    # ── Statistical checks ────────────────────────────────────────────────────

    def check_fat_tails(self, threshold: float = 3.0) -> Dict[str, float]:
        """
        Verify fat tails by computing excess kurtosis of log-returns.
        Normal distribution has kurtosis ≈ 3 (excess ≈ 0).
        Crypto-like returns typically show excess kurtosis > 3.
        """
        returns = self.sim.log_returns
        n  = len(returns)
        mu = returns.mean()
        sigma = returns.std(ddof=1)
        # Fisher kurtosis (excess)
        excess_kurt = (
            (np.sum((returns - mu) ** 4) / n) / (sigma ** 4)
        ) - 3.0
        passed = excess_kurt > threshold
        return {
            "excess_kurtosis": float(excess_kurt),
            "threshold": threshold,
            "fat_tails_detected": bool(passed),
        }

    def check_volatility_clustering(self) -> Dict[str, float]:
        """
        Verify volatility clustering by computing the autocorrelation of
        squared returns at lag 1.  A value > 0.05 is considered evidence
        of clustering.
        """
        ret  = self.sim.log_returns
        sq   = ret ** 2
        mean = sq.mean()
        var  = sq.var()
        if var == 0:
            return {"autocorr_sq_lag1": 0.0, "clustering_detected": False}
        autocorr = float(np.mean((sq[:-1] - mean) * (sq[1:] - mean)) / var)
        return {
            "autocorr_sq_lag1": autocorr,
            "clustering_detected": bool(autocorr > 0.05),
        }

    def check_spread_widening(self) -> Dict[str, float]:
        """
        Confirm spread is wider during high-vol periods.
        Compares mean spread in high_vol vs. non-high_vol regimes.
        """
        spreads  = np.array(self.sim.spread_history)
        regimes  = self.sim.regime_history
        n        = min(len(spreads), len(regimes))
        spreads  = spreads[:n]
        regimes  = regimes[:n]

        hv_mask  = np.array([r == "high_vol" for r in regimes])
        other    = spreads[~hv_mask]
        hv       = spreads[hv_mask]

        mean_hv    = float(hv.mean())   if hv.size    > 0 else float("nan")
        mean_other = float(other.mean()) if other.size > 0 else float("nan")

        ratio = mean_hv / mean_other if mean_other > 0 else float("nan")
        return {
            "mean_spread_high_vol":  mean_hv,
            "mean_spread_normal":    mean_other,
            "spread_ratio":          ratio,
            "spread_widens":         bool(ratio > 1.5) if not np.isnan(ratio) else False,
        }

    def run_all_checks(self) -> None:
        """Print a human-readable validation report."""
        print("=" * 55)
        print("  SIMULATION VALIDATION REPORT")
        print("=" * 55)

        ft = self.check_fat_tails()
        print(f"\n[FAT TAILS]")
        print(f"  Excess kurtosis : {ft['excess_kurtosis']:.4f}  (threshold > {ft['threshold']})")
        print(f"  Fat tails found : {'YES ✓' if ft['fat_tails_detected'] else 'NO ✗'}")

        vc = self.check_volatility_clustering()
        print(f"\n[VOLATILITY CLUSTERING]")
        print(f"  ACF(squared ret, lag=1) : {vc['autocorr_sq_lag1']:.4f}  (threshold > 0.05)")
        print(f"  Clustering detected     : {'YES ✓' if vc['clustering_detected'] else 'NO ✗'}")

        sw = self.check_spread_widening()
        print(f"\n[SPREAD WIDENING IN HIGH-VOL]")
        print(f"  Mean spread (high_vol) : {sw['mean_spread_high_vol']:.6f}")
        print(f"  Mean spread (normal)   : {sw['mean_spread_normal']:.6f}")
        print(f"  Ratio                  : {sw['spread_ratio']:.2f}×")
        print(f"  Spread widens          : {'YES ✓' if sw['spread_widens'] else 'NO ✗'}")

        liq = self.sim.liquidation_events
        print(f"\n[LIQUIDATION EVENTS]  {len(liq)} recorded")
        for ev in liq[:5]:
            print(f"  step={ev.step:5d}  price={ev.price_at_liq:.4f}  side={ev.side}")
        if len(liq) > 5:
            print(f"  ... and {len(liq)-5} more")

        jumps = sum(self.sim.jump_history)
        print(f"\n[JUMP EVENTS]  {jumps} / {len(self.sim.jump_history)} steps "
              f"({jumps/max(len(self.sim.jump_history),1)*100:.2f} %)")
        print("=" * 55)

    # ── Plotting ──────────────────────────────────────────────────────────────

    def plot(self, title: str = "Synthetic Crypto Market Simulation") -> None:
        """
        Post-run 4-panel static diagnostic figure (call after sim.run()).
          Panel 1 – Price with regime background & jump markers
          Panel 2 – Log returns
          Panel 3 – Return distribution vs. Normal (fat-tails check)
          Panel 4 – Rolling 50-step realised volatility
        """
        prices  = np.array(self.sim.price_history)
        returns = self.sim.log_returns
        regimes = self.sim.regime_history
        jumps   = np.array(self.sim.jump_history)
        T       = len(prices)

        _bgcmap = {
            "bull":     "#d5f5e3",
            "bear":     "#fadbd8",
            "high_vol": "#fdebd0",
            "low_vol":  "#d6eaf8",
        }
        from matplotlib.patches import Patch

        fig, axes = plt.subplots(4, 1, figsize=(15, 12))
        fig.suptitle(title, fontsize=13, fontweight="bold")

        ax1, ax2, ax3, ax4 = axes

        # Panel 1 – Price
        if regimes:
            prev_r, seg_s = regimes[0], 1
            for i, r in enumerate(regimes[1:], start=2):
                if r != prev_r:
                    ax1.axvspan(seg_s, i, color=_bgcmap.get(prev_r, "#eee"), alpha=0.55, lw=0)
                    prev_r, seg_s = r, i
            ax1.axvspan(seg_s, T, color=_bgcmap.get(prev_r, "#eee"), alpha=0.55, lw=0)
        ax1.plot(range(T), prices, color="#2c3e50", lw=0.8)
        jump_idx = np.where(jumps)[0] + 1
        if jump_idx.size:
            ax1.scatter(jump_idx, prices[jump_idx], color="#e74c3c", s=18, zorder=5, marker="^")
        for lev in self.sim.liquidation_events:
            ax1.axvline(lev.step, color="#9b59b6", lw=1.1, ls="--", alpha=0.8)
        ax1.set_title("Mid-Price  (regime shading | ▲ jumps | -- liquidation)", fontsize=9)
        ax1.set_ylabel("Price")
        legend_patches = [Patch(fc=_bgcmap[r], ec="gray", label=r.replace("_","-"))
                          for r in _REGIME_ORDER]
        ax1.legend(handles=legend_patches, fontsize=7, ncol=4, loc="upper left")

        # Panel 2 – Returns
        ax2.plot(range(1, T), returns, color="#2980b9", lw=0.5, alpha=0.8)
        ax2.axhline(0, color="#95a5a6", lw=0.7, ls="--")
        ax2.set_title("Log Returns", fontsize=9)
        ax2.set_ylabel("Log Return")

        # Panel 3 – Distribution
        mu_r, std_r = returns.mean(), returns.std()
        bins = min(120, max(20, len(returns) // 40))
        ax3.hist(returns, bins=bins, density=True, color="#3498db", alpha=0.5, label="Simulated")
        xr = np.linspace(returns.min(), returns.max(), 400)
        ax3.plot(xr, np.exp(-0.5 * ((xr - mu_r) / std_r) ** 2) / (std_r * np.sqrt(2 * np.pi)),
                 color="#e74c3c", lw=1.5, label="Normal fit")
        ax3.legend(fontsize=8)
        ax3.set_title("Return Distribution vs. Normal  (fat-tails check)", fontsize=9)
        ax3.set_ylabel("Density")

        # Panel 4 – Rolling vol
        win = 50
        if len(returns) >= win:
            rvol = [returns[max(0, i - win):i].std() for i in range(win, len(returns) + 1)]
            ax4.plot(range(win, len(returns) + 1), rvol, color="#8e44ad", lw=0.8)
        ax4.set_title("Rolling 50-Step Realised Volatility", fontsize=9)
        ax4.set_ylabel("Std")
        ax4.set_xlabel("Step")

        fig.tight_layout()
        plt.show()


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 9b · Candle Aggregation + Live 7-Timeframe Candlestick Chart
# ──────────────────────────────────────────────────────────────────────────────

# ── Time formatter ────────────────────────────────────────────────────────────

def _format_sim_time(seconds: int) -> str:
    """Convert simulated seconds into human-readable d h m s string."""
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600)  // 60
    s =  seconds % 60
    if d > 0:   return f"{d}d {h:02d}h {m:02d}m {s:02d}s"
    if h > 0:   return f"{h:02d}h {m:02d}m {s:02d}s"
    if m > 0:   return f"{m:02d}m {s:02d}s"
    return f"{s}s"


# ── Candle Aggregator ─────────────────────────────────────────────────────────

class CandleAggregator:
    """
    Aggregates individual price ticks into OHLC candles for a fixed
    *steps_per_candle* period.  Each completed candle is stored in
    ``self.candles`` as a tuple (start_step, open, high, low, close).
    The in-progress (incomplete) candle is available via ``self.current``.
    """

    def __init__(self, steps_per_candle: int) -> None:
        self.spc    = steps_per_candle
        self.candles: List[Tuple[int, float, float, float, float]] = []
        self._open:  Optional[float] = None
        self._high:  Optional[float] = None
        self._low:   Optional[float] = None
        self._last:  Optional[float] = None
        self._start: int = 0
        self._count: int = 0

    def push(self, step: int, price: float) -> Optional[Tuple]:
        """Feed one tick.  Returns the completed candle tuple if one just closed."""
        if self._open is None:
            self._open  = price
            self._high  = price
            self._low   = price
            self._last  = price
            self._start = step
            self._count = 1
        else:
            if price > self._high: self._high = price
            if price < self._low:  self._low  = price
            self._last  = price
            self._count += 1

        if self._count >= self.spc:
            candle = (self._start, self._open, self._high, self._low, self._last)
            self.candles.append(candle)
            self._open  = None;  self._high = None
            self._low   = None;  self._last = None
            self._count = 0;     self._start = step + 1
            return candle
        return None

    @property
    def current(self) -> Optional[Tuple]:
        """In-progress candle (open, high, low, last) or None."""
        if self._open is None:
            return None
        return (self._start, self._open, self._high, self._low, self._last)

    @property
    def progress(self) -> float:
        """Fraction [0, 1) of current candle completed."""
        return self._count / self.spc if self.spc > 0 else 0.0


# ── Candle drawing helper ─────────────────────────────────────────────────────

def _style_candle_ax(ax: "plt.Axes") -> None:
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#55557a", labelsize=6, length=2, width=0.5)
    ax.yaxis.set_tick_params(right=True, left=False, labelright=True, labelleft=False)
    for sp in ax.spines.values():
        sp.set_edgecolor("#1e1e35")


def _draw_candles(
    ax:          "plt.Axes",
    completed:   List[Tuple],
    current:     Optional[Tuple],
    max_display: int,
    tf_label:    str,
) -> None:
    """
    Redraw a candlestick panel.
    Completed candles are green/red; the live (in-progress) candle is dimmed.
    """
    ax.cla()
    ax.set_facecolor("#0d1117")

    disp    = list(completed[-max_display:])
    is_live = [False] * len(disp)

    if current is not None:
        disp.append(current)
        is_live.append(True)

    if not disp:
        ax.set_title(tf_label, fontsize=8, color="#444466", pad=2)
        _style_candle_ax(ax)
        return

    n      = len(disp)
    opens  = np.array([c[1] for c in disp], dtype=float)
    highs  = np.array([c[2] for c in disp], dtype=float)
    lows   = np.array([c[3] for c in disp], dtype=float)
    closes = np.array([c[4] for c in disp], dtype=float)
    bull   = closes >= opens
    live   = np.array(is_live, dtype=bool)
    xs     = np.arange(n, dtype=float)

    # ── Wicks via LineCollection (fast) ──────────────────────────────────────
    segs  = [[(float(i), lows[i]), (float(i), highs[i])] for i in range(n)]
    wcols = []
    for i in range(n):
        if live[i]:
            wcols.append("#1a6b65" if bull[i] else "#7b2525")
        else:
            wcols.append("#26a69a" if bull[i] else "#ef5350")
    lc = LineCollection(segs, colors=wcols, linewidths=0.8, zorder=2)
    ax.add_collection(lc)

    # ── Bodies (batched by colour) ───────────────────────────────────────────
    body_lo = np.minimum(opens, closes)
    body_hi = np.maximum(opens, closes)
    heights = np.maximum(body_hi - body_lo, (highs - lows) * 0.1 + 1e-12)

    def _batch_bar(mask, color, alpha=1.0):
        if mask.any():
            ax.bar(xs[mask], heights[mask], bottom=body_lo[mask],
                   width=0.65, color=color, linewidth=0, zorder=3, alpha=alpha)

    _batch_bar(bull & ~live,  "#26a69a")
    _batch_bar(~bull & ~live, "#ef5350")
    _batch_bar(bull &  live,  "#1a8a80", alpha=0.65)
    _batch_bar(~bull & live,  "#8b3030", alpha=0.65)

    # ── Axes limits ──────────────────────────────────────────────────────────
    price_range = highs.max() - lows.min()
    margin = max(price_range * 0.08, highs.max() * 0.0005, 1e-6)
    ax.set_ylim(lows.min() - margin, highs.max() + margin)
    ax.set_xlim(-0.5, n + 0.5)

    # Current price dashed line
    ax.axhline(closes[-1], color="#cccccc", linewidth=0.35, alpha=0.45, linestyle="--")

    n_closed = len(completed[-max_display:])
    ax.set_title(
        f"[ {tf_label} ]  {n_closed} closed  ·  {closes[-1]:.4f}",
        fontsize=8, color="#9999bb", pad=3, loc="left",
    )
    _style_candle_ax(ax)


# ── Live Simulation Plot ──────────────────────────────────────────────────────

class LiveSimulationPlot:
    """
    Real-time 7-timeframe candlestick chart driven by MarketSimulator.

    1 step = 1 simulated second.  At ``interval_ms=450`` each frame advances
    the clock by exactly 1 simulated second every ~0.45 real seconds.

    Panels
    ------
    1-second · 1-minute · 5-minute · 15-minute · 30-minute · 1-hour · 1-day
    + a live stats readout in the 8th cell.

    Parameters
    ----------
    sim         : freshly created MarketSimulator.
    interval_ms : real-time milliseconds between frames (default 450 -> 0.45 s).
    """

    # (label, steps_per_candle, max_candles_to_display)
    TIMEFRAMES = [
        ("1s",  1,     120),
        ("1m",  60,    90),
        ("5m",  300,   60),
        ("15m", 900,   40),
        ("30m", 1800,  30),
        ("1h",  3600,  24),
        ("1d",  86400, 14),
    ]

    _REGIME_COL = {
        "bull":     "#2ecc71",
        "bear":     "#e74c3c",
        "high_vol": "#e67e22",
        "low_vol":  "#3498db",
    }

    def __init__(
        self,
        sim,
        interval_ms: int = 450,
    ) -> None:
        self.sim         = sim
        self.interval_ms = interval_ms
        self._anim       = None

        # One CandleAggregator per timeframe
        self.aggregators = {
            tf: CandleAggregator(spc)
            for tf, spc, _ in self.TIMEFRAMES
        }

        # Figure: 4 rows x 2 cols  (last cell = stats readout)
        import matplotlib.gridspec as _gs
        self.fig = plt.figure(figsize=(18, 13), facecolor="#0d1117")
        gs = _gs.GridSpec(4, 2, figure=self.fig, hspace=0.52, wspace=0.15)

        positions = [(0,0),(0,1),(1,0),(1,1),(2,0),(2,1),(3,0)]
        self.ax = {}
        for (tf, spc, _), pos in zip(self.TIMEFRAMES, positions):
            r, c = pos
            self.ax[tf] = self.fig.add_subplot(gs[r, c])

        # Stats panel (bottom-right)
        self.ax_stats = self.fig.add_subplot(gs[3, 1])
        self.ax_stats.set_facecolor("#0d1117")
        self.ax_stats.axis("off")
        self._stats_text = self.ax_stats.text(
            0.06, 0.94, "Initialising...",
            transform=self.ax_stats.transAxes,
            va="top", fontsize=9, color="#aabbdd",
            fontfamily="monospace", linespacing=1.6,
        )

    # Internal update ─────────────────────────────────────────────────────────

    def _update(self, _frame):
        sim = self.sim
        sim.step()
        price = sim.price
        step  = sim.t

        # Feed tick to every aggregator
        for tf, _, _ in self.TIMEFRAMES:
            self.aggregators[tf].push(step, price)

        # Redraw each candlestick panel
        for tf, _, max_c in self.TIMEFRAMES:
            _draw_candles(
                self.ax[tf],
                self.aggregators[tf].candles,
                self.aggregators[tf].current,
                max_c,
                tf,
            )

        # Stats readout
        n_jumps = sum(sim.jump_history)
        lines = [
            "====== LIVE STATS ======",
            "",
            f"Sim Time  {_format_sim_time(step):>14}",
            f"Step      {step:>14,}",
            f"Price     {price:>14.4f}",
            f"Regime    {sim.regime:>14}",
            f"Jumps     {n_jumps:>14}",
            f"Liq.      {len(sim.liquidation_events):>14}",
            "",
            "Closed candles:",
        ]
        for tf, _, _ in self.TIMEFRAMES:
            n_c = len(self.aggregators[tf].candles)
            pct = self.aggregators[tf].progress * 100
            lines.append(f"  {tf:<4} {n_c:>6}  ({pct:4.1f}% open)")
        self._stats_text.set_text("\n".join(lines))

        # Supertitle
        rc = self._REGIME_COL.get(sim.regime, "#ffffff")
        self.fig.suptitle(
            f"SYNTHETIC CRYPTO MARKET  |  {_format_sim_time(step)}  |  "
            f"Price: {price:.4f}  |  Regime: {sim.regime.upper()}  |  "
            f"Jumps: {n_jumps}  |  Liqs: {len(sim.liquidation_events)}",
            fontsize=10, fontweight="bold", color=rc, y=0.997,
        )

    # Public entry point ──────────────────────────────────────────────────────

    def start(self):
        """Launch the live animation.  Blocks until window is closed."""
        from matplotlib.animation import FuncAnimation
        self._anim = FuncAnimation(
            self.fig, self._update,
            interval=self.interval_ms,
            cache_frame_data=False,
            save_count=0,
        )
        plt.show()

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 10 · Convenience Factory Functions
# ──────────────────────────────────────────────────────────────────────────────

def build_full_simulator(
    initial_price: float = 100.0,
    seed:          int   = 42,
) -> MarketSimulator:
    """
    Create a fully-featured MarketSimulator with all layers enabled
    and sensible default parameters.
    """
    return MarketSimulator(
        initial_price=initial_price,
        seed=seed,
        initial_regime="bull",
        enable_regime=True,
        enable_stochastic=True,
        enable_jumps=True,
        enable_spread=True,
        fee_params=FeeParams(),
        leverage_params=LeverageParams(),
        jump_params=JumpParams(),
        spread_params=SpreadParams(),
    )


def build_minimal_simulator(
    initial_price: float = 100.0,
    seed:          int   = 42,
) -> MarketSimulator:
    """
    Step 1 behaviour only: flat price, no randomness, no regime.
    """
    return MarketSimulator(
        initial_price=initial_price,
        seed=seed,
        enable_regime=False,
        enable_stochastic=False,
        enable_jumps=False,
        enable_spread=False,
    )


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 11 · Quick self-test (run this file directly)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n── Step 1 Verification: Flat price (no randomness) ──")
    s1 = build_minimal_simulator(initial_price=100.0)
    s1.run(10)
    print(f"  Prices: {s1.price_history}")
    assert all(p == 100.0 for p in s1.price_history), "Step 1 FAILED: price changed"
    print("  PASS\n")

    print("── Full Simulation: 5 000 steps, all features enabled ──")
    sim = build_full_simulator(initial_price=100.0, seed=42)
    prices = sim.run(5_000)
    print(f"  Final price   : {sim.price:.4f}")
    print(f"  Jump events   : {sum(sim.jump_history)}")
    print(f"  Regime changes: {sum(1 for i in range(1,len(sim.regime_history)) if sim.regime_history[i]!=sim.regime_history[i-1])}")

    # ── Fee / trade demo ─────────────────────────────────────────────────────
    print("\n── Trade Execution Demo (Step 6) ──")
    buy  = sim.execute_trade("buy",  qty=1.0, order_type="taker")
    sell = sim.execute_trade("sell", qty=1.0, order_type="maker")
    net  = sim.compute_net_pnl(buy, sell)
    print(f"  Buy  @ {buy.exec_price:.4f}  (fee={buy.fee_paid:.6f})")
    print(f"  Sell @ {sell.exec_price:.4f}  (fee={sell.fee_paid:.6f})")
    print(f"  Net PnL: {net:.6f}\n")

    # ── Leverage demo ─────────────────────────────────────────────────────────
    print("── Leverage Demo (Step 7, 10× long) ──")
    lev_sim = build_full_simulator(seed=99)
    lev_sim.run(200)
    entry_rec = lev_sim.open_leveraged_position("long", qty=1.0, margin_deposit=50.0)
    lev_sim.run(100)
    if lev_sim.open_position_qty != 0.0:
        close_rec, net_pnl = lev_sim.close_leveraged_position()
        print(f"  Entry: {entry_rec.exec_price:.4f}")
        print(f"  Exit : {close_rec.exec_price:.4f}")
        print(f"  Net PnL (10×): {net_pnl:.4f}")
    else:
        print(f"  Entry: {entry_rec.exec_price:.4f}")
        print("  Position was LIQUIDATED before manual close.")
    print(f"  Liquidations : {len(lev_sim.liquidation_events)}\n")

    # ── Validation ───────────────────────────────────────────────────────────
    validator = SimulationValidator(sim)
    validator.run_all_checks()

    # ── Live candlestick chart (7 timeframes, 1 step = 1 simulated second) ──
    print("\n── Launching live 7-timeframe candlestick chart ──")
    print("   1 step = 1 simulated second  |  0.45 real seconds per step")
    print("   Timeframes: 1s  1m  5m  15m  30m  1h  1d")
    print("   Close the window to stop.\n")
    live_sim = build_full_simulator(initial_price=100.0, seed=None)
    live = LiveSimulationPlot(
        sim=live_sim,
        interval_ms=450,   # 0.45 real seconds per simulated second
    )
    live.start()
