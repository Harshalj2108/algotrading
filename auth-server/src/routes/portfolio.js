/**
 * routes/portfolio.js - Portfolio and live paper-trading API endpoints.
 */

const crypto = require("crypto");
const express = require("express");
const axios = require("axios");
const { pool } = require("../db");
const { requireAuth } = require("../middleware/auth");
const {
  loadTradeFeed,
  recordAndBroadcastTradeEvent,
} = require("../tradeFeed");

const router = express.Router();

const INITIAL_VIRTUAL_BALANCE = 10000;
const SIMULATOR_URL = process.env.SIMULATOR_URL;

function httpError(status, message) {
  const err = new Error(message);
  err.status = status;
  return err;
}

function getUserId(req) {
  const userId = Number(req.user?.id);
  if (!Number.isInteger(userId) || userId <= 0) {
    throw httpError(401, "Invalid authenticated user");
  }
  return userId;
}

function asNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function parsePositiveNumber(value, label) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) {
    throw httpError(400, `${label} must be greater than zero`);
  }
  return n;
}

function parseOptionalPositiveNumber(value, label) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) {
    throw httpError(400, `${label} must be greater than zero`);
  }
  return n;
}

function normalizeAssetType(value) {
  const type = String(value || "").trim().toLowerCase();
  if (type === "crypto") return "crypto";
  if (type === "stock" || type === "stocks") return "stock";
  throw httpError(400, "asset_type must be crypto or stock");
}

function normalizeSymbol(value) {
  const symbol = String(value || "").trim().toUpperCase();
  if (!/^[A-Z0-9./:_-]{1,32}$/.test(symbol)) {
    throw httpError(400, "Invalid asset symbol");
  }
  return symbol;
}

function tradeKey(assetType, symbol) {
  return `${assetType}:${symbol}`;
}

function validateLongTpsl(entryPrice, stopLoss, takeProfit) {
  if (stopLoss != null && stopLoss >= entryPrice) {
    throw httpError(400, "Stop loss must be below entry price");
  }
  if (takeProfit != null && takeProfit <= entryPrice) {
    throw httpError(400, "Take profit must be above entry price");
  }
}

async function fetchLivePrice(assetType, symbol, fallbackPrice) {
  try {
    const response = await axios.get(`${SIMULATOR_URL}/api/live/ticker`, {
      params: { type: assetType, symbol },
      timeout: 3500,
    });
    const price = Number(response.data?.price);
    if (Number.isFinite(price) && price > 0) {
      return {
        price,
        ticker: response.data,
        source: "live",
      };
    }
  } catch (err) {
    console.warn(`Live price fetch failed for ${assetType}/${symbol}: ${err.message}`);
  }

  const fallback = Number(fallbackPrice);
  if (Number.isFinite(fallback) && fallback > 0) {
    return {
      price: fallback,
      ticker: { symbol, price: fallback, time: Math.floor(Date.now() / 1000) },
      source: "fallback",
    };
  }

  throw httpError(503, "Live price is unavailable for this asset");
}

async function getWallet(db, userId, { lock = false } = {}) {
  await db.query(
    `INSERT INTO paper_wallets (user_id, virtual_balance, total_portfolio_value, total_profit_loss)
     VALUES ($1, $2, $2, 0)
     ON CONFLICT (user_id) DO NOTHING`,
    [userId, INITIAL_VIRTUAL_BALANCE]
  );

  const result = await db.query(
    `SELECT user_id, virtual_balance, total_portfolio_value, total_profit_loss
     FROM paper_wallets
     WHERE user_id = $1
     ${lock ? "FOR UPDATE" : ""}`,
    [userId]
  );
  return normalizeWallet(result.rows[0]);
}

function normalizeWallet(row) {
  return {
    user_id: Number(row.user_id),
    virtual_balance: asNumber(row.virtual_balance),
    total_portfolio_value: asNumber(row.total_portfolio_value),
    total_profit_loss: asNumber(row.total_profit_loss),
  };
}

function calculateTrade(row, marketPrice) {
  const quantity = asNumber(row.quantity);
  const entryPrice = asNumber(row.entry_price);
  const investedAmount = asNumber(row.invested_amount);
  const price = Number.isFinite(Number(marketPrice)) && Number(marketPrice) > 0
    ? Number(marketPrice)
    : asNumber(row.exit_price, entryPrice);
  const currentValue = quantity * price;
  const isShort = row.side === 'short';
  const profitLoss = isShort ? (entryPrice - price) * quantity : (price - entryPrice) * quantity;
  const profitLossPercentage = entryPrice > 0 ? (profitLoss / investedAmount) * 100 : 0;

  return {
    currentValue,
    profitLoss,
    profitLossPercentage,
  };
}

