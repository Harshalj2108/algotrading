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
from realtime_engine import data_engine

# ─── configuration ────────────────────────────────────────────────────────────

JWT_SECRET   = os.getenv("JWT_SECRET")
CLIENT_URL = os.getenv("CLIENT_URL", "*")

def parse_origins(env_str):
    if not env_str or env_str == "*":
        return ["*"]
    return [u.strip().rstrip("/") for u in env_str.split(",")]

cors_origins = parse_origins(CLIENT_URL)

# ─── Socket.IO setup ──────────────────────────────────────────────────────────

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=cors_origins if cors_origins != ["*"] else [],
    logger=False,
    engineio_logger=False,
)

# ─── Live Market State ────────────────────────────────────────────────────────
# Keeps track of which clients are subscribed to which live market symbols
# format: { sid: {"asset_class": "crypto", "symbol": "BTC/USDT"} }
_live_subscriptions = {}
_live_polling_task = None

async def _poll_live_markets():
    """Background task to poll live markets for active subscriptions and broadcast."""
    while True:
        if _live_subscriptions:
            # Group by unique symbols to avoid duplicate API calls
            unique_subs = set((sub["asset_class"], sub["symbol"]) for sub in _live_subscriptions.values())
            for asset_class, symbol in unique_subs:
                ticker = await data_engine.get_ticker(asset_class, symbol)
                if ticker:
                    # Broadcast to all sids subscribed to this symbol
                    for sid, sub in _live_subscriptions.items():
                        if sub["asset_class"] == asset_class and sub["symbol"] == symbol:
                            await sio.emit("live_tick", ticker, to=sid)
        
        await asyncio.sleep(2) # Poll every 2 seconds

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
    allow_origins=cors_origins if cors_origins != ["*"] else [],
    allow_origin_regex=".*" if cors_origins == ["*"] else None,
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
    tp_price:      Optional[float] = None
    sl_price:      Optional[float] = None

class ClosePositionRequest(BaseModel):
    id: str

class UpdateTPSLRequest(BaseModel):
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None

class UpdateTradeTPSLRequest(UpdateTPSLRequest):
    id: Optional[str] = None
    trade_id: Optional[str] = None

class CancelOrderRequest(BaseModel):
    id: str


# ─── REST endpoints ────────────────────────────────────────────────────────────

@fastapi_app.get("/api/health")
def health():
    return {"status": "ok", "version": "4.0.0"}

@fastapi_app.get("/api/live/search")
async def live_search(q: str, type: str = "crypto"):
    """Search for symbols."""
    results = await data_engine.search_symbols(q, type)
    return {"results": results}

@fastapi_app.get("/api/live/history")
async def live_history(symbol: str, type: str = "crypto", tf: str = "5m"):
    """Fetch historical data to initialize chart."""
    import time as _time
    t0 = _time.monotonic()
    try:
        data = await data_engine.get_history(type, symbol, tf)
        elapsed = _time.monotonic() - t0
        print(f"[API] /api/live/history: {type}/{symbol}/{tf} -> {len(data)} candles in {elapsed:.1f}s")
        from simulator_core import compute_indicators
        inds = compute_indicators(data) if len(data) >= 2 else {}
        return {"symbol": symbol, "data": data, "indicators": inds}
    except Exception as e:
        elapsed = _time.monotonic() - t0
        print(f"[API] /api/live/history ERROR: {type}/{symbol}/{tf} -> {e} ({elapsed:.1f}s)")
        return JSONResponse(
            status_code=500,
            content={"symbol": symbol, "data": [], "indicators": {}, "error": "Failed to fetch historical data"},
        )

@fastapi_app.get("/api/live/ticker")
async def live_ticker(symbol: str, type: str = "crypto"):
    """Fetch the latest live ticker used by paper-trade execution."""
    try:
        ticker = await data_engine.get_ticker(type, symbol)
        if not ticker:
            return JSONResponse(
                status_code=404,
                content={"symbol": symbol, "type": type, "error": "Ticker unavailable"},
            )
        return {"type": type, **ticker}
    except Exception as e:
        print(f"[API] /api/live/ticker ERROR: {type}/{symbol} -> {e}")
        return JSONResponse(
            status_code=500,
            content={"symbol": symbol, "type": type, "error": "Failed to fetch ticker"},
        )

