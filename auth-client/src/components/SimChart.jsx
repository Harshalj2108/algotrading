import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, LineStyle } from "lightweight-charts";

const CHART_OPTS = {
  layout: { background: { type: "solid", color: "#131722" }, textColor: "#787b86" },
  grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
  rightPriceScale: { borderColor: "#2a2e39" },
  timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: true, rightOffset: 5 },
  handleScroll: true,
  handleScale: true,
};

// Indicator colors
const IND_COLORS = {
  sma20: "#f5c878", sma50: "#e066ff", sma200: "#ff6b6b",
  ema9: "#4fc3f7", ema20: "#ba68c8", ema50: "#ffa726",
  wma20: "#aed581", vwap: "#ffd54f",
  bb_upper: "#7e57c2", bb_middle: "#9575cd", bb_lower: "#7e57c2",
  kc_upper: "#26c6da", kc_middle: "#4dd0e1", kc_lower: "#26c6da",
  ichi_tenkan: "#26a69a", ichi_kijun: "#ef5350", ichi_span_a: "rgba(38,166,154,0.3)", ichi_span_b: "rgba(239,83,80,0.3)", ichi_chikou: "#9e9e9e",
};

// Keys for each indicator button
const IND_KEY_MAP = {
  sma20: ["sma20"], sma50: ["sma50"], sma200: ["sma200"],
  ema9: ["ema9"], ema20: ["ema20"], ema50: ["ema50"],
  wma20: ["wma20"], vwap: ["vwap"],
  bb: ["bb_upper", "bb_middle", "bb_lower"],
  keltner: ["kc_upper", "kc_middle", "kc_lower"],
  ichimoku: ["ichi_tenkan", "ichi_kijun", "ichi_span_a", "ichi_span_b", "ichi_chikou"],
};

function dedup(arr) {
  if (!arr || !arr.length) return [];
  const seen = new Map();
  for (const item of arr) seen.set(item.time, item);
  return Array.from(seen.values()).sort((a, b) => a.time - b.time);
}

