import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import Activity from "../src/pages/Activity";
import { fakeApi, renderWithClient } from "./helpers";

function Location() { return <output>{useLocation().pathname}</output>; }

const queued = (id: number, series_id: number | null) => ({
  id, status: "queued", kind: "direct", title: `Vanilla Essence - Chapter ${id}`, series_title: "Vanilla Essence",
  source_name: "atsumaru", series_id, chapter_id: id, progress: 0, error: "", created_at: "",
  root_folder_id: 1, root_folder_path: "/dojuin",
});

test("queue titles open their series page; an item without a series stays plain text", async () => {
  fakeApi((_, path) => (path.startsWith("/queue") ? { body: [queued(23, 2658), queued(24, null)] } : { body: [] }));
  renderWithClient(<><Activity /><Location /></>);
  expect(await screen.findByText("Vanilla Essence - Chapter 24")).not.toHaveAttribute("href");
  await userEvent.click(screen.getByRole("link", { name: "Vanilla Essence - Chapter 23" }));
  expect(screen.getByText("/series/2658")).toBeInTheDocument();
});

test("history series names open their series page", async () => {
  fakeApi((_, path) => path.startsWith("/history")
    ? { body: [{ id: 1, series_id: 2658, event: "imported", detail: "", source_name: "atsumaru", created_at: "", series_title: "Vanilla Essence" }] }
    : { body: [] });
  renderWithClient(<><Activity /><Location /></>);
  await userEvent.click(await screen.findByRole("button", { name: "History" }));
  await userEvent.click(await screen.findByRole("link", { name: "Vanilla Essence" }));
  expect(screen.getByText("/series/2658")).toBeInTheDocument();
});
