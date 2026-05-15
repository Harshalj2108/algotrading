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
import StarBorder from "./StarBorder";
import GooeyNav from "./GooeyNav";
import { AUTH_SERVER, SIMULATOR_URL, APP_URL } from '../config';


const AUTH_WS = AUTH_SERVER.replace(/^http/, "ws");

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

function normalizeSource(value, assetType = "") {
  const source = String(value || assetType || "").toLowerCase();
  if (source === "stock" || source === "stocks") return "stocks";
  if (source === "sim" || source === "simulator") return "simulator";
  return "crypto";
}

function marketLabel(value, assetType = "") {
  const source = normalizeSource(value, assetType);
  if (source === "stocks") return "Stocks";
  if (source === "simulator") return "Simulator";
  return "Crypto";
}

function tradeSide(trade) {
  const side = String(trade.buy_or_sell || trade.order_type || trade.side || "buy").toLowerCase();
  if (side === "sell" || side === "short" || side === "closed") return "sell";
  return "buy";
}

function tradeKey(trade) {
  const side = tradeSide(trade);
  const source = normalizeSource(trade.source_market, trade.asset_type);
  return trade.event_key || `${source}:${trade.trade_id || trade.id}:${side}`;
}

function mergeTrades(trades) {
  const byKey = new Map();
  for (const trade of trades || []) {
    if (!trade) continue;
    byKey.set(tradeKey(trade), trade);
  }
  return Array.from(byKey.values()).sort((a, b) => {
    const at = new Date(a.timestamp || a.created_at || a.closed_at || 0).getTime();
    const bt = new Date(b.timestamp || b.created_at || b.closed_at || 0).getTime();
    return bt - at;
  });
}

function normalizeSimPosition(position) {
  return {
    ...position,
    id: position.id,
    trade_id: position.id,
    asset_symbol: "SIM",
    symbol: "SIM",
    asset_type: "simulator",
    source_market: "simulator",
    order_type: position.side === "short" ? "sell" : "buy",
    current_value: Number(position.size_usd) || 0,
    quantity: Number(position.qty) || 0,
    profit_loss: Number(position.upnl) || 0,
  };
}

function positionKey(position) {
  return `${normalizeSource(position.source_market, position.asset_type)}:${position.id || position.trade_id}`;
}

function mergePositions(positions) {
  const byKey = new Map();
  for (const position of positions || []) {
    if (!position) continue;
    byKey.set(positionKey(position), position);
  }
  return Array.from(byKey.values());
}

// ── Animated counter ─────────────────────────────────────────────────────────

