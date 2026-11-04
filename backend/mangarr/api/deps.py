import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_session
from ..models import ApiKey, utcnow

_api_key: str | None = None


def get_api_key() -> str:
    global _api_key
    if _api_key is None:
        _api_key = config.resolve_api_key()
    return _api_key


def replace_bootstrap_key() -> str:
    """Issue a new web UI key, so copies handed out before login was turned on
    stop working. A fixed MANGARR_API_KEY cannot be replaced from here."""
    global _api_key
    if config.api_key:
        return get_api_key()
    key = secrets.token_hex(16)
    path = config.data_dir / "api_key"
    temp = path.with_name(f".api_key-{secrets.token_hex(4)}.tmp")
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write(key)
    os.replace(temp, path)
    _api_key = key
    return key


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps for safe comparison with ``utcnow()``.

    SQLite does not preserve the timezone offset for ``DateTime`` columns, so
    values read back from it are naïve even when the model requests timezone
    awareness. Mangarr writes UTC timestamps, therefore a naïve value from the
    database represents UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def require_api_key(
    request: Request,
    x_api_key: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    # Bootstrap key handed to the web UI via initialize.json.
    if x_api_key == get_api_key():
        from .. import auth
        # Browser keys must stop working when their session expires or is revoked.
        # Explicit environment keys retain their documented script compatibility.
        if auth.enabled() and not config.api_key and not auth.valid_session(request.cookies.get(auth.COOKIE)):
            raise HTTPException(status_code=401, detail="Login required")
        return
    # User-managed keys created in Settings.
    row = (
        await session.execute(select(ApiKey).where(ApiKey.key == x_api_key))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    now = utcnow()
    # Throttle writes: only record use at most once a minute per key.
    last_used_at = _as_utc(row.last_used_at) if row.last_used_at is not None else None
    if last_used_at is None or now - last_used_at > timedelta(minutes=1):
        row.last_used_at = now
        await session.commit()
