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
          media(3, "Mature", startDate=full_date, isAdult=True)], True),
        ([media(4, "New Series", startDate=full_date),
          media(4, "New Series", startDate=full_date)], False),
    ])
    monkeypatch.setattr(discovery.anilist, "new_releases", provider)

    result = await discovery.releases(session)
    assert [(item.provider_id, item.release_date) for item in result] == [("4", today)]
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

    def scored(node, rating=10):
        return {"rating": rating, "mediaRecommendation": node}

    provider = AsyncMock(return_value={
        10: [scored(media(20, "Elsewhere", synonyms=["Already owned"])),
             scored(media(11, "Second read")), scored(media(30, "Worth Trying"))],
        11: [scored(media(30, "Worth Trying"), rating=50),
             scored(media(41, "Prose", format="NOVEL")),
             scored(media(42, "Animation", type="ANIME")),
             scored(media(43, "Mature", isAdult=True))],
    })
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)

    result = await discovery.recommendations(session)
    provider.assert_awaited_once_with([10, 11])
    assert result.state == "ready"
    assert [(item.title, item.read_title) for item in result.items] == [
        ("Worth Trying", "Second read"),
    ]


async def test_recommendations_without_kavita_do_not_call_provider(session, monkeypatch):
    provider = AsyncMock()
    monkeypatch.setattr(discovery.anilist, "discovery_recommendations", provider)
    result = await discovery.recommendations(session)
    assert result.state == "needs_kavita" and not result.items
    provider.assert_not_awaited()
