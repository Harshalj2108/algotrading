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
const cors = require("cors");
const cookieParser = require("cookie-parser");
const { initDB } = require("./db");
const authRoutes = require("./routes/auth");
const portfolioRoutes = require("./routes/portfolio");

const app = express();
const PORT = process.env.PORT || 3001;

// ─── Middleware ──────────────────────────────────────────────────────────────

app.use(express.json());
app.use(cookieParser());

// CORS — allow React dev server and Flask simulator
app.use(cors({
  origin: [
    process.env.CLIENT_URL || "http://localhost:5173",
    process.env.SIMULATOR_URL || "http://localhost:8000",
  ],
  credentials: true,
}));

// ─── Routes ──────────────────────────────────────────────────────────────────

app.use("/api/auth", authRoutes);
app.use("/api/portfolio", portfolioRoutes);

// Health check
app.get("/api/health", (req, res) => {
  res.json({ status: "ok", service: "synthcrypto-auth" });
});

// ─── Start ───────────────────────────────────────────────────────────────────

async function start() {
  await initDB();

  app.listen(PORT, () => {
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
