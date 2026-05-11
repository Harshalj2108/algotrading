import { useState, useEffect, useRef, useCallback, useMemo, Component } from 'react';
import { io } from 'socket.io-client';
import SimChart from './SimChart';
import StarBorder from './StarBorder';
import './SimulatorPage.css';

class ChartErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false }; }
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(err) { console.warn("Chart error caught:", err); }
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

const SIMULATOR_URL = "http://localhost:8000";

const TFS = ["1m","5m","15m","30m","1h","1d"];
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };
const IND_BTNS = [
  {g:"Trend",items:[{k:"sma20",l:"SMA 20"},{k:"sma50",l:"SMA 50"},{k:"sma200",l:"SMA 200"},{k:"ema9",l:"EMA 9"},{k:"ema20",l:"EMA 20"},{k:"ema50",l:"EMA 50"},{k:"wma20",l:"WMA 20"},{k:"vwap",l:"VWAP"},{k:"ichimoku",l:"Ichimoku"}]},
  {g:"Bands",items:[{k:"bb",l:"Bollinger"},{k:"keltner",l:"Keltner"}]},
  {g:"Vol",items:[{k:"volume",l:"Volume",on:true}]},
];
const OSC_BTNS = [{k:"rsi14",l:"RSI"},{k:"macd",l:"MACD"},{k:"stoch",l:"Stoch"},{k:"cci20",l:"CCI"},{k:"williams_r",l:"W%R"},{k:"atr14",l:"ATR"},{k:"obv",l:"OBV"},{k:"cmf20",l:"CMF"}];

function fmtPrice(v){if(v==null)return"—";v=+v;if(v>=10000)return v.toFixed(2);if(v>=100)return v.toFixed(3);if(v>=1)return v.toFixed(4);return v.toFixed(6);}

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
    color: (c.close >= c.open) ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
  }));
}

