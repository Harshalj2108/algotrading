"""
simulator_core.py
=================
Pure simulation logic — zero web-framework dependencies.

Contains:
  • Indicator library (SMA / EMA / WMA / VWAP / BB / ATR / ADX / KC /
                       RSI / Stochastic / CCI / Williams%R / MACD /
                       OBV / CMF / Ichimoku)
  • Position / Order models
  • OHLCVAggregator
  • EMABBScalper strategy
  • DynamicStrategy — load & run user-pasted Python strategies at runtime
  • SimulationManager — owns the Phase-2 engine + background loop
"""

from __future__ import annotations

import ast
import csv
import math
import os
import random
import textwrap
import threading
import time
import calendar
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

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
EMIT_MS     = 50          # 20 fps target

TIMEFRAMES: List[Tuple[str, int]] = [
    ("1s",  1),
    ("1m",  60),
    ("5m",  300),
    ("15m", 900),
    ("30m", 1_800),
    ("1h",  3_600),
    ("1d",  86_400),
]

SPEED_MAP: Dict[Any, float] = {
    1:      EMIT_MS / 1000,
    10:     EMIT_MS * 10 / 1000,
    100:    EMIT_MS * 100 / 1000,
    1000:   EMIT_MS * 1000 / 1000,
    "max":  EMIT_MS * 20_000 / 1000,
}

INITIAL_BALANCE = 10_000.0
MAINT_MARGIN    = 0.005
TAKER_FEE_RATE  = 0.0006
MAKER_FEE_RATE  = 0.0002


# ─── indicator helpers (pure numpy) ───────────────────────────────────────────

def _nan(n: int) -> np.ndarray:
    a = np.empty(n); a[:] = np.nan; return a


def _ema_raw(arr: np.ndarray, period: int) -> np.ndarray:
    out = _nan(len(arr))
    k = 2.0 / (period + 1)
    for i, v in enumerate(arr):
        if np.isnan(v):
            continue
        if np.isnan(out[max(0, i - 1)]):
            s = 0.0; cnt = 0
            for j in range(i, min(i + period, len(arr))):
                if not np.isnan(arr[j]):
                    s += arr[j]; cnt += 1
                if cnt == period:
                    out[j] = s / period
                    for jj in range(j + 1, len(arr)):
                        if not np.isnan(arr[jj]):
                            out[jj] = arr[jj] * k + out[jj - 1] * (1 - k)
                    return out
            break
        else:
            out[i] = v * k + out[i - 1] * (1 - k)
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
    tp   = (highs + lows + closes) / 3.0
    cv   = np.cumsum(tp * volumes)
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
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i]  - closes[i - 1]))
    tr[0] = highs[0] - lows[0]
    out = _nan(n)
    if n >= period:
        out[period - 1] = tr[1:period + 1].mean()
        k = 1.0 / period
        for i in range(period, n):
            out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def ind_adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period * 2:
        return _nan(n)
    up_move   = np.diff(highs,  prepend=highs[0])
    down_move = -np.diff(lows,  prepend=lows[0])
    plus_dm   = np.where((up_move > down_move)   & (up_move > 0),   up_move,   0.0)
    minus_dm  = np.where((down_move > up_move)   & (down_move > 0), down_move, 0.0)
    atr       = ind_atr(highs, lows, closes, period)
    pds = _nan(n); mds = _nan(n)
    if n >= period:
        pds[period - 1] = plus_dm[1:period + 1].mean()
        mds[period - 1] = minus_dm[1:period + 1].mean()
        k = 1.0 / period
        for i in range(period, n):
            pds[i] = plus_dm[i]  * k + pds[i - 1] * (1 - k)
            mds[i] = minus_dm[i] * k + mds[i - 1] * (1 - k)
    safe_atr = np.where((atr > 0) & ~np.isnan(atr), atr, np.nan)
    pdi  = 100.0 * pds / safe_atr
    mdi  = 100.0 * mds / safe_atr
    dsum = pdi + mdi
    dsum = np.where(dsum == 0, np.nan, dsum)
    dx   = 100.0 * np.abs(pdi - mdi) / dsum
    adx  = _nan(n)
    start = None
    last_i = 0
    for i in range(n):
        if not np.isnan(dx[i]):
            if start is None:
                start = i
            if i - start >= period - 1:
                adx[i]  = np.nanmean(dx[start:i + 1][-period:])
                last_i  = i
                break
    if start is not None:
        for i in range(last_i + 1, n):
            if not np.isnan(dx[i]):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


def ind_keltner(highs, lows, closes, period=20, mult=2.0):
    mid = ind_ema(closes, period)
    atr = ind_atr(highs, lows, closes, period)
    return mid + mult * atr, mid, mid - mult * atr


def ind_rsi(closes, period=14):
    n = len(closes)
    out = _nan(n)
    if n < period + 1:
        return out
    delta  = np.diff(closes.astype(float))
    gain   = np.where(delta > 0,  delta,  0.0)
    loss   = np.where(delta < 0, -delta,  0.0)
    avg_g  = gain[:period].mean()
    avg_l  = loss[:period].mean()
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gain[i - 1]) / period
        avg_l = (avg_l * (period - 1) + loss[i - 1]) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    return out


def ind_stochastic(highs, lows, closes, k_period=14, d_period=3):
    n = len(closes)
    k = _nan(n)
    for i in range(k_period - 1, n):
        lo  = lows[i - k_period + 1:i + 1].min()
        hi  = highs[i - k_period + 1:i + 1].max()
        rng = hi - lo
        k[i] = 100.0 * (closes[i] - lo) / rng if rng > 0 else 50.0
    return k, ind_sma(k, d_period)


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
    n   = len(closes)
    out = _nan(n)
    for i in range(period - 1, n):
        hi  = highs[i - period + 1:i + 1].max()
        lo  = lows[i - period + 1:i + 1].min()
        rng = hi - lo
        out[i] = -100.0 * (hi - closes[i]) / rng if rng > 0 else -50.0
    return out


def ind_macd(closes, fast=12, slow=26, signal=9):
    line = _ema_raw(closes, fast) - _ema_raw(closes, slow)
    sig  = _ema_raw(line, signal)
    return line, sig, line - sig


def ind_obv(closes, volumes):
    n   = len(closes)
    out = np.zeros(n)
    out[0] = volumes[0]
    for i in range(1, n):
        out[i] = out[i - 1] + (volumes[i] if closes[i] > closes[i - 1]
                                else -volumes[i] if closes[i] < closes[i - 1]
                                else 0)
    return out


