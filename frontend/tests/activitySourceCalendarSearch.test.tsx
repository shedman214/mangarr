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

const today = new Date().toISOString();
const release = (id: number, title: string) => ({
  series_id: id, series_title: title, cover_url: "", chapter_id: id, number: id, volume: null, title: "",
  released_at: today, downloaded: false, monitored: true,
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
