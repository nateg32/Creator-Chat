import { useState } from "react";
import { login, register } from "../api/client";
import "./Login.css";

export function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isRegister, setIsRegister] = useState(false);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      let result;
      if (isRegister) {
        result = await register(email, password);
      } else {
        result = await login(email, password);
      }

      // Store session in cookie (handled by browser via HttpOnly cookie)
      if (onLogin) {
        onLogin(result);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <h1>Creator Chat</h1>
        <p className="login-subtitle">Build personalized AI chats from creator content</p>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
              placeholder="you@example.com"
            />
          </div>

          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
              placeholder="Password"
            />
          </div>

          {error && <div className="error-message">{error}</div>}

          <button type="submit" disabled={loading} className="login-button">
            {loading ? "Loading..." : isRegister ? "Sign Up" : "Sign In"}
          </button>

          <button
            type="button"
            onClick={() => {
              setIsRegister(!isRegister);
              setError(null);
            }}
            className="switch-mode-button"
            disabled={loading}
          >
            {isRegister
              ? "Already have an account? Sign in"
              : "Don't have an account? Sign up"}
          </button>
        </form>
      </div>
    </div>
  );
}
