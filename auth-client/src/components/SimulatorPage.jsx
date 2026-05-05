import { useState, useEffect, useRef, useCallback, Component } from "react";
import { io } from "socket.io-client";
import SimChart from "./SimChart";
import StrategyEditor from "./StrategyEditor";
import { MetricsOverlay, StressOverlay, EBBOverlay } from "./SimOverlays";
import "./SimulatorPage.css";

class ChartErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { hasError: false }; }
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(err) { console.warn("Chart error caught:", err); }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#787b86", fontSize: 13, flexDirection: "column", gap: 8 }}>
          <span>Chart encountered an error</span>
          <button className="btn" onClick={() => this.setState({ hasError: false })}>Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

const API = "http://localhost:8000";
const socket = io(API, { autoConnect: false, path: "/ws/socket.io" });

const TFS = ["1s","1m","5m","15m","30m","1h","1d"];
const SPEEDS = [{l:"×1",v:1},{l:"×10",v:10},{l:"×100",v:100},{l:"×1000",v:1000},{l:"MAX",v:"max"}];
const IND_BTNS = [
  {g:"Trend",items:[{k:"sma20",l:"SMA 20"},{k:"sma50",l:"SMA 50"},{k:"sma200",l:"SMA 200"},{k:"ema9",l:"EMA 9"},{k:"ema20",l:"EMA 20"},{k:"ema50",l:"EMA 50"},{k:"wma20",l:"WMA 20"},{k:"vwap",l:"VWAP"},{k:"ichimoku",l:"Ichimoku"}]},
  {g:"Bands",items:[{k:"bb",l:"Bollinger"},{k:"keltner",l:"Keltner"}]},
  {g:"Vol",items:[{k:"volume",l:"Volume",on:true}]},
];
const OSC_BTNS = [{k:"rsi14",l:"RSI"},{k:"macd",l:"MACD"},{k:"stoch",l:"Stoch"},{k:"cci20",l:"CCI"},{k:"williams_r",l:"W%R"},{k:"atr14",l:"ATR"},{k:"obv",l:"OBV"},{k:"cmf20",l:"CMF"}];

function fmtPrice(v){if(v==null)return"—";v=+v;if(v>=10000)return v.toFixed(2);if(v>=100)return v.toFixed(3);if(v>=1)return v.toFixed(4);return v.toFixed(6);}
function fmtSim(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60),ss=s%60;if(d>0)return`${d}d ${h}h`;if(h>0)return`${h}h ${m}m`;if(m>0)return`${m}m ${ss}s`;return`${ss}s`;}
function fmtC(v){if(v>=1e9)return(v/1e9).toFixed(1)+"B";if(v>=1e6)return(v/1e6).toFixed(1)+"M";if(v>=1e3)return(v/1e3).toFixed(1)+"K";return v.toFixed(0);}

