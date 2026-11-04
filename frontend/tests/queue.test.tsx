import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Activity from "../src/pages/Activity";
import { fakeApi, renderWithClient } from "./helpers";

const item = (id: number, status: string, kind = "direct") => ({
  id, series_id: 1, chapter_id: id, kind, status, title: `Chapter ${id}`, source_name: "mangadex",
  progress: status === "failed" ? 0 : 0.5, error: status === "failed" ? "Source returned 404" : "", created_at: "", series_title: "Dandadan",
});

describe("Queue", () => {
  it("offers Retry only on failed items and retries the right one", async () => {
    const calls = fakeApi((method, path) => {
      if (method === "GET" && path === "/queue") return { body: [item(1, "downloading"), item(2, "failed")] };
      if (method === "POST" && path === "/queue/2/retry") return { body: item(2, "queued") };
    });
    renderWithClient(<Activity />);
    expect(await screen.findByText("Source returned 404")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry Chapter 1" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry Chapter 2" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/queue/2/retry")).toBe(true));
  });

  it("shows why a retry was refused", async () => {
    fakeApi((method, path) => {
      if (path === "/queue") return { body: [item(2, "failed")] };
      if (method === "POST") return { status: 409, body: { detail: "Excluded chapters cannot be retried" } };
    });
    renderWithClient(<Activity />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry Chapter 2" }));
    expect(await screen.findByText(/Excluded chapters cannot be retried/)).toBeInTheDocument();
  });
});

describe("Queue removal asks first", () => {
  it("does nothing when the confirmation is cancelled", async () => {
    const calls = fakeApi((_method, path) => path === "/queue" ? { body: [item(5, "downloading", "torrent")] } : undefined);
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    renderWithClient(<Activity />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove Chapter 5" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("together with their downloaded data"));
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  it("removes the selected items after confirmation", async () => {
    const calls = fakeApi((method, path) => {
      if (path === "/queue") return { body: [item(5, "downloading"), item(6, "failed")] };
      if (method === "POST" && path === "/queue/remove") return { body: { removed: 2 } };
    });
    const confirm = vi.fn(() => true);
    vi.stubGlobal("confirm", confirm);
    renderWithClient(<Activity />);
    await userEvent.click(await screen.findByRole("button", { name: "Select all" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove selected" }));
    expect(confirm).toHaveBeenCalledWith("Remove 2 items from the queue?");
    await waitFor(() => expect(calls.find((c) => c.path === "/queue/remove")?.body).toEqual({ ids: [5, 6] }));
  });
});
