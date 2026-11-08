import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Chapter, Download, DownloadStatus, Series
from ..release_schedule import cadence_label, release_schedule
from ..schemas import (
    CalendarExpectedOut,
    CalendarOut,
    CalendarReleaseOut,
    CalendarSearchIn,
    CalendarSearchOut,
)

router = APIRouter(tags=["calendar"])

MAX_RELEASED = 500
MAX_SEARCH = 1000  # chapters per Search request

# the loop only holds weak references to tasks; keep searches alive until done
_background_tasks: set[asyncio.Task] = set()


@router.post("/calendar/search", response_model=CalendarSearchOut, status_code=202)
async def calendar_search(body: CalendarSearchIn, session: AsyncSession = Depends(get_session)):
    """Search linked sources for these missing chapters in the background
    (Calendar's Search all missing and per-row Search). Chapters that are
    downloaded, excluded, already queued or unknown are ignored."""
    from ..jobs.tasks import search_chapters

    ids = list(dict.fromkeys(body.chapter_ids))
    if not ids:
        raise HTTPException(422, "No chapters given")
    if len(ids) > MAX_SEARCH:
        raise HTTPException(422, f"At most {MAX_SEARCH} chapters per search")
    busy = select(Download.chapter_id).where(
        Download.chapter_id.isnot(None),
        Download.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.IMPORTING]),
    )
    rows = (await session.execute(
        select(Chapter.id, Chapter.series_id).where(
            Chapter.id.in_(ids),
            Chapter.downloaded == False,  # noqa: E712
            Chapter.excluded == False,  # noqa: E712
            Chapter.id.notin_(busy),  # already queued or downloading
        )
    )).all()
    if rows:
        task = asyncio.get_running_loop().create_task(search_chapters([r[0] for r in rows]))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return CalendarSearchOut(chapters=len(rows), series=len({r[1] for r in rows}))


@router.get("/calendar", response_model=CalendarOut)
async def calendar(
    days_back: int = Query(14, ge=0, le=90),
    days_ahead: int = Query(14, ge=0, le=90),
    session: AsyncSession = Depends(get_session),
):
    """Chapters released in the last `days_back` days and the next release
    each ongoing series is expected to have within `days_ahead`, going by
    its usual release rhythm."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days_back)
    rows = await session.execute(
        select(Chapter, Series.title, Series.cover_url, Series.monitored)
        .join(Series, Chapter.series_id == Series.id)
        .where(
            Chapter.excluded == False,  # noqa: E712
            Chapter.released_at.isnot(None),
            Chapter.released_at >= since,
        )
        .order_by(Chapter.released_at.desc(), Series.title, Chapter.number)
        .limit(MAX_RELEASED)
    )
    chapter_rows = rows.all()
    # chapters already in the queue show that instead of "Missing"
    active = {
        chapter_id: (status, progress)
        for chapter_id, status, progress in (await session.execute(
            select(Download.chapter_id, Download.status, Download.progress).where(
                Download.chapter_id.in_([ch.id for ch, *_ in chapter_rows]),
                Download.status.in_([
                    DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.IMPORTING,
                ]),
            )
        )).all()
    } if chapter_rows else {}
    released = [
        CalendarReleaseOut(
            series_id=ch.series_id,
            series_title=title,
            cover_url=cover,
            chapter_id=ch.id,
            number=ch.number,
            volume=ch.volume,
            title=ch.title,
            released_at=ch.released_at,
            downloaded=ch.downloaded,
            monitored=series_monitored and ch.monitored,
            download_status="" if ch.downloaded or ch.id not in active
            else getattr(active[ch.id][0], "value", active[ch.id][0]),
            download_progress=0.0 if ch.id not in active else float(active[ch.id][1] or 0),
        )
        for ch, title, cover, series_monitored in chapter_rows
    ]

    # only what the cadence needs, not whole chapter rows for the library
    dated: dict[int, list[Chapter]] = defaultdict(list)
    for series_id, number, released_at in (
        await session.execute(
            select(Chapter.series_id, Chapter.number, Chapter.released_at).where(
                Chapter.excluded == False,  # noqa: E712
                Chapter.released_at.isnot(None),
            )
        )
    ).all():
        dated[series_id].append(Chapter(number=number, released_at=released_at))
    horizon = now + timedelta(days=days_ahead)
    expected: list[CalendarExpectedOut] = []
    for series in (await session.execute(select(Series))).scalars().all():
        schedule = release_schedule(dated.get(series.id, []), series.status, now)
        if schedule.next_expected_at is None or schedule.next_expected_at > horizon:
            continue
        expected.append(CalendarExpectedOut(
            series_id=series.id,
            series_title=series.title,
            cover_url=series.cover_url,
            expected_at=schedule.next_expected_at,
            cadence_days=schedule.cadence_days,
            cadence_label=cadence_label(schedule.cadence_days),
            last_released_at=schedule.last_released_at,
            last_number=schedule.last_number,
            overdue=schedule.next_expected_at.date() < now.date(),
            monitored=series.monitored,
        ))
    expected.sort(key=lambda e: (e.expected_at, e.series_title))
    return CalendarOut(released=released, expected=expected)