def ind_cmf(highs, lows, closes, volumes, period=20):
    n   = len(closes)
    rng = highs - lows
    with np.errstate(invalid="ignore", divide="ignore"):
        mfm = np.where(rng > 0, ((closes - lows) - (highs - closes)) / rng, 0.0)
    mfv = mfm * volumes
    out = _nan(n)
    for i in range(period - 1, n):
        sv  = volumes[i - period + 1:i + 1].sum()
        out[i] = mfv[i - period + 1:i + 1].sum() / sv if sv > 0 else 0.0
    return out


def ind_ichimoku(highs, lows, closes, tenkan=9, kijun=26, senkou_b=52, chikou_offset=26):
    n = len(closes)
    def mid_hl(h, l, p):
        out = _nan(n)
        for i in range(p - 1, n):
            out[i] = (h[i - p + 1:i + 1].max() + l[i - p + 1:i + 1].min()) / 2
        return out
    tk = mid_hl(highs, lows, tenkan)
    kj = mid_hl(highs, lows, kijun)
    sa = (tk + kj) / 2
    sb = mid_hl(highs, lows, senkou_b)
    ck = _nan(n)
    for i in range(chikou_offset, n):
        ck[i - chikou_offset] = closes[i]
    return tk, kj, sa, sb, ck


# ─── compute_indicators ───────────────────────────────────────────────────────

def compute_indicators(candles: List[Dict]) -> Dict:
    if len(candles) < 2:
        return {}
    times   = np.array([c["time"]   for c in candles])
    opens   = np.array([c["open"]   for c in candles], dtype=float)
    highs   = np.array([c["high"]   for c in candles], dtype=float)
    lows    = np.array([c["low"]    for c in candles], dtype=float)
    closes  = np.array([c["close"]  for c in candles], dtype=float)
    volumes = np.array([c["volume"] for c in candles], dtype=float)

    def _s(values):
        return [{"time": int(t), "value": round(float(v), 6)}
                for t, v in zip(times, values) if not np.isnan(v)]

    def _h(values):
        return [{"time": int(t), "value": round(float(v), 6),
                 "color": "#26a69a" if v >= 0 else "#ef5350"}
                for t, v in zip(times, values) if not np.isnan(v)]

    sma20, sma50, sma200 = ind_sma(closes, 20), ind_sma(closes, 50), ind_sma(closes, 200)
    ema9, ema20, ema50   = ind_ema(closes, 9),  ind_ema(closes, 20), ind_ema(closes, 50)
    wma20  = ind_wma(closes, 20)
    vwap   = ind_vwap(highs, lows, closes, volumes)
    tk, kj, sa, sb, ck  = ind_ichimoku(highs, lows, closes)
    bb_u, bb_m, bb_l    = ind_bollinger(closes, 20, 2.0)
    atr14  = ind_atr(highs, lows, closes, 14)
    kc_u, kc_m, kc_l    = ind_keltner(highs, lows, closes, 20, 2.0)
    rsi14  = ind_rsi(closes, 14)
    stk, std = ind_stochastic(highs, lows, closes, 14, 3)
    cci20  = ind_cci(highs, lows, closes, 20)
    wr14   = ind_williams_r(highs, lows, closes, 14)
    ml, ms, mh = ind_macd(closes, 12, 26, 9)
    obv    = ind_obv(closes, volumes)
    cmf20  = ind_cmf(highs, lows, closes, volumes, 20)

    vol_bars = [
        {"time": int(t), "value": round(float(v), 2),
         "color": "#26a69a80" if c_ >= o else "#ef535080"}
        for t, v, o, c_ in zip(times, volumes, opens, closes)
    ]

    return {
        "sma20": _s(sma20), "sma50": _s(sma50), "sma200": _s(sma200),
        "ema9":  _s(ema9),  "ema20": _s(ema20),  "ema50":  _s(ema50),
        "wma20": _s(wma20), "vwap":  _s(vwap),
        "ichi_tenkan": _s(tk),  "ichi_kijun":  _s(kj),
        "ichi_span_a": _s(sa),  "ichi_span_b": _s(sb), "ichi_chikou": _s(ck),
        "bb_upper": _s(bb_u),   "bb_middle":   _s(bb_m), "bb_lower":  _s(bb_l),
        "atr14":   _s(atr14),
        "kc_upper": _s(kc_u),  "kc_middle":   _s(kc_m), "kc_lower":  _s(kc_l),
        "rsi14":    _s(rsi14), "stoch_k":      _s(stk),  "stoch_d":   _s(std),
        "cci20":    _s(cci20), "williams_r":   _s(wr14),
        "macd_line": _s(ml),   "macd_signal":  _s(ms),   "macd_hist": _h(mh),
        "obv":      _s(obv),   "cmf20":        _s(cmf20), "volume":   vol_bars,
    }


# ─── OHLCV aggregator ─────────────────────────────────────────────────────────

class OHLCVAggregator(CandleAggregator):
    def __init__(self, steps_per_candle: int) -> None:
        super().__init__(steps_per_candle)
        self.ohlcv:    List[Dict] = []
        self._vol_acc: float      = 0.0

    def push_v(self, step: int, price: float, volume: float) -> Optional[Dict]:
        result = self.push(step, price)
        self._vol_acc += volume
        if result is not None:
            s, o, h, l, c = result
            candle = {
                "time":   BASE_EPOCH + s,
                "open":   round(o, 6), "high":   round(h, 6),
                "low":    round(l, 6), "close":  round(c, 6),
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
            "open":   round(o, 6),   "high":  round(h, 6),
            "low":    round(l, 6),   "close": round(cl, 6),
            "volume": round(self._vol_acc, 2),
        }


# ─── Position / Order ─────────────────────────────────────────────────────────

def validate_tpsl(side: str, entry_price: float,
                  tp_price: Optional[float] = None,
                  sl_price: Optional[float] = None) -> Optional[str]:
    if tp_price is not None and not math.isfinite(tp_price):
        return "Take Profit must be a valid price"
    if sl_price is not None and not math.isfinite(sl_price):
        return "Stop Loss must be a valid price"
    if tp_price is not None and tp_price <= 0:
        return "Take Profit must be greater than zero"
    if sl_price is not None and sl_price <= 0:
        return "Stop Loss must be greater than zero"

    if side == "long":
        if tp_price is not None and tp_price <= entry_price:
            return "Long Take Profit must be above entry"
        if sl_price is not None and sl_price >= entry_price:
            return "Long Stop Loss must be below entry"
    else:
        if tp_price is not None and tp_price >= entry_price:
            return "Short Take Profit must be below entry"
        if sl_price is not None and sl_price <= entry_price:
            return "Short Stop Loss must be above entry"
    return None