function fallbackPriceForTrade(row) {
  const quantity = asNumber(row.quantity);
  const currentValue = asNumber(row.current_value);
  if (quantity > 0 && currentValue > 0) return currentValue / quantity;
  return asNumber(row.entry_price);
}

function normalizeTrade(row, marketPrice = null) {
  const entryPrice = asNumber(row.entry_price);
  const quantity = asNumber(row.quantity);
  const investedAmount = asNumber(row.invested_amount);
  const open = row.position_status === "open";
  const computed = open
    ? calculateTrade(row, marketPrice)
    : {
        currentValue: asNumber(row.current_value),
        profitLoss: asNumber(row.profit_loss),
        profitLossPercentage: asNumber(row.profit_loss_percentage),
      };

  const exitPrice = row.exit_price == null ? null : asNumber(row.exit_price);
  const stopLoss = row.stop_loss == null ? null : asNumber(row.stop_loss);
  const takeProfit = row.take_profit == null ? null : asNumber(row.take_profit);

  return {
    trade_id: row.trade_id,
    id: row.trade_id,
    user_id: Number(row.user_id),
    asset_symbol: row.asset_symbol,
    symbol: row.asset_symbol,
    asset_type: row.asset_type,
    order_type: row.order_type,
    side: row.side || "long",
    leverage: 1,
    quantity,
    qty: quantity,
    size_usd: investedAmount,
    margin: investedAmount,
    entry_price: entryPrice,
    exit_price: exitPrice,
    liq_price: null,
    position_status: row.position_status,
    status: row.position_status,
    invested_amount: investedAmount,
    current_value: computed.currentValue,
    profit_loss: computed.profitLoss,
    pnl: computed.profitLoss,
    upnl: open ? computed.profitLoss : 0,
    profit_loss_percentage: computed.profitLossPercentage,
    upnl_pct: computed.profitLossPercentage,
    stop_loss: stopLoss,
    take_profit: takeProfit,
    sl_price: stopLoss,
    tp_price: takeProfit,
    trigger_price: row.trigger_price == null ? null : asNumber(row.trigger_price),
    limit_price: row.limit_price == null ? null : asNumber(row.limit_price),
    close_reason: row.close_reason || null,
    created_at: row.created_at,
    closed_at: row.closed_at,
  };
}

function tradeEventFromPaperTrade(trade, eventType, overrides = {}) {
  const isClosing = eventType === "sell" || eventType === "close";
  const executionPrice = isClosing
    ? asNumber(trade.exit_price, asNumber(trade.entry_price))
    : asNumber(trade.entry_price);
  const quantity = asNumber(trade.quantity ?? trade.qty);
  const tradeValue = isClosing
    ? quantity * executionPrice
    : asNumber(trade.invested_amount ?? trade.size_usd);
  const sourceMarket = trade.asset_type === "stock" ? "stocks" : "crypto";
  const tradeSide = trade.side || "long";
  const buyOrSell = isClosing ? (tradeSide === "short" ? "buy" : "sell") : (tradeSide === "short" ? "sell" : "buy");

  return {
    event_key: overrides.event_key || `${sourceMarket}:${trade.trade_id || trade.id}:${eventType}`,
    trade_id: trade.trade_id || trade.id,
    asset_symbol: trade.asset_symbol || trade.symbol,
    asset_type: trade.asset_type,
    buy_or_sell: buyOrSell,
    quantity,
    entry_price: executionPrice,
    exit_price: isClosing ? executionPrice : null,
    execution_price: executionPrice,
    trade_value: tradeValue,
    profit_loss: isClosing ? asNumber(trade.profit_loss ?? trade.pnl) : 0,
    timestamp: overrides.timestamp || (isClosing ? trade.closed_at : trade.created_at) || new Date().toISOString(),
    source_market: sourceMarket,
  };
}

async function updateStoredMark(db, userId, tradeId, marketPrice) {
  const rowResult = await db.query(
    `SELECT * FROM paper_trades
     WHERE user_id = $1 AND trade_id = $2 AND position_status = 'open'`,
    [userId, tradeId]
  );
  if (!rowResult.rows.length) return null;

  const row = rowResult.rows[0];
  const computed = calculateTrade(row, marketPrice);
  await db.query(
    `UPDATE paper_trades
     SET current_value = $1,
         profit_loss = $2,
         profit_loss_percentage = $3,
         updated_at = NOW()
     WHERE trade_id = $4`,
    [computed.currentValue, computed.profitLoss, computed.profitLossPercentage, tradeId]
  );
  return normalizeTrade(row, marketPrice);
}

