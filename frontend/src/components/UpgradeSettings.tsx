import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { sourceLabel } from "../sources";
import { ErrorNotice, Toggle } from "./common";

type Summary = { series: number; upgrades_on: number; matching: number };

/** Sonarr-style "Upgrades Allowed" / "Upgrade Until", using the source priority
 * list above as the ranking. Defaults for new series, plus "apply to all". */
export function UpgradeSettings({ form, setForm, dirty, sources }: {
  form: Record<string, string>;
  setForm: (form: Record<string, string>) => void;
  dirty: boolean;
  sources: string[];
}) {
  const client = useQueryClient();
  const summary = useQuery({ queryKey: ["upgrade-summary"], queryFn: () => api.get<Summary>("/settings/upgrades") });
  const apply = useMutation({
    mutationFn: () => api.post<Summary & { changed: number }>("/settings/upgrades/apply"),
    onSuccess: (result) => client.setQueryData(["upgrade-summary"], result),
  });
  // "N match the saved settings" changes when the settings are saved.
  useEffect(() => {
    if (!dirty) void client.invalidateQueries({ queryKey: ["upgrade-summary"] });
  }, [dirty, client]);
  const allowed = form.upgrades_allowed === "true";
  const until = form.upgrade_until ?? "";
  const data = summary.data;
  const confirmApply = () => {
    const what = allowed
      ? `Turn on upgrades for all ${data?.series ?? ""} series, until ${until ? sourceLabel(until) : "the top source"}?`
      : `Turn off upgrades for all ${data?.series ?? ""} series?`;
    if (confirm(`${what} Per-series upgrade settings are replaced.${allowed ? " Upgrades are then queued gradually during monitor passes (at most 25 per series per pass)." : ""}`)) apply.mutate();
  };
  return <div className="upgrade-settings" role="group" aria-label="Upgrades">
    <h4>Upgrades</h4>
    <div className="form-row">
      <label>Upgrades allowed</label>
      <Toggle label="Upgrades allowed" on={allowed} onChange={(on) => setForm({ ...form, upgrades_allowed: on ? "true" : "false" })} />
    </div>
    <p className="section-hint">If disabled, chapters already downloaded are not upgraded. If enabled, Mangarr downloads a chapter again when a source higher in the list above has it, or when its file is from a blocked group.</p>
    <div className="form-row">
      <label htmlFor="upgrade-until">Upgrade until</label>
      <select id="upgrade-until" value={until} disabled={!allowed} onChange={(e) => setForm({ ...form, upgrade_until: e.target.value })}>
        <option value="">The top source in the list</option>
        {sources.map((name) => <option key={name} value={name}>{sourceLabel(name)} or better</option>)}
      </select>
    </div>
    <p className="section-hint">Once a chapter comes from this source or a better one, Mangarr no longer downloads it again. Only chapter files Mangarr downloaded itself are replaced; your own files, torrent imports and volume archives are never touched.</p>
    <p className="section-hint">These are the defaults for newly added series. Each series can still be changed under its Automation settings.</p>
    <div className="form-row">
      <label>Existing series</label>
      <button className="btn" disabled={dirty || apply.isPending || !data?.series} onClick={confirmApply}>
        {apply.isPending ? "Applying…" : "Apply to all series"}
      </button>
    </div>
    {dirty && <p className="section-hint">Save your changes first; existing series get the saved settings.</p>}
    {data && <p role="status" className="section-hint">
      {apply.data ? `Updated ${apply.data.changed} series. ` : ""}
      {data.upgrades_on} of {data.series} series have upgrades on; {data.matching} match the saved settings.
    </p>}
    <ErrorNotice error={summary.error || apply.error} />
  </div>;
}
