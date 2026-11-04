import { Toggle } from "./common";

/** Settings → Sources → Adult content: off by default; the user opts in. */
export function AdultContentSettings({ form, setForm }: {
  form: Record<string, string>;
  setForm: (form: Record<string, string>) => void;
}) {
  const on = form.adult_content === "true";
  return <div className="adult-content-settings" role="group" aria-label="Adult content">
    <h4>Adult content</h4>
    <div className="form-row">
      <label>Include adult content</label>
      <Toggle label="Include adult content" on={on} onChange={(v) => setForm({ ...form, adult_content: v ? "true" : "false" })} />
    </div>
    <p className="section-hint">
      Off (default): each site's normal adult and mature filtering applies. On: searches and
      downloads include adult titles where a source filters them — MangaDex adds its
      pornographic rating, FanFox and Toonily get their over-18 confirmation, and WeebCentral
      searches adult titles too. Applies as soon as you save. Only turn this on if you are an
      adult and adult content is legal where you are.
    </p>
  </div>;
}
