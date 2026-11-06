import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { HistoryItem, QueueItem } from "../api/types";
import { EmptyState, ErrorNotice, SelectionCheckbox, Spinner, statusPill, Toolbar } from "../components/common";

/** A queue or history title opens its series page, like Sonarr's series links. */
function SeriesLink({ seriesId, title }: { seriesId: number | null; title: string }) {
  return seriesId ? <Link to={`/series/${seriesId}`} className="series-link">{title}</Link> : <>{title}</>;
}

/** Removal stops downloads, and qBittorrent also deletes a torrent's downloaded data. */
function confirmRemove(items: QueueItem[]): boolean {
  const what = items.length === 1 ? `"${items[0].title || items[0].series_title}"` : `${items.length} items`;
  const torrents = items.some((item) => item.kind === "torrent")
    ? " Torrents are also removed from qBittorrent together with their downloaded data." : "";
  return confirm(`Remove ${what} from the queue?${torrents}`);
}

function QueueMoveHandle({ item, busy, onMove, onTarget }: {
  item: QueueItem; busy: boolean; onMove: (id: number, before?: number) => void;
  onTarget: (id: number | null) => void;
}) {
  const drag = useRef<{ x: number; y: number; moved: boolean; target: number | null } | null>(null);
  const ignoreClick = useRef(false);
  return <button type="button" className="btn queue-drag" disabled={busy}
    aria-label={`Move ${item.title || item.series_title} to top`}
    title="Drag to reorder, or click to download next"
    onClick={event => {
      if (ignoreClick.current && event.detail > 0) { ignoreClick.current = false; return; }
      onMove(item.id);
    }}
    onPointerDown={event => {
      if (event.button !== 0 || busy) return;
      ignoreClick.current = false;
      drag.current = { x: event.clientX, y: event.clientY, moved: false, target: null };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    }}
    onPointerMove={event => {
      const current = drag.current;
      if (!current || busy) return;
      if (!current.moved && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 6) return;
      current.moved = true;
      const row = document.elementFromPoint(event.clientX, event.clientY)?.closest("tr[data-queue-id]");
      const target = row ? Number(row.getAttribute("data-queue-id")) : null;
      current.target = target !== item.id ? target : null;
      onTarget(current.target);
    }}
    onPointerUp={event => {
      const current = drag.current;
      drag.current = null;
      if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      onTarget(null);
      if (current?.moved) {
        ignoreClick.current = true;
        if (current.target !== null && !busy) onMove(item.id, current.target);
      }
    }}
    onPointerCancel={() => { drag.current = null; ignoreClick.current = true; onTarget(null); }}>↕</button>;
}

