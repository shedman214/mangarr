from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx

from ..util import RateLimiter, rl_request


@dataclass
class SourceSeries:
    source_name: str
    external_id: str
    title: str
    url: str = ""
    alt_titles: list[str] = field(default_factory=list)


@dataclass
class SourceChapter:
    source_name: str
    external_id: str  # what get_pages needs to fetch this chapter
    number: float
    volume: int | None = None
    title: str = ""
    language: str = "en"
    url: str = ""
    # scanlation group(s) behind this copy, when the source says. Sources
    # that carry several groups' copies of a chapter list every copy, in
    # their own preference order; automation.select_group_variants picks one.
    group: str = ""


@dataclass
class ChapterMetadata:
    """Metadata a source knows about a chapter without necessarily serving it.

    ``number`` is optional because printed tables of contents frequently list
    an "Extra" or "Bonus" by title and position but give it no canonical
    number.  Those rows are reconciled with decimal-numbered local chapters
    later, when the rest of the series provides enough context.
    """

    source_name: str
    number: float | None
    volume: int | None = None
    title: str = ""
    kind: str = "chapter"  # chapter | extra
    url: str = ""


@dataclass
class TorrentRelease:
    source_name: str
    title: str
    magnet: str
    url: str = ""
    torrent_url: str = ""
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0


class SourceNetwork:
    """Separate, opt-in routing for source discovery; image routing is independent."""

    name: str
    _client: httpx.AsyncClient
    discovery_proxy_enabled: bool = False
    discovery_proxy_url: str = ""

    @asynccontextmanager
    async def discovery_client(self):
        enabled, proxy = self.discovery_proxy_enabled, self.discovery_proxy_url
        if not enabled:
            yield self._client
            return
        if not proxy:
            raise RuntimeError(f"{self.name} discovery proxy is enabled but has no URL")
        async with httpx.AsyncClient(
            proxy=proxy, headers=self._client.headers, timeout=self._client.timeout,
            cookies=self._client.cookies, trust_env=False,
            follow_redirects=self._client.follow_redirects,
        ) as client:
            try:
                yield client
            finally:
                self._client.cookies.update(client.cookies)

    async def discovery_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async with self.discovery_client() as client:
            return await rl_request(client, method, url, **kwargs)


class DirectSource(SourceNetwork, ABC):
    """A site we can search, list chapters on, and pull page images from."""

    name: str
    # Configured at runtime by registry.apply_settings(). Only page/image
    # bytes use this; source discovery has its own opt-in routing above.
    content_proxy_enabled: bool = False
    content_proxy_url: str = ""
    # per-source proactive limiter for page-image fetches (separate from the
    # HTML/API limiter so images can be pulled a bit faster). Sources override.
    image_limiter: RateLimiter | None = None

    @abstractmethod
    async def search_series(self, query: str) -> list[SourceSeries]: ...

    @abstractmethod
    async def list_chapters(self, external_id: str) -> list[SourceChapter]:
        """Chapters the source serves. May hold several copies of one chapter
        number (one per scanlation group), best first."""

    @abstractmethod
    async def get_pages(self, chapter_external_id: str) -> list[str]:
        """Returns ordered page image URLs."""

    def image_headers(self) -> dict:
        """Extra headers image CDNs need (e.g. a Referer). Override per source."""
        return {}

    async def download_page(self, client: httpx.AsyncClient, url: str) -> bytes:
        """Fetch one page image — rate-limited, with reactive back-off on 429
        and retry on transient errors (shared by all sources)."""
        resp = await rl_request(
            client, "GET", url, limiter=self.image_limiter, headers=self.image_headers()
        )
        resp.raise_for_status()
        return resp.content

    async def get_volume_map(self, external_id: str) -> dict[float, int]:
        """chapter number → volume number, for sources that know volume
        assignments even for chapters they can't serve. Optional."""
        return {}

    async def get_chapter_metadata(self, external_id: str) -> list[ChapterMetadata]:
        """Optional title/volume metadata for chapters this source cannot
        necessarily serve.  Kept separate from ``list_chapters`` so an
        official catalogue or Wikipedia never appears as a download source.
        """
        return []


class TorrentIndexer(SourceNetwork, ABC):
    name: str

    @abstractmethod
    async def search(self, query: str) -> list[TorrentRelease]: ...

    async def get_torrent_metadata(self, release: TorrentRelease) -> bytes:
        """Fetch a small .torrent file for coverage inspection, if offered."""
        return b""