async function closeTrade(client, userId, row, executionPrice, reason = "manual", closeQuantity = null) {
  const originalQuantity = asNumber(row.quantity);
  const qtyToClose = closeQuantity != null && closeQuantity > 0 && closeQuantity < originalQuantity
    ? asNumber(closeQuantity)
    : originalQuantity;
    
  const isPartial = qtyToClose < originalQuantity;

  if (isPartial) {
    // Partial close: We compute the values for the closed portion.
    const proportion = qtyToClose / originalQuantity;
    const closedInvestedAmount = asNumber(row.invested_amount) * proportion;
    const remainingInvestedAmount = asNumber(row.invested_amount) - closedInvestedAmount;
    const remainingQuantity = originalQuantity - qtyToClose;

    // Create a temporary row-like object for the closed portion to calculate PnL
    const tempRowForClosed = { ...row, quantity: qtyToClose, invested_amount: closedInvestedAmount };
    const computedClosed = calculateTrade(tempRowForClosed, executionPrice);

    // Insert a new closed trade record for the closed portion
    const newTradeId = crypto.randomUUID();
    await client.query(
      `INSERT INTO paper_trades (
         trade_id, user_id, asset_symbol, asset_type, order_type, side, quantity,
         entry_price, exit_price, position_status, invested_amount, current_value,
         profit_loss, profit_loss_percentage, close_reason, created_at, closed_at, updated_at
       ) VALUES ($1, $2, $3, $4, 'sell', $5, $6, $7, $8, 'closed', $9, $10, $11, $12, $13, $14, NOW(), NOW())`,
      [
        newTradeId, userId, row.asset_symbol, row.asset_type, row.side || 'long', qtyToClose,
        row.entry_price, executionPrice, closedInvestedAmount, computedClosed.currentValue,
        computedClosed.profitLoss, computedClosed.profitLossPercentage, reason, row.created_at
      ]
    );

    // Update the existing open position with the remaining quantity
    const tempRowForOpen = { ...row, quantity: remainingQuantity, invested_amount: remainingInvestedAmount };
    const computedOpen = calculateTrade(tempRowForOpen, executionPrice);
    await client.query(
      `UPDATE paper_trades
       SET quantity = $1,
           invested_amount = $2,
           current_value = $3,
           profit_loss = $4,
           profit_loss_percentage = $5,
           updated_at = NOW()
       WHERE trade_id = $6`,
      [remainingQuantity, remainingInvestedAmount, computedOpen.currentValue, computedOpen.profitLoss, computedOpen.profitLossPercentage, row.trade_id]
    );

    await client.query(
      `UPDATE paper_wallets
       SET virtual_balance = virtual_balance + $1,
           updated_at = NOW()
       WHERE user_id = $2`,
      [computedClosed.currentValue, userId]
    );

    return normalizeTrade(
      {
        ...row,
        trade_id: newTradeId,
        order_type: "sell",
        quantity: qtyToClose,
        invested_amount: closedInvestedAmount,
        exit_price: executionPrice,
        position_status: "closed",
        current_value: computedClosed.currentValue,
        profit_loss: computedClosed.profitLoss,
        profit_loss_percentage: computedClosed.profitLossPercentage,
        close_reason: reason,
        closed_at: new Date().toISOString(),
      },
      executionPrice
    );
  } else {
    // Full close
    const computed = calculateTrade(row, executionPrice);
    await client.query(
      `UPDATE paper_trades
       SET order_type = 'sell',
           exit_price = $1,
           position_status = 'closed',
           current_value = $2,
           profit_loss = $3,
           profit_loss_percentage = $4,
           close_reason = $5,
           closed_at = NOW(),
           updated_at = NOW()
       WHERE user_id = $6 AND trade_id = $7 AND position_status IN ('open', 'pending')`,
      [
        executionPrice,
        computed.currentValue,
        computed.profitLoss,
        computed.profitLossPercentage,
        reason,
        userId,
        row.trade_id,
      ]
    );

    if (row.position_status === 'open') {
      await client.query(
        `UPDATE paper_wallets
         SET virtual_balance = virtual_balance + $1,
             updated_at = NOW()
         WHERE user_id = $2`,
        [computed.currentValue, userId]
      );
    } else {
      // If cancelling a pending order, return the invested amount directly
      await client.query(
        `UPDATE paper_wallets
         SET virtual_balance = virtual_balance + $1,
             updated_at = NOW()
         WHERE user_id = $2`,
        [row.invested_amount, userId]
      );
    }

    return normalizeTrade(
      {
        ...row,
        order_type: "sell",
        exit_price: executionPrice,
        position_status: "closed",
        current_value: computed.currentValue,
        profit_loss: computed.profitLoss,
        profit_loss_percentage: computed.profitLossPercentage,
        close_reason: reason,
        closed_at: new Date().toISOString(),
      },
      executionPrice
    );
  }
}

