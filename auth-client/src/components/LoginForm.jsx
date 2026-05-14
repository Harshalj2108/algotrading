/**
 * LoginForm — Email + password login form
 */

import { useState } from "react";
import StarBorder from "./StarBorder";

const AUTH_SERVER = "http://localhost:3001";

export default function LoginForm({ onSuccess, onError }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email || !password) return;
    setLoading(true);
    onError("");

    try {
      const res = await fetch(`${AUTH_SERVER}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();

      if (!res.ok) {
        onError(data.error || "Login failed");
        setLoading(false);
        return;
      }

      onSuccess(data.user);
    } catch {
      onError("Cannot reach auth server. Is it running on port 3001?");
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} autoComplete="on">
      <div className="form-group">
        <label className="form-label" htmlFor="login-email">Email</label>
        <div className="input-wrap">
          <input
            className="form-input"
            type="email"
            id="login-email"
            name="email"
            placeholder="you@example.com"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ paddingLeft: '14px' }}
            required
          />
        </div>
      </div>

      <div className="form-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <label className="form-label" htmlFor="login-password" style={{ marginBottom: 0 }}>Password</label>
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShowPass(!showPass)}
            style={{ background: 'none', border: 'none', color: '#D8B4FE', fontSize: '13px', cursor: 'pointer', fontWeight: 600, padding: 0 }}
          >
            {showPass ? "Hide" : "Show"}
          </button>
        </div>
        <div className="input-wrap">
          <input
            className="form-input"
            type={showPass ? "text" : "password"}
            id="login-password"
            name="password"
            placeholder="Enter your password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ paddingLeft: '14px' }}
            required
          />
        </div>
      </div>

      <StarBorder
        as="button"
        type="submit"
        className={`btn-primary${loading ? " loading" : ""}`}
        disabled={loading}
        color="#26a69a"
      >
        <span className="btn-text">Sign In to Simulator</span>
      </StarBorder>
    </form>
  );
}
