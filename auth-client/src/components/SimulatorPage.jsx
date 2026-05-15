import { useState, useEffect, useRef, useCallback, Component } from "react";
import { io } from "socket.io-client";
import SimChart from "./SimChart";
import StrategyEditor from "./StrategyEditor";
import { MetricsOverlay, StressOverlay, EBBOverlay } from "./SimOverlays";
import "./SimulatorPage.css";
import StarBorder from "./StarBorder";
import { AUTH_SERVER } from '../config';


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

function toOptionalPrice(v){if(v===""||v==null)return null;const n=Number(v);return Number.isFinite(n)?n:NaN;}
function defaultTpsl(pos){const step=Math.max(pos.entry_price*0.01,0.000001);return pos.side==="long"?{tp:pos.entry_price+step,sl:pos.entry_price-step}:{tp:pos.entry_price-step,sl:pos.entry_price+step};}
function validateTpslValues(pos,tp,sl){if(!pos)return"Select a position first";if(tp==null&&sl==null)return"Enter at least one TP or SL price";if(Number.isNaN(tp)||Number.isNaN(sl))return"TP/SL prices must be valid numbers";if(tp!=null&&tp<=0)return"Take Profit must be greater than zero";if(sl!=null&&sl<=0)return"Stop Loss must be greater than zero";if(pos.side==="long"){if(tp!=null&&tp<=pos.entry_price)return"Long TP must be above entry";if(sl!=null&&sl>=pos.entry_price)return"Long SL must be below entry";}else{if(tp!=null&&tp>=pos.entry_price)return"Short TP must be below entry";if(sl!=null&&sl<=pos.entry_price)return"Short SL must be above entry";}return null;}
function riskReward(pos,tp=pos?.tp_price,sl=pos?.sl_price){if(!pos||tp==null||sl==null)return"â€”";const reward=Math.abs(tp-pos.entry_price);const risk=Math.abs(pos.entry_price-sl);return risk>0?(reward/risk).toFixed(2)+"R":"â€”";}

function postSimulatorTradeEvent(trade){
  if(!trade)return;
  fetch(`${AUTH_SERVER}/api/portfolio/trade-feed`,{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    credentials:"include",
    body:JSON.stringify(trade),
  }).catch(err=>console.warn("Trade feed sync failed:",err.message));
}

function simulatorOpenTradeEvent(position,extra={}){
  if(!position)return null;
  const side=position.side==="short"?"sell":"buy";
  return {
    event_key:extra.event_key||`simulator:${position.id}:open`,
    trade_id:position.id,
    asset_symbol:"SIM",
    asset_type:"simulator",
    buy_or_sell:side,
    side,
    quantity:position.qty||0,
    entry_price:position.entry_price||0,
    execution_price:position.entry_price||0,
    trade_value:position.size_usd||0,
    profit_loss:0,
    timestamp:new Date().toISOString(),
    source_market:"simulator",
  };
}

function simulatorCloseTradeEvent(result,extra={}){
  if(!result)return null;
  const tradeId=result.position_id||result.trade_id||result.id;
  if(!tradeId)return null;
  const side=result.side==="short"?"buy":"sell";
  return {
    event_key:extra.event_key||`simulator:${tradeId}:close:${result.reason||"manual"}`,
    trade_id:tradeId,
    asset_symbol:result.symbol||"SIM",
    asset_type:"simulator",
    buy_or_sell:side,
    side,
    quantity:result.qty||result.quantity||0,
    entry_price:result.exit_price??result.entry_price??0,
    exit_price:result.exit_price??null,
    execution_price:result.exit_price??result.entry_price??0,
    trade_value:result.size_usd||0,
    profit_loss:result.pnl||0,
    timestamp:new Date().toISOString(),
    source_market:"simulator",
  };
}

