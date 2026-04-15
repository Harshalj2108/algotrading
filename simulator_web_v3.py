"""
simulator_web_v3.py
===================
Flask + Socket.IO web interface integrated with Phase 2 simulator engines.

Combines the TradingView-style live charting from simulator_web.py with all
Phase 2 modules from synthetic_market_simulator_v2.py:

    ✓ GARCH(1,1) Volatility Engine     – realistic vol clustering
    ✓ Volume Simulation Engine          – return/regime-aware volume
    ✓ Dynamic Slippage Model            – vol + size-dependent execution cost
    ✓ Correlated Asset Engine           – Cholesky-decomposed BTC/ETH
    ✓ Liquidation Cascade Engine        – forced liquidations amplify crashes
    ✓ Risk Metrics                      – Sharpe, drawdown, VaR, expectancy
    ✓ Stress Testing                    – spread/vol multipliers, toggleable

Usage
-----
    python simulator_web_v3.py
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

# ─── constants ────────────────────────────────────────────────────────────────
BASE_EPOCH  = calendar.timegm(time.strptime("2024-01-01", "%Y-%m-%d"))
MAX_CANDLES = 2_000
EMIT_MS     = 50             # 20 fps

TIMEFRAMES: List[Tuple[str, int]] = [
    ("1s",   1),
    ("1m",   60),
    ("5m",   300),
    ("15m",  900),
    ("30m",  1_800),
    ("1h",   3_600),
    ("1d",   86_400),
]

SPEED_MAP: Dict[Any, float] = {
    1:      EMIT_MS / 1000,
    10:     EMIT_MS * 10 / 1000,
    100:    EMIT_MS * 100 / 1000,
    1000:   EMIT_MS * 1000 / 1000,
    "max":  EMIT_MS * 20_000 / 1000,
}

# ─── trading constants ────────────────────────────────────────────────────────
INITIAL_BALANCE  = 10_000.0
MAINT_MARGIN     = 0.005
TAKER_FEE_RATE   = 0.0006
MAKER_FEE_RATE   = 0.0002


class Position:
    """Single open leveraged position."""
    def __init__(self, pos_id: str, side: str, entry: float,
                 size_usd: float, leverage: float,
                 order_type: str = "market",
                 slippage_cost: float = 0.0) -> None:
        self.id          = pos_id
        self.side        = side
        self.entry_price = entry
        self.size_usd    = size_usd
        self.leverage    = leverage
        self.margin      = size_usd / leverage
        self.qty         = size_usd / entry
        fee_rate         = MAKER_FEE_RATE if order_type == "limit" else TAKER_FEE_RATE
        self.fee_paid    = size_usd * fee_rate
        self.slippage_cost = slippage_cost
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
            "id":             self.id,
            "side":           self.side,
            "entry_price":    round(self.entry_price, 6),
            "size_usd":       round(self.size_usd, 2),
            "leverage":       self.leverage,
            "margin":         round(self.margin, 2),
            "qty":            round(self.qty, 6),
            "liq_price":      round(self.liq_price, 6),
            "upnl":           round(upnl, 2),
            "upnl_pct":       round(upnl_pct, 2),
            "fee_paid":       round(self.fee_paid, 4),
            "slippage_cost":  round(self.slippage_cost, 6),
        }


class Order:
    """Pending limit / stop / stop-limit order."""
    def __init__(self, ord_id: str, order_type: str, side: str,
                 size_usd: float, leverage: float,
                 trigger_price: float,
                 limit_price: Optional[float] = None) -> None:
        self.id            = ord_id
        self.type          = order_type
        self.side          = side
        self.size_usd      = size_usd
        self.leverage      = leverage
        self.trigger_price = trigger_price
        self.limit_price   = limit_price

    def should_trigger(self, price: float) -> bool:
        if self.type == "limit":
            return price <= self.trigger_price if self.side == "long" else price >= self.trigger_price
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
    out = _nan(len(arr))
    k = 2.0 / (period + 1)
    for i, v in enumerate(arr):
        if np.isnan(v):
            continue
        if np.isnan(out[max(0, i-1)]):
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


def ind_sma(closes, period):
    out = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        out[i] = closes[i - period + 1:i + 1].mean()
    return out

def ind_ema(closes, period):
    return _ema_raw(closes, period)

def ind_wma(closes, period):
    weights = np.arange(1, period + 1, dtype=float)
    out = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        out[i] = np.dot(closes[i - period + 1:i + 1], weights) / weights.sum()
    return out

def ind_vwap(highs, lows, closes, volumes):
    tp  = (highs + lows + closes) / 3.0
    cv  = np.cumsum(tp * volumes)
    cvol = np.cumsum(volumes)
    return cv / np.where(cvol > 0, cvol, 1.0)

def ind_bollinger(closes, period=20, num_std=2.0):
    mid = ind_sma(closes, period)
    std = _nan(len(closes))
    for i in range(period - 1, len(closes)):
        std[i] = closes[i - period + 1:i + 1].std()
    return mid + num_std * std, mid, mid - num_std * std

def ind_atr(highs, lows, closes, period=14):
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

def ind_rsi(closes, period=14):
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

def ind_cci(highs, lows, closes, period=20):
    n  = len(closes)
    tp = (highs + lows + closes) / 3.0
    out = _nan(n)
    for i in range(period - 1, n):
        sl = tp[i - period + 1:i + 1]
        m  = sl.mean()
        md = np.abs(sl - m).mean()
        out[i] = (tp[i] - m) / (0.015 * md) if md > 0 else 0.0
    return out

def ind_williams_r(highs, lows, closes, period=14):
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

def ind_obv(closes, volumes):
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

def ind_cmf(highs, lows, closes, volumes, period=20):
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
    tk = mid_hl(highs, lows, tenkan)
    kj = mid_hl(highs, lows, kijun)
    sa = (tk + kj) / 2
    sb = mid_hl(highs, lows, senkou_b)
    ck = _nan(n)
    for i in range(chikou_offset, n):
        ck[i - chikou_offset] = closes[i]
    return tk, kj, sa, sb, ck


# ─── candle storage (OHLCV + step index) ─────────────────────────────────────

class OHLCVAggregator(CandleAggregator):
    """Extends CandleAggregator with volume tracking."""

    def __init__(self, steps_per_candle: int) -> None:
        super().__init__(steps_per_candle)
        self.ohlcv: List[Dict] = []
        self._vol_acc: float = 0.0

    def push_v(self, step: int, price: float, volume: float) -> Optional[Dict]:
        result_tuple = self.push(step, price)
        self._vol_acc += volume
        if result_tuple is not None:
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
    if len(candles) < 2:
        return {}
    times   = np.array([c["time"]   for c in candles])
    opens   = np.array([c["open"]   for c in candles], dtype=float)
    highs   = np.array([c["high"]   for c in candles], dtype=float)
    lows    = np.array([c["low"]    for c in candles], dtype=float)
    closes  = np.array([c["close"]  for c in candles], dtype=float)
    volumes = np.array([c["volume"] for c in candles], dtype=float)

    def _series(values):
        out = []
        for t, v in zip(times, values):
            if not np.isnan(v):
                out.append({"time": int(t), "value": round(float(v), 6)})
        return out

    def _hist_series(values):
        out = []
        for t, v in zip(times, values):
            if not np.isnan(v):
                color = "#26a69a" if v >= 0 else "#ef5350"
                out.append({"time": int(t), "value": round(float(v), 6), "color": color})
        return out

    sma20  = ind_sma(closes, 20); sma50  = ind_sma(closes, 50); sma200 = ind_sma(closes, 200)
    ema9   = ind_ema(closes, 9);  ema20  = ind_ema(closes, 20); ema50  = ind_ema(closes, 50)
    wma20  = ind_wma(closes, 20); vwap   = ind_vwap(highs, lows, closes, volumes)
    tk, kj, sa, sb, ck = ind_ichimoku(highs, lows, closes)
    bb_u, bb_m, bb_l = ind_bollinger(closes, 20, 2.0)
    atr14 = ind_atr(highs, lows, closes, 14)
    kc_u, kc_m, kc_l = ind_keltner(highs, lows, closes, 20, 2.0)
    rsi14 = ind_rsi(closes, 14)
    stk, std = ind_stochastic(highs, lows, closes, 14, 3)
    cci20  = ind_cci(highs, lows, closes, 20)
    wr14   = ind_williams_r(highs, lows, closes, 14)
    macd_l, macd_s, macd_h = ind_macd(closes, 12, 26, 9)
    obv    = ind_obv(closes, volumes)
    cmf20  = ind_cmf(highs, lows, closes, volumes, 20)

    vol_bars = []
    for t, v, o, c_ in zip(times, volumes, opens, closes):
        col = "#26a69a80" if c_ >= o else "#ef535080"
        vol_bars.append({"time": int(t), "value": round(float(v), 2), "color": col})

    return {
        "sma20":   _series(sma20),  "sma50":   _series(sma50),  "sma200":  _series(sma200),
        "ema9":    _series(ema9),   "ema20":   _series(ema20),  "ema50":   _series(ema50),
        "wma20":   _series(wma20),  "vwap":    _series(vwap),
        "ichi_tenkan": _series(tk), "ichi_kijun": _series(kj),
        "ichi_span_a": _series(sa), "ichi_span_b": _series(sb), "ichi_chikou": _series(ck),
        "bb_upper": _series(bb_u),  "bb_middle": _series(bb_m), "bb_lower": _series(bb_l),
        "atr14": _series(atr14),
        "kc_upper": _series(kc_u),  "kc_middle": _series(kc_m), "kc_lower": _series(kc_l),
        "rsi14":   _series(rsi14),  "stoch_k": _series(stk),    "stoch_d": _series(std),
        "cci20":   _series(cci20),  "williams_r": _series(wr14),
        "macd_line":  _series(macd_l), "macd_signal": _series(macd_s), "macd_hist": _hist_series(macd_h),
        "obv":     _series(obv),    "cmf20": _series(cmf20),    "volume": vol_bars,
    }


# ─── VETS Strategy ────────────────────────────────────────────────────────────

class VETSStrategy:
    """Volatility Expansion Trend Swing — vol expansion breakouts aligned with macro trend.

    Rules
    ─────
    • Long only when EMA50 > EMA200 ; Short only when EMA50 < EMA200
    • BB-width percentile < 30  AND  ATR below median  →  compression
    • Entry: close crosses outside BB + volume > 1.2× 20-bar avg
    • SL 1.5×ATR  |  TP 3.0×ATR  |  Trailing exit on EMA20 cross
    • Risk 1 % of capital per trade ; max 1 concurrent position ; 1× leverage
    """

    def __init__(self, capital: float = 10_000.0):
        self.enabled = False
        self.initial_capital = capital
        self.capital = capital
        # parameters
        self.ema_fast = 50
        self.ema_slow = 200
        self.ema_exit = 20
        self.atr_period = 14
        self.bb_period = 20
        self.bb_std = 2.0
        self.pctile_lookback = 100
        self.vol_lookback = 20
        self.vol_mult = 1.2
        self.risk_pct = 0.01
        self.sl_mult = 1.5
        self.tp_mult = 3.0
        # runtime
        self.pos: Optional[Dict] = None
        self.trades: List[Dict] = []
        self.signals: List[Dict] = []
        self.equity: List[float] = []
        self.peak = capital
        self.max_dd = 0.0
        self.min_candles = 220

    def reset(self, capital: float = 10_000.0):
        was = self.enabled
        self.__init__(capital)
        self.enabled = was

    # ── percentile helpers ────────────────────────────────────────────────────

    def _bb_width_pctile(self, closes: np.ndarray) -> Optional[float]:
        n = len(closes)
        if n < self.bb_period + self.pctile_lookback:
            return None
        upper, mid, lower = ind_bollinger(closes, self.bb_period, self.bb_std)
        widths: List[Optional[float]] = []
        for u, m, lo in zip(upper, mid, lower):
            uf, mf, lf = float(u), float(m), float(lo)
            if not np.isnan(mf) and mf > 0 and not np.isnan(uf) and not np.isnan(lf):
                widths.append((uf - lf) / mf)
            else:
                widths.append(None)
        if not widths or widths[-1] is None:
            return None
        cur = widths[-1]
        recent = [w for w in widths[-self.pctile_lookback:] if w is not None]
        if len(recent) < 10:
            return None
        return sum(1 for w in recent if w <= cur) / len(recent) * 100

    def _pctile(self, vals, lookback: int = 100) -> Optional[float]:
        # vals may be a numpy array or list
        if vals is None or len(vals) == 0:
            return None
        last_v = float(vals[-1])
        if np.isnan(last_v):
            return None
        recent = vals[-lookback:] if len(vals) >= lookback else vals
        valid = [float(v) for v in recent if not np.isnan(float(v))]
        if len(valid) < 10:
            return None
        cur = last_v
        return sum(1 for v in valid if v <= cur) / len(valid) * 100

    # ── candle handler ────────────────────────────────────────────────────────

    def on_candle(self, candles: List[Dict], price: float, step: int) -> List[Dict]:
        """Process one completed candle.  Returns list of action dicts."""
        if not self.enabled or len(candles) < self.min_candles:
            return []
        actions: List[Dict] = []
        closes = np.array([c['close'] for c in candles])
        highs  = np.array([c['high']  for c in candles])
        lows   = np.array([c['low']   for c in candles])
        vols   = np.array([c.get('volume', 0.0) for c in candles])

        e50_arr  = ind_ema(closes, self.ema_fast)
        e200_arr = ind_ema(closes, self.ema_slow)
        e20_arr  = ind_ema(closes, self.ema_exit)
        atr_arr  = ind_atr(highs, lows, closes, self.atr_period)
        bb_u_arr, _, bb_l_arr = ind_bollinger(closes, self.bb_period, self.bb_std)

        def _last(arr):
            if arr is None or len(arr) == 0:
                return None
            v = float(arr[-1])
            return None if np.isnan(v) else v

        e50  = _last(e50_arr)
        e200 = _last(e200_arr)
        e20  = _last(e20_arr)
        atr  = _last(atr_arr)
        bbu  = _last(bb_u_arr)
        bbl  = _last(bb_l_arr)

        if any(v is None for v in (e50, e200, e20, atr, bbu, bbl)):
            return actions

        cl = float(closes[-1])
        ts = candles[-1].get('time', step)

        # ── CHECK EXITS ──────────────────────────────────────────────────────
        if self.pos is not None:
            p = self.pos
            reason = ep = None
            if p['side'] == 'long':
                if cl <= p['sl']:    reason, ep = 'stop_loss', p['sl']
                elif cl >= p['tp']:  reason, ep = 'take_profit', p['tp']
                elif cl < e20:       reason, ep = 'ema20_trail', cl
            else:
                if cl >= p['sl']:    reason, ep = 'stop_loss', p['sl']
                elif cl <= p['tp']:  reason, ep = 'take_profit', p['tp']
                elif cl > e20:       reason, ep = 'ema20_trail', cl

            if reason:
                if p['side'] == 'long':
                    pnl = (ep - p['entry']) / p['entry'] * p['size']
                else:
                    pnl = (p['entry'] - ep) / p['entry'] * p['size']
                r_mult = pnl / p['risk_amt'] if p['risk_amt'] else 0.0
                trade = dict(side=p['side'], entry=p['entry'], exit=ep,
                             pnl=pnl, size=p['size'], reason=reason,
                             entry_step=p['entry_step'], exit_step=step,
                             r_mult=r_mult,
                             regime='bull' if e50 > e200 else 'bear',
                             mae=p.get('mae', 0), mfe=p.get('mfe', 0))
                self.capital += pnl
                self.trades.append(trade)
                sig = dict(time=ts, price=round(ep, 6), type='exit',
                           side=p['side'], reason=reason, pnl=round(pnl, 2))
                self.signals.append(sig)
                actions.append(dict(action='close', trade=trade, signal=sig))
                self.pos = None
                self.equity.append(self.capital)
                if self.capital > self.peak:
                    self.peak = self.capital
                dd = (self.peak - self.capital) / self.peak * 100
                if dd > self.max_dd:
                    self.max_dd = dd

        # ── CHECK ENTRIES (only when flat) ────────────────────────────────────
        if self.pos is None:
            bullish = e50 > e200
            bearish = e50 < e200
            bb_pct  = self._bb_width_pctile(closes)
            atr_pct = self._pctile(atr_arr, self.pctile_lookback)
            compressed = (bb_pct is not None and atr_pct is not None
                          and bb_pct < 30 and atr_pct < 50)
            vol_ok = True
            if len(vols) >= self.vol_lookback:
                va = float(np.mean(vols[-self.vol_lookback:]))
                if va > 0:
                    vol_ok = float(vols[-1]) > self.vol_mult * va

            if compressed and vol_ok:
                side = None
                if bullish and cl > bbu:
                    side = 'long'
                elif bearish and cl < bbl:
                    side = 'short'
                if side:
                    stop_d   = self.sl_mult * atr
                    risk_amt = self.capital * self.risk_pct
                    size     = risk_amt / (stop_d / cl) if stop_d > 0 else 0.0
                    size     = min(size, self.capital * 0.95)
                    if size > 10:
                        if side == 'long':
                            sl, tp = cl - stop_d, cl + self.tp_mult * atr
                        else:
                            sl, tp = cl + stop_d, cl - self.tp_mult * atr
                        self.pos = dict(side=side, entry=cl, size=size,
                                        sl=sl, tp=tp, entry_step=step,
                                        risk_amt=risk_amt, mae=0.0, mfe=0.0)
                        sig = dict(time=ts, price=round(cl, 6), type='entry',
                                   side=side, sl=round(sl, 6), tp=round(tp, 6))
                        self.signals.append(sig)
                        actions.append(dict(action='open', signal=sig,
                                            size=round(size, 2)))

        # ── UPDATE MAE / MFE ─────────────────────────────────────────────────
        if self.pos:
            p = self.pos
            if p['side'] == 'long':
                u = (cl - p['entry']) / p['entry']
            else:
                u = (p['entry'] - cl) / p['entry']
            p['mae'] = min(p['mae'], u)
            p['mfe'] = max(p['mfe'], u)

        return actions

    # ── metrics ───────────────────────────────────────────────────────────────

    def metrics(self) -> Dict:
        """Comprehensive strategy metrics."""
        base: Dict = dict(
            total_trades=len(self.trades), capital=round(self.capital, 2),
            net_pnl=round(self.capital - self.initial_capital, 2),
            in_position=self.pos is not None,
            pos_side=self.pos['side'] if self.pos else None,
            enabled=self.enabled,
        )
        if not self.trades:
            return base
        pnls   = [t['pnl'] for t in self.trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        std_p  = float(np.std(pnls)) if len(pnls) > 1 else 1.0
        base.update(dict(
            net_pnl_pct   = round((self.capital - self.initial_capital) / self.initial_capital * 100, 2),
            win_rate      = round(len(wins) / len(pnls) * 100, 1),
            avg_win       = round(float(np.mean(wins)), 2) if wins else 0,
            avg_loss      = round(float(np.mean(losses)), 2) if losses else 0,
            profit_factor = round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else 999.99,
            sharpe        = round(float(np.mean(pnls)) / std_p * np.sqrt(252), 2) if std_p > 0 else 0,
            max_dd        = round(self.max_dd, 2),
            avg_r         = round(float(np.mean([t.get('r_mult', 0) for t in self.trades])), 2),
            avg_mae_pct   = round(float(np.mean([t.get('mae', 0) for t in self.trades])) * 100, 2),
            avg_mfe_pct   = round(float(np.mean([t.get('mfe', 0) for t in self.trades])) * 100, 2),
            bull_n        = sum(1 for t in self.trades if t.get('regime') == 'bull'),
            bull_pnl      = round(sum(t['pnl'] for t in self.trades if t.get('regime') == 'bull'), 2),
            bear_n        = sum(1 for t in self.trades if t.get('regime') == 'bear'),
            bear_pnl      = round(sum(t['pnl'] for t in self.trades if t.get('regime') == 'bear'), 2),
        ))
        reasons: Dict[str, Dict] = {}
        for t in self.trades:
            r = t.get('reason', '?')
            reasons.setdefault(r, dict(n=0, pnl=0.0))
            reasons[r]['n'] += 1
            reasons[r]['pnl'] = round(reasons[r]['pnl'] + t['pnl'], 2)
        base['exit_reasons'] = reasons
        return base


# ─── simulation manager (Phase 2 integrated) ─────────────────────────────────

def _random_price() -> float:
    return round(math.exp(random.uniform(math.log(1), math.log(100_000))), 2)


class SimulationManager:
    """
    Owns the Phase2MarketSimulator, all 7 OHLCV aggregators,
    the correlated-asset tracker, and the background thread.
    """

    def __init__(self) -> None:
        self.lock         = threading.Lock()
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._speed: Any  = 1
        self._accum: float = 0.0

        self.p2sim: Optional[Phase2MarketSimulator] = None
        self.aggs:  Dict[str, OHLCVAggregator]     = {}
        # ── trading state ──
        self.balance:      float          = INITIAL_BALANCE
        self.realized_pnl: float          = 0.0
        self.positions:    List[Position] = []
        self.orders:       List[Order]    = []
        self.trade_pnls:   List[float]    = []   # closed trade PnL for risk metrics
        # ── VETS strategy ──
        self.strategy = VETSStrategy(INITIAL_BALANCE)
        # ── stress test (applied on next new_sim) ──
        self.stress_cfg = StressTestConfig()
        # ── Phase 2 config flags ──
        self.p2_flags = {
            "garch":   True,
            "volume":  True,
            "slippage": True,
            "corr":    True,
            "cascade": True,
        }
        self._new_sim(broadcast=False)

    # ── simulation control ────────────────────────────────────────────────────

    def _new_sim(self, broadcast: bool = True) -> None:
        with self.lock:
            price = _random_price()
            cfg = Phase2Config(
                enable_garch_volatility    = self.p2_flags["garch"],
                enable_volume_model        = self.p2_flags["volume"],
                enable_slippage_model      = self.p2_flags["slippage"],
                enable_correlated_assets   = self.p2_flags["corr"],
                enable_liquidation_cascade = self.p2_flags["cascade"],
                n_assets        = 2,
                asset_names     = ["BTC", "ETH"],
                asset_vol_scalars = [1.0, 1.4],
                correlation_matrix = [[1.0, 0.6], [0.6, 1.0]],
                volume_base     = price * 0.1,
                seed            = None,
            )
            self.p2sim = Phase2MarketSimulator(
                initial_price=price,
                cfg=cfg,
                stress=self.stress_cfg if self.stress_cfg.enabled else StressTestConfig(),
            )
            # If correlated engine exists, set secondary prices proportionally
            if self.p2sim.corr_engine is not None:
                self.p2sim.corr_engine.initialise_prices(price, secondary_ratio=0.06)
                # Re-init corr_prices tracking
                self.p2sim.corr_prices = [[p] for p in self.p2sim.corr_engine.prices]

            self.aggs          = {tf: OHLCVAggregator(spc) for tf, spc in TIMEFRAMES}
            self._accum        = 0.0
            self.balance       = INITIAL_BALANCE
            self.realized_pnl  = 0.0
            self.positions     = []
            self.orders        = []
            self.trade_pnls    = []
            self.strategy.reset(INITIAL_BALANCE)
        if broadcast:
            p2_info = self._p2_status()
            socketio.emit("new_sim", {
                "price": price, "step": 0,
                "regime": self.p2sim.regime,
                "balance": INITIAL_BALANCE,
                "p2": p2_info,
            }, namespace="/")

    def _p2_status(self) -> Dict:
        """Return current Phase 2 engine status for the frontend."""
        sim = self.p2sim
        if sim is None:
            return {}
        result = {
            "garch_enabled":   sim.garch is not None,
            "volume_enabled":  sim.volume_engine is not None,
            "slippage_enabled": sim.slippage is not None,
            "corr_enabled":    sim.corr_engine is not None,
            "cascade_enabled": sim.cascade is not None,
            "garch_sigma":     round(sim.sigmas[-1] * 100, 4) if sim.sigmas else 0,
            "garch_lr":        round(sim.garch.long_run_sigma() * 100, 4) if sim.garch else 0,
            "volume":          round(sim.volumes[-1], 1) if sim.volumes else 0,
            "cascade_count":   sim.n_cascade_events,
            "oi":              round(sim.cascade.open_interest, 0) if sim.cascade else 0,
            "corr_prices":     {},
            "stress_enabled":  self.stress_cfg.enabled,
        }
        if sim.corr_engine is not None and sim.corr_prices:
            for i, name in enumerate(sim.cfg.asset_names[:sim.cfg.n_assets]):
                if i < len(sim.corr_prices) and sim.corr_prices[i]:
                    result["corr_prices"][name] = round(sim.corr_prices[i][-1], 2)
        return result

    def _slippage_exec_price(self, mid: float, side: str, size_usd: float) -> Tuple[float, float]:
        """Get execution price through slippage model. Returns (exec_price, slippage_cost)."""
        sim = self.p2sim
        if sim is None:
            return mid, 0.0
        if sim.slippage is not None:
            sigma  = sim.sigmas[-1] if sim.sigmas else 0.0008
            spread = sim._p1.spread_params.base_spread
            jumped = sim.jumps[-1] if sim.jumps else False
            exec_p = sim.slippage.compute(mid, "buy" if side == "long" else "sell",
                                           size_usd, spread, sigma, jumped)
            slip_cost = abs(exec_p - mid)
            return exec_p, slip_cost
        return mid, 0.0

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
                tick_price: float = self.p2sim.price
                tick_step:  int   = self.p2sim.t
                tick_regime: str  = self.p2sim.regime

                filled_events: List[Dict] = []
                liq_events:    List[str]  = []
                cascade_fired: bool = False

                for _ in range(max(0, steps_now)):
                    with self.lock:
                        old_price = self.p2sim.price

                        # ── Phase 2 step (GARCH + vol + cascade all integrated) ──
                        new_price = self.p2sim.step()

                        cur_regime = self.p2sim.regime
                        cur_step   = self.p2sim.t

                        # Volume from Phase 2 engine (or fallback)
                        vol = self.p2sim.volumes[-1] if self.p2sim.volumes else 0.0

                        # Track cascade events
                        if self.p2sim.cascades and self.p2sim.cascades[-1]:
                            cascade_fired = True

                        # ── check pending orders (with slippage) ──
                        for order in list(self.orders):
                            if order.should_trigger(new_price):
                                base_fp = order.fill_price(new_price)
                                # Apply slippage to fill price
                                fp, slip_cost = self._slippage_exec_price(
                                    base_fp, order.side, order.size_usd)
                                pos = Position(
                                    str(uuid.uuid4())[:8], order.side,
                                    fp, order.size_usd, order.leverage,
                                    order.type, slip_cost)
                                if pos.margin + pos.fee_paid <= self.balance:
                                    self.balance -= pos.margin + pos.fee_paid
                                    self.positions.append(pos)
                                    filled_events.append({
                                        "order_id": order.id,
                                        "position": pos.to_dict(new_price),
                                    })
                                self.orders.remove(order)

                        # ── check liquidations ──
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
                    for tf, _ in TIMEFRAMES:
                        if closed_by_tf[tf]:
                            recent   = self.aggs[tf].ohlcv[-500:]
                            inds     = compute_indicators(recent) if len(recent) >= 2 else {}
                            ind_last = {k: v[-1] for k, v in inds.items() if v}
                            socketio.emit("candle_close", {
                                "tf":      tf,
                                "candles": closed_by_tf[tf],
                                "ind_last": ind_last,
                            }, namespace="/")

                    # ── VETS Strategy on 1m candle close ──
                    if self.strategy.enabled and closed_by_tf.get("1m"):
                        _sc = list(self.aggs["1m"].ohlcv)
                        _sa = self.strategy.on_candle(_sc, tick_price, tick_step)
                        if _sa:
                            socketio.emit("strategy_update", {
                                "actions": _sa,
                                "metrics": self.strategy.metrics(),
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

                    # Phase 2 engine data for the frontend
                    p2_tick = {
                        "sigma":     round(self.p2sim.sigmas[-1] * 100, 4) if self.p2sim.sigmas else 0,
                        "volume":    round(vol, 1),
                        "cascade":   cascade_fired,
                        "cascade_n": self.p2sim.n_cascade_events,
                        "oi":        round(self.p2sim.cascade.open_interest, 0) if self.p2sim.cascade else 0,
                        "jumped":    self.p2sim.jumps[-1] if self.p2sim.jumps else False,
                    }
                    # Correlated asset prices
                    if self.p2sim.corr_engine and self.p2sim.corr_prices:
                        cp = {}
                        for i, name in enumerate(self.p2sim.cfg.asset_names[:self.p2sim.cfg.n_assets]):
                            if i < len(self.p2sim.corr_prices) and self.p2sim.corr_prices[i]:
                                cp[name] = round(self.p2sim.corr_prices[i][-1], 2)
                        p2_tick["corr_prices"] = cp

                    socketio.emit("tick", {
                        "step":      tick_step,
                        "price":     round(tick_price, 6),
                        "regime":    tick_regime,
                        "live":      live_candles,
                        "positions": pos_dicts,
                        "orders":    ord_dicts,
                        "balance":   balance,
                        "rpnl":      rpnl,
                        "events":    {"filled": filled_events, "liquidated": liq_events},
                        "p2":        p2_tick,
                    }, namespace="/")

            elapsed = time.monotonic() - start_t
            sleep_t = max(0.0, interval_s - elapsed)
            socketio.sleep(sleep_t)

    # ── data for client ───────────────────────────────────────────────────────

    def get_tf_payload(self, tf: str) -> Dict:
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
            "step":       self.p2sim.t if self.p2sim else 0,
            "price":      round(self.p2sim.price, 6) if self.p2sim else 0,
            "regime":     self.p2sim.regime if self.p2sim else "bull",
            "p2":         self._p2_status(),
            "strategy":   {"metrics": self.strategy.metrics(),
                           "signals": self.strategy.signals[-200:]},
        }

    def compute_risk_metrics(self) -> Dict:
        """Compute risk metrics from current simulation state."""
        if self.p2sim is None or len(self.p2sim.prices) < 2:
            return {}
        prices = np.array(self.p2sim.prices)
        return RiskMetrics.full_report(prices, self.trade_pnls,
                                        initial_equity=INITIAL_BALANCE,
                                        print_report=False)


# ─── Flask / SocketIO setup ───────────────────────────────────────────────────

app     = Flask(__name__)
app.config["SECRET_KEY"] = "synth-crypto-v3-2024"
socketio = SocketIO(app, cors_allowed_origins="*",
                    async_mode="eventlet", logger=False, engineio_logger=False)

manager = SimulationManager()


@app.route("/")
def index():
    return render_template("index_v3.html")


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
        cur_price = manager.p2sim.price if manager.p2sim else 0.0
        if order_type == "market":
            # Apply slippage model for market orders
            exec_price, slip_cost = manager._slippage_exec_price(
                cur_price, side, size_usd)
            pos = Position(str(uuid.uuid4())[:8], side, exec_price,
                           size_usd, leverage, "market", slip_cost)
            if pos.margin + pos.fee_paid > manager.balance:
                emit("order_result", {"status": "error", "msg": "Insufficient balance"})
                return
            manager.balance -= pos.margin + pos.fee_paid
            manager.positions.append(pos)
            emit("order_result", {
                "status":   "filled",
                "position": pos.to_dict(cur_price),
                "balance":  round(manager.balance, 2),
                "slippage": round(slip_cost, 6),
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
        cur_price = manager.p2sim.price if manager.p2sim else pos.entry_price
        # Apply slippage on close too
        close_price, slip_cost = manager._slippage_exec_price(
            cur_price, "short" if pos.side == "long" else "long", pos.size_usd)
        if pos.side == "long":
            upnl = (close_price - pos.entry_price) / pos.entry_price * pos.size_usd
        else:
            upnl = (pos.entry_price - close_price) / pos.entry_price * pos.size_usd
        fee   = pos.size_usd * TAKER_FEE_RATE
        net   = upnl - fee
        manager.balance      += pos.margin + net
        manager.realized_pnl += net
        manager.trade_pnls.append(net)
        manager.positions.remove(pos)
        emit("order_result", {
            "status":   "closed",
            "pnl":      round(net, 2),
            "balance":  round(manager.balance, 2),
            "slippage": round(slip_cost, 6),
        }, broadcast=True, namespace="/")


@socketio.on("cancel_order")
def on_cancel_order(data):
    ord_id = data.get("id")
    with manager.lock:
        order = next((o for o in manager.orders if o.id == ord_id), None)
        if order:
            manager.orders.remove(order)
            emit("order_result", {"status": "cancelled", "order_id": ord_id})


# ─── Phase 2 specific socket events ──────────────────────────────────────────

@socketio.on("get_risk_metrics")
def on_risk_metrics(_=None):
    metrics = manager.compute_risk_metrics()
    emit("risk_metrics", metrics)


@socketio.on("set_stress")
def on_set_stress(data):
    """Configure stress test for next new_sim."""
    enabled   = data.get("enabled", False)
    spread_m  = float(data.get("spread_mult", 1.0))
    vol_m     = float(data.get("vol_mult", 1.0))
    latency   = int(data.get("latency", 0))
    with manager.lock:
        manager.stress_cfg = StressTestConfig(
            spread_multiplier  = spread_m,
            vol_multiplier     = vol_m,
            latency_steps      = latency,
            enabled            = enabled,
        )
    emit("stress_updated", {
        "enabled":     enabled,
        "spread_mult": spread_m,
        "vol_mult":    vol_m,
        "latency":     latency,
    })


@socketio.on("set_p2_flags")
def on_set_p2_flags(data):
    """Toggle Phase 2 engines for next new_sim."""
    with manager.lock:
        for key in ("garch", "volume", "slippage", "corr", "cascade"):
            if key in data:
                manager.p2_flags[key] = bool(data[key])
    emit("p2_flags_updated", manager.p2_flags)


# ─── VETS Strategy events ─────────────────────────────────────────────────────

@socketio.on("toggle_strategy")
def on_toggle_strategy(data):
    enabled = bool(data.get("enabled", False))
    with manager.lock:
        manager.strategy.enabled = enabled
    emit("strategy_toggled", {
        "enabled": enabled,
        "metrics": manager.strategy.metrics(),
        "signals": manager.strategy.signals[-200:],
    }, broadcast=True, namespace="/")


@socketio.on("get_strategy_metrics")
def on_get_strategy_metrics(_=None):
    emit("strategy_metrics", manager.strategy.metrics())


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    manager.start()
    print("=" * 60)
    print("  SynthCrypto v3 — Phase 2 Integrated Web Simulator")
    print("  Engines: GARCH · Volume · Slippage · Correlation · Cascade")
    print("  Open:  http://localhost:5000")
    print("  Stop:  Ctrl+C")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
