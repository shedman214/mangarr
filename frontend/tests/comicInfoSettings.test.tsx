import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ComicInfoSettings } from "../src/components/ComicInfoSettings";

function Harness({ onForm }: { onForm: (f: Record<string, string>) => void }) {
  const [form, setForm] = useState<Record<string, string>>({});
  return <ComicInfoSettings form={form} setForm={(f) => { setForm(f); onForm(f); }} />;
}

describe("Settings → Media Management → ComicInfo from MangaBaka", () => {
  it("is on when unset and turning it off saves \"false\"", async () => {
    const onForm = vi.fn();
    render(<Harness onForm={onForm} />);
    const toggle = screen.getByRole("button", { name: "ComicInfo from MangaBaka" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle);
    expect(onForm).toHaveBeenLastCalledWith({ mangabaka_metadata: "false" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    expect(onForm).toHaveBeenLastCalledWith({ mangabaka_metadata: "true" });
  });
});
