import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RootFolder } from "../api/types";
import { AddSeriesModal, type AddTarget } from "../components/AddSeriesModal";
import { ErrorNotice, Modal, Spinner, Toolbar, statusPill } from "../components/common";
import { sanitizeDescription } from "../sanitize";

type DiscoveryTitle = AddTarget & {
  cover_url: string;
  year: number | null;
  status: string;
  genres: string[];
  description: string;
  release_date: string | null;
  read_title: string | null;
  read_detail?: string | null;
  read_rating?: number | null;
  format?: string;
  country?: string;
};

type Hidden = { provider_id: string; title: string; hidden_at: string };

type Filters = { type: string; genre: string; status: string };
const NO_FILTERS: Filters = { type: "", genre: "", status: "" };

/** Manga / Manhwa / Manhua / One-shot from AniList's format and country of origin. */
export function titleType(item: Pick<DiscoveryTitle, "format" | "country">): string {
  if (item.format === "ONE_SHOT") return "One-shot";
  if (item.country === "KR") return "Manhwa";
  if (item.country === "CN" || item.country === "TW") return "Manhua";
  return "Manga";
}

function matches(item: DiscoveryTitle, filters: Filters): boolean {
  return (!filters.type || titleType(item) === filters.type)
    && (!filters.genre || item.genres.includes(filters.genre))
    && (!filters.status || item.status === filters.status);
}

/** "Because you finished X and rated it ★5" / "read 45% of X" / "read X". */
export function pickReason(item: DiscoveryTitle): string | null {
  if (!item.read_title) return null;
  const detail = item.read_detail;
  const read = detail === "finished" ? `finished ${item.read_title}`
    : detail?.startsWith("read ") ? `${detail} of ${item.read_title}`
      : `read ${item.read_title}`;
  return `Because you ${read}${item.read_rating ? ` and rated it ★${item.read_rating}` : ""}`;
}

type Recommendations = {
  items: DiscoveryTitle[];
  state: "ready" | "needs_kavita" | "no_history" | "no_matches";
};

const releaseDate = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", timeZone: "UTC",
});
const PAGE_SIZE = 12;

function DiscoveryRow({ item, onAdd, onHide }: {
  item: DiscoveryTitle;
  onAdd: (item: DiscoveryTitle) => void;
  onHide: (item: DiscoveryTitle) => void;
}) {
  const reason = pickReason(item);
  const detailsUrl = `https://anilist.co/manga/${encodeURIComponent(item.provider_id)}/`;
  return (
    <li className="discovery-row">
      {item.cover_url
        ? <img className="discovery-cover" src={item.cover_url} alt="" loading="lazy" />
        : <span className="discovery-cover" aria-hidden="true" />}
      <div className="discovery-details">
        <h3>
          <a className="discovery-title" href={detailsUrl} target="_blank" rel="noopener noreferrer"
            aria-label={`${item.title} details on AniList (opens in a new tab)`}>
            {item.title}
          </a>
          {item.year && <span className="dim"> ({item.year})</span>}
        </h3>
        {item.alt_titles[0] && <p className="dim">{item.alt_titles[0]}</p>}
        <div className="discovery-meta">
          {item.status !== "unknown" && (
            <span className={`pill ${statusPill[item.status] ?? "gray"}`}>{item.status.replaceAll("_", " ")}</span>
          )}
          {item.genres.slice(0, 4).map((genre) => <span className="tag" key={genre}>{genre}</span>)}
          {item.release_date && (
            <time className="dim" dateTime={item.release_date}>
              {releaseDate.format(new Date(item.release_date))}
            </time>
          )}
        </div>
        {item.description && (
          <div className="discovery-description dim"
            dangerouslySetInnerHTML={{ __html: sanitizeDescription(item.description) }} />
        )}
        {reason && <p className="discovery-reason">{reason}</p>}
      </div>
      <div className="discovery-actions">
        <button type="button" className="btn primary discovery-add"
          aria-label={`Add ${item.title}`} onClick={() => onAdd(item)}>
          + Add
        </button>
        <button type="button" className="btn discovery-hide"
          aria-label={`Not interested in ${item.title}`} onClick={() => onHide(item)}>
          ✕ Not interested
        </button>
      </div>
    </li>
  );
}

