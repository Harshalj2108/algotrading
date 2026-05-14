const crypto = require("crypto");
const { URL } = require("url");
const { pool } = require("./db");
const { verifyToken } = require("./middleware/auth");

const clientsByUser = new Map();

function parseCookies(header = "") {
  return header.split(";").reduce((cookies, part) => {
    const index = part.indexOf("=");
    if (index === -1) return cookies;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (key) cookies[key] = decodeURIComponent(value);
    return cookies;
  }, {});
}

function asNumber(value, fallback = null) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeSide(value) {
  const side = String(value || "").trim().toLowerCase();
  if (["sell", "short", "closed", "close"].includes(side)) return "sell";
  return "buy";
}

function normalizeAssetType(value, sourceMarket = "") {
  const type = String(value || sourceMarket || "").trim().toLowerCase();
  if (type === "stock" || type === "stocks") return "stock";
  if (type === "sim" || type === "simulator" || type === "paper") return "simulator";
  return "crypto";
}

function normalizeSourceMarket(value, assetType = "") {
  const source = String(value || assetType || "").trim().toLowerCase();
  if (source === "stock" || source === "stocks") return "stocks";
  if (source === "sim" || source === "simulator" || source === "paper") return "simulator";
  return "crypto";
}

function normalizeTradeEvent(row) {
  return {
    id: row.event_id,
    event_id: row.event_id,
    event_key: row.event_key,
    trade_id: row.trade_id,
    user_id: Number(row.user_id),
    asset_symbol: row.asset_symbol,
    symbol: row.asset_symbol,
    asset_type: row.asset_type,
    buy_or_sell: row.buy_or_sell,
    side: row.buy_or_sell,
    order_type: row.buy_or_sell,
    quantity: asNumber(row.quantity, 0),
    qty: asNumber(row.quantity, 0),
    entry_price: asNumber(row.entry_price, 0),
    exit_price: row.exit_price == null ? null : asNumber(row.exit_price, null),
    execution_price: asNumber(row.execution_price, asNumber(row.entry_price, 0)),
    trade_value: asNumber(row.trade_value, 0),
    size_usd: asNumber(row.trade_value, 0),
    invested_amount: asNumber(row.trade_value, 0),
    pnl: asNumber(row.profit_loss, 0),
    profit_loss: asNumber(row.profit_loss, 0),
    timestamp: row.event_timestamp,
    created_at: row.event_timestamp,
    closed_at: row.event_timestamp,
    source_market: row.source_market,
    isLive: true,
  };
}

function buildEventInput(userId, data) {
  const sourceMarket = normalizeSourceMarket(data.source_market, data.asset_type);
  const assetType = normalizeAssetType(data.asset_type, sourceMarket);
  const side = normalizeSide(data.buy_or_sell || data.side || data.order_type);
  const tradeId = String(data.trade_id || data.id || data.position_id || crypto.randomUUID()).trim();
  const eventKey = String(data.event_key || `${sourceMarket}:${tradeId}:${side}`).slice(0, 128);
  const entryPrice = asNumber(data.entry_price, asNumber(data.execution_price, asNumber(data.exit_price, 0))) || 0;
  const exitPrice = data.exit_price == null ? null : asNumber(data.exit_price, null);
  const executionPrice = asNumber(data.execution_price, side === "sell" ? (exitPrice ?? entryPrice) : entryPrice) || 0;
  const quantity = asNumber(data.quantity ?? data.qty, 0) || 0;
  const tradeValue = asNumber(
    data.trade_value ?? data.size_usd ?? data.invested_amount ?? data.current_value,
    quantity && executionPrice ? quantity * executionPrice : 0
  ) || 0;

  return {
    eventKey,
    tradeId,
    userId,
    assetSymbol: String(data.asset_symbol || data.symbol || "SIM").trim().toUpperCase().slice(0, 64),
    assetType,
    side,
    quantity,
    entryPrice,
    exitPrice,
    executionPrice,
    tradeValue,
    profitLoss: asNumber(data.profit_loss ?? data.pnl, 0) || 0,
    sourceMarket,
    timestamp: data.timestamp || data.created_at || data.closed_at || new Date().toISOString(),
  };
}

