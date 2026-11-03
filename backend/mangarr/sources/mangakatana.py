import re

from ._html_archive import HtmlArchive
from .base import SourceSeries, SourceChapter

SERIES = r"/manga/([A-Za-z0-9_-]+\.[0-9]+)"
CHAPTER = r"/manga/([A-Za-z0-9_-]+\.[0-9]+/c[0-9]+(?:\.[0-9]+)?)"


class MangaKatanaSource(HtmlArchive):
    name = "mangakatana"
    base_url = "https://mangakatana.com"
    chapter_pattern = CHAPTER

    async def search_series(self, query):
        if not query.strip():
            return []
        soup = await self.html("/", params={"search": query.strip(), "search_by": "m_name"})
        results = {}
        for anchor in soup.select(".item .title a, h3 a, h2 a"):
            identifier = self.identifier(str(anchor.get("href", "")), SERIES)
            if identifier and anchor.get_text(strip=True):
                results[identifier] = SourceSeries(self.name, identifier, anchor.get_text(" ", strip=True), self.base_url + "/manga/" + identifier)
        return list(results.values())[:30]

    async def list_chapters(self, external_id):
        if not re.fullmatch(SERIES, "/manga/" + external_id):
            raise ValueError("Invalid MangaKatana series ID")
        soup = await self.html("/manga/" + external_id)
        chapters = {}
        for anchor in soup.select("a[href]"):
            identifier = self.identifier(str(anchor.get("href", "")), CHAPTER)
            if identifier and identifier.startswith(external_id + "/"):
                number = float(identifier.rsplit("/c", 1)[1])
                chapters[identifier] = SourceChapter(self.name, identifier, number, title=anchor.get_text(" ", strip=True), url=self.base_url + "/manga/" + identifier)
        if not chapters:
            raise RuntimeError("MangaKatana returned no chapter links; check the series link or source availability")
        return sorted(chapters.values(), key=lambda c: c.number)

    async def get_pages(self, chapter_external_id):
        path = "/manga/" + chapter_external_id
        if not re.fullmatch(CHAPTER, path):
            raise ValueError("Invalid MangaKatana chapter ID")
        soup = await self.html(path)
        # Public reader's ordered image array; parse literals, never execute JS.
        raw = []
        for script in soup.select("script:not([src])"):
            match = re.search(r"\bvar\s+thzq\s*=\s*\[(.*?)\]\s*;", script.string or script.get_text(), re.S)
            if match:
                for quoted in re.findall(r"'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\"", match.group(1)):
                    value = next(v for v in quoted if v)
                    raw.append(value.replace(r"\/", "/"))
                break
        if not raw:
            raw = [str(i.get("data-src") or i.get("src") or "") for i in soup.select("#imgs img")]
        return self.page_urls(raw, path)


source = MangaKatanaSource()
