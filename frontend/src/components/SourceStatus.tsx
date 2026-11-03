import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNotice } from "./common";
import { SOURCE_LABELS } from "../sources";

type Observation = { state: string; last_success?: string; last_failure?: string; error?: string; retry_at?: string };
const when = (value?: string) => value ? new Date(value).toLocaleString() : "Not checked";

export function SourceStatus() {
  const status = useQuery({ queryKey: ["source-status"], queryFn: () => api.get<{ sources: Record<string, Observation> }>("/settings/sources/status"), refetchInterval: 15000 });
  const rows = Object.entries(status.data?.sources || {});
  return <div aria-label="Source status">
    <h4>Source status</h4>
    <p className="section-hint">Latest chapter checks since Mangarr restarted. A successful check can return zero chapters. Use Edit sources on a series to check its exact link without changing your library.</p>
    {rows.length === 0 && <p className="muted">No source checks recorded since startup.</p>}
    {rows.length > 0 && <table className="data-table"><thead><tr><th>Source</th><th>Latest result</th><th>Last success</th><th>Last failure / next retry</th></tr></thead><tbody>{rows.map(([name, row]) => <tr key={name}>
      <td>{SOURCE_LABELS[name] || name}</td><td>{row.error || "Check succeeded"}</td><td>{when(row.last_success)}</td><td>{row.last_failure ? when(row.last_failure) : "None"}{row.retry_at && <> / {when(row.retry_at)}</>}</td>
    </tr>)}</tbody></table>}
    <ErrorNotice error={status.error} />
  </div>;
}

export function SourceLinkCheck({ seriesId, linkId, source }: { seriesId: number; linkId: number; source: string }) {
  const check = useMutation({ mutationFn: () => api.post<{ state: string; message: string; language?: string }>(`/series/${seriesId}/sources/${linkId}/check`) });
  return <div><button className="btn sm" aria-label={`Check ${source} chapter availability`} disabled={check.isPending} onClick={() => check.mutate()}>{check.isPending ? "Checking…" : "Check chapters"}</button>
    {check.data && <p role="status" className={check.data.state === "error" ? "error-banner" : "section-hint"}>{check.data.language && <>{check.data.language}: </>}{check.data.message}</p>}
    <ErrorNotice error={check.error} />
  </div>;
}
