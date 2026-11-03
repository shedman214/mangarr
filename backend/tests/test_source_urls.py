from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from mangarr.api import library
from mangarr.sources.base import SourceSeries
from mangarr.sources.urls import series_from_url


@pytest.mark.parametrize("url,source,identifier", [
    ("https://mangafire.to/title/1o1nv-example-title", "mangafire", "1o1nv"),
    (" mangafire.to/title/abc-a-title/?utm_source=test#chapters ", "mangafire", "abc"),
    ("https://www.mangadex.org/title/12345678-abcd-abcd-abcd-123456789abc/title", "mangadex", "12345678-abcd-abcd-abcd-123456789abc"),
    ("https://weebcentral.com/series/ABC123/Example", "weebcentral", "ABC123"),
    ("https://atsu.moe/manga/abc-123", "atsumaru", "abc-123"),
    ("https://asurascans.com/comics/example-hash", "asura", "example-hash"),
    ("https://tcbonepiecechapters.com/mangas/12/example", "tcbscans", "12/example"),
    ("https://mangaplus.shueisha.co.jp/titles/100001", "mangaplus", "100001"),
    ("https://www.webtoons.com/en/action/example/list?title_no=123&foo=bar", "webtoons", "123"),
    ("https://www.viz.com/shonenjump/chapters/example", "viz", "/shonenjump/chapters/example"),
    ("https://en.wikipedia.org/wiki/Example_%28manga%29", "wikipedia", "Example (manga)"),
])
def test_supported_series_links(url, source, identifier):
    candidate = series_from_url(url)
    assert candidate.source_name == source
    assert candidate.external_id == identifier
    assert candidate.url.startswith("https://")
    assert "utm_source" not in candidate.url
    assert "#" not in candidate.url


@pytest.mark.parametrize("query", ["One Piece", "Example: Another Title", "A Title / Subtitle"])
def test_titles_are_not_urls(query):
    assert series_from_url(query) is None


@pytest.mark.parametrize("url", [
    "", "https://example.com/title/123", "https://mangafire.to.evil.com/title/abc",
    "https://user:password@mangafire.to/title/abc", "https://mangafire.to:8080/title/abc",
    "ftp://mangafire.to/title/abc", "https://mangafire.to/title/abc/chapter/123",
    "https://mangafire.to/search?keyword=example", "https://mangafire.to/title/",
    "https://mangafire.to/title/%2e%2e", "https://mangadex.org/title/not-a-uuid",
    "https://www.webtoons.com/en/canvas/example/list?title_no=123",
    "https://www.webtoons.com/en/action/example/list",
    "https://www.webtoons.com/en/action/example/list?title_no=1&title_no=2",
    "https://en.wikipedia.org/wiki/File:Example",
])
def test_invalid_links_are_rejected(url):
    with pytest.raises(ValueError):
        series_from_url(url)


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(library, "_load", AsyncMock())
    configure = AsyncMock(return_value={})
    monkeypatch.setattr(library.registry, "apply_settings", configure)
    return configure


async def test_url_search_detects_source_without_network_or_saving(endpoint):
    results = await library.source_search(1, "mangadex", "https://mangafire.to/title/abc-example", session=None)
    assert len(results) == 1
    assert results[0].source_name == "mangafire"
    assert results[0].external_id == "abc"
    endpoint.assert_not_awaited()


async def test_title_search_still_uses_selected_source(endpoint, monkeypatch):
    source = AsyncMock()
    source.search_series.return_value = [SourceSeries("mangafire", "abc", "Example")]
    monkeypatch.setitem(library.registry.DIRECT_SOURCES, "mangafire", source)
    results = await library.source_search(1, "mangafire", "Example", session=None)
    assert results[0].title == "Example"
    source.search_series.assert_awaited_once_with("Example")


async def test_invalid_url_is_a_validation_error(endpoint):
    with pytest.raises(HTTPException) as exc:
        await library.source_search(1, "mangafire", "https://example.com/title/123", session=None)
    assert exc.value.status_code == 422
    endpoint.assert_not_awaited()