@fastapi_app.get("/api/tf/{tf}")
def get_tf_data(tf: str, user=Depends(require_auth)):
    payload = manager.get_tf_payload(tf)
    if not payload:
        raise HTTPException(404, f"Unknown timeframe: {tf}")
    return payload


@fastapi_app.get("/api/risk")
def api_get_risk_metrics(user=Depends(require_auth)):
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
async def api_set_speed(request: Request, user=Depends(require_auth)):
    body = await request.json()
    speed = body.get("speed", 1)
    manager.set_speed("max" if speed == "max" else int(speed))
    return {"speed": speed}


@fastapi_app.post("/api/stress")
def api_set_stress(req: StressRequest, user=Depends(require_auth)):
    with manager.lock:
        manager.stress_cfg = StressTestConfig(
            spread_multiplier=req.spread_mult,
            vol_multiplier=req.vol_mult,
            latency_steps=req.latency,
            enabled=req.enabled,
        )
    return req.dict()


@fastapi_app.post("/api/p2-flags")
def api_set_p2_flags(req: P2FlagsRequest, user=Depends(require_auth)):
    with manager.lock:
        for key in ("garch", "volume", "slippage", "corr", "cascade"):
            val = getattr(req, key)
            if val is not None:
                manager.p2_flags[key] = val
    return manager.p2_flags


# ─── trading endpoints ────────────────────────────────────────────────────────

@fastapi_app.post("/api/orders")
def api_place_order(req: PlaceOrderRequest, user=Depends(require_auth)):
    import uuid
    from simulator_core import Position, Order
    with manager.lock:
        cur = manager.p2sim.price if manager.p2sim else 0.0
        if req.type == "market":
            ep, slip = manager._slippage_exec_price(cur, req.side, req.size_usd)
            pos = Position(str(uuid.uuid4())[:8], req.side, ep,
                           req.size_usd, req.leverage, "market", slip,
                           req.tp_price, req.sl_price)
            err = pos.set_tpsl(req.tp_price, req.sl_price)
            if err:
                raise HTTPException(400, err)
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
def api_close_position(pos_id: str, user=Depends(require_auth)):
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            raise HTTPException(404, "Position not found")
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        result = manager._close_position_locked(pos, cur, "manual")
    return result


@fastapi_app.patch("/api/positions/{pos_id}/tpsl")
def api_update_position_tpsl(pos_id: str, req: UpdateTPSLRequest,
                             user=Depends(require_auth)):
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            raise HTTPException(404, "Position not found")
        err = pos.set_tpsl(req.tp_price, req.sl_price)
        if err:
            raise HTTPException(400, err)
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        return {"status": "tpsl_updated", "position": pos.to_dict(cur)}


@fastapi_app.delete("/api/positions/{pos_id}/tpsl")
def api_remove_position_tpsl(pos_id: str, user=Depends(require_auth)):
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            raise HTTPException(404, "Position not found")
        pos.set_tpsl(None, None)
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        return {"status": "tpsl_removed", "position": pos.to_dict(cur)}


@fastapi_app.patch("/trade/update-tpsl")
def api_update_trade_tpsl(req: UpdateTradeTPSLRequest,
                          user=Depends(require_auth)):
    pos_id = req.id or req.trade_id
    if not pos_id:
        raise HTTPException(400, "trade_id is required")
    return api_update_position_tpsl(pos_id, req, user)


@fastapi_app.delete("/trade/remove-tpsl")
def api_remove_trade_tpsl(req: ClosePositionRequest,
                          user=Depends(require_auth)):
    return api_remove_position_tpsl(req.id, user)


