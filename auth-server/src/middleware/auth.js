/**
 * middleware/auth.js — JWT verification middleware
 * 
 * Reads JWT from httpOnly cookie ("token") and attaches user to req.user.
 * Also exports verifyToken for use by external services (Flask).
 */

const jwt = require("jsonwebtoken");

const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET) {
  console.error("FATAL: JWT_SECRET environment variable is not set.");
  console.error("       Set JWT_SECRET to a strong, random string (at least 32 characters).");
  process.exit(1);
}

// Pin the algorithm to prevent "none" algorithm attacks
const JWT_ALGORITHM = "HS256";
const JWT_EXPIRY = "7d";

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
    { expiresIn: JWT_EXPIRY, algorithm: JWT_ALGORITHM }
  );
}

/**
 * Verify and decode a JWT.  Returns the payload or null.
 */
function verifyToken(token) {
  try {
    // Explicitly specify allowed algorithms to prevent algorithm substitution
    return jwt.verify(token, JWT_SECRET, { algorithms: [JWT_ALGORITHM] });
  } catch {
    return null;
  }
}

/**
 * Express middleware — require valid JWT cookie.
 */
function requireAuth(req, res, next) {
  let token = req.cookies?.token;
  if (!token && req.headers.authorization && req.headers.authorization.startsWith('Bearer ')) {
    token = req.headers.authorization.substring(7);
  }
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
