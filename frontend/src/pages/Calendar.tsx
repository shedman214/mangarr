import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { CalendarData, CalendarExpected, CalendarRelease } from "../api/types";
import { DownloadPill, EmptyState, ErrorNotice, Spinner, Toolbar, chapterLabel } from "../components/common";

type SearchResult = { chapters: number; series: number };

/** Missing and not already in the queue: what Search can ask for. */
const searchable = (item: CalendarRelease) => !item.downloaded && !item.download_status;

const RANGE_KEY = "calendar-range";

function loadRange(): number {
  try {
    const stored = Number(localStorage.getItem(RANGE_KEY));
    return [7, 14, 30].includes(stored) ? stored : 14;
  } catch {
    return 14;
  }
}

/** Release dates are calendar days (MangaUpdates has no time of day), so
 * they are grouped by their UTC date rather than shifted into local time. */
function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function dayLabel(key: string): string {
  const today = new Date();
  const todayKey = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()))
    .toISOString()
    .slice(0, 10);
  const diff = Math.round((Date.parse(key) - Date.parse(todayKey)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return new Date(`${key}T12:00:00Z`).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function groupByDay<T>(items: T[], dateOf: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = dayKey(dateOf(item));
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.entries()];
}

function Cover({ url }: { url: string }) {
  return url ? <img className="agenda-cover" src={url} alt="" loading="lazy" /> : <div className="agenda-cover" />;
}

function ExpectedRow({ item }: { item: CalendarExpected }) {
  const next = item.last_number !== null ? Math.floor(item.last_number) + 1 : null;
  return (
    <div className="agenda-row">
      <Cover url={item.cover_url} />
      <div className="agenda-text">
        <Link to={`/series/${item.series_id}`}>{item.series_title}</Link>
        <div className="dim">
          {next !== null ? `Ch. ${next} expected` : "Next chapter expected"} · {item.cadence_label} · last{" "}
          {dayLabel(dayKey(item.last_released_at)).toLowerCase()}
        </div>
      </div>
      {item.overdue ? (
        <span className="pill orange" title="The usual release day has passed without a new chapter">
          Late
        </span>
      ) : (
        !item.monitored && <span className="pill gray">Unmonitored</span>
      )}
    </div>
  );
}

function ReleasedRow({ item, onSearch, searching }: {
  item: CalendarRelease;
  onSearch: (ids: number[]) => void;
  searching: boolean;
}) {
  return (
    <div className="agenda-row">
      <Cover url={item.cover_url} />
      <div className="agenda-text">
        <Link to={`/series/${item.series_id}`}>{item.series_title}</Link>
        <div className="dim">
          {chapterLabel(item.number, item.volume)}
          {item.title && ` — ${item.title}`}
        </div>
      </div>
      {item.downloaded ? (
        <span className="pill green">Downloaded</span>
      ) : item.download_status ? (
        <DownloadPill item={{ status: item.download_status, progress: item.download_progress }} />
      ) : (
        <>
          <button
            className="btn sm icon-btn"
            aria-label={`Search for ${item.series_title} ${chapterLabel(item.number, item.volume)}`}
            title="Search linked sources for this chapter now"
            disabled={searching}
            onClick={() => onSearch([item.chapter_id])}
          >
            🔍
          </button>
          {item.monitored ? (
            <span className="pill orange">Missing</span>
          ) : (
            <span className="pill gray">Unmonitored</span>
          )}
        </>
      )}
    </div>
  );
}

export default function Calendar() {
  const [range, setRange] = useState(loadRange);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  // after a search: refresh for two minutes so found chapters move to Queued, then stop
  const [pollUntil, setPollUntil] = useState(0);
  useEffect(() => {
    if (!pollUntil) return;
    const timer = setTimeout(() => { setPollUntil(0); setNotice(""); }, Math.max(0, pollUntil - Date.now()));
    return () => clearTimeout(timer);
  }, [pollUntil]);
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["calendar", range],
    queryFn: () => api.get<CalendarData>(`/calendar?days_back=${range}&days_ahead=${range}`),
    // while searches run, refresh so rows move to Queued / Downloading
    refetchInterval: pollUntil ? 5000 : false,
  });
  const search = useMutation({
    mutationFn: (chapterIds: number[]) => api.post<SearchResult>("/calendar/search", { chapter_ids: chapterIds }),
    onSuccess: (res) => {
      setNotice(res.chapters === 0
        ? "Nothing to search: those chapters are already downloaded or queued."
        : `Searching for ${res.chapters} chapter${res.chapters === 1 ? "" : "s"} in ${res.series} series. `
          + "Found chapters move to Queued here and appear in Activity.");
      setPollUntil(res.chapters ? Date.now() + 120_000 : 0);
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const q = query.trim().toLowerCase();
  const named = <T extends { series_title: string }>(items: T[]) =>
    q ? items.filter((item) => item.series_title.toLowerCase().includes(q)) : items;
  const released = data ? groupByDay(named(data.released), (r) => r.released_at) : [];
  // follows the name search, so "all missing" means all missing that are shown
  const missingIds = data ? named(data.released).filter(searchable).map((r) => r.chapter_id) : [];
  const expected = data ? groupByDay(named(data.expected), (e) => e.expected_at) : [];

  return (
    <>
      <Toolbar title="Calendar">
        <button
          className="btn"
          title="Search linked sources now for every missing chapter shown below"
          disabled={missingIds.length === 0 || search.isPending}
          onClick={() => search.mutate(missingIds)}
        >
          {search.isPending ? "Searching…" : `🔍 Search all missing (${missingIds.length})`}
        </button>
        <input
          type="search"
          className="toolbar-search"
          placeholder="Search by name…"
          aria-label="Search calendar by name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          value={range}
          aria-label="Date range"
          onChange={(e) => {
            const next = Number(e.target.value);
            setRange(next);
            try {
              localStorage.setItem(RANGE_KEY, String(next));
            } catch {
              /* storage unavailable */
            }
          }}
        >
          <option value={7}>± 1 week</option>
          <option value={14}>± 2 weeks</option>
          <option value={30}>± 30 days</option>
        </select>
      </Toolbar>
      <div className="content calendar-page">
        <ErrorNotice error={error} retry={() => void refetch()} />
        <ErrorNotice error={search.error} />
        {notice && <p className="section-hint" role="status">{notice}</p>}
        {isLoading ? (
          <Spinner />
        ) : data && data.released.length === 0 && data.expected.length === 0 ? (
          <EmptyState
            icon="◷"
            title="No releases to show"
            hint="Release dates come from MangaUpdates. Series linked to MangaUpdates show up here once they have a few releases."
          />
        ) : (
          data && (
            <div className="calendar-columns">
              <section>
                <h2 className="calendar-heading">Upcoming</h2>
                <p className="section-hint">
                  Predicted from each ongoing series&apos; usual release rhythm, so treat the day as an
                  estimate.
                </p>
                {expected.length === 0 && (
                  <p className="dim">{q ? `Nothing expected matches “${query.trim()}”.` : "Nothing expected in this range."}</p>
                )}
                {expected.map(([day, items]) => (
                  <div className="agenda-day" key={day}>
                    <h3>{dayLabel(day)}</h3>
                    {items.map((item) => (
                      <ExpectedRow key={item.series_id} item={item} />
                    ))}
                  </div>
                ))}
              </section>
              <section>
                <h2 className="calendar-heading">Recently released</h2>
                <p className="section-hint">Chapter release dates reported to MangaUpdates.</p>
                {released.length === 0 && (
                  <p className="dim">{q ? `No releases match “${query.trim()}”.` : "No releases in this range."}</p>
                )}
                {released.map(([day, items]) => (
                  <div className="agenda-day" key={day}>
                    <h3>{dayLabel(day)}</h3>
                    {items.map((item) => (
                      <ReleasedRow key={item.chapter_id} item={item}
                        onSearch={(ids) => search.mutate(ids)} searching={search.isPending} />
                    ))}
                  </div>
                ))}
              </section>
            </div>
          )
        )}
      </div>
    </>
  );
}
