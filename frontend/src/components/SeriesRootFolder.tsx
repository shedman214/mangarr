import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { RootFolder, SeriesEditorResult } from "../api/types";
import { ErrorNotice, Modal } from "./common";

export function SeriesRootFolder({ seriesId, rootId, folderName, onChanged }: {
  seriesId: number; rootId: number | null; folderName: string; onChanged: () => void;
}) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState("");
  const [moveFiles, setMoveFiles] = useState(false);
  const [notice, setNotice] = useState("");
  const roots = useQuery({ queryKey: ["rootfolders"], queryFn: () => api.get<RootFolder[]>("/rootfolders") });
  const current = roots.data?.find(r => r.id === rootId)?.path;
  const destination = roots.data?.find(r => r.id === Number(selected))?.path;
  const save = useMutation({
    mutationFn: async () => {
      const result = await api.put<SeriesEditorResult>("/series/editor", {
        series_ids: [seriesId], root_folder_id: Number(selected), move_files: moveFiles,
      });
      if (result.problems.length) throw new Error(result.problems.map(p => p.detail).join("; "));
      return result;
    },
    onSuccess: () => {
      setEditing(false);
      setNotice("Root folder updated. Kavita will be notified if its integration is configured.");
      void client.invalidateQueries({ queryKey: ["folders", seriesId] });
      void client.invalidateQueries({ queryKey: ["queue"] });
      onChanged();
    },
  });
  return <div className="series-root-folder">
    <div className="folder-line"><strong>Root folder</strong> <code>{current ?? "Not assigned"}</code>
      <button className="btn sm" onClick={() => { setSelected(String(rootId ?? "")); setMoveFiles(false); save.reset(); setEditing(true); }}>Change root folder</button>
    </div>
    {notice && <p role="status" className="form-hint">{notice}</p>}
    <ErrorNotice error={roots.error} retry={() => void roots.refetch()} />
    {editing && <Modal title="Change root folder" onClose={() => { if (!save.isPending) setEditing(false); }}>
      <p>Choose where this manga's future downloads will be saved.</p>
      <div className="form-row"><label htmlFor="series-root-folder">Root folder</label>
        <select id="series-root-folder" value={selected} disabled={save.isPending} onChange={e => setSelected(e.target.value)}>
          <option value="" disabled>Choose a root folder</option>
          {roots.data?.map(r => <option key={r.id} value={r.id}>{r.path}</option>)}
        </select>
      </div>
      <p className="form-hint">Add more root folders in Settings. The primary series folder is kept: <code>{folderName}</code>.</p>
      {destination && <p>New destination: <code>{folderName.startsWith("/") ? folderName : `${destination.replace(/\/$/, "")}/${folderName}`}</code></p>}
      <label className="editor-check"><input type="checkbox" checked={moveFiles} disabled={save.isPending} onChange={e => setMoveFiles(e.target.checked)} />Move existing files to the new root folder</label>
      <p className="form-hint">{moveFiles ? "The primary folder moves after you save. Downloads in progress or an existing destination prevent the move; extra folders stay where they are." : "Existing files stay where they are and remain tracked. Only future downloads use the new root folder."} Kavita is notified after a successful change when configured.</p>
      <ErrorNotice error={save.error} />
      <div className="modal-actions">
        <button className="btn" disabled={save.isPending} onClick={() => setEditing(false)}>Cancel</button>
        <button className="btn primary" disabled={save.isPending || !destination || Number(selected) === rootId} onClick={() => save.mutate()}>{save.isPending ? (moveFiles ? "Moving…" : "Saving…") : "Update root folder"}</button>
      </div>
    </Modal>}
  </div>;
}
