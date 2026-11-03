"""Shared HTML request and chapter-scoped image handling for archive sources."""
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
import re

import httpx
from bs4 import BeautifulSoup

from .. import USER_AGENT
from ..util import RateLimiter, rl_request
from .base import DirectSource


class HtmlArchive(DirectSource):
    base_url: str
    chapter_pattern: str
    image_limiter = None

    def __init__(self, client=None):
        self._client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=45, follow_redirects=True, trust_env=False)
        self.limiter = RateLimiter(rate=1, per_seconds=1)
        self.image_limiter = RateLimiter(rate=3, per_seconds=1)

    async def request(self, path, **kwargs):
        response = await self.discovery_request("GET", self.base_url + path, limiter=self.limiter, **kwargs)
        response.raise_for_status()
        return response

    async def html(self, path, **kwargs):
        response = await self.request(path, **kwargs)
        soup = BeautifulSoup(response.text, "lxml")
        if soup.title and any(t in soup.title.get_text().lower() for t in ("just a moment", "access denied")):
            raise RuntimeError(f"{self.name} returned an access challenge")
        return soup

    def identifier(self, url, pattern):
        parts = urlsplit(urljoin(self.base_url, url))
        if parts.scheme not in {"http", "https"} or parts.netloc.lower().removeprefix("www.") != urlsplit(self.base_url).netloc.removeprefix("www."):
            return None
        match = re.fullmatch(pattern, parts.path.rstrip("/"))
        return match.group(1) if match else None

    def page_urls(self, raw_urls, chapter_path):
        referer = self.base_url + chapter_path
        pages = []
        seen = set()
        for raw in raw_urls:
            raw = raw.strip()
            if not raw or raw.startswith(("#", "data:")):
                continue
            parts = urlsplit(urljoin(referer, raw))
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                continue
            url = urlunsplit(parts._replace(fragment=""))
            if url not in seen:
                seen.add(url)
                pages.append(url + "#" + urlencode({"mangarr_referer": referer}))
        if not pages:
            raise RuntimeError(f"{self.name} returned no readable page images; the chapter may be restricted or the site layout changed")
        return pages

    async def download_page(self, client, url):
        actual, _, fragment = url.partition("#")
        referer = (parse_qs(fragment).get("mangarr_referer") or [""])[0]
        if not self.identifier(referer, self.chapter_pattern):
            raise ValueError("Missing or invalid chapter Referer")
        response = await rl_request(client, "GET", actual, limiter=self.image_limiter, headers={"User-Agent": USER_AGENT, "Referer": referer})
        response.raise_for_status()
        return response.content
