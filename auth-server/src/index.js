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
const { initDB } = require("./db");
const authRoutes = require("./routes/auth");
const portfolioRoutes = require("./routes/portfolio");
const paymentsRoutes = require('./routes/payments');
const { attachTradeFeedWebSocket } = require("./tradeFeed");

const app = express();
const server = http.createServer(app);
const PORT = process.env.PORT || 3001;

// ─── Middleware ──────────────────────────────────────────────────────────────

app.use(express.json());
app.use(cookieParser());

app.set("trust proxy", 1); // Required for 'secure' cookies behind Railway proxy
app.use(cors({
  origin: true,
  credentials: true,
}));


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
    console.log("═".repeat(60));
  });
}

start();
