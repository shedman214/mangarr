import { useState } from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { SelectionCheckbox } from "../src/components/common";
import { SourcePriority } from "../src/pages/Settings";
import Wanted from "../src/pages/Wanted";
import Activity from "../src/pages/Activity";
import type { Settings } from "../src/api/types";
import { fakeApi, renderWithClient } from "./helpers";

test("the larger checkbox label toggles once and preserves native keyboard and shift clicks", async () => {
  const changed = vi.fn();
  const clicked = vi.fn();
  renderWithClient(<SelectionCheckbox label="Select chapter 1" onChange={changed} onClick={clicked} />);
  const checkbox = screen.getByRole("checkbox", { name: "Select chapter 1" });
  await userEvent.click(checkbox.closest("label")!);
  expect(checkbox).toBeChecked();
  expect(changed).toHaveBeenCalledTimes(1);
  checkbox.focus();
  await userEvent.keyboard(" ");
  expect(checkbox).not.toBeChecked();
  fireEvent.click(checkbox, { shiftKey: true });
  expect(clicked.mock.calls.at(-1)![0].shiftKey).toBe(true);
});

test("source reorder and discovery buttons affect only the named source", async () => {
  function Sources() {
    const [form, setForm] = useState<Settings>({ source_priority: "weebcentral,mangadex",
      source_weebcentral_enabled: "true", source_mangadex_enabled: "true",
      source_weebcentral_discovery_proxy_enabled: "true", source_mangadex_discovery_proxy_enabled: "true" });
    return <><SourcePriority form={form} setForm={setForm} /><output data-testid="settings">{JSON.stringify(form)}</output></>;
  }
  renderWithClient(<Sources />);
  expect(screen.getByRole("button", { name: "Move WeebCentral higher" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Move MangaDex higher" }));
  expect(screen.getByRole("button", { name: "Move MangaDex higher" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Use proxy for WeebCentral discovery" }));
  const settings = JSON.parse(screen.getByTestId("settings").textContent!);
  expect(settings.source_priority).toBe("mangadex,weebcentral");
  expect(settings.source_weebcentral_discovery_proxy_enabled).toBe("false");
  expect(settings.source_mangadex_discovery_proxy_enabled).toBe("true");
  expect(settings.source_weebcentral_enabled).toBe("true");
});

test("Wanted pagination keeps the right chapter links when moving between pages", async () => {
  const calls = fakeApi((method, path) => {
    if (method === "GET" && path.startsWith("/wanted/page?")) {
      const next = path.includes("offset=100");
      return { body: { total: 101, items: [{ chapter_id: next ? 101 : 1, series_id: next ? 22 : 11,
        series_title: next ? "Next series" : "First series", number: 1, volume: null, title: "" }] } };
    }
  });
  renderWithClient(<Wanted />);
  expect(await screen.findByRole("link", { name: "First series" })).toHaveAttribute("href", "/series/11");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByRole("link", { name: "Next series" })).toHaveAttribute("href", "/series/22");
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(await screen.findByRole("link", { name: "First series" })).toBeInTheDocument();
  expect(calls.some(c => c.path.includes("offset=100"))).toBe(true);
});

test("tapping a queue checkbox label selects its item without retrying or removing it", async () => {
  const calls = fakeApi((method, path) => method === "GET" && path === "/queue" ? { body: [{
    id: 5, series_id: 1, chapter_id: 1, kind: "direct", status: "failed", title: "Long chapter title",
    series_title: "Series", source_name: "weebcentral", progress: 0, error: "Unavailable", created_at: "",
  }] } : undefined);
  renderWithClient(<Activity />);
  const checkbox = await screen.findByRole("checkbox", { name: "Select Long chapter title" });
  await userEvent.click(checkbox.closest("label")!);
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry selected (1)" })).toBeEnabled());
  expect(calls.every(c => c.method === "GET")).toBe(true);
});
