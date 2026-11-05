import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..download.qbittorrent import QbtClient
from ..jobs.tasks import (
    REMOVED_BY_USER,
    enqueue_direct,
    enqueue_torrent,
    failed_grab_blocks,
    magnet_btih_hex,
    submit_torrent,
)
from ..models import (
    Chapter,
    Download,
    DownloadKind,
    DownloadStatus,
    HistoryEvent,
    RootFolder,
    Series,
)
from ..schemas import GrabIn, HistoryOut, QueueItemOut, QueueRemoveIn, QueueRemoveOut, QueueMoveIn, QueueMoveOut
from ..sources import registry

log = logging.getLogger(__name__)

router = APIRouter(tags=["queue"])

ACTIVE = [DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.IMPORTING]

# failed downloads stay visible in the queue this long — otherwise a failure
# silently disappears from Activity and looks like the grab never happened
FAILED_VISIBLE_FOR = timedelta(hours=48)


@router.get("/queue", response_model=list[QueueItemOut])
async def get_queue(session: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    failed_cutoff = now - FAILED_VISIBLE_FOR
    result = await session.execute(
        select(Download, Series.title, RootFolder.id, RootFolder.path)
        .outerjoin(Series, Download.series_id == Series.id)
        .outerjoin(RootFolder, Series.root_folder_id == RootFolder.id)
        .where(or_(
            Download.status.in_(ACTIVE),
            # user-removed items also carry FAILED, but hiding them is the
            # entire point of removal — only real failures stay visible
            and_(
                Download.status == DownloadStatus.FAILED,
                Download.error != REMOVED_BY_USER,
                Download.updated_at >= failed_cutoff,
            ),
            # ...and for as long as it keeps the monitor from retrying that
            # chapter on that source, however long that is
            failed_grab_blocks(now),
        ))
        .order_by(
            case((Download.status.in_([DownloadStatus.DOWNLOADING, DownloadStatus.IMPORTING]), 0),
                 (Download.status == DownloadStatus.QUEUED, 1), else_=2),
            Download.queue_priority.desc(), Download.id,
        )
    )
    items = []
    for dl, series_title, root_id, root_path in result.all():
        out = QueueItemOut.model_validate(dl)
        out.series_title = series_title or ""
        out.root_folder_id = root_id
        out.root_folder_path = root_path or ""
        items.append(out)
    return items


async def _remove_downloads(session: AsyncSession, ids: list[int]) -> int:
    """Mark downloads as removed and stop their torrents in qBittorrent.

    Direct downloads that are merely queued never start (the queue worker only
    picks up QUEUED items); active torrents are also deleted from qBittorrent
    along with their partial data so 'remove' really stops the transfer.

    Failed torrents are left alone in qBittorrent. A torrent only fails once it
    has finished (an import problem) or has already vanished from the client,
    and the import errors tell the user to import its files manually —
    deleting them on dismiss would destroy exactly what that message points at.

    The REMOVED_BY_USER error text is significant: the monitor's retry logic
    ignores it, so cancelling a queued grab doesn't blacklist that source for
    the chapter the way a real download failure does. Dismissing an
    already-failed row works the same way — the failure is cleared from the
    queue view and the chapter becomes eligible for retry again."""
    result = await session.execute(
        select(Download).where(
            Download.id.in_(ids),
            Download.status.in_([*ACTIVE, DownloadStatus.FAILED]),
        )
    )
    downloads = result.scalars().all()
    hashes = [
        dl.torrent_hash for dl in downloads
        if dl.kind == DownloadKind.TORRENT and dl.torrent_hash
        and dl.status in ACTIVE
    ]
    # Do not hide work until the download client confirms cancellation.
    if hashes:
        values = await registry.apply_settings(session)
        if values["qbittorrent_enabled"] != "true":
            raise HTTPException(409, "Enable qBittorrent to stop these torrents before removing them")
        client = QbtClient(
            values["qbittorrent_url"], values["qbittorrent_username"],
            values["qbittorrent_password"],
        )
        try:
            await client.delete_torrents(hashes)
        except Exception as exc:
            raise HTTPException(502, "Could not stop torrents in qBittorrent; items remain visible. Retry removal.") from exc
        finally:
            await client.close()
    for dl in downloads:
        dl.status = DownloadStatus.FAILED
        dl.error = REMOVED_BY_USER
    await session.commit()
    return len(downloads)


@router.delete("/queue/{download_id}", status_code=204)
async def remove_from_queue(download_id: int, session: AsyncSession = Depends(get_session)):
    removed = await _remove_downloads(session, [download_id])
    if removed == 0:
        raise HTTPException(404, "Download not found")


@router.post("/queue/remove", response_model=QueueRemoveOut)
async def remove_many_from_queue(
    body: QueueRemoveIn, session: AsyncSession = Depends(get_session)
):
    """Bulk removal for the queue's multi-select — one call stops and removes
    every selected download."""
    if not body.ids:
        raise HTTPException(422, "No download ids given")
    return QueueRemoveOut(removed=await _remove_downloads(session, body.ids))


@router.post("/queue/move", response_model=QueueMoveOut)
async def move_queued_downloads(body: QueueMoveIn, session: AsyncSession = Depends(get_session)):
    """Move queued direct chapters only. Never retry or preempt active work.

    SQLite's write lock serializes simultaneous edits before reading the order.
    New automatic grabs keep priority zero and join behind existing priorities.
    """
    if body.to_end and body.before_id is not None:
        raise HTTPException(422, "Choose either a drop target or the end of the queue.")
    await session.execute(text("BEGIN IMMEDIATE"))
    try:
        rows = (await session.execute(
            select(Download.id, Download.queue_priority)
            .where(Download.kind == DownloadKind.DIRECT, Download.status == DownloadStatus.QUEUED)
            .order_by(Download.queue_priority.desc(), Download.id)
        )).all()
        requested = set(body.ids)
        moving = [row.id for row in rows if row.id in requested]
        if body.before_id is not None and body.before_id not in {row.id for row in rows}:
            raise HTTPException(409, "That chapter is no longer queued. Refresh the queue and try again.")
        if body.before_id in moving:
            raise HTTPException(422, "Choose a different chapter as the drop target.")
        if not moving:
            raise HTTPException(409, "None of the selected chapters are still queued. Active, failed and torrent downloads cannot be moved.")
        if body.before_id is None and not body.to_end:
            highest = max((row.queue_priority for row in rows), default=0)
            priorities = {id: highest + len(moving) - index for index, id in enumerate(moving)}
        else:
            remaining = [row.id for row in rows if row.id not in requested]
            index = len(remaining) if body.to_end else remaining.index(body.before_id)
            ordered = remaining[:index] + moving + remaining[index:]
            old = dict(rows)
            priorities = {id: len(ordered) - index for index, id in enumerate(ordered)
                          if old[id] != len(ordered) - index}
        # Keep SQL statements bounded even when a user moves a whole series.
        pairs = list(priorities.items())
        for start in range(0, len(pairs), 400):
            chunk = dict(pairs[start:start + 400])
            await session.execute(update(Download).where(
                Download.id.in_(chunk), Download.kind == DownloadKind.DIRECT,
                Download.status == DownloadStatus.QUEUED,
            ).values(queue_priority=case(chunk, value=Download.id)),
                execution_options={"synchronize_session": False})
        await session.commit()
        return QueueMoveOut(moved=len(moving), skipped=len(requested) - len(moving))
    except Exception:
        await session.rollback()
        raise


@router.post("/queue/remove-failed", response_model=QueueRemoveOut)
async def remove_failed_from_queue(
    body: QueueRemoveIn, session: AsyncSession = Depends(get_session)
):
    """Dismiss the displayed failures, leaving active jobs and all files alone.

    Check status in the update itself: a row retried since the UI loaded must
    not be cancelled. Failed torrents are not deleted from qBittorrent.
    """
    if not body.ids:
        raise HTTPException(422, "No download ids given")
    result = await session.execute(
        update(Download).where(
            Download.id.in_(body.ids),
            Download.status == DownloadStatus.FAILED,
            Download.error != REMOVED_BY_USER,
        ).values(error=REMOVED_BY_USER).returning(Download.id)
    )
    removed = len(result.scalars().all())
    await session.commit()
    return QueueRemoveOut(removed=removed)


@router.post("/queue/{download_id}/retry", response_model=QueueItemOut)
async def retry_failed_download(
    download_id: int, session: AsyncSession = Depends(get_session)
):
    """Retry a failed Activity item using its original source payload."""
    dl = await session.get(Download, download_id)
    if dl is None:
        raise HTTPException(404, "Download not found")
    if dl.status != DownloadStatus.FAILED:
        raise HTTPException(409, "Only failed downloads can be retried")
    if dl.chapter_id is not None:
        chapter = await session.get(Chapter, dl.chapter_id)
        if chapter is None or chapter.excluded:
            raise HTTPException(409, "Excluded chapters cannot be retried")

    if dl.kind == DownloadKind.TORRENT:
        values = await registry.apply_settings(session)
        if values["qbittorrent_enabled"] != "true":
            raise HTTPException(400, "qBittorrent is not enabled in settings")
        try:
            dl.torrent_hash = await submit_torrent(dl.payload, values)
        except Exception as exc:
            raise HTTPException(502, f"qBittorrent error: {exc}") from exc
        dl.status = DownloadStatus.DOWNLOADING
    else:
        dl.status = DownloadStatus.QUEUED

    dl.progress = 0.0
    dl.error = ""
    dl.transient_error = False
    session.add(HistoryEvent(
        series_id=dl.series_id,
        chapter_id=dl.chapter_id,
        event="retried",
        source_name=dl.source_name,
        detail=dl.title,
    ))
    await session.commit()

    series = await session.get(Series, dl.series_id) if dl.series_id else None
    out = QueueItemOut.model_validate(dl)
    out.series_title = series.title if series else ""
    return out


@router.post("/queue/grab", response_model=QueueItemOut, status_code=201)
async def grab(body: GrabIn, session: AsyncSession = Depends(get_session)):
    values = await registry.apply_settings(session)

    if body.chapter_id is not None and body.source_name and body.external_id:
        chapter = await session.get(Chapter, body.chapter_id)
        if chapter is None or chapter.excluded:
            raise HTTPException(404, "Chapter not found")
        series = await session.get(Series, chapter.series_id)
        dl = await enqueue_direct(
            session, series, chapter, body.source_name, body.external_id, group=body.group
        )
    elif body.magnet:
        if values["qbittorrent_enabled"] != "true":
            raise HTTPException(400, "qBittorrent is not enabled in settings")
        if not magnet_btih_hex(body.magnet):
            raise HTTPException(422, "Magnet link must include a valid btih info hash")
        series = await session.get(Series, body.series_id) if body.series_id else None
        try:
            dl = await enqueue_torrent(
                session, series, body.magnet, body.title or "manual torrent", values
            )
        except Exception as exc:
            raise HTTPException(502, f"qBittorrent error: {exc}") from exc
    else:
        raise HTTPException(422, "Provide chapter_id+source_name+external_id or magnet")

    out = QueueItemOut.model_validate(dl)
    out.series_title = series.title if series else ""
    return out


@router.get("/history", response_model=list[HistoryOut])
async def get_history(limit: int = 100, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(HistoryEvent, Series.title)
        .outerjoin(Series, HistoryEvent.series_id == Series.id)
        .order_by(HistoryEvent.id.desc())
        .limit(limit)
    )
    items = []
    for ev, series_title in result.all():
        out = HistoryOut.model_validate(ev)
        out.series_title = series_title or ""
        items.append(out)
    return items