async function loadPaperPortfolio(userId, { fetchLive = true, priceOverrides = new Map() } = {}) {
  const wallet = await getWallet(pool, userId);
  const openResult = await pool.query(
    `SELECT * FROM paper_trades
     WHERE user_id = $1 AND position_status IN ('open', 'pending')
     ORDER BY created_at DESC`,
    [userId]
  );
  const historyResult = await pool.query(
    `SELECT * FROM paper_trades
     WHERE user_id = $1 AND position_status = 'closed'
     ORDER BY closed_at DESC NULLS LAST, created_at DESC`,
    [userId]
  );

  const priceMap = new Map(priceOverrides);
  if (fetchLive) {
    const uniqueKeys = new Map();
    for (const row of openResult.rows) {
      if (row.position_status === 'open') {
        const key = tradeKey(row.asset_type, row.asset_symbol);
        if (!priceMap.has(key)) uniqueKeys.set(key, row);
      }
    }
    await Promise.all(Array.from(uniqueKeys.values()).map(async (row) => {
      const fallback = fallbackPriceForTrade(row);
      try {
        const live = await fetchLivePrice(row.asset_type, row.asset_symbol, fallback);
        priceMap.set(tradeKey(row.asset_type, row.asset_symbol), live.price);
      } catch {
        priceMap.set(tradeKey(row.asset_type, row.asset_symbol), fallback);
      }
    }));
  }

  const positions = openResult.rows.map((row) => {
    const key = tradeKey(row.asset_type, row.asset_symbol);
    return normalizeTrade(row, priceMap.get(key));
  });
  const history = historyResult.rows.map((row) => normalizeTrade(row));
  const realizedProfitLoss = history.reduce((sum, trade) => sum + trade.profit_loss, 0);
  const unrealizedProfitLoss = positions.reduce((sum, trade) => sum + trade.profit_loss, 0);
  const openPositionValue = positions.reduce((sum, trade) => sum + trade.current_value, 0);
  const portfolioValue = wallet.virtual_balance + openPositionValue;
  const totalProfitLoss = realizedProfitLoss + unrealizedProfitLoss;
  const wins = history.filter((trade) => trade.profit_loss > 0).length;
  const winRate = history.length ? (wins / history.length) * 100 : null;
  const bestTrade = history.length
    ? history.reduce((best, trade) => trade.profit_loss > best.profit_loss ? trade : best, history[0])
    : null;
  const worstTrade = history.length
    ? history.reduce((worst, trade) => trade.profit_loss < worst.profit_loss ? trade : worst, history[0])
    : null;

  const nextWallet = {
    ...wallet,
    total_portfolio_value: portfolioValue,
    total_profit_loss: totalProfitLoss,
  };

  await pool.query(
    `UPDATE paper_wallets
     SET total_portfolio_value = $1,
         total_profit_loss = $2,
         updated_at = NOW()
     WHERE user_id = $3`,
    [portfolioValue, totalProfitLoss, userId]
  );

  return {
    wallet: nextWallet,
    positions,
    history,
    summary: {
      available_cash: wallet.virtual_balance,
      open_position_value: openPositionValue,
      total_portfolio_value: portfolioValue,
      realized_profit_loss: realizedProfitLoss,
      unrealized_profit_loss: unrealizedProfitLoss,
      total_profit_loss: totalProfitLoss,
      open_positions: positions.length,
      closed_trades: history.length,
      win_rate_percentage: winRate,
      best_trade: bestTrade,
      worst_trade: worstTrade,
    },
  };
}

