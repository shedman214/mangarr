import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesModal } from "../src/components/LibraryTools";
import { fakeApi, renderWithClient } from "./helpers";

const candidate = { source_name: "mangadex", external_id: "abc-123", title: "Kagurabachi", url: "https://mangadex.org/title/abc-123", alt_titles: [] };

describe("Edit sources", () => {
  it("links a pasted series URL without choosing a source first", async () => {
    const calls = fakeApi((method, path) => {
      if (method === "GET" && path === "/sources") return { body: ["mangadex", "mangaplus"] };
      if (method === "GET" && path.startsWith("/series/7/sources/search")) return { body: [candidate] };
      if (method === "POST" && path === "/series/7/sources") return { status: 201, body: {} };
    });
    const onChanged = vi.fn();
    renderWithClient(<SourcesModal seriesId={7} links={[]} onClose={() => {}} onChanged={onChanged} />);
    const search = screen.getByRole("button", { name: "Search" });
    expect(search).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Search title or series website link"), candidate.url);
    expect(search).toBeEnabled();
    await userEvent.click(search);
    expect(await screen.findByText(/Recognized series link/)).toBeInTheDocument();
    const query = calls.find((c) => c.path.startsWith("/series/7/sources/search"))!.path;
    expect(new URLSearchParams(query.split("?")[1]).get("query")).toBe(candidate.url);
    await userEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      source_name: "mangadex", external_id: "abc-123", external_title: "Kagurabachi", external_url: candidate.url,
    });
  });

  it("needs a source for a title search and shows a failed search", async () => {
    fakeApi((method, path) => {
      if (path === "/sources") return { body: ["mangadex"] };
      if (path.startsWith("/series/7/sources/search")) return { status: 502, body: { detail: "MangaDex did not respond" } };
    });
    renderWithClient(<SourcesModal seriesId={7} links={[]} onClose={() => {}} onChanged={() => {}} />);
    await userEvent.type(screen.getByLabelText("Search title or series website link"), "Kagurabachi");
    const search = screen.getByRole("button", { name: "Search" });
    expect(search).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole("combobox"), await screen.findByRole("option", { name: "mangadex" }));
    await userEvent.click(search);
    expect(await screen.findByText("MangaDex did not respond")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use" })).not.toBeInTheDocument();
  });
});
