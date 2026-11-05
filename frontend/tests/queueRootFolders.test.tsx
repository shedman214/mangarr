import { expect, test, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Activity from "../src/pages/Activity";
import { fakeApi, renderWithClient } from "./helpers";

const item = (id: number, root: number | null, status = "queued") => ({
  id, status, kind: "direct", title: `Chapter ${id}`, series_title: "Example", source_name: "mangadex",
  series_id: id, chapter_id: id, progress: 0, error: "", created_at: "",
  root_folder_id: root, root_folder_path: root === 1 ? "/manga" : root === 2 ? "/dojuin" : "",
});

test("root filter combines with title search and clears hidden selections", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: [item(1, 1), item(2, 2), item(3, null)] }
    : { body: { moved: 1, skipped: 0 } });
  renderWithClient(<Activity />);
  await userEvent.click(await screen.findByRole("checkbox", { name: "Select Chapter 1" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Root folder" }), "2");
  expect(screen.queryByText("Chapter 1")).not.toBeInTheDocument();
  expect(screen.getByText("Chapter 2")).toBeInTheDocument();
  expect(screen.getByText("Root folder: /dojuin")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download now (0)" })).toBeDisabled();
  await userEvent.type(screen.getByRole("searchbox", { name: "Find downloads" }), "Chapter 1");
  expect(screen.getByText("No matching downloads")).toBeInTheDocument();
  await userEvent.clear(screen.getByRole("searchbox", { name: "Find downloads" }));
  await userEvent.click(screen.getByRole("button", { name: "Select all matches" }));
  await userEvent.click(screen.getByRole("button", { name: "Download now (1)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("1 queued chapter moved");
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ ids: [2] });
});

test("failed actions affect only the selected root folder", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const calls = fakeApi((method, path) => method === "GET"
    ? { body: [item(1, 1, "failed"), item(2, 2, "failed"), item(3, 2)] }
    : { body: path === "/queue/remove-failed" ? { removed: 1 } : {} });
  renderWithClient(<Activity />);
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Root folder" }), "2");
  await userEvent.click(screen.getByRole("button", { name: "Retry failed matches (1)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("1 download queued for retry");
  await userEvent.click(screen.getByRole("button", { name: "Remove failed matches (1)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("1 failed job removed");
  expect(calls.filter(c => c.method === "POST")).toEqual([
    { method: "POST", path: "/queue/2/retry", body: undefined },
    { method: "POST", path: "/queue/remove-failed", body: { ids: [2] } },
  ]);
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("1 matching failed jobs"));
});

test("root filtering selects all matches across pages and resets pagination", async () => {
  const calls = fakeApi(method => method === "GET"
    ? { body: [...Array.from({ length: 101 }, (_, i) => item(i + 1, 1)), item(102, 2)] }
    : { body: { moved: 101, skipped: 0 } });
  renderWithClient(<Activity />);
  await userEvent.click(await screen.findByRole("button", { name: "Last" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Root folder" }), "1");
  expect(screen.getByText("Showing 1–100 of 101")).toBeInTheDocument();
  expect(screen.queryByText("Chapter 102")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Select all matches" }));
  await userEvent.click(screen.getByRole("button", { name: "Download now (101)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("101 queued chapters moved");
  expect((calls.find(c => c.method === "POST")?.body as { ids: number[] }).ids).toHaveLength(101);
});

test("unassigned destinations remain visible and can be filtered", async () => {
  const calls = fakeApi(() => ({ body: [item(1, 1), item(2, null)] }));
  renderWithClient(<Activity />);
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Root folder" }), "unassigned");
  expect(screen.queryByText("Chapter 1")).not.toBeInTheDocument();
  expect(screen.getByText("Root folder: Not assigned")).toBeInTheDocument();
  expect(within(screen.getByRole("table")).getByText("Chapter 2")).toBeInTheDocument();
  expect(calls.every(c => c.method === "GET")).toBe(true);
});
