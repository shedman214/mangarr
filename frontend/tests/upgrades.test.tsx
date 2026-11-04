import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UpgradeSettings } from "../src/components/UpgradeSettings";
import { fakeApi, renderWithClient } from "./helpers";

function Harness({ initial, dirty = false, onForm }: { initial: Record<string, string>; dirty?: boolean; onForm?: (f: Record<string, string>) => void }) {
  const [form, setForm] = useState(initial);
  return <UpgradeSettings form={form} dirty={dirty} sources={["mangadex", "weebcentral"]}
    setForm={(f) => { setForm(f); onForm?.(f); }} />;
}

describe("Settings → Sources → Upgrades", () => {
  it("sets Upgrades allowed and Upgrade until like Sonarr", async () => {
    fakeApi((method, path) => (method === "GET" && path === "/settings/upgrades" ? { body: { series: 4, upgrades_on: 1, matching: 3 } } : undefined));
    const onForm = vi.fn();
    renderWithClient(<Harness initial={{ upgrades_allowed: "false", upgrade_until: "" }} onForm={onForm} />);
    const until = screen.getByLabelText("Upgrade until");
    expect(until).toBeDisabled();
    expect(await screen.findByText("1 of 4 series have upgrades on; 3 match the saved settings.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Upgrades allowed" }));
    expect(onForm).toHaveBeenLastCalledWith({ upgrades_allowed: "true", upgrade_until: "" });
    expect(until).toBeEnabled();
    expect(screen.getByRole("option", { name: "WeebCentral or better" })).toBeInTheDocument();
    await userEvent.selectOptions(until, "weebcentral");
    expect(onForm).toHaveBeenLastCalledWith({ upgrades_allowed: "true", upgrade_until: "weebcentral" });
  });

  it("applies the saved settings to every series only after confirmation", async () => {
    const calls = fakeApi((method, path) => {
      if (method === "GET" && path === "/settings/upgrades") return { body: { series: 4, upgrades_on: 1, matching: 1 } };
      if (method === "POST" && path === "/settings/upgrades/apply") return { body: { changed: 3, series: 4, upgrades_on: 4, matching: 4 } };
    });
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    renderWithClient(<Harness initial={{ upgrades_allowed: "true", upgrade_until: "mangadex" }} />);
    const apply = await screen.findByRole("button", { name: "Apply to all series" });
    await waitFor(() => expect(apply).toBeEnabled());
    await userEvent.click(apply);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Turn on upgrades for all 4 series, until MangaDex?"));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(apply);
    expect(await screen.findByText(/Updated 3 series\. 4 of 4 series have upgrades on/)).toBeInTheDocument();
  });

  it("asks to save first when there are unsaved changes", async () => {
    fakeApi(() => ({ body: { series: 2, upgrades_on: 0, matching: 2 } }));
    renderWithClient(<Harness initial={{ upgrades_allowed: "true", upgrade_until: "" }} dirty />);
    expect(await screen.findByText(/Save your changes first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply to all series" })).toBeDisabled();
  });
});