async function updatePaperTradeTpsl(userId, tradeId, stopLoss, takeProfit) {
  if (!tradeId) throw httpError(400, "trade_id is required");

  const client = await pool.connect();
  let trade;
  try {
    await client.query("BEGIN");
    const result = await client.query(
      `SELECT * FROM paper_trades
       WHERE user_id = $1 AND trade_id = $2 AND position_status IN ('open', 'pending')
       FOR UPDATE`,
      [userId, tradeId]
    );
    if (!result.rows.length) throw httpError(404, "Open position not found");
    const row = result.rows[0];
    validateLongTpsl(asNumber(row.entry_price), stopLoss, takeProfit);

    const updateResult = await client.query(
      `UPDATE paper_trades
       SET stop_loss = $1,
           take_profit = $2,
           updated_at = NOW()
       WHERE user_id = $3 AND trade_id = $4
       RETURNING *`,
      [stopLoss, takeProfit, userId, tradeId]
    );
    await client.query("COMMIT");
    trade = normalizeTrade(updateResult.rows[0]);
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }

  const data = await loadPaperPortfolio(userId, { fetchLive: false });
  return { trade, data };
}

function handleRouteError(res, err, label) {
  const status = err.status || 500;
  if (status >= 500) console.error(`${label} error:`, err);
  res.status(status).json({ error: err.message || "Internal server error" });
}

// GET /api/portfolio/me - legacy simulator dashboard data.
router.get("/me", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);

    const userResult = await pool.query("SELECT balance FROM users WHERE id = $1", [userId]);
    if (userResult.rows.length === 0) {
      return res.status(404).json({ error: "User not found" });
    }
    const balance = parseFloat(userResult.rows[0].balance);

    const tradesResult = await pool.query(
      "SELECT * FROM trades WHERE user_id = $1 ORDER BY closed_at DESC LIMIT 50",
      [userId]
    );

    const pnlResult = await pool.query(
      "SELECT SUM(pnl) as total_pnl FROM trades WHERE user_id = $1",
      [userId]
    );
    const total_pnl = parseFloat(pnlResult.rows[0].total_pnl || 0);

    const paper = await loadPaperPortfolio(userId, { fetchLive: true });
    const tradeFeed = await loadTradeFeed(userId, { limit: 50 });

    res.json({
      balance,
      total_pnl,
      trades: tradesResult.rows,
      paper,
      trade_feed: tradeFeed,
    });
  } catch (err) {
    handleRouteError(res, err, "GET /portfolio/me");
  }
});

// GET /api/portfolio/trade-feed - unified recent trade feed.
router.get("/trade-feed", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const trades = await loadTradeFeed(userId, {
      limit: req.query.limit,
      before: req.query.before,
    });
    res.json({ success: true, trades, has_more: trades.length >= Math.min(Math.max(Number(req.query.limit) || 50, 1), 100) });
  } catch (err) {
    handleRouteError(res, err, "GET /portfolio/trade-feed");
  }
});

// POST /api/portfolio/trade-feed - record a simulator/manual execution event.
router.post("/trade-feed", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const { trade, inserted } = await recordAndBroadcastTradeEvent(pool, userId, {
      ...req.body,
      source_market: req.body.source_market || "simulator",
      asset_type: req.body.asset_type || "simulator",
    });
    res.status(inserted ? 201 : 200).json({ success: true, trade, inserted });
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/trade-feed");
  }
});

// POST /api/portfolio/trade - legacy simulated futures trade logger.
router.post("/trade", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const { symbol, side, size_usd, entry_price, exit_price, pnl } = req.body;

    if (!symbol || !side || size_usd === undefined || pnl === undefined) {
      return res.status(400).json({ error: "Missing required fields" });
    }

    const client = await pool.connect();
    let savedTrade;
    try {
      await client.query("BEGIN");

      const tradeResult = await client.query(
        `INSERT INTO trades (user_id, symbol, side, size_usd, entry_price, exit_price, pnl)
         VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *`,
        [userId, symbol, side, size_usd, entry_price, exit_price, pnl]
      );
      savedTrade = tradeResult.rows[0];

      await client.query(
        "UPDATE users SET balance = balance + $1 WHERE id = $2",
        [pnl, userId]
      );

      await client.query("COMMIT");
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }

    await recordAndBroadcastTradeEvent(pool, userId, {
      event_key: req.body.event_key || `simulator:${savedTrade.id}:sell`,
      trade_id: req.body.trade_id || savedTrade.id,
      asset_symbol: symbol,
      asset_type: "simulator",
      buy_or_sell: side === "short" ? "buy" : "sell",
      quantity: req.body.quantity ?? req.body.qty ?? 0,
      entry_price: entry_price,
      exit_price: exit_price,
      execution_price: exit_price ?? entry_price,
      trade_value: size_usd,
      profit_loss: pnl,
      timestamp: savedTrade.closed_at,
      source_market: "simulator",
    });

    res.json({ success: true, trade: savedTrade });
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/trade");
  }
});

