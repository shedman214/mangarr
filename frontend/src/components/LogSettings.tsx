import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNotice } from "./common";

type Level = "info" | "debug" | "trace";
type LogState = {
  level: Level; source: "settings" | "environment"; environment_level: Level; file_enabled: boolean;
  files: { name: string; bytes: number; modified: number }[]; lines: string[];
};

const LEVELS: { value: Level; label: string; hint: string }[] = [
  { value: "info", label: "Info", hint: "Mangarr's own messages: downloads, imports, warnings and errors. Recommended." },
  { value: "debug", label: "Debug", hint: "Adds Mangarr's detailed steps, web interface requests and scheduled job runs." },
  { value: "trace", label: "Trace", hint: "Adds every outgoing web request (sources, Kavita, proxies). Very busy; use while reproducing a problem, then switch back." },
];

export function LogSettings() {
  const client = useQueryClient();
  const [lines, setLines] = useState(300);
  const state = useQuery({ queryKey: ["logs", lines], queryFn: () => api.get<LogState>(`/system/logs?lines=${lines}`) });
  const setLevel = useMutation({
    mutationFn: (level: Level) => api.put<{ level: Level }>("/system/logs/level", { level }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["logs"] }),
  });
  const download = useMutation({ mutationFn: async () => {
    const { blob, name } = await api.logsDownload();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } });
  const level = state.data?.level;
  const total = (state.data?.files || []).reduce((sum, f) => sum + f.bytes, 0);
  return <section className="settings-section" aria-label="Logs">
    <h3>Logs</h3>
    <p className="section-hint">Mangarr writes its log to /config/logs/mangarr.txt (5 files of up to 5 MB, oldest removed first) and to the container log. API keys, passwords, tokens, cookies and proxy credentials are replaced with &lt;redacted&gt; before anything is written, so a downloaded log is safe to share for troubleshooting — still glance through it before posting it publicly.</p>
    <div className="form-row">
      <label htmlFor="log-level">Log level</label>
      <select id="log-level" value={level || ""} disabled={!level || setLevel.isPending} onChange={e => setLevel.mutate(e.target.value as Level)}>
        {LEVELS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
      </select>
    </div>
    {level && <p className="section-hint">{LEVELS.find(l => l.value === level)?.hint} Applies immediately{state.data?.source === "environment" ? ` (currently from MANGARR_LOG_LEVEL; choosing one here saves it and takes priority)` : " and is remembered across restarts"}.</p>}
    {state.data && !state.data.file_enabled && <div className="error-banner">Mangarr cannot write to /config/logs, so only the container log is available. Check the folder's owner and permissions.</div>}
    <div className="form-row">
      <label htmlFor="log-lines">Show</label>
      <select id="log-lines" value={lines} onChange={e => setLines(Number(e.target.value))}>
        {[100, 300, 1000, 2000].map(n => <option key={n} value={n}>Last {n} lines</option>)}
      </select>
      <button className="btn" disabled={state.isFetching} onClick={() => void state.refetch()}>{state.isFetching ? "Refreshing…" : "Refresh"}</button>
      <button className="btn" disabled={download.isPending || !state.data?.files.length} onClick={() => download.mutate()}>{download.isPending ? "Preparing…" : "Download logs"}</button>
    </div>
    {state.data && <p className="section-hint">{state.data.files.length} file{state.data.files.length === 1 ? "" : "s"}, {(total / 1048576).toFixed(1)} MB. The download is a ZIP of all of them.</p>}
    {state.data && (state.data.lines.length
      ? <pre className="log-view" aria-label="Recent log lines" tabIndex={0}>{state.data.lines.join("\n")}</pre>
      : <p className="muted">No log lines yet.</p>)}
    <ErrorNotice error={state.error || setLevel.error || download.error} />
  </section>;
}
