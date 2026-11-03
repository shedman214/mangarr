import asyncio
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import httpx
import pytest
import respx

from mangarr import settings_service
from mangarr.download.cbz import write_cbz
from mangarr.sources import registry, toonily
from mangarr.sources.toonily import BASE_URL, ToonilySource
from mangarr.sources.urls import series_from_url


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    async def acquire():
        pass
    monkeypatch.setattr(toonily._limiter, "acquire", acquire)
    monkeypatch.setattr(toonily._image_limiter, "acquire", acquire)


@respx.mock
async def test_search_filters_foreign_links_and_duplicates():
    route = respx.get(BASE_URL + "/", params={"s": "Example", "post_type": "wp-manga"}).respond(text='''
        <div class="post-title"><h3><a href="/serie/example/">Example</a></h3></div>
        <h3><a href="/serie/example/">Example</a></h3>
        <h3><a href="https://other.example/serie/wrong/">Wrong</a></h3>
        <h3><a href="/serie/example/chapter-1/">Not a series</a></h3>
    ''')
    async with httpx.AsyncClient() as client:
        results = await ToonilySource(client).search_series(" Example ")
    assert [(r.external_id, r.title) for r in results] == [("example", "Example")]
    assert route.called


CHAPTERS = '''
    <a href="/serie/example/chapter-2/">Chapter 2</a>
    <a href="/serie/example/chapter-1/">Read First</a>
    <a href="/serie/example/chapter-1/">Chapter 1</a>
    <a href="/serie/example/chapter-2.5/">Bonus</a>
    <a href="/serie/example/chapter-180/">Side Story 1</a>
    <a href="/serie/other/chapter-3/">Chapter 3</a>
    <a href="https://evil.example/serie/example/chapter-9/">Chapter 9</a>
'''


@respx.mock
async def test_chapters_preserve_decimal_numbers_and_series_scope():
    respx.get(BASE_URL + "/serie/example/").respond(text=CHAPTERS)
    async with httpx.AsyncClient() as client:
        chapters = await ToonilySource(client).list_chapters("example")
    assert [c.number for c in chapters] == [1, 2, 2.5, 180]
    assert chapters[-1].external_id == "example/chapter-180"


@respx.mock
async def test_ajax_chapter_list_fallback():
    respx.get(BASE_URL + "/serie/example/").respond(text='<div class="listing-chapters_wrap"></div>')
    respx.post(BASE_URL + "/serie/example/ajax/chapters/").respond(text=CHAPTERS)
    async with httpx.AsyncClient() as client:
        assert len(await ToonilySource(client).list_chapters("example")) == 4


@respx.mock
async def test_page_order_lazy_images_and_chapter_referer_are_preserved():
    chapter = BASE_URL + "/serie/example/chapter-1/"
    respx.get(chapter).respond(text='''
        <img src="https://cdn.example/advert.jpg">
        <div class="reading-content">
          <div class="page-break"><img data-src=" https://data.tnlycdn.com/010.jpg " src="placeholder.jpg"></div>
          <img data-lazy-src="https://data.tnlycdn.com/002.jpg">
          <img src="https://data.tnlycdn.com/002.jpg">
          <img src="data:image/gif;base64,abc">
        </div>
    ''')
    async with httpx.AsyncClient() as client:
        pages = await ToonilySource(client).get_pages("example/chapter-1")
    assert [p.split("#")[0] for p in pages] == [
        "https://data.tnlycdn.com/010.jpg", "https://data.tnlycdn.com/002.jpg",
    ]
    assert all(parse_qs(urlsplit(p).fragment)["mangarr_referer"] == [chapter] for p in pages)


@respx.mock
async def test_concurrent_chapters_keep_independent_referers_and_webp_format(tmp_path):
    async with httpx.AsyncClient() as client:
        source = ToonilySource(client)
        page_sets = []
        routes = []
        webp = b"RIFF\x10\x00\x00\x00WEBPexample"
        for number in (1, 2):
            chapter = f"{BASE_URL}/serie/example/chapter-{number}/"
            image = f"https://data.tnlycdn.com/{number}.jpg"
            respx.get(chapter).respond(text=f'<div class="reading-content"><img src="{image}"></div>')
            routes.append(respx.get(image).respond(content=webp, headers={"Content-Type": "image/webp"}))
            page_sets.append(await source.get_pages(f"example/chapter-{number}"))
        content = await asyncio.gather(*(source.download_page(client, pages[0]) for pages in page_sets))
        for number, route in enumerate(routes, start=1):
            request = route.calls[0].request
            assert request.headers["Referer"] == f"{BASE_URL}/serie/example/chapter-{number}/"
            assert "Mangarr" in request.headers["User-Agent"]
            assert not request.url.fragment
    archive = write_cbz(tmp_path / "test.cbz", content, "<ComicInfo/>")
    with ZipFile(archive) as zipped:
        assert zipped.namelist() == ["ComicInfo.xml", "001.webp", "002.webp"]


@respx.mock
async def test_access_denied_is_not_an_empty_success():
    respx.get(BASE_URL + "/serie/example/").respond(status_code=403)
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await ToonilySource(client).list_chapters("example")


async def test_invalid_chapter_identifier_never_requests_a_url():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await ToonilySource(client).get_pages("../../outside")


def test_registration_settings_and_direct_url():
    assert "toonily" in settings_service.CONTENT_SOURCE_NAMES
    assert settings_service.DEFAULTS["source_toonily_enabled"] == "false"
    values = {**settings_service.DEFAULTS, "source_toonily_enabled": "true", "source_priority": "mangadex"}
    assert registry.DIRECT_SOURCES["toonily"] in registry.enabled_direct_sources(values)
    assert "source_toonily_proxy_enabled" in values
    linked = series_from_url(BASE_URL + "/serie/example/")
    assert (linked.source_name, linked.external_id) == ("toonily", "example")