// GET /api/portfolio/paper - persisted live paper-trading portfolio.
router.get("/paper", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const data = await loadPaperPortfolio(userId);
    res.json(data);
  } catch (err) {
    handleRouteError(res, err, "GET /portfolio/paper");
  }
});

// POST /api/portfolio/paper/order - open a live paper position at latest price.
router.post(["/paper/order", "/paper/buy"], requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const assetType = normalizeAssetType(req.body.asset_type);
    const symbol = normalizeSymbol(req.body.asset_symbol);
    const fallbackPrice = req.body.market_price;
    const live = await fetchLivePrice(assetType, symbol, fallbackPrice);
    const livePrice = live.price;

    const side = (req.body.side === 'short' || req.body.side === 'sell') ? 'short' : 'long';
    const orderType = ['limit', 'stop_market', 'stop_limit'].includes(req.body.order_type) ? req.body.order_type : 'market';
    const positionStatus = orderType === 'market' ? 'open' : 'pending';

    const triggerPrice = parseOptionalPositiveNumber(req.body.trigger_price, "Trigger price");
    const limitPrice = parseOptionalPositiveNumber(req.body.limit_price, "Limit price");

    const entryPrice = orderType === 'market' ? livePrice : (orderType === 'limit' ? limitPrice : triggerPrice);

    const quantityInput = req.body.quantity;
    const amountInput = req.body.invested_amount ?? req.body.amount ?? req.body.size_usd;
    const quantity = quantityInput != null && quantityInput !== ""
      ? parsePositiveNumber(quantityInput, "Quantity")
      : null;
    const investedAmount = amountInput != null && amountInput !== ""
      ? parsePositiveNumber(amountInput, "Trade amount")
      : quantity * entryPrice;

    if (!Number.isFinite(investedAmount) || investedAmount <= 0) {
      throw httpError(400, "Enter a quantity or amount greater than zero");
    }

    const finalQuantity = quantity ?? investedAmount / entryPrice;
    const stopLoss = parseOptionalPositiveNumber(req.body.stop_loss ?? req.body.sl_price, "Stop loss");
    const takeProfit = parseOptionalPositiveNumber(req.body.take_profit ?? req.body.tp_price, "Take profit");

    const client = await pool.connect();
    let trade;
    try {
      await client.query("BEGIN");
      const wallet = await getWallet(client, userId, { lock: true });
      if (investedAmount > wallet.virtual_balance) {
        throw httpError(400, "Insufficient virtual balance");
      }

      const tradeId = crypto.randomUUID();
      const currentValue = finalQuantity * entryPrice;
      const result = await client.query(
        `INSERT INTO paper_trades (
           trade_id, user_id, asset_symbol, asset_type, order_type, side, quantity,
           entry_price, trigger_price, limit_price, position_status, invested_amount, current_value,
           profit_loss, profit_loss_percentage, stop_loss, take_profit
         )
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 0, 0, $14, $15)
         RETURNING *`,
        [
          tradeId,
          userId,
          symbol,
          assetType,
          orderType,
          side,
          finalQuantity,
          entryPrice,
          triggerPrice,
          limitPrice,
          positionStatus,
          investedAmount,
          currentValue,
          stopLoss,
          takeProfit,
        ]
      );

      await client.query(
        `UPDATE paper_wallets
         SET virtual_balance = virtual_balance - $1,
             updated_at = NOW()
         WHERE user_id = $2`,
        [investedAmount, userId]
      );

      await client.query("COMMIT");
      trade = normalizeTrade(result.rows[0], entryPrice);
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }

    const data = await loadPaperPortfolio(userId, {
      fetchLive: false,
      priceOverrides: new Map([[tradeKey(assetType, symbol), livePrice]]),
    });
    if (positionStatus === 'open') {
      await recordAndBroadcastTradeEvent(pool, userId, tradeEventFromPaperTrade(trade, "open"));
    }
    res.status(201).json({ success: true, trade, execution: live, ...data });
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/paper/order");
  }
});

