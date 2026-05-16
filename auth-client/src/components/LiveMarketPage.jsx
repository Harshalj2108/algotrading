import { useState, useEffect, useRef, useCallback, useMemo, Component } from "react";
import { io } from "socket.io-client";
import SimChart from "./SimChart";
import StarBorder from "./StarBorder";
import "./SimulatorPage.css";
import { AUTH_SERVER, SIMULATOR_URL } from '../config';


class ChartErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(err) {
    console.warn("Chart error caught:", err);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#787b86", fontSize: 13, flexDirection: "column", gap: 8 }}>
          <span>Chart encountered an error</span>
          <StarBorder as="button" className="btn" onClick={() => this.setState({ hasError: false })}>Retry</StarBorder>
        </div>
      );
    }
    return this.props.children;
  }
}


const TFS = ["1m", "5m", "15m", "30m", "1h", "1d"];
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };
const IND_BTNS = [
  { g: "Trend", items: [{ k: "sma20", l: "SMA 20" }, { k: "sma50", l: "SMA 50" }, { k: "sma200", l: "SMA 200" }, { k: "ema9", l: "EMA 9" }, { k: "ema20", l: "EMA 20" }, { k: "ema50", l: "EMA 50" }, { k: "wma20", l: "WMA 20" }, { k: "vwap", l: "VWAP" }, { k: "ichimoku", l: "Ichimoku" }] },
  { g: "Bands", items: [{ k: "bb", l: "Bollinger" }, { k: "keltner", l: "Keltner" }] },
  { g: "Vol", items: [{ k: "volume", l: "Volume", on: true }] },
];
const OSC_BTNS = [{ k: "rsi14", l: "RSI" }, { k: "macd", l: "MACD" }, { k: "stoch", l: "Stoch" }, { k: "cci20", l: "CCI" }, { k: "williams_r", l: "W%R" }, { k: "atr14", l: "ATR" }, { k: "obv", l: "OBV" }, { k: "cmf20", l: "CMF" }];

