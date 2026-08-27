import { useState } from "react";
import { useAuthStore } from "../../stores/authStore";

export function LoginPage() {
  const login = useAuthStore((s) => s.login);
  const error = useAuthStore((s) => s.error);
  const clearError = useAuthStore((s) => s.clearError);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch {
      // error is stored in authStore
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-logo">SQL<span>RPA</span></div>
        <p className="login-subtitle">登录后继续操作数据库审核与 Agent 功能</p>

        <div className="settings-field">
          <label htmlFor="login-username">用户名</label>
          <input
            id="login-username"
            value={username}
            onChange={(e) => { setUsername(e.target.value); clearError(); }}
            autoComplete="username"
            autoFocus
          />
        </div>

        <div className="settings-field">
          <label htmlFor="login-password">密码</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); clearError(); }}
            autoComplete="current-password"
          />
        </div>

        {error && <div className="login-error">{error}</div>}

        <button className="login-submit" disabled={submitting || !username.trim() || !password}>
          {submitting ? "登录中..." : "登录"}
        </button>
      </form>
    </div>
  );
}
