/**
 * db.js — PostgreSQL connection pool + auto-migration
 * 
 * Creates the users table on startup if it doesn't exist.
 */

const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// ── Auto-create users table ─────────────────────────────────────────────────

const INIT_SQL = `
  CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    username      VARCHAR(100),
    password_hash VARCHAR(255),
    google_id     VARCHAR(255),
    avatar_url    TEXT,
    created_at    TIMESTAMP DEFAULT NOW(),
    last_login    TIMESTAMP DEFAULT NOW()
  );
`;

async function initDB() {
  try {
    await pool.query(INIT_SQL);
    console.log("  ✓ Database connected — users table ready");
  } catch (err) {
    console.error("  ✗ Database connection failed:", err.message);
    console.error("    Make sure PostgreSQL is running and DATABASE_URL is correct in .env");
    process.exit(1);
  }
}

module.exports = { pool, initDB };
