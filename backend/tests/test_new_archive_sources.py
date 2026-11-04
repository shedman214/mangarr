import httpx
import pytest

from mangarr.sources.mangakatana import MangaKatanaSource
from mangarr.sources.manganato import ManganatoSource
from mangarr.sources.toomics import ToomicsSource
from mangarr.sources.urls import series_from_url
from mangarr import settings_service


def source(cls, handler):
    return cls(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("url,name,identifier", [
    ("https://mangakatana.com/manga/red-blue.26291", "mangakatana", "red-blue.26291"),
    ("https://www.manganato.gg/manga/red-blue", "manganato", "red-blue"),
    ("https://toomics.com/en/webtoon/episode/toon/6064", "toomics", "6064"),
])
def test_manual_links(url, name, identifier):
    result = series_from_url(url)
    assert (result.source_name, result.external_id) == (name, identifier)


async def test_katana_chapter_scope_decimal_and_array_order():
    def handler(request):
        if request.url.path.endswith("c1.5"):
            return httpx.Response(200, text="<script>var thzq=['https://cdn.example/2.jpg','https://cdn.example/1.jpg',];</script>")
        return httpx.Response(200, text='<a href="/manga/example.123/c1.5">Bonus</a><a href="/manga/other.44/c9">Other</a>')
    s = source(MangaKatanaSource, handler)
    try:
        chapters = await s.list_chapters("example.123")
        assert [c.number for c in chapters] == [1.5]
        pages = await s.get_pages(chapters[0].external_id)
        assert [p.split("#")[0] for p in pages] == ["https://cdn.example/2.jpg", "https://cdn.example/1.jpg"]
        def image(request):
            assert request.headers["Referer"].endswith("/example.123/c1.5")
            assert not request.url.fragment
            return httpx.Response(200, content=b"image")
        async with httpx.AsyncClient(transport=httpx.MockTransport(image)) as client:
            assert await s.download_page(client, pages[0]) == b"image"
        with pytest.raises(ValueError):
            await s.get_pages("../../bad")
    finally:
        await s._client.aclose()


async def test_manganato_all_pages_and_decimal_numbers():
    offsets = []
    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json={"success": True, "data": {"chapters": [{"chapter_slug": "chapter-2" if offset == 0 else "chapter-1-5", "chapter_num": 2 if offset == 0 else 1.5}], "pagination": {"has_more": offset == 0}}})
    s = source(ManganatoSource, handler)
    try:
        chapters = await s.list_chapters("example")
        assert offsets == [0, 1]
        assert [c.number for c in chapters] == [1.5, 2]
    finally:
        await s._client.aclose()


async def test_manganato_pagination_failure_does_not_return_partial_list():
    def handler(request):
        return httpx.Response(200, json={"success": True, "data": {"chapters": [{"chapter_slug": "chapter-1", "chapter_num": 1}], "pagination": {"has_more": True}}})
    s = source(ManganatoSource, handler)
    try:
        with pytest.raises(RuntimeError, match="stopped advancing"):
            await s.list_chapters("example")
    finally:
        await s._client.aclose()


async def test_toomics_explicit_free_only_session_and_no_purchase_links():
    def handler(request):
        assert request.headers["Cookie"] == "sample=session"
        return httpx.Response(200, text='''
          <a class="js-episode-link" onclick="location.href='/en/webtoon/detail/code/123/ep/1/toon/6064'"><span>FREE</span><div class="cell-title">Episode one</div></a>
          <a class="js-episode-link" onclick="location.href='/en/webtoon/detail/code/124/ep/2/toon/6064'">VIP ONLY</a>
          <a class="js-episode-link" onclick="popup('/purchase')">FREE</a>''')
    s = source(ToomicsSource, handler)
    s.configure("sample=session")
    try:
        assert [c.number for c in await s.list_chapters("6064")] == [1]
        with pytest.raises(ValueError):
            s.configure("bad\r\nInjected: value")
    finally:
        await s._client.aclose()


def test_new_sources_are_opt_in_with_independent_proxy_settings():
    for name in ["toomics", "manganato", "mangakatana"]:
        assert settings_service.DEFAULTS[f"source_{name}_enabled"] == "false"
        assert settings_service.DEFAULTS[f"source_{name}_proxy_enabled"] == "false"
        assert settings_service.DEFAULTS[f"source_{name}_discovery_proxy_enabled"] == "false"
    assert "toomics_session_cookie" in settings_service.SECRET_KEYS


@pytest.mark.parametrize("status", [403, 503])
async def test_cloudflare_blocks_are_reported_plainly(status):
    def handler(request):
        return httpx.Response(status, headers={"server": "cloudflare"}, text="Attention Required")
    s = source(ManganatoSource, handler)
    try:
        with pytest.raises(RuntimeError, match=f"blocking Mangarr's requests \\(Cloudflare {status}\\)"):
            await s.search_series("red blue")
        page = "https://img.example/1.webp#" + "mangarr_referer=https%3A%2F%2Fwww.manganato.gg%2Fmanga%2Fred-blue%2Fchapter-1"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="image server is blocking Mangarr"):
                await s.download_page(client, page)
    finally:
        await s._client.aclose()


async def test_other_errors_are_unchanged():
    s = source(ManganatoSource, lambda request: httpx.Response(404, text="missing"))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await s.search_series("red blue")
    finally:
        await s._client.aclose()
