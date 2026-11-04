import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { InteractiveSearch } from "../src/pages/SeriesDetail";
import { fakeApi, renderWithClient } from "./helpers";

describe("Release search", () => {
  it("shows each failed source check and says why nothing was returned", async () => {
    fakeApi((method, path) => {
      if (method === "GET" && path.startsWith("/search/releases?series_id=3")) {
        return { body: { releases: [], warnings: ["WeebCentral: timed out", "Nyaa: HTTP 503"] } };
      }
    });
    renderWithClient(<InteractiveSearch seriesId={3} title="Dandadan" onClose={() => {}} />);
    expect(await screen.findByText("WeebCentral: timed out")).toBeInTheDocument();
    expect(screen.getByText("Nyaa: HTTP 503")).toBeInTheDocument();
    expect(screen.getByText(/Some source checks failed/)).toBeInTheDocument();
  });

  it("explains an empty result when every source answered", async () => {
    fakeApi((_method, path) => path.startsWith("/search/releases") ? { body: { releases: [], warnings: [] } } : undefined);
    renderWithClient(<InteractiveSearch seriesId={3} title="Dandadan" onClose={() => {}} />);
    expect(await screen.findByText(/No matching downloadable releases found/)).toBeInTheDocument();
  });

  it("shows a search that failed outright", async () => {
    fakeApi((_method, path) => path.startsWith("/search/releases") ? { status: 500, body: { detail: "Search failed" } } : undefined);
    renderWithClient(<InteractiveSearch seriesId={3} chapterIds={[11]} title="Dandadan" onClose={() => {}} />);
    expect(await screen.findByText("Search failed")).toBeInTheDocument();
  });
});
