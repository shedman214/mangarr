import { useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNotice, Modal } from "./common";

interface Preview {
  folders: string[];
  refused: { path: string; reason: string }[];
}

export interface RemoveResult {
  count: number;
  deleted: string[];
  refused: { path: string; reason: string }[];
  problems: string[];
}

/** Remove one series (seriesIds of length 1, from its page) or several (Library
 * editor). "Delete files and folders on disk" is off by default; ticking it
 * lists exactly which folders go, and which are kept and why. */
export function RemoveSeriesModal({ seriesIds, title, onClose, onRemoved, children }: {
  seriesIds: number[];
  title: string;
  children?: ReactNode;
  onClose: () => void;
  onRemoved: (result: RemoveResult) => void;
}) {
  const [deleteFiles, setDeleteFiles] = useState(false);
  // folder deletion that failed after the series was removed: shown before closing
  const [failed, setFailed] = useState<RemoveResult | null>(null);
  const single = seriesIds.length === 1;
  const preview = useQuery({
    queryKey: ["delete-preview", seriesIds],
    queryFn: () => single
      ? api.get<Preview>(`/series/${seriesIds[0]}/delete-preview`)
      : api.post<Preview>("/series/editor/delete-preview", { series_ids: seriesIds }),
    enabled: deleteFiles,
    gcTime: 0, // a re-tick always shows a fresh list, never a cached one
  });
  // only a preview that finished fetching may be acted on
  const ready = !deleteFiles || (preview.isSuccess && preview.fetchStatus === "idle");
  const remove = useMutation({
    // the folders shown are the only ones the server may delete: if its fresh
    // plan differs it refuses (409) and nothing is removed
    mutationFn: () => api.post<RemoveResult>("/series/editor/delete", {
      series_ids: seriesIds,
      delete_files: deleteFiles,
      ...(deleteFiles ? { confirmed_folders: preview.data?.folders ?? [] } : {}),
    }),
    onSuccess: (result) => (result.problems.length > 0 ? setFailed(result) : onRemoved(result)),
  });
  const folders = ready ? (preview.data?.folders ?? []) : [];

  if (failed) {
    return (
      <Modal title={`Removed ${title}`} onClose={() => onRemoved(failed)}>
        <div className="remove-files-preview">
          <p role="alert" className="remove-files-warning">
            Removed from Mangarr, but some folders could not be deleted:
          </p>
          <ul>{failed.problems.map((p, i) => <li key={i}><code>{p}</code></li>)}</ul>
          {failed.deleted.length > 0 && (
            <>
              <p className="section-hint">Deleted:</p>
              <ul>{failed.deleted.map((p, i) => <li key={i}><code>{p}</code></li>)}</ul>
            </>
          )}
        </div>
        <div className="modal-actions">
          <button className="btn" onClick={() => onRemoved(failed)}>Close</button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title={`Remove ${title}`} onClose={() => { if (!remove.isPending) onClose(); }}>
      <p>Remove {single ? `“${title}”` : title} from Mangarr?</p>
      {children}
      <label className="remove-files-option">
        <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} />
        Delete files and folders on disk
      </label>
      {!deleteFiles ? (
        <p className="section-hint">Files on disk are kept.</p>
      ) : !ready ? (
        preview.isError ? null : <p className="section-hint">Checking folders…</p>
      ) : (
        <div className="remove-files-preview">
          {folders.length > 0 ? (
            <>
              <p className="remove-files-warning" role="alert">
                These folders and everything in them are permanently deleted:
              </p>
              <ul>{folders.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
            </>
          ) : (
            <p className="section-hint">No folder of {single ? "this series" : "these series"} can be deleted; files stay on disk.</p>
          )}
          {ready && (preview.data?.refused.length ?? 0) > 0 && (
            <>
              <p className="section-hint">Kept on disk:</p>
              <ul>{preview.data!.refused.map((r, i) => <li key={i}><code>{r.path}</code> — {r.reason}</li>)}</ul>
            </>
          )}
        </div>
      )}
      <ErrorNotice error={preview.error ?? remove.error} />
      <div className="modal-actions">
        <button className="btn" disabled={remove.isPending} onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={remove.isPending || !ready}
          onClick={() => remove.mutate()}>
          {remove.isPending ? "Removing…" : deleteFiles && folders.length > 0 ? "Remove and delete files" : "Remove"}
        </button>
      </div>
    </Modal>
  );
}