class Position:
    def __init__(self, pos_id: str, side: str, entry: float,
                 size_usd: float, leverage: float,
                 order_type: str = "market",
                 slippage_cost: float = 0.0,
                 tp_price: Optional[float] = None,
                 sl_price: Optional[float] = None) -> None:
        self.id            = pos_id
        self.side          = side
        self.entry_price   = entry
        self.size_usd      = size_usd
        self.leverage      = leverage
        self.margin        = size_usd / leverage
        self.qty           = size_usd / entry
        fee_rate           = MAKER_FEE_RATE if order_type == "limit" else TAKER_FEE_RATE
        self.fee_paid      = size_usd * fee_rate
        self.slippage_cost = slippage_cost
        self.tp_price      = tp_price
        self.sl_price      = sl_price
        if side == "long":
            self.liq_price = entry * (1 - 1 / leverage + MAINT_MARGIN)
        else:
            self.liq_price = entry * (1 + 1 / leverage - MAINT_MARGIN)

    def set_tpsl(self, tp_price: Optional[float] = None,
                 sl_price: Optional[float] = None) -> Optional[str]:
        err = validate_tpsl(self.side, self.entry_price, tp_price, sl_price)
        if err:
            return err
        self.tp_price = tp_price
        self.sl_price = sl_price
        return None

    def unrealized_pnl(self, price: float) -> float:
        if self.side == "long":
            return (price - self.entry_price) / self.entry_price * self.size_usd
        return (self.entry_price - price) / self.entry_price * self.size_usd

    def tpsl_trigger(self, price: float) -> Optional[Tuple[str, float]]:
        if self.side == "long":
            if self.tp_price is not None and price >= self.tp_price:
                return "take_profit", self.tp_price
            if self.sl_price is not None and price <= self.sl_price:
                return "stop_loss", self.sl_price
        else:
            if self.tp_price is not None and price <= self.tp_price:
                return "take_profit", self.tp_price
            if self.sl_price is not None and price >= self.sl_price:
                return "stop_loss", self.sl_price
        return None

    def is_liquidated(self, price: float) -> bool:
        return price <= self.liq_price if self.side == "long" else price >= self.liq_price

    def to_dict(self, price: float) -> Dict:
        upnl     = self.unrealized_pnl(price)
        upnl_pct = upnl / self.margin * 100 if self.margin else 0
        return {
            "id":            self.id,
            "side":          self.side,
            "entry_price":   round(self.entry_price, 6),
            "size_usd":      round(self.size_usd, 2),
            "leverage":      self.leverage,
            "margin":        round(self.margin, 2),
            "qty":           round(self.qty, 6),
            "liq_price":     round(self.liq_price, 6),
            "tp_price":      round(self.tp_price, 6) if self.tp_price is not None else None,
            "sl_price":      round(self.sl_price, 6) if self.sl_price is not None else None,
            "upnl":          round(upnl, 2),
            "upnl_pct":      round(upnl_pct, 2),
            "fee_paid":      round(self.fee_paid, 4),
            "slippage_cost": round(self.slippage_cost, 6),
        }


class Order:
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
        if self.type == "limit":  return self.trigger_price
        if self.type == "stop":   return price
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


# ─── VETS Strategy ────────────────────────────────────────────────────────────

class VETSStrategy:
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

    def on_candle(self, candles: List[Dict], price: float, step: int) -> List[Dict]:
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

        e50  = _last(e50_arr); e200 = _last(e200_arr); e20  = _last(e20_arr)
        atr  = _last(atr_arr); bbu  = _last(bb_u_arr); bbl  = _last(bb_l_arr)

        if any(v is None for v in (e50, e200, e20, atr, bbu, bbl)):
            return actions

        cl = float(closes[-1])
        ts = candles[-1].get('time', step)

        if self.pos is not None:
            p = self.pos; reason = ep = None
            if p['side'] == 'long':
                if cl <= p['sl']:    reason, ep = 'stop_loss', p['sl']
                elif cl >= p['tp']:  reason, ep = 'take_profit', p['tp']
                elif cl < e20:       reason, ep = 'ema20_trail', cl
            else:
                if cl >= p['sl']:    reason, ep = 'stop_loss', p['sl']
                elif cl <= p['tp']:  reason, ep = 'take_profit', p['tp']
                elif cl > e20:       reason, ep = 'ema20_trail', cl

            if reason:
                if p['side'] == 'long': pnl = (ep - p['entry']) / p['entry'] * p['size']
                else: pnl = (p['entry'] - ep) / p['entry'] * p['size']
                r_mult = pnl / p['risk_amt'] if p['risk_amt'] else 0.0
                trade = dict(side=p['side'], entry=p['entry'], exit=ep,
                             pnl=pnl, size=p['size'], reason=reason,
                             entry_step=p['entry_step'], exit_step=step,
                             r_mult=r_mult, regime='bull' if e50 > e200 else 'bear',
                             mae=p.get('mae', 0), mfe=p.get('mfe', 0))
                self.capital += pnl
                self.trades.append(trade)
                sig = dict(time=ts, price=round(ep, 6), type='exit',
                           side=p['side'], reason=reason, pnl=round(pnl, 2))
                self.signals.append(sig)
                actions.append(dict(action='close', trade=trade, signal=sig))
                self.pos = None
                self.equity.append(self.capital)
                if self.capital > self.peak: self.peak = self.capital
                dd = (self.peak - self.capital) / self.peak * 100
                if dd > self.max_dd: self.max_dd = dd

        if self.pos is None:
            bullish = e50 > e200; bearish = e50 < e200
            bb_pct  = self._bb_width_pctile(closes)
            atr_pct = self._pctile(atr_arr, self.pctile_lookback)
            compressed = (bb_pct is not None and atr_pct is not None and bb_pct < 30 and atr_pct < 50)
            vol_ok = True
            if len(vols) >= self.vol_lookback:
                va = float(np.mean(vols[-self.vol_lookback:]))
                if va > 0: vol_ok = float(vols[-1]) > self.vol_mult * va

            if compressed and vol_ok:
                side = None
                if bullish and cl > bbu: side = 'long'
                elif bearish and cl < bbl: side = 'short'
                if side:
                    stop_d   = self.sl_mult * atr
                    risk_amt = self.capital * self.risk_pct
                    size     = risk_amt / (stop_d / cl) if stop_d > 0 else 0.0
                    size     = min(size, self.capital * 0.95)
                    if size > 10:
                        if side == 'long': sl, tp = cl - stop_d, cl + self.tp_mult * atr
                        else: sl, tp = cl + stop_d, cl - self.tp_mult * atr
                        self.pos = dict(side=side, entry=cl, size=size, sl=sl, tp=tp, entry_step=step,
                                        risk_amt=risk_amt, mae=0.0, mfe=0.0)
                        sig = dict(time=ts, price=round(cl, 6), type='entry', side=side, sl=round(sl, 6), tp=round(tp, 6))
                        self.signals.append(sig)
                        actions.append(dict(action='open', signal=sig, size=round(size, 2)))

        if self.pos:
            p = self.pos
            if p['side'] == 'long': u = (cl - p['entry']) / p['entry']
            else: u = (p['entry'] - cl) / p['entry']
            p['mae'] = min(p['mae'], u)
            p['mfe'] = max(p['mfe'], u)
        return actions

    def metrics(self) -> Dict:
        base: Dict = dict(
            total_trades=len(self.trades), capital=round(self.capital, 2),
            net_pnl=round(self.capital - self.initial_capital, 2),
            in_position=self.pos is not None, pos_side=self.pos['side'] if self.pos else None, enabled=self.enabled,
        )
        if not self.trades: return base
        pnls   = [t['pnl'] for t in self.trades]; wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
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
            reasons[r]['n'] += 1; reasons[r]['pnl'] = round(reasons[r]['pnl'] + t['pnl'], 2)
        base['exit_reasons'] = reasons
        return base

