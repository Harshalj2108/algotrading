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
import { AboutUsPage, LearnTradingPage } from "./components/PublicPages";
import LandingPage from "./components/LandingPage";
import StarBorder from "./components/StarBorder";
import AssetSearch from "./components/AssetSearch";
import LiveMarketPage from "./components/LiveMarketPage";
import DotField from "./components/DotField";
import BuyMore from "./components/BuyMore";

import { AUTH_SERVER, SIMULATOR_URL } from './config';
const PUBLIC_PATHS = {
  home: "/",
  about: "/about",
  learn: "/learn-trading",
};
const VALID_PAGES = new Set([
  "home",
  "about",
  "learn",
  "auth",
  "dashboard",
  "simulator",
  "crypto_search",
  "stocks_search",
  "buy_more",
]);

// Pages that require authentication — if the user is logged out,
// navigating here (including via browser back button) should redirect.
const PROTECTED_PAGES = new Set([
  "dashboard",
  "simulator",
  "crypto_search",
  "stocks_search",
  "buy_more",
]);

function isProtectedPage(page) {
  if (PROTECTED_PAGES.has(page)) return true;
  if (page && page.startsWith("live_market:")) return true;
  return false;
}

function normalizeStoredPage(page) {
  if (page === "landing") return "home";
  if (page && page.startsWith("live_market:")) return page;
  return VALID_PAGES.has(page) ? page : "home";
}

function pageFromPathname(pathname) {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/about") return "about";
  if (path === "/learn" || path === "/learn-trading") return "learn";
  if (path === "/signup" || path === "/login" || path === "/register") return "auth";
  if (path === "/dashboard") return "dashboard";
  if (path.startsWith("/markets/crypto/")) {
    return `live_market:crypto:${decodeURIComponent(path.slice("/markets/crypto/".length))}`;
  }
  if (path.startsWith("/markets/stocks/")) {
    return `live_market:stock:${decodeURIComponent(path.slice("/markets/stocks/".length))}`;
  }
  if (path.startsWith("/simulation")) return "simulator";
  if (path === "/buy-s") return "buy_more";
  if (path === "/") return null;
  return null;
}

function writePath(path) {
  if (window.location.pathname !== path) {
    window.history.pushState({}, "", path);
  }
}

function marketPath(assetClass, symbol) {
  const encoded = encodeURIComponent(symbol || "");
  return `/markets/${assetClass === "stock" ? "stocks" : "crypto"}/${encoded}`;
}

function simulationPath(symbol = "SIM") {
  return `/simulation/${encodeURIComponent(symbol || "SIM")}`;
}

function tradeEventKey(trade) {
  return trade?.event_key || trade?.trade_id || trade?.id || `${trade?.source_market || "simulator"}:${trade?.timestamp || Date.now()}`;
}

function prependUniqueTrade(prev, trade) {
  const key = tradeEventKey(trade);
  return [trade, ...prev.filter(item => tradeEventKey(item) !== key)].slice(0, 100);
}

function simulatorOpenTradeEvent(position, extra = {}) {
  if (!position) return null;
  const side = position.side === "short" ? "sell" : "buy";
  return {
    event_key: extra.event_key || `simulator:${position.id}:open`,
    trade_id: position.id,
    asset_symbol: "SIM",
    asset_type: "simulator",
    buy_or_sell: side,
    side,
    quantity: position.qty || 0,
    entry_price: position.entry_price || 0,
    execution_price: position.entry_price || 0,
    trade_value: position.size_usd || 0,
    profit_loss: 0,
    timestamp: new Date().toISOString(),
    source_market: "simulator",
  };
}

function simulatorCloseTradeEvent(result, extra = {}) {
  if (!result) return null;
  const tradeId = result.position_id || result.trade_id || result.id;
  if (!tradeId) return null;
  const side = result.side === "short" ? "buy" : "sell";
  return {
    event_key: extra.event_key || `simulator:${tradeId}:close:${result.reason || "manual"}`,
    trade_id: tradeId,
    asset_symbol: result.symbol || "SIM",
    asset_type: "simulator",
    buy_or_sell: side,
    side,
    quantity: result.qty || result.quantity || 0,
    entry_price: result.exit_price ?? result.entry_price ?? 0,
    exit_price: result.exit_price ?? null,
    execution_price: result.exit_price ?? result.entry_price ?? 0,
    trade_value: result.size_usd || 0,
    profit_loss: result.pnl || 0,
    pnl: result.pnl || 0,
    timestamp: new Date().toISOString(),
    source_market: "simulator",
  };
}

