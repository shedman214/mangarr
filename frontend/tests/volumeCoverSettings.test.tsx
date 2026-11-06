import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VolumeCoverSettings } from "../src/components/VolumeCoverSettings";
import { fakeApi, renderWithClient } from "./helpers";

const IDLE = { running: false, done: 0, total: 0, series_changed: 0, volumes: 0, failed: 0, skipped_busy: 0, finished_at: null };

function Harness({ savedOn, initial }: { savedOn: boolean; initial: Record<string, string> }) {
  const [form, setForm] = useState(initial);
  return <VolumeCoverSettings form={form} setForm={setForm} savedOn={savedOn} dirty={false} />;
}

afterEach(() => vi.restoreAllMocks());

test("off by default, and the library update only shows once it is on and saved", async () => {
  fakeApi(() => ({ body: IDLE }));
  const view = renderWithClient(<Harness savedOn={false} initial={{}} />);
  expect(screen.getByRole("button", { name: "English volume covers" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryByRole("button", { name: "Update covers now" })).toBeNull();
  view.unmount();
  renderWithClient(<Harness savedOn initial={{ english_volume_covers: "true" }} />);
  expect(await screen.findByRole("button", { name: "Update covers now" })).toBeInTheDocument();
});

test("Update covers now asks before rewriting files", async () => {
  const calls = fakeApi((method) => (method === "POST" ? { body: { status: "running" } } : { body: IDLE }));
  renderWithClient(<Harness savedOn initial={{ english_volume_covers: "true" }} />);
  const button = await screen.findByRole("button", { name: "Update covers now" });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  await userEvent.click(button);
  expect(calls.some((c) => c.method === "POST")).toBe(false);
  await userEvent.click(button);
  await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path))
    .toEqual(["/settings/volume-covers/update"]));
  expect(confirm).toHaveBeenCalledTimes(2);
});
