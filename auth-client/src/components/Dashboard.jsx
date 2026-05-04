/**
 * Dashboard.jsx — SynthCrypto v3 Portfolio Dashboard
 *
 * Premium glassmorphism dashboard with:
 *   • Animated balance counter
 *   • PnL summary with trend indicators
 *   • Recent trade history with side badges
 *   • Glowing "Launch Simulator" CTA
 *   • Auto-refresh portfolio data
 */

import { useState, useEffect, useRef, useCallback } from "react";
import "./Dashboard.css";

const AUTH_SERVER = "http://localhost:3001";
const SIMULATOR_URL = "http://localhost:8000";
const LOGIN_URL = "http://localhost:5173";

async function readJsonResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const body = (await res.text()).trim();
    const preview = body.slice(0, 120) || "Empty response";
    throw new Error(`Expected JSON from ${res.url}, received: ${preview}`);
  }
  return res.json();
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
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(start + diff * eased);
      if (progress < 1) ref.current = requestAnimationFrame(tick);
    }

    ref.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(ref.current);
  }, [value, duration]);

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

// ── Sparkline (tiny SVG chart) ───────────────────────────────────────────────
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
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block" }}
    >
      <defs>
        <linearGradient id={`sp-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        points={points}
        strokeLinejoin="round"
      />
      {/* area fill */}
      <polygon
        fill={`url(#sp-${color.replace("#", "")})`}
        points={`0,${height} ${points} ${width},${height}`}
      />
    </svg>
  );
}