function DiscoveryPanel({
  id, title, description, items = [], pending, error, retry, empty, onAdd, onHide, filtered,
}: {
  id: string;
  title: string;
  description: string;
  items?: DiscoveryTitle[];
  pending: boolean;
  error: unknown;
  retry: () => void;
  empty: ReactNode;
  onAdd: (item: DiscoveryTitle) => void;
  onHide: (item: DiscoveryTitle) => void;
  filtered: boolean;
}) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const listId = `${id}-titles`;
  return (
    <section className="discovery-panel" aria-labelledby={id}>
      <header className="discovery-panel-header">
        <h2 id={id}>{title}</h2>
        <p className="dim">{description}</p>
      </header>
      <ErrorNotice error={error} retry={retry} />
      {pending && <Spinner />}
      {!pending && !error && items.length === 0 && (
        <p className="discovery-empty dim">{filtered ? "Nothing here matches these filters." : empty}</p>
      )}
      {items.length > 0 && (
        <ul className="discovery-list" id={listId}>
          {items.slice(0, visibleCount).map((item) => (
            <DiscoveryRow key={item.provider_id} item={item} onAdd={onAdd} onHide={onHide} />
          ))}
        </ul>
      )}
      {items.length > PAGE_SIZE && (
        <footer className="discovery-pagination">
          <span className="dim" role="status">Showing {Math.min(visibleCount, items.length)} of {items.length}</span>
          {visibleCount < items.length && (
            <button type="button" className="btn" aria-controls={listId}
              onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}>
              Show more
            </button>
          )}
        </footer>
      )}
    </section>
  );
}

const recommendationEmpty: Record<Recommendations["state"], ReactNode> = {
  ready: "No new matches for your recent reads.",
  needs_kavita: <>Connect Kavita in <Link to="/settings">Settings</Link> to get suggestions based on your reading.</>,
  no_history: "Read a series in Kavita to start getting recommendations.",
  no_matches: "We couldn’t match your recent Kavita reads to AniList yet.",
};

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (value: string) => void;
}) {
  return (
    <label className="discovery-filter">
      <span className="dim">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Any</option>
        {options.map((option) => <option key={option} value={option}>{option.replaceAll("_", " ")}</option>)}
      </select>
    </label>
  );
}

const TYPES = ["Manga", "Manhwa", "Manhua", "One-shot"];

