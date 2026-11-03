import re
import unicodedata

from ._html_archive import HtmlArchive
from .base import SourceSeries, SourceChapter

SERIES = r"/manga/([A-Za-z0-9_-]+)"
CHAPTER = r"/manga/([A-Za-z0-9_-]+/chapter-[0-9]+(?:[.-][0-9]+)*)"


class ManganatoSource(HtmlArchive):
    name = "manganato"
    base_url = "https://www.manganato.gg"
    chapter_pattern = CHAPTER

    async def search_series(self, query):
        if not query.strip():
            return []
        query = unicodedata.normalize("NFKD", query.lower()).encode("ascii", "ignore").decode()
        query = re.sub(r"[^a-z0-9]+", "_", query).strip("_")
        rows = (await self.request("/home/search/json", params={"searchword": query}, headers={"Referer": self.base_url + "/", "X-Requested-With": "XMLHttpRequest"})).json()
        if not isinstance(rows, list):
            raise RuntimeError("Manganato returned an invalid search response")
        results = {}
        for row in rows:
            identifier = self.identifier(row.get("url", ""), SERIES)
            if identifier and row.get("name"):
                results[identifier] = SourceSeries(self.name, identifier, row["name"], self.base_url + "/manga/" + identifier)
        return list(results.values())[:30]

    async def list_chapters(self, external_id):
        if not re.fullmatch(SERIES, "/manga/" + external_id):
            raise ValueError("Invalid Manganato series ID")
        chapters = {}
        offset = 0
        while True:
            response = (await self.request(f"/api/manga/{external_id}/chapters", params={"limit": 50, "offset": offset})).json()
            if not response.get("success"):
                raise RuntimeError("Manganato chapter lookup failed")
            data = response["data"]
            rows = data["chapters"]
            previous = len(chapters)
            for row in rows:
                identifier = external_id + "/" + row["chapter_slug"]
                if not re.fullmatch(CHAPTER, "/manga/" + identifier):
                    raise RuntimeError("Manganato returned an invalid chapter link")
                # API chapter_num preserves decimal chapters (URL uses hyphens).
                number = float(row["chapter_num"])
                chapters[identifier] = SourceChapter(self.name, identifier, number, title=row.get("chapter_name", ""), url=self.base_url + "/manga/" + identifier)
            if not data["pagination"]["has_more"]:
                break
            if len(chapters) == previous:
                raise RuntimeError("Manganato chapter pagination stopped advancing; existing chapters were kept")
            offset += len(rows)
        return sorted(chapters.values(), key=lambda c: c.number)

    async def get_pages(self, chapter_external_id):
        path = "/manga/" + chapter_external_id
        if not re.fullmatch(CHAPTER, path):
            raise ValueError("Invalid Manganato chapter ID")
        soup = await self.html(path)
        return self.page_urls([str(i.get("data-src") or i.get("src") or "") for i in soup.select(".container-chapter-reader img")], path)


source = ManganatoSource()
