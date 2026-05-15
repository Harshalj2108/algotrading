/**
 * routes/auth.js — Authentication endpoints
 * 
 * POST   /api/auth/register          — Create account (email + password)
 * POST   /api/auth/login             — Login with email + password
 * GET    /api/auth/google            — Redirect to Google OAuth consent
 * GET    /api/auth/google/callback   — Google OAuth callback (exchange code)
 * POST   /api/auth/logout            — Clear JWT cookie
 * GET    /api/auth/me                — Get current user from JWT
 * GET    /api/auth/verify            — Verify a JWT token (for Flask)
 */

const express = require("express");
const bcrypt = require("bcryptjs");
const axios = require("axios");
const crypto = require("crypto");
const { pool } = require("../db");
const { signToken, verifyToken, requireAuth } = require("../middleware/auth");

const router = express.Router();

// ─── env vars ────────────────────────────────────────────────────────────────

const {
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
  GOOGLE_REDIRECT_URI,
  CLIENT_URL,
  SIMULATOR_URL,
} = process.env;

// Cookie options — httpOnly, SameSite Lax for cross-port compat
const COOKIE_OPTS = {
  httpOnly: true,
  secure: false,           // set to true in production (HTTPS)
  sameSite: "lax",
  maxAge: 7 * 24 * 60 * 60 * 1000,   // 7 days
  path: "/",
};


// ─── POST /api/auth/register ─────────────────────────────────────────────────

