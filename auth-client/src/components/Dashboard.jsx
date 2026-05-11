/**
 * Dashboard.jsx — SynthCrypto v3 Portfolio Dashboard
 *
 * Live portfolio with MagicBento cards + trade history table.
 * Connects to the simulator via Socket.IO for real-time updates.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { io } from "socket.io-client";
import "./Dashboard.css";
import "./LandingPage.css";
import MagicBento from "./MagicBento";
import CircularText from "./CircularText";
import StarBorder from "./StarBorder";

const AUTH_SERVER = "http://localhost:3001";
const SIMULATOR_URL = "http://localhost:8000";

const simSocket = io(SIMULATOR_URL, { autoConnect: false, path: "/ws/socket.io" });

async function readJsonResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const body = (await res.text()).trim();
    const preview = body.slice(0, 120) || "Empty response";
    throw new Error(`Expected JSON from ${res.url}, received: ${preview}`);
  }
  return res.json();
}

function fmtPrice(v) {
  if (v == null) return "—";
  v = +v;
  if (v >= 10000) return v.toFixed(2);
  if (v >= 100) return v.toFixed(3);
  if (v >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

// ── Animated counter ─────────────────────────────────────────────────────────
function AnimatedValue({ value, prefix = "$", decimals = 2, duration = 800 }) {
  const [display, setDisplay] = useState(0);
  const ref = useRef(null);

  useEffect(() => {
    const start = display;
    const diff = value - start;
    const startTime = performance.now();
    function tick(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(start + diff * eased);
      if (progress < 1) ref.current = requestAnimationFrame(tick);
    }
    ref.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(ref.current);
  }, [value, duration, display]);

  return (
    <span>
      {prefix}
      {display.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
    </span>
  );
}

// ── Sparkline ────────────────────────────────────────────────────────────────
function Sparkline({ data, color = "#26a69a", width = 120, height = 32 }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      <defs>
        <linearGradient id={`sp-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} strokeLinejoin="round" />
      <polygon fill={`url(#sp-${color.replace("#", "")})`} points={`0,${height} ${points} ${width},${height}`} />
    </svg>
  );
}

// ── Main Dashboard ───────────────────────────────────────────────────────────
export default function Dashboard({ onLogout, onLaunchSimulator, onLaunchCrypto, onLaunchStocks, liveTrades = [], onResetTrades }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Live data from simulator
  const [balance, setBalance] = useState(10000);
  const [rpnl, setRpnl] = useState(0);
  const [positions, setPositions] = useState([]);
  const [connected, setConnected] = useState(false);

  // Persisted trades from auth-server DB
  const [dbTrades, setDbTrades] = useState([]);

  const goToLogin = useCallback(() => {
    if (onLogout) onLogout();
  }, [onLogout]);

  const fetchUserData = useCallback(async () => {
    try {
      setError("");
      const userRes = await fetch(`${AUTH_SERVER}/api/auth/me`, { credentials: "include" });
      if (userRes.status === 401) { goToLogin(); return; }
      const userData = await readJsonResponse(userRes);
      setUser(userData.user || userData);

      try {
        const res = await fetch(`${AUTH_SERVER}/api/portfolio/me`, { credentials: "include" });
        if (res.ok) {
          const data = await readJsonResponse(res);
          if (data.trades) setDbTrades(data.trades);
        }
      } catch { /* portfolio endpoint optional */ }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [goToLogin]);

  // Connect to simulator socket for live balance/positions data
  useEffect(() => {
    simSocket.connect();

    simSocket.on("connect", () => setConnected(true));
    simSocket.on("disconnect", () => setConnected(false));

    simSocket.on("init", d => {
      if (d.balance !== undefined) setBalance(d.balance);
    });

    simSocket.on("tick", d => {
      if (d.balance !== undefined) setBalance(d.balance);
      if (d.rpnl !== undefined) setRpnl(d.rpnl);
      if (d.positions !== undefined) setPositions(d.positions);
    });

    simSocket.on("new_sim", d => {
      setBalance(d.balance || 10000);
      setRpnl(0);
      setPositions([]);
    });

    simSocket.on("order_result", d => {
      if (d.status === "closed" && d.balance !== undefined) {
        setBalance(d.balance);
      }
    });

    return () => { simSocket.off(); simSocket.disconnect(); };
  }, []);

  useEffect(() => {
    setTimeout(fetchUserData, 0);
    const interval = setInterval(fetchUserData, 30000);
    return () => clearInterval(interval);
  }, [fetchUserData]);

  const handleLogout = async () => {
    await fetch(`${AUTH_SERVER}/api/auth/logout`, { method: "POST", credentials: "include" });
    goToLogin();
  };

  // ── Reset portfolio ──
  const [resetting, setResetting] = useState(false);

  const handleReset = async () => {
    if (!window.confirm(
      "Reset your entire portfolio?\n\nThis will:\n• Delete all trade history\n• Reset balance to $10,000\n• Start a new simulation\n\nThis cannot be undone."
    )) return;

    setResetting(true);
    try {
      // 1. Reset DB trades + balance
      const res = await fetch(`${AUTH_SERVER}/api/portfolio/reset`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error("Reset failed");

      // 2. Clear local state
      setDbTrades([]);
      setBalance(10000);
      setRpnl(0);
      setPositions([]);

      // 3. Clear App-level live trades
      if (onResetTrades) onResetTrades();

      // 4. Trigger a new simulation on the backend
      simSocket.emit("new_sim");

      // 5. Re-fetch user data to confirm reset
      await fetchUserData();
    } catch (err) {
      console.error("Reset failed:", err);
      alert("Failed to reset portfolio. Please try again.");
    } finally {
      setResetting(false);
    }
  };

  // ── Loading state ──────────────────────────────────────────────────────────
  if (loading) {
    return (
      <>
        <div className="orb orb-1" />
        <div className="orb orb-2" />
        <div className="orb orb-3" />
        <div className="dash-wrapper">
          <div className="dash-card" style={{ textAlign: "center", padding: "60px 40px" }}>
            <div className="dash-loader" />
            <p className="dash-loader-text">Loading your portfolio...</p>
          </div>
        </div>
      </>
    );
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (error) {
    return (
      <>
        <div className="orb orb-1" />
        <div className="orb orb-2" />
        <div className="dash-wrapper">
          <div className="dash-card">
            <div className="error-msg">⚠ {error}</div>
            <button className="btn-primary" onClick={goToLogin}>Back to Login</button>
          </div>
        </div>
      </>
    );
  }

  // ── Computed values ────────────────────────────────────────────────────────
  const INITIAL_CAPITAL = 10000;
  const pnlPositive = rpnl >= 0;
  const pnlPercent = ((rpnl / INITIAL_CAPITAL) * 100).toFixed(2);
  const activePositions = positions.length;
  const totalUpnl = positions.reduce((s, p) => s + (p.upnl || 0), 0);
  const totalPositionValue = positions.reduce((s, p) => s + (p.size_usd || 0), 0);
  const portfolioValue = balance + totalUpnl;

  // Merge live + DB trades (live first, then DB)
  const allTrades = [...liveTrades, ...dbTrades];
  const allTradeCount = allTrades.length;
  const wins = allTrades.filter(t => parseFloat(t.pnl) > 0).length;
  const losses = allTrades.filter(t => parseFloat(t.pnl) <= 0).length;
  const winRate = allTradeCount > 0 ? ((wins / allTradeCount) * 100).toFixed(1) : "—";

  // Equity sparkline
  const equityData = [10000];
  let running = 10000;
  for (const t of [...allTrades].reverse()) { running += parseFloat(t.pnl || 0); equityData.push(running); }

  /* ─────────────────────────────────────────────────────────────────────────
   *  BENTO CARD ORDER (matches the CSS grid layout):
   *
   *  ┌──────────┬──────────┬─────────────────────┐
   *  │ 1. P&L   │ 2. Trades│ 3. Positions (2×2)  │
   *  │ (square) │ (square) │                     │
   *  ├──────────┴──────────┤                     │
   *  │ 4. Portfolio Value  │                     │
   *  │ (rectangle 2×1)     ├──────────┬──────────┤
   *  ├─────────────────────┤ 6. Avail │          │
   *  │ 5. Simulator        │ Balance  │  (empty) │
   *  │ (rectangle 2×1)     │ (rect)   │          │
   *  └─────────────────────┴──────────┴──────────┘
   * ───────────────────────────────────────────────────────────────────────── */

  const bentoCards = [
    // Card 1: Realized P&L (square, top-left)
    {
      label: 'Realized P&L',
      content: (
        <>
          <div className={`dash-stat-value ${pnlPositive ? "pnl-up" : "pnl-dn"}`}>
            {pnlPositive ? "+" : ""}<AnimatedValue value={rpnl} prefix="$" />
          </div>
          <div className="dash-stat-sub">
            <span className={pnlPositive ? "pnl-up" : "pnl-dn"}>
              {pnlPositive ? "▲" : "▼"} {pnlPercent}%
            </span>
          </div>
        </>
      ),
      color: 'rgba(19, 23, 34, 0.4)'
    },
    // Card 2: Total Trades (square, top-left col 2)
    {
      label: 'Total Trades',
      content: (
        <>
          <div className="dash-stat-value">{allTradeCount}</div>
          <div className="dash-stat-sub">
            <span className="pnl-up">{wins}W</span>
            <span className="dash-stat-muted"> / </span>
            <span className="pnl-dn">{losses}L</span>
          </div>
        </>
      ),
      color: 'rgba(19, 23, 34, 0.4)'
    },
    // Card 3: Current Positions (large 2×2 square, top-right)
    {
      label: 'Current Positions',
      content: (
        <div className="bento-positions-list">
          {activePositions === 0 ? (
            <div className="bento-pos-empty">
              <div style={{fontSize: 28, marginBottom: 8, opacity: 0.4}}>📭</div>
              <div className="dash-stat-muted">No open positions</div>
            </div>
          ) : (
            positions.map(p => (
              <div key={p.id} className="bento-pos-card">
                <div className="bento-pos-row">
                  <span className={p.side === "long" ? "pnl-up" : "pnl-dn"}>
                    {p.side?.toUpperCase()} {p.leverage}×
                  </span>
                  <span className={(p.upnl || 0) >= 0 ? "pnl-up" : "pnl-dn"}>
                    {(p.upnl || 0) >= 0 ? "+" : ""}${(p.upnl || 0).toFixed(2)}
                  </span>
                </div>
                <div className="bento-pos-row bento-pos-detail">
                  <span>Entry: {fmtPrice(p.entry_price)}</span>
                  <span>Size: ${(p.size_usd || 0).toFixed(0)}</span>
                </div>
                <div className="bento-pos-row bento-pos-detail">
                  <span>Liq: <span className="pnl-dn">{fmtPrice(p.liq_price)}</span></span>
                  <span>Margin: ${(p.margin || 0).toFixed(2)}</span>
                </div>
              </div>
            ))
          )}
        </div>
      ),
      color: 'rgba(19, 23, 34, 0.4)'
    },
    // Card 4: Portfolio Value (wide rectangle, row 2 col 1-2)
    {
      label: 'Portfolio Value',
      content: (
        <>
          <div className="dash-stat-value">
            <AnimatedValue value={portfolioValue} />
          </div>
          <div className="dash-stat-sub" style={{marginTop: '6px'}}>
            <span className="dash-stat-muted">Cash: ${balance.toFixed(2)}</span>
            {totalPositionValue > 0 && (
              <span className="dash-stat-muted"> + Positions: ${totalPositionValue.toFixed(0)}</span>
            )}
          </div>
          <div className="dash-stat-sparkline" style={{marginTop: '8px'}}>
            <Sparkline data={equityData} color={pnlPositive ? "#26a69a" : "#ef5350"} width={200} height={32} />
          </div>
        </>
      ),
      color: 'rgba(19, 23, 34, 0.4)'
    },
    // Card 5: Apps / Markets (wide rectangle, row 3 col 1-2)
    {
      label: 'Markets & Simulators',
      content: (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: '10px 16px', gap: '12px' }}>
          <StarBorder as="button" onClick={onLaunchSimulator} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '6px', padding: '14px 8px', background: 'rgba(41,98,255,0.08)', border: '1px solid rgba(41,98,255,0.2)', borderRadius: '10px', cursor: 'pointer', transition: 'all 0.2s' }}>
            <span style={{ fontSize: '22px' }}>⬡</span>
            <span style={{ color: '#d1d4dc', fontSize: '12px', fontWeight: 700, letterSpacing: '0.3px' }}>Simulator</span>
          </StarBorder>
          <StarBorder as="button" onClick={onLaunchCrypto} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '6px', padding: '14px 8px', background: 'rgba(38,166,154,0.08)', border: '1px solid rgba(38,166,154,0.2)', borderRadius: '10px', cursor: 'pointer', transition: 'all 0.2s' }}>
            <span style={{ fontSize: '22px' }}>₿</span>
            <span style={{ color: '#d1d4dc', fontSize: '12px', fontWeight: 700, letterSpacing: '0.3px' }}>Live Crypto</span>
          </StarBorder>
          <StarBorder as="button" onClick={onLaunchStocks} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '6px', padding: '14px 8px', background: 'rgba(243,135,32,0.08)', border: '1px solid rgba(243,135,32,0.2)', borderRadius: '10px', cursor: 'pointer', transition: 'all 0.2s' }}>
            <span style={{ fontSize: '22px' }}>📈</span>
            <span style={{ color: '#d1d4dc', fontSize: '12px', fontWeight: 700, letterSpacing: '0.3px' }}>Live Stocks</span>
          </StarBorder>
        </div>
      ),
      color: 'rgba(38, 166, 154, 0.05)',
    },
    // Card 6: Available Balance (rectangle, row 3 col 3-4)

    {
      label: 'Available Balance',
      content: (
        <div className="dash-stat-value">
          <AnimatedValue value={balance} />
        </div>
      ),
      color: 'rgba(19, 23, 34, 0.4)'
    },
  ];

  // Recent trades: live trades first, then DB trades
  const recentTrades = allTrades.slice(0, 50);

  return (
    <>
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="orb orb-3" />

      {/* Circular brand — top left */}
      <div className="top-left-brand dash-brand-pos">
        <div className="top-left-logo">⬡</div>
        <CircularText
          text="SYNTHCRYPTO*SIMULATOR*"
          onHover="speedUp"
          spinDuration={20}
          className="brand-circular-text"
        />
      </div>

      {/* Logout + Reset — top right */}
      <div className="dash-top-right-actions">
        <StarBorder
          as="button"
          className="dash-reset-btn"
          onClick={handleReset}
          disabled={resetting}
          color="#26a69a"
        >
          {resetting ? "⏳ Resetting..." : "↺ Reset"}
        </StarBorder>
        <StarBorder
          as="button"
          className="dash-logout-btn"
          onClick={handleLogout}
          color="#ef5350"
        >
          Logout
        </StarBorder>
      </div>

      {/* Connection indicator */}
      <div className={`dash-conn-dot ${connected ? "live" : ""}`}>
        {connected ? "● live" : "○ offline"}
      </div>

      {/* Main: two-column layout */}
      <div className="dash-split-wrapper">
        {/* Left: Bento cards */}
        <div className="dash-left-col">
          <div className="dash-welcome">
            <h1 className="dash-title">
              Portfolio <span className="brand-tag">v3</span>
            </h1>
            <div className="dash-subtitle">
              {user?.username || user?.email || "Trader"}
            </div>
          </div>

          <MagicBento
            cards={bentoCards}
            glowColor="38, 166, 154"
            enableStars={true}
            enableTilt={true}
            enableMagnetism={true}
            clickEffect={true}
            particleCount={12}
          />
        </div>

        {/* Right: Trades summary + scrollable trade table */}
        <div className="dash-right-col">
          <div className="trades-summary-bar">
            <div className="trades-summary-item">
              <span className="trades-summary-label">Win Rate</span>
              <span className="trades-summary-val">{winRate !== "—" ? `${winRate}%` : "—"}</span>
            </div>
            <div className="trades-summary-item">
              <span className="pnl-up">{wins}W</span>
              <span className="dash-stat-muted"> / </span>
              <span className="pnl-dn">{losses}L</span>
            </div>
            <div className="trades-summary-item">
              <span className="trades-summary-label">Trades</span>
              <span className="trades-summary-val">{allTradeCount}</span>
            </div>
          </div>

          <div className="dash-trades-panel">
            <div className="dash-trades-header">
              <h2 className="dash-trades-title">Recent Trades</h2>
              <span className="dash-trades-count">{recentTrades.length} shown</span>
            </div>

            {recentTrades.length === 0 ? (
              <div className="dash-trades-empty">
                <div className="dash-trades-empty-icon">📊</div>
                <p>No trades yet</p>
                <p className="dash-trades-empty-sub">Jump into the simulator to start trading!</p>
              </div>
            ) : (
              <div className="dash-trades-table-wrap">
                <table className="dash-trades-table">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Symbol</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>Size</th>
                      <th>P&L</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentTrades.map((trade) => {
                      const pnl = parseFloat(trade.pnl);
                      const isWin = pnl >= 0;
                      return (
                        <tr key={trade.id} className={`dash-trade-row ${trade.isLive ? "dash-trade-live" : ""}`}>
                          <td>
                            <span className={`dash-side-badge ${trade.side}`}>
                              {(trade.side || "—").toUpperCase()}
                            </span>
                          </td>
                          <td className="dash-trade-symbol">{trade.symbol || "SIM"}</td>
                          <td className="dash-trade-mono">${parseFloat(trade.entry_price || 0).toFixed(2)}</td>
                          <td className="dash-trade-mono">${parseFloat(trade.exit_price || 0).toFixed(2)}</td>
                          <td className="dash-trade-mono">${parseFloat(trade.size_usd || 0).toFixed(0)}</td>
                          <td className={`dash-trade-pnl ${isWin ? "pnl-up" : "pnl-dn"}`}>
                            {isWin ? "+" : ""}${pnl.toFixed(2)}
                          </td>
                          <td className="dash-trade-date">
                            {new Date(trade.closed_at).toLocaleDateString("en-US", {
                              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                            })}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
 