# ─── EMA BB Scalper (only built-in strategy; user strategies loaded dynamically) ──


class EMABBScalper:
    """EMA + Bollinger Band scalper v2 — trend-aligned with adaptive filters."""

    FEE_RATE    = 0.0005
    MAX_DD_LIMIT = 0.10

    def __init__(self, capital: float = 10_000.0):
        self.enabled          = False
        self.initial_capital  = capital
        self.capital          = capital
        self.fast_ema         = 30
        self.slow_ema         = 50
        self.trend_ema        = 200
        self.bb_length        = 15
        self.bb_std           = 1.5
        self.atr_period       = 14
        self.adx_period       = 14
        self.adx_min          = 22
        self.bb_width_min_pct = 0.015
        self.cross_lookback   = 3
        self.sl_coeff         = 2.0
        self.tp_rr_ratio      = 1.5
        self.risk_pct         = 0.01
        self.cooldown_bars_after_sl  = 3
        self.max_consecutive_losses  = 3
        self.pos:              Optional[Dict] = None
        self.trades:           List[Dict]     = []
        self.signals:          List[Dict]     = []
        self.equity:           List[float]    = []
        self.peak              = capital
        self.max_dd            = 0.0
        self.dd_stopped        = False
        self.cooldown_remaining    = 0
        self.consecutive_losses    = 0
        self.loss_breaker_wait     = 0
        self.min_candles       = self.trend_ema + 10

    def reset(self, capital: float = 10_000.0):
        was = self.enabled
        self.__init__(capital)
        self.enabled = was

    def on_candle(self, candles: List[Dict], price: float, step: int) -> List[Dict]:
        if not self.enabled or len(candles) < self.min_candles or self.dd_stopped:
            return []
        actions: List[Dict] = []
        closes = np.array([c["close"] for c in candles], dtype=float)
        highs  = np.array([c["high"]  for c in candles], dtype=float)
        lows   = np.array([c["low"]   for c in candles], dtype=float)

        e_fast_arr  = ind_ema(closes, self.fast_ema)
        e_slow_arr  = ind_ema(closes, self.slow_ema)
        e_trend_arr = ind_ema(closes, self.trend_ema)
        atr_arr     = ind_atr(highs, lows, closes, self.atr_period)
        adx_arr     = ind_adx(highs, lows, closes, self.adx_period)
        bb_u_arr, bb_m_arr, bb_l_arr = ind_bollinger(closes, self.bb_length, self.bb_std)

        def _last(arr, offset=0):
            idx = -(1 + offset)
            if arr is None or len(arr) < (1 + offset):
                return None
            v = float(arr[idx])
            return None if np.isnan(v) else v

        e_fast  = _last(e_fast_arr);  e_slow  = _last(e_slow_arr)
        e_trend = _last(e_trend_arr); atr     = _last(atr_arr)
        adx_val = _last(adx_arr);     bbu     = _last(bb_u_arr)
        bbm     = _last(bb_m_arr);    bbl     = _last(bb_l_arr)

        if any(v is None for v in (e_fast, e_slow, e_trend, atr, bbu, bbl, bbm)):
            return actions

        cl = float(closes[-1])
        ts = candles[-1].get("time", step)
        bb_width = (bbu - bbl) / bbm if bbm > 0 else 0

        cross_signal = 0
        for lag in range(self.cross_lookback):
            ef = _last(e_fast_arr, lag);    es = _last(e_slow_arr, lag)
            ef_p = _last(e_fast_arr, lag+1); es_p = _last(e_slow_arr, lag+1)
            if ef is None or es is None or ef_p is None or es_p is None:
                continue
            if ef > es and ef_p <= es_p:   cross_signal =  1; break
            if ef < es and ef_p >= es_p:   cross_signal = -1; break

        # ── exits ──
        if self.pos is not None:
            p = self.pos; reason = ep = None
            if p["side"] == "long":
                if cl <= p["sl"]:   reason, ep = "stop_loss",   p["sl"]
                elif cl >= p["tp"]: reason, ep = "take_profit",  p["tp"]
            else:
                if cl >= p["sl"]:   reason, ep = "stop_loss",   p["sl"]
                elif cl <= p["tp"]: reason, ep = "take_profit",  p["tp"]
            if reason:
                pnl = ((ep - p["entry"]) / p["entry"] * p["size"] if p["side"] == "long"
                       else (p["entry"] - ep) / p["entry"] * p["size"])
                fee = p["size"] * self.FEE_RATE
                pnl -= fee
                r_mult = pnl / p["risk_amt"] if p["risk_amt"] else 0.0
                trade = dict(side=p["side"], entry=p["entry"], exit=ep,
                             pnl=pnl, size=p["size"], reason=reason,
                             entry_step=p["entry_step"], exit_step=step,
                             r_mult=r_mult, fee=fee,
                             mae=p.get("mae", 0), mfe=p.get("mfe", 0))
                self.capital += pnl
                self.trades.append(trade)
                sig = dict(time=ts, price=round(ep, 6), type="exit",
                           side=p["side"], reason=reason, pnl=round(pnl, 2))
                self.signals.append(sig)
                actions.append(dict(action="close", trade=trade, signal=sig))
                self.pos = None
                self.equity.append(self.capital)
                if self.capital > self.peak:
                    self.peak = self.capital
                dd = (self.peak - self.capital) / self.peak
                if dd * 100 > self.max_dd: self.max_dd = dd * 100
                if dd >= self.MAX_DD_LIMIT: self.dd_stopped = True
                if reason == "stop_loss":
                    self.cooldown_remaining = self.cooldown_bars_after_sl
                    self.consecutive_losses += 1
                else:
                    self.consecutive_losses = 0

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            return actions

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.loss_breaker_wait += 1
            if self.loss_breaker_wait >= self.bb_length:
                self.consecutive_losses = 0; self.loss_breaker_wait = 0
            else:
                return actions

        # ── entries ──
        if self.pos is None and not self.dd_stopped:
            if adx_val is not None and adx_val < self.adx_min:
                return actions
            if bb_width < self.bb_width_min_pct:
                return actions
            side = None
            if e_fast > e_slow and cl < bbl and cl > e_trend:
                side = "long"
            elif e_fast < e_slow and cl > bbu and cl < e_trend:
                side = "short"
            if side:
                stop_d   = self.sl_coeff * atr
                risk_amt = self.capital * self.risk_pct
                size     = min(risk_amt / (stop_d / cl) if stop_d > 0 else 0, self.capital * 0.95)
                entry_fee = size * self.FEE_RATE
                if size > 10 and (size + entry_fee) <= self.capital:
                    sl = cl - stop_d if side == "long" else cl + stop_d
                    tp = cl + stop_d * self.tp_rr_ratio if side == "long" else cl - stop_d * self.tp_rr_ratio
                    self.pos = dict(side=side, entry=cl, size=size,
                                    sl=sl, tp=tp, entry_step=step,
                                    risk_amt=risk_amt, mae=0.0, mfe=0.0)
                    sig = dict(time=ts, price=round(cl, 6), type="entry",
                               side=side, sl=round(sl, 6), tp=round(tp, 6))
                    self.signals.append(sig)
                    actions.append(dict(action="open", signal=sig, size=round(size, 2)))

        # ── trailing stop + MAE/MFE ──
        if self.pos:
            p = self.pos
            if p["side"] == "long":
                u = (cl - p["entry"]) / p["entry"]
                if cl > p["entry"]:
                    p["sl"] = max(p["sl"], p["entry"] + (cl - p["entry"]) * 0.75)
            else:
                u = (p["entry"] - cl) / p["entry"]
                if cl < p["entry"]:
                    p["sl"] = min(p["sl"], p["entry"] - (p["entry"] - cl) * 0.75)
            p["mae"] = min(p.get("mae", 0), u)
            p["mfe"] = max(p.get("mfe", 0), u)

        return actions

    def metrics(self) -> Dict:
        base: Dict = dict(
            total_trades=len(self.trades), capital=round(self.capital, 2),
            net_pnl=round(self.capital - self.initial_capital, 2),
            in_position=self.pos is not None,
            pos_side=self.pos["side"] if self.pos else None,
            enabled=self.enabled, dd_stopped=self.dd_stopped,
        )
        if not self.trades:
            return base
        pnls   = [t["pnl"] for t in self.trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        std_p  = float(np.std(pnls)) if len(pnls) > 1 else 1.0
        base.update(dict(
            net_pnl_pct   = round((self.capital - self.initial_capital) / self.initial_capital * 100, 2),
            win_rate      = round(len(wins) / len(pnls) * 100, 1),
            avg_win       = round(float(np.mean(wins)), 2)   if wins   else 0,
            avg_loss      = round(float(np.mean(losses)), 2) if losses else 0,
            profit_factor = round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else 999.99,
            sharpe        = round(float(np.mean(pnls)) / std_p * np.sqrt(252), 2) if std_p > 0 else 0,
            max_dd        = round(self.max_dd, 2),
            avg_r         = round(float(np.mean([t.get("r_mult", 0) for t in self.trades])), 2),
            avg_mae_pct   = round(float(np.mean([t.get("mae", 0) for t in self.trades])) * 100, 2),
            avg_mfe_pct   = round(float(np.mean([t.get("mfe", 0) for t in self.trades])) * 100, 2),
            total_fees    = round(sum(t.get("fee", 0) for t in self.trades), 2),
        ))
        reasons: Dict[str, Dict] = {}
        for t in self.trades:
            r = t.get("reason", "?")
            reasons.setdefault(r, dict(n=0, pnl=0.0))
            reasons[r]["n"]   += 1
            reasons[r]["pnl"]  = round(reasons[r]["pnl"] + t["pnl"], 2)
        base["exit_reasons"] = reasons
        return base


# ─── Dynamic Strategy Loader ──────────────────────────────────────────────────

# Allowed top-level imports inside user strategies.
_ALLOWED_IMPORTS = frozenset({"numpy", "np", "math", "random", "typing"})

# Banned AST node types (no file I/O, no subprocess, no __import__ tricks).
_BANNED_NODES = (
    ast.Import, ast.ImportFrom,          # caught separately below
    ast.Global, ast.Nonlocal,
)

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "enumerate": enumerate, "float": float,
    "int": int, "isinstance": isinstance, "len": len,
    "list": list, "max": max, "min": min, "print": print,
    "range": range, "round": round, "set": set, "str": str,
    "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "None": None, "True": True, "False": False,
}

