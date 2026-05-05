/**
 * App.jsx — SynthCrypto v3 Auth + Navigation
 * 
 * Single-page app with state-based routing:
 *   • "auth"      → Glassmorphism login/register page
 *   • "dashboard"  → Portfolio dashboard
 *   • "simulator"  → Live trading simulator
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { io } from "socket.io-client";
import LoginForm from "./components/LoginForm";
import RegisterForm from "./components/RegisterForm";
import GoogleButton from "./components/GoogleButton";
import Dashboard from "./components/Dashboard";
import SimulatorPage from "./components/SimulatorPage";
import LandingPage from "./components/LandingPage";

const AUTH_SERVER = "http://localhost:3001";
const SIMULATOR_URL = "http://localhost:8000";

// Persistent socket for capturing trade events across page navigations
const appSocket = io(SIMULATOR_URL, { autoConnect: false, path: "/ws/socket.io" });

// ── Ticker data ──────────────────────────────────────────────────────────────
const TICKERS = [
  { sym: "BTC/USD", price: 68423.5,  chg: 2.34 },
  { sym: "ETH/USD", price: 3891.2,   chg: -0.87 },
  { sym: "SOL/USD", price: 178.45,   chg: 5.12 },
  { sym: "BNB/USD", price: 612.3,    chg: 1.03 },
  { sym: "XRP/USD", price: 0.6234,   chg: -1.45 },
  { sym: "ADA/USD", price: 0.4821,   chg: 3.67 },
  { sym: "DOGE/USD", price: 0.1534,  chg: -2.11 },
  { sym: "AVAX/USD", price: 38.92,   chg: 4.28 },
  { sym: "DOT/USD", price: 7.45,     chg: 0.92 },
  { sym: "LINK/USD", price: 16.78,   chg: 1.56 },
  { sym: "MATIC/USD", price: 0.812,  chg: -0.42 },
  { sym: "UNI/USD", price: 9.34,     chg: 2.88 },
];

function TickerBar() {
  // Duplicate for seamless scroll loop
  const items = [...TICKERS, ...TICKERS];
  return (
    <div className="ticker-bar">
      <div className="ticker-scroll">
        {items.map((t, i) => (
          <div className="ticker-item" key={i}>
            <span className="sym">{t.sym}</span>
            <span>${t.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}</span>
            <span className={t.chg >= 0 ? "up" : "dn"}>
              {t.chg >= 0 ? "+" : ""}{t.chg.toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Particle System ──────────────────────────────────────────────────────────

function useParticles(canvasRef) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let animId;
    const particles = [];

    function resize() {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener("resize", resize);

    class Particle {
      constructor() { this.reset(); }
      reset() {
        this.x = Math.random() * canvas.width;
        this.y = Math.random() * canvas.height;
        this.vx = (Math.random() - 0.5) * 0.3;
        this.vy = (Math.random() - 0.5) * 0.3;
        this.r = Math.random() * 1.5 + 0.3;
        this.alpha = Math.random() * 0.4 + 0.1;
        this.color = Math.random() > 0.5 ? "38,166,154" : "41,98,255";
      }
      update() {
        this.x += this.vx;
        this.y += this.vy;
        if (this.x < 0 || this.x > canvas.width || this.y < 0 || this.y > canvas.height) {
          this.reset();
        }
      }
      draw() {
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${this.color},${this.alpha})`;
        ctx.fill();
      }
    }

    for (let i = 0; i < 55; i++) particles.push(new Particle());

    function drawLines() {
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < 110) {
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = `rgba(38,166,154,${0.055 * (1 - d / 110)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
    }

    function animate() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      particles.forEach((p) => { p.update(); p.draw(); });
      drawLines();
      animId = requestAnimationFrame(animate);
    }
    animate();

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(animId);
    };
  }, [canvasRef]);
}

// ── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [page, setPage] = useState(() => {
    const saved = localStorage.getItem("synthcrypto_page");
    return (saved === "simulator" || saved === "dashboard") ? saved : "landing";
  });      // "landing" | "auth" | "dashboard" | "simulator"
  const [tab, setTab] = useState("login");        // "login" | "register"
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const canvasRef = useRef(null);

  useParticles(canvasRef);

  // ── Persistent trade tracking (survives Dashboard ↔ Simulator navigation) ──
  const [liveTrades, setLiveTrades] = useState([]);

  useEffect(() => {
    appSocket.connect();

    appSocket.on("order_result", d => {
      if (d.status === "closed") {
        const trade = {
          id: `live-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          side: d.side || "—",
          symbol: d.symbol || "SIM",
          entry_price: d.entry_price || 0,
          exit_price: d.exit_price || 0,
          size_usd: d.size_usd || 0,
          pnl: d.pnl || 0,
          closed_at: new Date().toISOString(),
          isLive: true,
        };
        setLiveTrades(prev => [trade, ...prev]);
      }
    });

    appSocket.on("new_sim", () => {
      setLiveTrades([]);
    });

    return () => { appSocket.off(); appSocket.disconnect(); };
  }, []);

  useEffect(() => {
    localStorage.setItem("synthcrypto_page", page);
  }, [page]);

  // On mount: check if already logged in
  useEffect(() => {
    fetch(`${AUTH_SERVER}/api/auth/me`, { credentials: "include" })
      .then((r) => { 
        if (r.ok) {
          setPage(prev => (prev === "auth" || prev === "landing") ? "dashboard" : prev);
        } else {
          setPage(prev => prev === "dashboard" ? "landing" : prev);
        }
      })
      .catch(() => setPage(prev => prev === "dashboard" ? "landing" : prev));
  }, []);

  // Check URL for OAuth error param
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthError = params.get("error");
    if (oauthError) {
      setError(decodeURIComponent(oauthError.replace(/\+/g, " ")));
      window.history.replaceState({}, "", window.location.pathname);
    }
    // If redirected back from Google OAuth with a token cookie, go to dashboard
    const oauthSuccess = params.get("auth");
    if (oauthSuccess === "success") {
      setPage("dashboard");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const handleSuccess = useCallback((user) => {
    setError("");
    setSuccess(`Welcome, ${user.username || user.email}!`);
    setTimeout(() => {
      setPage("dashboard");
    }, 800);
  }, []);

  const handleError = useCallback((msg) => {
    setSuccess("");
    setError(msg);
  }, []);

  // When switching tabs, clear messages
  const switchTab = (t) => {
    setTab(t);
    setError("");
    setSuccess("");
  };

  // ── Render pages ──
  if (page === "landing") {
    return <LandingPage onGetStarted={() => setPage("auth")} />;
  }

  if (page === "simulator") {
    return <SimulatorPage onBack={() => setPage("dashboard")} />;
  }

  if (page === "dashboard") {
    return (
      <Dashboard
        onLogout={() => setPage("landing")}
        onLaunchSimulator={() => setPage("simulator")}
        liveTrades={liveTrades}
        onResetTrades={() => setLiveTrades([])}
      />
    );
  }

  // ── Auth page ──
  return (
    <>
      {/* Background layers */}
      <div className="orb orb-1" />
      <div className="orb orb-2" />
      <div className="orb orb-3" />
      <canvas id="particles-canvas" ref={canvasRef} />

      {/* Main content */}
      <div className="app-wrapper">
        <div className="auth-card">
          {/* Brand */}
          <div className="brand">
            <div className="brand-icon">⬡</div>
            <h1>SynthCrypto <span className="brand-tag">v3</span></h1>
            <div className="brand-sub">Phase 2 Live Market Simulator</div>
          </div>

          {/* Tab switcher */}
          <div className="tab-switcher">
            <button
              className={`tab-btn${tab === "login" ? " active" : ""}`}
              onClick={() => switchTab("login")}
            >
              Sign In
            </button>
            <button
              className={`tab-btn${tab === "register" ? " active" : ""}`}
              onClick={() => switchTab("register")}
            >
              Create Account
            </button>
          </div>

          {/* Google OAuth */}
          <GoogleButton />

          <div className="divider"><span>or</span></div>

          {/* Messages */}
          {error && (
            <div className="error-msg">
              <span>⚠</span> {error}
            </div>
          )}
          {success && (
            <div className="success-msg">
              <span>✓</span> {success}
            </div>
          )}

          {/* Forms */}
          {tab === "login" ? (
            <LoginForm onSuccess={handleSuccess} onError={handleError} />
          ) : (
            <RegisterForm onSuccess={handleSuccess} onError={handleError} />
          )}

          {/* Footer */}
          <div className="auth-footer">
            <span>Engines:</span> GARCH · Volume · Slippage · Correlation · Cascade
          </div>
        </div>
      </div>

      {/* Ambient ticker */}
      <TickerBar />
    </>
  );
}