function Queue() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [retryNotice, setRetryNotice] = useState("");
  const [filter, setFilter] = useState("");
  const [rootFilter, setRootFilter] = useState("");
  const [page, setPage] = useState(0);
  const [dropTarget, setDropTarget] = useState<number | null>(null);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["queue"],
    queryFn: () => api.get<QueueItem[]>("/queue"),
    refetchInterval: 2000,
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/queue/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["queue"] }),
  });

  const retry = useMutation({
    mutationFn: (id: number) => api.post(`/queue/${id}/retry`),
    onMutate: () => { setRetryNotice(""); retryMany.reset(); },
    onSuccess: () => {
      setRetryNotice("Download queued for retry.");
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const retryMany = useMutation({
    mutationFn: async (items: QueueItem[]) => {
      const retried: number[] = [];
      const failed: { title: string; error: string }[] = [];
      for (const item of items) {
        try {
          await api.post(`/queue/${item.id}/retry`);
          retried.push(item.id);
        } catch (error) {
          failed.push({ title: item.title || item.series_title, error: error instanceof Error ? error.message : String(error) });
        }
      }
      return { retried, failed };
    },
    onMutate: () => { setRetryNotice(""); retry.reset(); },
    onSuccess: ({ retried, failed }) => {
      setSelected(previous => new Set([...previous].filter(id => !retried.includes(id))));
      setRetryNotice(`${retried.length} download${retried.length === 1 ? "" : "s"} queued for retry.${failed.length ? ` ${failed.length} could not be retried.` : ""}`);
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const removeSelected = useMutation({
    mutationFn: (ids: number[]) => api.post("/queue/remove", { ids }),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const removeFailed = useMutation({
    mutationFn: (ids: number[]) => api.post<{ removed: number }>("/queue/remove-failed", { ids }),
    onMutate: () => { setRetryNotice(""); retry.reset(); retryMany.reset(); },
    onSuccess: ({ removed }, ids) => {
      setSelected(previous => new Set([...previous].filter(id => !ids.includes(id))));
      setRetryNotice(`${removed} failed job${removed === 1 ? "" : "s"} removed. No files were deleted.${removed < ids.length ? " Jobs that are no longer failed were left alone." : ""}`);
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const moveQueue = useMutation({
    mutationFn: (body: { ids: number[]; before_id?: number; to_end?: boolean }) =>
      api.post<{ moved: number; skipped: number }>("/queue/move", body),
    onMutate: () => setRetryNotice(""),
    onSuccess: ({ moved, skipped }, body) => {
      setSelected(previous => new Set([...previous].filter(id => !body.ids.includes(id))));
      setRetryNotice(`${moved} queued chapter${moved === 1 ? "" : "s"} moved ${body.to_end ? "to the bottom" : body.before_id ? "to the new position" : "to the front"}. The current download will finish first.${skipped ? ` ${skipped} no longer queued or unsupported item${skipped === 1 ? " was" : "s were"} left alone.` : ""}`);
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  if (isLoading) return <Spinner />;
  if (error && !data) return <ErrorNotice error={error} retry={() => void refetch()} />;
  if (!data || data.length === 0)
    return <><ErrorNotice error={error} retry={() => void refetch()} /><EmptyState icon="⇅" title="Queue is empty" hint="Grabbed releases will appear here." /></>;

  const rootKey = (item: QueueItem) => item.root_folder_id != null ? String(item.root_folder_id) : "unassigned";
  const roots = [...new Map(data.filter(item => item.root_folder_id != null)
    .map(item => [String(item.root_folder_id), item.root_folder_path] as const)).entries()]
    .sort((a, b) => a[1].localeCompare(b[1]));
  const hasFilter = Boolean(filter.trim() || rootFilter);
  const matches = data.filter(item => (!rootFilter || rootKey(item) === rootFilter)
    && `${item.title} ${item.series_title} ${item.source_name}`.toLowerCase().includes(filter.trim().toLowerCase()));
  // Bulk actions must never include downloads hidden by the current filters.
  const selectedVisible = matches.filter((item) => selected.has(item.id)).map((item) => item.id);
  const failedItems = matches.filter(item => item.status === "failed");
  const selectedFailed = failedItems.filter(item => selected.has(item.id));
  const selectedQueued = matches.filter(item => selected.has(item.id) && item.kind === "direct" && item.status === "queued");
  const retryBusy = retry.isPending || retryMany.isPending || removeFailed.isPending || removeSelected.isPending || remove.isPending || moveQueue.isPending;
  const allSelected = matches.length > 0 && matches.every(item => selected.has(item.id));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(matches.length / 100) - 1));
  const pageItems = matches.slice(currentPage * 100, (currentPage + 1) * 100);
  const selectMatches = () => setSelected(previous => {
    const next = new Set(previous);
    for (const item of matches) { if (allSelected) next.delete(item.id); else next.add(item.id); }
    return next;
  });

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <>
      <ErrorNotice error={error} retry={() => void refetch()} />
      <ErrorNotice error={remove.error || retry.error || retryMany.error || removeSelected.error || removeFailed.error || moveQueue.error} />
      {retryNotice && <p role="status">{retryNotice}</p>}
      {retryMany.data?.failed.map((item, index) => <p role="alert" key={index}>Could not retry {item.title}: {item.error}</p>)}
      <div className="queue-filter">
        <label htmlFor="queue-filter">Find downloads</label>
        <input id="queue-filter" type="search" placeholder="Manga title or source…" value={filter}
          onChange={event => { setFilter(event.target.value); setPage(0); setSelected(new Set()); }} />
        <label htmlFor="queue-root-folder">Root folder</label>
        <select id="queue-root-folder" value={rootFilter}
          onChange={event => { setRootFilter(event.target.value); setPage(0); setSelected(new Set()); }}>
          <option value="">All root folders</option>
          {roots.map(([id, path]) => <option key={id} value={id}>{path}</option>)}
          {rootFilter && rootFilter !== "unassigned" && !roots.some(([id]) => id === rootFilter)
            && <option value={rootFilter}>Selected root folder (no downloads)</option>}
          <option value="unassigned">Not assigned</option>
        </select>
      </div>
      <p className="section-hint">Root folder is the series’ library destination, including where torrent downloads are imported. Filters apply to selection, retries and removal.</p>
      <p className="section-hint">Select queued chapters and click Download now to move them to the front. The current download finishes first. Drag the ↕ handle to change a chapter’s position, or click it to move that chapter to the top. Torrent downloads are managed in qBittorrent.</p>
      <div className="table-actions queue-bulk-actions" role="region" aria-label="Download actions">
        <button
          className="btn"
          disabled={!matches.length || retryBusy}
          onClick={selectMatches}
        >
          {allSelected ? "Clear selected" : hasFilter ? "Select all matches" : "Select all"}
        </button>
        <span>{selectedVisible.length} selected</span>
        <button className="btn primary" disabled={!selectedQueued.length || retryBusy}
          onClick={() => moveQueue.mutate({ ids: selectedQueued.map(item => item.id) })}>
          {moveQueue.isPending ? "Moving…" : `Download now (${selectedQueued.length})`}
        </button>
        <button className="btn" disabled={!selectedQueued.length || retryBusy}
          onClick={() => moveQueue.mutate({ ids: selectedQueued.map(item => item.id), to_end: true })}>Move to bottom</button>
        <button className="btn primary" disabled={!selectedFailed.length || retryBusy}
          onClick={() => retryMany.mutate(selectedFailed)}>
          {retryMany.isPending ? "Retrying…" : `Retry selected (${selectedFailed.length})`}
        </button>
        <button className="btn" disabled={!failedItems.length || retryBusy}
          onClick={() => retryMany.mutate(failedItems)}>
          {hasFilter ? "Retry failed matches" : "Retry all failed"} ({failedItems.length})
        </button>
        <button className="btn danger" disabled={!failedItems.length || retryBusy}
          onClick={() => {
            if (confirm(`Remove ${failedItems.length}${hasFilter ? " matching" : ""} failed jobs from the queue? No files will be deleted, and active downloads will stay in the queue. Monitored chapters may be downloaded again automatically.`)) {
              removeFailed.mutate(failedItems.map(item => item.id));
            }
          }}>
          {removeFailed.isPending ? "Removing failed…" : `${hasFilter ? "Remove failed matches" : "Remove failed"} (${failedItems.length})`}
        </button>
        <button
          className="btn danger"
          disabled={selectedVisible.length === 0 || removeSelected.isPending || retryBusy}
          onClick={() => {
            if (confirmRemove(matches.filter((item) => selected.has(item.id)))) removeSelected.mutate(selectedVisible);
          }}
        >
          {removeSelected.isPending ? "Removing…" : "Remove selected"}
        </button>
      </div>
      {matches.length > 100 && <p className="section-hint">Select all includes every matching download across all pages.</p>}
      <div className="queue-pagination" aria-label="Queue pages">
        <span>{matches.length ? `Showing ${currentPage * 100 + 1}–${Math.min((currentPage + 1) * 100, matches.length)} of ${matches.length}` : "No matching downloads"}</span>
        {matches.length > 100 && <>
        <button className="btn" disabled={currentPage === 0} onClick={() => setPage(0)}>First</button>
        <button className="btn" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button>
        <button className="btn" disabled={(currentPage + 1) * 100 >= matches.length} onClick={() => setPage(currentPage + 1)}>Next</button>
        <button className="btn" disabled={(currentPage + 1) * 100 >= matches.length} onClick={() => setPage(Math.ceil(matches.length / 100) - 1)}>Last</button>
        </>}
      </div>
      <table className="data-table card-table queue-table">
        <thead>
          <tr>
            <th style={{ width: 34 }}>
              <input
                type="checkbox"
                aria-label="Select all downloads"
                checked={allSelected}
                onChange={selectMatches}
              />
            </th>
            <th>Title</th>
            <th style={{ width: 110 }}>Source</th>
            <th style={{ width: 90 }}>Type</th>
            <th style={{ width: 110 }}>Status</th>
            <th style={{ width: 180 }}>Progress</th>
            <th style={{ width: 132 }}></th>
          </tr>
        </thead>
        <tbody>
          {pageItems.map((item) => (
            <tr key={item.id} className={dropTarget === item.id ? "queue-drop-target" : undefined}
              data-queue-id={item.kind === "direct" && item.status === "queued" ? item.id : undefined}>
              <td className="cell-select">
                <SelectionCheckbox
                  label={`Select ${item.title || item.series_title}`}
                  checked={selected.has(item.id)}
                  onChange={() => toggle(item.id)}
                />
              </td>
              <td className="cell-qtitle">
                <SeriesLink seriesId={item.series_id} title={item.title || item.series_title} />
                <div className="queue-destination">Root folder: {item.root_folder_path || "Not assigned"}</div>
                {item.status === "failed" && item.error && (
                  <div style={{ color: "var(--danger)", fontSize: "0.85em", marginTop: 2 }}>
                    {item.error}
                  </div>
                )}
              </td>
              <td className="cell-source">{item.source_name}</td>
              <td className="cell-type">
                <span className={`pill ${item.kind === "torrent" ? "orange" : "blue"}`}>
                  {item.kind}
                </span>
              </td>
              <td className="cell-status">
                <span className={`pill ${statusPill[item.status] ?? "gray"}`}>{item.status}</span>
              </td>
              <td className="cell-progress">
                <div className="progress-bar">
                  <div style={{ width: `${Math.round(item.progress * 100)}%` }} />
                  <span>{Math.round(item.progress * 100)}%</span>
                </div>
              </td>
              <td className="cell-remove">
                <div className="queue-actions">
                  {item.kind === "direct" && item.status === "queued" && <QueueMoveHandle
                    item={item} busy={retryBusy} onTarget={setDropTarget}
                    onMove={(id, before) => moveQueue.mutate({ ids: [id], ...(before === undefined ? {} : { before_id: before }) })} />}
                  {item.status === "failed" && (
                    <button
                      className="btn"
                      title="Retry download"
                      aria-label={`Retry ${item.title || item.series_title}`}
                      disabled={retryBusy}
                      onClick={() => retry.mutate(item.id)}
                    >
                      {retry.isPending && retry.variables === item.id ? "Retrying…" : "Retry"}
                    </button>
                  )}
                  <button
                    className="btn icon-btn"
                    title="Remove"
                    aria-label={`Remove ${item.title || item.series_title}`}
                    disabled={remove.isPending || retryBusy}
                    onClick={() => confirmRemove([item]) && remove.mutate(item.id)}
                  >
                    ✕
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function History() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["history"],
    queryFn: () => api.get<HistoryItem[]>("/history"),
    refetchInterval: 5000,
  });

  if (isLoading) return <Spinner />;
  if (error && !data) return <ErrorNotice error={error} retry={() => void refetch()} />;
  if (!data || data.length === 0) return <><ErrorNotice error={error} retry={() => void refetch()} /><EmptyState icon="🕘" title="No history yet" /></>;

  return (
    <>
    <ErrorNotice error={error} retry={() => void refetch()} />
    <table className="data-table card-table history-table">
      <thead>
        <tr>
          <th style={{ width: 100 }}>Event</th>
          <th style={{ width: 220 }}>Series</th>
          <th>Detail</th>
          <th style={{ width: 110 }}>Source</th>
          <th style={{ width: 170 }}>Date</th>
        </tr>
      </thead>
      <tbody>
        {data.map((ev) => (
          <tr key={ev.id}>
            <td className="cell-event">
              <span className={`pill ${statusPill[ev.event] ?? "gray"}`}>{ev.event}</span>
            </td>
            <td className="cell-series"><SeriesLink seriesId={ev.series_id} title={ev.series_title} /></td>
            <td className="cell-detail" style={{ color: "var(--text-dim)", wordBreak: "break-all" }}>{ev.detail}</td>
            <td className="cell-source">{ev.source_name}</td>
            <td className="cell-date" style={{ color: "var(--text-dim)" }}>
              {new Date(ev.created_at).toLocaleString()}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </>
  );
}

export default function Activity() {
  const [tab, setTab] = useState<"queue" | "history">("queue");
  return (
    <>
      <Toolbar title="Activity">
        <button className={`btn${tab === "queue" ? " primary" : ""}`} onClick={() => setTab("queue")}>
          Queue
        </button>
        <button
          className={`btn${tab === "history" ? " primary" : ""}`}
          onClick={() => setTab("history")}
        >
          History
        </button>
      </Toolbar>
      <div className="content">{tab === "queue" ? <Queue /> : <History />}</div>
    </>
  );
}
