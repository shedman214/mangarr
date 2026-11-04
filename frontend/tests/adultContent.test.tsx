import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdultContentSettings } from "../src/components/AdultContentSettings";

function Harness({ onForm }: { onForm: (f: Record<string, string>) => void }) {
  const [form, setForm] = useState<Record<string, string>>({ adult_content: "false" });
  return <AdultContentSettings form={form} setForm={(f) => { setForm(f); onForm(f); }} />;
}

describe("Settings → Sources → Adult content", () => {
  it("is off until the user turns it on, and explains what it changes", async () => {
    const onForm = vi.fn();
    render(<Harness onForm={onForm} />);
    const toggle = screen.getByRole("button", { name: "Include adult content" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/Off \(default\)/)).toHaveTextContent("MangaDex adds its pornographic rating");
    await userEvent.click(toggle);
    expect(onForm).toHaveBeenLastCalledWith({ adult_content: "true" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle);
    expect(onForm).toHaveBeenLastCalledWith({ adult_content: "false" });
  });
});
