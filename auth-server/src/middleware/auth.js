/**
 * middleware/auth.js — JWT verification middleware
 * 
 * Reads JWT from httpOnly cookie ("token") and attaches user to req.user.
 * Also exports verifyToken for use by external services (Flask).
 */

const jwt = require("jsonwebtoken");

const JWT_SECRET = process.env.JWT_SECRET || "fallback-secret";

/**
 * Generate a JWT for a user.
 */
function signToken(user) {
  return jwt.sign(
    {
      id: user.id,
      email: user.email,
      username: user.username,
      avatar_url: user.avatar_url || null,
    },
    JWT_SECRET,
    { expiresIn: "7d" }
  );
}

/**
 * Verify and decode a JWT.  Returns the payload or null.
 */
function verifyToken(token) {
  try {
    return jwt.verify(token, JWT_SECRET);
  } catch {
    return null;
  }
}

/**
 * Express middleware — require valid JWT cookie.
 */
function requireAuth(req, res, next) {
  const token = req.cookies?.token;
  if (!token) {
    return res.status(401).json({ error: "Not authenticated" });
  }
  const payload = verifyToken(token);
  if (!payload) {
    return res.status(401).json({ error: "Invalid or expired token" });
  }
  req.user = payload;
  next();
}

module.exports = { signToken, verifyToken, requireAuth, JWT_SECRET };
