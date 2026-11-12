"""Add New Series also lists MangaBaka entries that neither MangaUpdates nor
AniList has (western comics and webtoons such as Dungeon Crawler Carl), and
series can be added and refreshed from MangaBaka."""
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import adding
from mangarr.api import search
from mangarr.jobs import tasks
from mangarr.metadata.base import SeriesMetadata
from mangarr.metadata.mangabaka import MangaBakaClient, linked_elsewhere, to_metadata
from mangarr.models import Base, Series, SeriesStatus

# trimmed from api.mangabaka.org/v1/series/362153
DCC = {
    "id": 362153, "state": "active", "title": "Dungeon Crawler Carl",
    "native_title": None, "romanized_title": None,
    "secondary_titles": {"unknown": [{"title": "Dungeon Crawler Carl"}, {"title": "DCC"}]},
    "titles": [{"language": "en", "title": "Dungeon Crawler Carl", "is_primary": True}],
    "cover": {"raw": {"url": "https://images.mangabaka.dev/dcc"}},
    "description": "Carl and Princess Donut.", "year": 2025, "status": "releasing",
    "total_chapters": "61", "final_volume": None, "genres": None, "publishers": [],
    "source": {"anilist": {"id": None}, "manga_updates": {"id": None}},
}
FRIEREN = {
    "id": 1995, "state": "active", "title": "Frieren: Beyond Journey's End", "year": 2020,
    "status": "hiatus", "total_chapters": "147", "final_volume": "15",
    "genres": ["slice_of_life", "fantasy"],
    "source": {"anilist": {"id": 118586}, "manga_updates": {"id": "ugf5dzu"}},
}


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def client(handler) -> MangaBakaClient:
    return MangaBakaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_record_becomes_series_metadata():
    meta = to_metadata(DCC)
    assert (meta.provider, meta.provider_id, meta.title) == ("mangabaka", "362153", "Dungeon Crawler Carl")
    assert meta.alt_titles == ["DCC"]  # the title itself is not repeated
    assert (meta.status, meta.year, meta.total_chapters, meta.total_volumes) == ("releasing", 2025, 61, None)
    assert meta.cover_url == "https://images.mangabaka.dev/dcc"
    frieren = to_metadata(FRIEREN)
    assert (frieren.status, frieren.total_volumes, frieren.genres) == ("hiatus", 15, ["Slice Of Life", "Fantasy"])
    assert to_metadata({**DCC, "status": "completed"}).status == "finished"
    assert to_metadata({**DCC, "status": "upcoming"}).status == "not_yet_released"
    assert to_metadata({**DCC, "status": "something-new", "total_chapters": "12.5"}).status == "unknown"
    assert to_metadata({**DCC, "total_chapters": "12.5"}).total_chapters is None


def test_linked_elsewhere():
    assert not linked_elsewhere(DCC)
    assert linked_elsewhere(FRIEREN)
    assert linked_elsewhere({**DCC, "source": {"anilist": {"id": 5}}})


async def test_search_unlinked_keeps_only_what_the_others_lack():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [DCC, FRIEREN, {**DCC, "id": 9, "state": "merged"}]})
    found = await client(handler).search_unlinked("Dungeon Crawler Carl", limit=999)
    assert [m.provider_id for m in found] == ["362153"]
    assert seen["params"] == {"q": "Dungeon Crawler Carl", "limit": "50"}


async def test_get_series_follows_a_merge():
    def handler(request):
        if request.url.path.endswith("/series/1"):
            return httpx.Response(200, json={"data": {"id": 1, "state": "merged", "merged_with": 362153}})
        return httpx.Response(200, json={"data": DCC})
    meta = await client(handler).get_series("1")
    assert meta.provider_id == "362153"
    assert await client(lambda r: httpx.Response(404, json={})).get_series("5") is None


def mu_result(title, provider_id="77", year=2020):
    return SeriesMetadata(provider="mangaupdates", provider_id=provider_id, title=title, year=year)


async def test_add_series_search_lists_mangabaka_after_mangaupdates(maker, monkeypatch):
    async def mu_search(query, limit=20):
        return [mu_result("Ka'er Huaite"), mu_result("Dungeon Crawler Carl", "88", 2025)]

    async def mb_unlinked(query, limit=20):
        return [to_metadata(DCC), to_metadata({**DCC, "id": 5, "title": "Dungeon Crawler Carl Vol. 2"})]
    monkeypatch.setattr(search.mangaupdates, "search", mu_search)
    monkeypatch.setattr(search.mangabaka, "search_unlinked", mb_unlinked)
    async with maker() as session:
        session.add(Series(title="DCC 2", sort_title="dcc 2", mangabaka_id=5))
        await session.commit()
        results = await search.metadata_results(session, "mangaupdates", "dcc", include_mangabaka=True)
        # the same title and year MangaUpdates already listed is not shown twice
        assert [(r.provider, r.provider_id) for r in results] == [
            ("mangaupdates", "77"), ("mangaupdates", "88"), ("mangabaka", "5")]
        assert results[2].in_library and results[2].in_library_series_id is not None
        # library import keeps searching MangaUpdates only
        plain = await search.metadata_results(session, "mangaupdates", "dcc")
        assert [r.provider for r in plain] == ["mangaupdates", "mangaupdates"]
        with pytest.raises(HTTPException):
            await search.metadata_results(session, "other", "dcc")


async def test_a_mangabaka_outage_leaves_the_mangaupdates_results(maker, monkeypatch):
    async def mu_search(query, limit=20):
        return [mu_result("Something")]

    async def down(query, limit=20):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(search.mangaupdates, "search", mu_search)
    monkeypatch.setattr(search.mangabaka, "search_unlinked", down)
    async with maker() as session:
        results = await search.metadata_results(session, "mangaupdates", "x", include_mangabaka=True)
    assert [r.provider_id for r in results] == ["77"]


async def test_add_and_refresh_from_mangabaka(maker, monkeypatch):
    async def get_series(provider_id):
        return to_metadata({**DCC, "total_chapters": "62", "status": "hiatus"})
    monkeypatch.setattr(adding.mangabaka, "get_series", get_series)
    async with maker() as session:
        series = await adding.create_series(
            session, adding.AddOptions(root_folder_id=None), mangabaka_id=362153)
        assert (series.mangabaka_id, series.mangaupdates_id, series.anilist_id) == (362153, None, None)
        assert series.title == "Dungeon Crawler Carl" and series.total_chapters == 62
        with pytest.raises(adding.SeriesExists):
            await adding.create_series(session, adding.AddOptions(root_folder_id=None), mangabaka_id=362153)
        with pytest.raises(ValueError):
            await adding.create_series(
                session, adding.AddOptions(root_folder_id=None), mangabaka_id=1, mangaupdates_id=2)

        async def no_link(series):
            return False

        async def no_extras(*args, **kwargs):
            return None
        monkeypatch.setattr(tasks, "link_mangaupdates", no_link)
        monkeypatch.setattr(tasks.mangabaka, "get_series", get_series)
        monkeypatch.setattr(tasks, "refresh_comicinfo_extra", no_extras)
        series.title = "stale"
        await tasks.refresh_series_metadata(session, series)
        assert series.title == "Dungeon Crawler Carl" and series.status == SeriesStatus.HIATUS
