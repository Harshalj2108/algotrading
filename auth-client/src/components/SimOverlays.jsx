import { useState } from "react";
import StarBorder from "./StarBorder";

/* ── Risk Metrics Overlay ── */
export function MetricsOverlay({ data, onClose }) {
  if (!data) return null;
  const rows = data && Object.keys(data).length > 0 ? [
    ["Total Return", (data.total_return_pct >= 0 ? "+" : "") + data.total_return_pct + "%"],
    ["Sharpe Ratio", data.sharpe_ratio],
    ["Max Drawdown", data.max_drawdown_pct + "%"],
    ["VaR 95%", data.var_95_pct + "%"],
    ["Win Rate", data.n_trades > 0 ? data.win_rate_pct + "%" : "N/A"],
    ["Trade Expectancy", data.n_trades > 0 ? data.trade_expectancy : "N/A"],
    ["Total Trades", data.n_trades],
  ] : null;
  return (
    <div className="overlay-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="overlay-panel">
        <h3>📊 Risk Metrics</h3>
        {rows ? rows.map(([l, v], i) => (
          <div className="metric-row" key={i}><span>{l}</span><span>{v}</span></div>
        )) : <div className="tp-empty">Not enough data yet</div>}
        <StarBorder as="button" className="overlay-close" onClick={onClose} color="#ef5350">Close</StarBorder>
      </div>
    </div>
  );
}

/* ── Stress Test Overlay ── */
export function StressOverlay({ socket, onClose }) {
  const [enabled, setEnabled] = useState(false);
  const [spread, setSpread] = useState(1.0);
  const [vol, setVol] = useState(1.0);
  const [latency, setLatency] = useState(0);
  const apply = () => {
    socket.emit("set_stress", { enabled, spread_mult: spread, vol_mult: vol, latency });
    onClose();
  };
  return (
    <div className="overlay-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="overlay-panel" style={{ minWidth: 300 }}>
        <h3>⚡ Stress Test Config</h3>
        <p style={{ color: "#787b86", fontSize: 11, marginBottom: 10 }}>Applied on next <b>New Sim</b></p>
        <div className="stress-row"><label>Enable stress</label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /></div>
        <div className="stress-row"><label>Spread multiplier</label><input type="number" value={spread} min={0.5} max={10} step={0.1} onChange={e => setSpread(+e.target.value)} /></div>
        <div className="stress-row"><label>Vol multiplier</label><input type="number" value={vol} min={0.5} max={10} step={0.1} onChange={e => setVol(+e.target.value)} /></div>
        <div className="stress-row"><label>Latency (steps)</label><input type="number" value={latency} min={0} max={100} step={1} onChange={e => setLatency(+e.target.value)} /></div>
        <StarBorder as="button" className="stress-apply" onClick={apply} color="#26a69a">Apply &amp; Save</StarBorder>
        <StarBorder as="button" className="overlay-close" onClick={onClose} color="#ef5350">Cancel</StarBorder>
      </div>
    </div>
  );
}

/* ── EBB Strategy Metrics Overlay ── */
export function EBBOverlay({ metrics, onClose }) {
  const vc = v => v >= 0 ? "#26a69a" : "#ef5350";
  const row = (l, v, style) => (
    <div key={l} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
      <span style={{ color: "#787b86" }}>{l}</span>
      <span style={{ color: style || "#d1d4dc" }}>{v}</span>
    </div>
  );
  const m = metrics;
  return (
    <div className="overlay-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="ebb-overlay-panel">
        <h3 style={{ margin: "0 0 4px", color: "#d1d4dc", fontSize: 14 }}>EMA Bollinger Scalper v2</h3>
        <p style={{ color: "#787b86", fontSize: 10, margin: "0 0 12px" }}>EMA30/50 + BB breakout | EMA200 + ADX | 2.0 ATR SL | 1.5 R:R | 5m</p>
        {!m || !m.total_trades ? (
          <div className="tp-empty">No completed trades yet — Capital: {m ? "$" + m.capital?.toFixed(2) : "—"}</div>
        ) : (
          <div style={{ fontSize: 12 }}>
            <div style={{ borderBottom: "1px solid #2a2e39", paddingBottom: 8, marginBottom: 8 }}>
              <div style={{ color: "#787b86", fontSize: 10, textTransform: "uppercase", marginBottom: 4 }}>Performance</div>
              {row("Net P&L", `${m.net_pnl >= 0 ? "+" : ""}$${Math.abs(m.net_pnl).toFixed(2)} (${m.net_pnl_pct}%)`, vc(m.net_pnl))}
              {row("Capital", "$" + m.capital.toFixed(2))}
              {row("Total Trades", m.total_trades)}
              {row("Win Rate", m.win_rate + "%")}
              {row("Profit Factor", m.profit_factor)}
              {row("Sharpe", m.sharpe, vc(m.sharpe))}
            </div>
            <div style={{ borderBottom: "1px solid #2a2e39", paddingBottom: 8, marginBottom: 8 }}>
              <div style={{ color: "#787b86", fontSize: 10, textTransform: "uppercase", marginBottom: 4 }}>Risk</div>
              {row("Max DD", m.max_dd + "%", "#ef5350")}
              {row("Avg Win", "+$" + Math.abs(m.avg_win).toFixed(2), "#26a69a")}
              {row("Avg Loss", "-$" + Math.abs(m.avg_loss).toFixed(2), "#ef5350")}
              {row("Total Fees", "$" + m.total_fees, "#787b86")}
            </div>
          </div>
        )}
        <StarBorder as="button" className="overlay-close" onClick={onClose} color="#ef5350">Close</StarBorder>
      </div>
    </div>
  );
}