function fmtPrice(v) {
  if (v == null || !Number.isFinite(Number(v))) return "-";
  const n = Number(v);
  if (n >= 10000) return n.toFixed(2);
  if (n >= 100) return n.toFixed(3);
  if (n >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

function fallbackTpsl(position) {
  const step = Math.max(position.entry_price * 0.01, 0.000001);
  return position.side === "long"
    ? { tp: position.entry_price + step, sl: position.entry_price - step }
    : { tp: position.entry_price - step, sl: position.entry_price + step };
}

function normalizeTpslDrag(position, kind, price) {
  const tick = Math.max(position.entry_price * 0.0001, 0.000001);
  const rounded = Math.round(Number(price) / tick) * tick;
  if (position.side === "long") {
    return kind === "tp"
      ? Math.max(rounded, position.entry_price + tick)
      : Math.min(rounded, position.entry_price - tick);
  }
  return kind === "tp"
    ? Math.min(rounded, position.entry_price - tick)
    : Math.max(rounded, position.entry_price + tick);
}

function riskReward(position, tp, sl) {
  if (tp == null || sl == null) return "-";
  const reward = Math.abs(tp - position.entry_price);
  const risk = Math.abs(position.entry_price - sl);
  return risk > 0 ? `${(reward / risk).toFixed(2)}R` : "-";
}

export default function SimChart({
  candles,
  timeframe,
  liveCandle,
  volumeData,
  indicatorData,
  activeInds,
  positions = [],
  currentPrice = 0,
  selectedPositionId,
  onSelectPosition,
  onOpenPositionDetails,
  onUpdatePositionTpsl,
  onManagePositionTpsl,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volSeriesRef = useRef(null);
  const prevTFRef = useRef(timeframe);
  const dataLoadedRef = useRef(false);
  const dataRangeRef = useRef(null);
  const overlaySeriesRef = useRef({}); // key -> series reference
  const positionLineRefs = useRef({});
  const [tradeOverlays, setTradeOverlays] = useState([]);
  const [dragState, setDragState] = useState(null);

  const selectedId = selectedPositionId || positions[0]?.id;
  const displayPositions = useMemo(() => positions.map(position => {
    if (position.id !== selectedId) return position;
    const defaults = fallbackTpsl(position);
    return {
      ...position,
      chart_tp_price: position.tp_price ?? defaults.tp,
      chart_sl_price: position.sl_price ?? defaults.sl,
    };
  }), [positions, selectedId]);

  const positionById = useMemo(() => {
    const byId = new Map();
    for (const position of displayPositions) byId.set(position.id, position);
    return byId;
  }, [displayPositions]);

  const priceToY = useCallback((price) => {
    const series = candleSeriesRef.current;
    if (!series || price == null) return null;
    try {
      const y = series.priceToCoordinate(Number(price));
      return Number.isFinite(y) ? y : null;
    } catch {
      return null;
    }
  }, []);

  const yToPrice = useCallback((clientY) => {
    const el = containerRef.current;
    const series = candleSeriesRef.current;
    if (!el || !series) return null;
    const rect = el.getBoundingClientRect();
    const y = clientY - rect.top;
    try {
      const price = series.coordinateToPrice(y);
      return Number.isFinite(price) ? price : null;
    } catch {
      return null;
    }
  }, []);

  const refreshTradeOverlays = useCallback(() => {
    const el = containerRef.current;
    if (!el || !candleSeriesRef.current) return;
    const width = el.clientWidth || 0;
    const height = el.clientHeight || 400;
    const overlays = displayPositions.map(position => {
      const active = position.id === selectedId;
      const drag = dragState?.positionId === position.id ? dragState : null;
      const tp = drag?.kind === "tp" ? drag.price : position.chart_tp_price ?? position.tp_price;
      const sl = drag?.kind === "sl" ? drag.price : position.chart_sl_price ?? position.sl_price;
      const entryY = priceToY(position.entry_price);
      if (entryY == null) return null;
      const tpY = active && tp != null ? priceToY(tp) : null;
      const slY = active && sl != null ? priceToY(sl) : null;
      const priceNow = Number(currentPrice) || position.entry_price;
      const pnl = position.upnl ?? (
        position.side === "long"
          ? (priceNow - position.entry_price) / position.entry_price * position.size_usd
          : (position.entry_price - priceNow) / position.entry_price * position.size_usd
      );
      return { position, active, entryY, tpY, slY, tp, sl, pnl, width, height };
    }).filter(Boolean);
    setTradeOverlays(overlays);
  }, [currentPrice, displayPositions, dragState, priceToY, selectedId]);

  // Create chart once on mount — never re-create
  useEffect(() => {
    const el = containerRef.current;
    if (!el || chartRef.current) return;

    try {
      const chart = createChart(el, {
        ...CHART_OPTS,
        width: el.clientWidth || 800,
        height: el.clientHeight || 400,
      });

      const cs = chart.addSeries(CandlestickSeries, {
        upColor: "#26a69a", downColor: "#ef5350",
        borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
      });

      const vs = chart.addSeries(HistogramSeries, {
        color: "rgba(38,166,154,0.35)", priceFormat: { type: "volume" }, priceScaleId: "vol",
      });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.80, bottom: 0 } });

      chartRef.current = chart;
      candleSeriesRef.current = cs;
      volSeriesRef.current = vs;

      let fitted = false;
      const ro = new ResizeObserver(() => {
        if (chartRef.current && el.clientWidth > 0 && el.clientHeight > 0) {
          chartRef.current.applyOptions({ width: el.clientWidth, height: el.clientHeight });
          // On first real resize, call fitContent so chart zooms to show all candles
          if (!fitted && dataLoadedRef.current) {
            fitted = true;
            requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
          }
        }
      });
      ro.observe(el);

      return () => {
        ro.disconnect();
        chartRef.current = null;
        candleSeriesRef.current = null;
        volSeriesRef.current = null;
        overlaySeriesRef.current = {};
        positionLineRefs.current = {};
        try { chart.remove(); } catch { /* no-op */ }
      };
    } catch (err) {
      console.error("SimChart: failed to create chart", err);
    }
  }, []);

  // Reset loaded flag when candles go empty (new sim or TF switch)
  useEffect(() => {
    if (!candles || candles.length === 0) {
      dataLoadedRef.current = false;
      dataRangeRef.current = null;
      // Clear existing chart data to avoid stale visuals
      if (candleSeriesRef.current) {
        try { candleSeriesRef.current.setData([]); } catch { /* no-op */ }
      }
      if (volSeriesRef.current) {
        try { volSeriesRef.current.setData([]); } catch { /* no-op */ }
      }
      // Also clear indicator overlays
      if (chartRef.current) {
        for (const key of Object.keys(overlaySeriesRef.current)) {
          try { chartRef.current.removeSeries(overlaySeriesRef.current[key]); } catch { /* no-op */ }
        }
        overlaySeriesRef.current = {};
      }
    }
  }, [candles]);

  // Load full candle data when candles array or timeframe changes
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const vs = volSeriesRef.current;
    if (!cs || !candles || candles.length === 0) return;

    const nextData = dedup(candles);
    const firstTime = nextData[0]?.time;
    const lastTime = nextData[nextData.length - 1]?.time;
    const prevRange = dataRangeRef.current;
    const tfChanged = prevTFRef.current !== timeframe;
    const shouldReloadFullData =
      tfChanged ||
      !dataLoadedRef.current ||
      !prevRange ||
      firstTime !== prevRange.firstTime ||
      nextData.length < prevRange.length ||
      nextData.length > prevRange.length + 1;

    if (shouldReloadFullData) {
      try {
        console.log(`[SimChart] setData: ${nextData.length} candles, tf=${timeframe}`);
        cs.setData(nextData);
        vs.setData(volumeData && volumeData.length > 0 ? dedup(volumeData) : []);
        dataLoadedRef.current = true;
        dataRangeRef.current = { firstTime, lastTime, length: nextData.length };
        prevTFRef.current = timeframe;
        // fitContent zooms to show ALL candles; scrollToPosition(0) only pans
        chartRef.current?.timeScale().fitContent();
        // Deferred fallback: if container had 0 dims, fitContent above did nothing.
        // The ResizeObserver handles the first case, but this covers edge cases.
        setTimeout(() => chartRef.current?.timeScale().fitContent(), 100);
        setTimeout(() => chartRef.current?.timeScale().fitContent(), 500);
      } catch (e) {
        console.warn("SimChart: setData error", e);
      }
    } else if (candles.length > 0) {
      try {
        const latest = nextData[nextData.length - 1];
        cs.update(latest);
        if (volumeData && volumeData.length > 0) {
          const volPoints = dedup(volumeData);
          const latestVol = volPoints[volPoints.length - 1];
          if (latestVol) vs.update(latestVol);
        } else {
          vs.setData([]);
        }
        dataRangeRef.current = { firstTime, lastTime, length: nextData.length };
      } catch { /* no-op */ }
    }
  }, [candles, timeframe, volumeData]);

  // Handle live candle updates (ticks between candle closes)
  useEffect(() => {
    if (!candleSeriesRef.current || !liveCandle) return;
    try { candleSeriesRef.current.update(liveCandle); } catch { /* no-op */ }
    requestAnimationFrame(refreshTradeOverlays);
  }, [liveCandle, refreshTradeOverlays]);

  useEffect(() => {
    const cs = candleSeriesRef.current;
    if (!cs) return;
    for (const lines of Object.values(positionLineRefs.current)) {
      for (const line of Object.values(lines)) {
        try { cs.removePriceLine(line); } catch { /* no-op */ }
      }
    }

    const next = {};
    for (const position of displayPositions) {
      const active = position.id === selectedId;
      const sideColor = position.side === "long" ? "#4fc3f7" : "#ffb74d";
      next[position.id] = {};
      try {
        next[position.id].entry = cs.createPriceLine({
          price: position.entry_price,
          color: sideColor,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `${position.side.toUpperCase()} Entry`,
        });
      } catch { /* no-op */ }

      const tp = active ? position.chart_tp_price : position.tp_price;
      const sl = active ? position.chart_sl_price : position.sl_price;
      if (tp != null) {
        try {
          next[position.id].tp = cs.createPriceLine({
            price: tp,
            color: "#26a69a",
            lineWidth: active ? 2 : 1,
            lineStyle: active ? LineStyle.Solid : LineStyle.Dashed,
            axisLabelVisible: true,
            title: "TP",
          });
        } catch { /* no-op */ }
      }
      if (sl != null) {
        try {
          next[position.id].sl = cs.createPriceLine({
            price: sl,
            color: "#ef5350",
            lineWidth: active ? 2 : 1,
            lineStyle: active ? LineStyle.Solid : LineStyle.Dashed,
            axisLabelVisible: true,
            title: "SL",
          });
        } catch { /* no-op */ }
      }
    }
    positionLineRefs.current = next;
    requestAnimationFrame(refreshTradeOverlays);
  }, [displayPositions, refreshTradeOverlays, selectedId]);

  useEffect(() => {
    requestAnimationFrame(refreshTradeOverlays);
  }, [candles, currentPrice, displayPositions, dragState, refreshTradeOverlays]);

  useEffect(() => {
    if (!dragState) return undefined;

    const handleMove = (event) => {
      const position = positionById.get(dragState.positionId);
      const price = yToPrice(event.clientY);
      if (!position || price == null) return;
      setDragState(state => state ? {
        ...state,
        price: normalizeTpslDrag(position, state.kind, price),
      } : state);
    };

    const handleUp = () => {
      const position = positionById.get(dragState.positionId);
      if (position && onUpdatePositionTpsl) {
        const nextTp = dragState.kind === "tp" ? dragState.price : position.tp_price ?? position.chart_tp_price;
        const nextSl = dragState.kind === "sl" ? dragState.price : position.sl_price ?? position.chart_sl_price;
        onUpdatePositionTpsl(position.id, { tp_price: nextTp, sl_price: nextSl });
      }
      setDragState(null);
    };

    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp, { once: true });
    document.body.classList.add("sim-chart-dragging");
    return () => {
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
      document.body.classList.remove("sim-chart-dragging");
    };
  }, [dragState, onUpdatePositionTpsl, positionById, yToPrice]);

  // ── Render indicator overlays ──────────────────────────────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !indicatorData) return;

    // Determine which overlay keys should be visible
    const wantedKeys = new Set();
    if (activeInds) {
      for (const ind of activeInds) {
        const keys = IND_KEY_MAP[ind];
        if (keys) keys.forEach(k => wantedKeys.add(k));
      }
    }

    // Remove series that are no longer wanted
    for (const key of Object.keys(overlaySeriesRef.current)) {
      if (!wantedKeys.has(key)) {
        try { chart.removeSeries(overlaySeriesRef.current[key]); } catch { /* no-op */ }
        delete overlaySeriesRef.current[key];
      }
    }

    // Add or update series that are wanted
    for (const key of wantedKeys) {
      const data = indicatorData[key];
      if (!data || data.length === 0) continue;

      const color = IND_COLORS[key] || "#9e9e9e";

      if (!overlaySeriesRef.current[key]) {
        try {
          const s = chart.addSeries(LineSeries, {
            color,
            lineWidth: 1,
            priceScaleId: "right",
            lastValueVisible: false,
            priceLineVisible: false,
          });
          overlaySeriesRef.current[key] = s;
        } catch { continue; }
      }

      try {
        overlaySeriesRef.current[key].setData(dedup(data));
      } catch { /* no-op */ }
    }
  }, [indicatorData, activeInds]);

  const startDrag = (event, overlay, kind) => {
    event.preventDefault();
    event.stopPropagation();
    onSelectPosition?.(overlay.position.id);
    setDragState({
      positionId: overlay.position.id,
      kind,
      price: kind === "tp" ? overlay.tp : overlay.sl,
    });
  };

  return (
    <div className="sim-chart-shell">
      <div ref={containerRef} className="sim-chart-canvas" />
      <div className="trade-overlay-layer">
        {tradeOverlays.map(overlay => {
          const { position, active, entryY, tpY, slY, tp, sl, pnl, width, height } = overlay;
          const boxTop = Math.max(6, Math.min(entryY - 18, height - 44));
          const profitY = tpY;
          const lossY = slY;
          return (
            <div key={position.id} className={`trade-overlay ${active ? "active" : ""}`}>
              {active && profitY != null && (
                <div
                  className="chart-risk-zone profit"
                  style={{ top: Math.min(entryY, profitY), height: Math.abs(entryY - profitY), width }}
                />
              )}
              {active && lossY != null && (
                <div
                  className="chart-risk-zone loss"
                  style={{ top: Math.min(entryY, lossY), height: Math.abs(entryY - lossY), width }}
                />
              )}
              {active && tpY != null && (
                <div
                  className="chart-tpsl-line tp"
                  style={{ top: tpY }}
                  onMouseDown={event => startDrag(event, overlay, "tp")}
                  title="Drag Take Profit"
                >
                  <span
                    onMouseDown={event => event.stopPropagation()}
                    onClick={event => {
                      event.stopPropagation();
                      onSelectPosition?.(position.id);
                      onManagePositionTpsl?.(position.id);
                    }}
                  >
                    TP {fmtPrice(tp)}
                  </span>
                </div>
              )}
              {active && slY != null && (
                <div
                  className="chart-tpsl-line sl"
                  style={{ top: slY }}
                  onMouseDown={event => startDrag(event, overlay, "sl")}
                  title="Drag Stop Loss"
                >
                  <span
                    onMouseDown={event => event.stopPropagation()}
                    onClick={event => {
                      event.stopPropagation();
                      onSelectPosition?.(position.id);
                      onManagePositionTpsl?.(position.id);
                    }}
                  >
                    SL {fmtPrice(sl)}
                  </span>
                </div>
              )}
              <button
                type="button"
                className={`trade-pnl-box ${pnl >= 0 ? "profit" : "loss"}`}
                style={{ top: boxTop }}
                onClick={event => {
                  event.stopPropagation();
                  onSelectPosition?.(position.id);
                }}
                onDoubleClick={event => {
                  event.stopPropagation();
                  onOpenPositionDetails?.(position.id);
                }}
              >
                <strong>{position.side.toUpperCase()}</strong>
                <span>{pnl >= 0 ? "+" : "-"}${Math.abs(pnl).toFixed(2)}</span>
                <small>Entry {fmtPrice(position.entry_price)} · {riskReward(position, tp, sl)}</small>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
