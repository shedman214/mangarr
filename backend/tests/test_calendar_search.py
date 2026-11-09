"""Calendar → Search all missing / per-row Search: only the asked-for missing
chapters are searched, and chapters already in the queue show that status."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import calendar as calendar_api
from mangarr.jobs import tasks
from mangarr.models import Base, Chapter, Download, DownloadKind, DownloadStatus, RootFolder, Series
from mangarr.schemas import CalendarSearchIn


@pytest.fixture
async def session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    class Scope:
        async def __aenter__(self):
            self.s = maker()
            return await self.s.__aenter__()

        async def __aexit__(self, *exc):
            return await self.s.__aexit__(*exc)
    monkeypatch.setattr(tasks, "session_scope", lambda: Scope())
    async with maker() as s:
        yield s
    await engine.dispose()


async def seed(session, tmp_path):
    now = datetime.now(timezone.utc)
    series = Series(title="Example", sort_title="example", root_folder=RootFolder(path=str(tmp_path)),
                    folder_name="Example", monitored=True)
    series.chapters = [
        Chapter(number=1.0, monitored=True, downloaded=True, released_at=now - timedelta(days=3)),
        Chapter(number=2.0, monitored=True, released_at=now - timedelta(days=2)),
        Chapter(number=3.0, monitored=True, released_at=now - timedelta(days=1)),
        Chapter(number=4.0, monitored=True, released_at=now),
    ]
    session.add(series)
    await session.commit()
    c = {ch.number: ch for ch in series.chapters}
    session.add(Download(series_id=series.id, chapter_id=c[4.0].id, kind=DownloadKind.DIRECT,
                         status=DownloadStatus.DOWNLOADING, progress=0.4, source_name="fake",
                         payload="x", title="Example ch 4"))
    await session.commit()
    return series, c


async def test_calendar_rows_show_queue_status(session, tmp_path):
    series, c = await seed(session, tmp_path)
    out = await calendar_api.calendar(days_back=14, days_ahead=14, session=session)
    rows = {r.number: r for r in out.released}
    assert rows[4.0].download_status == "downloading" and rows[4.0].download_progress == pytest.approx(0.4)
    assert rows[2.0].download_status == "" and rows[1.0].download_status == ""


async def test_search_endpoint_ignores_downloaded_and_queued(session, tmp_path, monkeypatch):
    series, c = await seed(session, tmp_path)
    started = []

    async def fake_search(ids):
        started.append(sorted(ids))
        return 0
    monkeypatch.setattr(tasks, "search_chapters", fake_search)
    out = await calendar_api.calendar_search(
        CalendarSearchIn(chapter_ids=[c[1.0].id, c[2.0].id, c[3.0].id, 999999]), session)
    # chapter 1 is downloaded, chapter 4 is already downloading, 999999 does not exist
    assert (out.chapters, out.series) == (2, 1)
    for task in list(calendar_api._background_tasks):
        await task
    assert started == [sorted([c[2.0].id, c[3.0].id])]
    with pytest.raises(calendar_api.HTTPException):
        await calendar_api.calendar_search(CalendarSearchIn(chapter_ids=[]), session)


async def test_search_targets_only_the_asked_chapters(session, tmp_path, monkeypatch):
    series, c = await seed(session, tmp_path)
    calls = []

    async def fake_grab(session_, series_, values, only_monitored=True, exclude_numbers=None, **kw):
        calls.append((series_.id, only_monitored, sorted(exclude_numbers or [])))
        return 1

    async def fake_settings(_session):
        return {}
    monkeypatch.setattr(tasks, "grab_missing_chapters", fake_grab)
    monkeypatch.setattr(tasks.registry, "apply_settings", fake_settings)
    queued = await tasks.search_chapters([c[2.0].id, c[1.0].id])  # chapter 1 is downloaded: ignored
    assert queued == 1
    assert calls == [(series.id, False, [1.0, 3.0, 4.0])]  # every other chapter excluded
