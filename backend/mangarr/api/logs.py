"""Settings → Logs: level (Info/Debug/Trace), recent lines and a ZIP download."""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import logs
from ..config import config
from ..db import get_session
from ..models import Setting

router = APIRouter(prefix="/system/logs", tags=["system"])
log = logging.getLogger("mangarr")
# Stored outside settings_service.DEFAULTS so the main Settings form never overwrites it.
SETTING_KEY = "log_level"


class LevelIn(BaseModel):
    level: str


async def apply_saved_level(session: AsyncSession) -> None:
    row = await session.get(Setting, SETTING_KEY)
    if row is not None and row.value:
        try:
            logs.set_level(row.value)
        except ValueError:
            log.warning("Ignoring invalid saved log level %r", row.value)


async def _source(session: AsyncSession) -> str:
    row = await session.get(Setting, SETTING_KEY)
    return "settings" if row is not None and row.value else "environment"


@router.get("")
async def get_logs(response: Response, lines: int = Query(300, ge=1, le=2000), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return {
        "level": logs.current_level(),
        "source": await _source(session),
        "environment_level": logs.environment_level(config.log_level),
        "file_enabled": logs.current_file() is not None,
        "files": await asyncio.to_thread(logs.files),
        "lines": await asyncio.to_thread(logs.tail, lines),
    }


@router.put("/level")
async def set_level(body: LevelIn, session: AsyncSession = Depends(get_session)):
    try:
        level = logs.set_level(body.level)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = await session.get(Setting, SETTING_KEY)
    if row is None:
        session.add(Setting(key=SETTING_KEY, value=level))
    else:
        row.value = level
    await session.commit()
    log.info("Log level set to %s", level.capitalize())
    return {"level": level, "source": "settings"}


@router.get("/download")
async def download():
    data = await asyncio.to_thread(logs.archive)
    name = time.strftime("mangarr-logs-%Y%m%d-%H%M%S.zip")
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
