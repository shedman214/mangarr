"""Automatic backups and retention (Settings → Backup and restore)."""
import time

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import backup_schedule, backups
from mangarr.api import backups as routes
from mangarr.db import get_session
from mangarr.models import Base
from tests.test_backups import library

DAY = 86400
NOW = 2_000_000_000


def b(id_, reason, days_old):
    return {"id": id_, "reason": reason, "created": NOW - days_old * DAY, "bytes": 1}


def test_plan_keeps_newest_automatic_and_manual_by_default():
    saved = [b("m1", "manual", 1), b("s1", "scheduled", 3), b("p1", "pre-migration", 40),
             b("s2", "scheduled", 30), b("m2", "manual", 400), b("p2", "pre-migration", 90)]
    doomed = {x["id"] for x in backups.prune_plan(saved, retention_days=28, now=NOW)}
    # s2 and p2 are old automatic ones; p1 is old but the newest before-update backup; manual kept
    assert doomed == {"s2", "p2"}


def test_manual_retention_and_newest_overall_is_never_removed():
    saved = [b("m1", "manual", 100), b("m2", "manual", 200), b("s1", "scheduled", 300)]
    doomed = {x["id"] for x in backups.prune_plan(saved, retention_days=28, manual_retention_days=30, now=NOW)}
    assert doomed == {"m2"}  # m1 is the newest backup overall; s1 is the newest scheduled one
    assert backups.prune_plan(saved, 28, 0, now=NOW) == []


def test_validate_limits():
    ok = backup_schedule.validate({"enabled": True, "interval_days": "7", "retention_days": 28, "manual_retention_days": 0})
    assert ok == {"enabled": True, "interval_days": 7, "retention_days": 28, "manual_retention_days": 0}
    for bad in ({"interval_days": 0}, {"retention_days": 99999}, {"manual_retention_days": -1}, {"interval_days": "x"}):
        with pytest.raises(ValueError):
            backup_schedule.validate({**ok, **bad})


def test_run_once_makes_a_backup_only_when_due_and_prunes(tmp_path, monkeypatch):
    library(tmp_path).close()
    monkeypatch.setattr(backup_schedule.config, "data_dir", tmp_path)
    values = dict(backup_schedule.DEFAULTS)
    first = backup_schedule.run_once(values)
    assert first["created"]["reason"] == "scheduled"
    assert backup_schedule.run_once(values)["created"] is None  # not due again yet
    later = time.time() + 8 * DAY
    assert backup_schedule.run_once(values, now=later)["created"] is not None
    assert backup_schedule.run_once({**values, "enabled": False}, now=later + 30 * DAY)["created"] is None


@pytest.fixture
async def client(tmp_path, monkeypatch):
    library(tmp_path).close()
    monkeypatch.setattr(routes.config, "data_dir", tmp_path)
    monkeypatch.setattr(backup_schedule.config, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with maker() as s:
            yield s
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_schedule_api_saves_validates_and_applies_retention(client, tmp_path):
    state = (await client.get("/settings/backups/schedule")).json()
    assert state["enabled"] is True and state["interval_days"] == 7 and state["retention_days"] == 28
    assert state["manual_retention_days"] == 0 and state["folder"] == "/config/backups"
    bad = await client.put("/settings/backups/schedule", json={"enabled": True, "interval_days": 0, "retention_days": 28})
    assert bad.status_code == 422
    made = [(await client.post("/settings/backups")).json()["id"] for _ in range(2)]
    saved = await client.put("/settings/backups/schedule",
                             json={"enabled": False, "interval_days": 3, "retention_days": 10, "manual_retention_days": 0})
    assert saved.status_code == 200
    body = saved.json()
    assert body["enabled"] is False and body["interval_days"] == 3 and body["next_automatic"] is None
    assert body["removed"] == 0 and body["manual_kept"] == 2
    assert (await client.get("/settings/backups/schedule")).json()["retention_days"] == 10
    # deleting one backup, and the fixed /restore path still works
    assert (await client.delete(f"/settings/backups/{made[0]}")).status_code == 204
    assert (await client.delete(f"/settings/backups/{made[0]}")).status_code == 404
    assert (await client.delete("/settings/backups/not-a-token")).status_code == 404
    assert (await client.delete("/settings/backups/restore")).status_code == 204
    assert [x["id"] for x in (await client.get("/settings/backups")).json()] == [made[1]]
