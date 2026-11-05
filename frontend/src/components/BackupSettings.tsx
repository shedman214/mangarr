import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNotice } from "./common";

type StoredBackup = { id: string; created: number | null; version: string | null; reason: string; bytes: number };
type Schedule = {
  enabled: boolean; interval_days: number; retention_days: number; manual_retention_days: number;
  folder: string; last_automatic: number | null; next_automatic: number | null; automatic_kept: number; manual_kept: number;
  removed?: number;
};
const AUTOMATIC = new Set(["scheduled", "pre-migration"]);
const DAY = 86400;
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "Not yet");
const LABELS: Record<string, string> = { scheduled: "Automatic", "pre-migration": "Before database update", manual: "Manual" };

/** Mirror of the server's prune rules, to warn before saving a policy that removes backups. */
function wouldRemove(saved: StoredBackup[], retention: number, manualRetention: number, now = Date.now() / 1000): number {
  const newestOverall = saved[0]?.id;
  const newestAuto = new Set<string>();
  const seen = new Set<string>();
  for (const b of saved) if (AUTOMATIC.has(b.reason) && !seen.has(b.reason)) { seen.add(b.reason); newestAuto.add(b.id); }
  return saved.filter((b) => {
    if (b.id === newestOverall || newestAuto.has(b.id)) return false;
    const age = (now - (b.created ?? now)) / DAY;
    return AUTOMATIC.has(b.reason) ? age > retention : manualRetention > 0 && age > manualRetention;
  }).length;
}

function AutomaticBackups({ saved }: { saved: StoredBackup[] | undefined }) {
  const client = useQueryClient();
  const schedule = useQuery({ queryKey: ["backup-schedule"], queryFn: () => api.get<Schedule>("/settings/backups/schedule") });
  const [draft, setDraft] = useState<{ enabled: boolean; interval: string; retention: string; manual: string } | null>(null);
  const d = schedule.data;
  const form = draft ?? (d ? { enabled: d.enabled, interval: String(d.interval_days), retention: String(d.retention_days), manual: String(d.manual_retention_days) } : null);
  const save = useMutation({
    mutationFn: (body: object) => api.put<Schedule>("/settings/backups/schedule", body),
    onSuccess: (result) => {
      setDraft(null);
      client.setQueryData(["backup-schedule"], result);
      client.invalidateQueries({ queryKey: ["backups"] });
    },
  });
  if (!form) return <ErrorNotice error={schedule.error} />;
  const set = (patch: Partial<typeof form>) => setDraft({ ...form, ...patch });
  const submit = () => {
    const body = { enabled: form.enabled, interval_days: Number(form.interval), retention_days: Number(form.retention), manual_retention_days: Number(form.manual) };
    if (!saved) return; // the warning below needs the full list of saved backups
    const count = wouldRemove(saved, body.retention_days, body.manual_retention_days);
    if (count && !confirm(`These settings remove ${count} saved backup${count === 1 ? "" : "s"} now. Continue?`)) return;
    save.mutate(body);
  };
  return <div className="automatic-backups" role="group" aria-label="Automatic backups">
    <h4>Automatic backups</h4>
    <p className="section-hint">Like Sonarr: Mangarr makes a backup every few days and deletes automatic backups older than the retention period. The newest automatic backup is always kept. Manual backups are kept unless you set a manual retention below.</p>
    <div className="form-row">
      <label htmlFor="backup-enabled">Make backups automatically</label>
      <input id="backup-enabled" type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
    </div>
    <div className="form-row">
      <label htmlFor="backup-interval">Interval (days)</label>
      <input id="backup-interval" type="text" inputMode="numeric" value={form.interval} disabled={!form.enabled} onChange={(e) => set({ interval: e.target.value })} />
    </div>
    <div className="form-row">
      <label htmlFor="backup-retention">Keep automatic backups (days)</label>
      <input id="backup-retention" type="text" inputMode="numeric" value={form.retention} onChange={(e) => set({ retention: e.target.value })} />
    </div>
    <div className="form-row">
      <label htmlFor="backup-manual-retention">Delete manual backups after (days)</label>
      <input id="backup-manual-retention" type="text" inputMode="numeric" value={form.manual} onChange={(e) => set({ manual: e.target.value })} />
    </div>
    <p className="section-hint">Interval: how often a backup is made (Sonarr default 7). Keep: automatic and before-update backups older than this are deleted (Sonarr default 28). Manual: 0 keeps manual backups until you delete them.</p>
    <div className="form-row">
      <label />
      <button className="btn primary" disabled={!draft || !saved || save.isPending} onClick={submit}>{save.isPending ? "Saving…" : "Save automatic backups"}</button>
    </div>
    {d && <dl className="backup-status">
      <dt>Folder</dt><dd>{d.folder}</dd>
      <dt>Last automatic backup</dt><dd>{when(d.last_automatic)}</dd>
      <dt>Next</dt><dd>{d.enabled ? (d.last_automatic ? when(d.next_automatic) : "Within the hour") : "Off"}</dd>
      <dt>Kept</dt><dd>{d.automatic_kept} automatic · {d.manual_kept} manual</dd>
    </dl>}
    {save.data?.removed ? <p role="status">Removed {save.data.removed} old backup{save.data.removed === 1 ? "" : "s"}.</p> : null}
    <ErrorNotice error={save.error || schedule.error} />
  </div>;
}

