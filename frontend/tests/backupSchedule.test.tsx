import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BackupSettings } from "../src/components/BackupSettings";
import { fakeApi, renderWithClient } from "./helpers";

const now = Date.now() / 1000;
const day = 86400;
const schedule = { enabled: true, interval_days: 7, retention_days: 28, manual_retention_days: 0, folder: "/config/backups",
  last_automatic: now - 2 * day, next_automatic: now + 5 * day, automatic_kept: 1, manual_kept: 3 };
const saved = [
  { id: "a".repeat(32), created: now - 2 * day, version: "0.1.1", reason: "scheduled", bytes: 1048576 },
  { id: "b".repeat(32), created: now - 5 * day, version: "0.1.1", reason: "manual", bytes: 1048576 },
  { id: "c".repeat(32), created: now - 40 * day, version: "0.1.1", reason: "manual", bytes: 1048576 },
  { id: "d".repeat(32), created: now - 60 * day, version: "0.1.1", reason: "manual", bytes: 1048576 },
];

function api(extra?: (method: string, path: string, body: unknown) => { status?: number; body?: unknown } | undefined) {
  return fakeApi((method, path, body) => {
    const hit = extra?.(method, path, body);
    if (hit) return hit;
    if (method === "GET" && path === "/settings/backups/restore") return { body: { pending: false } };
    if (method === "GET" && path === "/settings/backups") return { body: saved };
    if (method === "GET" && path === "/settings/backups/schedule") return { body: schedule };
  });
}

describe("Backup and restore → Automatic backups", () => {
  it("shows the schedule and saves new values", async () => {
    const calls = api((method, path, body) => method === "PUT" && path === "/settings/backups/schedule"
      ? { body: { ...schedule, ...(body as object), removed: 0 } } : undefined);
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderWithClient(<BackupSettings />);
    const group = await screen.findByRole("group", { name: "Automatic backups" });
    expect(group).toHaveTextContent("/config/backups");
    expect(group).toHaveTextContent("1 automatic · 3 manual");
    expect(screen.getByLabelText("Make backups automatically")).toBeChecked();
    const save = screen.getByRole("button", { name: "Save automatic backups" });
    expect(save).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Keep automatic backups (days)"));
    await userEvent.type(screen.getByLabelText("Keep automatic backups (days)"), "14");
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual(
      { enabled: true, interval_days: 7, retention_days: 14, manual_retention_days: 0 }));
  });

  it("warns before a manual retention removes existing backups", async () => {
    const calls = api();
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    renderWithClient(<BackupSettings />);
    const field = await screen.findByLabelText("Delete manual backups after (days)");
    await userEvent.clear(field);
    await userEvent.type(field, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save automatic backups" }));
    expect(confirm).toHaveBeenCalledWith("These settings remove 2 saved backups now. Continue?");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("labels automatic backups and deletes one only after confirmation", async () => {
    const calls = api((method, path) => method === "DELETE" && path === `/settings/backups/${"d".repeat(32)}` ? { status: 204 } : undefined);
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    renderWithClient(<BackupSettings />);
    const list = await screen.findByRole("list", { name: "Saved backups" });
    expect(list).toHaveTextContent("Automatic · v0.1.1");
    const del = screen.getAllByRole("button", { name: /^Delete backup from/ });
    expect(del).toHaveLength(4);
    await userEvent.click(del[3]);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(del[3]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});
