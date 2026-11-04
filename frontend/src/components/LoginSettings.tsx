import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setApiKey, signOut } from "../api/client";
import { ErrorNotice } from "./common";

type LoginState = { enabled: boolean; username: string; disabled_by_environment: boolean; fixed_api_key: boolean; apiKey?: string };

export function LoginSettings() {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ["login-settings"], queryFn: () => api.get<LoginState>("/settings/login") });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [current, setCurrent] = useState("");
  const [saved, setSaved] = useState("");
  const enabled = Boolean(state.data?.enabled);
  const save = useMutation({
    mutationFn: (enable: boolean) => api.put<LoginState>("/settings/login", {
      enabled: enable, username: username || state.data?.username || "", new_password: enable ? password : "", current_password: current,
    }),
    onMutate: () => setSaved(""),
    onSuccess: (result, enable) => {
      if (result.apiKey) setApiKey(result.apiKey);
      setPassword(""); setRepeat(""); setCurrent("");
      setSaved(!enable ? "Login turned off." : result.apiKey ? "Login is on. This browser stays signed in; other browsers must sign in." : "Login updated. Other browsers must sign in again.");
      client.setQueryData(["login-settings"], result);
    },
  });
  const mismatch = password !== repeat;
  // A new password needs the current one while login is on; without one, the saved password
  // re-enables login (when off) or a username change is saved (when on).
  const canEnable = Boolean(username || state.data?.username) && (password
    ? password.length >= 8 && !mismatch && (!enabled || Boolean(current))
    : Boolean(current) && (enabled ? Boolean(username) : Boolean(state.data?.username)));
  return <section className="settings-section" aria-label="Login">
    <h3>Login</h3>
    <p className="section-hint">Require a username and password before the web interface opens. While it is off, anyone who can reach this port can use Mangarr. Turning it on replaces the browser API key, so copies of the old key stop working; give scripts their own key under API Keys. Passwords are stored hashed in /config/auth.json, which backups do not include.</p>
    {state.data?.disabled_by_environment && <div className="error-banner">MANGARR_LOGIN_DISABLED is set, so login is ignored. Change the password here, then remove that variable and restart.</div>}
    {state.data?.fixed_api_key && <div className="error-banner">MANGARR_API_KEY is set, so the browser key cannot be replaced. Anyone who knows it can still use the API; remove that variable to let login protect it.</div>}
    {state.data && <p role="status">Login is {enabled ? <>on for <strong>{state.data.username}</strong></> : "off"}.</p>}
    <div className="form-row"><label htmlFor="login-username">Username</label><input id="login-username" type="text" autoComplete="username" placeholder={state.data?.username || "admin"} value={username} onChange={e => setUsername(e.target.value)} /></div>
    <div className="form-row"><label htmlFor="login-password">{enabled ? "New password" : "Password"}</label><input id="login-password" type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} /></div>
    <div className="form-row"><label htmlFor="login-repeat">Repeat password</label><input id="login-repeat" type="password" autoComplete="new-password" value={repeat} onChange={e => setRepeat(e.target.value)} /></div>
    {(enabled || state.data?.username) && <div className="form-row"><label htmlFor="login-current">Current password</label><input id="login-current" type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} /></div>}
    {password && password.length < 8 && <p className="section-hint">Use at least 8 characters.</p>}
    {password && repeat && mismatch && <p className="section-hint">The passwords do not match.</p>}
    <div className="form-row">
      <button className="btn primary" disabled={!canEnable || save.isPending} onClick={() => save.mutate(true)}>{enabled ? "Save login changes" : "Turn on login"}</button>
      {enabled && <button className="btn danger" disabled={!current || save.isPending} onClick={() => save.mutate(false)}>Turn off login</button>}
      {enabled && <button className="btn" onClick={() => void signOut().then(() => window.location.reload())}>Sign out</button>}
    </div>
    {saved && <p role="status">{saved}</p>}
    <ErrorNotice error={save.error || state.error} />
  </section>;
}