async function download(id: string) {
  const url = URL.createObjectURL(await api.backupDownload(id));
  const link = document.createElement("a");
  link.href = url; link.download = `mangarr-backup-${id}.zip`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function BackupSettings() {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const status = useQuery({ queryKey: ["restore-status"], queryFn: () => api.get<{ pending: boolean }>("/settings/backups/restore") });
  const stored = useQuery({ queryKey: ["backups"], queryFn: () => api.get<StoredBackup[]>("/settings/backups") });
  const backup = useMutation({ mutationFn: async () => {
    const result = await api.post<{ id: string }>("/settings/backups");
    await download(result.id);
  }, onSuccess: () => client.invalidateQueries({ queryKey: ["backups"] }) });
  const saved = useMutation({ mutationFn: download });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/settings/backups/${id}`),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["backups"] }); client.invalidateQueries({ queryKey: ["backup-schedule"] }); },
  });
  const restore = useMutation({ mutationFn: () => api.restoreUpload(file!), onSuccess: () => {
    setConfirmed(false); setFile(null); client.invalidateQueries({ queryKey: ["restore-status"] });
  } });
  const cancel = useMutation({ mutationFn: () => api.del("/settings/backups/restore"), onSuccess: () => client.invalidateQueries({ queryKey: ["restore-status"] }) });
  return <section className="settings-section" aria-label="Backup and restore">
    <h3>Backup and restore</h3>
    <p className="section-hint">Save the database, settings, source links, library records, history, and API keys. Manga files, logs, permission undo records, and container environment settings are not included. Backups contain passwords and keys—store them privately.</p>
    <button className="btn" disabled={backup.isPending} onClick={() => backup.mutate()}>{backup.isPending ? "Creating backup…" : "Download backup"}</button>
    <ErrorNotice error={backup.error} />
    {backup.isSuccess && <p role="status">Backup created and download requested. A copy is retained in /config/backups.</p>}
    {/* undefined until the list is loaded and current (not mid-refetch): saving waits, so the
        "removes N backups" warning is never skipped or counted from a stale list */}
    <AutomaticBackups saved={stored.isSuccess && !stored.isFetching ? stored.data : undefined} />
    <h4>Saved backups</h4>
    <p className="section-hint">Kept in /config/backups. Mangarr also saves one automatically before an update changes the database layout.</p>
    {stored.data && (stored.data.length ? <ul aria-label="Saved backups">
      {stored.data.map(b => <li key={b.id}>
        {b.created ? new Date(b.created * 1000).toLocaleString() : "Unknown date"} · {LABELS[b.reason] ?? "Manual"}{b.version ? ` · v${b.version}` : ""} · {(b.bytes / 1048576).toFixed(1)} MB{" "}
        <button className="btn" disabled={saved.isPending} onClick={() => saved.mutate(b.id)}>Download</button>{" "}
        <button className="btn danger" aria-label={`Delete backup from ${b.created ? new Date(b.created * 1000).toLocaleString() : "unknown date"}`} disabled={remove.isPending}
          onClick={() => { if (confirm("Delete this backup? This cannot be undone.")) remove.mutate(b.id); }}>Delete</button>
      </li>)}
    </ul> : <p>No saved backups yet.</p>)}
    <ErrorNotice error={stored.error || saved.error || remove.error} />
    <h4>Restore a backup</h4>
    <p className="section-hint">Restore replaces database records, including queue state and API keys, at the next container restart. The installation’s bootstrap key is preserved. Use the same library mounts. Mangarr validates the backup and saves a safety backup before restoring. Finish active downloads and permission repairs before restarting.</p>
    {status.data?.pending ? <><p role="status">Restore scheduled. Restart the container when current work is finished.</p><button className="btn" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel scheduled restore</button></> : <>
      <input type="file" aria-label="Backup ZIP file" accept=".zip" onChange={e => { setFile(e.target.files?.[0] || null); setConfirmed(false); }} />
      <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> Replace my database with this backup at the next restart.</label>
      <button className="btn" disabled={!file || !confirmed || restore.isPending || status.isPending || status.isError} onClick={() => restore.mutate()}>{restore.isPending ? "Validating backup…" : "Validate and schedule restore"}</button>
    </>}
    <ErrorNotice error={restore.error || cancel.error || status.error} />
  </section>;
}
