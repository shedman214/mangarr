"""Queue destinations follow the series assignment without losing orphaned jobs."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api.queue import get_queue
from mangarr.models import Base, Download, DownloadKind, DownloadStatus, RootFolder, Series


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


async def test_queue_reports_current_library_root_for_direct_and_torrent_jobs(session):
    manga, other = RootFolder(path="/manga"), RootFolder(path="/dojuin")
    session.add_all([manga, other])
    await session.flush()
    series = Series(title="Example", sort_title="example", root_folder_id=manga.id)
    session.add(series)
    await session.flush()
    direct = Download(series_id=series.id, kind=DownloadKind.DIRECT, status=DownloadStatus.QUEUED)
    torrent = Download(series_id=series.id, kind=DownloadKind.TORRENT, status=DownloadStatus.DOWNLOADING)
    session.add_all([direct, torrent])
    await session.commit()
    rows = await get_queue(session)
    assert [row.id for row in rows] == [torrent.id, direct.id]
    assert {(row.root_folder_id, row.root_folder_path) for row in rows} == {(manga.id, "/manga")}
    # A library move changes the planned destination; stale job metadata must not win.
    series.root_folder_id = other.id
    await session.commit()
    rows = await get_queue(session)
    assert {(row.root_folder_id, row.root_folder_path) for row in rows} == {(other.id, "/dojuin")}
    assert direct.status == DownloadStatus.QUEUED
    assert torrent.status == DownloadStatus.DOWNLOADING


async def test_queue_keeps_jobs_with_no_series_or_no_assigned_root(session):
    series = Series(title="Unassigned", sort_title="unassigned")
    session.add(series)
    await session.flush()
    session.add_all([
        Download(series_id=series.id, kind=DownloadKind.DIRECT, status=DownloadStatus.QUEUED),
        Download(series_id=None, kind=DownloadKind.TORRENT, status=DownloadStatus.QUEUED),
    ])
    await session.commit()
    rows = await get_queue(session)
    assert len(rows) == 2
    assert all(row.root_folder_id is None and row.root_folder_path == "" for row in rows)
    assert [row.series_title for row in rows] == ["Unassigned", ""]
