"""
simulator_web.py
================
Flask + Socket.IO web interface for the Synthetic Crypto Market Simulator.

Features
--------
  - Real-time candlestick streaming (WebSocket)
  - 7 timeframes : 1s · 1m · 5m · 15m · 30m · 1h · 1d
  - Full indicator suite (numpy only):
      Trend  : SMA · EMA · WMA · VWAP · Ichimoku
      Vol    : Bollinger Bands · ATR · Keltner Channel
      Momentum: RSI · Stochastic · CCI · Williams %R
      MACD   : line · signal · histogram
      Volume : OBV · CMF · volume bars
  - Speed multiplier : ×1 · ×10 · ×100 · ×1000 · MAX
  - Random starting price every new simulation
  - TradingView Lightweight Charts frontend

Usage
-----
    python simulator_web.py
    Open http://localhost:5000
"""

from __future__ import annotations

import math
import random
import threading
import time
import calendar
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

from synthetic_market_simulator import (
    MarketSimulator,
    JumpParams,
    SpreadParams,
    FeeParams,
    LeverageParams,
    CandleAggregator,
    REGIMES,
)

# ─── constants ────────────────────────────────────────────────────────────────
BASE_EPOCH  = calendar.timegm(time.strptime("2024-01-01", "%Y-%m-%d"))   # UTC
MAX_CANDLES = 2_000          # candles kept per timeframe
EMIT_MS     = 50             # visual update interval (ms) → 20 fps

TIMEFRAMES: List[Tuple[str, int]] = [
    ("1s",   1),
    ("1m",   60),
    ("5m",   300),
    ("15m",  900),
    ("30m",  1_800),
    ("1h",   3_600),
    ("1d",   86_400),
]

# steps_per_frame at each named speed  (frame = EMIT_MS)
SPEED_MAP: Dict[Any, float] = {
    1:      EMIT_MS / 1000,          # 1 step/s  → ~0.05 steps/frame
    10:     EMIT_MS * 10 / 1000,     # 10 steps/s
    100:    EMIT_MS * 100 / 1000,    # 100 steps/s
    1000:   EMIT_MS * 1000 / 1000,   # 1000 steps/s
    "max":  EMIT_MS * 20_000 / 1000, # ~20k steps/s
}

# ─── trading constants ────────────────────────────────────────────────────────
INITIAL_BALANCE  = 10_000.0
MAINT_MARGIN     = 0.005    # 0.5 % maintenance margin rate
TAKER_FEE_RATE   = 0.0006   # 0.06 %
MAKER_FEE_RATE   = 0.0002   # 0.02 %


class Position:
    """Single open leveraged position."""
    def __init__(self, pos_id: str, side: str, entry: float,
                 size_usd: float, leverage: float,
                 order_type: str = "market") -> None:
        self.id          = pos_id
        self.side        = side          # 'long' | 'short'
        self.entry_price = entry
        self.size_usd    = size_usd      # notional
        self.leverage    = leverage
        self.margin      = size_usd / leverage
        self.qty         = size_usd / entry
        fee_rate         = MAKER_FEE_RATE if order_type == "limit" else TAKER_FEE_RATE
        self.fee_paid    = size_usd * fee_rate
        if side == "long":
            self.liq_price = entry * (1 - 1 / leverage + MAINT_MARGIN)
        else:
            self.liq_price = entry * (1 + 1 / leverage - MAINT_MARGIN)

    def unrealized_pnl(self, price: float) -> float:
        if self.side == "long":
            return (price - self.entry_price) / self.entry_price * self.size_usd
        return (self.entry_price - price) / self.entry_price * self.size_usd

    def is_liquidated(self, price: float) -> bool:
        return price <= self.liq_price if self.side == "long" else price >= self.liq_price

    def to_dict(self, price: float) -> Dict:
        upnl     = self.unrealized_pnl(price)
        upnl_pct = upnl / self.margin * 100 if self.margin else 0
        return {
            "id":           self.id,
            "side":         self.side,
            "entry_price":  round(self.entry_price, 6),
            "size_usd":     round(self.size_usd, 2),
            "leverage":     self.leverage,
            "margin":       round(self.margin, 2),
            "qty":          round(self.qty, 6),
            "liq_price":    round(self.liq_price, 6),
            "upnl":         round(upnl, 2),
            "upnl_pct":     round(upnl_pct, 2),
            "fee_paid":     round(self.fee_paid, 4),
        }


class Order:
    """Pending limit / stop / stop-limit order."""
    def __init__(self, ord_id: str, order_type: str, side: str,
                 size_usd: float, leverage: float,
                 trigger_price: float,
                 limit_price: Optional[float] = None) -> None:
        self.id            = ord_id
        self.type          = order_type   # 'limit' | 'stop' | 'stop_limit'
        self.side          = side
        self.size_usd      = size_usd
        self.leverage      = leverage
        self.trigger_price = trigger_price
        self.limit_price   = limit_price

    def should_trigger(self, price: float) -> bool:
        if self.type == "limit":
            return price <= self.trigger_price if self.side == "long" else price >= self.trigger_price
        # stop / stop_limit
        return price >= self.trigger_price if self.side == "long" else price <= self.trigger_price

    def fill_price(self, price: float) -> float:
        if self.type == "limit":    return self.trigger_price
        if self.type == "stop":     return price
        return self.limit_price if self.limit_price else self.trigger_price

    def to_dict(self) -> Dict:
        return {
            "id":            self.id,
            "type":          self.type,
            "side":          self.side,
            "size_usd":      round(self.size_usd, 2),
            "leverage":      self.leverage,
            "trigger_price": round(self.trigger_price, 6),
            "limit_price":   round(self.limit_price, 6) if self.limit_price else None,
        }


