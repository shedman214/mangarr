import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import permission_jobs as jobs, permissions
from mangarr.models import Base, Chapter, RootFolder, Series


@pytest.fixture
async def environment(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def scope():
        async with maker() as session:
            yield session
    monkeypatch.setattr(jobs, "session_scope", scope)
    monkeypatch.setattr(jobs.config, "data_dir", tmp_path)
    monkeypatch.setattr(jobs, "_active", None)
    monkeypatch.setattr(jobs, "_start_lock", asyncio.Lock())
    if not hasattr(jobs.os, "fchown"):
        monkeypatch.setattr(jobs.os, "fchown", lambda *args: None, raising=False)
    async with maker() as session:
        s = Series(title="Sample", root_folder=RootFolder(path=str(tmp_path)))
        s.chapters = [Chapter(number=1, downloaded=True, file_path=str(tmp_path / "1.cbz"), file_source="sample"),
                      Chapter(number=2, downloaded=True, file_source="", file_path=str(tmp_path / "adopted.cbz"))]
        session.add(s)
        await session.commit()
    yield tmp_path
    if jobs._tasks:
        await asyncio.gather(*jobs._tasks)
    await engine.dispose()


async def complete():
    for _ in range(200):
        state = jobs.get_job()
        if state["state"] != "running":
            return state
        await asyncio.sleep(.01)
    raise AssertionError("job did not finish")


async def test_start_returns_before_slow_preview_and_progress_survives_reload(environment, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def preview(candidates, data_dir, **kwargs):
        assert list(candidates) == [(str(environment / "1.cbz"), str(environment))]
        kwargs["progress"]({"processed": 1, "changed": 1, "skipped": 0})
        entered.set()
        release.wait(5)
        return {"count": 1, "preview_id": "a" * 32, "examples": [], "skipped": 0}
    monkeypatch.setattr(permissions, "preview", preview)
    started = await jobs.start("preview")
    try:
        assert started["state"] == "running"
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.01)
        assert entered.is_set(), jobs.get_job()
        assert jobs.get_job(started["id"])["processed"] == 1
        with pytest.raises(ValueError, match="already running"):
            await jobs.start("preview")
    finally:
        release.set()
    state = await complete()
    assert state["state"] == "complete" and state["result"]["count"] == 1
    monkeypatch.setattr(jobs, "_active", None)
    assert jobs.get_job()["result"]["count"] == 1


async def test_job_failure_is_reported_and_partial_undo_survives_restart(environment, monkeypatch):
    def repair(*args, **kwargs):
        kwargs["progress"]({"undo_id": "b" * 32, "processed": 1, "changed": 1})
        raise OSError("Sample filesystem unavailable")
    monkeypatch.setattr(permissions, "repair", repair)
    await jobs.start("fix", "a" * 32)
    state = await complete()
    assert state["state"] == "failed" and state["undo_id"] == "b" * 32
    path = jobs._folder() / f"job-{state['id']}.json"
    state["state"] = "running"
    path.write_text(json.dumps(state))
    monkeypatch.setattr(jobs, "_active", None)
    recovered = jobs.get_job()
    assert recovered["state"] == "interrupted" and recovered["undo_id"] == "b" * 32


async def test_job_ids_cannot_escape_log_directory(environment):
    with pytest.raises(ValueError):
        jobs.get_job("../../private")

