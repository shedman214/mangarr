import json
import stat

import httpx
import pytest

from mangarr import auth
from mangarr.api import deps
from mangarr.config import config
from mangarr.db import get_session
from mangarr.main import api, app
from mangarr.models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", tmp_path)
    monkeypatch.setattr(config, "api_key", "")
    monkeypatch.setattr(config, "login_disabled", False)
    monkeypatch.setattr(deps, "_api_key", None)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with Session() as s:
            yield s
    api.dependency_overrides[get_session] = session
    auth.reset_cache()
    yield
    auth.reset_cache()
    api.dependency_overrides.pop(get_session, None)
    await engine.dispose()


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def enable(c, key, **extra):
    body = {"enabled": True, "username": "admin", "new_password": PASSWORD, **extra}
    return await c.put("/api/v1/settings/login", json=body, headers={"X-Api-Key": key})


async def test_login_is_off_by_default_and_bootstrap_stays_open():
    async with client() as c:
        boot = (await c.get("/initialize.json")).json()
        assert boot["apiKey"] and boot["login"] is False
        assert (await c.get("/auth/status")).json() == {"enabled": False, "authenticated": False}
        assert (await c.post("/auth/login", json={"username": "admin", "password": PASSWORD})).status_code == 401


async def test_enabling_login_protects_bootstrap_and_replaces_the_old_key(tmp_path):
    async with client() as c:
        old_key = (await c.get("/initialize.json")).json()["apiKey"]
        too_short = await c.put("/api/v1/settings/login", headers={"X-Api-Key": old_key},
                                json={"enabled": True, "username": "admin", "new_password": "short"})
        assert too_short.status_code == 400 and not auth.enabled()
        result = await enable(c, old_key)
        assert result.status_code == 200
        new_key = result.json()["apiKey"]
        assert new_key != old_key
        cookie = result.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        # The old key no longer opens the API; the enabling browser stays signed in.
        assert (await c.get("/api/v1/settings/login", headers={"X-Api-Key": old_key})).status_code == 401
        assert (await c.get("/initialize.json")).json()["apiKey"] == new_key
    stored = tmp_path / "auth.json"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert PASSWORD not in stored.read_text() and json.loads(stored.read_text())["password"].startswith("scrypt$")

    async with client() as stranger:
        boot = await stranger.get("/initialize.json")
        assert boot.status_code == 401 and boot.json()["loginRequired"] and "apiKey" not in boot.json()
        assert (await stranger.get("/api/v1/settings/login")).status_code == 401


async def test_sign_in_wrong_password_lockout_and_sign_out():
    async with client() as c:
        await enable(c, (await c.get("/initialize.json")).json()["apiKey"])
    async with client() as c:
        wrong = await c.post("/auth/login", json={"username": "admin", "password": "not the password"})
        assert wrong.status_code == 401 and "Incorrect" in wrong.json()["detail"]
        assert (await c.post("/auth/login", json={"username": "Admin", "password": PASSWORD})).status_code == 401
        ok = await c.post("/auth/login", json={"username": "admin", "password": PASSWORD})
        assert ok.status_code == 204
        assert (await c.get("/auth/status")).json() == {"enabled": True, "authenticated": True}
        token = c.cookies[auth.COOKIE]
        assert (await c.get("/initialize.json")).status_code == 200
        assert (await c.post("/auth/logout")).status_code == 204
        # The token is revoked on the server, not only removed from this browser.
        c.cookies.set(auth.COOKIE, token)
        assert (await c.get("/initialize.json")).status_code == 401
    async with client() as c:
        for _ in range(auth.MAX_FAILURES):
            await c.post("/auth/login", json={"username": "admin", "password": "guess guess"})
        locked = await c.post("/auth/login", json={"username": "admin", "password": PASSWORD})
        assert locked.status_code == 401 and "Too many" in locked.json()["detail"]


async def test_changes_and_disabling_need_the_current_password():
    async with client() as c:
        key = (await enable(c, (await c.get("/initialize.json")).json()["apiKey"])).json()["apiKey"]
        headers = {"X-Api-Key": key}
        bad = await c.put("/api/v1/settings/login", headers=headers, json={"enabled": False, "current_password": "nope nope"})
        assert bad.status_code == 400 and auth.enabled()
        changed = await c.put("/api/v1/settings/login", headers=headers, json={
            "enabled": True, "username": "admin", "new_password": "another password", "current_password": PASSWORD})
        assert changed.status_code == 200 and "apiKey" not in changed.json()  # key is only replaced when turning login on
        off = await c.put("/api/v1/settings/login", headers=headers, json={"enabled": False, "current_password": "another password"})
        assert off.status_code == 200 and not auth.enabled()
    async with client() as stranger:
        assert (await stranger.get("/initialize.json")).status_code == 200
        # Turning it back on with the saved password requires that password.
        h = {"X-Api-Key": key}
        assert (await stranger.put("/api/v1/settings/login", headers=h, json={"enabled": True})).status_code == 400
        again = await stranger.put("/api/v1/settings/login", headers=h, json={"enabled": True, "current_password": "another password"})
        assert again.status_code == 200 and auth.enabled()


async def test_environment_recovery_switch_and_fixed_key(monkeypatch):
    monkeypatch.setattr(config, "api_key", "f" * 32)
    async with client() as c:
        result = await enable(c, "f" * 32)
        assert result.json()["apiKey"] == "f" * 32 and result.json()["fixed_api_key"] is True
    monkeypatch.setattr(config, "login_disabled", True)
    async with client() as stranger:
        assert (await stranger.get("/initialize.json")).json()["apiKey"] == "f" * 32
        assert (await stranger.get("/auth/status")).json()["enabled"] is False


async def test_cookie_is_secure_behind_https_proxy():
    async with client() as c:
        key = (await c.get("/initialize.json")).json()["apiKey"]
        result = await c.put("/api/v1/settings/login", headers={"X-Api-Key": key, "X-Forwarded-Proto": "https"},
                             json={"enabled": True, "username": "admin", "new_password": PASSWORD})
        assert "secure" in result.headers["set-cookie"].lower()
