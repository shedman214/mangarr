"""Add New Series by id or link (mangabaka:, anilist:, mal:, mangaupdates:, or a pasted link)."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import search
from mangarr.metadata import id_lookup
from mangarr.metadata.base import SeriesMetadata
from mangarr.models import Base, Series


@pytest.mark.parametrize("query,expected", [
    ("mangabaka:126821", ("mangabaka", "126821")),
    ("  MB : 1331 ", ("mangabaka", "1331")),
    ("anilist:159441", ("anilist", "159441")),
    ("al:159441", ("anilist", "159441")),
    ("mal:20593", ("mal", "20593")),
    ("myanimelist:20593", ("mal", "20593")),
    ("mangaupdates:z7zkmto", ("mangaupdates", "z7zkmto")),
    ("mu:36271319846", ("mangaupdates", "36271319846")),
    ("https://mangabaka.org/manga/126821/Dame-Zettai-Fujun-Isei-Kouyuu", ("mangabaka", "126821")),
    ("https://mangabaka.org/oel/362153/Dungeon-Crawler-Carl", ("mangabaka", "362153")),
    ("https://anilist.co/manga/159441/Pick-Me-Up/", ("anilist", "159441")),
    ("https://myanimelist.net/manga/20593/Btooom", ("mal", "20593")),
    ("https://www.mangaupdates.com/series/gnv09ie/kimetsu-no-yaiba", ("mangaupdates", "gnv09ie")),
])
def test_ids_and_links_are_recognised(query, expected):
    assert id_lookup.parse(query) == expected


@pytest.mark.parametrize("query", [
    "Dame! Zettai! Fujun Isei Kouyuu", "Re:Zero", "anilist:abc", "mal:", "unknown:123",
    "https://example.com/manga/123", "Steins;Gate 0", "x" * 400,
])
def test_titles_are_not_mistaken_for_ids(query):
    assert id_lookup.parse(query) is None


def test_mangaupdates_ids_in_both_forms():
    assert id_lookup.mangaupdates_id("gnv09ie") == 36271319846 == id_lookup.mangaupdates_id("36271319846")


def meta(provider, pid, title="Series"):
    return SeriesMetadata(provider=provider, provider_id=str(pid), title=title)


def record(mu=None, al=None, rid=126821):
    return {"id": rid, "title": "Otome Netoria", "state": "active",
            "source": {"manga_updates": {"id": mu}, "anilist": {"id": al}}}


async def test_mangaupdates_is_preferred_then_anilist_then_mangabaka(monkeypatch):
    mu_get = AsyncMock(return_value=meta("mangaupdates", 77))
    al_get = AsyncMock(return_value=meta("anilist", 88))
    monkeypatch.setattr(id_lookup.mangaupdates, "get_series", mu_get)
    monkeypatch.setattr(id_lookup.anilist, "get_series", al_get)
    monkeypatch.setattr(id_lookup.mangabaka, "record_by_id", AsyncMock(return_value=record(mu="z7zkmto", al=109000)))
    found = await id_lookup.resolve("mangabaka", "126821")
    assert found.provider == "mangaupdates" and mu_get.await_args.args == (str(int("z7zkmto", 36)),)

    monkeypatch.setattr(id_lookup.mangabaka, "record_by_source", AsyncMock(return_value=record(al=50593)))
    found = await id_lookup.resolve("mal", "20593")
    assert found.provider == "anilist" and al_get.await_args.args == ("50593",)
    assert id_lookup.mangabaka.record_by_source.await_args.args == ("mal", 20593)

    monkeypatch.setattr(id_lookup.mangabaka, "record_by_id", AsyncMock(return_value=record(rid=362153)))
    found = await id_lookup.resolve("mangabaka", "362153")
    assert (found.provider, found.provider_id) == ("mangabaka", "362153")


async def test_an_anilist_id_mangabaka_does_not_know_still_resolves(monkeypatch):
    monkeypatch.setattr(id_lookup.mangabaka, "record_by_source", AsyncMock(return_value=None))
    monkeypatch.setattr(id_lookup.anilist, "get_series", AsyncMock(return_value=meta("anilist", 5)))
    assert (await id_lookup.resolve("anilist", "5")).provider_id == "5"
    monkeypatch.setattr(id_lookup.mangabaka, "record_by_id", AsyncMock(return_value=None))
    assert await id_lookup.resolve("mangabaka", "999999999") is None


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_add_new_series_shows_the_one_series_and_marks_it_owned(session, monkeypatch):
    session.add(Series(title="Otome Netoria", sort_title="otome netoria", mangaupdates_id=77))
    await session.commit()
    title_search = AsyncMock(side_effect=AssertionError("an id must not run a title search"))
    monkeypatch.setattr(search.mangaupdates, "search", title_search)
    monkeypatch.setattr(search.mangabaka, "search_unlinked", title_search)
    monkeypatch.setattr(id_lookup, "resolve", AsyncMock(return_value=meta("mangaupdates", 77, "Otome Netoria")))
    results = await search.metadata_results(session, "mangaupdates", "mangabaka:126821", include_mangabaka=True)
    assert [(r.provider, r.provider_id, r.in_library) for r in results] == [("mangaupdates", "77", True)]
    monkeypatch.setattr(id_lookup, "resolve", AsyncMock(return_value=None))
    assert await search.metadata_results(session, "mangaupdates", "mal:1", include_mangabaka=True) == []


async def test_library_import_keeps_treating_text_as_a_title(session, monkeypatch):
    monkeypatch.setattr(search.mangaupdates, "search", AsyncMock(return_value=[meta("mangaupdates", 9)]))
    resolve = AsyncMock()
    monkeypatch.setattr(id_lookup, "resolve", resolve)
    await search.metadata_results(session, "mangaupdates", "mal:20593")
    resolve.assert_not_awaited()
