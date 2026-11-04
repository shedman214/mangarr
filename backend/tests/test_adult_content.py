"""Settings → Sources → Adult content: off by default; when on, each source lifts
its own adult/mature filter — and only then."""
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import settings_service
from mangarr.models import Base
from mangarr.sources import registry
from mangarr.sources.fanfox import FanFoxSource
from mangarr.sources.mangadex import MangaDexSource
from mangarr.sources.toonily import ToonilySource
from mangarr.sources.weebcentral import WeebCentralSource


def test_off_by_default_and_validated():
    assert settings_service.DEFAULTS["adult_content"] == "false"
    settings_service.validate({**settings_service.DEFAULTS, "adult_content": "true"})
    with pytest.raises(ValueError):
        settings_service.validate({**settings_service.DEFAULTS, "adult_content": "yes"})


async def test_saving_the_setting_reaches_every_source():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sources = {**registry.DIRECT_SOURCES, **registry.TORRENT_INDEXERS}.values()
    try:
        async with async_sessionmaker(engine)() as session:
            await settings_service.set_many(session, {"adult_content": "true"})
            await registry.apply_settings(session)
            assert all(s.adult_content for s in sources)
            await settings_service.set_many(session, {"adult_content": "false"})
            await registry.apply_settings(session)
            assert not any(s.adult_content for s in sources)
    finally:
        for s in sources:
            s.adult_content = False
        await engine.dispose()


def test_mangadex_ratings():
    s = MangaDexSource()
    assert s._ratings() == ["safe", "suggestive", "erotica"]
    s.adult_content = True
    assert s._ratings() == ["safe", "suggestive", "erotica", "pornographic"]


@pytest.mark.parametrize("adult", [False, True])
async def test_mangadex_search_and_feed_send_ratings(adult):
    seen = []

    def handler(request):
        seen.append(request.url.params.get_list("contentRating[]"))
        if request.url.path.endswith("/feed"):
            return httpx.Response(200, json={"data": [], "total": 0, "limit": 500, "offset": 0})
        return httpx.Response(200, json={"data": []})
    s = MangaDexSource(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    s.adult_content = adult
    await s.search_series("x")
    await s.list_chapters("32d76d19-8a05-4db0-9fc2-e0b0648fe9d0")
    assert seen and all(("pornographic" in r) is adult for r in seen)


@pytest.mark.parametrize("adult", [False, True])
async def test_fanfox_sends_its_over_18_confirmation_only_when_on(adult):
    cookies = []

    def handler(request):
        cookies.append(request.headers.get("Cookie"))
        return httpx.Response(200, text='<ul class="manga-list-4-list"></ul>')
    s = FanFoxSource(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    s.adult_content = adult
    await s.search_series("x")
    assert cookies == (["isAdult=1"] if adult else [None])


@pytest.mark.parametrize("adult", [False, True])
async def test_toonily_sends_its_mature_confirmation_only_when_on(adult):
    cookies = []

    def handler(request):
        cookies.append(request.headers.get("Cookie"))
        return httpx.Response(200, text="<html><body></body></html>")
    s = ToonilySource(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    s.adult_content = adult
    await s.search_series("x")
    assert cookies and all((c == "toonily-mature=1") is adult for c in cookies)


@pytest.mark.parametrize("adult", [False, True])
async def test_weebcentral_searches_adult_titles_only_when_on(adult):
    params = []

    def handler(request):
        params.append(dict(request.url.params))
        return httpx.Response(200, text="<html></html>")
    s = WeebCentralSource(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    s.adult_content = adult
    await s.search_series("x")
    assert params[0].get("adult") == ("Any" if adult else None)
