import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddSeries from "../src/pages/AddSeries";
import { fakeApi, renderWithClient } from "./helpers";

const found = {
  provider: "mangaupdates", provider_id: "72792030757", title: "Dorohedoro", english_title: "", alt_titles: [],
  description: "", status: "finished", year: 2000, cover_url: "", genres: [], total_chapters: 167,
  total_volumes: 23, in_library: false, in_library_series_id: null,
};

test("the id hints search for that id, and a typed link is sent as is", async () => {
  const calls = fakeApi((_, path) => {
    if (path === "/rootfolders") return { body: [{ id: 1, path: "/manga" }] };
    if (path.startsWith("/search/metadata")) return { body: [found] };
  });
  renderWithClient(<AddSeries />);
  expect(screen.getByText(/paste a MangaBaka, AniList, MyAnimeList or MangaUpdates link/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "mangabaka:1331" }));
  expect(await screen.findByText("Dorohedoro")).toBeInTheDocument();
  expect(calls.some((c) => c.path === "/search/metadata?q=mangabaka%3A1331")).toBe(true);
  expect(screen.getByPlaceholderText(/Search by title/)).toHaveValue("mangabaka:1331");

  const box = screen.getByPlaceholderText(/Search by title/);
  await userEvent.clear(box);
  await userEvent.type(box, "https://anilist.co/manga/159441/Pick-Me-Up/");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByText("Dorohedoro");
  expect(calls.some((c) => c.path === "/search/metadata?q=" + encodeURIComponent("https://anilist.co/manga/159441/Pick-Me-Up/"))).toBe(true);
});
