import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Activity from "../src/pages/Activity";
import Calendar from "../src/pages/Calendar";
import { fakeApi, renderWithClient } from "./helpers";

const queued = (id: number, source: string, title: string) => ({
  id, status: "queued", kind: "direct", title, series_title: title.split(" - ")[0], source_name: source,
  series_id: id, chapter_id: id, progress: 0, error: "", created_at: "", root_folder_id: 1, root_folder_path: "/manga",
});

test("Activity filters by source, combined with the title search", async () => {
  fakeApi((_, path) => (path.startsWith("/queue") ? { body: [
    queued(1, "mangafire", "Alpha - Chapter 1"), queued(2, "mangadex", "Beta - Chapter 2"),
    queued(3, "mangafire", "Gamma - Chapter 3"),
  ] } : { body: [] }));
  renderWithClient(<Activity />);
  const source = await screen.findByRole("combobox", { name: "Source" });
  expect([...source.querySelectorAll("option")].map((o) => o.value)).toEqual(["", "mangadex", "mangafire"]);
  await userEvent.selectOptions(source, "mangafire");
  expect(screen.getByText("Alpha - Chapter 1")).toBeInTheDocument();
  expect(screen.queryByText("Beta - Chapter 2")).toBeNull();
  await userEvent.type(screen.getByRole("searchbox", { name: "Find downloads" }), "Gamma");
  expect(screen.queryByText("Alpha - Chapter 1")).toBeNull();
  expect(screen.getByText("Gamma - Chapter 3")).toBeInTheDocument();
  // bulk actions follow the filters
  expect(screen.getByRole("button", { name: "Select all matches" })).toBeEnabled();
});

test("the title search also matches a source's display name", async () => {
  fakeApi((_, path) => (path.startsWith("/queue") ? { body: [
    queued(1, "weebcentral", "Alpha - Chapter 1"), queued(2, "mangadex", "Beta - Chapter 2"),
  ] } : { body: [] }));
  renderWithClient(<Activity />);
  const search = await screen.findByRole("searchbox", { name: "Find downloads" });
  const label = (await import("../src/sources")).sourceLabel("weebcentral");
  await userEvent.type(search, label);
  expect(screen.getByText("Alpha - Chapter 1")).toBeInTheDocument();
  expect(screen.queryByText("Beta - Chapter 2")).toBeNull();
});

const today = new Date().toISOString();
const release = (id: number, title: string, extra: Record<string, unknown> = {}) => ({
  series_id: id, series_title: title, cover_url: "", chapter_id: id, number: id, volume: null, title: "",
  released_at: today, downloaded: false, monitored: true, download_status: "", download_progress: 0, ...extra,
});
const expected = (id: number, title: string) => ({
  series_id: id, series_title: title, cover_url: "", expected_at: today, cadence_days: 7, cadence_label: "weekly",
  last_released_at: today, last_number: 1, overdue: false, monitored: true,
});

test("Calendar search filters both lists by name", async () => {
  fakeApi((_, path) => (path.startsWith("/calendar") ? { body: {
    released: [release(1, "Alpha Story"), release(2, "Beta Tale")],
    expected: [expected(3, "Alpha Returns"), expected(4, "Delta")],
  } } : { body: [] }));
  renderWithClient(<Calendar />);
  await screen.findByText("Beta Tale");
  await userEvent.type(screen.getByRole("searchbox", { name: "Search calendar by name" }), "alpha");
  expect(screen.getByText("Alpha Story")).toBeInTheDocument();
  expect(screen.getByText("Alpha Returns")).toBeInTheDocument();
  expect(screen.queryByText("Beta Tale")).toBeNull();
  expect(screen.queryByText("Delta")).toBeNull();
  await userEvent.clear(screen.getByRole("searchbox", { name: "Search calendar by name" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search calendar by name" }), "zzz");
  expect(screen.getByText("Nothing expected matches “zzz”.")).toBeInTheDocument();
  expect(screen.getByText("No releases match “zzz”.")).toBeInTheDocument();
});

test("Calendar: Search all missing sends every shown missing chapter, and a row searches just its own", async () => {
  const calls = fakeApi((method, path) => method === "POST" && path === "/calendar/search"
    ? { status: 202, body: { chapters: 1, series: 1 } }
    : path.startsWith("/calendar") ? { body: {
      released: [
        release(1, "Alpha Story"),
        release(2, "Beta Tale"),
        release(3, "Gamma Days", { downloaded: true }),
        release(4, "Delta Road", { download_status: "downloading", download_progress: 0.18 }),
      ],
      expected: [],
    } } : { body: [] });
  renderWithClient(<Calendar />);
  await screen.findByText("Beta Tale");
  // already downloading: shows it, and offers no search
  expect(screen.getByText("Downloading 18%")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Search for Delta Road/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Search for Gamma Days/ })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Search for Beta Tale Ch. 2" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Searching for 1 chapter in 1 series");
  expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ chapter_ids: [2] });

  await userEvent.click(screen.getByRole("button", { name: /Search all missing \(2\)/ }));
  expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ chapter_ids: [1, 2] });
});

test("Calendar: Search all missing follows the name search", async () => {
  const calls = fakeApi((method, path) => method === "POST" ? { status: 202, body: { chapters: 1, series: 1 } }
    : path.startsWith("/calendar") ? { body: { released: [release(1, "Alpha Story"), release(2, "Beta Tale")], expected: [] } }
    : { body: [] });
  renderWithClient(<Calendar />);
  await screen.findByText("Beta Tale");
  await userEvent.type(screen.getByRole("searchbox", { name: "Search calendar by name" }), "beta");
  await userEvent.click(screen.getByRole("button", { name: /Search all missing \(1\)/ }));
  expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ chapter_ids: [2] });
});