function persistTradeFeedEvent(trade) {
  if (!trade) return;
  fetch(`${AUTH_SERVER}/api/portfolio/trade-feed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(trade),
  }).catch(err => console.error("Failed to save trade feed event", err));
}

const SEO_BY_PAGE = {
  home: {
    title: "Crypto Trading Simulator | Learn Trading Risk-Free",
    description: "Practice crypto trading with virtual money using live market simulations and interactive learning tools.",
  },
  about: {
    title: "About Us | Crypto Learning Platform",
    description: "Learn about our mission to make crypto trading education practical, interactive, and risk-free.",
  },
  learn: {
    title: "Learn Crypto Trading | Beginner to Advanced",
    description: "Explore crypto basics, candlestick patterns, futures trading, spot trading, and technical indicators.",
  },
  auth: {
    title: "Sign In | SynthCrypto",
    description: "Sign in or create an account to use the SynthCrypto trading simulator.",
  },
  dashboard: {
    title: "Portfolio | SynthCrypto",
    description: "View your virtual crypto trading portfolio and simulation performance.",
  },
};

// Persistent socket for capturing trade events across page navigations
const appSocket = io(SIMULATOR_URL, { autoConnect: false, path: "/ws/socket.io" });

// ── Particle System ──────────────────────────────────────────────────────────

function useParticles(canvasRef, active) {
  useEffect(() => {
    if (!active) return;
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
  }, [canvasRef, active]);
}

// ── Search Wrapper ───────────────────────────────────────────────────────────
function SearchPageWrapper({ assetClass, onBack, onSelect }) {
  return (
    <div className="asset-search-shell">
      <div className="asset-search-dotfield-bg">
        <DotField
          dotRadius={2}
          dotSpacing={16}
          bulgeStrength={60}
          glowRadius={220}
          sparkle={true}
          waveAmplitude={0}
          gradientFrom="#8B5CF6"
          gradientTo="#D8B4FE"
          glowColor="rgba(139, 92, 246, 0.4)"
        />
      </div>
      <AssetSearch assetClass={assetClass} onSelect={onSelect} onBack={onBack} />
    </div>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [page, setPage] = useState(() => {
    const routedPage = pageFromPathname(window.location.pathname);
    if (routedPage) return routedPage;
    const saved = localStorage.getItem("synthcrypto_page");
    return normalizeStoredPage(saved);
  });
  const [tab, setTab] = useState(() => (window.location.pathname === "/signup" || window.location.pathname === "/register") ? "register" : "login");        // "login" | "register"
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [focusedChartPosition, setFocusedChartPosition] = useState(null);
  const canvasRef = useRef(null);

  useParticles(canvasRef, page === "auth");

  // ── Persistent trade tracking (survives Dashboard ↔ Simulator navigation) ──
  const [liveTrades, setLiveTrades] = useState([]);

  useEffect(() => {
    if (!isAuthenticated) return;

    appSocket.connect();

    appSocket.on("order_result", d => {
      let trade = null;
      if (d.status === "filled" && d.position) {
        trade = simulatorOpenTradeEvent(d.position);
      } else if (d.status === "closed") {
        trade = simulatorCloseTradeEvent(d);
      }
      if (!trade) return;
      setLiveTrades(prev => prependUniqueTrade(prev, trade));
      persistTradeFeedEvent(trade);
    });

    appSocket.on("tick", d => {
      for (const event of d?.events?.filled || []) {
        const trade = simulatorOpenTradeEvent(event.position, {
          event_key: `simulator:${event.position?.id || event.order_id}:open`,
        });
        if (trade) {
          setLiveTrades(prev => prependUniqueTrade(prev, trade));
          persistTradeFeedEvent(trade);
        }
      }
      for (const event of d?.events?.tpsl_closed || []) {
        const trade = simulatorCloseTradeEvent(event, {
          event_key: `simulator:${event.position_id || event.trade_id || event.id}:close:${event.reason || "tpsl"}`,
        });
        if (trade) {
          setLiveTrades(prev => prependUniqueTrade(prev, trade));
          persistTradeFeedEvent(trade);
        }
      }
    });

    appSocket.on("new_sim", () => {
      setLiveTrades([]);
    });

    return () => { appSocket.off(); appSocket.disconnect(); };
  }, [isAuthenticated]);

  useEffect(() => {
    localStorage.setItem("synthcrypto_page", page);
  }, [page]);

  useEffect(() => {
    const meta = SEO_BY_PAGE[page];
    if (!meta) return;

    document.title = meta.title;
    let description = document.querySelector('meta[name="description"]');
    if (!description) {
      description = document.createElement("meta");
      description.setAttribute("name", "description");
      document.head.appendChild(description);
    }
    description.setAttribute("content", meta.description);
  }, [page]);

  useEffect(() => {
    const handlePopState = () => {
      const routedPage = pageFromPathname(window.location.pathname) || "home";

      // Auth guard: if user navigates back to a protected page after logout,
      // redirect them to home instead of showing stale content.
      if (isProtectedPage(routedPage) && !isAuthenticated) {
        setPage("home");
        window.history.replaceState({}, "", "/");
        return;
      }

      setPage(routedPage);
      if (window.location.pathname === "/signup" || window.location.pathname === "/register") setTab("register");
      if (window.location.pathname === "/login") setTab("login");
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [isAuthenticated]);

  const navigatePublic = useCallback((nextPage) => {
    setPage(nextPage);
    writePath(PUBLIC_PATHS[nextPage] || "/");
  }, []);

  const openAuth = useCallback((mode = "register") => {
    if (isAuthenticated) {
      setPage("dashboard");
      writePath("/dashboard");
      return;
    }

    setTab(mode);
    setError("");
    setSuccess("");
    setPage("auth");
    writePath(mode === "register" ? "/signup" : "/login");
  }, [isAuthenticated]);

  // On mount: check if already logged in or returning from OAuth
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthError = params.get("error");
    if (oauthError) {
      setTimeout(() => setError(decodeURIComponent(oauthError.replace(/\+/g, " "))), 0);
      window.history.replaceState({}, "", window.location.pathname);
    }

    const oauthSuccess = params.get("auth");
    let isOauth = false;
    if (oauthSuccess === "success") {
      // Token is passed in URL for cross-origin OAuth redirect.
      // Immediately store it and clean the URL.
      const token = params.get("token");
      if (token) localStorage.setItem("synthcrypto_token", token);
      if (params.get("isNew") === "true") {
        localStorage.setItem("isNewRegistration", "true");
      }
      isOauth = true;
      window.history.replaceState({}, "", "/dashboard");
    }

    const token = localStorage.getItem("synthcrypto_token");
    const headers = {};
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    fetch(`${AUTH_SERVER}/api/auth/me`, { headers, credentials: "include" })
      .then(async (r) => {
        if (r.ok) {
          const data = await r.json();
          if (data.user) {
            setIsAuthenticated(true);
            setPage(prev => {
              if (isOauth || prev === "auth") {
                writePath("/dashboard");
                return "dashboard";
              }
              return prev;
            });
          } else {
            setIsAuthenticated(false);
            setPage(prev => {
              if (prev !== "dashboard") return prev;
              writePath("/");
              return "home";
            });
          }
        } else {
          setIsAuthenticated(false);
          setPage(prev => {
            if (prev !== "dashboard") return prev;
            writePath("/");
            return "home";
          });
        }
      })
      .catch(() => setPage(prev => {
        setIsAuthenticated(false);
        if (prev !== "dashboard") return prev;
        writePath("/");
        return "home";
      }));
  }, []);

  const handleSuccess = useCallback((user) => {
    setError("");
    setSuccess(`Welcome, ${user.username || user.email}!`);
    setIsAuthenticated(true);
    setTimeout(() => {
      setPage("dashboard");
      writePath("/dashboard");
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
    window.history.replaceState({}, "", t === "register" ? "/signup" : "/login");
  };

  // ── Render pages ──
  const openDashboard = useCallback(() => {
    setFocusedChartPosition(null);
    setPage("dashboard");
    writePath("/dashboard");
  }, []);

  const openSimulator = useCallback((focus = null) => {
    setFocusedChartPosition(focus);
    setPage("simulator");
    writePath(simulationPath(focus?.asset_symbol || focus?.symbol || "SIM"));
  }, []);

  const openLiveMarket = useCallback((assetClass, symbol, focus = null) => {
    const normalizedAssetClass = assetClass === "stock" || assetClass === "stocks" ? "stock" : "crypto";
    const normalizedSymbol = String(symbol || "").toUpperCase();
    setFocusedChartPosition(focus);
    setPage(`live_market:${normalizedAssetClass}:${normalizedSymbol}`);
    writePath(marketPath(normalizedAssetClass, normalizedSymbol));
  }, []);

  const openPositionChart = useCallback((position) => {
    const source = String(position?.source_market || position?.asset_type || "").toLowerCase();
    const symbol = position?.asset_symbol || position?.symbol || "SIM";
    if (source === "simulator" || symbol === "SIM") {
      openSimulator(position);
      return;
    }
    openLiveMarket(source === "stock" || source === "stocks" ? "stock" : "crypto", symbol, position);
  }, [openLiveMarket, openSimulator]);

  // ── Public pages (no auth required) ──

  if (page === "home") {
    return <LandingPage onNavigate={navigatePublic} isAuthenticated={isAuthenticated} onGoDashboard={openDashboard} />;
  }

  if (page === "about") {
    return <AboutUsPage onNavigate={navigatePublic} onGetStarted={() => openAuth("register")} onSignIn={() => openAuth("login")} isAuthenticated={isAuthenticated} onGoDashboard={openDashboard} />;
  }

  if (page === "learn") {
    return <LearnTradingPage onNavigate={navigatePublic} onGetStarted={() => openAuth("register")} onSignIn={() => openAuth("login")} isAuthenticated={isAuthenticated} onGoDashboard={openDashboard} />;
  }

  // ── Auth guard: redirect unauthenticated users away from protected pages ──
  // This catches direct URL access, browser back button, and stale localStorage.
  if (isProtectedPage(page) && !isAuthenticated) {
    // Use a microtask so we don't setState during render
    Promise.resolve().then(() => {
      setPage("auth");
      writePath("/login");
    });
    // Show nothing while redirecting (prevents flash of protected content)
    return null;
  }

  // ── Protected pages (auth verified above) ──

  if (page === "simulator") {
    return <SimulatorPage onBack={openDashboard} focusPositionId={focusedChartPosition?.id || focusedChartPosition?.trade_id || null} />;
  }

  if (page === "crypto_search") {
    return <SearchPageWrapper assetClass="crypto" onBack={openDashboard} onSelect={(sym) => openLiveMarket("crypto", sym)} />;
  }

  if (page === "stocks_search") {
    return <SearchPageWrapper assetClass="stock" onBack={openDashboard} onSelect={(sym) => openLiveMarket("stock", sym)} />;
  }

  if (page && page.startsWith("live_market:")) {
    const parts = page.split(":");
    const assetClass = parts[1];
    const symbol = parts.slice(2).join(":"); // recombine rest in case symbol has a colon
    return (
      <LiveMarketPage
        assetClass={assetClass}
        symbol={symbol}
        onBack={openDashboard}
        focusPositionId={focusedChartPosition?.id || focusedChartPosition?.trade_id || null}
      />
    );
  }

  if (page === "buy_more") {
    return <BuyMore onBack={() => { setPage("dashboard"); writePath("/dashboard"); }} />;
  }

  if (page === "dashboard") {
    return (
      <Dashboard
        onLogout={() => {
          setIsAuthenticated(false);
          setLiveTrades([]);
          localStorage.removeItem("synthcrypto_page");
          localStorage.removeItem("synthcrypto_token");
          // Replace current history entry so back button can't return here
          window.history.replaceState({}, "", "/");
          setPage("home");
        }}
        onLaunchSimulator={() => openSimulator()}
        onLaunchCrypto={() => setPage("crypto_search")}
        onLaunchStocks={() => setPage("stocks_search")}
        onOpenPosition={openPositionChart}
        liveTrades={liveTrades}
        onResetTrades={() => setLiveTrades([])}
        onBuyMore={() => {
          setPage("buy_more");
          writePath("/buy-s");
        }}
        onGoHome={() => navigatePublic("home")}
      />
    );
  }

  // ── Auth page ──
  return (
    <div className="auth-split-layout">
      <div className="auth-left">
        <DotField
          gradientFrom="#8B5CF6"
          gradientTo="#D8B4FE"
          dotRadius={2}
          dotSpacing={12}
          glowColor="rgba(139, 92, 246, 0.3)"
          sparkle={true}
        />
      </div>
      <div className="auth-right">
        <div className="ds-auth-container">
          <div className="ds-brand">
            <h1>SynthCrypto</h1>
          </div>

          <div className="gooey-tabs">
            <div className={`gooey-indicator ${tab}`} />
            <button
              className={`gooey-btn ${tab === "login" ? "active" : ""}`}
              onClick={() => switchTab("login")}
            >
              Sign In
            </button>
            <button
              className={`gooey-btn ${tab === "register" ? "active" : ""}`}
              onClick={() => switchTab("register")}
            >
              Create Account
            </button>
          </div>

          <GoogleButton />
          <div className="divider"><span>or</span></div>

          {error && <div className="error-msg"><span>⚠</span> {error}</div>}
          {success && <div className="success-msg"><span>✓</span> {success}</div>}

          {tab === "login" ? (
            <LoginForm onSuccess={handleSuccess} onError={handleError} />
          ) : (
            <RegisterForm onSuccess={handleSuccess} onError={handleError} />
          )}
        </div>
      </div>
    </div>
  );
}

// Triggering HMR cache invalidation