export default function Discovery() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState<DiscoveryTitle | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [lastHidden, setLastHidden] = useState<DiscoveryTitle | null>(null);
  const [managing, setManaging] = useState(false);
  const releases = useQuery({
    queryKey: ["discovery", "releases"],
    queryFn: () => api.get<DiscoveryTitle[]>("/discovery/releases"),
    gcTime: 0,
    retry: false,
  });
  const recommendations = useQuery({
    queryKey: ["discovery", "recommendations"],
    queryFn: () => api.get<Recommendations>("/discovery/recommendations"),
    gcTime: 0,
    retry: false,
  });
  const hidden = useQuery({
    queryKey: ["discovery", "hidden"],
    queryFn: () => api.get<Hidden[]>("/discovery/hidden"),
  });
  const roots = useQuery({
    queryKey: ["rootfolders"],
    queryFn: () => api.get<RootFolder[]>("/rootfolders"),
    enabled: !!adding,
  });

  const removeFromLists = (providerId: string) => {
    queryClient.setQueryData<DiscoveryTitle[]>(["discovery", "releases"],
      (items) => items?.filter((item) => item.provider_id !== providerId));
    queryClient.setQueryData<Recommendations>(["discovery", "recommendations"],
      (data) => data && { ...data, items: data.items.filter((item) => item.provider_id !== providerId) });
  };
  const hide = useMutation({
    mutationFn: (item: DiscoveryTitle) =>
      api.post("/discovery/hidden", { provider_id: item.provider_id, title: item.title }),
    onSuccess: (_, item) => {
      removeFromLists(item.provider_id);
      setLastHidden(item);
      void queryClient.invalidateQueries({ queryKey: ["discovery", "hidden"] });
    },
  });
  const unhide = useMutation({
    mutationFn: (providerId: string) => api.del(`/discovery/hidden/${encodeURIComponent(providerId)}`),
    onSuccess: () => {
      setLastHidden(null);
      void queryClient.invalidateQueries({ queryKey: ["discovery"] });
    },
  });

  const all = useMemo(
    () => [...(releases.data ?? []), ...(recommendations.data?.items ?? [])],
    [releases.data, recommendations.data],
  );
  const genres = useMemo(() => [...new Set(all.flatMap((item) => item.genres))].sort(), [all]);
  const statuses = useMemo(
    () => [...new Set(all.map((item) => item.status).filter((status) => status !== "unknown"))].sort(),
    [all],
  );
  const filtered = filters.type !== "" || filters.genre !== "" || filters.status !== "";
  const filterKey = `${filters.type}|${filters.genre}|${filters.status}`;
  const shownReleases = releases.data?.filter((item) => matches(item, filters));
  const shownPicks = recommendations.data?.items.filter((item) => matches(item, filters));
  const hiddenCount = hidden.data?.length ?? 0;

  return (
    <>
      <Toolbar title="Discovery" />
      <main className="content">
        <div className="discovery-filters" role="group" aria-label="Filter Discovery">
          <FilterSelect label="Type" value={filters.type} options={TYPES}
            onChange={(type) => setFilters((current) => ({ ...current, type }))} />
          <FilterSelect label="Genre" value={filters.genre} options={genres}
            onChange={(genre) => setFilters((current) => ({ ...current, genre }))} />
          <FilterSelect label="Status" value={filters.status} options={statuses}
            onChange={(status) => setFilters((current) => ({ ...current, status }))} />
          {filtered && (
            <button type="button" className="btn" onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>
          )}
          {hiddenCount > 0 && (
            <button type="button" className="btn discovery-manage" onClick={() => setManaging(true)}>
              {hiddenCount} hidden · Manage
            </button>
          )}
        </div>
        <ErrorNotice error={hide.error ?? unhide.error} />
        <div className="discovery-panels">
          <DiscoveryPanel key={`releases-${filterKey}`} id="new-releases" title="New releases"
            description="Manga debuting this month"
            items={shownReleases} pending={releases.isPending} error={releases.error}
            retry={() => void releases.refetch()} empty="No new releases outside your library found this month."
            onAdd={setAdding} onHide={(item) => hide.mutate(item)} filtered={filtered} />
          <DiscoveryPanel key={`picks-${filterKey}`} id="picked-for-you" title="Picked for you"
            description="Based on what you read in Kavita"
            items={shownPicks} pending={recommendations.isPending} error={recommendations.error}
            retry={() => void recommendations.refetch()}
            empty={recommendationEmpty[recommendations.data?.state ?? "ready"]}
            onAdd={setAdding} onHide={(item) => hide.mutate(item)} filtered={filtered} />
        </div>
        {lastHidden && (
          <div className="discovery-toast" role="status">
            Hidden “{lastHidden.title}” — it won’t be suggested again.
            <button type="button" className="btn" onClick={() => unhide.mutate(lastHidden.provider_id)}>Undo</button>
            <button type="button" className="btn" aria-label="Dismiss" onClick={() => setLastHidden(null)}>✕</button>
          </div>
        )}
        {managing && (
          <Modal title="Hidden from Discovery" onClose={() => setManaging(false)}>
            {hidden.data?.length ? (
              <ul className="discovery-hidden-list">
                {hidden.data.map((row) => (
                  <li key={row.provider_id}>
                    <span>{row.title || `AniList #${row.provider_id}`}</span>
                    <button type="button" className="btn" aria-label={`Unhide ${row.title}`}
                      onClick={() => unhide.mutate(row.provider_id)}>
                      Unhide
                    </button>
                  </li>
                ))}
              </ul>
            ) : <p className="dim">Nothing is hidden.</p>}
          </Modal>
        )}
        {adding && roots.data?.length ? (
          <AddSeriesModal result={adding} rootFolders={roots.data} onClose={() => setAdding(null)} />
        ) : null}
        {adding && !roots.data?.length && (
          <Modal title={`Add — ${adding.title}`} onClose={() => setAdding(null)}>
            {roots.isPending && <Spinner />}
            <ErrorNotice error={roots.error} retry={() => void roots.refetch()} />
            {roots.data?.length === 0 && (
              <p>Add a root folder in <Link to="/settings">Settings</Link> before adding a series.</p>
            )}
          </Modal>
        )}
      </main>
    </>
  );
}
