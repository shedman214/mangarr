import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RootFolder } from "../api/types";
import { AddSeriesModal, type AddTarget } from "../components/AddSeriesModal";
import { ErrorNotice, Modal, Spinner, Toolbar } from "../components/common";

type DiscoveryTitle = AddTarget & {
  cover_url: string;
  release_date: string | null;
  read_title: string | null;
};

type Recommendations = {
  items: DiscoveryTitle[];
  state: "ready" | "needs_kavita" | "no_history" | "no_matches";
};

const releaseDate = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", timeZone: "UTC",
});

function DiscoveryPanel({
  id, title, description, items = [], pending, error, retry, empty, onAdd,
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
}) {
  return (
    <section className="discovery-panel" aria-labelledby={id}>
      <header className="discovery-panel-header">
        <h2 id={id}>{title}</h2>
        <p className="dim">{description}</p>
      </header>
      <ErrorNotice error={error} retry={retry} />
      {pending && <Spinner />}
      {!pending && !error && items.length === 0 && <p className="discovery-empty dim">{empty}</p>}
      {items.length > 0 && (
        <ul className="discovery-list">
          {items.map((item) => (
            <li key={item.provider_id} className="discovery-row">
              {item.cover_url
                ? <img className="discovery-cover" src={item.cover_url} alt="" loading="lazy" />
                : <span className="discovery-cover" aria-hidden="true" />}
              <div className="discovery-details">
                <h3>{item.title}</h3>
                {item.release_date && (
                  <time className="dim" dateTime={item.release_date}>
                    {releaseDate.format(new Date(item.release_date))}
                  </time>
                )}
                {item.read_title && <p className="dim">Because you read {item.read_title}</p>}
              </div>
              <button type="button" className="btn primary discovery-add"
                aria-label={`Add ${item.title}`} onClick={() => onAdd(item)}>
                + Add
              </button>
            </li>
          ))}
        </ul>
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

export default function Discovery() {
  const [adding, setAdding] = useState<DiscoveryTitle | null>(null);
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
  const roots = useQuery({
    queryKey: ["rootfolders"],
    queryFn: () => api.get<RootFolder[]>("/rootfolders"),
    enabled: !!adding,
  });

  return (
    <>
      <Toolbar title="Discovery" />
      <main className="content">
        <div className="discovery-panels">
          <DiscoveryPanel id="new-releases" title="New releases" description="Manga debuting this month"
            items={releases.data} pending={releases.isPending} error={releases.error}
            retry={() => void releases.refetch()} empty="No new releases outside your library found this month."
            onAdd={setAdding} />
          <DiscoveryPanel id="picked-for-you" title="Picked for you" description="Based on what you read in Kavita"
            items={recommendations.data?.items} pending={recommendations.isPending} error={recommendations.error}
            retry={() => void recommendations.refetch()}
            empty={recommendationEmpty[recommendations.data?.state ?? "ready"]} onAdd={setAdding} />
        </div>
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