// POST /api/portfolio/paper/sell - close an open live paper position.
router.post("/paper/sell", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const tradeId = String(req.body.trade_id || req.body.id || "").trim();
    if (!tradeId) throw httpError(400, "trade_id is required");

    const client = await pool.connect();
    let closedTrade;
    let live;
    try {
      await client.query("BEGIN");
      await getWallet(client, userId, { lock: true });
      const result = await client.query(
        `SELECT * FROM paper_trades
         WHERE user_id = $1 AND trade_id = $2 AND position_status IN ('open', 'pending')
         FOR UPDATE`,
        [userId, tradeId]
      );
      if (!result.rows.length) {
        throw httpError(404, "Open position not found");
      }

      const row = result.rows[0];
      const fallback = req.body.market_price ?? fallbackPriceForTrade(row);
      live = await fetchLivePrice(row.asset_type, row.asset_symbol, fallback);
      closedTrade = await closeTrade(client, userId, row, live.price, "manual");
      await client.query("COMMIT");
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }

    const data = await loadPaperPortfolio(userId, { fetchLive: false });
    await recordAndBroadcastTradeEvent(pool, userId, tradeEventFromPaperTrade(closedTrade, "sell"));
    res.json({ success: true, trade: closedTrade, execution: live, ...data });
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/paper/sell");
  }
});

// PATCH /api/portfolio/paper/trades/:tradeId - update stop loss/take profit.
router.patch("/paper/trades/:tradeId", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const tradeId = req.params.tradeId;
    const stopLoss = parseOptionalPositiveNumber(req.body.stop_loss ?? req.body.sl_price, "Stop loss");
    const takeProfit = parseOptionalPositiveNumber(req.body.take_profit ?? req.body.tp_price, "Take profit");
    const { trade, data } = await updatePaperTradeTpsl(userId, tradeId, stopLoss, takeProfit);
    res.json({ success: true, trade, ...data });
  } catch (err) {
    handleRouteError(res, err, "PATCH /portfolio/paper/trades/:tradeId");
  }
});

// DELETE /api/portfolio/paper/trades/:tradeId/tpsl - remove stop loss/take profit.
router.delete("/paper/trades/:tradeId/tpsl", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const { trade, data } = await updatePaperTradeTpsl(userId, req.params.tradeId, null, null);
    res.json({ success: true, trade, ...data });
  } catch (err) {
    handleRouteError(res, err, "DELETE /portfolio/paper/trades/:tradeId/tpsl");
  }
});

// PATCH /api/portfolio/trade/update-tpsl - compatibility endpoint.
router.patch("/trade/update-tpsl", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const tradeId = String(req.body.trade_id || req.body.id || "").trim();
    const stopLoss = parseOptionalPositiveNumber(req.body.stop_loss ?? req.body.sl_price, "Stop loss");
    const takeProfit = parseOptionalPositiveNumber(req.body.take_profit ?? req.body.tp_price, "Take profit");
    const { trade, data } = await updatePaperTradeTpsl(userId, tradeId, stopLoss, takeProfit);
    res.json({ success: true, trade, ...data });
  } catch (err) {
    handleRouteError(res, err, "PATCH /portfolio/trade/update-tpsl");
  }
});

// DELETE /api/portfolio/trade/remove-tpsl - compatibility endpoint.
router.delete("/trade/remove-tpsl", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const tradeId = String(req.body.trade_id || req.body.id || req.query.trade_id || req.query.id || "").trim();
    const { trade, data } = await updatePaperTradeTpsl(userId, tradeId, null, null);
    res.json({ success: true, trade, ...data });
  } catch (err) {
    handleRouteError(res, err, "DELETE /portfolio/trade/remove-tpsl");
  }
});

