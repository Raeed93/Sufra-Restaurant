import { useState, type FormEvent } from "react";
import { api, ApiError } from "./api";

type Mode = "login" | "register";

export default function Login({ onLoggedIn }: { onLoggedIn: () => Promise<void> }) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await api.login(username, password);
      else await api.register(username, password);
      await onLoggedIn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Can't reach Sufra. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <div className="sadu" aria-hidden="true" />
      <section className="login-card">
        <h1 className="wordmark">
          Sufra <span lang="ar">سفرة</span>
        </h1>
        <p className="login-lede">
          Tell us what you like and what you can't eat. We'll remember it next time you order.
        </p>

        <div className="mode-switch" role="tablist" aria-label="Account">
          <button role="tab" aria-selected={mode === "login"} onClick={() => setMode("login")}>
            Sign in
          </button>
          <button role="tab" aria-selected={mode === "register"} onClick={() => setMode("register")}>
            Create account
          </button>
        </div>

        <form onSubmit={submit}>
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              minLength={3}
              maxLength={30}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              minLength={8}
            />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "One moment…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </section>
    </main>
  );
}
