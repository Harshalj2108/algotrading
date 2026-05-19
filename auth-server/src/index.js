/**
 * index.js — SynthCrypto Auth Server
 * 
 * Express server providing:
 *   • Email/password registration & login
 *   • Raw Google OAuth 2.0 (no Passport)
 *   • JWT-based sessions via httpOnly cookies
 *   • Token verification endpoint for Flask
 */

require("dotenv").config();

const express = require("express");
const http = require("http");
const cors = require("cors");
const cookieParser = require("cookie-parser");
const rateLimit = require("express-rate-limit");
const { initDB } = require("./db");
const authRoutes = require("./routes/auth");
const portfolioRoutes = require("./routes/portfolio");
const paymentsRoutes = require('./routes/payments');
const { attachTradeFeedWebSocket } = require("./tradeFeed");

const app = express();
const server = http.createServer(app);
const PORT = process.env.PORT || 3001;

// ─── CORS Origin Allowlist ───────────────────────────────────────────────────

function parseAllowedOrigins() {
  const clientUrl = process.env.CLIENT_URL || "";
  const simulatorUrl = process.env.SIMULATOR_URL || "";
  const extraOrigins = process.env.EXTRA_CORS_ORIGINS || "";

  const origins = new Set();
  [clientUrl, simulatorUrl, ...extraOrigins.split(",")]
    .map(u => u.trim().replace(/\/+$/, ""))
    .filter(Boolean)
    .forEach(u => origins.add(u));

  // Always allow localhost in development
  if (!process.env.NODE_ENV || process.env.NODE_ENV === "development") {
    origins.add("http://localhost:5173");
    origins.add("http://localhost:3000");
    origins.add("http://localhost:8000");
  }

  return [...origins];
}

const ALLOWED_ORIGINS = parseAllowedOrigins();

// ─── Security Headers Middleware ─────────────────────────────────────────────

function securityHeaders(req, res, next) {
  // Prevent clickjacking
  res.setHeader("X-Frame-Options", "DENY");
  // Prevent MIME-sniffing
  res.setHeader("X-Content-Type-Options", "nosniff");
  // Modern recommendation: disable legacy XSS filter (let CSP handle it)
  res.setHeader("X-XSS-Protection", "0");
  // HSTS - enforce HTTPS (1 year)
  res.setHeader("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
  // Content Security Policy - prevent framing and inline script injection
  res.setHeader("Content-Security-Policy", "frame-ancestors 'none'; default-src 'self'");
  // Referrer policy - don't leak full URL to third parties
  res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
  // Restrict browser features
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  // Remove server fingerprint
  res.removeHeader("X-Powered-By");
  next();
}

// ─── Prototype Pollution Protection ──────────────────────────────────────────

function sanitizeBody(obj) {
  if (obj === null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map(sanitizeBody);
  const clean = {};
  for (const key of Object.keys(obj)) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") continue;
    clean[key] = sanitizeBody(obj[key]);
  }
  return clean;
}

function prototypePollutionGuard(req, res, next) {
  if (req.body && typeof req.body === "object") {
    req.body = sanitizeBody(req.body);
  }
  next();
}

// ─── Middleware ──────────────────────────────────────────────────────────────

app.use(securityHeaders);
app.use(express.json({ limit: "1mb" }));
app.use(cookieParser());
app.use(prototypePollutionGuard);

app.set("trust proxy", 1); // Required for 'secure' cookies behind Railway proxy
app.use(cors({
  origin: function (origin, callback) {
    // Allow requests with no origin (mobile apps, curl, server-to-server)
    if (!origin) return callback(null, true);
    if (ALLOWED_ORIGINS.includes(origin)) {
      return callback(null, true);
    }
    return callback(new Error("Not allowed by CORS"));
  },
  credentials: true,
}));

// ─── Global Rate Limiting ────────────────────────────────────────────────────
// Broad rate limit: 200 requests per minute per IP across all endpoints
const globalLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 200,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests, please try again later" },
});
app.use(globalLimiter);


// ─── Routes ──────────────────────────────────────────────────────────────────

app.use("/api/auth", authRoutes);
app.use("/api/portfolio", portfolioRoutes);
app.use("/api/payments", paymentsRoutes);

// Health check
app.get("/api/health", (req, res) => {
  res.json({ status: "ok", service: "synthcrypto-auth" });
});

// ─── Start ───────────────────────────────────────────────────────────────────

async function start() {
  await initDB();
  attachTradeFeedWebSocket(server);

  server.listen(PORT, () => {
    console.log("═".repeat(60));
    console.log("  SynthCrypto Auth Server");
    console.log(`  API:     http://localhost:${PORT}/api/auth`);
    console.log(`  Health:  http://localhost:${PORT}/api/health`);
    console.log("  Routes:");
    console.log("    POST /api/auth/register");
    console.log("    POST /api/auth/login");
    console.log("    GET  /api/auth/google");
    console.log("    GET  /api/auth/google/callback");
    console.log("    POST /api/auth/logout");
    console.log("    GET  /api/auth/me");
    console.log("    GET  /api/auth/verify");
    console.log(`  CORS origins: ${ALLOWED_ORIGINS.join(", ") || "(development: all localhost)"}`);
    console.log("═".repeat(60));
  });
}

start();
