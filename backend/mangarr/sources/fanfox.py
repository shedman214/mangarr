"""FanFox (MangaFox): title search, chapter list and page images from the public
desktop reader. The reader's packed scripts are decoded as text (Dean Edwards
"p,a,c,k,e,d" format) — never executed."""
import re

from ._html_archive import HtmlArchive
from .base import SourceChapter, SourceSeries

SERIES = r"/manga/([A-Za-z0-9_]+)"
CHAPTER = r"/manga/([A-Za-z0-9_]+(?:/v[0-9A-Za-z.]+)?/c[0-9]+(?:\.[0-9]+)?)(?:/[0-9]+\.html)?"
_PACKED = re.compile(r"\}\('(.*)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)", re.S)
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_PAGES = 500


def _encode(number, radix):
    return (_encode(number // radix, radix) if number >= radix else "") + _DIGITS[number % radix]


def unpack(script: str) -> str:
    """Decode a packed script by replacing its numbered words; nothing is run."""
    match = _PACKED.search(script)
    if not match:
        raise RuntimeError("FanFox reader script not recognised; the site layout may have changed")
    payload, radix, count, words = match.group(1), int(match.group(2)), int(match.group(3)), match.group(4).split("|")
    if not 2 <= radix <= len(_DIGITS) or count > len(words) + 1:
        raise RuntimeError("FanFox reader script not recognised; the site layout may have changed")
    table = {_encode(i, radix): (words[i] if i < len(words) and words[i] else _encode(i, radix)) for i in range(count)}
    return re.sub(r"\b\w+\b", lambda w: table.get(w.group(0), w.group(0)), payload.replace("\\'", "'"))


def _number(chapter_id):
    return float(re.search(r"/c([0-9]+(?:\.[0-9]+)?)$", chapter_id).group(1))


def _volume(chapter_id):
    match = re.search(r"/v([0-9]+)/", chapter_id)
    return int(match.group(1)) if match else None


class FanFoxSource(HtmlArchive):
    name = "fanfox"
    base_url = "https://fanfox.net"
    chapter_pattern = CHAPTER

    async def request(self, path, **kwargs):
        if self.adult_content:
            # FanFox's own "I am over 18" confirmation; without it adult titles list one chapter.
            kwargs["headers"] = {**kwargs.get("headers", {}), "Cookie": "isAdult=1"}
        return await super().request(path, **kwargs)

    async def search_series(self, query):
        if not query.strip():
            return []
        soup = await self.html("/search", params={"title": query.strip()})
        results = {}
        for anchor in soup.select(".manga-list-4-list li a[href][title]"):
            identifier = self.identifier(str(anchor.get("href", "")), SERIES)
            title = str(anchor.get("title", "")).strip()
            if identifier and title and identifier not in results:
                results[identifier] = SourceSeries(self.name, identifier, title, f"{self.base_url}/manga/{identifier}/")
        return list(results.values())[:30]

    async def list_chapters(self, external_id):
        if not re.fullmatch(SERIES, "/manga/" + external_id):
            raise ValueError("Invalid FanFox series ID")
        soup = await self.html(f"/manga/{external_id}/")
        chapters = {}
        for anchor in soup.select("#chapterlist a[href], .detail-main-list a[href]"):
            identifier = self.identifier(str(anchor.get("href", "")), CHAPTER)
            if not identifier or not identifier.startswith(external_id + "/") or identifier in chapters:
                continue
            title = anchor.select_one(".title3")
            chapters[identifier] = SourceChapter(
                self.name, identifier, _number(identifier), volume=_volume(identifier),
                title=(title or anchor).get_text(" ", strip=True),
                url=f"{self.base_url}/manga/{identifier}/1.html")
        if not chapters:
            raise RuntimeError("FanFox returned no chapter links; check the series link, or turn on Settings → Sources → Adult content for adult titles")
        return sorted(chapters.values(), key=lambda c: (c.number, c.external_id))

    async def get_pages(self, chapter_external_id):
        path = f"/manga/{chapter_external_id}/1.html"
        if not re.fullmatch(CHAPTER, path):
            raise ValueError("Invalid FanFox chapter ID")
        text = (await self.request(path)).text
        count = re.search(r"\bvar\s+imagecount\s*=\s*(\d+)\s*;", text)
        chapter = re.search(r"\bvar\s+chapterid\s*=\s*(\d+)\s*;", text)
        if not count or not chapter:
            raise RuntimeError("FanFox chapter page has no reader data; the chapter may be unavailable")
        total = min(int(count.group(1)), MAX_PAGES)
        key = ""
        start = text.find("eval(function(p,a,c,k,e,d)")
        if start >= 0:
            end = text.find("</script>", start)
            guid = re.search(r"guidkey\s*=\s*((?:''|'\w'|\+)+)", unpack(text[start:end if end > 0 else None]))
            key = "".join(re.findall(r"'(\w)'", guid.group(1))) if guid else ""
        raw: list[str] = []
        page = 1
        # chapterfun.ashx answers with the image for `page` and the next one.
        while len(raw) < total and page <= total:
            response = await self.request(
                f"/manga/{chapter_external_id}/chapterfun.ashx",
                params={"cid": chapter.group(1), "page": page, "key": key},
                headers={"Referer": self.base_url + path, "X-Requested-With": "XMLHttpRequest"})
            decoded = unpack(response.text)
            base = re.search(r"\bpix\s*=\s*\"([^\"]+)\"", decoded)
            names = re.search(r"\bpvalue\s*=\s*\[(.*?)\]", decoded, re.S)
            if not base or not names:
                break
            batch = [base.group(1) + name for name in re.findall(r"\"([^\"]+)\"", names.group(1))]
            new = [url for url in batch if url not in raw]
            if not new:
                break
            raw.extend(new)
            page += len(batch) or 1
        return self.page_urls(raw[:total], path)


source = FanFoxSource()
