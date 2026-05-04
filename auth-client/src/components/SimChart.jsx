import { useEffect, useRef, useCallback } from "react";
import { createChart, CandlestickSeries, HistogramSeries } from "lightweight-charts";

const CHART_OPTS = {
  layout: { background: { type: "solid", color: "#131722" }, textColor: "#787b86" },
  grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
  rightPriceScale: { borderColor: "#2a2e39" },
  timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: true, rightOffset: 5 },
  handleScroll: true,
  handleScale: true,
};

export default function SimChart({ candles, timeframe, liveCandle, volumeData }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volSeriesRef = useRef(null);
  const prevTFRef = useRef(timeframe);
  const dataLoadedRef = useRef(false);

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
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderVisible: false,
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350",
      });

      const vs = chart.addSeries(HistogramSeries, {
        color: "rgba(38,166,154,0.35)",
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
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
        try { chart.remove(); } catch (e) {}
      };
    } catch (err) {
      console.error("SimChart: failed to create chart", err);
    }
  }, []);

  // Load full candle data when candles array or timeframe changes
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const vs = volSeriesRef.current;
    if (!cs || !candles || candles.length === 0) return;

    const tfChanged = prevTFRef.current !== timeframe;
    // Only do full setData on TF change or initial load
    if (tfChanged || !dataLoadedRef.current) {
      try {
        // Deduplicate by time (keep last entry per timestamp) — lightweight-charts
        // v5 throws if duplicate or non-ascending timestamps exist
        const seen = new Map();
        for (const c of candles) {
          seen.set(c.time, c);
        }
        const deduped = Array.from(seen.values()).sort((a, b) => a.time - b.time);
        cs.setData(deduped);
        if (volumeData && volumeData.length > 0) {
          const vSeen = new Map();
          for (const v of volumeData) vSeen.set(v.time, v);
          vs.setData(Array.from(vSeen.values()).sort((a, b) => a.time - b.time));
        }
        dataLoadedRef.current = true;
        prevTFRef.current = timeframe;
        chartRef.current?.timeScale().scrollToPosition(0, false);
      } catch (e) {
        console.warn("SimChart: setData error", e);
      }
    } else if (candles.length > 0) {
      // Incremental: just update the last candle
      try {
        cs.update(candles[candles.length - 1]);
      } catch (e) {}
    }
  }, [candles, timeframe, volumeData]);


  // Handle live candle updates (ticks between candle closes)
  useEffect(() => {
    if (!candleSeriesRef.current || !liveCandle) return;
    try {
      candleSeriesRef.current.update(liveCandle);
    } catch (e) {
      // Silently ignore — stale time errors are harmless
    }
  }, [liveCandle]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