export default function SimulatorPage({ onBack }) {
  const [tf, setTf] = useState("1m");
  const [speed, setSpeed] = useState(1);
  const [paused, setPaused] = useState(false);
  const [tab, setTab] = useState("chart"); // chart | lab
  const tfRef = useRef(tf);
  // Market data
  const [candles, setCandles] = useState([]);
  const [volData, setVolData] = useState([]);
  const [liveCandle, setLiveCandle] = useState(null);
  const [price, setPrice] = useState(0);
  const [prevPrice, setPrevPrice] = useState(null);
  const [regime, setRegime] = useState("bull");
  const [step, setStep] = useState(0);
  const [ohlc, setOhlc] = useState({o:"—",h:"—",l:"—",c:"—"});
  // Phase 2
  const [p2, setP2] = useState({});
  // Trading
  const [balance, setBalance] = useState(10000);
  const [rpnl, setRpnl] = useState(0);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [side, setSide] = useState("long");
  const [otype, setOtype] = useState("market");
  const [lev, setLev] = useState(1);
  const [sizeUsd, setSizeUsd] = useState(100);
  const [trigPrice, setTrigPrice] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [toast, setToast] = useState(null);
  // Overlays
  const [showMetrics, setShowMetrics] = useState(false);
  const [metricsData, setMetricsData] = useState(null);
  const [showStress, setShowStress] = useState(false);
  const [showEbb, setShowEbb] = useState(false);
  const [ebbEnabled, setEbbEnabled] = useState(false);
  const [ebbMetrics, setEbbMetrics] = useState(null);
  const [ebbBadge, setEbbBadge] = useState(null);
  // Indicators
  const [activeInds, setActiveInds] = useState(new Set(["volume"]));
  const [activeOsc, setActiveOsc] = useState("rsi14");
  const [indicatorData, setIndicatorData] = useState({});
  const [simLoading, setSimLoading] = useState(false);
  const toastTimer = useRef();

  const showToastMsg = useCallback((msg, type="ok") => {
    setToast({msg,type});
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  // Socket setup
  useEffect(() => {
    socket.connect();
    socket.on("init", d => {
      if(d.candles) setCandles(d.candles);
      if(d.indicators) { setIndicatorData(d.indicators); if(d.indicators.volume) setVolData(d.indicators.volume); }
      if(d.price){setPrice(d.price);setPrevPrice(d.price);}
      if(d.regime) setRegime(d.regime);
      if(d.step) setStep(d.step);
      if(d.p2) setP2(d.p2);
      if(d.paused) setPaused(true);
      if(d.ebb_strategy?.metrics){setEbbMetrics(d.ebb_strategy.metrics);setEbbEnabled(!!d.ebb_strategy.metrics.enabled);}
    });
    socket.on("tf_data", d => {
      if(d.candles) setCandles(d.candles);
      if(d.indicators) { setIndicatorData(d.indicators); if(d.indicators.volume) setVolData(d.indicators.volume); }
      if(d.price){setPrevPrice(price);setPrice(d.price);}
      if(d.regime) setRegime(d.regime);
      if(d.step) setStep(d.step);
      if(d.p2) setP2(d.p2);
      if(d.ebb_strategy?.metrics) setEbbMetrics(d.ebb_strategy.metrics);
    });
    socket.on("tick", d => {
      setStep(d.step);
      setPrevPrice(p=>d.price);
      setPrice(d.price);
      setRegime(d.regime);
      if(d.live){const lc=d.live[tfRef.current];if(lc)setLiveCandle({...lc});}
      if(d.positions!==undefined)setPositions(d.positions);
      if(d.orders!==undefined)setOrders(d.orders);
      if(d.balance!==undefined)setBalance(d.balance);
      if(d.rpnl!==undefined)setRpnl(d.rpnl);
      if(d.p2){
        setP2(prev=>({...prev,...d.p2,
          garch_sigma:d.p2.sigma??prev.garch_sigma,
          volume:d.p2.volume??prev.volume,
          cascade_count:d.p2.cascade_n??prev.cascade_count,
          oi:d.p2.oi??prev.oi,
          corr_prices:d.p2.corr_prices??prev.corr_prices,
        }));
      }
      if(d.events?.liquidated?.length) d.events.liquidated.forEach(id=>showToastMsg("⚡ LIQUIDATED "+id,"liq"));
      if(d.events?.filled?.length) showToastMsg("✓ Order filled","ok");
    });
    socket.on("candle_close", d => {
      if(d.tf===tfRef.current && d.candles) setCandles(prev=>[...prev,...d.candles].slice(-2000));
    });
    socket.on("new_sim", d => {
      if(d.price){setPrice(d.price);setPrevPrice(d.price);}
      setStep(0);if(d.regime)setRegime(d.regime);if(d.p2)setP2(d.p2);
      setBalance(d.balance||10000);setRpnl(0);setPositions([]);setOrders([]);
      setEbbMetrics(null);
      // Clear chart data — server will follow up with tf_data containing fresh candles
      setCandles([]);setVolData([]);setLiveCandle(null);setIndicatorData({});
      // Reset TF to 1m
      setTf("1m");tfRef.current="1m";
      setSimLoading(false);
    });
    socket.on("paused",()=>setPaused(true));
    socket.on("resumed",()=>setPaused(false));
    socket.on("risk_metrics",d=>setMetricsData(d));
    socket.on("order_result",d=>{
      if(d.status==="filled")showToastMsg("✓ Filled"+(d.slippage>0?` (slip: $${d.slippage.toFixed(4)})`:""),"ok");
      else if(d.status==="pending")showToastMsg("⏱ Order placed","ok");
      else if(d.status==="closed")showToastMsg(`✓ Closed ${d.pnl>=0?"+":""}$${Math.abs(d.pnl).toFixed(2)}`,d.pnl>=0?"ok":"err");
      else if(d.status==="cancelled")showToastMsg("Order cancelled","ok");
      else if(d.status==="error")showToastMsg("⚠ "+d.msg,"err");
    });
    socket.on("ebb_strategy_toggled",d=>{setEbbEnabled(d.enabled);if(d.metrics)setEbbMetrics(d.metrics);});
    socket.on("ebb_strategy_update",d=>{if(d.metrics)setEbbMetrics(d.metrics);});
    socket.on("ebb_strategy_metrics",d=>setEbbMetrics(d));
    return()=>{socket.off();socket.disconnect();};
  }, []);

  // Actions
  const switchTf=t=>{setTf(t);tfRef.current=t;socket.emit("switch_tf",{tf:t});};
  const setSpd=s=>{setSpeed(s);socket.emit("set_speed",{speed:s});};
  const togglePause=()=>{paused?socket.emit("resume"):socket.emit("pause");};
  const newSim=()=>{
    if(simLoading) return; // prevent double clicks
    setSimLoading(true);
    showToastMsg("⏳ Initializing new simulation...","ok");
    // Clear chart immediately for visual feedback
    setCandles([]);setVolData([]);setLiveCandle(null);setIndicatorData({});
    socket.emit("new_sim");
    // Safety timeout in case server doesn't respond
    setTimeout(()=>setSimLoading(false), 5000);
  };
  const placeOrder=()=>{
    const sz=+sizeUsd;if(sz<=0)return showToastMsg("Size must be > 0","err");
    socket.emit("place_order",{type:otype,side,size_usd:sz,leverage:+lev,
      trigger_price:otype!=="market"?+trigPrice:undefined,
      limit_price:otype==="stop_limit"?+limitPrice:undefined});
  };
  const closePos=id=>socket.emit("close_position",{id});
  const cancelOrd=id=>socket.emit("cancel_order",{id});
  const openMetrics=()=>{setShowMetrics(true);setMetricsData(null);socket.emit("get_risk_metrics");};
  const toggleEbb=()=>{const n=!ebbEnabled;setEbbEnabled(n);socket.emit("toggle_ebb_strategy",{enabled:n});showToastMsg(n?"EBB Scalper ENABLED (5m)":"EBB Scalper disabled","ok");};
  const openEbbMetrics=e=>{e.preventDefault();setShowEbb(true);socket.emit("get_ebb_strategy_metrics");};

  const margin=(sizeUsd/lev).toFixed(2);
  const fee=(sizeUsd*(otype==="limit"?0.0002:0.0006)).toFixed(4);
  const ep=price||0;
  const liqP=ep?(side==="long"?ep*(1-1/lev+0.005):ep*(1+1/lev-0.005)):0;
  const totalUpnl=positions.reduce((s,p)=>s+(p.upnl||0),0);

  if(tab==="lab") return (
    <div className="sim-page">
      <div className="toolbar">
        {onBack&&<button className="btn" onClick={onBack} style={{marginRight:4}}>← Back</button>}
        <span className="logo">⬡ SynthCrypto</span><span className="v3-tag">v3</span>
        <div className="sep"/>
        <button className="btn active" onClick={()=>setTab("chart")}>Dashboard</button>
        <button className="btn ctrl-purple" onClick={()=>setTab("lab")}>Strategy Lab</button>
        {onBack&&<><div className="sep"/><button className="btn" onClick={onBack}>📁 Portfolio</button></>}
      </div>
      <div className="strategy-lab-container">
        <StrategyEditor socket={socket} apiBase={API}/>
      </div>
    </div>
  );

  return (
    <div className="sim-page">
      {/* ── Toolbar ── */}
      <div className="toolbar">
        {onBack&&<button className="btn" onClick={onBack} style={{marginRight:4}}>← Back</button>}
        <span className="logo">⬡ SynthCrypto</span><span className="v3-tag">v3</span>
        <span className="label-sm">TF</span>
        <div className="btn-group">
          {TFS.map(t=><button key={t} className={`btn${tf===t?" active":""}`} onClick={()=>switchTf(t)}>{t}</button>)}
        </div>
        <div className="sep"/>
        <span className="label-sm">Speed</span>
        <div className="btn-group">
          {SPEEDS.map(s=><button key={s.v} className={`btn${speed===s.v?" active":""}`} onClick={()=>setSpd(s.v)}>{s.l}</button>)}
        </div>
        <div className="sep"/>
        <button className={`btn ${paused?"ctrl-red":"ctrl-green"}`} onClick={togglePause}>{paused?"▶ Resume":"❚❚ Pause"}</button>
        <button className={`btn ctrl-orange${simLoading?" loading":""}`} onClick={newSim} disabled={simLoading}>{simLoading?"⏳ Loading...":"⊕ New Sim"}</button>
        <div className="sep"/>
        <button className="btn ctrl-purple" onClick={openMetrics}>📊 Metrics</button>
        <button className="btn" onClick={()=>setShowStress(true)}>⚡ Stress</button>
        <div className="sep"/>
        <button className={`btn${ebbEnabled?" active":""}`} onClick={toggleEbb} onContextMenu={openEbbMetrics} title="Click=toggle | Right-click=metrics" style={ebbEnabled?{}:{background:"#1a3327",color:"#26a69a"}}>EBB Scalper</button>
        {ebbEnabled&&ebbMetrics&&<span className="ebb-badge">
          <span className={ebbMetrics.in_position?(ebbMetrics.pos_side==="long"?"up":"dn"):""} style={ebbMetrics.in_position?{}:{color:"#787b86"}}>{ebbMetrics.in_position?ebbMetrics.pos_side?.toUpperCase():"FLAT"}</span>
          {" "}<span className={(ebbMetrics.net_pnl||0)>=0?"up":"dn"}>{(ebbMetrics.net_pnl||0)>=0?"+":""}${Math.abs(ebbMetrics.net_pnl||0).toFixed(2)}</span> | {ebbMetrics.total_trades||0}T
        </span>}
        <div className="sep"/>
        <button className="btn" onClick={()=>setTab("lab")}>🧪 Strategy Lab</button>
        {onBack&&<><div className="sep"/><button className="btn" onClick={onBack}>📁 Portfolio</button></>}
      </div>

      {/* ── Indicator bar ── */}
      <div className="indbar">
        {IND_BTNS.map((g,gi)=><span key={gi} style={{display:"contents"}}>
          <span className="label-sm">{g.g}</span>
          {g.items.map(i=><button key={i.k} className={`btn${activeInds.has(i.k)?" active":""}`} onClick={()=>setActiveInds(s=>{const n=new Set(s);n.has(i.k)?n.delete(i.k):n.add(i.k);return n;})}>{i.l}</button>)}
          {gi<IND_BTNS.length-1&&<div className="sep"/>}
        </span>)}
        <div className="sep"/>
        <span className="label-sm">Oscillator</span>
        {OSC_BTNS.map(o=><button key={o.k} className={`btn${activeOsc===o.k?" active":""}`} onClick={()=>setActiveOsc(o.k)}>{o.l}</button>)}
      </div>

      {/* ── Phase 2 bar ── */}
      <div className="p2bar">
        <div className="p2-item"><span className={`p2-dot ${p2.garch_enabled!==false?"on":"off"}`}/><span className="p2-lbl">GARCH σ</span><span className="p2-val">{p2.garch_sigma!=null?(p2.garch_sigma).toFixed?.(4)+"%":"—"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.volume_enabled!==false?"on":"off"}`}/><span className="p2-lbl">Volume</span><span className="p2-val">{p2.volume!=null?fmtC(p2.volume):"—"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.slippage_enabled!==false?"on":"off"}`}/><span className="p2-lbl">Slippage</span><span className="p2-val">{p2.slippage_enabled!==false?"ON":"OFF"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.corr_enabled!==false?"on":"off"}`}/><span className="p2-lbl">ETH</span><span className="p2-val">{p2.corr_prices?.ETH!=null?"$"+fmtPrice(p2.corr_prices.ETH):"—"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.cascade_enabled!==false?"on":"off"}`}/><span className="p2-lbl">Cascades</span><span className="p2-val">{p2.cascade_count??p2.n_cascade_events??0}</span></div>
        <div className="p2-item"><span className="p2-lbl">OI</span><span className="p2-val">{p2.oi!=null?fmtC(p2.oi):"—"}</span></div>
        {p2.stress_enabled&&<div className="p2-item"><span style={{color:"#f38720",fontWeight:700}}>⚡ STRESS</span></div>}
      </div>

      {/* ── Price info bar ── */}
      <div className="priceinfo">
        <span className={`pi-price ${price>=(prevPrice||0)?"up":"dn"}`}>{fmtPrice(price)}</span>
        <span className="pi-lbl">Regime</span><span className={`pi-badge badge-${regime}`}>{regime}</span>
        <span className="pi-lbl">SimTime</span><span style={{fontFamily:"monospace"}}>{fmtSim(step)}</span>
        <span className="pi-lbl">Step</span><span style={{fontFamily:"monospace"}}>{step.toLocaleString()}</span>
        <span className="pi-lbl">O</span><span className="pi-ohlc">{ohlc.o}</span>
        <span className="pi-lbl">H</span><span className="pi-ohlc up">{ohlc.h}</span>
        <span className="pi-lbl">L</span><span className="pi-ohlc dn">{ohlc.l}</span>
        <span className="pi-lbl">C</span><span className="pi-ohlc">{ohlc.c}</span>
      </div>

      {/* ── Charts + Trade Panel ── */}
      <div className="charts-wrap">
        <div className="charts-col">
          <div className="main-wrap">
            <ChartErrorBoundary>
              <SimChart candles={candles} timeframe={tf} liveCandle={liveCandle} volumeData={activeInds.has("volume")?volData:[]} indicatorData={indicatorData} activeInds={activeInds} activeOsc={activeOsc}/>
            </ChartErrorBoundary>
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
              {["market","limit","stop","stop_limit"].map(t=><button key={t} className={`btn${otype===t?" active":""}`} onClick={()=>setOtype(t)}>{t==="stop_limit"?"S-Limit":t.charAt(0).toUpperCase()+t.slice(1)}</button>)}
            </div>
          </div>
          <div className="tp-row" style={{gap:4}}>
            <button className={`tp-side-btn long${side==="long"?" active":""}`} onClick={()=>setSide("long")}>▲ Long</button>
            <button className={`tp-side-btn short${side==="short"?" active":""}`} onClick={()=>setSide("short")}>▼ Short</button>
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
          <button className={`tp-place ${side}`} onClick={placeOrder}>{side==="long"?"Buy / Long":"Sell / Short"}</button>
          {toast&&<div className={`tp-toast toast-${toast.type}`}>{toast.msg}</div>}
          <div className="tp-section"><span>Positions</span><span className={totalUpnl>=0?"up":"dn"}>{positions.length?`${totalUpnl>=0?"+":""}$${totalUpnl.toFixed(2)}`:"—"}</span></div>
          <div>{!positions.length?<div className="tp-empty">No open positions</div>:positions.map(p=>(
            <div className="pos-card" key={p.id}>
              <div className="pos-card-row"><span className={p.side==="long"?"up":"dn"}>{p.side.toUpperCase()} {p.leverage}×</span><span className={p.upnl>=0?"up":"dn"}>{p.upnl>=0?"+":""}${p.upnl.toFixed(2)} ({p.upnl_pct>=0?"+":""}{p.upnl_pct.toFixed(1)}%)</span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Entry: {fmtPrice(p.entry_price)}</span><span>Margin: ${p.margin.toFixed(2)}</span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Liq: <span className="dn">{fmtPrice(p.liq_price)}</span></span><span>Size: ${p.size_usd.toFixed(2)}</span></div>
              <button className="pos-card-close" onClick={()=>closePos(p.id)}>× Close</button>
            </div>
          ))}</div>
          <div className="tp-section"><span>Pending Orders</span></div>
          <div>{!orders.length?<div className="tp-empty">No pending orders</div>:orders.map(o=>(
            <div className="ord-card" key={o.id}>
              <div className="pos-card-row"><span className={o.side==="long"?"up":"dn"}>{o.type.toUpperCase()} {o.side.toUpperCase()} {o.leverage}×</span><span style={{color:"#787b86"}}>${o.size_usd.toFixed(2)}</span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Trigger: {fmtPrice(o.trigger_price)}</span>{o.limit_price&&<span>Limit: {fmtPrice(o.limit_price)}</span>}</div>
              <button className="ord-card-cancel" onClick={()=>cancelOrd(o.id)}>× Cancel</button>
            </div>
          ))}</div>
          <div className="tp-footer"><span className="pi-lbl">Realized PnL</span><span className={rpnl>=0?"up":"dn"}>{rpnl>=0?"+":""}${Math.abs(rpnl).toFixed(2)}</span></div>
        </div>
      </div>

      {/* Connection indicator */}
      <div className="conn-indicator live">● live</div>

      {/* ── Overlays ── */}
      {showMetrics&&<MetricsOverlay data={metricsData} onClose={()=>setShowMetrics(false)}/>}
      {showStress&&<StressOverlay socket={socket} onClose={()=>setShowStress(false)}/>}
      {showEbb&&<EBBOverlay metrics={ebbMetrics} onClose={()=>setShowEbb(false)}/>}
    </div>
  );
}
