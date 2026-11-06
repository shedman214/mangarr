import { expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RemoveSeriesModal } from "../src/components/RemoveSeriesModal";
import { fakeApi, renderWithClient } from "./helpers";

const PREVIEW = { folders: ["/manga/Alpha"], refused: [{ path: "/manga/Shared", reason: "it is a shared folder" }] };

test("files are kept unless the box is ticked", async () => {
  const removed = vi.fn();
  const calls = fakeApi((method) => (method === "DELETE" ? { body: { count: 1, deleted: [], refused: [], problems: [] } } : undefined));
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={removed} />);
  expect(screen.getByRole("checkbox", { name: "Delete files and folders on disk" })).not.toBeChecked();
  expect(screen.getByText("Files on disk are kept.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(removed).toHaveBeenCalled());
  expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["DELETE /series/7?delete_files=false"]);
});

test("ticking it lists the folders that go and those kept, then deletes them", async () => {
  const removed = vi.fn();
  const calls = fakeApi((method, path) => path === "/series/7/delete-preview" ? { body: PREVIEW }
    : method === "DELETE" ? { body: { count: 1, deleted: ["/manga/Alpha"], refused: PREVIEW.refused, problems: [] } } : undefined);
  renderWithClient(<RemoveSeriesModal seriesIds={[7]} title="Alpha" onClose={() => {}} onRemoved={removed} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("permanently deleted");
  expect(screen.getByText("/manga/Alpha")).toBeInTheDocument();
  expect(screen.getByText("/manga/Shared")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Remove and delete files" }));
  await waitFor(() => expect(removed).toHaveBeenCalled());
  expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/series/7?delete_files=true");
});

test("bulk removal sends delete_files with every selected series", async () => {
  const calls = fakeApi((_, path) => path === "/series/editor/delete-preview" ? { body: { folders: ["/a", "/b"], refused: [] } }
    : { body: { count: 2, deleted: ["/a", "/b"], refused: [], problems: [] } });
  renderWithClient(<RemoveSeriesModal seriesIds={[1, 2]} title="2 series" onClose={() => {}} onRemoved={() => {}} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Delete files and folders on disk" }));
  await userEvent.click(await screen.findByRole("button", { name: "Remove and delete files" }));
  await waitFor(() => expect(calls.at(-1)?.body).toEqual({ series_ids: [1, 2], delete_files: true }));
});
