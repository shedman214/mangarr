"""FanFox (MangaFox) adapter: search, chapter list (volumes, decimals, scope),
page list from the packed reader responses, Referer handling, manual links."""
import re

import httpx
import pytest

from mangarr import settings_service
from mangarr.sources import fanfox, registry
from mangarr.sources.fanfox import FanFoxSource, unpack
from mangarr.sources.urls import series_from_url

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def enc(n, radix=62):
    return (enc(n // radix, radix) if n >= radix else "") + DIGITS[n % radix]


def pack(code: str) -> str:
    """Minimal Dean Edwards style packer, matching the format FanFox serves."""
    words = list(dict.fromkeys(re.findall(r"\b\w+\b", code)))
    index = {w: enc(i) for i, w in enumerate(words)}
    payload = re.sub(r"\b\w+\b", lambda m: index[m.group(0)], code).replace("'", "\\'")
    return ("eval(function(p,a,c,k,e,d){e=function(c){return c};return p}"
            f"('{payload}',62,{len(words)},'{'|'.join(words)}'.split('|'),0,{{}}))")


def test_unpack_round_trip_and_rejects_other_text():
    code = "var guidkey=''+'5'+'e'+'2';$(\"#dm5_key\").val(guidkey);"
    assert unpack(pack(code)) == code
    with pytest.raises(RuntimeError):
        unpack("console.log('not packed')")


SEARCH = """<ul class="manga-list-4-list line">
<li><a href="/manga/kawaii_wa/" title="Kawaii wa Subete"><img></a>
<p class="manga-list-4-item-title"><a href="/manga/kawaii_wa/" title="Kawaii wa Subete">Kawaii wa Subete</a></p></li>
<li><a href="https://evil.example/manga/x/" title="Elsewhere"></a></li></ul>"""

SERIES = """<div id="chapterlist"><ul class="detail-main-list">
<li><a href="/manga/kawaii_wa/v02/c010.5/1.html" title="t"><p class="title3">Vol.02 Ch.010.5</p></a></li>
<li><a href="/manga/kawaii_wa/vTBD/c011/1.html" title="t"><p class="title3">Vol.TBD Ch.011</p></a></li>
<li><a href="/manga/kawaii_wa/c001/1.html" title="t"><p class="title3">Ch.001</p></a></li>
<li><a href="/manga/kawaii_wa/c001/1.html" title="t"><p class="title3">Ch.001</p></a></li>
<li><a href="/manga/other_title/c005/1.html" title="t"><p class="title3">Other</p></a></li>
</ul></div>"""

KEY_SCRIPT = "var guidkey=''+'a'+'b'+'1'+'2';$(\"#dm5_key\").val(guidkey);"
CHAPTER = ("<script>var comicid = 1; var chapterid =555; var imagepage=1; var imagecount=3;</script>"
           f"<script>{pack(KEY_SCRIPT)}</script>")


def fun(first, second=None):
    names = ",".join(f'"/{n}.jpg?token=t{n}"' for n in (first, second) if n)
    return pack('function dm5imagefun(){var pix="//cdn.example/store/1/c001";'
                f'var pvalue=[{names}];return pvalue}}var d;d=dm5imagefun();')


def make(handler):
    return FanFoxSource(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_search_and_chapter_list():
    def handler(request):
        if request.url.path == "/search":
            assert request.url.params["title"] == "kawaii"
            return httpx.Response(200, text=SEARCH)
        assert request.url.path == "/manga/kawaii_wa/"
        return httpx.Response(200, text=SERIES)
    s = make(handler)
    try:
        found = await s.search_series(" kawaii ")
        assert [(r.external_id, r.title, r.url) for r in found] == [
            ("kawaii_wa", "Kawaii wa Subete", "https://fanfox.net/manga/kawaii_wa/")]
        chapters = await s.list_chapters("kawaii_wa")
        assert [(c.external_id, c.number, c.volume, c.title) for c in chapters] == [
            ("kawaii_wa/c001", 1.0, None, "Ch.001"),
            ("kawaii_wa/v02/c010.5", 10.5, 2, "Vol.02 Ch.010.5"),
            ("kawaii_wa/vTBD/c011", 11.0, None, "Vol.TBD Ch.011"),
        ]
        with pytest.raises(ValueError):
            await s.list_chapters("../etc")
    finally:
        await s._client.aclose()


async def test_pages_follow_reader_pairs_and_keep_the_chapter_referer():
    seen = []

    def handler(request):
        if request.url.path == "/manga/kawaii_wa/v02/c010.5/1.html":
            return httpx.Response(200, text=CHAPTER)
        assert request.url.path == "/manga/kawaii_wa/v02/c010.5/chapterfun.ashx"
        assert request.headers["Referer"] == "https://fanfox.net/manga/kawaii_wa/v02/c010.5/1.html"
        assert request.url.params["cid"] == "555" and request.url.params["key"] == "ab12"
        page = int(request.url.params["page"])
        seen.append(page)
        return httpx.Response(200, text=fun("001", "002") if page == 1 else fun("003"))
    s = make(handler)
    try:
        pages = await s.get_pages("kawaii_wa/v02/c010.5")
        assert seen == [1, 3]
        assert [p.split("#")[0] for p in pages] == [
            "https://cdn.example/store/1/c001/001.jpg?token=t001",
            "https://cdn.example/store/1/c001/002.jpg?token=t002",
            "https://cdn.example/store/1/c001/003.jpg?token=t003"]

        def image(request):
            assert request.headers["Referer"] == "https://fanfox.net/manga/kawaii_wa/v02/c010.5/1.html"
            assert not request.url.fragment
            return httpx.Response(200, content=b"jpeg")
        async with httpx.AsyncClient(transport=httpx.MockTransport(image)) as client:
            assert await s.download_page(client, pages[0]) == b"jpeg"
        with pytest.raises(ValueError):
            await s.get_pages("kawaii_wa/../../x")
    finally:
        await s._client.aclose()


async def test_missing_reader_data_is_reported():
    s = make(lambda request: httpx.Response(200, text="<html>nothing</html>"))
    try:
        with pytest.raises(RuntimeError, match="no reader data"):
            await s.get_pages("kawaii_wa/c001")
    finally:
        await s._client.aclose()


@pytest.mark.parametrize("url", ["https://fanfox.net/manga/kawaii_wa/", "fanfox.net/manga/kawaii_wa",
                                 "https://m.fanfox.net/manga/kawaii_wa/"])
def test_manual_series_links(url):
    result = series_from_url(url)
    assert (result.source_name, result.external_id) == ("fanfox", "kawaii_wa")


def test_chapter_links_are_not_series_links():
    with pytest.raises(ValueError):
        series_from_url("https://fanfox.net/manga/kawaii_wa/c001/1.html")


def test_registered_and_opt_in():
    assert registry.DIRECT_SOURCES["fanfox"] is fanfox.source
    for key in ("source_fanfox_enabled", "source_fanfox_proxy_enabled", "source_fanfox_discovery_proxy_enabled"):
        assert settings_service.DEFAULTS[key] == "false"
