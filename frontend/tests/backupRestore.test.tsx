import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BackupSettings } from "../src/components/BackupSettings";
import { fakeApi, renderWithClient } from "./helpers";

describe("Backup and restore", () => {
  it("only schedules a restore after a file is chosen and replacement is confirmed", async () => {
    const calls = fakeApi((method, path) => {
      if (path === "/settings/backups/restore" && method === "GET") return { body: { pending: false } };
      if (path === "/settings/backups/schedule") return { body: { enabled: true, interval_days: 7, retention_days: 28, manual_retention_days: 0, folder: "/config/backups", last_automatic: null, next_automatic: null, automatic_kept: 0, manual_kept: 0 } };
      if (path === "/settings/backups" && method === "GET") return { body: [] };
      if (path === "/settings/backups/restore" && method === "POST") return { body: { pending: true } };
    });
    renderWithClient(<BackupSettings />);
    const schedule = await screen.findByRole("button", { name: "Validate and schedule restore" });
    expect(schedule).toBeDisabled();
    await userEvent.upload(screen.getByLabelText("Backup ZIP file"), new File(["zip"], "backup.zip", { type: "application/zip" }));
    expect(schedule).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Replace my database/ }));
    await waitFor(() => expect(schedule).toBeEnabled());
    await userEvent.click(schedule);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/settings/backups/restore")).toBe(true));
  });

  it("lists saved backups, including automatic pre-upgrade ones", async () => {
    fakeApi((method, path) => {
      if (path === "/settings/backups/restore") return { body: { pending: false } };
      if (path === "/settings/backups/schedule") return { body: { enabled: true, interval_days: 7, retention_days: 28, manual_retention_days: 0, folder: "/config/backups", last_automatic: null, next_automatic: null, automatic_kept: 0, manual_kept: 0 } };
      if (path === "/settings/backups" && method === "GET") return { body: [
        { id: "a".repeat(32), created: 1_800_000_000, version: "0.9.0", reason: "pre-migration", bytes: 2_097_152 },
        { id: "b".repeat(32), created: 1_790_000_000, version: "0.8.0", reason: "manual", bytes: 1_048_576 },
      ] };
    });
    renderWithClient(<BackupSettings />);
    const list = await screen.findByRole("list", { name: "Saved backups" });
    expect(list).toHaveTextContent("Before database update · v0.9.0 · 2.0 MB");
    expect(list).toHaveTextContent("Manual · v0.8.0 · 1.0 MB");
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(2);
  });
});
