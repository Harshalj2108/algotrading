"""
simulator_api.py
================
FastAPI + python-socketio web layer.

Run:
    pip install fastapi uvicorn python-socketio PyJWT
    uvicorn simulator_api:app --host 0.0.0.0 --port 8000 --reload

Env vars (optional, override defaults):
    JWT_SECRET   – must match your auth-server's JWT secret
    FRONTEND_URL – CORS origin (default http://localhost:5173)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import socketio
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    import jwt as pyjwt
except ImportError:
    pyjwt = None

from simulator_core import (
    INITIAL_BALANCE,
    SimulationManager,
    StrategyValidationError,
    StressTestConfig,
    load_strategy,
)

# ─── configuration ────────────────────────────────────────────────────────────

JWT_SECRET   = os.getenv("JWT_SECRET",   "synthcrypto-jwt-secret-change-me-in-production")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# ─── Socket.IO setup ──────────────────────────────────────────────────────────

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5174"],
    logger=False,
    engineio_logger=False,
)


# ─── main event loop reference (set in startup) ──────────────────────────────

_main_loop: asyncio.AbstractEventLoop | None = None


def _sync_emit(event: str, data: Any) -> None:
    """Synchronous emit wrapper passed to SimulationManager (runs in bg thread).

    Uses asyncio.run_coroutine_threadsafe to schedule the async emit on the
    main event loop — avoids the 'no current event loop in thread' crash.
    """
    loop = _main_loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)


# ─── simulation manager (singleton) ──────────────────────────────────────────

manager = SimulationManager(emit_fn=_sync_emit)

# ─── FastAPI app ──────────────────────────────────────────────────────────────

fastapi_app = FastAPI(title="SynthCrypto Simulator API", version="4.0.0")

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Socket.IO under /ws
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="/ws/socket.io")

# ─── auth helper ──────────────────────────────────────────────────────────────

def _decode_token(request: Request) -> Optional[Dict]:
    if pyjwt is None:
        return {"sub": "dev", "email": "dev@local"}
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.cookies.get("token", "")
    if not token:
        return None
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def require_auth(request: Request) -> Dict:
    payload = _decode_token(request)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or missing token")
    return payload


# ─── Pydantic request models ──────────────────────────────────────────────────

class LoadStrategyRequest(BaseModel):
    source: str
    capital: float = INITIAL_BALANCE

class ToggleStrategyRequest(BaseModel):
    enabled: bool

class StressRequest(BaseModel):
    enabled:      bool  = False
    spread_mult:  float = 1.0
    vol_mult:     float = 1.0
    latency:      int   = 0

class P2FlagsRequest(BaseModel):
    garch:    Optional[bool] = None
    volume:   Optional[bool] = None
    slippage: Optional[bool] = None
    corr:     Optional[bool] = None
    cascade:  Optional[bool] = None

class PlaceOrderRequest(BaseModel):
    type:          str   = "market"
    side:          str   = "long"
    size_usd:      float = 100.0
    leverage:      float = 1.0
    trigger_price: Optional[float] = None
    limit_price:   Optional[float] = None

class ClosePositionRequest(BaseModel):
    id: str

class CancelOrderRequest(BaseModel):
    id: str


# ─── REST endpoints ────────────────────────────────────────────────────────────

@fastapi_app.get("/api/health")
def health():
    return {"status": "ok", "version": "4.0.0"}


@fastapi_app.get("/api/tf/{tf}")
def get_tf_data(tf: str, user=Depends(require_auth)):
    payload = manager.get_tf_payload(tf)
    if not payload:
        raise HTTPException(404, f"Unknown timeframe: {tf}")
    return payload


@fastapi_app.get("/api/risk")
def get_risk_metrics(user=Depends(require_auth)):
    return manager.compute_risk_metrics()


@fastapi_app.post("/api/sim/new")
def new_simulation(user=Depends(require_auth)):
    manager._new_sim(broadcast=True)
    return manager.get_tf_payload("1m")


@fastapi_app.post("/api/sim/pause")
def pause_simulation(user=Depends(require_auth)):
    manager.pause()
    return {"paused": True}


@fastapi_app.post("/api/sim/resume")
def resume_simulation(user=Depends(require_auth)):
    manager.resume()
    return {"paused": False}


@fastapi_app.post("/api/sim/speed")
async def set_speed(request: Request, user=Depends(require_auth)):
    body = await request.json()
    speed = body.get("speed", 1)
    manager.set_speed("max" if speed == "max" else int(speed))
    return {"speed": speed}


@fastapi_app.post("/api/stress")
def set_stress(req: StressRequest, user=Depends(require_auth)):
    with manager.lock:
        manager.stress_cfg = StressTestConfig(
            spread_multiplier=req.spread_mult,
            vol_multiplier=req.vol_mult,
            latency_steps=req.latency,
            enabled=req.enabled,
        )
    return req.dict()


@fastapi_app.post("/api/p2-flags")
def set_p2_flags(req: P2FlagsRequest, user=Depends(require_auth)):
    with manager.lock:
        for key in ("garch", "volume", "slippage", "corr", "cascade"):
            val = getattr(req, key)
            if val is not None:
                manager.p2_flags[key] = val
    return manager.p2_flags


# ─── trading endpoints ────────────────────────────────────────────────────────

@fastapi_app.post("/api/orders")
def place_order(req: PlaceOrderRequest, user=Depends(require_auth)):
    import uuid
    from simulator_core import Position, Order
    with manager.lock:
        cur = manager.p2sim.price if manager.p2sim else 0.0
        if req.type == "market":
            ep, slip = manager._slippage_exec_price(cur, req.side, req.size_usd)
            pos = Position(str(uuid.uuid4())[:8], req.side, ep,
                           req.size_usd, req.leverage, "market", slip)
            if pos.margin + pos.fee_paid > manager.balance:
                raise HTTPException(400, "Insufficient balance")
            manager.balance -= pos.margin + pos.fee_paid
            manager.positions.append(pos)
            return {"status": "filled", "position": pos.to_dict(cur),
                    "balance": round(manager.balance, 2), "slippage": round(slip, 6)}
        else:
            if req.trigger_price is None:
                raise HTTPException(400, "trigger_price required for non-market orders")
            order = Order(str(uuid.uuid4())[:8], req.type, req.side,
                          req.size_usd, req.leverage, req.trigger_price, req.limit_price)
            manager.orders.append(order)
            return {"status": "pending", "order": order.to_dict()}


@fastapi_app.post("/api/positions/{pos_id}/close")
def close_position(pos_id: str, user=Depends(require_auth)):
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            raise HTTPException(404, "Position not found")
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        cp, slip = manager._slippage_exec_price(
            cur, "short" if pos.side == "long" else "long", pos.size_usd)
        upnl = ((cp - pos.entry_price) / pos.entry_price * pos.size_usd
                if pos.side == "long"
                else (pos.entry_price - cp) / pos.entry_price * pos.size_usd)
        from simulator_core import TAKER_FEE_RATE
        fee = pos.size_usd * TAKER_FEE_RATE
        net = upnl - fee
        manager.balance      += pos.margin + net
        manager.realized_pnl += net
        manager.trade_pnls.append(net)
        manager.positions.remove(pos)
        manager.log_trade("manual", {
            "side": pos.side, "entry": pos.entry_price, "exit": cp,
            "size": pos.size_usd, "pnl": net, "reason": "user_close",
        })
    return {"status": "closed", "pnl": round(net, 2),
            "balance": round(manager.balance, 2), "slippage": round(slip, 6)}


@fastapi_app.delete("/api/orders/{ord_id}")
def cancel_order(ord_id: str, user=Depends(require_auth)):
    with manager.lock:
        order = next((o for o in manager.orders if o.id == ord_id), None)
        if not order:
            raise HTTPException(404, "Order not found")
        manager.orders.remove(order)
    return {"status": "cancelled", "order_id": ord_id}


# ─── EMA BB Scalper endpoints ─────────────────────────────────────────────────

@fastapi_app.post("/api/strategy/ebb/toggle")
def toggle_ebb(req: ToggleStrategyRequest, user=Depends(require_auth)):
    with manager.lock:
        manager.ebb_strategy.enabled = req.enabled
    return {"enabled": req.enabled, "metrics": manager.ebb_strategy.metrics()}


@fastapi_app.get("/api/strategy/ebb/metrics")
def ebb_metrics(user=Depends(require_auth)):
    return manager.ebb_strategy.metrics()


# ─── Dynamic (user-pasted) strategy endpoints ─────────────────────────────────

@fastapi_app.post("/api/strategy/dynamic/load")
def load_dynamic_strategy(req: LoadStrategyRequest, user=Depends(require_auth)):
    """
    Validate, compile, and register a user-pasted strategy.

    Request body:
        {
          "source": "<python source code string>",
          "capital": 10000.0   // optional
        }

    The Python class must be named 'Strategy' and implement:
        __init__(self, capital: float)
        on_candle(self, candles: list[dict], price: float, step: int) -> list[dict]
        metrics(self) -> dict
        reset(self, capital: float)

    All ind_* indicator helpers and numpy (as np) are pre-imported.
    """
    try:
        dyn = load_strategy(req.source, req.capital)
    except StrategyValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with manager.lock:
        manager.dynamic_strategy = dyn

    return {
        "status":  "loaded",
        "name":    dyn.name,
        "metrics": dyn.metrics(),
    }


@fastapi_app.post("/api/strategy/dynamic/toggle")
def toggle_dynamic(req: ToggleStrategyRequest, user=Depends(require_auth)):
    if manager.dynamic_strategy is None:
        raise HTTPException(400, "No strategy loaded. POST to /api/strategy/dynamic/load first.")
    with manager.lock:
        manager.dynamic_strategy.enabled = req.enabled
    return {
        "enabled": req.enabled,
        "metrics": manager.dynamic_strategy.metrics(),
    }


@fastapi_app.get("/api/strategy/dynamic/metrics")
def dynamic_metrics(user=Depends(require_auth)):
    if manager.dynamic_strategy is None:
        return {"loaded": False}
    return {"loaded": True, **manager.dynamic_strategy.metrics()}


@fastapi_app.delete("/api/strategy/dynamic")
def unload_dynamic_strategy(user=Depends(require_auth)):
    with manager.lock:
        manager.dynamic_strategy = None
    return {"status": "unloaded"}


@fastapi_app.get("/api/strategy/dynamic/template")
def strategy_template():
    """
    Returns a commented starter template the frontend can pre-fill
    in the code editor so the user knows the required interface.
    """
    template = '''"""
