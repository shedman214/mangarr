"""Small, credential-free source observations for the running process."""
from datetime import datetime, timedelta, timezone

import httpx

_observations: dict[str, dict] = {}


def success(name: str, count: int | None = None) -> None:
    previous = _observations.get(name, {})
    _observations[name] = {**previous, "state": "ok", "last_success": datetime.now(timezone.utc).isoformat(),
                           "error": None, "retry_at": None, "chapters": count}


def failure(name: str, exc: BaseException, retry_seconds: float = 0) -> str:
    # Never expose request URLs, proxy credentials, response bodies or tokens.
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"Source returned HTTP {exc.response.status_code}."
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        message = "Source request timed out."
    elif isinstance(exc, httpx.RequestError):
        message = "Could not connect to the source or its configured proxy."
    else:
        message = f"Source check failed ({type(exc).__name__})."
    now = datetime.now(timezone.utc)
    _observations[name] = {**_observations.get(name, {}), "state": "error", "last_failure": now.isoformat(),
                           "error": message, "retry_at": (now + timedelta(seconds=retry_seconds)).isoformat() if retry_seconds else None}
    return message


def snapshot() -> dict[str, dict]:
    return {name: dict(value) for name, value in _observations.items()}