export default function LiveMarketPage({ assetClass, symbol, onBack }) {
  const decodedSymbol = decodeURIComponent(symbol);

  const [tf, setTf] = useState("5m");
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
  // Prevent live_tick from creating candles before history has loaded
  const historyLoadedRef = useRef(false);

  // Trading placeholders
  const [balance] = useState(10000);
  const [rpnl] = useState(0);
  const [positions] = useState([]);
  const [orders] = useState([]);
  const [side, setSide] = useState("long");
  const [otype, setOtype] = useState("market");
  const [lev, setLev] = useState(1);
  const [sizeUsd, setSizeUsd] = useState(100);
  const [trigPrice, setTrigPrice] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [toast, setToast] = useState(null);

  const [activeInds, setActiveInds] = useState(new Set(["volume"]));
  const [activeOsc, setActiveOsc] = useState("rsi14");
  const [indicatorData, setIndicatorData] = useState({});
  const toastTimer = useRef();

  const showToastMsg = useCallback((msg, type="ok") => {
    setToast({msg,type});
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const fetchHistory = useCallback(async (currentTf) => {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    historyLoadedRef.current = false;
    try {
      setLoading(true);
      setError(null);
      console.log(`[LiveMarket] Fetching history: symbol=${decodedSymbol} type=${assetClass} tf=${currentTf}`);
      const res = await fetch(`${SIMULATOR_URL}/api/live/history?symbol=${encodeURIComponent(decodedSymbol)}&type=${assetClass}&tf=${currentTf}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data = await res.json();
      if (requestId !== historyRequestRef.current) return;
      const rawCandles = data.data || [];
      console.log(`[LiveMarket] Received ${rawCandles.length} raw candles`);
      if (rawCandles.length === 0) {
        setError(`No data available for ${decodedSymbol}. The market may be closed or the symbol may be invalid.`);
      } else {
        const sorted = normalizeCandles(rawCandles);
        console.log(`[LiveMarket] Normalized to ${sorted.length} candles`);
        setCandles(sorted);
        historyLoadedRef.current = true;
        if (data.indicators) setIndicatorData(data.indicators);
        
        if (sorted.length > 0) {
          const lp = sorted[sorted.length - 1].close;
          setPrevPrice(priceRef.current || lp);
          setPrice(lp);
          priceRef.current = lp;
        }
      }
    } catch (e) {
      if (requestId !== historyRequestRef.current) return;
      console.error("[LiveMarket] Failed to fetch history", e);
      setError(`Failed to load data: ${e.message}. Make sure the simulator API is running on port 8000.`);
    } finally {
      if (requestId === historyRequestRef.current) setLoading(false);
    }
  }, [assetClass, decodedSymbol]);

  useEffect(() => {
    const timer = setTimeout(() => fetchHistory(tf), 0);
    return () => clearTimeout(timer);
  }, [fetchHistory, tf]);

  useEffect(() => {
    const sock = io(SIMULATOR_URL, { autoConnect: false, path: "/ws/socket.io" });
    socketRef.current = sock;
    sock.connect();

    sock.on("connect", () => {
      setConnected(true);
      sock.emit("subscribe_live_market", { type: assetClass, symbol: decodedSymbol });
    });

    sock.on("disconnect", () => setConnected(false));

    sock.on("live_tick", (ticker) => {
      if (ticker && ticker.symbol === decodedSymbol && ticker.price) {
        const tickPrice = Number(ticker.price);
        if (!Number.isFinite(tickPrice) || tickPrice <= 0) return;

        const tickTime = toUnixSeconds(ticker.time || ticker.timestamp);
        const tickVolume = Number.isFinite(Number(ticker.volume)) ? Number(ticker.volume) : 0;

        setPrevPrice(priceRef.current || tickPrice);
        setPrice(tickPrice);
        priceRef.current = tickPrice;

        // Don't create candles from ticks until historical data has loaded.
        // This prevents the "single candle" problem where a tick arrives
        // before the history fetch completes.
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
            return [...updated, next].slice(-2000);
          }

          last.close = tickPrice;
          last.high = Math.max(Number(last.high), tickPrice);
          last.low = Math.min(Number(last.low), tickPrice);
          last.volume = (Number(last.volume) || 0) + tickVolume;
          updated[updated.length - 1] = last;
          return updated;
        });
      }
    });

    return () => {
      sock.emit("unsubscribe_live_market", {});
      sock.off();
      sock.disconnect();
    };
  }, [assetClass, decodedSymbol]);

  const switchTf = t => {
    if (t === tfRef.current) return;
    setTf(t); tfRef.current = t;
    setCandles([]); setLiveCandle(null); setIndicatorData({});
    historyLoadedRef.current = false;
  };

  const placeOrder = () => {
    showToastMsg("Live paper trading coming soon", "err");
  };

  const margin = (sizeUsd / lev).toFixed(2);
  const fee = (sizeUsd * (otype === "limit" ? 0.0002 : 0.0006)).toFixed(4);
  const ep = price || 0;
  const liqP = ep ? (side === "long" ? ep * (1 - 1 / lev + 0.005) : ep * (1 + 1 / lev - 0.005)) : 0;
  const totalUpnl = 0;

  // Derive OHLC from last candle
  let ohlc = { o: "—", h: "—", l: "—", c: "—" };
  if (candles.length > 0) {
    const last = candles[candles.length - 1];
    ohlc = { o: fmtPrice(last.open), h: fmtPrice(last.high), l: fmtPrice(last.low), c: fmtPrice(last.close) };
  }

  return (
    <div className="sim-page">
      {/* ── Toolbar ── */}
      <div className="toolbar">
        {onBack && <StarBorder as="button" className="btn" onClick={onBack} style={{marginRight:4}}>← Back</StarBorder>}
        <span className="logo">⬡ SynthCrypto</span><span className="v3-tag">live</span>
        <div className="sep"/>
        <span className="label-sm">TF</span>
        <div className="btn-group">
          {TFS.map(t=><StarBorder as="button" key={t} className={`btn${tf===t?" active":""}`} onClick={()=>switchTf(t)}>{t}</StarBorder>)}
        </div>
        <div className="sep"/>
        <span className="label-sm">Symbol</span>
        <span style={{ fontWeight: "bold", marginLeft: 4 }}>{decodedSymbol}</span>
      </div>

      {/* ── Indicator bar ── */}
      <div className="indbar">
        {IND_BTNS.map((g,gi)=><span key={gi} style={{display:"contents"}}>
          <span className="label-sm">{g.g}</span>
          {g.items.map(i=><StarBorder as="button" key={i.k} className={`btn${activeInds.has(i.k)?" active":""}`} onClick={()=>setActiveInds(s=>{const n=new Set(s);n.has(i.k)?n.delete(i.k):n.add(i.k);return n;})}>{i.l}</StarBorder>)}
          {gi<IND_BTNS.length-1&&<div className="sep"/>}
        </span>)}
        <div className="sep"/>
        <span className="label-sm">Oscillator</span>
        {OSC_BTNS.map(o=><StarBorder as="button" key={o.k} className={`btn${activeOsc===o.k?" active":""}`} onClick={()=>setActiveOsc(o.k)}>{o.l}</StarBorder>)}
      </div>

      {/* ── Price info bar ── */}
      <div className="priceinfo">
        <span className={`pi-price ${price >= (prevPrice || 0) ? "up" : "dn"}`}>{fmtPrice(price)}</span>
        <span className="pi-lbl">Live Data</span><span className={`pi-badge badge-bull`}>{assetClass.toUpperCase()}</span>
        <span className="pi-lbl">O</span><span className="pi-ohlc">{ohlc.o}</span>
        <span className="pi-lbl">H</span><span className="pi-ohlc up">{ohlc.h}</span>
        <span className="pi-lbl">L</span><span className="pi-ohlc dn">{ohlc.l}</span>
        <span className="pi-lbl">C</span><span className="pi-ohlc">{ohlc.c}</span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: connected ? "#26a69a" : "#ef5350" }}>
          <div className={`conn-indicator ${connected ? "live" : ""}`} style={{ position: "static" }}>● {connected ? "Connected" : "Reconnecting..."}</div>
        </div>
      </div>

      {/* ── Charts + Trade Panel ── */}
      <div className="charts-wrap">
        <div className="charts-col">
          <div className="main-wrap">
            {/* Always mount SimChart so it has stable dimensions */}
            <ChartErrorBoundary>
              <SimChart candles={candles} timeframe={tf} liveCandle={liveCandle} volumeData={activeInds.has("volume")?volData:[]} indicatorData={indicatorData} activeInds={activeInds} activeOsc={activeOsc}/>
            </ChartErrorBoundary>
            {/* Loading / error overlays on top of the chart */}
            {loading && (
              <div style={{ position: 'absolute', inset: 0, zIndex: 60, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: '12px', color: '#787b86', background: 'rgba(19,23,34,0.92)' }}>
                <div style={{ width: '32px', height: '32px', border: '3px solid #2a2e39', borderTop: '3px solid #26a69a', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
                <span>Loading market data for {decodedSymbol}...</span>
              </div>
            )}
            {!loading && error && (
              <div style={{ position: 'absolute', inset: 0, zIndex: 60, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: '12px', color: '#787b86', background: 'rgba(19,23,34,0.92)' }}>
                <div style={{ fontSize: '48px', opacity: 0.4 }}>📉</div>
                <div style={{ color: '#ef5350', fontSize: '16px', fontWeight: 600 }}>{error}</div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <StarBorder as="button" className="btn" onClick={() => fetchHistory(tf)}>↻ Retry</StarBorder>
                  <StarBorder as="button" className="btn" onClick={onBack}>← Back to Dashboard</StarBorder>
                </div>
              </div>
            )}
            {!loading && !error && candles.length > 0 && (
              <div style={{ position: 'absolute', top: 6, right: 80, zIndex: 50, background: 'rgba(19,23,34,0.85)', border: '1px solid #2a2e39', borderRadius: 4, padding: '2px 8px', fontSize: 10, color: '#787b86' }}>
                {candles.length} candles
              </div>
            )}
          </div>
          <div className="osc-wrap">
            <div className="osc-label">{OSC_BTNS.find(o=>o.k===activeOsc)?.l||activeOsc}</div>
            <div className="osc-chart-div" style={{background:"#131722",height:"100%",display:"flex",alignItems:"center",justifyContent:"center",color:"#4c5166",fontSize:11}}>Oscillator: {activeOsc}</div>
          </div>
        </div>

        {/* ── Trade Panel ── */}
        <div className="trade-panel">
          <div className="tp-header">
            <span style={{fontWeight:700,color:"#d1d4dc"}}>► Trade</span>
            <span className={balance>=10000?"up":"dn"}>${balance.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
          </div>
          <div className="tp-row" style={{flexWrap:"wrap",gap:3}}>
            <span className="tp-lbl">Type</span>
            <div className="btn-group">
              {["market","limit","stop","stop_limit"].map(t=><StarBorder as="button" key={t} className={`btn${otype===t?" active":""}`} onClick={()=>setOtype(t)}>{t==="stop_limit"?"S-Limit":t.charAt(0).toUpperCase()+t.slice(1)}</StarBorder>)}
            </div>
          </div>
          <div className="tp-row" style={{gap:4}}>
            <StarBorder as="button" className={`tp-side-btn long${side==="long"?" active":""}`} onClick={()=>setSide("long")}>▲ Long</StarBorder>
            <StarBorder as="button" className={`tp-side-btn short${side==="short"?" active":""}`} onClick={()=>setSide("short")}>▼ Short</StarBorder>
          </div>
          <div className="tp-row">
            <span className="tp-lbl">Leverage</span>
            <input type="range" min={1} max={125} value={lev} onChange={e=>{setLev(+e.target.value)}}/>
            <input type="number" min={1} max={125} value={lev} onChange={e=>setLev(+e.target.value)} style={{width:44}}/>
            <span style={{color:"#787b86"}}>×</span>
          </div>
          <div className="tp-row">
            <span className="tp-lbl">Size $</span>
            <input type="number" min={1} value={sizeUsd} onChange={e=>setSizeUsd(+e.target.value)} style={{flex:1}}/>
          </div>
          {otype!=="market"&&<div className="tp-row">
            <span className="tp-lbl">{otype==="limit"?"Price $":"Stop $"}</span>
            <input type="number" value={trigPrice} onChange={e=>setTrigPrice(e.target.value)} style={{flex:1}} placeholder="Trigger price"/>
          </div>}
          {otype==="stop_limit"&&<div className="tp-row">
            <span className="tp-lbl">Limit $</span>
            <input type="number" value={limitPrice} onChange={e=>setLimitPrice(e.target.value)} style={{flex:1}} placeholder="Limit price"/>
          </div>}
          <div className="tp-info">
            <div><span className="tp-lbl">Margin</span><span>${margin}</span></div>
            <div><span className="tp-lbl">Liq</span><span>{liqP?fmtPrice(liqP):"—"}</span></div>
            <div><span className="tp-lbl">Fee</span><span>${fee}</span></div>
          </div>
          <StarBorder as="button" className={`tp-place ${side}`} onClick={placeOrder}>{side==="long"?"Buy / Long":"Sell / Short"}</StarBorder>
          {toast&&<div className={`tp-toast toast-${toast.type}`}>{toast.msg}</div>}
          <div className="tp-section"><span>Positions</span><span className={totalUpnl>=0?"up":"dn"}>{positions.length?`${totalUpnl>=0?"+":""}$${totalUpnl.toFixed(2)}`:"—"}</span></div>
          <div>{!positions.length?<div className="tp-empty">No open positions</div>:positions.map(p=>(
            <div className="pos-card" key={p.id}>
            </div>
          ))}</div>
          <div className="tp-section"><span>Pending Orders</span></div>
          <div>{!orders.length?<div className="tp-empty">No pending orders</div>:orders.map(o=>(
            <div className="ord-card" key={o.id}>
            </div>
          ))}</div>
          <div className="tp-footer"><span className="pi-lbl">Realized PnL</span><span className={rpnl>=0?"up":"dn"}>{rpnl>=0?"+":""}${Math.abs(rpnl).toFixed(2)}</span></div>
        </div>
      </div>
    </div>
  );
}