Strategy template for SynthCrypto simulator.
============================================
Rules:
  • Class must be named exactly  Strategy
  • Constructor accepts capital (float)
  • on_candle  must return a list of action dicts:
      {"action": "open",  "signal": {...}, "size": float}
      {"action": "close", "trade":  {...}, "signal": {...}}
  • metrics()  must return a plain dict
  • reset(capital) restarts internal state

Available (pre-imported):
  np               – NumPy
  ind_sma, ind_ema, ind_wma, ind_vwap,
  ind_bollinger, ind_atr, ind_adx, ind_keltner,
  ind_rsi, ind_stochastic, ind_cci, ind_williams_r,
  ind_macd, ind_obv, ind_cmf, ind_ichimoku
"""

class Strategy:
    def __init__(self, capital: float = 10_000.0):
        self.enabled          = False
        self.initial_capital  = capital
        self.capital          = capital
        self.pos              = None
        self.trades           = []
        self.signals          = []

    def reset(self, capital: float = 10_000.0):
        was = self.enabled
        self.__init__(capital)
        self.enabled = was

    def on_candle(self, candles, price, step):
        """
        candles : list of dicts with keys time, open, high, low, close, volume
        price   : current market price (float)
        step    : simulation step counter (int)
        Returns : list of action dicts
        """
        if not self.enabled or len(candles) < 50:
            return []

        actions = []
        closes  = np.array([c["close"] for c in candles], dtype=float)
        highs   = np.array([c["high"]  for c in candles], dtype=float)
        lows    = np.array([c["low"]   for c in candles], dtype=float)

        rsi  = ind_rsi(closes, 14)
        atr  = ind_atr(highs, lows, closes, 14)
        ts   = candles[-1]["time"]

        last_rsi = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0
        last_atr = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        RISK_PCT = 0.01          # 1 % risk per trade
        SL_MULT  = 1.5
        TP_MULT  = 3.0

        # ── exit ──
        if self.pos is not None:
            p = self.pos
            reason = ep = None
            if p["side"] == "long":
                if price <= p["sl"]:   reason, ep = "stop_loss",  p["sl"]
                elif price >= p["tp"]: reason, ep = "take_profit", p["tp"]
            else:
                if price >= p["sl"]:   reason, ep = "stop_loss",  p["sl"]
                elif price <= p["tp"]: reason, ep = "take_profit", p["tp"]
            if reason:
                pnl = ((ep - p["entry"]) / p["entry"] * p["size"]
                       if p["side"] == "long"
                       else (p["entry"] - ep) / p["entry"] * p["size"])
                self.capital += pnl
                trade = dict(side=p["side"], entry=p["entry"], exit=ep,
                             pnl=pnl, size=p["size"], reason=reason,
                             entry_step=p["entry_step"], exit_step=step)
                self.trades.append(trade)
                sig = dict(time=ts, price=round(ep,6), type="exit",
                           side=p["side"], reason=reason, pnl=round(pnl,2))
                self.signals.append(sig)
                actions.append(dict(action="close", trade=trade, signal=sig))
                self.pos = None

        # ── entry ──
        if self.pos is None and last_atr > 0:
            side = None
            if last_rsi < 30:   side = "long"
            elif last_rsi > 70: side = "short"

            if side:
                stop_d   = SL_MULT * last_atr
                risk_amt = self.capital * RISK_PCT
                size     = min(risk_amt / (stop_d / price), self.capital * 0.95)
                if size > 10:
                    sl = price - stop_d if side == "long" else price + stop_d
                    tp = price + TP_MULT * last_atr if side == "long" else price - TP_MULT * last_atr
                    self.pos = dict(side=side, entry=price, size=size,
                                    sl=sl, tp=tp, entry_step=step)
                    sig = dict(time=ts, price=round(price,6), type="entry",
                               side=side, sl=round(sl,6), tp=round(tp,6))
                    self.signals.append(sig)
                    actions.append(dict(action="open", signal=sig, size=round(size,2)))

        return actions

    def metrics(self):
        if not self.trades:
            return dict(total_trades=0, capital=round(self.capital,2),
                        net_pnl=round(self.capital-self.initial_capital,2),
                        enabled=self.enabled)
        pnls = [t["pnl"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        return dict(
            total_trades = len(self.trades),
            capital      = round(self.capital, 2),
            net_pnl      = round(self.capital - self.initial_capital, 2),
            win_rate     = round(len(wins) / len(pnls) * 100, 1),
            enabled      = self.enabled,
        )
'''
    return {"template": template}


# ─── Socket.IO events ─────────────────────────────────────────────────────────

@sio.event
async def connect(sid, environ, auth=None):
    payload = manager.get_tf_payload("1m")
    payload["paused"] = manager.is_paused()
    payload["speed"]  = manager._speed
    await sio.emit("init", payload, to=sid)


@sio.event
async def switch_tf(sid, data):
    tf = data.get("tf", "1m") if isinstance(data, dict) else "1m"
    await sio.emit("tf_data", manager.get_tf_payload(tf), to=sid)


@sio.event
async def set_speed(sid, data):
    speed = data.get("speed", 1) if isinstance(data, dict) else 1
    manager.set_speed("max" if speed == "max" else int(speed))


@sio.event
async def pause(sid, data=None):
    manager.pause()
    await sio.emit("paused", {})


@sio.event
async def resume(sid, data=None):
    manager.resume()
    await sio.emit("resumed", {})


@sio.event
async def new_sim(sid, data=None):
    manager._new_sim(broadcast=True)
    await sio.emit("tf_data", manager.get_tf_payload("1m"))


@sio.event
async def place_order(sid, data):
    """WebSocket shortcut for placing orders (mirrors the REST endpoint)."""
    import uuid
    from simulator_core import Position, Order, TAKER_FEE_RATE
    data = data or {}
    order_type    = data.get("type", "market")
    side          = data.get("side", "long")
    size_usd      = float(data.get("size_usd", 100))
    leverage      = float(data.get("leverage", 1))
    trigger_price = data.get("trigger_price")
    limit_price   = data.get("limit_price")

    with manager.lock:
        cur = manager.p2sim.price if manager.p2sim else 0.0
        if order_type == "market":
            ep, slip = manager._slippage_exec_price(cur, side, size_usd)
            pos = Position(str(uuid.uuid4())[:8], side, ep,
                           size_usd, leverage, "market", slip)
            if pos.margin + pos.fee_paid > manager.balance:
                await sio.emit("order_result",
                               {"status": "error", "msg": "Insufficient balance"}, to=sid)
                return
            manager.balance -= pos.margin + pos.fee_paid
            manager.positions.append(pos)
            await sio.emit("order_result", {
                "status": "filled", "position": pos.to_dict(cur),
                "balance": round(manager.balance, 2), "slippage": round(slip, 6),
            }, to=sid)
        else:
            if trigger_price is None:
                await sio.emit("order_result",
                               {"status": "error", "msg": "trigger_price required"}, to=sid)
                return
            order = Order(str(uuid.uuid4())[:8], order_type, side, size_usd, leverage,
                          float(trigger_price),
                          float(limit_price) if limit_price is not None else None)
            manager.orders.append(order)
            await sio.emit("order_result",
                           {"status": "pending", "order": order.to_dict()}, to=sid)


@sio.event
async def close_position(sid, data):
    data = data or {}
    pos_id = data.get("id")
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            await sio.emit("order_result",
                           {"status": "error", "msg": "Position not found"}, to=sid)
            return
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        cp, slip = manager._slippage_exec_price(
            cur, "short" if pos.side == "long" else "long", pos.size_usd)
        upnl = ((cp - pos.entry_price) / pos.entry_price * pos.size_usd
                if pos.side == "long"
                else (pos.entry_price - cp) / pos.entry_price * pos.size_usd)
        from simulator_core import TAKER_FEE_RATE
        fee = pos.size_usd * TAKER_FEE_RATE
        net = upnl - fee
        manager.balance      += pos.margin + net
        manager.realized_pnl += net
        manager.trade_pnls.append(net)
        manager.positions.remove(pos)
        manager.log_trade("manual", {
            "side": pos.side, "entry": pos.entry_price, "exit": cp,
            "size": pos.size_usd, "pnl": net, "reason": "user_close",
        })
    await sio.emit("order_result", {
        "status": "closed", "pnl": round(net, 2),
        "balance": round(manager.balance, 2), "slippage": round(slip, 6),
    })


@sio.event
async def cancel_order(sid, data):
    data = data or {}
    ord_id = data.get("id")
    with manager.lock:
        order = next((o for o in manager.orders if o.id == ord_id), None)
        if order:
            manager.orders.remove(order)
            await sio.emit("order_result",
                           {"status": "cancelled", "order_id": ord_id}, to=sid)


@sio.event
async def get_risk_metrics(sid, data=None):
    await sio.emit("risk_metrics", manager.compute_risk_metrics(), to=sid)


@sio.event
async def set_stress(sid, data):
    data = data or {}
    with manager.lock:
        manager.stress_cfg = StressTestConfig(
            spread_multiplier=float(data.get("spread_mult", 1.0)),
            vol_multiplier=float(data.get("vol_mult", 1.0)),
            latency_steps=int(data.get("latency", 0)),
            enabled=bool(data.get("enabled", False)),
        )
    await sio.emit("stress_updated", data, to=sid)


@sio.event
async def set_p2_flags(sid, data):
    data = data or {}
    with manager.lock:
        for key in ("garch", "volume", "slippage", "corr", "cascade"):
            if key in data:
                manager.p2_flags[key] = bool(data[key])
    await sio.emit("p2_flags_updated", manager.p2_flags, to=sid)


@sio.event
async def toggle_ebb_strategy(sid, data):
    data = data or {}
    enabled = bool(data.get("enabled", False))
    with manager.lock:
        manager.ebb_strategy.enabled = enabled
    await sio.emit("ebb_strategy_toggled", {
        "enabled": enabled,
        "metrics": manager.ebb_strategy.metrics(),
        "signals": manager.ebb_strategy.signals[-200:],
    }, to=sid)


@sio.event
async def toggle_dynamic_strategy(sid, data):
    data = data or {}
    enabled = bool(data.get("enabled", False))
    if manager.dynamic_strategy is None:
        await sio.emit("dynamic_strategy_error",
                       {"error": "No strategy loaded"}, to=sid)
        return
    with manager.lock:
        manager.dynamic_strategy.enabled = enabled
    await sio.emit("dynamic_strategy_toggled", {
        "enabled": enabled,
        "metrics": manager.dynamic_strategy.metrics(),
    }, to=sid)


# ─── startup / shutdown ───────────────────────────────────────────────────────

@fastapi_app.on_event("startup")
async def startup():
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    manager.start()
    print("=" * 60)
    print("  SynthCrypto v4 — FastAPI + Socket.IO")
    print("  REST:      http://localhost:8000/docs")
    print("  Socket.IO: ws://localhost:8000/ws/socket.io")
    print("=" * 60)


@fastapi_app.on_event("shutdown")
async def shutdown():
    manager.stop()
