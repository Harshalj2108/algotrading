/**
 * RegisterForm — Email + username + password registration form
 */

import { useState, useMemo } from "react";
import StarBorder from "./StarBorder";

const AUTH_SERVER = "http://localhost:3001";

function getStrength(pw) {
  if (!pw) return { score: 0, label: "" };
  let s = 0;
  if (pw.length >= 6) s++;
  if (pw.length >= 10) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw)) s++;
  const score = Math.min(4, s);
  const labels = ["", "Weak", "Fair", "Good", "Strong"];
  return { score, label: labels[score] };
}

export default function RegisterForm({ onSuccess, onError }) {
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const strength = useMemo(() => getStrength(password), [password]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email || !password) return;
    setLoading(true);
    onError("");

    try {
      const res = await fetch(`${AUTH_SERVER}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, username, password }),
      });
      const data = await res.json();

      if (!res.ok) {
        onError(data.error || "Registration failed");
        setLoading(false);
        return;
      }

      localStorage.setItem("isNewRegistration", "true");
      onSuccess(data.user);
    } catch {
      onError("Cannot reach auth server. Is it running on port 3001?");
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} autoComplete="on">
      <div className="form-group">
        <label className="form-label" htmlFor="reg-email">Email</label>
        <div className="input-wrap">
          <input
            className="form-input"
            type="email"
            id="reg-email"
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
        <label className="form-label" htmlFor="reg-username">Username</label>
        <div className="input-wrap">
          <input
            className="form-input"
            type="text"
            id="reg-username"
            name="username"
            placeholder="Choose a username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{ paddingLeft: '14px' }}
          />
        </div>
      </div>

      <div className="form-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <label className="form-label" htmlFor="reg-password" style={{ marginBottom: 0 }}>Password</label>
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
            id="reg-password"
            name="new-password"
            placeholder="Min 6 characters"
            autoComplete="new-password"
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ paddingLeft: '14px' }}
            required
          />
        </div>
        {password && (
          <>
            <div className="strength-bar">
              {[1, 2, 3, 4].map((i) => (
                <div
                  key={i}
                  className={`strength-seg${i <= strength.score ? ` s${strength.score}` : ""}`}
                />
              ))}
            </div>
            <div className="strength-label">{strength.label}</div>
          </>
        )}
      </div>

      <StarBorder
        as="button"
        type="submit"
        className={`btn-primary${loading ? " loading" : ""}`}
        disabled={loading || password.length < 6}
        color="#26a69a"
      >
        <span className="btn-text">Create Account</span>
      </StarBorder>
    </form>
  );
}