# ─── indicator helpers (pure numpy) ───────────────────────────────────────────

def _nan(n: int) -> np.ndarray:
    a = np.empty(n); a[:] = np.nan; return a


def _ema_raw(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA via recursive formula; NaN where not enough data."""
    out = _nan(len(arr))
    k = 2.0 / (period + 1)
    for i, v in enumerate(arr):
        if np.isnan(v):
            continue
        if np.isnan(out[max(0, i-1)]):
            # seed with first valid SMA
            start = i
            s = 0.0; cnt = 0
            for j in range(i, min(i + period, len(arr))):
                if not np.isnan(arr[j]):
                    s += arr[j]; cnt += 1
                if cnt == period:
                    out[j] = s / period
                    for jj in range(j+1, len(arr)):
                        if not np.isnan(arr[jj]):
                            out[jj] = arr[jj] * k + out[jj-1] * (1 - k)
                    return out
            break
        else:
            out[i] = v * k + out[i-1] * (1 - k)
    return out


def ind_sma(closes: np.ndarray, period: int) -> np.ndarray:
    out = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        out[i] = closes[i - period + 1:i + 1].mean()
    return out


def ind_ema(closes: np.ndarray, period: int) -> np.ndarray:
    return _ema_raw(closes, period)


def ind_wma(closes: np.ndarray, period: int) -> np.ndarray:
    weights = np.arange(1, period + 1, dtype=float)
    out = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        out[i] = np.dot(closes[i - period + 1:i + 1], weights) / weights.sum()
    return out


def ind_vwap(highs, lows, closes, volumes) -> np.ndarray:
    """Cumulative VWAP from first candle."""
    tp  = (highs + lows + closes) / 3.0
    cv  = np.cumsum(tp * volumes)
    cvol = np.cumsum(volumes)
    return cv / np.where(cvol > 0, cvol, 1.0)


def ind_bollinger(closes, period=20, num_std=2.0):
    mid = ind_sma(closes, period)
    std = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        std[i] = closes[i - period + 1:i + 1].std()
    return mid + num_std * std, mid, mid - num_std * std   # upper, mid, lower


def ind_atr(highs, lows, closes, period=14) -> np.ndarray:
    n  = len(closes)
    tr = _nan(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i]  - closes[i-1]))
    tr[0] = highs[0] - lows[0]
    out = _nan(n)
    if n >= period:
        out[period - 1] = tr[1:period + 1].mean()
        k = 1.0 / period
        for i in range(period, n):
            out[i] = tr[i] * k + out[i-1] * (1 - k)
    return out


def ind_keltner(highs, lows, closes, period=20, mult=2.0):
    mid = ind_ema(closes, period)
    atr = ind_atr(highs, lows, closes, period)
    return mid + mult * atr, mid, mid - mult * atr


def ind_rsi(closes, period=14) -> np.ndarray:
    n = len(closes)
    out = _nan(n)
    if n < period + 1:
        return out
    delta = np.diff(closes.astype(float))
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_g = gain[:period].mean()
    avg_l = loss[:period].mean()
    if avg_l == 0:
        out[period] = 100.0
    else:
        rs = avg_g / avg_l
        out[period] = 100.0 - 100.0 / (1 + rs)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gain[i-1]) / period
        avg_l = (avg_l * (period - 1) + loss[i-1]) / period
        if avg_l == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1 + avg_g / avg_l)
    return out


def ind_stochastic(highs, lows, closes, k_period=14, d_period=3):
    n  = len(closes)
    k  = _nan(n)
    for i in range(k_period - 1, n):
        lo = lows[i - k_period + 1:i + 1].min()
        hi = highs[i - k_period + 1:i + 1].max()
        rng = hi - lo
        k[i] = 100.0 * (closes[i] - lo) / rng if rng > 0 else 50.0
    d = ind_sma(k, d_period)
    return k, d


def ind_cci(highs, lows, closes, period=20) -> np.ndarray:
    n  = len(closes)
    tp = (highs + lows + closes) / 3.0
    out = _nan(n)
    for i in range(period - 1, n):
        sl = tp[i - period + 1:i + 1]
        m  = sl.mean()
        md = np.abs(sl - m).mean()
        out[i] = (tp[i] - m) / (0.015 * md) if md > 0 else 0.0
    return out


def ind_williams_r(highs, lows, closes, period=14) -> np.ndarray:
    n  = len(closes)
    out = _nan(n)
    for i in range(period - 1, n):
        hi = highs[i - period + 1:i + 1].max()
        lo = lows[i - period + 1:i + 1].min()
        rng = hi - lo
        out[i] = -100.0 * (hi - closes[i]) / rng if rng > 0 else -50.0
    return out


def ind_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = ind_ema(closes, fast)
    ema_slow = ind_ema(closes, slow)
    line     = ema_fast - ema_slow
    sig      = _ema_raw(line, signal)
    hist     = line - sig
    return line, sig, hist


def ind_obv(closes, volumes) -> np.ndarray:
    n   = len(closes)
    out = np.zeros(n)
    out[0] = volumes[0]
    for i in range(1, n):
        if closes[i] > closes[i-1]:
            out[i] = out[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            out[i] = out[i-1] - volumes[i]
        else:
            out[i] = out[i-1]
    return out


def ind_cmf(highs, lows, closes, volumes, period=20) -> np.ndarray:
    n  = len(closes)
    rng = highs - lows
    with np.errstate(invalid='ignore', divide='ignore'):
        mfv_raw = np.where(rng > 0,
                           ((closes - lows) - (highs - closes)) / rng,
                           0.0)
    mfm = mfv_raw
    mfv = mfm * volumes
    out = _nan(n)
    for i in range(period - 1, n):
        sv = volumes[i - period + 1:i + 1].sum()
        out[i] = mfv[i - period + 1:i + 1].sum() / sv if sv > 0 else 0.0
    return out


def ind_ichimoku(highs, lows, closes,
                 tenkan=9, kijun=26, senkou_b=52, chikou_offset=26):
    n  = len(closes)
    def mid_hl(h, l, p):
        out = _nan(n)
        for i in range(p - 1, n):
            out[i] = (h[i-p+1:i+1].max() + l[i-p+1:i+1].min()) / 2
        return out
    tk   = mid_hl(highs, lows, tenkan)
    kj   = mid_hl(highs, lows, kijun)
    sa   = (tk + kj) / 2      # Span A (leading 26)
    sb   = mid_hl(highs, lows, senkou_b)  # Span B (leading 26)
    # chikou = close shifted back 26
    ck = _nan(n)
    for i in range(chikou_offset, n):
        ck[i - chikou_offset] = closes[i]
    return tk, kj, sa, sb, ck


# ─── volume simulation ────────────────────────────────────────────────────────

_REGIME_VMULT = {"bull": 1.2, "bear": 1.3, "high_vol": 2.5, "low_vol": 0.5}

def _sim_volume(base_vol: float, price_ret: float, regime: str,
                rng: np.random.Generator) -> float:
    activity = 1.0 + abs(price_ret) * 200
    mult     = _REGIME_VMULT.get(regime, 1.0)
    noise    = max(0.2, rng.normal(1.0, 0.25))
    return max(1.0, base_vol * activity * mult * noise)


# ─── candle storage (OHLCV + step index) ─────────────────────────────────────

class OHLCVAggregator(CandleAggregator):
    """Extends CandleAggregator with volume tracking."""

    def __init__(self, steps_per_candle: int) -> None:
        super().__init__(steps_per_candle)
        self.ohlcv: List[Dict] = []           # completed candles
        self._vol_acc: float = 0.0

    def push_v(self, step: int, price: float, volume: float
               ) -> Optional[Dict]:
        """Feed one OHLCV tick; returns completed candle dict or None."""
        result_tuple = self.push(step, price)
        self._vol_acc += volume
        if result_tuple is not None:
            # candle just closed
            s, o, h, l, c = result_tuple
            candle = {
                "time":   BASE_EPOCH + s,
                "open":   round(o, 6),
                "high":   round(h, 6),
                "low":    round(l, 6),
                "close":  round(c, 6),
                "volume": round(self._vol_acc, 2),
            }
            self.ohlcv.append(candle)
            if len(self.ohlcv) > MAX_CANDLES:
                self.ohlcv = self.ohlcv[-MAX_CANDLES:]
            self._vol_acc = 0.0
            return candle
        return None

    @property
    def current_ohlcv(self) -> Optional[Dict]:
        c = self.current
        if c is None:
            return None
        s, o, h, l, cl = c
        return {
            "time":   BASE_EPOCH + s,
            "open":   round(o, 6),
            "high":   round(h, 6),
            "low":    round(l, 6),
            "close":  round(cl, 6),
            "volume": round(self._vol_acc, 2),
        }


# ─── indicator calculator ─────────────────────────────────────────────────────

def compute_indicators(candles: List[Dict]) -> Dict:
    """Compute all indicators for a list of OHLCV candles."""
    if len(candles) < 2:
        return {}

    times   = np.array([c["time"]   for c in candles])
    opens   = np.array([c["open"]   for c in candles], dtype=float)
    highs   = np.array([c["high"]   for c in candles], dtype=float)
    lows    = np.array([c["low"]    for c in candles], dtype=float)
    closes  = np.array([c["close"]  for c in candles], dtype=float)
    volumes = np.array([c["volume"] for c in candles], dtype=float)

    def _series(values: np.ndarray) -> List[Dict]:
        out = []
        for t, v in zip(times, values):
            if not np.isnan(v):
                out.append({"time": int(t), "value": round(float(v), 6)})
        return out

    def _hist_series(values: np.ndarray) -> List[Dict]:
        out = []
        for t, v in zip(times, values):
            if not np.isnan(v):
                color = "#26a69a" if v >= 0 else "#ef5350"
                out.append({"time": int(t), "value": round(float(v), 6),
                             "color": color})
        return out

    # ── trend ─────────────────────────────────────────────────────────────────
    sma20   = ind_sma(closes, 20)
    sma50   = ind_sma(closes, 50)
    sma200  = ind_sma(closes, 200)
    ema9    = ind_ema(closes, 9)
    ema20   = ind_ema(closes, 20)
    ema50   = ind_ema(closes, 50)
    wma20   = ind_wma(closes, 20)
    vwap    = ind_vwap(highs, lows, closes, volumes)

    tk, kj, sa, sb, ck = ind_ichimoku(highs, lows, closes)

    # ── volatility ─────────────────────────────────────────────────────────────
    bb_u, bb_m, bb_l = ind_bollinger(closes, 20, 2.0)
    atr14            = ind_atr(highs, lows, closes, 14)
    kc_u, kc_m, kc_l = ind_keltner(highs, lows, closes, 20, 2.0)

    # ── momentum ──────────────────────────────────────────────────────────────
    rsi14  = ind_rsi(closes, 14)
    stk, std = ind_stochastic(highs, lows, closes, 14, 3)
    cci20  = ind_cci(highs, lows, closes, 20)
    wr14   = ind_williams_r(highs, lows, closes, 14)

    # ── MACD ──────────────────────────────────────────────────────────────────
    macd_l, macd_s, macd_h = ind_macd(closes, 12, 26, 9)

    # ── volume ────────────────────────────────────────────────────────────────
    obv    = ind_obv(closes, volumes)
    cmf20  = ind_cmf(highs, lows, closes, volumes, 20)

    # ── volume bar colours ────────────────────────────────────────────────────
    vol_bars = []
    for t, v, o, c_ in zip(times, volumes, opens, closes):
        col = "#26a69a80" if c_ >= o else "#ef535080"
        vol_bars.append({"time": int(t), "value": round(float(v), 2),
                         "color": col})

    return {
        # trend
        "sma20":   _series(sma20),
        "sma50":   _series(sma50),
        "sma200":  _series(sma200),
        "ema9":    _series(ema9),
        "ema20":   _series(ema20),
        "ema50":   _series(ema50),
        "wma20":   _series(wma20),
        "vwap":    _series(vwap),
        # ichimoku
        "ichi_tenkan":  _series(tk),
        "ichi_kijun":   _series(kj),
        "ichi_span_a":  _series(sa),
        "ichi_span_b":  _series(sb),
        "ichi_chikou":  _series(ck),
        # volatility
        "bb_upper":  _series(bb_u),
        "bb_middle": _series(bb_m),
        "bb_lower":  _series(bb_l),
        "atr14":     _series(atr14),
        "kc_upper":  _series(kc_u),
        "kc_middle": _series(kc_m),
        "kc_lower":  _series(kc_l),
        # momentum
        "rsi14":    _series(rsi14),
        "stoch_k":  _series(stk),
        "stoch_d":  _series(std),
        "cci20":    _series(cci20),
        "williams_r": _series(wr14),
        # MACD
        "macd_line":  _series(macd_l),
        "macd_signal":_series(macd_s),
        "macd_hist":  _hist_series(macd_h),
        # volume
        "obv":     _series(obv),
        "cmf20":   _series(cmf20),
        "volume":  vol_bars,
    }


# ─── strategy engine ──────────────────────────────────────────────────────────

@dataclass
class StrategyCondition:
    """Single condition: e.g. 'rsi14' '<' 30."""
    indicator: str       # key in indicator output (rsi14, ema9, etc.)
    operator:  str       # '<', '>', 'crosses_above', 'crosses_below'
    value:     float     # threshold value
    compare_indicator: Optional[str] = None  # if set, compare vs another indicator


@dataclass
class StrategyConfig:
    """Full strategy configuration."""
    name:             str
    entry_conditions: List[Dict]   # [{indicator, operator, value, compare_indicator}]
    exit_conditions:  List[Dict]   # same format
    take_profit_pct:  float = 5.0  # % TP
    stop_loss_pct:    float = 3.0  # % SL
    trailing_stop:    bool  = False
    size_usd:         float = 500.0
    side:             str   = 'long'  # 'long', 'short', 'both'
    timeframe:        str   = '1m'
    enabled:          bool  = True


class Strategy:
    """Live strategy instance with its own balance and trade tracking."""

    def __init__(self, sid: str, config: StrategyConfig) -> None:
        self.id       = sid
        self.config   = config
        self.balance  = INITIAL_BALANCE
        self.position: Optional[Dict] = None  # {side, entry, size_usd, peak}
        self.trades:   List[Dict] = []         # closed trades
        self.total_pnl = 0.0
        self._prev_ind: Dict = {}              # previous indicator snapshot
        self.markers:  List[Dict] = []         # chart markers [{time, side, price}]

    def _eval_condition(self, cond: Dict, ind_last: Dict) -> bool:
        """Evaluate one condition against current indicator values."""
        key = cond.get('indicator', '')
        op  = cond.get('operator', '>')
        # Get current value
        cur_val = None
        if key in ind_last and isinstance(ind_last[key], dict):
            cur_val = ind_last[key].get('value')
        elif key in ind_last:
            cur_val = ind_last[key]
        if cur_val is None:
            return False
        # Compare against another indicator or a fixed value
        cmp_key = cond.get('compare_indicator')
        if cmp_key:
            cmp_val = None
            if cmp_key in ind_last and isinstance(ind_last[cmp_key], dict):
                cmp_val = ind_last[cmp_key].get('value')
            elif cmp_key in ind_last:
                cmp_val = ind_last[cmp_key]
            if cmp_val is None:
                return False
            threshold = cmp_val
        else:
            threshold = cond.get('value', 0)
        # Get previous values for cross detection
        prev_val = self._prev_ind.get(key)
        prev_thr = self._prev_ind.get(cmp_key) if cmp_key else threshold
        if op == '<':
            return cur_val < threshold
        elif op == '>':
            return cur_val > threshold
        elif op == '<=':
            return cur_val <= threshold
        elif op == '>=':
            return cur_val >= threshold
        elif op == 'crosses_above':
            if prev_val is None or prev_thr is None:
                return False
            return prev_val <= prev_thr and cur_val > threshold
        elif op == 'crosses_below':
            if prev_val is None or prev_thr is None:
                return False
            return prev_val >= prev_thr and cur_val < threshold
        return False

    def _all_conditions_met(self, conditions: List[Dict], ind_last: Dict) -> bool:
        if not conditions:
            return False
        return all(self._eval_condition(c, ind_last) for c in conditions)

    def evaluate(self, ind_last: Dict, price: float, candle_time: int) -> Optional[Dict]:
        """
        Evaluate strategy on candle close. Returns action dict or None.
        ind_last: {indicator_key: last_value_dict_or_number}
        """
        cfg = self.config
        if not cfg.enabled:
            return None

        # Flatten ind_last: extract .value from dicts
        flat = {}
        for k, v in ind_last.items():
            if isinstance(v, dict) and 'value' in v:
                flat[k] = v['value']
            elif isinstance(v, (int, float)):
                flat[k] = v

        action = None

        if self.position is None:
            # Check entry
            if self._all_conditions_met(cfg.entry_conditions, flat):
                side = cfg.side if cfg.side != 'both' else 'long'
                if self.balance >= cfg.size_usd * 0.01:
                    cost = min(cfg.size_usd, self.balance * 0.95)
                    self.position = {
                        'side': side,
                        'entry': price,
                        'size_usd': cost,
                        'peak': price,
                        'time': candle_time,
                    }
                    self.balance -= cost
                    self.markers.append({
                        'time': candle_time,
                        'position': 'belowBar' if side == 'long' else 'aboveBar',
                        'color': '#26a69a' if side == 'long' else '#ef5350',
                        'shape': 'arrowUp' if side == 'long' else 'arrowDown',
                        'text': f'{cfg.name} ENTRY',
                    })
                    action = {'type': 'entry', 'side': side, 'price': price}
        else:
            # Check exit conditions
            pos  = self.position
            side = pos['side']
            entry = pos['entry']
            # P&L calculation
            if side == 'long':
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100
            # Update trailing stop peak
            if side == 'long':
                pos['peak'] = max(pos['peak'], price)
            else:
                pos['peak'] = min(pos['peak'], price)
            # Exit reasons
            exit_reason = None
            if pnl_pct >= cfg.take_profit_pct:
                exit_reason = 'TP'
            elif pnl_pct <= -cfg.stop_loss_pct:
                exit_reason = 'SL'
            elif cfg.trailing_stop:
                if side == 'long':
                    trail_pnl = (price - pos['peak']) / pos['peak'] * 100
                else:
                    trail_pnl = (pos['peak'] - price) / pos['peak'] * 100
                if trail_pnl <= -cfg.stop_loss_pct * 0.5:
                    exit_reason = 'TRAIL'
            if exit_reason is None and self._all_conditions_met(cfg.exit_conditions, flat):
                exit_reason = 'SIGNAL'

            if exit_reason:
                pnl_usd = pos['size_usd'] * pnl_pct / 100
                self.balance += pos['size_usd'] + pnl_usd
                self.total_pnl += pnl_usd
                self.trades.append({
                    'entry': entry, 'exit': price, 'side': side,
                    'pnl': round(pnl_usd, 2), 'pnl_pct': round(pnl_pct, 2),
                    'reason': exit_reason,
                })
                self.markers.append({
                    'time': candle_time,
                    'position': 'aboveBar' if side == 'long' else 'belowBar',
                    'color': '#26a69a' if pnl_usd >= 0 else '#ef5350',
                    'shape': 'arrowDown' if side == 'long' else 'arrowUp',
                    'text': f'{exit_reason} {pnl_pct:+.1f}%',
                })
                self.position = None
                action = {'type': 'exit', 'reason': exit_reason, 'pnl': round(pnl_usd, 2)}

        # Save previous indicator values for cross detection
        self._prev_ind = dict(flat)
        return action

    def to_dict(self, price: float) -> Dict:
        """Serialize for frontend."""
        wins = [t for t in self.trades if t['pnl'] > 0]
        wr = len(wins) / len(self.trades) * 100 if self.trades else 0
        upnl = 0.0
        if self.position:
            p = self.position
            if p['side'] == 'long':
                upnl = p['size_usd'] * (price - p['entry']) / p['entry']
            else:
                upnl = p['size_usd'] * (p['entry'] - price) / p['entry']
        return {
            'id':         self.id,
            'name':       self.config.name,
            'enabled':    self.config.enabled,
            'side':       self.config.side,
            'timeframe':  self.config.timeframe,
            'balance':    round(self.balance, 2),
            'total_pnl':  round(self.total_pnl, 2),
            'upnl':       round(upnl, 2),
            'n_trades':   len(self.trades),
            'win_rate':   round(wr, 1),
            'in_position': self.position is not None,
            'position_side': self.position['side'] if self.position else None,
            'trades':     self.trades[-10:],  # last 10
            'config':     {
                'entry_conditions': self.config.entry_conditions,
                'exit_conditions':  self.config.exit_conditions,
                'take_profit_pct':  self.config.take_profit_pct,
                'stop_loss_pct':    self.config.stop_loss_pct,
                'trailing_stop':    self.config.trailing_stop,
                'size_usd':         self.config.size_usd,
            },
        }


# ── Built-in preset strategies ────────────────────────────────────────────────

PRESET_STRATEGIES = {
    'rsi_reversal': StrategyConfig(
        name='RSI Reversal',
        entry_conditions=[{'indicator': 'rsi14', 'operator': '<', 'value': 30}],
        exit_conditions=[{'indicator': 'rsi14', 'operator': '>', 'value': 70}],
        take_profit_pct=5.0, stop_loss_pct=3.0, side='long', timeframe='1m',
    ),
    'ema_crossover': StrategyConfig(
        name='EMA Crossover',
        entry_conditions=[{'indicator': 'ema9', 'operator': 'crosses_above', 'value': 0, 'compare_indicator': 'ema50'}],
        exit_conditions=[{'indicator': 'ema9', 'operator': 'crosses_below', 'value': 0, 'compare_indicator': 'ema50'}],
        take_profit_pct=8.0, stop_loss_pct=4.0, side='long', timeframe='1m',
    ),
    'bollinger_bounce': StrategyConfig(
        name='Bollinger Bounce',
        entry_conditions=[{'indicator': 'bb_lower', 'operator': '>', 'value': 0, 'compare_indicator': 'close'}],
        exit_conditions=[{'indicator': 'close', 'operator': '>', 'value': 0, 'compare_indicator': 'bb_upper'}],
        take_profit_pct=4.0, stop_loss_pct=2.5, side='long', timeframe='1m',
    ),
    'macd_signal': StrategyConfig(
        name='MACD Signal',
        entry_conditions=[{'indicator': 'macd_line', 'operator': 'crosses_above', 'value': 0, 'compare_indicator': 'macd_signal'}],
        exit_conditions=[{'indicator': 'macd_line', 'operator': 'crosses_below', 'value': 0, 'compare_indicator': 'macd_signal'}],
        take_profit_pct=6.0, stop_loss_pct=3.5, side='long', timeframe='1m',
    ),
}


class StrategyEngine:
    """Manages all user strategies."""

    def __init__(self) -> None:
        self.strategies: Dict[str, Strategy] = {}
        self._counter = 0

    def add(self, config: StrategyConfig) -> Strategy:
        self._counter += 1
        sid = f"strat_{self._counter}"
        s = Strategy(sid, config)
        self.strategies[sid] = s
        return s

    def remove(self, sid: str) -> bool:
        return self.strategies.pop(sid, None) is not None

    def toggle(self, sid: str) -> Optional[bool]:
        s = self.strategies.get(sid)
        if s:
            s.config.enabled = not s.config.enabled
            return s.config.enabled
        return None

    def evaluate_all(self, ind_last: Dict, price: float, candle_time: int,
                     tf: str) -> List[Dict]:
        """Evaluate all strategies for a given timeframe candle close."""
        events = []
        for sid, strat in self.strategies.items():
            if strat.config.timeframe != tf:
                continue
            if not strat.config.enabled:
                continue
            result = strat.evaluate(ind_last, price, candle_time)
            if result:
                result['strategy_id'] = sid
                result['strategy_name'] = strat.config.name
                events.append(result)
        return events

    def get_all_markers(self) -> List[Dict]:
        markers = []
        for s in self.strategies.values():
            markers.extend(s.markers)
        return markers

    def to_dict(self, price: float) -> List[Dict]:
        return [s.to_dict(price) for s in self.strategies.values()]

    def reset(self) -> None:
        self.strategies.clear()
        self._counter = 0


# ─── simulation manager ───────────────────────────────────────────────────────

def _random_price() -> float:
    """Log-uniform random price between $1 and $100 000 (crypto-like)."""
    return round(math.exp(random.uniform(math.log(1), math.log(100_000))), 2)


class SimulationManager:
    """Owns the simulator, all 7 aggregators, and the background thread."""

    def __init__(self) -> None:
        self.lock         = threading.Lock()
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()  # set = paused
        self._speed: Any  = 1                  # 1 / 10 / 100 / 1000 / "max"
        self._accum: float = 0.0               # fractional step accumulator

        self.sim: Optional[MarketSimulator]    = None
        self.aggs: Dict[str, OHLCVAggregator]  = {}
        self.base_vol: float                   = 0.0
        # ── trading state ──────────────────────────────────────────────────
        self.balance:      float        = INITIAL_BALANCE
        self.realized_pnl: float        = 0.0
        self.positions:    List[Position] = []
        self.orders:       List[Order]    = []
        # ── strategy engine ────────────────────────────────────────────────
        self.strategy_engine = StrategyEngine()
        self._new_sim(broadcast=False)

    # ── simulation control ────────────────────────────────────────────────────

    def _new_sim(self, broadcast: bool = True) -> None:
        with self.lock:
            price = _random_price()
            self.sim = MarketSimulator(
                initial_price=price,
                seed=None,
                initial_regime=random.choice(["bull", "bear", "low_vol"]),
                enable_regime=True,
                enable_stochastic=True,
                enable_jumps=True,
                enable_spread=True,
                fee_params=FeeParams(),
                jump_params=JumpParams(),
                spread_params=SpreadParams(),
            )
            self.base_vol      = price * 0.1
            self.aggs          = {tf: OHLCVAggregator(spc) for tf, spc in TIMEFRAMES}
            self._accum        = 0.0
            self.balance       = INITIAL_BALANCE
            self.realized_pnl  = 0.0
            self.positions     = []
            self.orders        = []
            self.strategy_engine.reset()
        if broadcast:
            socketio.emit("new_sim", {
                "price": price, "step": 0, "regime": self.sim.regime,
                "balance": INITIAL_BALANCE,
            }, namespace="/")

    def set_speed(self, speed: Any) -> None:
        with self.lock:
            self._speed  = speed
            self._accum  = 0.0

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    # ── background thread ─────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        socketio.start_background_task(self._loop)

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            interval_s = EMIT_MS / 1000.0
            start_t    = time.monotonic()

            if not self._pause_event.is_set():
                with self.lock:
                    speed     = self._speed
                    spf       = SPEED_MAP.get(speed, SPEED_MAP[1])
                    self._accum += spf
                    steps_now  = int(self._accum)
                    self._accum -= steps_now

                closed_by_tf: Dict[str, List[Dict]] = {tf: [] for tf, _ in TIMEFRAMES}
                tick_price: float = self.sim.price
                tick_step:  int   = self.sim.t
                tick_regime: str  = self.sim.regime

                filled_events: List[Dict] = []
                liq_events:    List[str]  = []

                for _ in range(max(0, steps_now)):
                    with self.lock:
                        old_price  = self.sim.price
                        self.sim.step()
                        new_price  = self.sim.price
                        cur_regime = self.sim.regime
                        cur_step   = self.sim.t
                        vol = _sim_volume(
                            self.base_vol,
                            (new_price - old_price) / max(old_price, 1e-9),
                            cur_regime,
                            self.sim.rng,
                        )
                        # ── check pending orders ──────────────────────────
                        for order in list(self.orders):
                            if order.should_trigger(new_price):
                                fp  = order.fill_price(new_price)
                                pos = Position(str(uuid.uuid4())[:8], order.side,
                                               fp, order.size_usd, order.leverage,
                                               order.type)
                                if pos.margin + pos.fee_paid <= self.balance:
                                    self.balance -= pos.margin + pos.fee_paid
                                    self.positions.append(pos)
                                    filled_events.append({
                                        "order_id": order.id,
                                        "position": pos.to_dict(new_price),
                                    })
                                self.orders.remove(order)
                        # ── check liquidations ────────────────────────────
                        for pos in list(self.positions):
                            if pos.is_liquidated(new_price):
                                liq_events.append(pos.id)
                                self.positions.remove(pos)

                    for tf, _ in TIMEFRAMES:
                        c = self.aggs[tf].push_v(cur_step, new_price, vol)
                        if c is not None:
                            closed_by_tf[tf].append(c)

                    tick_price  = new_price
                    tick_step   = cur_step
                    tick_regime = cur_regime

                if steps_now > 0:
                    # Emit candle closes per timeframe
                    strat_events_all = []
                    for tf, _ in TIMEFRAMES:
                        if closed_by_tf[tf]:
                            recent   = self.aggs[tf].ohlcv[-500:]
                            inds     = compute_indicators(recent) if len(recent) >= 2 else {}
                            ind_last = {k: v[-1] for k, v in inds.items() if v}
                            # ── evaluate strategies on candle close ────
                            last_candle = closed_by_tf[tf][-1]
                            candle_time = last_candle.get('time', 0)
                            # Add close price to ind_last for Bollinger comparison
                            ind_last['close'] = last_candle.get('close', tick_price)
                            se = self.strategy_engine.evaluate_all(
                                ind_last, tick_price, candle_time, tf)
                            strat_events_all.extend(se)
                            socketio.emit("candle_close", {
                                "tf":      tf,
                                "candles": closed_by_tf[tf],
                                "ind_last": ind_last,
                            }, namespace="/")

                    live_candles = {
                        tf: self.aggs[tf].current_ohlcv
                        for tf, _ in TIMEFRAMES
                    }
                    with self.lock:
                        pos_dicts  = [p.to_dict(tick_price) for p in self.positions]
                        ord_dicts  = [o.to_dict() for o in self.orders]
                        balance    = round(self.balance, 2)
                        rpnl       = round(self.realized_pnl, 2)
                        strat_data = self.strategy_engine.to_dict(tick_price)
                        strat_markers = self.strategy_engine.get_all_markers()
                    socketio.emit("tick", {
                        "step":      tick_step,
                        "price":     round(tick_price, 6),
                        "regime":    tick_regime,
                        "live":      live_candles,
                        "positions": pos_dicts,
                        "orders":    ord_dicts,
                        "balance":   balance,
                        "rpnl":      rpnl,
                        "events":    {"filled": filled_events, "liquidated": liq_events,
                                      "strategy": strat_events_all},
                        "strategies": strat_data,
                        "strat_markers": strat_markers[-50:],
                    }, namespace="/")

            elapsed = time.monotonic() - start_t
            sleep_t = max(0.0, interval_s - elapsed)
            socketio.sleep(sleep_t)

    # ── data for client ───────────────────────────────────────────────────────

    def get_tf_payload(self, tf: str) -> Dict:
        """Return full candle history + indicators for one timeframe."""
        agg = self.aggs.get(tf)
        if agg is None:
            return {}
        candles  = list(agg.ohlcv)
        current  = agg.current_ohlcv
        if current:
            candles.append(current)
        inds = compute_indicators(candles) if len(candles) >= 2 else {}
        return {
            "tf":         tf,
            "candles":    candles,
            "indicators": inds,
            "step":       self.sim.t if self.sim else 0,
            "price":      round(self.sim.price, 6) if self.sim else 0,
            "regime":     self.sim.regime if self.sim else "bull",
        }


# ─── Flask / SocketIO setup ───────────────────────────────────────────────────

app     = Flask(__name__)
app.config["SECRET_KEY"] = "synth-crypto-2024"
socketio = SocketIO(app, cors_allowed_origins="*",
                    async_mode="eventlet", logger=False, engineio_logger=False)

manager = SimulationManager()


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    payload = manager.get_tf_payload("1m")
    payload["paused"] = manager.is_paused()
    payload["speed"]  = manager.is_paused() and 1 or manager._speed
    emit("init", payload)


@socketio.on("switch_tf")
def on_switch_tf(data):
    tf = data.get("tf", "1m")
    emit("tf_data", manager.get_tf_payload(tf))


@socketio.on("set_speed")
def on_set_speed(data):
    speed = data.get("speed", 1)
    if speed == "max":
        manager.set_speed("max")
    else:
        manager.set_speed(int(speed))


@socketio.on("pause")
def on_pause(_=None):
    manager.pause()
    emit("paused", {}, broadcast=True, namespace="/")


@socketio.on("resume")
def on_resume(_=None):
    manager.resume()
    emit("resumed", {}, broadcast=True, namespace="/")


@socketio.on("new_sim")
def on_new_sim(_=None):
    manager._new_sim(broadcast=True)
    payload = manager.get_tf_payload("1m")
    emit("tf_data", payload, broadcast=True, namespace="/")


@socketio.on("place_order")
def on_place_order(data):
    order_type    = data.get("type", "market")
    side          = data.get("side", "long")
    size_usd      = float(data.get("size_usd",  100))
    leverage      = float(data.get("leverage",    1))
    trigger_price = data.get("trigger_price")
    limit_price   = data.get("limit_price")

    with manager.lock:
        cur_price = manager.sim.price if manager.sim else 0.0
        if order_type == "market":
            pos = Position(str(uuid.uuid4())[:8], side, cur_price,
                           size_usd, leverage, "market")
            if pos.margin + pos.fee_paid > manager.balance:
                emit("order_result", {"status": "error", "msg": "Insufficient balance"})
                return
            manager.balance -= pos.margin + pos.fee_paid
            manager.positions.append(pos)
            emit("order_result", {
                "status":   "filled",
                "position": pos.to_dict(cur_price),
                "balance":  round(manager.balance, 2),
            })
        else:
            if trigger_price is None:
                emit("order_result", {"status": "error", "msg": "Trigger price required"})
                return
            order = Order(
                str(uuid.uuid4())[:8], order_type, side, size_usd, leverage,
                float(trigger_price),
                float(limit_price) if limit_price is not None else None,
            )
            manager.orders.append(order)
            emit("order_result", {"status": "pending", "order": order.to_dict()})


@socketio.on("close_position")
def on_close_position(data):
    pos_id = data.get("id")
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            emit("order_result", {"status": "error", "msg": "Position not found"})
            return
        cur_price = manager.sim.price if manager.sim else pos.entry_price
        upnl      = pos.unrealized_pnl(cur_price)
        fee       = pos.size_usd * TAKER_FEE_RATE
        net       = upnl - fee
        manager.balance      += pos.margin + net
        manager.realized_pnl += net
        manager.positions.remove(pos)
        emit("order_result", {
            "status":  "closed",
            "pnl":     round(net, 2),
            "balance": round(manager.balance, 2),
        }, broadcast=True, namespace="/")


@socketio.on("cancel_order")
def on_cancel_order(data):
    ord_id = data.get("id")
    with manager.lock:
        order = next((o for o in manager.orders if o.id == ord_id), None)
        if order:
            manager.orders.remove(order)
            emit("order_result", {"status": "cancelled", "order_id": ord_id})


# ─── strategy SocketIO events ─────────────────────────────────────────────────

@socketio.on("add_strategy")
def on_add_strategy(data):
    preset = data.get("preset")
    if preset and preset in PRESET_STRATEGIES:
        import copy
        cfg = copy.deepcopy(PRESET_STRATEGIES[preset])
    else:
        cfg = StrategyConfig(
            name=data.get('name', 'Custom Strategy'),
            entry_conditions=data.get('entry_conditions', []),
            exit_conditions=data.get('exit_conditions', []),
            take_profit_pct=float(data.get('take_profit_pct', 5.0)),
            stop_loss_pct=float(data.get('stop_loss_pct', 3.0)),
            trailing_stop=bool(data.get('trailing_stop', False)),
            size_usd=float(data.get('size_usd', 500)),
            side=data.get('side', 'long'),
            timeframe=data.get('timeframe', '1m'),
        )
    s = manager.strategy_engine.add(cfg)
    price = manager.sim.price if manager.sim else 0
    emit("strategy_added", s.to_dict(price), broadcast=True, namespace="/")


@socketio.on("toggle_strategy")
def on_toggle_strategy(data):
    sid = data.get('id')
    new_state = manager.strategy_engine.toggle(sid)
    if new_state is not None:
        emit("strategy_toggled", {'id': sid, 'enabled': new_state},
             broadcast=True, namespace="/")


@socketio.on("remove_strategy")
def on_remove_strategy(data):
    sid = data.get('id')
    if manager.strategy_engine.remove(sid):
        emit("strategy_removed", {'id': sid}, broadcast=True, namespace="/")


@socketio.on("get_strategies")
def on_get_strategies(_=None):
    price = manager.sim.price if manager.sim else 0
    emit("strategies_list", {
        'strategies': manager.strategy_engine.to_dict(price),
        'presets': list(PRESET_STRATEGIES.keys()),
    })


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    manager.start()
    print("=" * 60)
    print("  Synthetic Crypto Market Simulator  (Web Interface)")
    print("  Open:  http://localhost:5000")
    print("  Stop:  Ctrl+C")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