@fastapi_app.delete("/api/orders/{ord_id}")
def api_cancel_order(ord_id: str, user=Depends(require_auth)):
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
    from simulator_core import Position, Order
    data = data or {}
    order_type    = data.get("type", "market")
    side          = data.get("side", "long")
    size_usd      = float(data.get("size_usd", 100))
    leverage      = float(data.get("leverage", 1))
    trigger_price = data.get("trigger_price")
    limit_price   = data.get("limit_price")
    tp_price      = data.get("tp_price")
    sl_price      = data.get("sl_price")
    try:
        tp_price = float(tp_price) if tp_price not in (None, "") else None
        sl_price = float(sl_price) if sl_price not in (None, "") else None
    except (TypeError, ValueError):
        await sio.emit("order_result",
                       {"status": "error", "msg": "TP/SL prices must be numbers"}, to=sid)
        return

    with manager.lock:
        cur = manager.p2sim.price if manager.p2sim else 0.0
        if order_type == "market":
            ep, slip = manager._slippage_exec_price(cur, side, size_usd)
            pos = Position(str(uuid.uuid4())[:8], side, ep,
                           size_usd, leverage, "market", slip,
                           tp_price, sl_price)
            err = pos.set_tpsl(tp_price, sl_price)
            if err:
                await sio.emit("order_result",
                               {"status": "error", "msg": err}, to=sid)
                return
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
        result = manager._close_position_locked(pos, cur, "manual")
    await sio.emit("order_result", result, to=sid)


@sio.event
async def update_position_tpsl(sid, data):
    data = data or {}
    pos_id = data.get("id")
    tp_price = data.get("tp_price")
    sl_price = data.get("sl_price")
    try:
        tp_price = float(tp_price) if tp_price not in (None, "") else None
        sl_price = float(sl_price) if sl_price not in (None, "") else None
    except (TypeError, ValueError):
        await sio.emit("order_result",
                       {"status": "error", "msg": "TP/SL prices must be numbers"}, to=sid)
        return

    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            await sio.emit("order_result",
                           {"status": "error", "msg": "Position not found"}, to=sid)
            return
        err = pos.set_tpsl(tp_price, sl_price)
        if err:
            await sio.emit("order_result", {"status": "error", "msg": err}, to=sid)
            return
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        position = pos.to_dict(cur)

    await sio.emit("order_result",
                   {"status": "tpsl_updated", "position": position}, to=sid)


@sio.event
async def remove_position_tpsl(sid, data):
    data = data or {}
    pos_id = data.get("id")
    with manager.lock:
        pos = next((p for p in manager.positions if p.id == pos_id), None)
        if not pos:
            await sio.emit("order_result",
                           {"status": "error", "msg": "Position not found"}, to=sid)
            return
        pos.set_tpsl(None, None)
        cur = manager.p2sim.price if manager.p2sim else pos.entry_price
        position = pos.to_dict(cur)

    await sio.emit("order_result",
                   {"status": "tpsl_removed", "position": position}, to=sid)


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


@sio.event
async def subscribe_live_market(sid, data):
    data = data or {}
    asset_class = data.get("type", "crypto")
    symbol = data.get("symbol")
    if symbol:
        _live_subscriptions[sid] = {"asset_class": asset_class, "symbol": symbol}
        print(f"[{sid}] Subscribed to {asset_class} - {symbol}")


@sio.event
async def unsubscribe_live_market(sid, data):
    if sid in _live_subscriptions:
        del _live_subscriptions[sid]
        print(f"[{sid}] Unsubscribed from live market")


@sio.event
async def disconnect(sid):
    if sid in _live_subscriptions:
        del _live_subscriptions[sid]
    # Rest of default disconnect logic if any


# ─── startup / shutdown ───────────────────────────────────────────────────────

@fastapi_app.on_event("startup")
async def startup():
    global _main_loop, _live_polling_task
    _main_loop = asyncio.get_running_loop()
    manager.start()
    _live_polling_task = asyncio.create_task(_poll_live_markets())
    print("=" * 60)
    print("  SynthCrypto v4 — FastAPI + Socket.IO")
    print("  REST:      http://localhost:8000/docs")
    print("  Socket.IO: ws://localhost:8000/ws/socket.io")
    print("=" * 60)


@fastapi_app.on_event("shutdown")
async def shutdown():
    if _live_polling_task:
        _live_polling_task.cancel()
    manager.stop()
