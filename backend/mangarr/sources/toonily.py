"""Toonily HTML source with a per-image chapter Referer.

Keep the Referer in each image URL's private fragment, rather than mutable
source state: two chapters may be downloading from this singleton at once.
"""

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .. import USER_AGENT
from ..util import RateLimiter, parse_chapter_number, rl_request
from .base import DirectSource, SourceChapter, SourceSeries

BASE_URL = "https://toonily.com"
_limiter = RateLimiter(rate=1, per_seconds=1)
_image_limiter = RateLimiter(rate=3, per_seconds=1)
_SERIES = re.compile(r"/serie/([A-Za-z0-9_-]+)/?")
_CHAPTER = re.compile(r"/serie/([A-Za-z0-9_-]+/chapter-[A-Za-z0-9_.-]+)/?")


def _site_path(url: str, pattern: re.Pattern) -> str | None:
    parts = urlsplit(urljoin(BASE_URL, url.strip()))
    if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in {"toonily.com", "www.toonily.com"}:
        return None
    match = pattern.fullmatch(parts.path)
    return match.group(1) if match else None


class ToonilySource(DirectSource):
    name = "toonily"
    image_limiter = _image_limiter

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Referer": BASE_URL + "/"},
            timeout=60, trust_env=False, follow_redirects=True,
        )

    async def _html(self, path: str, *, method: str = "GET", params: dict | None = None) -> BeautifulSoup:
        response = await self.discovery_request(method, BASE_URL + path, limiter=_limiter, params=params)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        if soup.title and any(text in soup.title.get_text().lower() for text in ("just a moment", "access denied")):
            raise RuntimeError("Toonily returned an access challenge; try again later or disable this source.")
        return soup

    async def search_series(self, query: str) -> list[SourceSeries]:
        if not query.strip():
            return []
        soup = await self._html("/", params={"s": query.strip(), "post_type": "wp-manga"})
        results: dict[str, SourceSeries] = {}
        for anchor in soup.select(".post-title a, .tab-summary h3 a, .post-title h3 a, h3 a, h2 a"):
            slug = _site_path(str(anchor.get("href") or ""), _SERIES)
            title = anchor.get_text(" ", strip=True)
            if slug and title and slug not in results:
                results[slug] = SourceSeries(self.name, slug, title, f"{BASE_URL}/serie/{slug}/")
        return list(results.values())[:20]

    async def list_chapters(self, external_id: str) -> list[SourceChapter]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", external_id):
            raise ValueError("Invalid Toonily series identifier")
        path = f"/serie/{external_id}/"
        soup = await self._html(path)
        chapters = self._chapters(soup, external_id)
        # Some Madara deployments load the chapter list separately.
        if not chapters and soup.select_one(".listing-chapters_wrap, .c-page__content"):
            soup = await self._html(path + "ajax/chapters/", method="POST")
            chapters = self._chapters(soup, external_id)
        return sorted(chapters.values(), key=lambda c: c.number)

    def _chapters(self, soup: BeautifulSoup, series_id: str) -> dict[float, SourceChapter]:
        chapters: dict[float, SourceChapter] = {}
        for anchor in soup.select("a[href]"):
            identifier = _site_path(str(anchor.get("href") or ""), _CHAPTER)
            if not identifier or not identifier.startswith(series_id + "/"):
                continue
            # Parse the URL's explicit chapter number. Labels such as Side Story
            # 1 must not overwrite main chapter 1 when the URL says chapter-180.
            number = parse_chapter_number(identifier.split("/", 1)[1].replace("-", " "))
            if number is not None and number not in chapters:
                chapters[number] = SourceChapter(
                    self.name, identifier, number,
                    title=anchor.get_text(" ", strip=True),
                    url=f"{BASE_URL}/serie/{identifier}/",
                )
        return chapters

    async def get_pages(self, chapter_external_id: str) -> list[str]:
        path = f"/serie/{chapter_external_id}/"
        if _site_path(path, _CHAPTER) != chapter_external_id:
            raise ValueError("Invalid Toonily chapter identifier")
        referer = BASE_URL + path
        soup = await self._html(path)
        pages: list[str] = []
        seen: set[str] = set()
        for image in soup.select(".reading-content img, .page-break img"):
            raw = str(image.get("data-src") or image.get("data-lazy-src") or image.get("src") or "").strip()
            if not raw or raw.startswith("data:"):
                continue
            parts = urlsplit(urljoin(referer, raw))
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                continue
            actual = urlunsplit(parts._replace(fragment=""))
            if actual not in seen:
                seen.add(actual)
                pages.append(actual + "#" + urlencode({"mangarr_referer": referer}))
        return pages

    async def download_page(self, client: httpx.AsyncClient, url: str) -> bytes:
        actual, _, fragment = url.partition("#")
        referer = (parse_qs(fragment).get("mangarr_referer") or [""])[0]
        if not _site_path(referer, _CHAPTER):
            raise ValueError("Missing Toonily chapter Referer")
        response = await rl_request(
            client, "GET", actual, limiter=self.image_limiter,
            headers={"User-Agent": USER_AGENT, "Referer": referer},
        )
        response.raise_for_status()
        return response.content


source = ToonilySource()