router.post("/register", async (req, res) => {
  try {
    const { email, username, password, referralCode } = req.body;

    if (!email || !password) {
      return res.status(400).json({ error: "Email and password are required" });
    }
    if (password.length < 6) {
      return res.status(400).json({ error: "Password must be at least 6 characters" });
    }

    // Check if user exists
    const existing = await pool.query("SELECT id FROM users WHERE email = $1", [email.toLowerCase()]);
    if (existing.rows.length > 0) {
      return res.status(409).json({ error: "An account with this email already exists" });
    }

    let referrerId = null;
    let cleanRefCode = referralCode;
    if (referralCode && referralCode.includes("ref=")) {
      try {
        const parsedUrl = new URL(referralCode);
        cleanRefCode = parsedUrl.searchParams.get("ref") || referralCode;
      } catch {
        cleanRefCode = referralCode.split("ref=")[1] || referralCode;
      }
    }

    if (cleanRefCode) cleanRefCode = cleanRefCode.trim();

    if (cleanRefCode) {
      const referrer = await pool.query("SELECT id FROM users WHERE referral_code = $1", [cleanRefCode]);
      if (referrer.rows.length > 0) {
        referrerId = referrer.rows[0].id;
      }
    }

    const myReferralCode = Math.random().toString(36).substring(2, 10).toUpperCase();

    // Hash password
    const salt = await bcrypt.genSalt(12);
    const password_hash = await bcrypt.hash(password, salt);

    // Insert user
    const result = await pool.query(
      `INSERT INTO users (email, username, password_hash, referral_code, referred_by)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING id, email, username, avatar_url, created_at`,
      [email.toLowerCase(), username || email.split("@")[0], password_hash, myReferralCode, referrerId]
    );

    const user = result.rows[0];

    if (referrerId) {
      await pool.query("UPDATE users SET balance = balance + 1000 WHERE id = $1", [referrerId]);
    }

    const token = signToken(user);

    res.cookie("token", token, COOKIE_OPTS);
    res.status(201).json({
      message: "Account created successfully",
      user: { id: user.id, email: user.email, username: user.username, avatar_url: user.avatar_url },
    });
  } catch (err) {
    console.error("Register error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});


// ─── POST /api/auth/login ────────────────────────────────────────────────────

router.post("/login", async (req, res) => {
  try {
    const { email, password } = req.body;

    if (!email || !password) {
      return res.status(400).json({ error: "Email and password are required" });
    }

    // Find user
    const result = await pool.query(
      "SELECT id, email, username, password_hash, avatar_url FROM users WHERE email = $1",
      [email.toLowerCase()]
    );
    if (result.rows.length === 0) {
      return res.status(401).json({ error: "Invalid email or password" });
    }

    const user = result.rows[0];

    // If user registered via Google only (no password)
    if (!user.password_hash) {
      return res.status(401).json({ error: "This account uses Google sign-in. Please use the Google button." });
    }

    // Verify password
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      return res.status(401).json({ error: "Invalid email or password" });
    }

    // Update last_login
    await pool.query("UPDATE users SET last_login = NOW() WHERE id = $1", [user.id]);

    const token = signToken(user);
    res.cookie("token", token, COOKIE_OPTS);
    res.json({
      message: "Login successful",
      user: { id: user.id, email: user.email, username: user.username, avatar_url: user.avatar_url },
    });
  } catch (err) {
    console.error("Login error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});


// ─── GET /api/auth/google — Redirect to Google consent screen ────────────────

router.get("/google", (req, res) => {
  if (!GOOGLE_CLIENT_ID) {
    return res.status(500).json({ error: "Google OAuth not configured. Set GOOGLE_CLIENT_ID in .env" });
  }

  // Generate CSRF state token
  let state = crypto.randomBytes(16).toString("hex");
  if (req.query.ref) {
    state += `_REF_${req.query.ref}`;
  }

  // Store state in a short-lived cookie for verification
  res.cookie("oauth_state", state, {
    httpOnly: true,
    secure: false,
    sameSite: "lax",
    maxAge: 5 * 60 * 1000,   // 5 min
  });

  const params = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    redirect_uri: GOOGLE_REDIRECT_URI,
    response_type: "code",
    scope: [
      "https://www.googleapis.com/auth/userinfo.email",
      "https://www.googleapis.com/auth/userinfo.profile",
    ].join(" "),
    access_type: "offline",
    prompt: "consent",
    state,
  });

  res.redirect(`https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`);
});


// ─── GET /api/auth/google/callback — Exchange code for tokens ────────────────

router.get("/google/callback", async (req, res) => {
  try {
    const { code, state } = req.query;
    const savedState = req.cookies?.oauth_state;

    // Verify CSRF state
    if (!state || state !== savedState) {
      return res.redirect(`${CLIENT_URL}?error=Invalid+OAuth+state`);
    }
    // Clear state cookie
    res.clearCookie("oauth_state");

    let referralCode = null;
    if (state.includes("_REF_")) {
      referralCode = state.split("_REF_")[1];
    }

    if (!code) {
      return res.redirect(`${CLIENT_URL}?error=No+authorization+code`);
    }

    // Exchange code for tokens
    const tokenResponse = await axios.post("https://oauth2.googleapis.com/token", {
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      code,
      grant_type: "authorization_code",
      redirect_uri: GOOGLE_REDIRECT_URI,
    });

    const { access_token } = tokenResponse.data;

    // Fetch user profile from Google
    const profileResponse = await axios.get(
      "https://www.googleapis.com/oauth2/v2/userinfo",
      { headers: { Authorization: `Bearer ${access_token}` } }
    );

    const profile = profileResponse.data;
    const { id: google_id, email, name, picture } = profile;

    // Upsert user — find by google_id or email, create if not found
    let user;
    let isNewGoogleUser = false;
    const existingByGoogle = await pool.query(
      "SELECT id, email, username, avatar_url FROM users WHERE google_id = $1",
      [google_id]
    );

    if (existingByGoogle.rows.length > 0) {
      // Existing Google user — update last_login & avatar
      user = existingByGoogle.rows[0];
      await pool.query(
        "UPDATE users SET last_login = NOW(), avatar_url = $1 WHERE id = $2",
        [picture, user.id]
      );
      user.avatar_url = picture;
    } else {
      // Check if email exists (registered with password)
      const existingByEmail = await pool.query(
        "SELECT id, email, username, avatar_url FROM users WHERE email = $1",
        [email.toLowerCase()]
      );

      if (existingByEmail.rows.length > 0) {
        // Link Google ID to existing account
        user = existingByEmail.rows[0];
        await pool.query(
          "UPDATE users SET google_id = $1, avatar_url = $2, last_login = NOW() WHERE id = $3",
          [google_id, picture, user.id]
        );
        user.avatar_url = picture;
      } else {
        // Create new user
        let referrerId = null;
        let cleanRefCode = referralCode;
        if (referralCode && referralCode.includes("ref=")) {
          try {
            const parsedUrl = new URL(referralCode);
            cleanRefCode = parsedUrl.searchParams.get("ref") || referralCode;
          } catch {
            cleanRefCode = referralCode.split("ref=")[1] || referralCode;
          }
        }
        
        if (cleanRefCode) cleanRefCode = cleanRefCode.trim();
        
        console.log("Processing new Google user. cleanRefCode extracted:", cleanRefCode);
        if (cleanRefCode) {
          const referrer = await pool.query("SELECT id FROM users WHERE referral_code = $1", [cleanRefCode]);
          console.log("Referrer query result:", referrer.rows);
          if (referrer.rows.length > 0) {
            referrerId = referrer.rows[0].id;
          }
        }

        console.log("Final referrerId for new user:", referrerId);
        const myReferralCode = Math.random().toString(36).substring(2, 10).toUpperCase();
        const result = await pool.query(
          `INSERT INTO users (email, username, google_id, avatar_url, referral_code, referred_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING id, email, username, avatar_url`,
          [email.toLowerCase(), name || email.split("@")[0], google_id, picture, myReferralCode, referrerId]
        );
        user = result.rows[0];
        
        if (referrerId) {
          await pool.query("UPDATE users SET balance = balance + 1000 WHERE id = $1", [referrerId]);
          console.log("Updated referrer balance for ID:", referrerId);
        }
        
        isNewGoogleUser = true;
        isNewGoogleUser = true;
      }
    }

    // Issue JWT & redirect to simulator
    const token = signToken(user);
    res.cookie("token", token, COOKIE_OPTS);
    if (isNewGoogleUser) {
      res.redirect(`${CLIENT_URL}?auth=success&isNew=true`);
    } else {
      res.redirect(`${CLIENT_URL}?auth=success`);
    }
  } catch (err) {
    console.error("Google OAuth callback error:", err.response?.data || err.message);
    res.redirect(`${CLIENT_URL}?error=Google+authentication+failed`);
  }
});


// ─── POST /api/auth/logout ───────────────────────────────────────────────────

router.post("/logout", (req, res) => {
  res.clearCookie("token", { path: "/" });
  res.json({ message: "Logged out successfully" });
});


// ─── GET /api/auth/me — Return current authenticated user ────────────────────

router.get("/me", async (req, res) => {
  try {
    const token = req.cookies?.token;
    if (!token) return res.status(200).json({ user: null });
    
    const payload = verifyToken(token);
    if (!payload) return res.status(200).json({ user: null });

    const result = await pool.query("SELECT id, email, username, avatar_url, referral_code FROM users WHERE id = $1", [payload.id]);
    if (result.rows.length === 0) return res.status(200).json({ user: null });
    const user = result.rows[0];

    // Auto-generate referral code for existing users who don't have one
    if (!user.referral_code) {
      const newRefCode = Math.random().toString(36).substring(2, 10).toUpperCase();
      await pool.query("UPDATE users SET referral_code = $1 WHERE id = $2", [newRefCode, payload.id]);
      user.referral_code = newRefCode;
    }

    const refCount = await pool.query("SELECT COUNT(*) FROM users WHERE referred_by = $1", [payload.id]);
    user.referral_count = parseInt(refCount.rows[0].count, 10);
    res.json({ user });
  } catch (err) {
    console.error("Get /me error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});


// ─── GET /api/auth/verify — Verify token (used by Flask) ─────────────────────

router.get("/verify", (req, res) => {
  const token = req.cookies?.token || req.query?.token || req.headers["x-auth-token"];
  if (!token) {
    return res.status(401).json({ valid: false, error: "No token" });
  }
  const payload = verifyToken(token);
  if (!payload) {
    return res.status(401).json({ valid: false, error: "Invalid token" });
  }
  res.json({ valid: true, user: payload });
});


module.exports = router;