function fmtPrice(v) {
  if (v == null || !Number.isFinite(Number(v))) return "-";
  const n = Number(v);
  if (n >= 10000) return n.toFixed(2);
  if (n >= 100) return n.toFixed(3);
  if (n >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

function fmtMoney(v) {
  const n = Number(v) || 0;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function normalizeCandles(candleArr) {
  const seen = new Map();
  for (const c of candleArr || []) {
    const time = Math.floor(Number(c.time));
    const open = Number(c.open);
    const high = Number(c.high);
    const low = Number(c.low);
    const close = Number(c.close);
    if (![time, open, high, low, close].every(Number.isFinite)) continue;
    seen.set(time, {
      ...c,
      time,
      open,
      high,
      low,
      close,
      volume: Number.isFinite(Number(c.volume)) ? Number(c.volume) : 0,
    });
  }
  return Array.from(seen.values()).sort((a, b) => a.time - b.time);
}

function candleBucket(timeSeconds, timeframe) {
  const seconds = TF_SECONDS[timeframe] || TF_SECONDS["5m"];
  return Math.floor(Math.floor(timeSeconds) / seconds) * seconds;
}

function toUnixSeconds(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return Math.floor(Date.now() / 1000);
  return Math.floor(n > 10000000000 ? n / 1000 : n);
}

function buildVolumeData(candleArr) {
  return candleArr.map(c => ({
    time: c.time,
    value: c.volume || 0,
    color: (c.close >= c.open) ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
  }));
}

async function readJsonResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await res.json() : { error: await res.text() };
  if (!res.ok) throw new Error(data.error || `Request failed with ${res.status}`);
  return data;
}

async function portfolioRequest(path, options = {}) {
  const headers = options.body
    ? { "Content-Type": "application/json", ...(options.headers || {}) }
    : options.headers;
  const res = await fetch(`${AUTH_SERVER}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });
  return readJsonResponse(res);
}

function normalizePosition(position) {
  const entry = Number(position.entry_price) || 0;
  const quantity = Number(position.quantity ?? position.qty) || 0;
  const currentValue = Number(position.current_value) || 0;
  const pnl = Number(position.profit_loss ?? position.pnl ?? position.upnl) || 0;
  const pnlPct = Number(position.profit_loss_percentage ?? position.upnl_pct) || 0;
  return {
    ...position,
    id: position.id || position.trade_id,
    trade_id: position.trade_id || position.id,
    side: "long",
    leverage: 1,
    entry_price: entry,
    quantity,
    qty: quantity,
    size_usd: Number(position.invested_amount ?? position.size_usd) || 0,
    margin: Number(position.invested_amount ?? position.margin ?? position.size_usd) || 0,
    current_value: currentValue,
    profit_loss: pnl,
    pnl,
    upnl: position.position_status === "open" ? pnl : 0,
    profit_loss_percentage: pnlPct,
    upnl_pct: pnlPct,
    stop_loss: position.stop_loss == null ? null : Number(position.stop_loss),
    take_profit: position.take_profit == null ? null : Number(position.take_profit),
    sl_price: position.stop_loss == null ? null : Number(position.stop_loss),
    tp_price: position.take_profit == null ? null : Number(position.take_profit),
  };
}

function markPosition(position, assetClass, symbol, markPrice) {
  if (position.asset_type !== assetClass || position.asset_symbol !== symbol || position.position_status !== "open") {
    return position;
  }
  const entry = Number(position.entry_price) || 0;
  const quantity = Number(position.quantity) || 0;
  const currentValue = quantity * markPrice;
  const pnl = (markPrice - entry) * quantity;
  const pnlPct = entry > 0 ? ((markPrice - entry) / entry) * 100 : 0;
  return {
    ...position,
    current_value: currentValue,
    profit_loss: pnl,
    pnl,
    upnl: pnl,
    profit_loss_percentage: pnlPct,
    upnl_pct: pnlPct,
  };
}

function recalcSummary(wallet, positions, history) {
  const cash = Number(wallet.virtual_balance) || 0;
  const openValue = positions.reduce((sum, p) => sum + (Number(p.current_value) || 0), 0);
  const unrealized = positions.reduce((sum, p) => sum + (Number(p.profit_loss) || 0), 0);
  const realized = history.reduce((sum, t) => sum + (Number(t.profit_loss ?? t.pnl) || 0), 0);
  const wins = history.filter(t => Number(t.profit_loss ?? t.pnl) > 0).length;
  return {
    available_cash: cash,
    open_position_value: openValue,
    total_portfolio_value: cash + openValue,
    realized_profit_loss: realized,
    unrealized_profit_loss: unrealized,
    total_profit_loss: realized + unrealized,
    open_positions: positions.length,
    closed_trades: history.length,
    win_rate_percentage: history.length ? (wins / history.length) * 100 : null,
  };
}

function tradeDurationMs(trade) {
  const start = new Date(trade.created_at).getTime();
  const end = new Date(trade.closed_at || Date.now()).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return Math.max(0, end - start);
}

function fmtDuration(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function toOptionalPrice(value) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
}

function defaultTpsl(position) {
  const step = Math.max(position.entry_price * 0.01, 0.000001);
  return { tp: position.entry_price + step, sl: position.entry_price - step };
}

function riskReward(position, tp = position?.take_profit, sl = position?.stop_loss) {
  if (!position || tp == null || sl == null) return "-";
  const reward = Math.abs(tp - position.entry_price);
  const risk = Math.abs(position.entry_price - sl);
  return risk > 0 ? `${(reward / risk).toFixed(2)}R` : "-";
}

export default function LiveMarketPage({ assetClass, symbol, onBack, focusPositionId = null }) {
  const decodedSymbol = decodeURIComponent(symbol).toUpperCase();

  const [tf, setTf] = useState(() => localStorage.getItem("synthcrypto_live_timeframe") || "5m");
  const tfRef = useRef(tf);
  const priceRef = useRef(0);

  const [connected, setConnected] = useState(false);
  const [candles, setCandles] = useState([]);
  const volData = useMemo(() => buildVolumeData(candles), [candles]);
  const [liveCandle, setLiveCandle] = useState(null);
  const [price, setPrice] = useState(0);
  const [prevPrice, setPrevPrice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const socketRef = useRef(null);
  const historyRequestRef = useRef(0);
  const historyLoadedRef = useRef(false);

  const [wallet, setWallet] = useState({
    virtual_balance: 10000,
    total_portfolio_value: 10000,
    total_profit_loss: 0,
  });
  const [summary, setSummary] = useState({});
  const [positions, setPositions] = useState([]);
  const [history, setHistory] = useState([]);
  const [paperLoading, setPaperLoading] = useState(true);
  const [orderBusy, setOrderBusy] = useState(false);
  const [selectedPosId, setSelectedPosId] = useState(null);
  const [detailsPosId, setDetailsPosId] = useState(null);
  const [tpslManagePosId, setTpslManagePosId] = useState(null);
  const [tpslEditPosId, setTpslEditPosId] = useState(null);
  const [tpslDeletePosId, setTpslDeletePosId] = useState(null);
  const [tradeMode, setTradeMode] = useState("buy");
  const [otype, setOtype] = useState("market");
  const [sizeUsd, setSizeUsd] = useState(100);
  const [quantity, setQuantity] = useState("");
  const [takeProfit, setTakeProfit] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [trigPrice, setTrigPrice] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [tpslDraft, setTpslDraft] = useState({ tp: "", sl: "" });
  const [historyFilter, setHistoryFilter] = useState("symbol");
  const [historySort, setHistorySort] = useState("newest");
  const [toast, setToast] = useState(null);
  const toastTimer = useRef();
  const tickSyncRef = useRef({ busy: false, last: 0 });

  const [activeInds, setActiveInds] = useState(new Set(["volume"]));
  const [activeOsc, setActiveOsc] = useState("rsi14");
  const [indicatorData, setIndicatorData] = useState({});

  const symbolPositions = useMemo(
    () => positions.filter(p => p.asset_type === assetClass && p.asset_symbol === decodedSymbol && p.position_status === "open"),
    [assetClass, decodedSymbol, positions]
  );
  const portfolioSummary = useMemo(
    () => ({ ...summary, ...recalcSummary(wallet, positions, history) }),
    [history, positions, summary, wallet]
  );
  const selectedPosition = symbolPositions.find(p => p.id === selectedPosId)
    || symbolPositions.find(p => p.id === focusPositionId)
    || symbolPositions[0]
    || null;
  const detailsPosition = positions.find(p => p.id === detailsPosId) || null;
  const tpslManagePosition = positions.find(p => p.id === tpslManagePosId) || null;
  const tpslEditPosition = positions.find(p => p.id === tpslEditPosId) || null;
  const tpslDeletePosition = positions.find(p => p.id === tpslDeletePosId) || null;

  const showToastMsg = useCallback((msg, type = "ok") => {
    setToast({ msg, type });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const syncPaperData = useCallback((data) => {
    if (data.wallet) setWallet(data.wallet);
    if (data.positions) setPositions(data.positions.map(normalizePosition));
    if (data.history) setHistory(data.history.map(normalizePosition));
    if (data.summary) setSummary(data.summary);
  }, []);

  const fetchPaperPortfolio = useCallback(async () => {
    try {
      setPaperLoading(true);
      const data = await portfolioRequest("/api/portfolio/paper");
      syncPaperData(data);
    } catch (err) {
      showToastMsg(err.message || "Failed to load paper portfolio", "err");
    } finally {
      setPaperLoading(false);
    }
  }, [showToastMsg, syncPaperData]);

  useEffect(() => {
    const timer = setTimeout(fetchPaperPortfolio, 0);
    return () => clearTimeout(timer);
  }, [fetchPaperPortfolio]);

  useEffect(() => {
    localStorage.setItem("synthcrypto_live_timeframe", tf);
  }, [tf]);

  const fetchHistory = useCallback(async (currentTf) => {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    historyLoadedRef.current = false;
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`${SIMULATOR_URL}/api/live/history?symbol=${encodeURIComponent(decodedSymbol)}&type=${assetClass}&tf=${currentTf}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data = await res.json();
      if (requestId !== historyRequestRef.current) return;
      const rawCandles = data.data || [];
      if (rawCandles.length === 0) {
        setError(`No data available for ${decodedSymbol}. The market may be closed or the symbol may be invalid.`);
      } else {
        const sorted = normalizeCandles(rawCandles);
        setCandles(sorted);
        historyLoadedRef.current = true;
        if (data.indicators) setIndicatorData(data.indicators);
        if (sorted.length > 0) {
          const latestPrice = sorted[sorted.length - 1].close;
          setPrevPrice(priceRef.current || latestPrice);
          setPrice(latestPrice);
          priceRef.current = latestPrice;
        }
      }
    } catch (err) {
      if (requestId !== historyRequestRef.current) return;
      setError(`Failed to load data: ${err.message}. Make sure the simulator API is running on port 8000.`);
    } finally {
      if (requestId === historyRequestRef.current) setLoading(false);
    }
  }, [assetClass, decodedSymbol]);

  const syncLiveTickToServer = useCallback(async (tickPrice) => {
    const now = Date.now();
    if (tickSyncRef.current.busy || now - tickSyncRef.current.last < 1500) return;
    tickSyncRef.current = { busy: true, last: now };
    try {
      const data = await portfolioRequest("/api/portfolio/paper/tick", {
        method: "POST",
        body: JSON.stringify({
          asset_type: assetClass,
          asset_symbol: decodedSymbol,
          market_price: tickPrice,
        }),
      });
      syncPaperData(data);
      for (const event of data.events || []) {
        if (event.reason === "take_profit") {
          showToastMsg(`Take profit closed +S${Math.abs(event.trade?.profit_loss || 0).toFixed(2)}`, "ok");
        } else if (event.reason === "stop_loss") {
          showToastMsg(`Stop loss closed -S${Math.abs(event.trade?.profit_loss || 0).toFixed(2)}`, "err");
        }
      }
    } catch (err) {
      console.warn("Paper tick sync failed:", err.message);
    } finally {
      tickSyncRef.current.busy = false;
    }
  }, [assetClass, decodedSymbol, showToastMsg, syncPaperData]);

  useEffect(() => {
    const timer = setTimeout(() => fetchHistory(tf), 0);
    return () => clearTimeout(timer);
  }, [fetchHistory, tf]);

  useEffect(() => {
    const sock = io(SIMULATOR_URL, {
      autoConnect: false,
      path: "/ws/socket.io",
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 800,
      reconnectionDelayMax: 5000,
    });
    socketRef.current = sock;
    sock.connect();

    sock.on("connect", () => {
      setConnected(true);
      sock.emit("subscribe_live_market", { type: assetClass, symbol: decodedSymbol });
    });

    sock.on("disconnect", () => setConnected(false));
    sock.on("connect_error", () => setConnected(false));
    sock.io.on("reconnect", () => {
      setConnected(true);
      sock.emit("subscribe_live_market", { type: assetClass, symbol: decodedSymbol });
    });

    sock.on("live_tick", (ticker) => {
      if (!ticker || ticker.symbol !== decodedSymbol || !ticker.price) return;
      const tickPrice = Number(ticker.price);
      if (!Number.isFinite(tickPrice) || tickPrice <= 0) return;

      const tickTime = toUnixSeconds(ticker.time || ticker.timestamp);
      const tickVolume = Number.isFinite(Number(ticker.volume)) ? Number(ticker.volume) : 0;

      setPrevPrice(priceRef.current || tickPrice);
      setPrice(tickPrice);
      priceRef.current = tickPrice;

      setPositions(prev => prev.map(p => markPosition(p, assetClass, decodedSymbol, tickPrice)));
      syncLiveTickToServer(tickPrice);

      if (!historyLoadedRef.current) return;

      setCandles(prev => {
        if (prev.length === 0) return prev;
        const bucket = candleBucket(tickTime, tfRef.current);
        const updated = [...prev];
        const last = { ...updated[updated.length - 1] };
        const lastTime = Math.floor(Number(last.time));

        if (bucket > lastTime) {
          const next = {
            time: bucket,
            open: last.close,
            high: Math.max(last.close, tickPrice),
            low: Math.min(last.close, tickPrice),
            close: tickPrice,
            volume: tickVolume,
          };
          setLiveCandle(next);
          return [...updated, next].slice(-2000);
        }

        last.close = tickPrice;
        last.high = Math.max(Number(last.high), tickPrice);
        last.low = Math.min(Number(last.low), tickPrice);
        last.volume = (Number(last.volume) || 0) + tickVolume;
        updated[updated.length - 1] = last;
        setLiveCandle(last);
        return updated;
      });
    });

    return () => {
      sock.emit("unsubscribe_live_market", {});
      sock.off();
      sock.disconnect();
    };
  }, [assetClass, decodedSymbol, syncLiveTickToServer]);

  const switchTf = (nextTf) => {
    if (nextTf === tfRef.current) return;
    setTf(nextTf);
    tfRef.current = nextTf;
    setCandles([]);
    setLiveCandle(null);
    setIndicatorData({});
    historyLoadedRef.current = false;
  };

  const placeOrder = async () => {
    if (orderBusy) return;
    const markPrice = priceRef.current || price;
    if (!Number.isFinite(markPrice) || markPrice <= 0) {
      showToastMsg("Live price is not ready", "err");
      return;
    }

    setOrderBusy(true);
    try {
      if (tradeMode === "buy") {
        const amount = Number(sizeUsd);
        const qty = quantity === "" ? null : Number(quantity);
        if ((!Number.isFinite(amount) || amount <= 0) && (!Number.isFinite(qty) || qty <= 0)) {
          throw new Error("Enter an amount or quantity greater than zero");
        }
        const data = await portfolioRequest("/api/portfolio/paper/buy", {
          method: "POST",
          body: JSON.stringify({
            asset_type: assetClass,
            asset_symbol: decodedSymbol,
            invested_amount: Number.isFinite(amount) && amount > 0 ? amount : undefined,
            quantity: Number.isFinite(qty) && qty > 0 ? qty : undefined,
            take_profit: takeProfit || undefined,
            stop_loss: stopLoss || undefined,
            market_price: markPrice,
          }),
        });
        syncPaperData(data);
        setSelectedPosId(data.trade?.id || data.trade?.trade_id || null);
        showToastMsg(`Bought ${decodedSymbol} @ ${fmtPrice(data.trade?.entry_price)}`, "ok");
      } else {
        if (!selectedPosition) throw new Error("Select an open position to close");
        const data = await portfolioRequest("/api/portfolio/paper/sell", {
          method: "POST",
          body: JSON.stringify({
            trade_id: selectedPosition.id,
            market_price: markPrice,
          }),
        });
        syncPaperData(data);
        showToastMsg(`Closed ${decodedSymbol} ${data.trade?.profit_loss >= 0 ? "+" : "-"}S${Math.abs(data.trade?.profit_loss || 0).toFixed(2)}`, data.trade?.profit_loss >= 0 ? "ok" : "err");
      }
    } catch (err) {
      showToastMsg(err.message || "Order failed", "err");
    } finally {
      setOrderBusy(false);
    }
  };

  const closePosition = async (positionId) => {
    setSelectedPosId(positionId);
    setTradeMode("sell");
    const markPrice = priceRef.current || price;
    setOrderBusy(true);
    try {
      const data = await portfolioRequest("/api/portfolio/paper/sell", {
        method: "POST",
        body: JSON.stringify({ trade_id: positionId, market_price: markPrice }),
      });
      syncPaperData(data);
      showToastMsg(`Closed ${data.trade?.asset_symbol || decodedSymbol}`, data.trade?.profit_loss >= 0 ? "ok" : "err");
    } catch (err) {
      showToastMsg(err.message || "Close failed", "err");
    } finally {
      setOrderBusy(false);
    }
  };

  const updatePositionTpsl = useCallback(async (positionId, next) => {
    try {
      const data = await portfolioRequest(`/api/portfolio/paper/trades/${positionId}`, {
        method: "PATCH",
        body: JSON.stringify({
          take_profit: next.tp_price ?? next.take_profit ?? null,
          stop_loss: next.sl_price ?? next.stop_loss ?? null,
        }),
      });
      syncPaperData(data);
      showToastMsg("TP/SL updated", "ok");
      return true;
    } catch (err) {
      showToastMsg(err.message || "TP/SL update failed", "err");
      return false;
    }
  }, [showToastMsg, syncPaperData]);

  const removePositionTpsl = useCallback(async (positionId) => {
    try {
      setPositions(prev => prev.map(p => p.id === positionId ? {
        ...p,
        take_profit: null,
        stop_loss: null,
        tp_price: null,
        sl_price: null,
      } : p));
      const data = await portfolioRequest(`/api/portfolio/paper/trades/${positionId}/tpsl`, {
        method: "DELETE",
      });
      syncPaperData(data);
      setTpslManagePosId(null);
      setTpslEditPosId(null);
      setTpslDeletePosId(null);
      showToastMsg("TP/SL removed", "ok");
      return true;
    } catch (err) {
      showToastMsg(err.message || "TP/SL remove failed", "err");
      return false;
    }
  }, [showToastMsg, syncPaperData]);

  const openTpslManage = useCallback((positionId) => {
    const position = positions.find(p => p.id === positionId);
    if (!position) return;
    setSelectedPosId(position.id);
    setTpslManagePosId(position.id);
  }, [positions]);

  const openTpslEditModal = useCallback((position) => {
    if (!position) return;
    const defaults = defaultTpsl(position);
    setSelectedPosId(position.id);
    setTpslDraft({
      tp: String(position.take_profit ?? defaults.tp.toFixed(6)),
      sl: String(position.stop_loss ?? defaults.sl.toFixed(6)),
    });
    setTpslManagePosId(null);
    setTpslEditPosId(position.id);
  }, []);

  const saveTpslEditModal = useCallback(async () => {
    if (!tpslEditPosition) return;
    const ok = await updatePositionTpsl(tpslEditPosition.id, {
      take_profit: tpslDraft.tp === "" ? null : tpslDraft.tp,
      stop_loss: tpslDraft.sl === "" ? null : tpslDraft.sl,
    });
    if (ok) setTpslEditPosId(null);
  }, [tpslDraft, tpslEditPosition, updatePositionTpsl]);

  const saveSelectedTpsl = () => {
    if (!selectedPosition) {
      showToastMsg("Select a position first", "err");
      return;
    }
    updatePositionTpsl(selectedPosition.id, {
      take_profit: takeProfit || null,
      stop_loss: stopLoss || null,
    });
  };

  const selectPositionForEdit = (position) => {
    setSelectedPosId(position.id);
    setTakeProfit(position.take_profit == null ? "" : String(position.take_profit));
    setStopLoss(position.stop_loss == null ? "" : String(position.stop_loss));
  };

  const estimatedQty = price > 0 && Number(sizeUsd) > 0 ? Number(sizeUsd) / price : 0;
  const totalUpnl = symbolPositions.reduce((sum, p) => sum + (Number(p.upnl) || 0), 0);

  let ohlc = { o: "-", h: "-", l: "-", c: "-" };
  if (candles.length > 0) {
    const last = candles[candles.length - 1];
    ohlc = { o: fmtPrice(last.open), h: fmtPrice(last.high), l: fmtPrice(last.low), c: fmtPrice(last.close) };
  }

  const visibleHistory = useMemo(() => {
    const rows = historyFilter === "symbol"
      ? history.filter(t => t.asset_type === assetClass && t.asset_symbol === decodedSymbol)
      : history;
    return [...rows].sort((a, b) => {
      if (historySort === "pnl") return (Number(b.profit_loss) || 0) - (Number(a.profit_loss) || 0);
      if (historySort === "duration") return tradeDurationMs(b) - tradeDurationMs(a);
      return new Date(b.closed_at || 0) - new Date(a.closed_at || 0);
    }).slice(0, 8);
  }, [assetClass, decodedSymbol, history, historyFilter, historySort]);

  return (
    <div className="sim-page">
      <div className="toolbar">
        {onBack && <StarBorder as="button" className="btn" onClick={onBack} style={{ marginRight: 4 }}>Back</StarBorder>}
        <span className="logo">SynthCrypto</span>
        <div className="sep" />
        <span className="label-sm">TF</span>
        <div className="btn-group">
          {TFS.map(t => <StarBorder as="button" key={t} className={`btn${tf === t ? " active" : ""}`} onClick={() => switchTf(t)}>{t}</StarBorder>)}
        </div>
        <div className="sep" />
        <span className="label-sm">Symbol</span>
        <span style={{ fontWeight: "bold", marginLeft: 4 }}>{decodedSymbol}</span>
      </div>

      <div className="indbar">
        {IND_BTNS.map((group, groupIndex) => (
          <span key={group.g} style={{ display: "contents" }}>
            <span className="label-sm">{group.g}</span>
            {group.items.map(item => (
              <StarBorder
                as="button"
                key={item.k}
                className={`btn${activeInds.has(item.k) ? " active" : ""}`}
                onClick={() => setActiveInds(current => {
                  const next = new Set(current);
                  next.has(item.k) ? next.delete(item.k) : next.add(item.k);
                  return next;
                })}
              >
                {item.l}
              </StarBorder>
            ))}
            {groupIndex < IND_BTNS.length - 1 && <div className="sep" />}
          </span>
        ))}
        <div className="sep" />
        <span className="label-sm">Oscillator</span>
        {OSC_BTNS.map(o => <StarBorder as="button" key={o.k} className={`btn${activeOsc === o.k ? " active" : ""}`} onClick={() => setActiveOsc(o.k)}>{o.l}</StarBorder>)}
      </div>

      <div className="priceinfo">
        <span className={`pi-price ${price >= (prevPrice || 0) ? "up" : "dn"}`}>{fmtPrice(price)}</span>
        <span className="pi-lbl">Live Data</span><span className="pi-badge badge-bull">{assetClass.toUpperCase()}</span>
        <span className="pi-lbl">O</span><span className="pi-ohlc">{ohlc.o}</span>
        <span className="pi-lbl">H</span><span className="pi-ohlc up">{ohlc.h}</span>
        <span className="pi-lbl">L</span><span className="pi-ohlc dn">{ohlc.l}</span>
        <span className="pi-lbl">C</span><span className="pi-ohlc">{ohlc.c}</span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: connected ? "#26a69a" : "#ef5350" }}>
          <div className={`conn-indicator ${connected ? "live" : ""}`} style={{ position: "static" }}>{connected ? "live" : "reconnecting"}</div>
        </div>
      </div>

      <div className="charts-wrap">
        <div className="charts-col">
          <div className="main-wrap">
            <ChartErrorBoundary>
              <SimChart
                candles={candles}
                timeframe={tf}
                liveCandle={liveCandle}
                volumeData={activeInds.has("volume") ? volData : []}
                indicatorData={indicatorData}
                activeInds={activeInds}
                activeOsc={activeOsc}
                positions={symbolPositions}
                currentPrice={price}
                selectedPositionId={selectedPosition?.id}
                onSelectPosition={setSelectedPosId}
                onOpenPositionDetails={setDetailsPosId}
                onUpdatePositionTpsl={updatePositionTpsl}
                onManagePositionTpsl={openTpslManage}
              />
            </ChartErrorBoundary>
            {loading && (
              <div style={{ position: "absolute", inset: 0, zIndex: 60, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: 12, color: "#787b86", background: "rgba(19,23,34,0.92)" }}>
                <div style={{ width: 32, height: 32, border: "3px solid #2a2e39", borderTop: "3px solid #26a69a", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
                <span>Loading market data for {decodedSymbol}...</span>
              </div>
            )}
            {!loading && error && (
              <div style={{ position: "absolute", inset: 0, zIndex: 60, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: 12, color: "#787b86", background: "rgba(19,23,34,0.92)" }}>
                <div style={{ color: "#ef5350", fontSize: 16, fontWeight: 600 }}>{error}</div>
                <div style={{ display: "flex", gap: 8 }}>
                  <StarBorder as="button" className="btn" onClick={() => fetchHistory(tf)}>Retry</StarBorder>
                  <StarBorder as="button" className="btn" onClick={onBack}>Back to Dashboard</StarBorder>
                </div>
              </div>
            )}
            {!loading && !error && candles.length > 0 && (
              <div style={{ position: "absolute", top: 6, right: 80, zIndex: 50, background: "rgba(19,23,34,0.85)", border: "1px solid #2a2e39", borderRadius: 4, padding: "2px 8px", fontSize: 10, color: "#787b86" }}>
                {candles.length} candles
              </div>
            )}
          </div>
          <div className="osc-wrap">
            <div className="osc-label">{OSC_BTNS.find(o => o.k === activeOsc)?.l || activeOsc}</div>
            <div className="osc-chart-div" style={{ background: "#131722", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#4c5166", fontSize: 11 }}>Oscillator: {activeOsc}</div>
          </div>
        </div>

        <div className="trade-panel">
          <div className="tp-header">
            <span style={{ fontWeight: 700, color: "#d1d4dc" }}>Paper Trade</span>
            <span className={portfolioSummary.total_profit_loss >= 0 ? "up" : "dn"}>S{fmtMoney(portfolioSummary.total_portfolio_value)}</span>
          </div>

          <div className="tp-row" style={{ gap: 4 }}>
            <StarBorder as="button" className={`tp-side-btn long${tradeMode === "buy" ? " active" : ""}`} onClick={() => setTradeMode("buy")}>Buy / Long</StarBorder>
            <StarBorder as="button" className={`tp-side-btn short${tradeMode === "sell" ? " active" : ""}`} onClick={() => setTradeMode("sell")}>Sell / Short</StarBorder>
          </div>

          <div className="tp-row">
            <span className="tp-lbl">Order</span>
            <div className="btn-group">
              <StarBorder as="button" className={`btn${otype === "market" ? " active" : ""}`} onClick={() => setOtype("market")}>Market</StarBorder>
              <StarBorder as="button" className={`btn${otype === "limit" ? " active" : ""}`} onClick={() => setOtype("limit")}>Limit</StarBorder>
              <StarBorder as="button" className={`btn${otype === "stop_market" ? " active" : ""}`} onClick={() => setOtype("stop_market")}>Stop</StarBorder>
              <StarBorder as="button" className={`btn${otype === "stop_limit" ? " active" : ""}`} onClick={() => setOtype("stop_limit")}>Stop Limit</StarBorder>
            </div>
            <span style={{ color: "#787b86", fontSize: 11, minWidth: 45, textAlign: "right" }}>@ {fmtPrice(price)}</span>
          </div>

          {otype !== "market" && <div className="tp-row">
            <span className="tp-lbl">{otype === "limit" ? "Limit Price" : "Trigger Price"}</span>
            <input type="number" min={0} value={trigPrice} onChange={e => setTrigPrice(e.target.value)} style={{ flex: 1 }} placeholder="Enter price" />
          </div>}
          {otype === "stop_limit" && <div className="tp-row">
            <span className="tp-lbl">Limit Price</span>
            <input type="number" min={0} value={limitPrice} onChange={e => setLimitPrice(e.target.value)} style={{ flex: 1 }} placeholder="Enter limit price" />
          </div>}

          <div className="tp-row">
            <span className="tp-lbl">Amount S</span>
            <input type="number" min={1} value={sizeUsd} onChange={e => setSizeUsd(e.target.value)} style={{ flex: 1 }} />
          </div>
          <div className="tp-row">
            <span className="tp-lbl">Quantity</span>
            <input type="number" min={0} value={quantity} onChange={e => setQuantity(e.target.value)} style={{ flex: 1 }} placeholder={estimatedQty ? estimatedQty.toFixed(8) : "optional"} />
          </div>
          <div className="tp-row">
            <span className="tp-lbl">Take Profit</span>
            <input type="number" min={0} value={takeProfit} onChange={e => setTakeProfit(e.target.value)} style={{ flex: 1 }} placeholder="optional" />
          </div>
          <div className="tp-row">
            <span className="tp-lbl">Stop Loss</span>
            <input type="number" min={0} value={stopLoss} onChange={e => setStopLoss(e.target.value)} style={{ flex: 1 }} placeholder="optional" />
          </div>

          <div className="tp-info">
            <div><span className="tp-lbl">Cash</span><span>S{fmtMoney(wallet.virtual_balance)}</span></div>
            <div><span className="tp-lbl">Value</span><span>S{fmtMoney(portfolioSummary.total_portfolio_value)}</span></div>
            <div><span className="tp-lbl">U-PnL</span><span className={portfolioSummary.unrealized_profit_loss >= 0 ? "up" : "dn"}>{portfolioSummary.unrealized_profit_loss >= 0 ? "+" : ""}S{Math.abs(portfolioSummary.unrealized_profit_loss || 0).toFixed(2)}</span></div>
            <div><span className="tp-lbl">Est Qty</span><span>{estimatedQty.toFixed(8)}</span></div>
          </div>

          <StarBorder as="button" className={`tp-place ${tradeMode === "buy" ? "long" : "short"}`} onClick={placeOrder} disabled={orderBusy}>
            {orderBusy ? "Working..." : tradeMode === "buy" ? (otype === "market" ? "Buy Market" : "Place Order") : (otype === "market" ? "Sell Market" : "Place Order")}
          </StarBorder>
          {selectedPosition && (
            <StarBorder as="button" className="btn" onClick={saveSelectedTpsl} style={{ margin: "0 8px 7px", width: "calc(100% - 16px)" }}>
              Save TP/SL for selected
            </StarBorder>
          )}
          {toast && <div className={`tp-toast toast-${toast.type}`}>{toast.msg}</div>}
          {paperLoading && <div className="tp-empty">Loading portfolio...</div>}

          <div className="tp-section"><span>Open / Pending Positions</span><span className={totalUpnl >= 0 ? "up" : "dn"}>{symbolPositions.length ? `${totalUpnl >= 0 ? "+" : ""}S${totalUpnl.toFixed(2)}` : "-"}</span></div>
          <div>
            {!symbolPositions.length ? <div className="tp-empty">No open or pending positions for {decodedSymbol}</div> : symbolPositions.map(position => (
              <div className={`pos-card${selectedPosition?.id === position.id ? " selected" : ""}`} key={position.id} onClick={() => selectPositionForEdit(position)}>
                <div className="pos-card-row">
                  <span className={position.side === "short" ? "dn" : "up"}>
                    {position.position_status === "pending" ? "PENDING " : ""}
                    {position.side === "short" ? "SHORT" : "LONG"} {position.asset_symbol}
                  </span>
                  <span className={position.upnl >= 0 ? "up" : "dn"}>
                    {position.upnl >= 0 ? "+" : ""}S{position.upnl.toFixed(2)} ({position.upnl_pct >= 0 ? "+" : ""}{position.upnl_pct.toFixed(1)}%)
                  </span>
                </div>
                <div className="pos-card-row" style={{ color: "#787b86" }}>
                  <span>{position.position_status === "pending" ? (position.order_type === "limit" ? "Limit" : "Trigger") : "Entry"}: {fmtPrice(position.position_status === "pending" ? (position.limit_price || position.trigger_price) : position.entry_price)}</span>
                  <span>Qty: {position.quantity.toFixed(6)}</span>
                </div>
                <div className="pos-card-row tpsl-card-row"><span>TP: <span className="up">{position.take_profit ? fmtPrice(position.take_profit) : "-"}</span></span><span>SL: <span className="dn">{position.stop_loss ? fmtPrice(position.stop_loss) : "-"}</span></span></div>
                <div className="pos-card-row" style={{ color: "#787b86" }}><span className="up">Invested: S{position.size_usd.toFixed(2)}</span><span>Value: S{position.current_value.toFixed(2)}</span></div>
                <div className="pos-card-actions">
                  <StarBorder as="button" className="pos-card-tpsl" onClick={e => { e.stopPropagation(); openTpslEditModal(position); }}>Edit TP/SL</StarBorder>
                  <StarBorder as="button" className="pos-card-tpsl" onClick={e => { e.stopPropagation(); setDetailsPosId(position.id); }}>Details</StarBorder>
                </div>
                <StarBorder as="button" className="pos-card-close" onClick={e => { e.stopPropagation(); closePosition(position.id); }}>{position.position_status === "pending" ? "Cancel" : "Close"}</StarBorder>
              </div>
            ))}
          </div>

          <div className="tp-section"><span>Portfolio</span><span className={portfolioSummary.total_profit_loss >= 0 ? "up" : "dn"}>{portfolioSummary.total_profit_loss >= 0 ? "+" : ""}S{Math.abs(portfolioSummary.total_profit_loss || 0).toFixed(2)}</span></div>
          <div className="tp-info">
            <div><span className="tp-lbl">Realized</span><span className={portfolioSummary.realized_profit_loss >= 0 ? "up" : "dn"}>{portfolioSummary.realized_profit_loss >= 0 ? "+" : ""}S{Math.abs(portfolioSummary.realized_profit_loss || 0).toFixed(2)}</span></div>
            <div><span className="tp-lbl">Win Rate</span><span>{portfolioSummary.win_rate_percentage == null ? "-" : `${portfolioSummary.win_rate_percentage.toFixed(1)}%`}</span></div>
            <div><span className="tp-lbl">Closed</span><span>{portfolioSummary.closed_trades || 0}</span></div>
          </div>

          <div className="tp-section"><span>Trade History</span><span>{visibleHistory.length}</span></div>
          <div className="tp-row" style={{ gap: 6 }}>
            <select value={historyFilter} onChange={e => setHistoryFilter(e.target.value)} style={{ flex: 1, background: "#131722", color: "#d1d4dc", border: "1px solid #2a2e39", borderRadius: 3, padding: "3px 5px", fontSize: 11 }}>
              <option value="symbol">This symbol</option>
              <option value="all">All assets</option>
            </select>
            <select value={historySort} onChange={e => setHistorySort(e.target.value)} style={{ flex: 1, background: "#131722", color: "#d1d4dc", border: "1px solid #2a2e39", borderRadius: 3, padding: "3px 5px", fontSize: 11 }}>
              <option value="newest">Newest</option>
              <option value="pnl">P/L</option>
              <option value="duration">Duration</option>
            </select>
          </div>
          <div>
            {!visibleHistory.length ? <div className="tp-empty">No closed trades</div> : visibleHistory.map(trade => (
              <div className="ord-card" key={trade.id}>
                <div className="pos-card-row"><span>{trade.asset_symbol}</span><span className={trade.profit_loss >= 0 ? "up" : "dn"}>{trade.profit_loss >= 0 ? "+" : ""}S{trade.profit_loss.toFixed(2)}</span></div>
                <div className="pos-card-row" style={{ color: "#787b86" }}><span>{fmtPrice(trade.entry_price)}{" -> "}{fmtPrice(trade.exit_price)}</span><span>{fmtDuration(tradeDurationMs(trade))}</span></div>
                <div className="pos-card-row" style={{ color: "#787b86" }}><span>Qty {trade.quantity.toFixed(6)}</span><span>S{trade.invested_amount.toFixed(2)}</span></div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {tpslManagePosition && (
        <div className="overlay-backdrop" onClick={() => setTpslManagePosId(null)}>
          <div className="overlay-panel tpsl-modal" onClick={e => e.stopPropagation()}>
            <h3>Manage TP/SL</h3>
            <div className="metric-row"><span>Position</span><span>{tpslManagePosition.asset_symbol} @ {fmtPrice(tpslManagePosition.entry_price)}</span></div>
            <div className="metric-row"><span>Take profit</span><span className="up">{tpslManagePosition.take_profit ? fmtPrice(tpslManagePosition.take_profit) : "-"}</span></div>
            <div className="metric-row"><span>Stop loss</span><span className="dn">{tpslManagePosition.stop_loss ? fmtPrice(tpslManagePosition.stop_loss) : "-"}</span></div>
            <div className="detail-actions tpsl-modal-actions">
              <StarBorder as="button" className="btn ctrl-green" onClick={() => openTpslEditModal(tpslManagePosition)}>Edit</StarBorder>
              <StarBorder as="button" className="btn ctrl-red" onClick={() => { setTpslDeletePosId(tpslManagePosition.id); setTpslManagePosId(null); }}>Remove TP/SL</StarBorder>
              <StarBorder as="button" className="overlay-close" onClick={() => setTpslManagePosId(null)}>Cancel</StarBorder>
            </div>
          </div>
        </div>
      )}

      {tpslEditPosition && (
        <div className="overlay-backdrop" onClick={() => setTpslEditPosId(null)}>
          <div className="overlay-panel tpsl-modal" onClick={e => e.stopPropagation()}>
            <h3>Edit TP/SL</h3>
            <div className="metric-row"><span>Entry</span><span>{fmtPrice(tpslEditPosition.entry_price)}</span></div>
            <label className="tpsl-modal-field"><span>Take Profit</span><input type="number" value={tpslDraft.tp} onChange={e => setTpslDraft(d => ({ ...d, tp: e.target.value }))} /></label>
            <label className="tpsl-modal-field"><span>Stop Loss</span><input type="number" value={tpslDraft.sl} onChange={e => setTpslDraft(d => ({ ...d, sl: e.target.value }))} /></label>
            <div className="tpsl-summary">
              <span>R/R {riskReward(tpslEditPosition, toOptionalPrice(tpslDraft.tp), toOptionalPrice(tpslDraft.sl))}</span>
            </div>
            <div className="detail-actions tpsl-modal-actions">
              <StarBorder as="button" className="btn ctrl-green" onClick={saveTpslEditModal}>Save Changes</StarBorder>
              <StarBorder as="button" className="btn ctrl-red" onClick={() => setTpslDeletePosId(tpslEditPosition.id)}>Remove TP/SL</StarBorder>
              <StarBorder as="button" className="overlay-close" onClick={() => setTpslEditPosId(null)}>Cancel</StarBorder>
            </div>
          </div>
        </div>
      )}

      {tpslDeletePosition && (
        <div className="overlay-backdrop" onClick={() => setTpslDeletePosId(null)}>
          <div className="overlay-panel tpsl-modal" onClick={e => e.stopPropagation()}>
            <h3>Remove TP/SL</h3>
            <p className="tpsl-confirm-copy">Remove Take Profit and Stop Loss from this position?</p>
            <div className="detail-actions">
              <StarBorder as="button" className="btn ctrl-red" onClick={() => removePositionTpsl(tpslDeletePosition.id)}>Remove TP/SL</StarBorder>
              <StarBorder as="button" className="overlay-close" onClick={() => setTpslDeletePosId(null)}>Cancel</StarBorder>
            </div>
          </div>
        </div>
      )}

      {detailsPosition && (
        <div className="overlay-backdrop" onClick={() => setDetailsPosId(null)}>
          <div className="overlay-panel position-detail-panel" onClick={e => e.stopPropagation()}>
            <h3>{detailsPosition.asset_symbol} Position</h3>
            <div className="metric-row"><span>Entry price</span><span>{fmtPrice(detailsPosition.entry_price)}</span></div>
            <div className="metric-row"><span>Current price</span><span>{fmtPrice(price)}</span></div>
            <div className="metric-row"><span>Take profit</span><span className="up">{detailsPosition.take_profit ? fmtPrice(detailsPosition.take_profit) : "-"}</span></div>
            <div className="metric-row"><span>Stop loss</span><span className="dn">{detailsPosition.stop_loss ? fmtPrice(detailsPosition.stop_loss) : "-"}</span></div>
            <div className="metric-row"><span>P/L</span><span className={detailsPosition.profit_loss >= 0 ? "up" : "dn"}>{detailsPosition.profit_loss >= 0 ? "+" : ""}S{Math.abs(detailsPosition.profit_loss).toFixed(2)} ({detailsPosition.profit_loss_percentage >= 0 ? "+" : ""}{detailsPosition.profit_loss_percentage.toFixed(1)}%)</span></div>
            <div className="metric-row"><span>Quantity</span><span>{detailsPosition.quantity.toFixed(8)}</span></div>
            <div className="metric-row"><span>Invested</span><span>S{detailsPosition.invested_amount.toFixed(2)}</span></div>
            <div className="detail-actions">
              <StarBorder as="button" className="btn ctrl-green" onClick={() => openTpslEditModal(detailsPosition)}>Edit TP/SL</StarBorder>
              <StarBorder as="button" className="overlay-close" onClick={() => setDetailsPosId(null)}>Close</StarBorder>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