# Indicator functions exposed to user strategies.
_INDICATOR_NS = {
    "ind_sma":        ind_sma,
    "ind_ema":        ind_ema,
    "ind_wma":        ind_wma,
    "ind_vwap":       ind_vwap,
    "ind_bollinger":  ind_bollinger,
    "ind_atr":        ind_atr,
    "ind_adx":        ind_adx,
    "ind_keltner":    ind_keltner,
    "ind_rsi":        ind_rsi,
    "ind_stochastic": ind_stochastic,
    "ind_cci":        ind_cci,
    "ind_williams_r": ind_williams_r,
    "ind_macd":       ind_macd,
    "ind_obv":        ind_obv,
    "ind_cmf":        ind_cmf,
    "ind_ichimoku":   ind_ichimoku,
}


class StrategyValidationError(Exception):
    pass


def _validate_ast(source: str) -> None:
    """
    Light static-analysis pass.  Raises StrategyValidationError for obvious
    security violations before we exec() anything.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise StrategyValidationError(f"Syntax error: {e}") from e

    for node in ast.walk(tree):
        # Block dangerous builtins by name
        if isinstance(node, ast.Name) and node.id in (
            "__import__", "eval", "exec", "compile",
            "open", "breakpoint", "globals", "locals",
            "vars", "getattr", "setattr", "delattr",
        ):
            raise StrategyValidationError(
                f"Forbidden name: '{node.id}' is not allowed in strategy code.")

        # Allow only numpy / math / random / typing imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names]
                     if isinstance(node, ast.Import)
                     else [node.module or ""])
            for name in names:
                root = (name or "").split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    raise StrategyValidationError(
                        f"Import '{name}' is not allowed.  "
                        f"Permitted: numpy, math, random, typing.  "
                        f"All indicator functions are pre-imported.")


def load_strategy(source: str, capital: float = 10_000.0) -> "DynamicStrategy":
    """
    Compile and instantiate a user-provided strategy class.

    Contract the class must satisfy:
      • Named 'Strategy'  (exactly)
      • __init__(self, capital: float)
      • on_candle(self, candles, price, step) → list[dict]
           Each dict must have at least {'action': 'open'|'close'}
      • metrics(self) → dict
      • reset(self, capital: float)

    Returns a DynamicStrategy wrapper.
    """
    _validate_ast(source)

    ns: Dict = {
        "__builtins__": _SAFE_BUILTINS,
        "np": __import__("numpy"),
        "numpy": __import__("numpy"),
        "math": __import__("math"),
        "random": __import__("random"),
        **_INDICATOR_NS,
    }

    try:
        exec(compile(source, "<strategy>", "exec"), ns)          # noqa: S102
    except Exception as e:
        raise StrategyValidationError(f"Runtime error during load: {e}") from e

    cls = ns.get("Strategy")
    if cls is None:
        raise StrategyValidationError(
            "No class named 'Strategy' found.  "
            "Your top-level class must be named exactly 'Strategy'.")
    if not callable(cls):
        raise StrategyValidationError("'Strategy' is not a class.")

    for method in ("on_candle", "metrics", "reset"):
        if not callable(getattr(cls, method, None)):
            raise StrategyValidationError(
                f"Strategy class must implement a '{method}' method.")

    try:
        instance = cls(capital)
    except Exception as e:
        raise StrategyValidationError(f"Strategy.__init__ raised: {e}") from e

    return DynamicStrategy(instance, source, capital)


class DynamicStrategy:
    """
    Thin wrapper around a user-loaded strategy instance.
    Provides the same interface as EMABBScalper so SimulationManager
    can treat them identically.
    """

    def __init__(self, instance: Any, source: str, capital: float) -> None:
        self._inst    = instance
        self.source   = source
        self.capital  = capital
        self.enabled  = False
        self.error:   Optional[str] = None
        self.name:    str = type(instance).__name__
        # cache signals for the API
        self._signals: List[Dict] = []

    def reset(self, capital: float = 10_000.0) -> None:
        was = self.enabled
        try:
            self._inst.reset(capital)
        except Exception as e:
            self.error = str(e)
        self.capital  = capital
        self.enabled  = was
        self._signals = []

    def on_candle(self, candles: List[Dict], price: float, step: int) -> List[Dict]:
        if not self.enabled:
            return []
        try:
            actions = self._inst.on_candle(candles, price, step)
            if not isinstance(actions, list):
                actions = []
            # harvest signals
            for a in actions:
                sig = a.get("signal")
                if sig:
                    self._signals.append(sig)
                    if len(self._signals) > 500:
                        self._signals = self._signals[-500:]
            self.error = None
            return actions
        except Exception:
            self.error = traceback.format_exc(limit=5)
            return []

    def metrics(self) -> Dict:
        try:
            m = self._inst.metrics()
            if not isinstance(m, dict):
                m = {}
        except Exception as e:
            m = {}
            self.error = str(e)
        m["enabled"] = self.enabled
        m["name"]    = self.name
        if self.error:
            m["error"] = self.error
        return m

    @property
    def signals(self) -> List[Dict]:
        try:
            return list(self._inst.signals)
        except AttributeError:
            return self._signals


# ─── Simulation Manager ───────────────────────────────────────────────────────

def _random_price() -> float:
    return round(math.exp(random.uniform(math.log(1), math.log(100_000))), 2)


class SimulationManager:
    """
    Owns the Phase-2 engine, all OHLCV aggregators, and the background loop.
    The emit_fn callback is injected by the API layer so this class stays
    framework-agnostic.
    """

    def __init__(self, emit_fn: Callable) -> None:
        self._emit        = emit_fn          # emit_fn(event_name, data)
        self.lock         = threading.Lock()
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._speed: Any  = 1
        self._accum: float = 0.0

        self.p2sim: Optional[Phase2MarketSimulator] = None
        self.aggs:  Dict[str, OHLCVAggregator]      = {}

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # trading state
        self.balance:      float          = INITIAL_BALANCE
        self.realized_pnl: float          = 0.0
        self.positions:    List[Position] = []
        self.orders:       List[Order]    = []
        self.trade_pnls:   List[float]    = []

        # strategies
        self.strategy:         VETSStrategy              = VETSStrategy(INITIAL_BALANCE)
        self.ebb_strategy:     EMABBScalper              = EMABBScalper(INITIAL_BALANCE)
        self.dynamic_strategy: Optional[DynamicStrategy] = None

        # Phase-2 config
        self.stress_cfg = StressTestConfig()
        self.p2_flags   = dict(garch=True, volume=True, slippage=True,
                               corr=True, cascade=True)

        self._new_sim(broadcast=False)

    # ── logging ───────────────────────────────────────────────────────────────



    def log_candle(self, tf: str, candle: Dict) -> None:
        if tf != "5m":
            return
        exists = os.path.isfile(self.csv_candles)
        with open(self.csv_candles, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["Timestamp","Step","Open","High","Low","Close","Volume"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                candle.get("time",0),
                round(candle.get("open",0),6), round(candle.get("high",0),6),
                round(candle.get("low",0),6),  round(candle.get("close",0),6),
                round(candle.get("volume",0),6),
            ])

    # ── simulation lifecycle ──────────────────────────────────────────────────

    def _new_sim(self, broadcast: bool = True) -> None:
        with self.lock:
            price = _random_price()
            cfg   = Phase2Config(
                enable_garch_volatility    = self.p2_flags["garch"],
                enable_volume_model        = self.p2_flags["volume"],
                enable_slippage_model      = self.p2_flags["slippage"],
                enable_correlated_assets   = self.p2_flags["corr"],
                enable_liquidation_cascade = self.p2_flags["cascade"],
                n_assets=2, asset_names=["BTC","ETH"],
                asset_vol_scalars=[1.0, 1.4],
                correlation_matrix=[[1.0, 0.6],[0.6, 1.0]],
                volume_base=price * 0.1, seed=None,
            )
            self.p2sim = Phase2MarketSimulator(
                initial_price=price, cfg=cfg,
                stress=self.stress_cfg if self.stress_cfg.enabled else StressTestConfig(),
            )
            if self.p2sim.corr_engine is not None:
                self.p2sim.corr_engine.initialise_prices(price, secondary_ratio=0.06)
                self.p2sim.corr_prices = [[p] for p in self.p2sim.corr_engine.prices]

            self.aggs          = {tf: OHLCVAggregator(spc) for tf, spc in TIMEFRAMES}
            self._accum        = 0.0
            self.balance       = INITIAL_BALANCE
            self.realized_pnl  = 0.0
            self.positions     = []
            self.orders        = []
            self.trade_pnls    = []
            self.ebb_strategy.reset(INITIAL_BALANCE)
            if self.dynamic_strategy:
                self.dynamic_strategy.reset(INITIAL_BALANCE)

        if broadcast:
            self._emit("new_sim", {
                "price": price, "step": 0,
                "regime": self.p2sim.regime,
                "balance": INITIAL_BALANCE,
                "p2": self._p2_status(),
            })

    def _p2_status(self) -> Dict:
        sim = self.p2sim
        if sim is None:
            return {}
        result: Dict = {
            "garch_enabled":    sim.garch   is not None,
            "volume_enabled":   sim.volume_engine is not None,
            "slippage_enabled": sim.slippage is not None,
            "corr_enabled":     sim.corr_engine is not None,
            "cascade_enabled":  sim.cascade  is not None,
            "garch_sigma":  round(sim.sigmas[-1]  * 100, 4) if sim.sigmas  else 0,
            "garch_lr":     round(sim.garch.long_run_sigma() * 100, 4) if sim.garch else 0,
            "volume":       round(sim.volumes[-1], 1)  if sim.volumes  else 0,
            "cascade_count": sim.n_cascade_events,
            "oi":           round(sim.cascade.open_interest, 0) if sim.cascade else 0,
            "corr_prices":  {},
            "stress_enabled": self.stress_cfg.enabled,
        }
        if sim.corr_engine and sim.corr_prices:
            for i, name in enumerate(sim.cfg.asset_names[:sim.cfg.n_assets]):
                if i < len(sim.corr_prices) and sim.corr_prices[i]:
                    result["corr_prices"][name] = round(sim.corr_prices[i][-1], 2)
        return result

    def _slippage_exec_price(self, mid: float, side: str, size_usd: float) -> Tuple[float, float]:
        sim = self.p2sim
        if sim is None:
            return mid, 0.0
        if sim.slippage is not None:
            sigma  = sim.sigmas[-1] if sim.sigmas else 0.0008
            spread = sim._p1.spread_params.base_spread
            jumped = sim.jumps[-1] if sim.jumps else False
            ep     = sim.slippage.compute(mid, "buy" if side == "long" else "sell",
                                          size_usd, spread, sigma, jumped)
            return ep, abs(ep - mid)
        return mid, 0.0

    def _close_position_locked(self, pos: Position, price: float,
                               reason: str = "manual") -> Dict:
        cp, slip = self._slippage_exec_price(
            price, "short" if pos.side == "long" else "long", pos.size_usd)
        upnl = ((cp - pos.entry_price) / pos.entry_price * pos.size_usd
                if pos.side == "long"
                else (pos.entry_price - cp) / pos.entry_price * pos.size_usd)
        fee = pos.size_usd * TAKER_FEE_RATE
        net = upnl - fee
        self.balance      += pos.margin + net
        self.realized_pnl += net
        self.trade_pnls.append(net)
        if pos in self.positions:
            self.positions.remove(pos)
        return {
            "status": "closed",
            "reason": reason,
            "position_id": pos.id,
            "pnl": round(net, 2),
            "balance": round(self.balance, 2),
            "slippage": round(slip, 6),
            "side": pos.side,
            "entry_price": round(pos.entry_price, 6),
            "exit_price": round(cp, 6),
            "size_usd": round(pos.size_usd, 2),
            "leverage": pos.leverage,
            "symbol": "SIM",
        }

    # ── controls ──────────────────────────────────────────────────────────────

    def set_speed(self, speed: Any) -> None:
        with self.lock:
            self._speed = speed
            self._accum = 0.0

    def pause(self)  -> None: self._pause_event.set()
    def resume(self) -> None: self._pause_event.clear()
    def is_paused(self) -> bool: return self._pause_event.is_set()

    def start(self) -> None:
        self._stop_event.clear()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ── background loop ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            interval_s = EMIT_MS / 1000.0
            t0         = time.monotonic()

            if not self._pause_event.is_set():
                with self.lock:
                    spf          = SPEED_MAP.get(self._speed, SPEED_MAP[1])
                    self._accum += spf
                    steps_now    = int(self._accum)
                    self._accum -= steps_now

                closed_by_tf: Dict[str, List[Dict]] = {tf: [] for tf, _ in TIMEFRAMES}
                tick_price   = self.p2sim.price
                tick_step    = self.p2sim.t
                tick_regime  = self.p2sim.regime
                filled_events: List[Dict] = []
                liq_events:    List[str]  = []
                tpsl_events:   List[Dict] = []
                cascade_fired  = False

                for _ in range(max(0, steps_now)):
                    with self.lock:
                        new_price  = self.p2sim.step()
                        cur_regime = self.p2sim.regime
                        cur_step   = self.p2sim.t
                        vol        = self.p2sim.volumes[-1] if self.p2sim.volumes else 0.0
                        if self.p2sim.cascades and self.p2sim.cascades[-1]:
                            cascade_fired = True

                        for order in list(self.orders):
                            if order.should_trigger(new_price):
                                fp, slip = self._slippage_exec_price(
                                    order.fill_price(new_price), order.side, order.size_usd)
                                pos = Position(str(uuid.uuid4())[:8], order.side,
                                               fp, order.size_usd, order.leverage,
                                               order.type, slip)
                                if pos.margin + pos.fee_paid <= self.balance:
                                    self.balance -= pos.margin + pos.fee_paid
                                    self.positions.append(pos)
                                    filled_events.append({"order_id": order.id,
                                                          "position": pos.to_dict(new_price)})
                                self.orders.remove(order)

                        for pos in list(self.positions):
                            tpsl_hit = pos.tpsl_trigger(new_price)
                            if tpsl_hit is not None:
                                reason, trigger_price = tpsl_hit
                                tpsl_events.append(
                                    self._close_position_locked(pos, trigger_price, reason))
                                continue
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
                    for tf, _ in TIMEFRAMES:
                        if closed_by_tf[tf]:
                            recent   = self.aggs[tf].ohlcv[-500:]
                            inds     = compute_indicators(recent) if len(recent) >= 2 else {}
                            ind_last = {k: v[-1] for k, v in inds.items() if v}
                            self._emit("candle_close", {
                                "tf":       tf,
                                "candles":  closed_by_tf[tf],
                                "ind_last": ind_last,
                            })

                    # EMA BB Scalper on 5m
                    if self.ebb_strategy.enabled and closed_by_tf.get("5m"):
                        ec  = list(self.aggs["5m"].ohlcv)
                        ea  = self.ebb_strategy.on_candle(ec, tick_price, tick_step)
                        if ea:
                            self._emit("ebb_strategy_update", {
                                "actions": ea,
                                "metrics": self.ebb_strategy.metrics(),
                            })

                    # Dynamic (user) strategy on 5m
                    if self.dynamic_strategy and self.dynamic_strategy.enabled and closed_by_tf.get("5m"):
                        dc  = list(self.aggs["5m"].ohlcv)
                        da  = self.dynamic_strategy.on_candle(dc, tick_price, tick_step)
                        if da or self.dynamic_strategy.error:
                            self._emit("dynamic_strategy_update", {
                                "actions": da or [],
                                "metrics": self.dynamic_strategy.metrics(),
                                "error":   self.dynamic_strategy.error,
                            })

                    with self.lock:
                        pos_dicts = [p.to_dict(tick_price) for p in self.positions]
                        ord_dicts = [o.to_dict()           for o in self.orders]
                        balance   = round(self.balance,       2)
                        rpnl      = round(self.realized_pnl,  2)

                    p2_tick: Dict = {
                        "sigma":     round(self.p2sim.sigmas[-1] * 100, 4) if self.p2sim.sigmas else 0,
                        "volume":    round(vol, 1),
                        "cascade":   cascade_fired,
                        "cascade_n": self.p2sim.n_cascade_events,
                        "oi":        round(self.p2sim.cascade.open_interest, 0) if self.p2sim.cascade else 0,
                        "jumped":    self.p2sim.jumps[-1] if self.p2sim.jumps else False,
                    }
                    if self.p2sim.corr_engine and self.p2sim.corr_prices:
                        cp = {}
                        for i, name in enumerate(self.p2sim.cfg.asset_names[:self.p2sim.cfg.n_assets]):
                            if i < len(self.p2sim.corr_prices) and self.p2sim.corr_prices[i]:
                                cp[name] = round(self.p2sim.corr_prices[i][-1], 2)
                        p2_tick["corr_prices"] = cp

                    self._emit("tick", {
                        "step":      tick_step,
                        "price":     round(tick_price, 6),
                        "regime":    tick_regime,
                        "live":      {tf: self.aggs[tf].current_ohlcv for tf, _ in TIMEFRAMES},
                        "positions": pos_dicts,
                        "orders":    ord_dicts,
                        "balance":   balance,
                        "rpnl":      rpnl,
                        "events":    {
                            "filled": filled_events,
                            "liquidated": liq_events,
                            "tpsl_closed": tpsl_events,
                        },
                        "p2":        p2_tick,
                    })

            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, interval_s - elapsed))

    # ── data helpers ──────────────────────────────────────────────────────────

    def get_tf_payload(self, tf: str) -> Dict:
        agg = self.aggs.get(tf)
        if agg is None:
            return {}
        candles = list(agg.ohlcv)
        cur     = agg.current_ohlcv
        if cur:
            candles.append(cur)
        inds = compute_indicators(candles) if len(candles) >= 2 else {}
        dyn  = None
        if self.dynamic_strategy:
            dyn = {
                "metrics": self.dynamic_strategy.metrics(),
                "signals": self.dynamic_strategy.signals[-200:],
                "name":    self.dynamic_strategy.name,
                "enabled": self.dynamic_strategy.enabled,
                "error":   self.dynamic_strategy.error,
            }
        return {
            "tf":         tf,
            "candles":    candles,
            "indicators": inds,
            "step":       self.p2sim.t     if self.p2sim else 0,
            "price":      round(self.p2sim.price, 6) if self.p2sim else 0,
            "regime":     self.p2sim.regime if self.p2sim else "bull",
            "p2":         self._p2_status(),
            "ebb_strategy": {
                "metrics": self.ebb_strategy.metrics(),
                "signals": self.ebb_strategy.signals[-200:],
            },
            "dynamic_strategy": dyn,
        }

    def compute_risk_metrics(self) -> Dict:
        if self.p2sim is None or len(self.p2sim.prices) < 2:
            return {}
        return RiskMetrics.full_report(
            np.array(self.p2sim.prices), self.trade_pnls,
            initial_equity=INITIAL_BALANCE, print_report=False,
        )