// ── Main Dashboard ───────────────────────────────────────────────────────────
export default function Dashboard({ onLogout, onLaunchSimulator }) {
  const [portfolio, setPortfolio] = useState(null);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const goToLogin = useCallback(() => {
    if (onLogout) onLogout();
  }, [onLogout]);

  const fetchPortfolio = useCallback(async () => {
    try {
      setError("");
      // Fetch user info
      const userRes = await fetch(`${AUTH_SERVER}/api/auth/me`, {
        credentials: "include",
      });
      if (userRes.status === 401) {
        goToLogin();
        return;
      }
      const userData = await readJsonResponse(userRes);
      setUser(userData.user || userData);

      // Fetch portfolio
      const res = await fetch(`${AUTH_SERVER}/api/portfolio/me`, {
        credentials: "include",
      });
      if (res.status === 401) {
        goToLogin();
        return;
      }
      const data = await readJsonResponse(res);
      if (!res.ok) throw new Error(data.error || "Failed to load portfolio");
      setPortfolio(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [goToLogin]);

  useEffect(() => {
    fetchPortfolio();
    // Auto-refresh every 15 seconds
    const interval = setInterval(fetchPortfolio, 15000);
    return () => clearInterval(interval);
  }, [fetchPortfolio]);

  const handleLogout = async () => {
    await fetch(`${AUTH_SERVER}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    goToLogin();
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
            <button className="btn-primary" onClick={goToLogin}>
              Back to Login
            </button>
          </div>
        </div>
      </>
    );
  }

  const pnlPositive = portfolio.total_pnl >= 0;
  const pnlPercent =
    portfolio.balance > 0
      ? ((portfolio.total_pnl / (portfolio.balance - portfolio.total_pnl)) * 100).toFixed(2)
      : "0.00";

  // Build equity sparkline from trades
  const equityData = [10000];
  let running = 10000;
  if (portfolio.trades && portfolio.trades.length > 0) {
    const reversed = [...portfolio.trades].reverse();
    for (const t of reversed) {
      running += parseFloat(t.pnl || 0);
      equityData.push(running);
    }
  }

  const wins = portfolio.trades?.filter((t) => parseFloat(t.pnl) > 0).length || 0;
  const losses = portfolio.trades?.filter((t) => parseFloat(t.pnl) <= 0).length || 0;
  const totalTrades = portfolio.trades?.length || 0;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : "—";

  return (
    <>
      {/* Background */}
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="orb orb-3" />

      <div className="dash-wrapper">
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="dash-header">
          <div className="dash-brand">
            <div className="brand-icon" style={{ width: 40, height: 40, fontSize: 20, marginBottom: 0, borderRadius: 12 }}>
              ⬡
            </div>
            <div>
              <h1 className="dash-title">
                SynthCrypto <span className="brand-tag">v3</span>
              </h1>
              <div className="dash-subtitle">
                {user?.username || user?.email || "Trader"}
              </div>
            </div>
          </div>
          <div className="dash-header-actions">
            <button className="dash-btn-ghost" onClick={fetchPortfolio} title="Refresh">
              ↻
            </button>
            <button onClick={onLaunchSimulator} className="dash-btn-ghost dash-btn-link">
              Simulator
            </button>
            <button className="dash-btn-ghost" onClick={goToLogin}>
              Logout
            </button>
          </div>
        </div>

        {/* ── Stat Cards ─────────────────────────────────────────────────── */}
        <div className="dash-stats-grid">
          {/* Balance Card */}
          <div className="dash-stat-card dash-stat-primary">
            <div className="dash-stat-label">Available Balance</div>
            <div className="dash-stat-value">
              <AnimatedValue value={portfolio.balance} />
            </div>
            <div className="dash-stat-sparkline">
              <Sparkline
                data={equityData}
                color={pnlPositive ? "#26a69a" : "#ef5350"}
                width={140}
                height={36}
              />
            </div>
          </div>

          {/* PnL Card */}
          <div className={`dash-stat-card ${pnlPositive ? "dash-stat-green" : "dash-stat-red"}`}>
            <div className="dash-stat-label">Realized P&L</div>
            <div className={`dash-stat-value ${pnlPositive ? "pnl-up" : "pnl-dn"}`}>
              {pnlPositive ? "+" : ""}
              <AnimatedValue value={portfolio.total_pnl} prefix="$" />
            </div>
            <div className="dash-stat-sub">
              <span className={pnlPositive ? "pnl-up" : "pnl-dn"}>
                {pnlPositive ? "▲" : "▼"} {pnlPercent}%
              </span>
              <span className="dash-stat-muted"> from initial</span>
            </div>
          </div>

          {/* Trades Card */}
          <div className="dash-stat-card">
            <div className="dash-stat-label">Total Trades</div>
            <div className="dash-stat-value">{totalTrades}</div>
            <div className="dash-stat-sub">
              <span className="pnl-up">{wins}W</span>
              <span className="dash-stat-muted"> / </span>
              <span className="pnl-dn">{losses}L</span>
              <span className="dash-stat-muted"> · {winRate}% WR</span>
            </div>
          </div>
        </div>

        {/* ── Launch CTA ─────────────────────────────────────────────────── */}
        <div className="dash-launch-link" id="launch-simulator-btn" onClick={onLaunchSimulator} style={{cursor: 'pointer'}}>
          <button className="dash-launch-btn" style={{pointerEvents: 'none'}}>
            <span className="dash-launch-icon">🚀</span>
            <span>Launch Simulator</span>
            <span className="dash-launch-arrow">→</span>
          </button>
        </div>

        {/* ── Trade History ───────────────────────────────────────────────── */}
        <div className="dash-card dash-trades-card">
          <div className="dash-trades-header">
            <h2 className="dash-trades-title">Recent Trades</h2>
            <span className="dash-trades-count">{totalTrades} total</span>
          </div>

          {totalTrades === 0 ? (
            <div className="dash-trades-empty">
              <div className="dash-trades-empty-icon">📊</div>
              <p>No trades yet</p>
              <p className="dash-trades-empty-sub">
                Jump into the simulator to start trading!
              </p>
            </div>
          ) : (
            <div className="dash-trades-list">
              {/* Table header */}
              <div className="dash-trade-row dash-trade-header-row">
                <span>Side</span>
                <span>Symbol</span>
                <span>Entry</span>
                <span>Exit</span>
                <span>Size</span>
                <span className="dash-trade-pnl-col">P&L</span>
                <span>Date</span>
              </div>
              {portfolio.trades.map((trade) => {
                const pnl = parseFloat(trade.pnl);
                const isWin = pnl >= 0;
                return (
                  <div className="dash-trade-row" key={trade.id}>
                    <span>
                      <span className={`dash-side-badge ${trade.side}`}>
                        {trade.side.toUpperCase()}
                      </span>
                    </span>
                    <span className="dash-trade-symbol">{trade.symbol || "SIM"}</span>
                    <span className="dash-trade-mono">
                      ${parseFloat(trade.entry_price).toFixed(2)}
                    </span>
                    <span className="dash-trade-mono">
                      ${parseFloat(trade.exit_price).toFixed(2)}
                    </span>
                    <span className="dash-trade-mono">
                      ${parseFloat(trade.size_usd).toFixed(0)}
                    </span>
                    <span className={`dash-trade-pnl-col ${isWin ? "pnl-up" : "pnl-dn"}`}>
                      {isWin ? "+" : ""}${pnl.toFixed(2)}
                    </span>
                    <span className="dash-trade-date">
                      {new Date(trade.closed_at).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
