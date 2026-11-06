import { afterEach, expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Library from "../src/pages/Library";
import { fakeApi, renderWithClient } from "./helpers";

const series = (id: number, title: string, added_at: string) => ({
  id, title, added_at, english_title: "", alt_titles: "", cover_url: `/covers/${id}.jpg`, monitored: true, status: "ongoing",
  downloaded_count: 1, chapter_count: 1, special_count: 0, special_downloaded_count: 0,
});

// the server returns title order
const LIBRARY = [
  series(1, "Alpha", "2026-03-01T00:00:00Z"),
  series(2, "Bravo", "2026-11-01T00:00:00Z"),
  series(3, "Charlie", "2025-01-01T00:00:00Z"),
];

const titles = () => screen.getAllByRole("img").map((img) => img.getAttribute("alt"));

afterEach(() => localStorage.clear());

test("library sorts by recently added in both directions and remembers the choice", async () => {
  fakeApi((_, path) => (path === "/series" ? { body: LIBRARY } : undefined));
  const view = renderWithClient(<Library />);
  await screen.findByAltText("Alpha");
  expect(titles()).toEqual(["Alpha", "Bravo", "Charlie"]);

  const sort = screen.getByLabelText("Sort library");
  await userEvent.selectOptions(sort, "added-desc");
  expect(titles()).toEqual(["Bravo", "Alpha", "Charlie"]);
  await userEvent.selectOptions(sort, "added-asc");
  expect(titles()).toEqual(["Charlie", "Alpha", "Bravo"]);
  expect(localStorage.getItem("library-sort")).toBe("added-asc");

  view.unmount();
  renderWithClient(<Library />);
  await screen.findByAltText("Alpha");
  expect(screen.getByLabelText("Sort library")).toHaveValue("added-asc");
  expect(titles()).toEqual(["Charlie", "Alpha", "Bravo"]);
});

test("an unknown saved sort falls back to title order", async () => {
  localStorage.setItem("library-sort", "bogus");
  fakeApi((_, path) => (path === "/series" ? { body: LIBRARY } : undefined));
  renderWithClient(<Library />);
  await screen.findByAltText("Alpha");
  expect(screen.getByLabelText("Sort library")).toHaveValue("title");
  expect(titles()).toEqual(["Alpha", "Bravo", "Charlie"]);
});
