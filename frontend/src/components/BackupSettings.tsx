import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNotice } from "./common";

type StoredBackup = { id: string; created: number | null; version: string | null; reason: string; bytes: number };

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
    <h4>Saved backups</h4>
    <p className="section-hint">Kept in /config/backups. Mangarr also saves one automatically before an update changes the database layout (the newest 5 automatic ones are kept).</p>
    {stored.data && (stored.data.length ? <ul aria-label="Saved backups">
      {stored.data.map(b => <li key={b.id}>
        {b.created ? new Date(b.created * 1000).toLocaleString() : "Unknown date"} · {b.reason === "pre-migration" ? "Before database update" : "Manual"}{b.version ? ` · v${b.version}` : ""} · {(b.bytes / 1048576).toFixed(1)} MB{" "}
        <button className="btn" disabled={saved.isPending} onClick={() => saved.mutate(b.id)}>Download</button>
      </li>)}
    </ul> : <p>No saved backups yet.</p>)}
    <ErrorNotice error={stored.error || saved.error} />
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