async function recordTradeEvent(db, userId, data) {
  const event = buildEventInput(userId, data);
  const result = await db.query(
    `INSERT INTO trade_events (
       event_key, trade_id, user_id, asset_symbol, asset_type, buy_or_sell,
       quantity, entry_price, exit_price, execution_price, trade_value,
       profit_loss, event_timestamp, source_market
     )
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
     ON CONFLICT (user_id, event_key) DO NOTHING
     RETURNING *`,
    [
      event.eventKey,
      event.tradeId,
      event.userId,
      event.assetSymbol,
      event.assetType,
      event.side,
      event.quantity,
      event.entryPrice,
      event.exitPrice,
      event.executionPrice,
      event.tradeValue,
      event.profitLoss,
      event.timestamp,
      event.sourceMarket,
    ]
  );

  if (result.rows.length) {
    return { trade: normalizeTradeEvent(result.rows[0]), inserted: true };
  }

  const existing = await db.query(
    `SELECT * FROM trade_events WHERE user_id = $1 AND event_key = $2`,
    [userId, event.eventKey]
  );
  return {
    trade: existing.rows[0] ? normalizeTradeEvent(existing.rows[0]) : null,
    inserted: false,
  };
}

async function recordAndBroadcastTradeEvent(db, userId, data) {
  const result = await recordTradeEvent(db, userId, data);
  if (result.inserted && result.trade) {
    broadcastTradeEvent(userId, result.trade);
  }
  return result;
}

async function loadTradeFeed(userId, { limit = 50, before } = {}) {
  const boundedLimit = Math.min(Math.max(Number(limit) || 50, 1), 100);
  const params = [userId, boundedLimit];
  let where = "WHERE user_id = $1";
  if (before) {
    params.splice(1, 0, before);
    where += " AND event_timestamp < $2";
  }

  const limitParam = before ? "$3" : "$2";
  const result = await pool.query(
    `SELECT * FROM trade_events
     ${where}
     ORDER BY event_timestamp DESC, event_id DESC
     LIMIT ${limitParam}`,
    params
  );
  return result.rows.map(normalizeTradeEvent);
}

function encodeFrame(payload) {
  const data = Buffer.from(payload);
  const length = data.length;
  if (length < 126) {
    return Buffer.concat([Buffer.from([0x81, length]), data]);
  }
  if (length < 65536) {
    const header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(length, 2);
    return Buffer.concat([header, data]);
  }
  const header = Buffer.alloc(10);
  header[0] = 0x81;
  header[1] = 127;
  header.writeBigUInt64BE(BigInt(length), 2);
  return Buffer.concat([header, data]);
}

function sendFrame(socket, payload) {
  if (socket.destroyed || !socket.writable) return;
  try {
    socket.write(encodeFrame(payload));
  } catch {
    socket.destroy();
  }
}

function broadcastTradeEvent(userId, trade) {
  const sockets = clientsByUser.get(Number(userId));
  if (!sockets || sockets.size === 0) return;
  const payload = JSON.stringify({ type: "trade", trade });
  for (const socket of sockets) sendFrame(socket, payload);
}

function attachTradeFeedWebSocket(server) {
  server.on("upgrade", (req, socket) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname !== "/api/portfolio/trade-feed/ws") return;

    const cookies = parseCookies(req.headers.cookie || "");
    const user = verifyToken(cookies.token);
    const key = req.headers["sec-websocket-key"];
    if (!user || !key) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      return;
    }

    const accept = crypto
      .createHash("sha1")
      .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
    );

    const userId = Number(user.id);
    if (!clientsByUser.has(userId)) clientsByUser.set(userId, new Set());
    clientsByUser.get(userId).add(socket);
    sendFrame(socket, JSON.stringify({ type: "ready" }));

    const cleanup = () => {
      const sockets = clientsByUser.get(userId);
      if (!sockets) return;
      sockets.delete(socket);
      if (sockets.size === 0) clientsByUser.delete(userId);
    };

    socket.on("data", (buffer) => {
      if ((buffer[0] & 0x0f) === 0x8) {
        cleanup();
        socket.end();
      }
    });
    socket.on("close", cleanup);
    socket.on("error", cleanup);
  });
}

module.exports = {
  attachTradeFeedWebSocket,
  broadcastTradeEvent,
  loadTradeFeed,
  normalizeTradeEvent,
  recordAndBroadcastTradeEvent,
  recordTradeEvent,
};
