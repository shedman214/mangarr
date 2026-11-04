import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import kavita, notifications, settings_service
from .. import permissions
from ..config import config
from ..models import Chapter, RootFolder, Series
from ..db import get_session
from ..download.qbittorrent import QbtError, test_connection
from ..schemas import (
    KavitaLibraryOut,
    KavitaTestIn,
    KavitaTestOut,
    QbtTestIn,
    WebhookTestIn,
)

router = APIRouter(prefix="/settings", tags=["settings"])

MASK = "••••••••"


@router.get("/sources/status")
async def sources_status():
    from ..source_status import snapshot
    return {"sources": snapshot(), "scope": "since_restart"}


class PermissionAction(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")


class PermissionJobIn(BaseModel):
    operation: str = Field(pattern=r"^(preview|fix|undo)$")
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.post("/permissions/jobs", status_code=202)
async def start_permission_job(body: PermissionJobIn):
    from .. import permission_jobs
    if body.operation != "preview" and body.id is None:
        raise HTTPException(422, "A preview or undo ID is required")
    try:
        return await permission_jobs.start(body.operation, body.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/permissions/jobs/latest")
async def latest_permission_job():
    from ..permission_jobs import get_job
    return await asyncio.to_thread(get_job)


@router.get("/permissions/jobs/{job_id}")
async def permission_job(job_id: str):
    from ..permission_jobs import get_job
    try:
        return await asyncio.to_thread(get_job, job_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/toonily/test")
async def toonily_test(session: AsyncSession = Depends(get_session)):
    from ..source_checks import check_toonily
    try:
        return await check_toonily(session)
    except Exception as exc:
        raise HTTPException(502, f"Toonily test failed: {exc}") from exc


@router.post("/mangafire/test")
async def mangafire_test(session: AsyncSession = Depends(get_session)):
    from ..source_checks import check_mangafire
    try:
        return await check_mangafire(session)
    except Exception as exc:
        from ..source_status import failure
        raise HTTPException(502, f"MangaFire test failed: {failure('mangafire', exc)}") from exc


@router.get("/permissions/status")
async def permission_status():
    return {"supported": hasattr(os, "fchown"), "uid": os.getuid() if hasattr(os, "getuid") else None,
            "gid": os.getgid() if hasattr(os, "getgid") else None}


@router.post("/permissions/preview")
async def permission_preview(session: AsyncSession = Depends(get_session)):
    await settings_service.get_all(session)
    candidates = (await session.execute(
        select(Chapter.file_path, RootFolder.path).join(Series, Chapter.series_id == Series.id)
        .join(RootFolder, Series.root_folder_id == RootFolder.id)
        .where(Chapter.downloaded == True, Chapter.excluded == False, Chapter.file_source != "", Chapter.file_path != "")
        .distinct()
    )).all()
    try:
        return await asyncio.to_thread(permissions.preview, [(p, r) for p, r in candidates], config.data_dir)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/permissions/fix")
async def permission_fix(body: PermissionAction, session: AsyncSession = Depends(get_session)):
    await settings_service.get_all(session)
    try:
        return await asyncio.to_thread(permissions.repair, config.data_dir, body.id)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/permissions/undo")
async def permission_undo(body: PermissionAction):
    try:
        return await asyncio.to_thread(permissions.repair, config.data_dir, body.id, undo=True)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    values = await settings_service.get_all(session)
    for key in settings_service.SECRET_KEYS:
        if values.get(key):
            values[key] = MASK
    return values


@router.put("")
async def update_settings(
    body: dict[str, str], session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    # ignore masked secrets that the user did not change
    to_save = {k: v for k, v in body.items() if v != MASK}
    try:
        current = await settings_service.get_all(session)
        settings_service.validate({**current, **to_save})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await settings_service.set_many(session, to_save)
    # apply a changed monitor interval immediately (no restart needed)
    if "monitor_interval_minutes" in to_save:
        from ..jobs.scheduler import reschedule_monitor

        reschedule_monitor(int(to_save["monitor_interval_minutes"]))
    if "import_list_sync_hours" in to_save:
        from ..jobs.scheduler import reschedule_import_lists

        reschedule_import_lists(int(to_save["import_list_sync_hours"]))
    return await get_settings(session)


@router.post("/webhook/test")
async def webhook_test(body: WebhookTestIn, session: AsyncSession = Depends(get_session)):
    secret = body.secret
    if secret == MASK:
        secret = await settings_service.get(session, "webhook_secret")
    ok = await notifications.send_webhook(
        body.url, secret, {"app": notifications.APP_NAME, "event": "test", "series_id": 0}
    )
    if not ok:
        raise HTTPException(400, "Webhook endpoint rejected the test event or is unreachable")
    return {"ok": True}


@router.post("/kavita/test", response_model=KavitaTestOut)
async def kavita_test(body: KavitaTestIn, session: AsyncSession = Depends(get_session)):
    """Verify the connection and return the libraries, so the settings page can
    offer real libraries to map root folders onto."""
    api_key = body.api_key
    if api_key == MASK or not api_key:
        api_key = await settings_service.get(session, "kavita_api_key")
    try:
        version, libraries = await kavita.test_connection(body.url, api_key)
    except kavita.KavitaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return KavitaTestOut(
        ok=True, version=version,
        libraries=[
            KavitaLibraryOut(id=lib.id, name=lib.name, folders=lib.folders)
            for lib in libraries
        ],
    )


@router.post("/qbittorrent/test")
async def qbt_test(body: QbtTestIn, session: AsyncSession = Depends(get_session)):
    password = body.password
    if password == MASK:
        password = await settings_service.get(session, "qbittorrent_password")
    try:
        version = await test_connection(body.url, body.username, password)
    except QbtError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "version": version}


@router.get("/upgrades")
async def upgrade_summary(session: AsyncSession = Depends(get_session)):
    """How many series follow the saved Upgrades Allowed / Upgrade Until defaults."""
    allowed = await settings_service.get(session, "upgrades_allowed") == "true"
    until = await settings_service.get(session, "upgrade_until")
    rows = (await session.execute(select(Series.upgrades_enabled, Series.upgrade_cutoff))).all()
    return {
        "series": len(rows),
        "upgrades_on": sum(1 for enabled, _ in rows if enabled),
        "matching": sum(1 for enabled, cutoff in rows
                        if bool(enabled) == allowed and (not allowed or (cutoff or "") == until)),
    }


@router.post("/upgrades/apply")
async def apply_upgrades_to_all(session: AsyncSession = Depends(get_session)):
    """Set every series' upgrade switch and cutoff to the saved defaults. Upgrades
    then happen through the normal monitor passes (a limited number per pass)."""
    allowed = await settings_service.get(session, "upgrades_allowed") == "true"
    until = await settings_service.get(session, "upgrade_until")
    changed = 0
    for series in (await session.execute(select(Series))).scalars():
        cutoff = until if allowed else series.upgrade_cutoff
        if series.upgrades_enabled != allowed or series.upgrade_cutoff != cutoff:
            series.upgrades_enabled = allowed
            series.upgrade_cutoff = cutoff
            changed += 1
    await session.commit()
    return {"changed": changed, **await upgrade_summary(session)}
