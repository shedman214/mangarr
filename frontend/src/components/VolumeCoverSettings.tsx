import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Toggle } from "./common";

interface CoversJob {
  running: boolean;
  done: number;
  total: number;
  series_changed: number;
  volumes: number;
  failed: number;
  skipped_busy: number;
  finished_at: string | null;
}

/** Settings → Media Management → English volume covers: off by default. The
 * library-wide update only shows once the setting is on and saved. */
export function VolumeCoverSettings({ form, setForm, savedOn, dirty }: {
  form: Record<string, string>;
  setForm: (form: Record<string, string>) => void;
  savedOn: boolean;
  dirty: boolean;
}) {
  const client = useQueryClient();
  const on = form.english_volume_covers === "true";
  const { data: job } = useQuery({
    queryKey: ["volume-covers-job"],
    queryFn: () => api.get<CoversJob>("/settings/volume-covers/status"),
    enabled: savedOn,
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: () => api.post("/settings/volume-covers/update"),
    onSettled: () => void client.invalidateQueries({ queryKey: ["volume-covers-job"] }),
  });

  return <div className="volume-cover-settings" role="group" aria-label="English volume covers">
    <div className="form-row">
      <label>English volume covers</label>
      <Toggle label="English volume covers" on={on}
        onChange={(v) => setForm({ ...form, english_volume_covers: v ? "true" : "false" })} />
    </div>
    <p className="section-hint">
      Off (default). On: each volume's first chapter file gets the volume's English front cover
      from <a href="https://mangabaka.org" target="_blank" rel="noreferrer">MangaBaka</a> as its
      first image, which Kavita uses as the volume cover. Volumes without an English cover keep
      theirs. This rewrites those chapter files on disk; new downloads are covered at each
      monitor pass.
    </p>
    {savedOn && on && (<>
      <div className="form-row">
        <label>Existing library</label>
        <button className="btn" disabled={dirty || job?.running || start.isPending}
          onClick={() => {
            if (confirm("Rewrite chapter files in every series with volume numbers? Each volume's first "
              + "chapter file gets its English cover from MangaBaka.")) start.mutate();
          }}>
          {job?.running ? "Updating covers…" : "Update covers now"}
        </button>
      </div>
      <p className="section-hint" role="status">
        {start.error ? String((start.error as Error).message)
          : job?.running ? `Checked ${job.done} of ${job.total} series — ${job.volumes} cover(s) added so far.`
          : job?.finished_at ? `Last run: ${job.volumes} cover(s) added in ${job.series_changed} series`
            + (job.failed ? `, ${job.failed} could not be updated (see Logs)` : "")
            + (job.skipped_busy ? `, ${job.skipped_busy} busy series left for the next monitor pass` : "") + "."
          : "Checks every series with volume numbers, one at a time in the background, and only rewrites files whose cover is missing or changed."}
      </p>
    </>)}
  </div>;
}
