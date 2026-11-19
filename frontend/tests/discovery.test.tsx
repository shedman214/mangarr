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
  year: 2026,
  status: "releasing",
  genres: ["Adventure"],
  description: "<p>A story about exploring a new world.</p>",
  release_date: "2026-11-18",
  read_title: null,
};

test("shows both panels and adds a recommendation through the series dialog", async () => {
  const recommendation = {
    ...release, provider_id: "200", title: "Recommended title",
    release_date: null, read_title: "A recent read",
  };
  const calls = fakeApi((method, path) => {
    if (path === "/discovery/hidden") return { body: [] };
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
    if (path === "/discovery/hidden") return { body: [] };
    if (path === "/discovery/releases") return { body: [release] };
    if (path === "/discovery/recommendations") return { body: { state: "needs_kavita", items: [] } };
  });
  renderWithClient(<Discovery />);
  expect(await screen.findByRole("button", { name: "Add New title" })).toBeVisible();
  expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
});

test("shows manga details and reveals more titles in each panel independently", async () => {
  const releases = Array.from({ length: 13 }, (_, index) => ({
    ...release, provider_id: String(100 + index), title: `Release ${index + 1}`,
  }));
  const recommendations = releases.map((item, index) => ({
    ...item, provider_id: String(200 + index), title: `Pick ${index + 1}`,
    release_date: null, read_title: "A recent read",
  }));
  const calls = fakeApi((_, path) => {
    if (path === "/discovery/hidden") return { body: [] };
    if (path === "/discovery/releases") return { body: releases };
    if (path === "/discovery/recommendations") return { body: { state: "ready", items: recommendations } };
  });
  renderWithClient(<Discovery />);

  const newPanel = within(screen.getByRole("region", { name: "New releases" }));
  const kavitaPanel = within(screen.getByRole("region", { name: "Picked for you" }));
  const moreReleases = await newPanel.findByRole("button", { name: "Show more" });
  const morePicks = await kavitaPanel.findByRole("button", { name: "Show more" });

  expect(newPanel.getAllByRole("listitem")).toHaveLength(12);
  expect(kavitaPanel.getAllByRole("listitem")).toHaveLength(12);
  await userEvent.click(morePicks);
  expect(kavitaPanel.getAllByRole("listitem")).toHaveLength(13);
  expect(newPanel.getAllByRole("listitem")).toHaveLength(12);
  expect(kavitaPanel.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();

  await userEvent.click(moreReleases);
  const link = newPanel.getByRole("link", { name: /Release 13 details on AniList/ });
  expect(link).toHaveAttribute("href", "https://anilist.co/manga/112/");
  const lastRelease = within(link.closest("li")!);
  expect(lastRelease.getByText("(2026)")).toBeVisible();
  expect(lastRelease.getByText("releasing")).toBeVisible();
  expect(lastRelease.getByText("Adventure")).toBeVisible();
  expect(lastRelease.getByText("A story about exploring a new world.")).toBeVisible();
  expect(lastRelease.getByRole("button", { name: "Add Release 13" })).toBeVisible();
  // Show more never refetches the lists (the hidden-titles count is fetched once on its own)
  expect(calls.filter((call) => call.path !== "/discovery/hidden")).toHaveLength(2);
});

const pick = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
  ...release, provider_id: id, title, release_date: null, read_title: "Btooom!", ...extra,
});

test("picks explain why: finished, percent read and rating", async () => {
  fakeApi((_, path) => {
    if (path === "/discovery/hidden") return { body: [] };
    if (path === "/discovery/releases") return { body: [] };
    if (path === "/discovery/recommendations") {
      return { body: { state: "ready", items: [
        pick("1", "Loved follow-up", { read_detail: "finished", read_rating: 5 }),
        pick("2", "Halfway pick", { read_title: "Chainsaw Man", read_detail: "read 45%" }),
        pick("3", "Plain pick", { read_title: "Old read" }),
      ] } };
    }
  });
  renderWithClient(<Discovery />);
  expect(await screen.findByText("Because you finished Btooom! and rated it ★5")).toBeInTheDocument();
  expect(screen.getByText("Because you read 45% of Chainsaw Man")).toBeInTheDocument();
  expect(screen.getByText("Because you read Old read")).toBeInTheDocument();
});

