from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import settings_service
from mangarr.api import discovery
from mangarr.models import Base, Series


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def media(identifier, title, **extra):
    return {
        "id": identifier, "type": "MANGA", "format": "MANGA", "isAdult": False,
        "title": {"english": title}, **extra,
    }


@pytest.mark.parametrize("adult_content", [True, False])
async def test_release_query_omits_adult_filter_when_adult_content_is_allowed(adult_content, monkeypatch):
    query = AsyncMock(return_value={"Page": {"media": []}})
    monkeypatch.setattr(discovery.anilist, "_query", query)
    await discovery.anilist.new_releases(20261031, 20261201, adult_content, 1)
    variables = query.await_args.args[1]
    if adult_content:
        assert "includeAdult" not in variables
    else:
        assert variables["includeAdult"] is False


async def test_new_releases_skip_owned_titles_and_use_complete_dates(session, monkeypatch):
    session.add(Series(title="Local Title", sort_title="local title", alt_titles="Official Title",
                       mangaupdates_id=10))
    await session.commit()
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1)
    end = (first + timedelta(days=32)).replace(day=1)
    full_date = {"year": today.year, "month": today.month, "day": today.day}
    provider = AsyncMock(side_effect=[
        ([media(1, "Official Title", startDate=full_date),
          media(2, "Unscheduled", startDate={"year": today.year}),
          media(3, "Mature", startDate=full_date, isAdult=True),
          *(media(i, f"Debut {i}", startDate=full_date) for i in range(10, 23))], True),
        ([media(4, "New Series", startDate=full_date, description="A new adventure",
                status="RELEASING", genres=["Adventure"]),
          media(4, "New Series", startDate=full_date),
          media(5, "Official Title", startDate=full_date)], False),
    ])
    monkeypatch.setattr(discovery.anilist, "new_releases", provider)

    result = await discovery.releases(session)
    assert [(item.provider_id, item.release_date) for item in result] == [
        *((str(i), today) for i in range(10, 23)), ("4", today),
    ]
    assert (result[-1].year, result[-1].status, result[-1].genres, result[-1].description) == (
        today.year, "releasing", ["Adventure"], "A new adventure",
    )
    provider.assert_any_await(discovery._fuzzy_date(first - timedelta(days=1)),
                             discovery._fuzzy_date(end), False, 2)


async def test_recommendations_use_kavita_reads_and_filter_library(session, monkeypatch):
    await settings_service.set_many(session, {
        "kavita_enabled": "true", "kavita_url": "https://kavita.example",
        "kavita_api_key": "fixture-key",
    })
    session.add_all([
        Series(title="First read", sort_title="first read", anilist_id=10),
        Series(title="Second read", sort_title="second read", anilist_id=11),
        Series(title="Already owned", sort_title="already owned", mangaupdates_id=20),
    ])
    await session.commit()
    read = AsyncMock(return_value=["First read", "First read", "Second read"])
    monkeypatch.setattr(discovery.kavita.KavitaClient, "recently_read_titles", read)
    # an older Kavita without the series list: titles only
    monkeypatch.setattr(discovery.kavita.KavitaClient, "reading_profile", AsyncMock(return_value=[]))

    def scored(node, rating=10):
        return {"rating": rating, "mediaRecommendation": node}

    provider = AsyncMock(side_effect=[({
        10: [scored(media(20, "Elsewhere", synonyms=["Already owned"])),
             scored(media(11, "Second read")), scored(media(30, "Worth Trying"))],
        11: [scored(media(30, "Worth Trying"), rating=50),
             scored(media(41, "Prose", format="NOVEL")),
             scored(media(42, "Animation", type="ANIME")),
             scored(media(43, "Mature", isAdult=True))],
    }, True), ({
        11: [*(scored(media(i, f"Recommendation {i}")) for i in range(50, 63)),
             scored(media(64, "Already owned"))],
    }, False)])
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)

    result = await discovery.recommendations(session)
    assert [invocation.args for invocation in provider.await_args_list] == [([10, 11], 1), ([10, 11], 2)]
    assert result.state == "ready"
    assert [(item.title, item.read_title) for item in result.items] == [
        ("Worth Trying", "Second read"),
        *((f"Recommendation {i}", "Second read") for i in range(50, 63)),
    ]


async def test_recommendations_without_kavita_do_not_call_provider(session, monkeypatch):
    provider = AsyncMock()
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)
    result = await discovery.recommendations(session)
    assert result.state == "needs_kavita" and not result.items
    provider.assert_not_awaited()



from mangarr import kavita  # noqa: E402
from mangarr.models import DiscoveryHidden  # noqa: E402


def entry(name, anilist_id=None, pages=100, pages_read=0, rating=0.0, rated=False):
    return kavita.ReadEntry(name=name, anilist_id=anilist_id, pages=pages, pages_read=pages_read,
                            rating=rating, rated=rated)


def test_kavita_series_rows_become_read_entries():
    row = {"name": "Btooom!", "aniListId": 7, "pages": 200, "pagesRead": 200,
           "userRating": 5, "hasUserRated": True}
    e = kavita.ReadEntry.from_series(row)
    assert (e.name, e.anilist_id, e.finished, e.rating, e.rated) == ("Btooom!", 7, True, 5.0, True)
    assert kavita.ReadEntry.from_series({"name": "x", "aniListId": 0, "pages": None}).anilist_id is None
    assert kavita.ReadEntry.from_series({"name": " "}) is None


