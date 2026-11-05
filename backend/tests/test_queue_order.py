"""Queue edits persist and affect the worker without touching active downloads."""
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import queue
from mangarr.models import Base, Download, DownloadKind, DownloadStatus
from mangarr.schemas import QueueMoveIn
from mangarr.jobs import tasks


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session, maker
    await engine.dispose()


async def add(session, **kwargs):
    dl = Download(kind=DownloadKind.DIRECT, status=DownloadStatus.QUEUED, **kwargs)
    session.add(dl)
    await session.commit()
    return dl.id


async def ids(session):
    return [row.id for row in await queue.get_queue(session) if row.status == "queued" and row.kind == "direct"]


async def test_download_now_preserves_selection_order_and_survives_new_grabs(db):
    session, maker = db
    a, b, c, d = [await add(session) for _ in range(4)]
    result = await queue.move_queued_downloads(QueueMoveIn(ids=[d, b, b]), session)
    assert (result.moved, result.skipped) == (2, 0)
    # Keep existing chapter order, not the order users ticked checkboxes.
    assert await ids(session) == [b, d, a, c]
    new = await add(session)
    async with maker() as reloaded:
        assert await ids(reloaded) == [b, d, a, c, new]


async def test_drag_before_and_move_to_bottom(db):
    session, _ = db
    a, b, c, d = [await add(session) for _ in range(4)]
    await queue.move_queued_downloads(QueueMoveIn(ids=[d], before_id=b), session)
    assert await ids(session) == [a, d, b, c]
    await session.commit()
    await queue.move_queued_downloads(QueueMoveIn(ids=[a, b], to_end=True), session)
    assert await ids(session) == [d, c, a, b]
    new = await add(session)
    assert await ids(session) == [d, c, a, b, new]


async def test_move_does_not_retry_failures_or_preempt_active_or_torrents(db):
    session, _ = db
    queued = await add(session)
    others = [Download(kind=DownloadKind.DIRECT, status=status, error="keep")
              for status in [DownloadStatus.DOWNLOADING, DownloadStatus.IMPORTING,
                             DownloadStatus.FAILED, DownloadStatus.DONE]]
    torrent = Download(kind=DownloadKind.TORRENT, status=DownloadStatus.QUEUED)
    session.add_all(others + [torrent]); await session.commit()
    result = await queue.move_queued_downloads(QueueMoveIn(ids=[queued] + [d.id for d in others] + [torrent.id, 99999]), session)
    assert (result.moved, result.skipped) == (1, 6)
    for dl in others:
        await session.refresh(dl)
        assert dl.queue_priority == 0 and dl.error == "keep"
    await session.refresh(torrent)
    assert torrent.queue_priority == 0


@pytest.mark.parametrize("mode", ["missing", "self", "conflicting", "no_queued"])
async def test_stale_or_invalid_moves_leave_order_unchanged(db, mode):
    session, _ = db
    a, b = [await add(session) for _ in range(2)]
    payload = {"missing": dict(ids=[a], before_id=999), "self": dict(ids=[a], before_id=a),
               "conflicting": dict(ids=[a], before_id=b, to_end=True),
               "no_queued": dict(ids=[999])}[mode]
    with pytest.raises(HTTPException):
        await queue.move_queued_downloads(QueueMoveIn(**payload), session)
    assert await ids(session) == [a, b]


async def test_worker_runs_promoted_chapter_next(db, monkeypatch):
    session, maker = db
    a, b, c = [await add(session) for _ in range(3)]
    await queue.move_queued_downloads(QueueMoveIn(ids=[c]), session)
    @asynccontextmanager
    async def scope():
        async with maker() as s:
            yield s
    processed = []
    async def run(s, dl):
        processed.append(dl.id)
        dl.status = DownloadStatus.DONE
        await s.commit()
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_run_direct_download", run)
    await tasks.process_direct_queue()
    assert processed == [c, a, b]


async def test_worker_rechecks_order_after_each_chapter(db, monkeypatch):
    session, maker = db
    a, b, c = [await add(session) for _ in range(3)]
    @asynccontextmanager
    async def scope():
        async with maker() as s:
            yield s
    processed = []
    async def run(s, dl):
        processed.append(dl.id)
        dl.status = DownloadStatus.DONE
        await s.commit()
        if dl.id == a:
            async with maker() as edit:
                await queue.move_queued_downloads(QueueMoveIn(ids=[c]), edit)
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_run_direct_download", run)
    await tasks.process_direct_queue()
    assert processed == [a, c, b]


async def test_existing_database_gets_default_order_without_losing_downloads(tmp_path, monkeypatch):
    from mangarr import db as database, backups
    path = tmp_path / "mangarr.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        first, second = [await add(session) for _ in range(2)]
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE downloads DROP COLUMN queue_priority")
    assert "downloads.queue_priority" in backups.pending_schema_changes(path)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database.config, "data_dir", tmp_path)
    await database.init_db()
    await database.init_db()  # safe on subsequent restarts
    async with maker() as session:
        assert await ids(session) == [first, second]
        assert list((await session.execute(select(Download.queue_priority))).scalars()) == [0, 0]
    await engine.dispose()
