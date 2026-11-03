import asyncio
from pathlib import Path
import tempfile
import sqlite3
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import backups
from ..config import config

router = APIRouter(prefix="/settings/backups", tags=["backups"])


@router.post("")
async def create_backup():
    return await asyncio.to_thread(backups.create, config.data_dir)


@router.get("/restore")
async def restore_status():
    return backups.status(config.data_dir)


@router.delete("/restore", status_code=204)
async def cancel_restore():
    (backups.folder(config.data_dir) / "pending-restore.zip").unlink(missing_ok=True)


@router.post("/restore", status_code=202)
async def stage_restore(request: Request):
    from ..permission_jobs import get_job
    job = await asyncio.to_thread(get_job)
    if job and job.get("state") == "running":
        raise HTTPException(409, "Wait for the permissions operation to finish before scheduling a restore")
    with tempfile.NamedTemporaryFile(dir=backups.folder(config.data_dir), delete=False) as handle:
        path = Path(handle.name)
    try:
        size = 0
        with path.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > backups.MAX_BYTES:
                    raise HTTPException(413, "Backup exceeds the 2 GiB upload limit")
                await asyncio.to_thread(output.write, chunk)
        return await asyncio.to_thread(backups.stage, config.data_dir, path)
    except (ValueError, zipfile.BadZipFile, UnicodeError, KeyError, sqlite3.DatabaseError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.get("/{token}")
async def download_backup(token: str):
    try:
        path = backups.archive_path(config.data_dir, token)
    except ValueError as exc:
        raise HTTPException(404, "Backup not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, media_type="application/zip", filename=f"mangarr-backup-{token}.zip", headers={"Cache-Control": "no-store"})
