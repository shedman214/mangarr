"""Optional built-in login (off by default).

When enabled, ``/initialize.json`` hands the web UI its API key only to a
browser holding a valid session cookie, so the key — and with it the API —
is no longer available to anyone who can reach the port. Credentials and
sessions live in ``<data dir>/auth.json`` (0600), outside the database, so
they are not exposed through settings and not replaced by a restored backup.
Set ``MANGARR_LOGIN_DISABLED=true`` (or delete auth.json) and restart to
recover from a forgotten password.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .config import config

COOKIE = "mangarr_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_SESSIONS = 50
MIN_PASSWORD = 8
MAX_PASSWORD = 256
# Failed logins per client address before a temporary lockout.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
LOCKOUT_SECONDS = 60

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_lock = threading.Lock()
_state: dict | None = None
_failures: dict[str, list[float]] = {}


class LoginError(ValueError):
    pass


def _path() -> Path:
    return Path(config.data_dir) / "auth.json"


def _load() -> dict:
    global _state
    if _state is None:
        try:
            data = json.loads(_path().read_text())
            _state = data if isinstance(data, dict) and data.get("version") == 1 else {}
        except FileNotFoundError:
            _state = {}
    return _state


def _save(state: dict) -> None:
    global _state
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".auth-{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    _state = state


def reset_cache() -> None:
    """Forget the in-memory copy (tests, and after the file is replaced)."""
    global _state
    with _lock:
        _state = None
        _failures.clear()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32,
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual.hex(), digest)


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def enabled() -> bool:
    if config.login_disabled:
        return False
    with _lock:
        state = _load()
        return bool(state.get("enabled") and state.get("username") and state.get("password"))


def describe() -> dict:
    with _lock:
        state = _load()
        return {
            "enabled": bool(state.get("enabled")) and not config.login_disabled,
            "username": state.get("username", ""),
            "disabled_by_environment": config.login_disabled,
            "fixed_api_key": bool(config.api_key),
        }


def valid_session(token: str | None) -> bool:
    if not token or not enabled():
        return False
    with _lock:
        expires = _load().get("sessions", {}).get(_session_id(token))
        return isinstance(expires, (int, float)) and expires > time.time()


def _new_session(state: dict) -> str:
    now = time.time()
    sessions = {k: v for k, v in state.get("sessions", {}).items() if v > now}
    token = secrets.token_urlsafe(32)
    sessions[_session_id(token)] = now + SESSION_SECONDS
    # Oldest sessions go first when the cap is reached.
    state["sessions"] = dict(sorted(sessions.items(), key=lambda item: item[1])[-MAX_SESSIONS:])
    return token


def _locked_out(client: str, now: float) -> bool:
    recent = [t for t in _failures.get(client, []) if now - t < FAILURE_WINDOW]
    _failures[client] = recent
    return len(recent) >= MAX_FAILURES and now - recent[-1] < LOCKOUT_SECONDS


def login(username: str, password: str, client: str) -> str:
    """Return a new session token, or raise LoginError."""
    if not enabled():
        raise LoginError("Login is not enabled")
    now = time.time()
    with _lock:
        if _locked_out(client, now):
            raise LoginError("Too many failed attempts; wait a minute and try again")
        state = _load()
        # Always run the hash so a wrong username takes as long as a wrong password.
        ok = _check_password(password, state["password"])
        ok = hmac.compare_digest(username.encode(), state["username"].encode()) and ok
        if not ok:
            _failures.setdefault(client, []).append(now)
            raise LoginError("Incorrect username or password")
        _failures.pop(client, None)
        new = dict(state)
        token = _new_session(new)
        _save(new)
        return token


def logout(token: str | None) -> None:
    if not token:
        return
    with _lock:
        state = _load()
        sessions = state.get("sessions", {})
        if _session_id(token) in sessions:
            new = dict(state, sessions={k: v for k, v in sessions.items() if k != _session_id(token)})
            _save(new)


def _validate(username: str, password: str) -> None:
    if not (1 <= len(username) <= 64) or not username.isprintable() or username != username.strip():
        raise LoginError("Username must be 1-64 printable characters without leading or trailing spaces")
    if not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        raise LoginError(f"Password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")


def configure(*, enable: bool, username: str = "", new_password: str = "", current_password: str = "") -> str | None:
    """Turn login on/off or change the credentials. Returns a session token for
    the caller when login ends up enabled (all earlier sessions are revoked)."""
    with _lock:
        state = _load()
        active = bool(state.get("enabled") and state.get("password"))
        # Re-enabling with the saved password proves knowledge of it, like any change while on.
        reuse = enable and not new_password and bool(state.get("password"))
        recovery = config.login_disabled and bool(new_password)
        if (active or reuse) and not recovery and not _check_password(current_password, state.get("password", "")):
            raise LoginError("Current password is incorrect")
        if not enable:
            # Keep the credentials so login can be turned back on; drop every session.
            _save(dict(state, version=1, enabled=False, sessions={}))
            return None
        username = username or state.get("username", "")
        if new_password:
            _validate(username, new_password)
            password = _hash_password(new_password)
        elif reuse:
            _validate(username, "x" * MIN_PASSWORD)
            password = state["password"]
        else:
            raise LoginError("Choose a password to turn on login")
        new = {"version": 1, "enabled": True, "username": username, "password": password, "sessions": {}}
        token = _new_session(new)
        _save(new)
        return token
