import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Settings } from "../api/types";
import { ErrorNotice, Toggle } from "./common";

type Preview = { preview_id: string; count: number; skipped: number; examples: { path: string; before: number[]; after: number[] }[] };
type Result = { changed: number; skipped: number; error_count: number; errors: { path: string; error: string }[]; undo_id: string | null };

export function PermissionsSettings({ form, setForm, dirty }: { form: Settings; setForm: (form: Settings) => void; dirty: boolean }) {
  const enabled = form.permissions_enabled === "true";
  const folder = parseInt(form.permissions_folder_mode || "775", 8);
  const file = folder & 0o666;
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [confirm, setConfirm] = useState(false);
  const { data: status } = useQuery({ queryKey: ["permissions-status"], queryFn: () => api.get<{ supported: boolean; uid: number; gid: number }>("/settings/permissions/status") });
  useEffect(() => { setPreview(null); setConfirm(false); }, [form]);
  const scan = useMutation({
    mutationFn: () => api.post<Preview>("/settings/permissions/preview"),
    onMutate: () => { setPreview(null); setConfirm(false); setResult(null); },
    onSuccess: setPreview,
  });
  const fix = useMutation({
    mutationFn: () => api.post<Result>("/settings/permissions/fix", { id: preview!.preview_id }),
    onSuccess: (r) => { setResult(r); setPreview(null); setConfirm(false); },
  });
  const undo = useMutation({
    mutationFn: () => api.post<Result>("/settings/permissions/undo", { id: result!.undo_id }),
    onSuccess: (r) => setResult({ ...r, undo_id: null }),
  });
  const busy = scan.isPending || fix.isPending || undo.isPending;
  const set = (key: string, value: string) => setForm({ ...form, [key]: value });
  return <section className="settings-section" aria-label="Permissions">
    <h3>Permissions</h3>
    <div className="form-row">
      <label>Set permissions</label>
      <Toggle on={enabled} label="Set permissions on files and folders Mangarr creates" onChange={(on) => set("permissions_enabled", on ? "true" : "false")} />
    </div>
    <p className="section-hint">Apply chmod/chown when Mangarr creates a CBZ, copies an import, or creates a library folder. Leave off to use the container’s PUID, PGID, and UMASK.</p>
    <div className="form-row">
      <label htmlFor="permissions-mode">chmod Folder</label>
      <select id="permissions-mode" disabled={!enabled} value={form.permissions_folder_mode || "775"} onChange={(e) => set("permissions_folder_mode", e.target.value)}>
        <option value="755">755 — drwxr-xr-x</option><option value="775">775 — drwxrwxr-x</option><option value="777">777 — drwxrwxrwx</option>
        {!["755", "775", "777"].includes(form.permissions_folder_mode || "775") && <option>{form.permissions_folder_mode}</option>}
      </select>
    </div>
    <table aria-label="Resulting permissions" style={{ margin: "12px 0", fontFamily: "monospace" }}><tbody>
      <tr><th style={{ textAlign: "left", paddingRight: 24 }}>Umask equivalent</th><td>{(0o777 & ~folder).toString(8).padStart(4, "0")}</td></tr>
      <tr><th style={{ textAlign: "left" }}>Folder</th><td>{folder.toString(8)}</td></tr>
      <tr><th style={{ textAlign: "left" }}>File</th><td>{file.toString(8)} (no execute bits)</td></tr>
    </tbody></table>
    <div className="form-row"><label htmlFor="permissions-user">chown User</label><input id="permissions-user" disabled={!enabled} value={form.permissions_user ?? "99"} onChange={(e) => set("permissions_user", e.target.value)} /></div>
    <p className="section-hint">User name or UID. On Unraid, use 99 for nobody. Blank keeps the current owner. {status?.supported && <>Mangarr runs as UID {status.uid}; changing to another owner requires sufficient privileges.</>}</p>
    <div className="form-row"><label htmlFor="permissions-group">chown Group</label><input id="permissions-group" disabled={!enabled} value={form.permissions_group ?? "100"} onChange={(e) => set("permissions_group", e.target.value)} /></div>
    <p className="section-hint">Group name or GID. On Unraid, use 100 for users. Blank keeps the current group.</p>
    <p className="section-hint">Only newly created files and folders are changed automatically. Shared hardlinks keep their existing permissions so seeding files are unaffected.</p>
    <hr style={{ margin: "24px 0", borderColor: "var(--border)" }} />
    <h4>Fix existing Mangarr-created CBZ files</h4>
    <p className="section-hint">Preview tracked files that differ from the saved settings. Files adopted from disk, shared hardlinks, symlinks, existing folders, and unrelated files are excluded. Each repair saves an undo record in /config/logs/permissions.</p>
    {dirty && <p className="section-hint">Save Changes before previewing or fixing permissions.</p>}
    {status && !status.supported && <p className="section-hint">Permission repair is available in the Linux container.</p>}
    <button className="btn" disabled={dirty || busy || !status?.supported} onClick={() => scan.mutate()}>{scan.isPending ? "Checking…" : "Preview permissions"}</button>
    {preview && <div role="status" style={{ marginTop: 12 }}>
      <p>{preview.count} file(s) need changes. {preview.skipped} unavailable or protected file(s) skipped.</p>
      {preview.examples.length > 0 && <div style={{ maxHeight: 240, overflow: "auto" }}><table className="data-table"><thead><tr><th>File</th><th>Current mode / owner</th><th>New mode / owner</th></tr></thead><tbody>{preview.examples.map((e) => <tr key={e.path}><td style={{ overflowWrap: "anywhere" }}>{e.path}</td><td>{e.before[0].toString(8)} / {e.before[1]}:{e.before[2]}</td><td>{e.after[0].toString(8)} / {e.after[1]}:{e.after[2]}</td></tr>)}</tbody></table></div>}
      {preview.count > 0 && <><label style={{ display: "block", margin: "12px 0" }}><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> Apply these permission changes to the previewed files.</label><button className="btn" disabled={!enabled || !confirm || busy || dirty} onClick={() => fix.mutate()}>Fix previewed files</button>{!enabled && <p className="section-hint">Enable Set permissions and save before applying a repair.</p>}</>}
    </div>}
    {result && <div role="status"><p>Changed {result.changed}; skipped {result.skipped}; errors {result.error_count}.</p>{result.undo_id && <button className="btn" disabled={busy} onClick={() => undo.mutate()}>Undo this repair</button>}{result.errors.map((e) => <p key={e.path} className="error-banner">{e.path}: {e.error}</p>)}</div>}
    <ErrorNotice error={scan.error || fix.error || undo.error} />
  </section>;
}
