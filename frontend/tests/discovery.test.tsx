import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { Route, Routes } from "react-router-dom";
import Discovery from "../src/pages/Discovery";
import { fakeApi, renderWithClient } from "./helpers";

const release = {
  provider: "anilist",
  provider_id: "100",
  title: "New title",
  english_title: "",
  alt_titles: [],
  cover_url: "",
  release_date: "2026-11-18",
  read_title: null,
};

test("shows both panels and adds a recommendation through the series dialog", async () => {
  const recommendation = {
    ...release, provider_id: "200", title: "Recommended title",
    release_date: null, read_title: "A recent read",
  };
  const calls = fakeApi((method, path) => {
    if (path === "/discovery/releases") return { body: [release] };
    if (path === "/discovery/recommendations") {
      return { body: { state: "ready", items: [recommendation] } };
    }
    if (path === "/rootfolders") return { body: [{ id: 1, path: "/manga" }] };
    if (path === "/library/folder-preview") return { body: { folder_name: "Recommended title", matched: false } };
    if (method === "POST" && path === "/series") return { body: { id: 50 } };
  });
  renderWithClient(
    <Routes>
      <Route path="/" element={<Discovery />} />
      <Route path="/series/50" element={<h1>Series page</h1>} />
    </Routes>,
  );

  const releases = screen.getByRole("region", { name: "New releases" });
  const picked = screen.getByRole("region", { name: "Picked for you" });
  expect(await within(releases).findByRole("button", { name: "Add New title" })).toBeVisible();
  await userEvent.click(await within(picked).findByRole("button", { name: "Add Recommended title" }));
  expect(within(picked).getByText("Because you read A recent read")).toBeVisible();
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", {
    name: "Add Recommended title",
  })).toBeVisible());
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add Recommended title" }));
  expect(await screen.findByRole("heading", { name: "Series page" })).toBeVisible();
  expect(calls).toContainEqual({
    method: "POST", path: "/series", body: expect.objectContaining({ anilist_id: 200, root_folder_id: 1 }),
  });
});

test("new releases work independently when Kavita needs to be connected", async () => {
  fakeApi((_, path) => {
    if (path === "/discovery/releases") return { body: [release] };
    if (path === "/discovery/recommendations") return { body: { state: "needs_kavita", items: [] } };
  });
  renderWithClient(<Discovery />);
  expect(await screen.findByRole("button", { name: "Add New title" })).toBeVisible();
  expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
});