export default function SimulatorPage({ onBack, focusPositionId = null }) {
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
  const priceRef = useRef(0);
  const [regime, setRegime] = useState("bull");
  const [step, setStep] = useState(0);
  const ohlc = {o:"—",h:"—",l:"—",c:"—"};
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
  const [selectedPosId, setSelectedPosId] = useState(null);
  const [tpslPanelOpen, setTpslPanelOpen] = useState(false);
  const [tpslDraft, setTpslDraft] = useState({ tp: "", sl: "" });
  const [detailsPosId, setDetailsPosId] = useState(null);
  const [tpslManagePosId, setTpslManagePosId] = useState(null);
  const [tpslEditPosId, setTpslEditPosId] = useState(null);
  const [tpslDeletePosId, setTpslDeletePosId] = useState(null);
  // Overlays
  const [showMetrics, setShowMetrics] = useState(false);
  const [metricsData, setMetricsData] = useState(null);
  const [showStress, setShowStress] = useState(false);
  const [showEbb, setShowEbb] = useState(false);
  const [ebbEnabled, setEbbEnabled] = useState(false);
  const [ebbMetrics, setEbbMetrics] = useState(null);
  
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

  const selectedPosition = positions.find(p => p.id === selectedPosId)
    || positions.find(p => p.id === focusPositionId)
    || positions[0]
    || null;
  const detailsPosition = positions.find(p => p.id === detailsPosId) || null;
  const tpslManagePosition = positions.find(p => p.id === tpslManagePosId) || null;
  const tpslEditPosition = positions.find(p => p.id === tpslEditPosId) || null;
  const tpslDeletePosition = positions.find(p => p.id === tpslDeletePosId) || null;

  const openTpslPanel = useCallback((pos = selectedPosition) => {
    if (!pos) {
      showToastMsg("Open a position before adding TP/SL", "err");
      return;
    }
    const defaults = defaultTpsl(pos);
    setSelectedPosId(pos.id);
    setTpslDraft({
      tp: String(pos.tp_price ?? defaults.tp.toFixed(6)),
      sl: String(pos.sl_price ?? defaults.sl.toFixed(6)),
    });
    setTpslPanelOpen(true);
  }, [selectedPosition, showToastMsg]);

  const applyTpsl = useCallback((pos = selectedPosition, nextDraft = tpslDraft) => {
    if (!pos) {
      showToastMsg("Select a position first", "err");
      return false;
    }
    const tp = toOptionalPrice(nextDraft.tp);
    const sl = toOptionalPrice(nextDraft.sl);
    const err = validateTpslValues(pos, tp, sl);
    if (err) {
      showToastMsg(err, "err");
      return false;
    }
    socket.emit("update_position_tpsl", { id: pos.id, tp_price: tp, sl_price: sl });
    return true;
  }, [selectedPosition, showToastMsg, tpslDraft]);

  const openTpslManage = useCallback((posId) => {
    const pos = positions.find(p => p.id === posId);
    if (!pos) return;
    setSelectedPosId(pos.id);
    setTpslManagePosId(pos.id);
  }, [positions]);

  const openTpslEditModal = useCallback((pos) => {
    if (!pos) return;
    const defaults = defaultTpsl(pos);
    setSelectedPosId(pos.id);
    setTpslDraft({
      tp: String(pos.tp_price ?? defaults.tp.toFixed(6)),
      sl: String(pos.sl_price ?? defaults.sl.toFixed(6)),
    });
    setTpslManagePosId(null);
    setTpslEditPosId(pos.id);
  }, []);

  const saveTpslEditModal = useCallback(() => {
    if (!tpslEditPosition) return;
    if (applyTpsl(tpslEditPosition, tpslDraft)) {
      setTpslEditPosId(null);
    }
  }, [applyTpsl, tpslDraft, tpslEditPosition]);

  const removeTpsl = useCallback((pos = tpslDeletePosition) => {
    if (!pos) return;
    setPositions(prev => prev.map(p => p.id === pos.id ? { ...p, tp_price: null, sl_price: null } : p));
    socket.emit("remove_position_tpsl", { id: pos.id });
    setTpslPanelOpen(false);
    setTpslManagePosId(null);
    setTpslEditPosId(null);
    setTpslDeletePosId(null);
  }, [tpslDeletePosition]);

  const updatePositionTpsl = useCallback((posId, next) => {
    const pos = positions.find(p => p.id === posId);
    if (!pos) return false;
    return applyTpsl(pos, {
      tp: next.tp_price == null ? "" : String(next.tp_price),
      sl: next.sl_price == null ? "" : String(next.sl_price),
    });
  }, [applyTpsl, positions]);

  // Keep priceRef in sync so socket handlers can read current price without re-subscribing
  useEffect(() => { priceRef.current = price; }, [price]);

  // Socket setup — runs ONCE on mount, never tears down mid-session
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
      if(d.price){setPrevPrice(priceRef.current);setPrice(d.price);}
      if(d.regime) setRegime(d.regime);
      if(d.step) setStep(d.step);
      if(d.p2) setP2(d.p2);
      if(d.ebb_strategy?.metrics) setEbbMetrics(d.ebb_strategy.metrics);
    });
    socket.on("tick", d => {
      setStep(d.step);
      setPrevPrice(priceRef.current);
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
      if(d.events?.tpsl_closed?.length) d.events.tpsl_closed.forEach(ev=>{
        const label=ev.reason==="take_profit"?"Take Profit":"Stop Loss";
        postSimulatorTradeEvent(simulatorCloseTradeEvent(ev,{event_key:`simulator:${ev.position_id||ev.trade_id||ev.id}:close:${ev.reason||"tpsl"}`}));
        showToastMsg(`${label} closed ${ev.side?.toUpperCase?.()||""} ${ev.pnl>=0?"+":""}S${Math.abs(ev.pnl||0).toFixed(2)}`,ev.pnl>=0?"ok":"err");
      });
      if(d.events?.filled?.length){
        d.events.filled.forEach(ev=>postSimulatorTradeEvent(simulatorOpenTradeEvent(ev.position,{event_key:`simulator:${ev.position?.id||ev.order_id}:open`})));
        showToastMsg("✓ Order filled","ok");
      }
    });
    socket.on("candle_close", d => {
      if(d.tf===tfRef.current && d.candles) setCandles(prev=>[...prev,...d.candles].slice(-2000));
    });
    socket.on("new_sim", d => {
      if(d.price){setPrice(d.price);setPrevPrice(d.price);}
      setStep(0);if(d.regime)setRegime(d.regime);if(d.p2)setP2(d.p2);
      setBalance(d.balance||10000);setRpnl(0);setPositions([]);setOrders([]);
      setSelectedPosId(null);setTpslPanelOpen(false);setDetailsPosId(null);setTpslManagePosId(null);setTpslEditPosId(null);setTpslDeletePosId(null);
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
      if(d.status==="filled"){
        postSimulatorTradeEvent(simulatorOpenTradeEvent(d.position));
        showToastMsg("✓ Filled"+(d.slippage>0?` (slip: S${d.slippage.toFixed(4)})`:""),"ok");
      }
      else if(d.status==="pending")showToastMsg("⏱ Order placed","ok");
      else if(d.status==="closed"){
        postSimulatorTradeEvent(simulatorCloseTradeEvent(d));
        showToastMsg(`✓ Closed ${d.pnl>=0?"+":""}S${Math.abs(d.pnl).toFixed(2)}`,d.pnl>=0?"ok":"err");
      }
      else if(d.status==="tpsl_updated"){
        if(d.position)setPositions(prev=>prev.map(p=>p.id===d.position.id?{...p,...d.position}:p));
        setTpslPanelOpen(false);
        showToastMsg("TP/SL updated","ok");
      }
      else if(d.status==="tpsl_removed"){
        if(d.position)setPositions(prev=>prev.map(p=>p.id===d.position.id?{...p,...d.position}:p));
        setTpslPanelOpen(false);
        setTpslManagePosId(null);
        setTpslEditPosId(null);
        setTpslDeletePosId(null);
        showToastMsg("TP/SL removed","ok");
      }
      else if(d.status==="cancelled")showToastMsg("Order cancelled","ok");
      else if(d.status==="error")showToastMsg("⚠ "+d.msg,"err");
    });
    socket.on("ebb_strategy_toggled",d=>{setEbbEnabled(d.enabled);if(d.metrics)setEbbMetrics(d.metrics);});
    socket.on("ebb_strategy_update",d=>{if(d.metrics)setEbbMetrics(d.metrics);});
    socket.on("ebb_strategy_metrics",d=>setEbbMetrics(d));
    return()=>{socket.off();socket.disconnect();};
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Actions
  const switchTf=t=>{
    if(t===tfRef.current) return;
    setTf(t);tfRef.current=t;
    setCandles([]);setVolData([]);setLiveCandle(null);setIndicatorData({});
    socket.emit("switch_tf",{tf:t});
  };
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
        {onBack&&<StarBorder as="button" className="btn" onClick={onBack} style={{marginRight:4}}>← Back</StarBorder>}
        <span className="logo">⬡ SynthCrypto</span>
        <div className="sep"/>
        <StarBorder as="button" className="btn active" onClick={()=>setTab("chart")}>Dashboard</StarBorder>
        <StarBorder as="button" className="btn ctrl-purple" onClick={()=>setTab("lab")}>Strategy Lab</StarBorder>
        {onBack&&<><div className="sep"/><StarBorder as="button" className="btn" onClick={onBack}>📁 Portfolio</StarBorder></>}
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
        {onBack&&<StarBorder as="button" className="btn" onClick={onBack} style={{marginRight:4}}>← Back</StarBorder>}
        <span className="logo">⬡ SynthCrypto</span>
        <span className="label-sm">TF</span>
        <div className="btn-group">
          {TFS.map(t=><StarBorder as="button" key={t} className={`btn${tf===t?" active":""}`} onClick={()=>switchTf(t)}>{t}</StarBorder>)}
        </div>
        <div className="sep"/>
        <span className="label-sm">Speed</span>
        <div className="btn-group">
          {SPEEDS.map(s=><StarBorder as="button" key={s.v} className={`btn${speed===s.v?" active":""}`} onClick={()=>setSpd(s.v)}>{s.l}</StarBorder>)}
        </div>
        <div className="sep"/>
        <StarBorder as="button" className={`btn ${paused?"ctrl-red":"ctrl-green"}`} onClick={togglePause}>{paused?"▶ Resume":"❚❚ Pause"}</StarBorder>
        <StarBorder as="button" className={`btn ctrl-orange${simLoading?" loading":""}`} onClick={newSim} disabled={simLoading}>{simLoading?"⏳ Loading...":"⊕ New Sim"}</StarBorder>
        <div className="sep"/>
        <StarBorder as="button" className="btn ctrl-purple" onClick={openMetrics}>📊 Metrics</StarBorder>
        <StarBorder as="button" className="btn" onClick={()=>setShowStress(true)}>⚡ Stress</StarBorder>
        <div className="sep"/>
        <StarBorder as="button" className={`btn${ebbEnabled?" active":""}`} onClick={toggleEbb} onContextMenu={openEbbMetrics} title="Click=toggle | Right-click=metrics" style={ebbEnabled?{}:{background:"#1a3327",color:"#26a69a"}}>EBB Scalper</StarBorder>
        {ebbEnabled&&ebbMetrics&&<span className="ebb-badge">
          <span className={ebbMetrics.in_position?(ebbMetrics.pos_side==="long"?"up":"dn"):""} style={ebbMetrics.in_position?{}:{color:"#787b86"}}>{ebbMetrics.in_position?ebbMetrics.pos_side?.toUpperCase():"FLAT"}</span>
          {" "}<span className={(ebbMetrics.net_pnl||0)>=0?"up":"dn"}>{(ebbMetrics.net_pnl||0)>=0?"+":""}S{Math.abs(ebbMetrics.net_pnl||0).toFixed(2)}</span> | {ebbMetrics.total_trades||0}T
        </span>}
        <div className="sep"/>
        <StarBorder as="button" className="btn" onClick={()=>setTab("lab")}>🧪 Strategy Lab</StarBorder>
        {onBack&&<><div className="sep"/><StarBorder as="button" className="btn" onClick={onBack}>📁 Portfolio</StarBorder></>}
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

      {/* ── Phase 2 bar ── */}
      <div className="p2bar">
        <div className="p2-item"><span className={`p2-dot ${p2.garch_enabled!==false?"on":"off"}`}/><span className="p2-lbl">GARCH σ</span><span className="p2-val">{p2.garch_sigma!=null?(p2.garch_sigma).toFixed?.(4)+"%":"—"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.volume_enabled!==false?"on":"off"}`}/><span className="p2-lbl">Volume</span><span className="p2-val">{p2.volume!=null?fmtC(p2.volume):"—"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.slippage_enabled!==false?"on":"off"}`}/><span className="p2-lbl">Slippage</span><span className="p2-val">{p2.slippage_enabled!==false?"ON":"OFF"}</span></div>
        <div className="p2-item"><span className={`p2-dot ${p2.corr_enabled!==false?"on":"off"}`}/><span className="p2-lbl">ETH</span><span className="p2-val">{p2.corr_prices?.ETH!=null?"S"+fmtPrice(p2.corr_prices.ETH):"—"}</span></div>
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
              <SimChart
                candles={candles}
                timeframe={tf}
                liveCandle={liveCandle}
                volumeData={activeInds.has("volume")?volData:[]}
                indicatorData={indicatorData}
                activeInds={activeInds}
                activeOsc={activeOsc}
                positions={positions}
                currentPrice={price}
                selectedPositionId={selectedPosition?.id}
                onSelectPosition={setSelectedPosId}
                onOpenPositionDetails={setDetailsPosId}
                onUpdatePositionTpsl={updatePositionTpsl}
                onManagePositionTpsl={openTpslManage}
              />
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
            <span className={balance>=10000?"up":"dn"}>S{balance.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
          </div>
          <div className="tp-row" style={{flexWrap:"wrap",gap:3}}>
            <span className="tp-lbl">Type</span>
            <div className="btn-group">
              {["market","limit","stop","stop_limit"].map(t=><StarBorder as="button" key={t} className={`btn${otype===t?" active":""}`} onClick={()=>setOtype(t)}>{t==="stop_limit"?"S-Limit":t.charAt(0).toUpperCase()+t.slice(1)}</StarBorder>)}
            </div>
          </div>
          <div className="tp-row tpsl-action-row">
            <StarBorder as="button" className="btn tpsl-add-btn" onClick={()=>openTpslPanel()} disabled={!positions.length}>Add TP/SL</StarBorder>
            <span className="tpsl-selected">{selectedPosition?`${selectedPosition.side.toUpperCase()} @ ${fmtPrice(selectedPosition.entry_price)}`:"No position"}</span>
          </div>
          {tpslPanelOpen&&selectedPosition&&<div className="tpsl-panel">
            <div className="tpsl-panel-head">
              <span>TP/SL</span>
              <select value={selectedPosition.id} onChange={e=>{
                const pos=positions.find(p=>p.id===e.target.value);
                if(pos)openTpslPanel(pos);
              }}>
                {positions.map(p=><option key={p.id} value={p.id}>{p.side.toUpperCase()} {fmtPrice(p.entry_price)}</option>)}
              </select>
            </div>
            <label><span>Take Profit</span><input type="number" value={tpslDraft.tp} onChange={e=>setTpslDraft(d=>({...d,tp:e.target.value}))}/></label>
            <label><span>Stop Loss</span><input type="number" value={tpslDraft.sl} onChange={e=>setTpslDraft(d=>({...d,sl:e.target.value}))}/></label>
            <div className="tpsl-summary">
              <span>Entry {fmtPrice(selectedPosition.entry_price)}</span>
              <span>R/R {riskReward(selectedPosition,toOptionalPrice(tpslDraft.tp),toOptionalPrice(tpslDraft.sl))}</span>
            </div>
            <div className="tpsl-actions">
              <StarBorder as="button" className="btn ctrl-green" onClick={()=>applyTpsl()}>Save Changes</StarBorder>
              <StarBorder as="button" className="btn ctrl-red" onClick={()=>setTpslDeletePosId(selectedPosition.id)}>Remove TP/SL</StarBorder>
              <StarBorder as="button" className="btn" onClick={()=>setTpslPanelOpen(false)}>Cancel</StarBorder>
            </div>
          </div>}
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
            <span className="tp-lbl">Size S</span>
            <input type="number" min={1} value={sizeUsd} onChange={e=>setSizeUsd(+e.target.value)} style={{flex:1}}/>
          </div>
          {otype!=="market"&&<div className="tp-row">
            <span className="tp-lbl">{otype==="limit"?"Price S":"Stop S"}</span>
            <input type="number" value={trigPrice} onChange={e=>setTrigPrice(e.target.value)} style={{flex:1}} placeholder="Trigger price"/>
          </div>}
          {otype==="stop_limit"&&<div className="tp-row">
            <span className="tp-lbl">Limit S</span>
            <input type="number" value={limitPrice} onChange={e=>setLimitPrice(e.target.value)} style={{flex:1}} placeholder="Limit price"/>
          </div>}
          <div className="tp-info">
            <div><span className="tp-lbl">Margin</span><span>S{margin}</span></div>
            <div><span className="tp-lbl">Liq</span><span>{liqP?fmtPrice(liqP):"—"}</span></div>
            <div><span className="tp-lbl">Fee</span><span>S{fee}</span></div>
          </div>
          <StarBorder as="button" className={`tp-place ${side}`} onClick={placeOrder}>{side==="long"?"Buy / Long":"Sell / Short"}</StarBorder>
          {toast&&<div className={`tp-toast toast-${toast.type}`}>{toast.msg}</div>}
          <div className="tp-section"><span>Positions</span><span className={totalUpnl>=0?"up":"dn"}>{positions.length?`${totalUpnl>=0?"+":""}S${totalUpnl.toFixed(2)}`:"—"}</span></div>
          <div>{!positions.length?<div className="tp-empty">No open positions</div>:positions.map(p=>(
            <div className={`pos-card${selectedPosition?.id===p.id?" selected":""}`} key={p.id} onClick={()=>setSelectedPosId(p.id)}>
              <div className="pos-card-row"><span className={p.side==="long"?"up":"dn"}>{p.side.toUpperCase()} {p.leverage}×</span><span className={p.upnl>=0?"up":"dn"}>{p.upnl>=0?"+":""}S{p.upnl.toFixed(2)} ({p.upnl_pct>=0?"+":""}{p.upnl_pct.toFixed(1)}%)</span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Entry: {fmtPrice(p.entry_price)}</span><span>Margin: S{p.margin.toFixed(2)}</span></div>
              <div className="pos-card-row tpsl-card-row"><span>TP: <span className="up">{p.tp_price?fmtPrice(p.tp_price):"—"}</span></span><span>SL: <span className="dn">{p.sl_price?fmtPrice(p.sl_price):"—"}</span></span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Liq: <span className="dn">{fmtPrice(p.liq_price)}</span></span><span>Size: S{p.size_usd.toFixed(2)}</span></div>
              <div className="pos-card-actions">
                <StarBorder as="button" className="pos-card-tpsl" onClick={e=>{e.stopPropagation();openTpslPanel(p);}}>TP/SL</StarBorder>
                <StarBorder as="button" className="pos-card-tpsl" onClick={e=>{e.stopPropagation();setDetailsPosId(p.id);}}>Details</StarBorder>
              </div>
              <StarBorder as="button" className="pos-card-close" onClick={e=>{e.stopPropagation();closePos(p.id);}}>× Close</StarBorder>
            </div>
          ))}</div>
          <div className="tp-section"><span>Pending Orders</span></div>
          <div>{!orders.length?<div className="tp-empty">No pending orders</div>:orders.map(o=>(
            <div className="ord-card" key={o.id}>
              <div className="pos-card-row"><span className={o.side==="long"?"up":"dn"}>{o.type.toUpperCase()} {o.side.toUpperCase()} {o.leverage}×</span><span style={{color:"#787b86"}}>S{o.size_usd.toFixed(2)}</span></div>
              <div className="pos-card-row" style={{color:"#787b86"}}><span>Trigger: {fmtPrice(o.trigger_price)}</span>{o.limit_price&&<span>Limit: {fmtPrice(o.limit_price)}</span>}</div>
              <StarBorder as="button" className="ord-card-cancel" onClick={()=>cancelOrd(o.id)}>× Cancel</StarBorder>
            </div>
          ))}</div>
          <div className="tp-footer"><span className="pi-lbl">Realized PnL</span><span className={rpnl>=0?"up":"dn"}>{rpnl>=0?"+":""}S{Math.abs(rpnl).toFixed(2)}</span></div>
        </div>
      </div>

      {/* Connection indicator */}
      <div className="conn-indicator live">● live</div>

      {/* ── Overlays ── */}
      {tpslManagePosition&&<div className="overlay-backdrop" onClick={()=>setTpslManagePosId(null)}>
        <div className="overlay-panel tpsl-modal" onClick={e=>e.stopPropagation()}>
          <h3>Manage TP/SL</h3>
          <div className="metric-row"><span>Position</span><span>{tpslManagePosition.side.toUpperCase()} @ {fmtPrice(tpslManagePosition.entry_price)}</span></div>
          <div className="metric-row"><span>Take Profit</span><span className="up">{tpslManagePosition.tp_price?fmtPrice(tpslManagePosition.tp_price):"—"}</span></div>
          <div className="metric-row"><span>Stop Loss</span><span className="dn">{tpslManagePosition.sl_price?fmtPrice(tpslManagePosition.sl_price):"—"}</span></div>
          <div className="detail-actions tpsl-modal-actions">
            <StarBorder as="button" className="btn ctrl-green" onClick={()=>openTpslEditModal(tpslManagePosition)}>Edit</StarBorder>
            <StarBorder as="button" className="btn ctrl-red" onClick={()=>{setTpslDeletePosId(tpslManagePosition.id);setTpslManagePosId(null);}}>Remove TP/SL</StarBorder>
            <StarBorder as="button" className="overlay-close" onClick={()=>setTpslManagePosId(null)}>Cancel</StarBorder>
          </div>
        </div>
      </div>}

      {tpslEditPosition&&<div className="overlay-backdrop" onClick={()=>setTpslEditPosId(null)}>
        <div className="overlay-panel tpsl-modal" onClick={e=>e.stopPropagation()}>
          <h3>Edit TP/SL</h3>
          <div className="metric-row"><span>Entry</span><span>{fmtPrice(tpslEditPosition.entry_price)}</span></div>
          <label className="tpsl-modal-field"><span>Take Profit</span><input type="number" value={tpslDraft.tp} onChange={e=>setTpslDraft(d=>({...d,tp:e.target.value}))}/></label>
          <label className="tpsl-modal-field"><span>Stop Loss</span><input type="number" value={tpslDraft.sl} onChange={e=>setTpslDraft(d=>({...d,sl:e.target.value}))}/></label>
          <div className="tpsl-summary">
            <span>R/R {riskReward(tpslEditPosition,toOptionalPrice(tpslDraft.tp),toOptionalPrice(tpslDraft.sl))}</span>
          </div>
          <div className="detail-actions tpsl-modal-actions">
            <StarBorder as="button" className="btn ctrl-green" onClick={saveTpslEditModal}>Save Changes</StarBorder>
            <StarBorder as="button" className="btn ctrl-red" onClick={()=>setTpslDeletePosId(tpslEditPosition.id)}>Remove TP/SL</StarBorder>
            <StarBorder as="button" className="overlay-close" onClick={()=>setTpslEditPosId(null)}>Cancel</StarBorder>
          </div>
        </div>
      </div>}

      {tpslDeletePosition&&<div className="overlay-backdrop" onClick={()=>setTpslDeletePosId(null)}>
        <div className="overlay-panel tpsl-modal" onClick={e=>e.stopPropagation()}>
          <h3>Remove TP/SL</h3>
          <p className="tpsl-confirm-copy">Remove Take Profit and Stop Loss from this position?</p>
          <div className="detail-actions">
            <StarBorder as="button" className="btn ctrl-red" onClick={()=>removeTpsl(tpslDeletePosition)}>Remove TP/SL</StarBorder>
            <StarBorder as="button" className="overlay-close" onClick={()=>setTpslDeletePosId(null)}>Cancel</StarBorder>
          </div>
        </div>
      </div>}

      {detailsPosition&&<div className="overlay-backdrop" onClick={()=>setDetailsPosId(null)}>
        <div className="overlay-panel position-detail-panel" onClick={e=>e.stopPropagation()}>
          <h3>{detailsPosition.side.toUpperCase()} Position</h3>
          <div className="metric-row"><span>Entry price</span><span>{fmtPrice(detailsPosition.entry_price)}</span></div>
          <div className="metric-row"><span>Current price</span><span>{fmtPrice(price)}</span></div>
          <div className="metric-row"><span>Take Profit</span><span className="up">{detailsPosition.tp_price?fmtPrice(detailsPosition.tp_price):"—"}</span></div>
          <div className="metric-row"><span>Stop Loss</span><span className="dn">{detailsPosition.sl_price?fmtPrice(detailsPosition.sl_price):"—"}</span></div>
          <div className="metric-row"><span>Risk / Reward</span><span>{riskReward(detailsPosition)}</span></div>
          <div className="metric-row"><span>PnL</span><span className={detailsPosition.upnl>=0?"up":"dn"}>{detailsPosition.upnl>=0?"+":""}S{Math.abs(detailsPosition.upnl).toFixed(2)} ({detailsPosition.upnl_pct>=0?"+":""}{detailsPosition.upnl_pct.toFixed(1)}%)</span></div>
          <div className="metric-row"><span>Trade size</span><span>S{detailsPosition.size_usd.toFixed(2)} / {detailsPosition.qty} qty</span></div>
          <div className="metric-row"><span>Liquidation</span><span className="dn">{fmtPrice(detailsPosition.liq_price)}</span></div>
          <div className="detail-actions">
            <StarBorder as="button" className="btn ctrl-green" onClick={()=>openTpslEditModal(detailsPosition)}>Edit TP/SL</StarBorder>
            <StarBorder as="button" className="overlay-close" onClick={()=>setDetailsPosId(null)}>Close</StarBorder>
          </div>
        </div>
      </div>}
      {showMetrics&&<MetricsOverlay data={metricsData} onClose={()=>setShowMetrics(false)}/>}
      {showStress&&<StressOverlay socket={socket} onClose={()=>setShowStress(false)}/>}
      {showEbb&&<EBBOverlay metrics={ebbMetrics} onClose={()=>setShowEbb(false)}/>}
    </div>
  );
}
