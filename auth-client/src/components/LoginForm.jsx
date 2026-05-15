/**
 * LoginForm — Email + password login form
 */

import { useState } from "react";
import StarBorder from "./StarBorder";
import { AUTH_SERVER } from '../config';



export default function LoginForm({ onSuccess, onError }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const [step, setStep] = useState("login");
  const [otp, setOtp] = useState("");

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
        if (data.error === "verification_required") {
          setStep("verify");
          setLoading(false);
          return;
        }
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

  const handleVerifyOTP = async (e) => {
    e.preventDefault();
    if (!otp || otp.length !== 6) return;
    setLoading(true);
    onError("");

    try {
      const res = await fetch(`${AUTH_SERVER}/api/auth/verify-otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, otp }),
      });
      const data = await res.json();

      if (!res.ok) {
        onError(data.error || "Verification failed");
        setLoading(false);
        return;
      }

      onSuccess(data.user);
    } catch {
      onError("Cannot reach auth server.");
      setLoading(false);
    }
  };

  if (step === "verify") {
    return (
      <form onSubmit={handleVerifyOTP} autoComplete="off">
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <h3 style={{ color: '#f8fafc', marginBottom: '8px' }}>Verify Your Email</h3>
          <p style={{ color: '#94a3b8', fontSize: '14px' }}>
            We sent a 6-digit code to <strong>{email}</strong>.
          </p>
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="login-otp">Verification Code</label>
          <div className="input-wrap">
            <input
              className="form-input"
              type="text"
              id="login-otp"
              name="otp"
              placeholder="123456"
              maxLength={6}
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              style={{ letterSpacing: '6px', textAlign: 'center', fontSize: '20px', paddingLeft: '10px' }}
              required
            />
          </div>
        </div>

        <StarBorder
          as="button"
          type="submit"
          className={`btn-primary${loading ? " loading" : ""}`}
          disabled={loading || otp.length !== 6}
          color="#26a69a"
        >
          <span className="btn-text">Verify Account</span>
        </StarBorder>
      </form>
    );
  }

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