def test_seed_weights_and_reasons():
    finished = entry("A", pages_read=100)
    started = entry("B", pages_read=10)
    loved = entry("C", pages_read=100, rating=5, rated=True)
    disliked = entry("D", pages_read=100, rating=1.5, rated=True)
    unknown = kavita.ReadEntry(name="E")
    assert discovery.seed_weight(loved) > discovery.seed_weight(finished) > discovery.seed_weight(started) > 0
    assert discovery.seed_weight(disliked) == 0
    assert discovery.seed_weight(unknown) == 0.65
    assert discovery.read_detail(finished) == "finished"
    assert discovery.read_detail(entry("F", pages_read=45)) == "read 45%"
    assert discovery.read_detail(unknown) is None


async def kavita_settings(session):
    await settings_service.set_many(session, {
        "kavita_enabled": "true", "kavita_url": "https://kavita.example", "kavita_api_key": "fixture-key",
    })


async def test_picks_use_kavita_anilist_ids_progress_ratings_and_skip_hidden(session, monkeypatch):
    await kavita_settings(session)
    session.add(DiscoveryHidden(anilist_id=33, title="Hidden one"))
    await session.commit()
    reads = [
        entry("Just started", anilist_id=1, pages_read=5),
        entry("Loved it", anilist_id=2, pages_read=100, rating=5, rated=True),
        entry("Disliked", anilist_id=3, pages_read=100, rating=1, rated=True),
    ]
    monkeypatch.setattr(discovery.kavita.KavitaClient, "reading_profile", AsyncMock(return_value=reads))
    titles = AsyncMock(side_effect=AssertionError("titles are only a fallback"))
    monkeypatch.setattr(discovery.kavita.KavitaClient, "recently_read_titles", titles)

    def scored(node, rating=10):
        return {"rating": rating, "mediaRecommendation": node}
    provider = AsyncMock(return_value=({
        1: [scored(media(30, "Shared pick", countryOfOrigin="KR")), scored(media(31, "Only from the start"))],
        2: [scored(media(30, "Shared pick", countryOfOrigin="KR")), scored(media(33, "Hidden one"))],
    }, False))
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)

    result = await discovery.recommendations(session)
    seeds = provider.await_args.args[0]
    assert 3 not in seeds and set(seeds) == {1, 2}  # a 1-star read is not a basis for picks
    shared, other = result.items
    assert (shared.title, shared.read_title, shared.read_detail, shared.read_rating, shared.country) == (
        "Shared pick", "Loved it", "finished", 5.0, "KR")
    assert (other.title, other.read_title, other.read_detail) == ("Only from the start", "Just started", "read 5%")
    assert "Hidden one" not in [i.title for i in result.items]


async def test_hide_list_unhide_and_releases_skip_hidden(session, monkeypatch):
    await discovery.hide_title(discovery.HiddenIn(provider_id="40", title="Not for me"), session)
    await discovery.hide_title(discovery.HiddenIn(provider_id="40", title="Not for me"), session)  # idempotent
    assert [(h.provider_id, h.title) for h in await discovery.hidden_titles(session)] == [("40", "Not for me")]
    today = datetime.now(timezone.utc).date()
    start = {"year": today.year, "month": today.month, "day": 1}
    provider = AsyncMock(return_value=([media(40, "Not for me", startDate=start),
                                        media(41, "Still shown", startDate=start)], False))
    monkeypatch.setattr(discovery.anilist, "new_releases", provider)
    assert [i.title for i in await discovery.releases(session)] == ["Still shown"]
    await discovery.unhide_title("40", session)
    assert await discovery.hidden_titles(session) == []
    assert {i.title for i in await discovery.releases(session)} == {"Not for me", "Still shown"}
    with pytest.raises(discovery.HTTPException):
        await discovery.unhide_title("abc", session)


def test_hidden_input_is_validated():
    with pytest.raises(ValueError):
        discovery.HiddenIn(provider_id="12a")
    with pytest.raises(ValueError):
        discovery.HiddenIn(provider_id="1", title="x" * 301)


async def test_reading_profile_asks_for_last_read_series_and_keeps_only_read_ones(monkeypatch):
    calls = []

    class Answer:
        def __init__(self, rows): self.rows = rows
        def json(self): return self.rows

    async def request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Answer([{"name": "Read", "pages": 10, "pagesRead": 4, "aniListId": 9},
                       {"name": "Unread", "pages": 10, "pagesRead": 0}, "junk"])
    monkeypatch.setattr(kavita.KavitaClient, "_request", request)
    client = kavita.KavitaClient("https://kavita.example", "fixture-key")
    try:
        reads = await client.reading_profile()
    finally:
        await client.close()
    assert [(r.name, r.anilist_id, r.pages_read) for r in reads] == [("Read", 9, 4)]
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/api/Series/all-v2")
    assert kwargs["json"]["sortOptions"] == {"sortField": kavita.SORT_BY_LAST_READ, "isAscending": False}


async def test_one_read_cannot_fill_the_top_of_the_list(session, monkeypatch):
    await kavita_settings(session)
    reads = [entry("Big series", anilist_id=1, pages_read=100), entry("Other read", anilist_id=2, pages_read=50)]
    monkeypatch.setattr(discovery.kavita.KavitaClient, "reading_profile", AsyncMock(return_value=reads))
    provider = AsyncMock(return_value=({
        1: [{"rating": 100 - i, "mediaRecommendation": media(100 + i, f"Big {i}")} for i in range(8)],
        2: [{"rating": 5, "mediaRecommendation": media(200, "Other pick")}],
    }, False))
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)
    titles = [i.title for i in (await discovery.recommendations(session)).items]
    assert titles[:6] == ["Big 0", "Big 1", "Big 2", "Big 3", "Big 4", "Other pick"]
    assert titles[6:] == ["Big 5", "Big 6", "Big 7"]
