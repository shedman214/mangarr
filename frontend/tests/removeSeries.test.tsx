import { expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RemoveSeriesModal } from "../src/components/RemoveSeriesModal";
import { fakeApi, renderWithClient } from "./helpers";

const PREVIEW = { folders: ["/manga/Alpha"], refused: [{ path: "/manga/Shared", reason: "it is a shared folder" }] };

test("files are kept unless the box is ticked", async () => {
  const removed = vi.fn();
  const calls = fakeApi((method) => (method === "POST" ? { body: { count: 1, deleted: [], refused: [], problems: [] } } : undefined));
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={removed} />);
  expect(screen.getByRole("checkbox", { name: "Delete files and folders on disk" })).not.toBeChecked();
  expect(screen.getByText("Files on disk are kept.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(removed).toHaveBeenCalled());
  expect(calls.map((c) => [c.method, c.path, c.body])).toEqual([["POST", "/series/editor/delete", { series_ids: [7], delete_files: false }]]);
});

test("ticking it lists the folders that go and those kept, then deletes them", async () => {
  const removed = vi.fn();
  const calls = fakeApi((method, path) => path === "/series/7/delete-preview" ? { body: PREVIEW }
    : method === "POST" ? { body: { count: 1, deleted: ["/manga/Alpha"], refused: PREVIEW.refused, problems: [] } } : undefined);
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={removed} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("permanently deleted");
  expect(screen.getByText("/manga/Alpha")).toBeInTheDocument();
  expect(screen.getByText("/manga/Shared")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Remove and delete files" }));
  await waitFor(() => expect(removed).toHaveBeenCalled());
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ series_ids: [7], delete_files: true, confirmed_folders: ["/manga/Alpha"] });
});

test("bulk removal sends delete_files with every selected series", async () => {
  const calls = fakeApi((_, path) => path === "/series/editor/delete-preview" ? { body: { folders: ["/a", "/b"], refused: [] } }
    : { body: { count: 2, deleted: ["/a", "/b"], refused: [], problems: [] } });
  renderWithClient(<RemoveSeriesModal seriesIds={[1, 2]} title="2 series" onClose={() => {}} onRemoved={() => {}} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  await userEvent.click(await screen.findByRole("button", { name: "Remove and delete files" }));
  await waitFor(() => expect(calls.at(-1)?.body).toEqual({ series_ids: [1, 2], delete_files: true, confirmed_folders: ["/a", "/b"] }));
});

test("re-ticking waits for a fresh preview, and a failed one blocks removal", async () => {
  let fail = false;
  fakeApi((_, path) => path === "/series/7/delete-preview"
    ? (fail ? { status: 500, body: { detail: "disk unavailable" } } : { body: PREVIEW }) : undefined);
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={() => {}} />);
  const box = screen.getByRole("checkbox", { name: "Delete files and folders on disk" });
  await userEvent.click(box);
  expect(await screen.findByText("/manga/Alpha")).toBeInTheDocument();
  await userEvent.click(box);
  fail = true;
  await userEvent.click(box);
  expect(await screen.findByText(/disk unavailable/)).toBeInTheDocument();
  expect(screen.queryByText("/manga/Alpha")).toBeNull(); // the earlier list is not reused
  expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
});

test("folders that could not be deleted are shown before closing", async () => {
  const removed = vi.fn();
  fakeApi((method, path) => path === "/series/7/delete-preview" ? { body: PREVIEW }
    : method === "POST" ? { body: { count: 1, deleted: [], refused: [], problems: ["/manga/Alpha: Permission denied"] } } : undefined);
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={removed} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  await userEvent.click(await screen.findByRole("button", { name: "Remove and delete files" }));
  expect(await screen.findByText("/manga/Alpha: Permission denied")).toBeInTheDocument();
  expect(removed).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(removed).toHaveBeenCalledTimes(1);
});

test("a shared folder listed twice in a bulk preview renders once per entry without key clashes", async () => {
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  fakeApi(() => ({ body: { folders: [], refused: [{ path: "/s", reason: "it is a shared folder" }, { path: "/s", reason: "it is a shared folder" }] } }));
  renderWithClient(<RemoveSeriesModal seriesIds={[1, 2]} title="2 series" onClose={() => {}} onRemoved={() => {}} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  expect(await screen.findAllByText("/s")).toHaveLength(2);
  expect(errors.mock.calls.some((c) => String(c[0]).includes("same key"))).toBe(false);
});

test("the dialog stays open while the removal runs", async () => {
  const closed = vi.fn();
  let finish: (v: Response) => void = () => {};
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { finish = r; })));
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={closed} onRemoved={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(await screen.findByRole("button", { name: "Removing…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await userEvent.keyboard("{Escape}");
  expect(closed).not.toHaveBeenCalled();
  finish(new Response(JSON.stringify({ count: 1, deleted: [], refused: [], problems: [] }), { headers: { "Content-Type": "application/json" } }));
});

test("a changed folder list is refused and shown", async () => {
  fakeApi((method, path) => path === "/series/7/delete-preview" ? { body: PREVIEW }
    : method === "POST" ? { status: 409, body: { detail: "The folders to delete changed since the preview" } } : undefined);
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={() => {}} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  await userEvent.click(await screen.findByRole("button", { name: "Remove and delete files" }));
  expect(await screen.findByText(/changed since the preview/)).toBeInTheDocument();
});
