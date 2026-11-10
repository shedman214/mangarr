import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Wanted from "../src/pages/Wanted";
import { fakeApi, renderWithClient } from "./helpers";

const item = (id: number, title: string) => ({ chapter_id: id, series_id: id, series_title: title, cover_url: "", number: id, volume: null, title: "" });

test("searching Wanted asks the server for matching chapters, from page 1", async () => {
  const calls = fakeApi((_, path) => {
    if (path.includes("q=beta")) return { body: { items: [item(2, "Beta Road")], total: 1 } };
    if (path.includes("q=zzz")) return { body: { items: [], total: 0 } };
    return { body: { items: [item(1, "Alpha Quest"), item(2, "Beta Road")], total: 250 } };
  });
  renderWithClient(<Wanted />);
  await screen.findByText("Alpha Quest");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search wanted by name" }), "beta");
  expect(await screen.findByText("Beta Road")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText("Alpha Quest")).toBeNull());
  expect(calls.at(-1)?.path).toBe("/wanted/page?limit=100&offset=0&q=beta");
  await userEvent.clear(screen.getByRole("searchbox", { name: "Search wanted by name" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "Search wanted by name" }), "zzz");
  expect(await screen.findByText("No wanted chapters match “zzz”.")).toBeInTheDocument();
});