function AnimatedValue({ value, prefix = "S", decimals = 2, duration = 1000 }) {
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
export default function Dashboard({ onLogout, onLaunchSimulator, onLaunchCrypto, onLaunchStocks, onOpenPosition, liveTrades = [], onResetTrades, onBuyMore, onGoHome }) {
  const [showRewardPopup, setShowRewardPopup] = useState(() => {
    return localStorage.getItem("isNewRegistration") === "true";
  });

  const handleClaimReward = () => {
    localStorage.removeItem("isNewRegistration");
    setShowRewardPopup(false);
  };

  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Live persisted paper-trading data
  const [balance, setBalance] = useState(10000);
  const [rpnl, setRpnl] = useState(0);
  const [positions, setPositions] = useState([]);
  const [paperSummary, setPaperSummary] = useState({});
  const [connected, setConnected] = useState(false);

  // Persisted trades from auth-server DB
  const [dbTrades, setDbTrades] = useState([]);
  const [tradeFeed, setTradeFeed] = useState([]);
  const [feedHasMore, setFeedHasMore] = useState(true);
  const [feedLoadingMore, setFeedLoadingMore] = useState(false);
  const [simPositions, setSimPositions] = useState([]);

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
          if (data.paper) {
            setBalance(Number(data.paper.wallet?.virtual_balance) || 0);
            setRpnl(Number(data.paper.summary?.realized_profit_loss) || 0);
            setPositions(data.paper.positions || []);
            setPaperSummary(data.paper.summary || {});
            setDbTrades(data.paper.history || []);
            if (data.trade_feed) setTradeFeed(prev => mergeTrades([...data.trade_feed, ...prev]));
          } else if (data.trades) {
            setDbTrades(data.trades);
          }
        }
      } catch { /* portfolio endpoint optional */ }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [goToLogin]);

  const fetchTradeFeed = useCallback(async ({ append = false } = {}) => {
    if (append && (!feedHasMore || feedLoadingMore)) return;
    setFeedLoadingMore(true);
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (append && tradeFeed.length) {
        const before = tradeFeed[tradeFeed.length - 1]?.timestamp
          || tradeFeed[tradeFeed.length - 1]?.created_at
          || tradeFeed[tradeFeed.length - 1]?.closed_at;
        if (before) params.set("before", before);
      }
      const res = await fetch(`${AUTH_SERVER}/api/portfolio/trade-feed?${params.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) return;
      const data = await readJsonResponse(res);
      const nextTrades = data.trades || [];
      setTradeFeed(prev => append ? mergeTrades([...prev, ...nextTrades]) : mergeTrades([...nextTrades, ...prev]));
      setFeedHasMore(Boolean(data.has_more) && nextTrades.length > 0);
    } catch {
      // Live feed is best-effort; the portfolio fetch still keeps the dashboard usable.
    } finally {
      setFeedLoadingMore(false);
    }
  }, [feedHasMore, feedLoadingMore, tradeFeed]);

  // Connect to simulator socket for live balance/positions data
  useEffect(() => {
    simSocket.connect();

    simSocket.on("connect", () => setConnected(true));
    simSocket.on("disconnect", () => setConnected(false));
    simSocket.on("tick", d => {
      if (d.positions) setSimPositions(d.positions.map(normalizeSimPosition));
    });
    simSocket.on("new_sim", () => setSimPositions([]));

    return () => { simSocket.off(); simSocket.disconnect(); };
  }, []);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer = null;
    let ws = null;

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(`${AUTH_WS}/api/portfolio/trade-feed/ws`);

      ws.onopen = () => {
        if (stopped) ws.close();
      };

      ws.onmessage = (event) => {
        if (stopped) return;
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "trade" && payload.trade) {
            setTradeFeed(prev => mergeTrades([payload.trade, ...prev]));
          }
        } catch {
          // Ignore malformed websocket payloads.
        }
      };

      ws.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connect, 2500);
      };

      ws.onerror = () => {
        if (ws && ws.readyState === 1) ws.close();
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(reconnectTimer);
      if (ws) {
        ws.onclose = null; // Prevent reconnect loop on unmount
        ws.onerror = null;
        if (ws.readyState === 1) {
          ws.close();
        } else if (ws.readyState === 0) {
          // If still connecting, close it immediately upon open to suppress browser warnings
          ws.onopen = () => ws.close();
        }
      }
    };
  }, []);

  useEffect(() => {
    setTimeout(fetchUserData, 0);
    const interval = setInterval(fetchUserData, 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => fetchTradeFeed(), 0);
    const interval = setInterval(() => fetchTradeFeed(), 15000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogout = async () => {
    await fetch(`${AUTH_SERVER}/api/auth/logout`, { method: "POST", credentials: "include" });
    goToLogin();
  };

  // ── Reset portfolio ──
  const [resetting, setResetting] = useState(false);

  const handleReset = async () => {
    if (!window.confirm(
      "Reset your entire portfolio?\n\nThis will:\n• Delete all trade history\n• Reset balance to S10,000\n• Start a new simulation\n\nThis cannot be undone."
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
      setPaperSummary({});
      setTradeFeed([]);
      setFeedHasMore(true);
      setSimPositions([]);

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
  const allPositions = mergePositions([...positions, ...simPositions]);
  const activePositions = allPositions.length;
  const totalPositionValue = positions.reduce((s, p) => s + (Number(p.current_value ?? p.size_usd) || 0), 0);
  const portfolioValue = Number(paperSummary.total_portfolio_value) || (balance + totalPositionValue);

  // Merge live + DB trades (live first, then DB)
  const allTrades = mergeTrades([...tradeFeed, ...liveTrades, ...dbTrades]);
  const allTradeCount = allTrades.length;
  const settledTrades = allTrades.filter(t => tradeSide(t) === "sell" && (t.pnl != null || t.profit_loss != null));
  const wins = settledTrades.filter(t => parseFloat(t.pnl ?? t.profit_loss) > 0).length;
  const losses = settledTrades.filter(t => parseFloat(t.pnl ?? t.profit_loss) <= 0).length;
  const winRate = settledTrades.length > 0 ? ((wins / settledTrades.length) * 100).toFixed(1) : "—";

  // Equity sparkline
  const equityData = [10000];
  let running = 10000;
  for (const t of [...settledTrades].reverse()) { running += parseFloat(t.pnl ?? t.profit_loss ?? 0); equityData.push(running); }

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
            {pnlPositive ? "+" : ""}<AnimatedValue value={rpnl} prefix="S" />
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
              <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.4 }}>📭</div>
              <div className="dash-stat-muted">No open positions</div>
            </div>
          ) : (
            allPositions.map(p => {
              const positionPnl = Number(p.upnl ?? p.profit_loss) || 0;
              const positionSource = marketLabel(p.source_market, p.asset_type);
              return (
                <button
                  key={positionKey(p)}
                  type="button"
                  className="bento-pos-card bento-pos-clickable"
                  onClick={() => onOpenPosition?.(p)}
                  aria-label={`Open ${p.asset_symbol || p.symbol || "SIM"} chart`}
                >
                  <div className="bento-pos-row">
                    <span className="pnl-up">
                      {(p.order_type || "buy").toUpperCase()} {p.asset_symbol || p.symbol || "SIM"}
                    </span>
                    <span className={positionPnl >= 0 ? "pnl-up" : "pnl-dn"}>
                      {positionPnl >= 0 ? "+" : ""}S{positionPnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="bento-pos-row bento-pos-detail">
                    <span>Entry: {fmtPrice(p.entry_price)}</span>
                    <span>Invested: S{(Number(p.size_usd ?? p.invested_amount) || 0).toFixed(0)}</span>
                  </div>
                  <div className="bento-pos-row bento-pos-detail">
                    <span>{positionSource}</span>
                    <span>Qty: {(Number(p.quantity ?? p.qty) || 0).toFixed(4)}</span>
                  </div>
                </button>
              );
            })
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
          <div className="dash-stat-sub" style={{ marginTop: '6px' }}>
            <span className="dash-stat-muted">Cash: S{balance.toFixed(2)}</span>
            {totalPositionValue > 0 && (
              <span className="dash-stat-muted"> + Positions: S{totalPositionValue.toFixed(0)}</span>
            )}
          </div>
          <div className="dash-stat-sparkline" style={{ marginTop: '8px' }}>
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
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
          <GooeyNav
            items={[
              { label: "Simulator", onClick: onLaunchSimulator },
              { label: "Live Crypto", onClick: onLaunchCrypto },
              { label: "Live Stocks", onClick: onLaunchStocks }
            ]}
          />
        </div>
      ),
      color: 'rgba(139, 92, 246, 0.05)',
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
  const recentTrades = allTrades;
  const handleTradeScroll = (event) => {
    const el = event.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
      fetchTradeFeed({ append: true });
    }
  };

  return (
    <>
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="orb orb-3" />

      {/* Home Button — top left */}
      <div style={{ position: 'absolute', top: '24px', left: '32px', zIndex: 100 }}>
        <button
          onClick={onGoHome}
          style={{
            padding: '8px 16px',
            borderRadius: '8px',
            cursor: 'pointer',
            background: 'transparent',
            border: '1px solid rgba(139, 92, 246, 0.5)',
            color: '#D8B4FE',
            fontWeight: 600,
            transition: 'all 0.2s ease',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.background = 'rgba(139, 92, 246, 0.1)';
            e.currentTarget.style.borderColor = '#8B5CF6';
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.background = 'transparent';
            e.currentTarget.style.borderColor = 'rgba(139, 92, 246, 0.5)';
          }}
        >
          <span>←</span> Home
        </button>
      </div>

      {/* Profile Menu — top right */}
      <div className="dash-top-right-actions" ref={menuRef}>
        <button
          className="dash-profile-btn"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          <div className="dash-avatar">
            {user?.username ? user.username.charAt(0).toUpperCase() : (user?.email ? user.email.charAt(0).toUpperCase() : "U")}
          </div>
        </button>

        {menuOpen && (
          <div className="dash-dropdown-menu">
            <div className="dash-dropdown-header">
              <span className="dash-dropdown-name">{user?.username || "Trader"}</span>
              <span className="dash-dropdown-email">{user?.email || ""}</span>
            </div>
            <div className="dash-dropdown-divider" />
            <button className="dash-dropdown-item" onClick={() => { setMenuOpen(false); if (onBuyMore) onBuyMore(); }}>
              Buy More S
            </button>
            <button
              className="dash-dropdown-item"
              onClick={() => { setMenuOpen(false); handleReset(); }}
              disabled={resetting}
            >
              {resetting ? "⏳ Resetting..." : "Reset Portfolio"}
            </button>
            <button
              className="dash-dropdown-item"
              onClick={() => {
                setMenuOpen(false);
                if (user?.referral_code) {
                  prompt("Copy your referral link:", `${APP_URL}/register?ref=${user.referral_code}`);
                }
              }}
            >
              Show Referral Link
            </button>
            <div className="dash-dropdown-divider" />
            <button
              className="dash-dropdown-item text-danger"
              onClick={() => { setMenuOpen(false); handleLogout(); }}
            >
              Log out
            </button>
          </div>
        )}
      </div>

      {/* Connection indicator removed */}

      {/* Main: two-column layout */}
      <div className="dash-split-wrapper">
        {/* Left: Bento cards */}
        <div className="dash-left-col">
          <div className="dash-welcome">
            <h1 className="dash-title">
              Portfolio
            </h1>
            <div className="dash-subtitle" style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <span>{user?.username || user?.email || "Trader"}</span>
              {user?.referral_code && (
                <span style={{ fontSize: '0.85em', color: '#D8B4FE', display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ opacity: 0.7 }}>|</span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span>Code: <strong style={{ color: '#fff', letterSpacing: '1px' }}>{user.referral_code}</strong></span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(`${APP_URL}/register?ref=${user.referral_code}`);
                        alert("Referral link copied to clipboard!");
                      }}
                      style={{ background: 'rgba(139, 92, 246, 0.2)', border: '1px solid rgba(139, 92, 246, 0.5)', borderRadius: '4px', padding: '2px 8px', color: '#fff', cursor: 'pointer' }}
                    >
                      Copy Link
                    </button>
                  </span>
                  <span style={{ opacity: 0.7 }}>|</span>
                  <span>Share your code to earn 2000S per referral!</span>
                  <span style={{ opacity: 0.7 }}>|</span>
                  <span>Referrals: <strong style={{ color: '#fff' }}>{user?.referral_count || 0}</strong></span>
                </span>
              )}
            </div>
          </div>

          <MagicBento
            cards={bentoCards}
            glowColor="139, 92, 246"
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
              <div className="dash-trades-table-wrap" onScroll={handleTradeScroll}>
                <table className="dash-trades-table">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Market</th>
                      <th>Symbol</th>
                      <th>Price</th>
                      <th>Value</th>
                      <th>P&L</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentTrades.map((trade) => {
                      const pnl = parseFloat(trade.pnl ?? trade.profit_loss ?? 0);
                      const isWin = pnl >= 0;
                      const sideLabel = tradeSide(trade);
                      const symbolLabel = trade.asset_symbol || trade.symbol || "SIM";
                      const sizeValue = trade.trade_value ?? trade.size_usd ?? trade.invested_amount ?? 0;
                      const sourceLabel = marketLabel(trade.source_market, trade.asset_type);
                      const tradeTime = trade.timestamp || trade.closed_at || trade.created_at || "";
                      const priceValue = trade.execution_price ?? trade.entry_price ?? trade.exit_price ?? 0;
                      return (
                        <tr key={tradeKey(trade)} className={`dash-trade-row ${trade.isLive ? "dash-trade-live" : ""}`}>
                          <td>
                            <span className={`dash-side-badge ${sideLabel}`}>
                              {sideLabel.toUpperCase()}
                            </span>
                          </td>
                          <td><span className={`dash-source-badge ${normalizeSource(trade.source_market, trade.asset_type)}`}>{sourceLabel}</span></td>
                          <td className="dash-trade-symbol">{symbolLabel}</td>
                          <td className="dash-trade-mono">S{parseFloat(priceValue || 0).toFixed(2)}</td>
                          <td className="dash-trade-mono">S{parseFloat(sizeValue || 0).toFixed(0)}</td>
                          <td className={`dash-trade-pnl ${isWin ? "pnl-up" : "pnl-dn"}`}>
                            {isWin ? "+" : ""}S{pnl.toFixed(2)}
                          </td>
                          <td className="dash-trade-date">
                            {tradeTime ? new Date(tradeTime).toLocaleDateString("en-US", {
                              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                            }) : "—"}
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
      {showRewardPopup && (
        <div className="reward-popup-overlay">
          <div className="reward-popup-content">
            <h2>🎉 Congratulations!</h2>
            <p>You have received 10,000S</p>
            <button
              className="reward-popup-close-btn"
              onClick={handleClaimReward}
            >
              Claim Reward
            </button>
          </div>
        </div>
      )}
    </>
  );
}
