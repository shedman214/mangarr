import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddSeries from "../src/pages/AddSeries";
import { fakeApi, renderWithClient } from "./helpers";

const result = (provider: string, provider_id: string, title: string) => ({
  provider, provider_id, title, english_title: "", alt_titles: [], description: "", status: "releasing",
  year: 2025, cover_url: "", genres: [], total_chapters: null, total_volumes: null,
  in_library: false, in_library_series_id: null,
});

test("a MangaBaka result is labelled and added by its MangaBaka id", async () => {
  const calls = fakeApi((method, path) => {
    if (path === "/rootfolders") return { body: [{ id: 3, path: "/manga" }] };
    if (path.startsWith("/search/metadata")) {
      // the same number on both providers must not collide
      return { body: [result("mangaupdates", "362153", "Ka'er Huaite"), result("mangabaka", "362153", "Dungeon Crawler Carl")] };
    }
    if (path === "/library/folder-preview") {
      return { body: { folder_name: "Dungeon Crawler Carl", path: "/manga/Dungeon Crawler Carl", exists: false, matched: false, default_folder_name: "Dungeon Crawler Carl" } };
    }
    if (method === "POST" && path === "/series") return { status: 201, body: { id: 9 } };
    return undefined;
  });
  renderWithClient(<AddSeries />);
  await userEvent.type(screen.getByPlaceholderText(/Search MangaUpdates \(and MangaBaka\)/), "Dungeon Crawler Carl");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("Dungeon Crawler Carl")).toBeInTheDocument();
  expect(screen.getByText("Ka'er Huaite")).toBeInTheDocument();
  expect(screen.getAllByText("From MangaBaka")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Add Dungeon Crawler Carl" }));
  // the modal's button (the result row carries the same name)
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Add Dungeon Crawler Carl" })).toHaveLength(2));
  const buttons = screen.getAllByRole("button", { name: "Add Dungeon Crawler Carl" });
  await userEvent.click(buttons.find((b) => b.tagName === "BUTTON")!);
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/series")).toBe(true));
  const add = calls.find((c) => c.method === "POST" && c.path === "/series")!.body as Record<string, unknown>;
  expect(add.mangabaka_id).toBe(362153);
  expect(add).not.toHaveProperty("mangaupdates_id");
  expect(add.root_folder_id).toBe(3);
});
