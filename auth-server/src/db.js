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
    ADD COLUMN IF NOT EXISTS balance NUMERIC(18, 2) NOT NULL DEFAULT 10000,
    ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20) UNIQUE,
    ADD COLUMN IF NOT EXISTS referred_by INTEGER REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS otp_code VARCHAR(6),
    ADD COLUMN IF NOT EXISTS otp_expires_at TIMESTAMP;

  CREATE TABLE IF NOT EXISTS pending_registrations (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    username      VARCHAR(100),
    password_hash VARCHAR(255) NOT NULL,
    referral_code VARCHAR(20),
    referred_by   INTEGER,
    otp_code      VARCHAR(6) NOT NULL,
    otp_expires_at TIMESTAMP NOT NULL,
    created_at    TIMESTAMP DEFAULT NOW()
  );

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

  CREATE TABLE IF NOT EXISTS trade_events (
    event_id                BIGSERIAL PRIMARY KEY,
    event_key               VARCHAR(128) NOT NULL,
    trade_id                VARCHAR(128) NOT NULL,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_symbol            VARCHAR(64) NOT NULL,
    asset_type              VARCHAR(16) NOT NULL CHECK (asset_type IN ('crypto', 'stock', 'simulator')),
    buy_or_sell             VARCHAR(8) NOT NULL CHECK (buy_or_sell IN ('buy', 'sell')),
    quantity                NUMERIC(24, 10) NOT NULL DEFAULT 0,
    entry_price             NUMERIC(24, 10) NOT NULL DEFAULT 0,
    exit_price              NUMERIC(24, 10),
    execution_price         NUMERIC(24, 10) NOT NULL DEFAULT 0,
    trade_value             NUMERIC(18, 2) NOT NULL DEFAULT 0,
    profit_loss             NUMERIC(18, 2) NOT NULL DEFAULT 0,
    event_timestamp         TIMESTAMP NOT NULL DEFAULT NOW(),
    source_market           VARCHAR(16) NOT NULL CHECK (source_market IN ('crypto', 'stocks', 'simulator')),
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, event_key)
  );

  CREATE INDEX IF NOT EXISTS idx_trades_user_closed_at
    ON trades (user_id, closed_at DESC);

  CREATE INDEX IF NOT EXISTS idx_paper_trades_user_status
    ON paper_trades (user_id, position_status);

  CREATE INDEX IF NOT EXISTS idx_paper_trades_user_symbol_status
    ON paper_trades (user_id, asset_type, asset_symbol, position_status);

  CREATE INDEX IF NOT EXISTS idx_trade_events_user_timestamp
    ON trade_events (user_id, event_timestamp DESC, event_id DESC);

  -- Migration for advanced orders & shorting
  ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_order_type_check;
  ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_position_status_check;
  ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trigger_price NUMERIC(24, 10);
  ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS limit_price NUMERIC(24, 10);
  ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS side VARCHAR(16) DEFAULT 'long';
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
