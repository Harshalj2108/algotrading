import { useEffect, useRef } from "react";
import { createChart, CandlestickSeries, HistogramSeries, LineSeries } from "lightweight-charts";

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

export default function SimChart({ candles, timeframe, liveCandle, volumeData, indicatorData, activeInds }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volSeriesRef = useRef(null);
  const prevTFRef = useRef(timeframe);
  const dataLoadedRef = useRef(false);
  const overlaySeriesRef = useRef({}); // key -> series reference

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

      const ro = new ResizeObserver(() => {
        if (chartRef.current && el.clientWidth > 0 && el.clientHeight > 0) {
          chartRef.current.applyOptions({ width: el.clientWidth, height: el.clientHeight });
        }
      });
      ro.observe(el);

      return () => {
        ro.disconnect();
        chartRef.current = null;
        candleSeriesRef.current = null;
        volSeriesRef.current = null;
        overlaySeriesRef.current = {};
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

    const tfChanged = prevTFRef.current !== timeframe;
    if (tfChanged || !dataLoadedRef.current) {
      try {
        cs.setData(dedup(candles));
        if (volumeData && volumeData.length > 0) {
          vs.setData(dedup(volumeData));
        }
        dataLoadedRef.current = true;
        prevTFRef.current = timeframe;
        chartRef.current?.timeScale().scrollToPosition(0, false);
      } catch (e) {
        console.warn("SimChart: setData error", e);
      }
    } else if (candles.length > 0) {
      try { cs.update(candles[candles.length - 1]); } catch { /* no-op */ }
    }
  }, [candles, timeframe, volumeData]);

  // Handle live candle updates (ticks between candle closes)
  useEffect(() => {
    if (!candleSeriesRef.current || !liveCandle) return;
    try { candleSeriesRef.current.update(liveCandle); } catch { /* no-op */ }
  }, [liveCandle]);

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

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
