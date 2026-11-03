/** Display names for source identifiers, shared by Settings and series pages. */
export const SOURCE_LABELS: Record<string, string> = {
  mangaplus: "MangaPlus",
  webtoons: "WEBTOON",
  tcbscans: "TCB Scans",
  mangadex: "MangaDex",
  mangafire: "MangaFire",
  weebcentral: "WeebCentral",
  atsumaru: "Atsumaru",
  asura: "Asura Scans",
  toonily: "Toonily (experimental)",
  mangakatana: "MangaKatana (experimental)",
  manganato: "Manganato.gg (experimental)",
  toomics: "Toomics (experimental, free episodes)",
  viz: "VIZ (official metadata)",
  wikipedia: "Wikipedia (metadata)",
  nyaa: "Nyaa (torrents)",
};

export function sourceLabel(name: string): string {
  return SOURCE_LABELS[name] ?? name;
}
