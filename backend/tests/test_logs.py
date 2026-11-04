import io
import logging
import zipfile

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import logs
from mangarr.api import logs as routes
from mangarr.db import get_session
from mangarr.models import Base, Setting

KAVITA_KEY = "k4v1ta-secret-key-123"


@pytest.fixture(autouse=True)
def restore_logging():
    yield
    logs.set_secrets([])
    logs.setup(None, "info")


def test_redaction_masks_secrets_but_keeps_ordinary_text():
    logs.set_secrets([KAVITA_KEY, "abc"])  # too short to mask: would hide ordinary words
    cases = {
        f'HTTP Request: POST http://kavita:5000/api/Plugin/authenticate?apiKey={KAVITA_KEY}&pluginName=mangarr "HTTP/1.1 200 OK"':
            'HTTP Request: POST http://kavita:5000/api/Plugin/authenticate?apiKey=<redacted>&pluginName=mangarr "HTTP/1.1 200 OK"',
        "proxy http://user:hunter2@gluetun:8888 failed": "proxy http://<redacted>@gluetun:8888 failed",
        "GET /x?token=abc123&page=2": "GET /x?token=<redacted>&page=2",
        "headers {'Authorization': 'Bearer eyJhbGciOi'}": "headers {'Authorization': 'Bearer <redacted>'}",
        "X-Api-Key: 0123456789abcdef": "X-Api-Key: <redacted>",
        f"kavita said {KAVITA_KEY} was wrong": "kavita said <redacted> was wrong",
        "Current password is incorrect; token rejected, retrying once": "Current password is incorrect; token rejected, retrying once",
        "abc chapter 12 downloaded": "abc chapter 12 downloaded",
    }
    for raw, expected in cases.items():
        assert logs.redact(raw) == expected


def test_levels_control_mangarr_and_noisy_libraries():
    assert logs.set_level("info") == "info"
    assert logging.getLogger("mangarr").level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    logs.set_level("debug")
    assert logging.getLogger("mangarr").level == logging.DEBUG
    assert logging.getLogger().level == logging.INFO  # libraries' own debug output stays out
    assert logging.getLogger("httpx").level == logging.WARNING  # outbound requests only at Trace
    assert logging.getLogger("uvicorn.access").level == logging.INFO
    logs.set_level("TRACE")
    assert logging.getLogger("mangarr").level == logs.TRACE
    assert logging.getLogger("httpx").level == logging.INFO
    with pytest.raises(ValueError):
        logs.set_level("verbose")
    assert logs.environment_level("WARNING") == "info"
    assert logs.environment_level("nonsense") == "info"
    assert logs.environment_level("Debug") == "debug"


def test_file_is_redacted_tailed_rotated_and_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "MAX_BYTES", 2000)
    logs.setup(tmp_path, "trace")
    log = logging.getLogger("mangarr.test")
    log.log(logs.TRACE, "trace line visible")
    log.info("late secret %s written before it was known", "s3cret-value-xyz")
    logs.set_secrets([KAVITA_KEY])
    log.info("request ?apiKey=%s", KAVITA_KEY)
    try:
        raise RuntimeError(f"boom with password={KAVITA_KEY}")
    except RuntimeError:
        log.exception("failed")
    lines = logs.tail(50)
    text = "\n".join(lines)
    assert "trace line visible" in text and "TRACE" in text
    assert KAVITA_KEY not in text and "apiKey=<redacted>" in text and "password=<redacted>" in text
    for n in range(60):
        log.info("filler line %03d %s", n, "x" * 60)
    names = [f["name"] for f in logs.files()]
    assert names[0] == "mangarr.txt" and "mangarr.txt.1" in names and len(names) <= logs.BACKUPS + 1
    assert logs.tail(3)[-1].endswith("filler line 059 " + "x" * 60)
    # Secrets learned after a line was written are masked in the download too.
    logs.set_secrets([KAVITA_KEY, "s3cret-value-xyz"])
    with zipfile.ZipFile(io.BytesIO(logs.archive())) as z:
        assert set(z.namelist()) == set(names)
        everything = "".join(z.read(n).decode() for n in z.namelist())
    assert "s3cret-value-xyz" not in everything and KAVITA_KEY not in everything
    assert "late secret <redacted> written" in everything


def test_unwritable_log_folder_keeps_console_logging(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("")
    logs.setup(blocker, "info")  # <blocker>/logs cannot be created
    assert logs.current_file() is None and logs.files() == [] and logs.tail() == []


@pytest.fixture
async def client(tmp_path):
    logs.setup(tmp_path, "info")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'logs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with Session() as s:
            yield s
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.Session = Session
        yield c
    await engine.dispose()


async def test_level_is_saved_applied_and_reported(client):
    state = (await client.get("/system/logs")).json()
    assert state["level"] == "info" and state["source"] == "environment" and state["file_enabled"]
    assert (await client.put("/system/logs/level", json={"level": "loud"})).status_code == 422
    saved = await client.put("/system/logs/level", json={"level": "debug"})
    assert saved.json() == {"level": "debug", "source": "settings"}
    assert logging.getLogger("mangarr").level == logging.DEBUG
    state = (await client.get("/system/logs", params={"lines": 5})).json()
    assert state["source"] == "settings" and any("Log level set to Debug" in line for line in state["lines"])
    async with client.Session() as session:
        assert (await session.get(Setting, "log_level")).value == "debug"
        logs.set_level("info")
        await routes.apply_saved_level(session)  # what startup does
    assert logs.current_level() == "debug"


async def test_download_is_a_zip_attachment(client):
    logging.getLogger("mangarr").warning("something to download")
    result = await client.get("/system/logs/download")
    assert result.status_code == 200 and result.headers["content-type"] == "application/zip"
    assert result.headers["content-disposition"].startswith('attachment; filename="mangarr-logs-')
    assert result.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(result.content)) as z:
        assert "something to download" in z.read("mangarr.txt").decode()


async def test_browser_key_is_masked_once_loaded_or_replaced(tmp_path, monkeypatch):
    from mangarr import settings_service
    from mangarr.api import deps
    from mangarr.config import config
    monkeypatch.setattr(config, "data_dir", tmp_path)
    monkeypatch.setattr(config, "api_key", "")
    monkeypatch.setattr(deps, "_api_key", None)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'k.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        await settings_service.get_all(session)
        assert not (tmp_path / "api_key").exists()  # reading settings never creates the key file
        key = deps.get_api_key()
        await settings_service.get_all(session)
        assert logs.redact(f"X {key} Y") == "X <redacted> Y"
    new = deps.replace_bootstrap_key()
    assert logs.redact(f"X {new} Y") == "X <redacted> Y"
    await engine.dispose()
