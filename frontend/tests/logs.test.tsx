import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LogSettings } from "../src/components/LogSettings";
import { fakeApi, renderWithClient } from "./helpers";

const state = (level: string, source = "environment") => ({
  level, source, environment_level: "info", file_enabled: true,
  files: [{ name: "mangarr.txt", bytes: 1048576, modified: 1 }, { name: "mangarr.txt.1", bytes: 1048576, modified: 0 }],
  lines: ["2026-11-04 INFO    mangarr: ready", "2026-11-04 INFO    httpx: GET ?apiKey=<redacted>"],
});

describe("Settings → Logs", () => {
  it("shows recent lines and changes the level immediately", async () => {
    let level = "info";
    const calls = fakeApi((method, path, body) => {
      if (method === "GET" && path.startsWith("/system/logs?lines=")) return { body: state(level, level === "info" ? "environment" : "settings") };
      if (method === "PUT" && path === "/system/logs/level") { level = (body as { level: string }).level; return { body: { level, source: "settings" } }; }
    });
    renderWithClient(<LogSettings />);
    expect(await screen.findByLabelText("Recent log lines")).toHaveTextContent("mangarr: ready");
    expect(screen.getByText(/2 files, 2.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/currently from MANGARR_LOG_LEVEL/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Log level"), "trace");
    await waitFor(() => expect(screen.getByText(/Adds every outgoing web request/)).toBeInTheDocument());
    expect(screen.getByText(/remembered across restarts/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ level: "trace" });
    await userEvent.selectOptions(screen.getByLabelText("Show"), "1000");
    await waitFor(() => expect(calls.some((c) => c.path === "/system/logs?lines=1000")).toBe(true));
  });

  it("downloads the ZIP under the server's file name", async () => {
    fakeApi((method, path) => (method === "GET" && path.startsWith("/system/logs?") ? { body: state("info") } : undefined));
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => url === "/api/v1/system/logs/download"
      ? new Response("zip", { headers: { "Content-Disposition": 'attachment; filename="mangarr-logs-20261104-120000.zip"' } })
      : real(url, init)));
    URL.createObjectURL = vi.fn(() => "blob:logs");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("mangarr-logs-20261104-120000.zip");
    });
    renderWithClient(<LogSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Download logs" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
  });

  it("explains when the log folder is not writable", async () => {
    fakeApi(() => ({ body: { ...state("info"), file_enabled: false, files: [], lines: [] } }));
    renderWithClient(<LogSettings />);
    expect(await screen.findByText(/cannot write to \/config\/logs/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download logs" })).toBeDisabled();
  });
});