test("filters by type, genre and status across both panels", async () => {
  fakeApi((_, path) => {
    if (path === "/discovery/hidden") return { body: [] };
    if (path === "/discovery/releases") {
      return { body: [
        { ...release, provider_id: "10", title: "Korean release", country: "KR", genres: ["Action"] },
        { ...release, provider_id: "11", title: "Japanese release", country: "JP", genres: ["Romance"] },
      ] };
    }
    if (path === "/discovery/recommendations") {
      return { body: { state: "ready", items: [
        pick("12", "Korean pick", { country: "KR", status: "finished", genres: ["Action"] }),
        pick("13", "One-shot pick", { format: "ONE_SHOT", genres: ["Action"] }),
      ] } };
    }
  });
  renderWithClient(<Discovery />);
  await screen.findByText("Korean release");
  await userEvent.selectOptions(screen.getByLabelText("Type"), "Manhwa");
  expect(screen.getByText("Korean release")).toBeInTheDocument();
  expect(screen.getByText("Korean pick")).toBeInTheDocument();
  expect(screen.queryByText("Japanese release")).toBeNull();
  expect(screen.queryByText("One-shot pick")).toBeNull();
  await userEvent.selectOptions(screen.getByLabelText("Status"), "finished");
  expect(screen.queryByText("Korean release")).toBeNull();
  expect(screen.getByText("Nothing here matches these filters.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByText("Japanese release")).toBeInTheDocument();
});

test("Not interested hides a title, Undo brings it back, Manage can unhide", async () => {
  let hidden: { provider_id: string; title: string; hidden_at: string }[] = [];
  const calls = fakeApi((method, path, body) => {
    if (path === "/discovery/hidden" && method === "GET") return { body: hidden };
    if (path === "/discovery/hidden" && method === "POST") {
      const b = body as { provider_id: string; title: string };
      hidden = [{ ...b, hidden_at: "2026-11-19T00:00:00Z" }];
      return { status: 204 };
    }
    if (path.startsWith("/discovery/hidden/") && method === "DELETE") { hidden = []; return { status: 204 }; }
    if (path === "/discovery/releases") return { body: hidden.length ? [] : [release] };
    if (path === "/discovery/recommendations") return { body: { state: "ready", items: [] } };
  });
  renderWithClient(<Discovery />);
  await screen.findByText("New title");
  await userEvent.click(screen.getByRole("button", { name: "Not interested in New title" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: /New title details/ })).toBeNull());
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ provider_id: "100", title: "New title" });
  expect(await screen.findByText(/won’t be suggested again/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "1 hidden · Manage" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("New title")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Unhide New title" }));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/discovery/hidden/100")).toBe(true));
  expect(await screen.findByRole("link", { name: /New title details/ })).toBeInTheDocument();
});

test("a list answer that still contains a hidden title does not bring it back", async () => {
  // the server's hidden list is authoritative even when a list response predates the hide
  fakeApi((_, path) => {
    if (path === "/discovery/hidden") return { body: [{ provider_id: "100", title: "New title", hidden_at: "2026-11-19T00:00:00Z" }] };
    if (path === "/discovery/releases") return { body: [release, { ...release, provider_id: "101", title: "Other title" }] };
    if (path === "/discovery/recommendations") return { body: { state: "ready", items: [] } };
  });
  renderWithClient(<Discovery />);
  expect(await screen.findByText("Other title")).toBeInTheDocument();
  await screen.findByRole("button", { name: "1 hidden · Manage" });
  expect(screen.queryByText("New title")).toBeNull();
});

test("a failed hidden-list request says so and can be retried", async () => {
  let failing = true;
  fakeApi((_, path) => {
    if (path === "/discovery/hidden") return failing ? { status: 500, body: { detail: "boom" } } : { body: [] };
    if (path === "/discovery/releases") return { body: [release] };
    if (path === "/discovery/recommendations") return { body: { state: "ready", items: [] } };
  });
  renderWithClient(<Discovery />);
  expect(await screen.findByText(/Couldn’t load hidden titles/)).toBeInTheDocument();
  failing = false;
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.queryByText(/Couldn’t load hidden titles/)).toBeNull());
});

test("filtering releases keeps the connect-Kavita message in Picked for you", async () => {
  fakeApi((_, path) => {
    if (path === "/discovery/hidden") return { body: [] };
    if (path === "/discovery/releases") return { body: [{ ...release, country: "KR" }] };
    if (path === "/discovery/recommendations") return { body: { state: "needs_kavita", items: [] } };
  });
  renderWithClient(<Discovery />);
  await screen.findByText("New title");
  await userEvent.selectOptions(screen.getByLabelText("Type"), "Manga");
  const picks = screen.getByRole("region", { name: "Picked for you" });
  expect(within(picks).getByRole("link", { name: "Settings" })).toBeInTheDocument();
  expect(within(picks).queryByText("Nothing here matches these filters.")).toBeNull();
  const releases = screen.getByRole("region", { name: "New releases" });
  expect(within(releases).getByText("Nothing here matches these filters.")).toBeInTheDocument();
});
