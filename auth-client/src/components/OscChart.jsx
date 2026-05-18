import { useEffect, useRef } from "react";
import { createChart, LineSeries, HistogramSeries } from "lightweight-charts";

const CHART_OPTS = {
  layout: { background: { type: "solid", color: "#131722" }, textColor: "#787b86" },
  grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
  rightPriceScale: { borderColor: "#2a2e39" },
  timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: true, rightOffset: 5 },
  handleScroll: true,
  handleScale: true,
};

function dedup(arr) {
  if (!arr || !arr.length) return [];
  const seen = new Map();
  for (const item of arr) seen.set(item.time, item);
  return Array.from(seen.values()).sort((a, b) => a.time - b.time);
}

export default function OscChart({ indicatorData, activeOsc }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      ...CHART_OPTS,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });
    chartRef.current = chart;

    const handleResize = () => {
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !indicatorData || !activeOsc) return;

    // Clear previous series
    Object.values(seriesRef.current).forEach(s => {
        try { chart.removeSeries(s); } catch { /* ignore */ }
    });
    seriesRef.current = {};

    let seriesDefs;
    if (activeOsc === "macd") {
      seriesDefs = [
        { key: "macd_hist", type: "histogram" },
        { key: "macd_line", type: "line", color: "#2962FF" },
        { key: "macd_signal", type: "line", color: "#FF6D00" },
      ];
    } else {
      // Default single line oscillator (e.g. rsi14, stoch, cci20)
      seriesDefs = [{ key: activeOsc, type: "line", color: "#ba68c8" }];
    }

    seriesDefs.forEach(def => {
      const data = indicatorData[def.key];
      if (!data || !data.length) return;

      let s;
      if (def.type === "histogram") {
        s = chart.addSeries(HistogramSeries, {
          priceScaleId: "",
        });
        // lightweight-charts histogram series coloring is usually via data points, 
        // but we can map colors to the data
        const coloredData = data.map(d => ({
            ...d, 
            color: d.value >= 0 ? 'rgba(38,166,154,0.8)' : 'rgba(239,83,80,0.8)'
        }));
        s.setData(dedup(coloredData));
      } else {
        s = chart.addSeries(LineSeries, {
          color: def.color,
          lineWidth: 2,
          crosshairMarkerVisible: false,
        });
        s.setData(dedup(data));
      }
      seriesRef.current[def.key] = s;
    });

    try {
        chart.timeScale().fitContent();
    } catch {
        // ignore error if content cannot fit
    }

  }, [indicatorData, activeOsc]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
