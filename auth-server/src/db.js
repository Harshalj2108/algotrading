/**
 * db.js - PostgreSQL connection pool + auto-migration
 *
 * Creates the auth and paper-trading tables on startup if they do not exist.
 */

const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// Auto-create and migrate the local simulator schema.
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

  ALTER TABLE users
    ADD COLUMN IF NOT EXISTS balance NUMERIC(18, 2) NOT NULL DEFAULT 10000;

  CREATE TABLE IF NOT EXISTS trades (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol      VARCHAR(64) NOT NULL,
    side        VARCHAR(16) NOT NULL,
    size_usd    NUMERIC(18, 2) NOT NULL,
    entry_price NUMERIC(24, 10),
    exit_price  NUMERIC(24, 10),
    pnl         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    closed_at   TIMESTAMP DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS paper_wallets (
    user_id                 INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    virtual_balance         NUMERIC(18, 2) NOT NULL DEFAULT 10000,
    total_portfolio_value   NUMERIC(18, 2) NOT NULL DEFAULT 10000,
    total_profit_loss       NUMERIC(18, 2) NOT NULL DEFAULT 0,
    updated_at              TIMESTAMP DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id                UUID PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_symbol            VARCHAR(64) NOT NULL,
    asset_type              VARCHAR(16) NOT NULL CHECK (asset_type IN ('crypto', 'stock')),
    order_type              VARCHAR(16) NOT NULL CHECK (order_type IN ('buy', 'sell')),
    quantity                NUMERIC(24, 10) NOT NULL,
    entry_price             NUMERIC(24, 10) NOT NULL,
    exit_price              NUMERIC(24, 10),
    position_status         VARCHAR(16) NOT NULL CHECK (position_status IN ('open', 'closed')),
    invested_amount         NUMERIC(18, 2) NOT NULL,
    current_value           NUMERIC(18, 2) NOT NULL,
    profit_loss             NUMERIC(18, 2) NOT NULL DEFAULT 0,
    profit_loss_percentage  NUMERIC(12, 4) NOT NULL DEFAULT 0,
    stop_loss               NUMERIC(24, 10),
    take_profit             NUMERIC(24, 10),
    close_reason            VARCHAR(32),
    created_at              TIMESTAMP DEFAULT NOW(),
    closed_at               TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT NOW()
  );

  CREATE INDEX IF NOT EXISTS idx_trades_user_closed_at
    ON trades (user_id, closed_at DESC);

  CREATE INDEX IF NOT EXISTS idx_paper_trades_user_status
    ON paper_trades (user_id, position_status);

  CREATE INDEX IF NOT EXISTS idx_paper_trades_user_symbol_status
    ON paper_trades (user_id, asset_type, asset_symbol, position_status);
`;

async function initDB() {
  try {
    await pool.query(INIT_SQL);
    console.log("  Database connected - auth and paper-trading tables ready");
  } catch (err) {
    console.error("  Database connection failed:", err.message);
    console.error("    Make sure PostgreSQL is running and DATABASE_URL is correct in .env");
    process.exit(1);
  }
}

module.exports = { pool, initDB };
