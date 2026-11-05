import { expect, test, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Activity from "../src/pages/Activity";
import { fakeApi, renderWithClient } from "./helpers";

const item = (id: number, status = "queued", kind = "direct") => ({
  id, status, kind, title: `Chapter ${id}`, series_title: "New manga", source_name: "mangadex",
  series_id: 1, chapter_id: id, progress: 0, error: "", created_at: "",
});

test("Download now only promotes selected queued direct chapters", async () => {
  const calls = fakeApi((method) => method === "GET"
    ? { body: [item(1, "downloading"), item(2), item(3, "failed"), item(4), item(5, "queued", "torrent")] }
    : { body: { moved: 2, skipped: 0 } });
  renderWithClient(<Activity />);
  expect(await screen.findByRole("button", { name: "Download now (0)" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Select all" }));
  await userEvent.click(screen.getByRole("button", { name: "Download now (2)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("2 queued chapters moved to the front. The current download will finish first.");
  expect(calls.filter(c => c.method === "POST")).toEqual([{ method: "POST", path: "/queue/move", body: { ids: [2, 4] } }]);
});

test("dragging a queued chapter inserts it before the drop target", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: [item(1), item(2)] } : { body: { moved: 1, skipped: 0 } });
  renderWithClient(<Activity />);
  const handle = await screen.findByRole("button", { name: "Move Chapter 2 to top" });
  const target = screen.getByText("Chapter 1").closest("tr")!;
  vi.stubGlobal("PointerEvent", MouseEvent);
  const original = document.elementFromPoint;
  document.elementFromPoint = vi.fn(() => target);
  fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(handle, { clientX: 50, clientY: 50 });
  expect(target).toHaveClass("queue-drop-target");
  fireEvent.pointerUp(handle, { clientX: 50, clientY: 50 });
  document.elementFromPoint = original;
  expect(await screen.findByRole("status")).toHaveTextContent("moved to the new position");
  // Browsers can emit a click after pointerup; do not promote it a second time.
  fireEvent.click(handle, { detail: 1 });
  expect(calls.filter(c => c.method === "POST")).toHaveLength(1);
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ ids: [2], before_id: 1 });
});

test("cancelling a touch drag does not change the queue", async () => {
  const calls = fakeApi(() => ({ body: [item(1), item(2)] }));
  renderWithClient(<Activity />);
  const handle = await screen.findByRole("button", { name: "Move Chapter 2 to top" });
  vi.stubGlobal("PointerEvent", MouseEvent);
  fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
  fireEvent.pointerCancel(handle);
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("dragging back to the starting chapter cancels the move", async () => {
  const calls = fakeApi(() => ({ body: [item(1), item(2)] }));
  renderWithClient(<Activity />);
  const handle = await screen.findByRole("button", { name: "Move Chapter 2 to top" });
  vi.stubGlobal("PointerEvent", MouseEvent);
  const original = document.elementFromPoint;
  const target = screen.getByText("Chapter 1").closest("tr")!;
  const hitTest = vi.fn().mockReturnValueOnce(target).mockReturnValue(handle);
  document.elementFromPoint = hitTest;
  fireEvent.pointerDown(handle, { button: 0, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(handle, { clientX: 50, clientY: 50 });
  fireEvent.pointerMove(handle, { clientX: 10, clientY: 10 });
  fireEvent.pointerUp(handle, { clientX: 10, clientY: 10 });
  document.elementFromPoint = original;
  expect(calls.every(c => c.method === "GET")).toBe(true);
});

test("the drag handle is also a keyboard-accessible move-to-top button", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: [item(1)] } : { body: { moved: 1, skipped: 0 } });
  renderWithClient(<Activity />);
  (await screen.findByRole("button", { name: "Move Chapter 1 to top" })).focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("status")).toHaveTextContent("moved to the front");
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ ids: [1] });
});

test("stale moves show the server error without retrying or deleting jobs", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: [item(1)] }
    : { status: 409, body: { detail: "That chapter is no longer queued. Refresh the queue and try again." } });
  renderWithClient(<Activity />);
  await userEvent.click(await screen.findByRole("button", { name: "Move Chapter 1 to top" }));
  expect(await screen.findByText(/That chapter is no longer queued/)).toBeInTheDocument();
  expect(calls.filter(c => c.method === "POST")).toHaveLength(1);
});

test("large queues are paginated and filtered selection includes all matches", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: Array.from({ length: 201 }, (_, i) => item(i + 1)) }
    : { body: { moved: 201, skipped: 0 } });
  renderWithClient(<Activity />);
  expect(await screen.findByText("Showing 1–100 of 201")).toBeInTheDocument();
  expect(screen.queryByText("Chapter 101")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Last" }));
  expect(screen.getByText("Chapter 201")).toBeInTheDocument();
  await userEvent.type(screen.getByRole("searchbox", { name: "Find downloads" }), "New manga");
  expect(screen.getByText("Showing 1–100 of 201")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Select all matches" }));
  await userEvent.click(screen.getByRole("button", { name: "Download now (201)" }));
  expect(await screen.findByRole("status")).toHaveTextContent("201 queued chapters moved");
  expect((calls.find(c => c.method === "POST")?.body as { ids: number[] }).ids).toHaveLength(201);
});

test("Move to bottom keeps selected chapter order and uses the same guarded API", async () => {
  const calls = fakeApi(method => method === "GET" ? { body: [item(1), item(2)] } : { body: { moved: 2, skipped: 0 } });
  renderWithClient(<Activity />);
  await userEvent.click(await screen.findByRole("button", { name: "Select all" }));
  await userEvent.click(screen.getByRole("button", { name: "Move to bottom" }));
  expect(await screen.findByRole("status")).toHaveTextContent("moved to the bottom");
  expect(calls.find(c => c.method === "POST")?.body).toEqual({ ids: [1, 2], to_end: true });
});
