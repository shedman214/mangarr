import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { WantedItem } from "../api/types";
import { chapterLabel, EmptyState, ErrorNotice, Spinner, Toolbar } from "../components/common";

export default function Wanted() {
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  // the server searches all wanted chapters; wait for a pause in typing before asking
  const [q, setQ] = useState("");
  const MAX_SEARCH = 200; // the server's limit
  const tooLong = query.trim().length > MAX_SEARCH;
  useEffect(() => {
    const next = query.trim();
    if (next === q || next.length > MAX_SEARCH) return; // unchanged (e.g. on first load) or too long: keep the page
    const timer = setTimeout(() => { setQ(next); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [query, q]);
  const pageSize = 100;
  const { data: page, isLoading, error, refetch } = useQuery({
    queryKey: ["wanted", offset, q],
    queryFn: () => api.get<{ items: WantedItem[]; total: number }>(
      `/wanted/page?limit=${pageSize}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
    ),
    refetchInterval: 15000,
  });

  const data = page?.items;
  useEffect(() => {
    if (page && offset > 0 && offset >= page.total) setOffset(Math.max(0, Math.ceil(page.total / pageSize) - 1) * pageSize);
  }, [page, offset]);

  return (
    <>
      <Toolbar title="Wanted" className="wanted-toolbar">
        <input
          type="search"
          className="toolbar-search"
          placeholder="Search by name…"
          aria-label="Search wanted by name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="wanted-pagination" aria-label="Wanted pages">
        <button className="btn" disabled={offset === 0 || isLoading} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous</button>
        {page && <span>{page.total === 0 ? "0" : `${offset + 1}–${offset + data!.length}`} of {page.total} chapters</span>}
        <button className="btn" disabled={!page || offset + pageSize >= page.total || isLoading} onClick={() => setOffset(offset + pageSize)}>Next</button>
        </div>
      </Toolbar>
      <div className="content">
        {tooLong && <p className="section-hint" role="alert">Shorten the search to {MAX_SEARCH} characters or fewer.</p>}
        <ErrorNotice error={error} retry={() => void refetch()} />
        {isLoading ? (
          <Spinner />
        ) : !data && error ? null : !data || data.length === 0 ? (
          q ? (
            <EmptyState icon="🔍" title="No matches" hint={`No wanted chapters match “${q}”.`} />
          ) : (
            <EmptyState
              icon="✔"
              title="Nothing wanted"
              hint="All monitored chapters are downloaded."
            />
          )
        ) : (
          <table className="data-table card-table wanted-table">
            <thead>
              <tr>
                <th style={{ width: 280 }}>Series</th>
                <th style={{ width: 140 }}>Chapter</th>
                <th>Title</th>
              </tr>
            </thead>
            <tbody>
              {data.map((w) => (
                <tr key={w.chapter_id}>
                  <td className="cell-series">
                    <Link to={`/series/${w.series_id}`} style={{ color: "var(--info)" }}>
                      {w.series_title}
                    </Link>
                  </td>
                  <td className="cell-chapter">{chapterLabel(w.number, w.volume)}</td>
                  <td className="cell-wtitle" style={{ color: "var(--text-dim)" }}>{w.title || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
