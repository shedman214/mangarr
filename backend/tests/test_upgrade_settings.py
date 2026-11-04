"""Settings → Sources → Upgrades: Sonarr-style "Upgrades allowed" / "Upgrade until"
defaults for new series, and applying them to every existing series."""
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import adding, settings_service
from mangarr.api import settings as routes
from mangarr.db import get_session
from mangarr.metadata.base import SeriesMetadata
from mangarr.models import Base, Series


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_defaults_and_validation():
    assert settings_service.DEFAULTS["upgrades_allowed"] == "false"  # nothing changes until turned on
    assert settings_service.DEFAULTS["upgrade_until"] == ""
    base = dict(settings_service.DEFAULTS)
    settings_service.validate({**base, "upgrades_allowed": "true", "upgrade_until": "mangadex"})
    with pytest.raises(ValueError):
        settings_service.validate({**base, "upgrades_allowed": "yes"})
    with pytest.raises(ValueError):
        settings_service.validate({**base, "upgrade_until": "not-a-source"})


@pytest.mark.parametrize("allowed,until", [("true", "mangafire"), ("false", "")])
async def test_new_series_take_the_saved_defaults(maker, monkeypatch, allowed, until):
    async def get_series(provider_id):
        return SeriesMetadata(provider="mangaupdates", provider_id=provider_id, title=f"Series {provider_id}")
    monkeypatch.setattr(adding.mangaupdates, "get_series", get_series)
    async with maker() as session:
        await settings_service.set_many(session, {"upgrades_allowed": allowed, "upgrade_until": until})
        series = await adding.create_series(session, adding.AddOptions(root_folder_id=None), mangaupdates_id=7)
    assert series.upgrades_enabled is (allowed == "true")
    assert series.upgrade_cutoff == until


async def test_summary_and_apply_to_all(maker):
    async with maker() as session:
        session.add_all([
            Series(title="A", sort_title="a", upgrades_enabled=False, upgrade_cutoff=""),
            Series(title="B", sort_title="b", upgrades_enabled=True, upgrade_cutoff="mangadex"),
            Series(title="C", sort_title="c", upgrades_enabled=True, upgrade_cutoff="weebcentral"),
        ])
        await session.commit()

    async def session_dep():
        async with maker() as s:
            yield s
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = session_dep
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # defaults: upgrades off — only A matches
        assert (await c.get("/settings/upgrades")).json() == {"series": 3, "upgrades_on": 2, "matching": 1}
        async with maker() as session:
            await settings_service.set_many(session, {"upgrades_allowed": "true", "upgrade_until": "weebcentral"})
        assert (await c.get("/settings/upgrades")).json()["matching"] == 1  # only C
        applied = (await c.post("/settings/upgrades/apply")).json()
        assert applied == {"changed": 2, "series": 3, "upgrades_on": 3, "matching": 3}
        assert (await c.post("/settings/upgrades/apply")).json()["changed"] == 0  # idempotent
        # turning it off everywhere keeps each series' cutoff for later
        async with maker() as session:
            await settings_service.set_many(session, {"upgrades_allowed": "false"})
        off = (await c.post("/settings/upgrades/apply")).json()
        assert off == {"changed": 3, "series": 3, "upgrades_on": 0, "matching": 3}
    async with maker() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(Series.upgrades_enabled, Series.upgrade_cutoff))).all()
    assert rows == [(False, "weebcentral")] * 3
