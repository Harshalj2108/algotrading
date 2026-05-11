/**
 * routes/portfolio.js — Portfolio API endpoints
 * 
 * GET  /api/portfolio/me    — Get current user's balance and recent trades
 * POST /api/portfolio/trade — Internal endpoint used by Flask to log trades
 */

const express = require("express");
const { pool } = require("../db");
const { requireAuth } = require("../middleware/auth");

const router = express.Router();

// ─── GET /api/portfolio/me ───────────────────────────────────────────────────

router.get("/me", requireAuth, async (req, res) => {
  try {
    const user_id = req.user.id;

    // Get balance
    const userResult = await pool.query("SELECT balance FROM users WHERE id = $1", [user_id]);
    if (userResult.rows.length === 0) {
      return res.status(404).json({ error: "User not found" });
    }
    const balance = parseFloat(userResult.rows[0].balance);

    // Get recent trades (last 50)
    const tradesResult = await pool.query(
      "SELECT * FROM trades WHERE user_id = $1 ORDER BY closed_at DESC LIMIT 50",
      [user_id]
    );

    // Calculate total realized PnL
    const pnlResult = await pool.query(
      "SELECT SUM(pnl) as total_pnl FROM trades WHERE user_id = $1",
      [user_id]
    );
    const total_pnl = parseFloat(pnlResult.rows[0].total_pnl || 0);

    res.json({
      balance,
      total_pnl,
      trades: tradesResult.rows,
    });
  } catch (err) {
    console.error("GET /portfolio/me error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// ─── POST /api/portfolio/trade ───────────────────────────────────────────────
// This is called by the Flask simulator when a trade closes.
// For security in a real app, this should be protected by an internal secret,
// but for this simulator, we'll verify it via the user's token or just accept it if internal.

router.post("/trade", requireAuth, async (req, res) => {
  try {
    const user_id = req.user.id;
    const { symbol, side, size_usd, entry_price, exit_price, pnl } = req.body;

    if (!user_id || !symbol || !side || size_usd === undefined || pnl === undefined) {
      return res.status(400).json({ error: "Missing required fields" });
    }

    // Begin transaction
    const client = await pool.connect();
    try {
      await client.query("BEGIN");

      // Log trade
      const tradeResult = await client.query(
        `INSERT INTO trades (user_id, symbol, side, size_usd, entry_price, exit_price, pnl)
         VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *`,
        [user_id, symbol, side, size_usd, entry_price, exit_price, pnl]
      );

      // Update balance
      await client.query(
        "UPDATE users SET balance = balance + $1 WHERE id = $2",
        [pnl, user_id]
      );

      await client.query("COMMIT");
      res.json({ success: true, trade: tradeResult.rows[0] });
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }
  } catch (err) {
    console.error("POST /portfolio/trade error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// ─── GET /api/portfolio/balance/:userId ──────────────────────────────────────
// Internal endpoint used by Flask simulator to load a user's persisted balance
// on connect.  No auth required — server-to-server only.

router.get("/balance/:userId", async (req, res) => {
  try {
    const userId = parseInt(req.params.userId, 10);
    if (isNaN(userId)) {
      return res.status(400).json({ error: "Invalid user id" });
    }

    const userResult = await pool.query(
      "SELECT balance FROM users WHERE id = $1",
      [userId]
    );
    if (userResult.rows.length === 0) {
      return res.status(404).json({ error: "User not found" });
    }

    const balance = parseFloat(userResult.rows[0].balance);

    // Also fetch total realized PnL
    const pnlResult = await pool.query(
      "SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE user_id = $1",
      [userId]
    );
    const total_pnl = parseFloat(pnlResult.rows[0].total_pnl);

    res.json({ balance, total_pnl });
  } catch (err) {
    console.error("GET /portfolio/balance error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// ─── POST /api/portfolio/reset ───────────────────────────────────────────────
// Resets the user's portfolio: deletes all trades and sets balance to 10000.

router.post("/reset", requireAuth, async (req, res) => {
  try {
    const user_id = req.user.id;
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("DELETE FROM trades WHERE user_id = $1", [user_id]);
      await client.query("UPDATE users SET balance = 10000 WHERE id = $1", [user_id]);
      await client.query("COMMIT");
      res.json({ success: true, balance: 10000, message: "Portfolio reset to initial state" });
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }
  } catch (err) {
    console.error("POST /portfolio/reset error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

module.exports = router;
