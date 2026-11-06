import { Toggle } from "./common";

/** Settings → Media Management → ComicInfo from MangaBaka: on by default
 * (an unset value counts as on). */
export function ComicInfoSettings({ form, setForm }: {
  form: Record<string, string>;
  setForm: (form: Record<string, string>) => void;
}) {
  const on = (form.mangabaka_metadata ?? "true") === "true";
  return <>
    <div className="form-row">
      <label>ComicInfo from MangaBaka</label>
      <Toggle label="ComicInfo from MangaBaka" on={on}
        onChange={(v) => setForm({ ...form, mangabaka_metadata: v ? "true" : "false" })} />
    </div>
    <p className="section-hint">
      Each downloaded file's ComicInfo.xml carries the publisher, genres, release year and
      publication status Kavita and Komga show. On (default): writers, artists, tags, age
      rating and AniList/MangaBaka links are added too, from{" "}
      <a href="https://mangabaka.org" target="_blank" rel="noreferrer">MangaBaka</a>, looked up by
      the series&apos; AniList or MangaUpdates id. Publishers come from MangaUpdates when it lists
      them. Picked up at each series&apos; next metadata refresh; files already on disk keep
      their current ComicInfo.
    </p>
  </>;
}