// POST /api/portfolio/paper/tick - mark open positions and auto-close TP/SL hits.
router.post("/paper/tick", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const assetType = normalizeAssetType(req.body.asset_type);
    const symbol = normalizeSymbol(req.body.asset_symbol);
    const live = await fetchLivePrice(assetType, symbol, req.body.market_price);
    const price = live.price;

    const client = await pool.connect();
    const events = [];
    try {
      await client.query("BEGIN");
      await getWallet(client, userId, { lock: true });
      const result = await client.query(
        `SELECT * FROM paper_trades
         WHERE user_id = $1
           AND asset_type = $2
           AND asset_symbol = $3
           AND position_status IN ('open', 'pending')
         ORDER BY created_at ASC
         FOR UPDATE`,
        [userId, assetType, symbol]
      );

      for (const row of result.rows) {
        if (row.position_status === 'pending') {
          let execute = false;
          let executionPrice = price;

          if (row.order_type === 'limit') {
            if (row.side === 'long' && price <= asNumber(row.limit_price)) execute = true;
            if (row.side === 'short' && price >= asNumber(row.limit_price)) execute = true;
          } else if (row.order_type === 'stop_market' || row.order_type === 'stop_limit') {
            if (row.side === 'long' && price >= asNumber(row.trigger_price)) execute = true;
            if (row.side === 'short' && price <= asNumber(row.trigger_price)) execute = true;
          }

          if (execute) {
            const updateRes = await client.query(
              `UPDATE paper_trades SET position_status = 'open', entry_price = $1, updated_at = NOW() WHERE trade_id = $2 RETURNING *`,
              [executionPrice, row.trade_id]
            );
            events.push({ type: "opened", trade: normalizeTrade(updateRes.rows[0], price) });
          }
        } else {
          const takeProfit = row.take_profit == null ? null : asNumber(row.take_profit);
          const stopLoss = row.stop_loss == null ? null : asNumber(row.stop_loss);
          const isShort = row.side === 'short';

          let hitTp = false;
          let hitSl = false;

          if (takeProfit != null) {
            hitTp = isShort ? price <= takeProfit : price >= takeProfit;
          }
          if (stopLoss != null) {
            hitSl = isShort ? price >= stopLoss : price <= stopLoss;
          }

          if (hitTp) {
            const trade = await closeTrade(client, userId, row, takeProfit, "take_profit");
            events.push({ type: "closed", reason: "take_profit", trade });
          } else if (hitSl) {
            const trade = await closeTrade(client, userId, row, stopLoss, "stop_loss");
            events.push({ type: "closed", reason: "stop_loss", trade });
          } else {
            await updateStoredMark(client, userId, row.trade_id, price);
          }
        }
      }

      await client.query("COMMIT");
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }

    const data = await loadPaperPortfolio(userId, {
      fetchLive: false,
      priceOverrides: new Map([[tradeKey(assetType, symbol), price]]),
    });
    for (const event of events) {
      if (event.type === 'opened' && event.trade) {
        await recordAndBroadcastTradeEvent(pool, userId, tradeEventFromPaperTrade(event.trade, "open"));
      } else if (event.type === 'closed' && event.trade) {
        await recordAndBroadcastTradeEvent(
          pool,
          userId,
          tradeEventFromPaperTrade(event.trade, "sell", {
            event_key: `${assetType === "stock" ? "stocks" : "crypto"}:${event.trade.trade_id || event.trade.id}:sell`,
          })
        );
      }
    }
    res.json({ success: true, price, execution: live, events, ...data });
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/paper/tick");
  }
});

// GET /api/portfolio/balance/:userId - legacy internal simulator balance.
router.get("/balance/:userId", async (req, res) => {
  try {
    const userId = parseInt(req.params.userId, 10);
    if (Number.isNaN(userId)) {
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
    const pnlResult = await pool.query(
      "SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE user_id = $1",
      [userId]
    );
    const total_pnl = parseFloat(pnlResult.rows[0].total_pnl);

    res.json({ balance, total_pnl });
  } catch (err) {
    handleRouteError(res, err, "GET /portfolio/balance");
  }
});

// POST /api/portfolio/reset - reset legacy simulator and paper portfolio.
router.post("/reset", requireAuth, async (req, res) => {
  try {
    const userId = getUserId(req);
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("DELETE FROM trades WHERE user_id = $1", [userId]);
      await client.query("DELETE FROM paper_trades WHERE user_id = $1", [userId]);
      await client.query("DELETE FROM trade_events WHERE user_id = $1", [userId]);
      await client.query("UPDATE users SET balance = $1 WHERE id = $2", [INITIAL_VIRTUAL_BALANCE, userId]);
      await client.query(
        `INSERT INTO paper_wallets (user_id, virtual_balance, total_portfolio_value, total_profit_loss)
         VALUES ($1, $2, $2, 0)
         ON CONFLICT (user_id)
         DO UPDATE SET virtual_balance = EXCLUDED.virtual_balance,
                       total_portfolio_value = EXCLUDED.total_portfolio_value,
                       total_profit_loss = 0,
                       updated_at = NOW()`,
        [userId, INITIAL_VIRTUAL_BALANCE]
      );
      await client.query("COMMIT");
      res.json({
        success: true,
        balance: INITIAL_VIRTUAL_BALANCE,
        wallet: {
          user_id: userId,
          virtual_balance: INITIAL_VIRTUAL_BALANCE,
          total_portfolio_value: INITIAL_VIRTUAL_BALANCE,
          total_profit_loss: 0,
        },
        message: "Portfolio reset to initial state",
      });
    } catch (e) {
      await client.query("ROLLBACK");
      throw e;
    } finally {
      client.release();
    }
  } catch (err) {
    handleRouteError(res, err, "POST /portfolio/reset");
  }
});

module.exports = router;
