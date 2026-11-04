import { useState } from "react";
import { api } from "../api/client";

type Result = { status: string; message: string };

export default function ToomicsCookieTest({ cookie }: { cookie: string }) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ cookie: string; value: Result } | null>(null);
  async function test() {
    setPending(true);
    setResult(null);
    try {
      const value = await api.post<Result>("/settings/toomics/test-cookie", { session_cookie: cookie });
      setResult({ cookie, value });
    } catch {
      setResult({ cookie, value: { status: "error", message: "Could not check the cookie. Check your connection and saved Toomics discovery proxy settings, then try again." } });
    } finally {
      setPending(false);
    }
  }
  return <div className="form-row"><span /><div>
    <button type="button" className="btn" disabled={pending || !cookie.trim()} onClick={test}>{pending ? "Testing cookie…" : "Test cookie"}</button>
    <p className="section-hint">Checks sign-in with the cookie above without saving it or downloading chapters. Uses your saved discovery proxy settings.</p>
    {result && result.cookie === cookie && <p role="status">{result.value.message}</p>}
  </div></div>;
}
