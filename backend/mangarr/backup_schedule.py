"""Automatic backups (Settings → Backup and restore), like Sonarr/Preludarr: a backup
every `interval` days, automatic backups older than `retention` days removed, manual
backups kept unless a manual retention is set. Settings live in their own rows
(outside settings_service.DEFAULTS) so the main Settings form never overwrites them."""
import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from . import backups
from .config import config
from .db import session_scope
from .models import Setting

log = logging.getLogger(__name__)

DEFAULTS = {"enabled": True, "interval_days": 7, "retention_days": 28, "manual_retention_days": 0}
LIMITS = {"interval_days": (1, 365), "retention_days": (1, 3650), "manual_retention_days": (0, 3650)}
_KEY = "backup_schedule_%s"


async def load(session: AsyncSession) -> dict:
    values = dict(DEFAULTS)
    for name in DEFAULTS:
        row = await session.get(Setting, _KEY % name)
        if row is None or row.value == "":
            continue
        try:
            if name == "enabled":
                if row.value not in {"true", "false"}:
                    raise ValueError("Invalid boolean")
                values[name] = row.value == "true"
            else:
                number = int(row.value)
                low, high = LIMITS[name]
                if not low <= number <= high:
                    raise ValueError("Out of range")
                values[name] = number
        except ValueError:
            log.warning("ignoring invalid backup setting %s=%r", name, row.value)
    return values


def validate(values: dict) -> dict:
    out = {"enabled": bool(values.get("enabled"))}
    for name, (low, high) in LIMITS.items():
        try:
            number = int(values.get(name))
        except (TypeError, ValueError):
            raise ValueError(f"{name.replace('_', ' ')} must be a whole number") from None
        if not low <= number <= high:
            raise ValueError(f"{name.replace('_', ' ')} must be between {low} and {high}")
        out[name] = number
    return out


async def save(session: AsyncSession, values: dict) -> dict:
    values = validate(values)
    for name, value in values.items():
        text = ("true" if value else "false") if name == "enabled" else str(value)
        row = await session.get(Setting, _KEY % name)
        if row is None:
            session.add(Setting(key=_KEY % name, value=text))
        else:
            row.value = text
    await session.commit()
    return values


def status(values: dict, saved: list[dict]) -> dict:
    automatic = [b for b in saved if b["reason"] in backups.AUTOMATIC_REASONS]
    last = next((b["created"] for b in saved if b["reason"] == "scheduled"), None)
    return {
        **values,
        "folder": "/config/backups",
        "last_automatic": last,
        "next_automatic": (last + values["interval_days"] * 86400 if last else time.time()) if values["enabled"] else None,
        "automatic_kept": len(automatic),
        "manual_kept": len(saved) - len(automatic),
    }


def run_once(values: dict, now: float | None = None) -> dict:
    """Make a scheduled backup when one is due, then apply retention. Blocking."""
    now = time.time() if now is None else now
    made = None
    if values["enabled"]:
        last = next((b["created"] for b in backups.list_backups(config.data_dir) if b["reason"] == "scheduled"), None)
        if last is None or now - last >= values["interval_days"] * 86400:
            made = backups.create(config.data_dir, reason="scheduled")
            log.info("Automatic backup %s saved (%d bytes)", made["id"], made["bytes"])
    removed = backups.prune(config.data_dir, values["retention_days"], values["manual_retention_days"], now)
    if removed:
        log.info("Removed %d backup(s) past their retention period", len(removed))
    return {"created": made, "removed": len(removed)}


async def scheduled_job() -> None:
    """Hourly: cheap unless a backup is due."""
    try:
        async with session_scope() as session:
            values = await load(session)
        await asyncio.to_thread(run_once, values)
    except Exception:
        log.exception("automatic backup failed")
