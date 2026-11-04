import { useState, type FormEvent } from "react";
import { signIn } from "../api/client";

export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await signIn(username, password);
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  return <main className="login-page">
    <form className="login-card" onSubmit={submit} aria-label="Sign in">
      <h2>Mangarr</h2>
      <label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required autoFocus /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <button className="btn primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <p className="section-hint">Forgot the password? Set MANGARR_LOGIN_DISABLED=true on the container, restart, and choose a new one in Settings → Login.</p>
    </form>
  </main>;
}
