import { expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import AddSeries from "../src/pages/AddSeries";
import { SeriesRootFolder } from "../src/components/SeriesRootFolder";
import { fakeApi, renderWithClient } from "./helpers";

function Location() { return <output>{useLocation().pathname}</output>; }

test("an existing search result opens its local series even without configured roots", async () => {
  fakeApi((_, path) => path === "/rootfolders" ? { body: [] } : path.startsWith("/search/metadata") ? { body: [{
    provider: "mangaupdates", provider_id: "111", title: "Example", genres: [], status: "finished",
    description: "", in_library: true, in_library_series_id: 42,
  }] } : undefined);
  renderWithClient(<><AddSeries /><Location /></>);
  await userEvent.type(screen.getByPlaceholderText(/Search MangaUpdates/), "Example");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  const result = await screen.findByRole("link", { name: "Open Example" });
  result.focus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByText("/series/42")).toBeInTheDocument();
});

test("root folder changes only after saving and keeps existing files by default", async () => {
  const changed = vi.fn();
  const calls = fakeApi((method, path) => path === "/rootfolders" ? { body: [{ id: 1, path: "/manga" }, { id: 2, path: "/comics" }] }
    : method === "PUT" && path === "/series/editor" ? { body: { updated: 1, moved: 0, problems: [] } } : undefined);
  renderWithClient(<SeriesRootFolder seriesId={42} rootId={1} folderName="Example" onChanged={changed} />);
  await userEvent.click(screen.getByRole("button", { name: "Change root folder" }));
  await userEvent.selectOptions(await screen.findByLabelText("Root folder"), "2");
  expect(screen.getByText("/comics/Example")).toBeInTheDocument();
  expect(calls.some(c => c.method === "PUT")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Update root folder" }));
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  expect(calls.find(c => c.method === "PUT")?.body).toEqual({ series_ids: [42], root_folder_id: 2, move_files: false });
});

test("a refused move shows the reason and leaves the dialog open", async () => {
  const changed = vi.fn();
  const calls = fakeApi((method, path) => path === "/rootfolders" ? { body: [{ id: 1, path: "/manga" }, { id: 2, path: "/comics" }] }
    : method === "PUT" ? { body: { updated: 0, moved: 0, problems: [{ detail: "downloads are in progress" }] } } : undefined);
  renderWithClient(<SeriesRootFolder seriesId={42} rootId={1} folderName="Example" onChanged={changed} />);
  await userEvent.click(screen.getByRole("button", { name: "Change root folder" }));
  await userEvent.selectOptions(await screen.findByLabelText("Root folder"), "2");
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Update root folder" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("downloads are in progress");
  expect(changed).not.toHaveBeenCalled();
  expect(calls.find(c => c.method === "PUT")?.body).toMatchObject({ move_files: true });
  expect(screen.getByRole("button", { name: "Update root folder" })).toBeEnabled();
});
