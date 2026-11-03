import httpx
import pytest
from fastapi import FastAPI

from mangarr.api import backups as routes
from mangarr import permission_jobs


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(routes.config, "data_dir", tmp_path)
    application = FastAPI()
    application.include_router(routes.router)
    return application


async def test_restore_rejects_running_permission_job(app, monkeypatch):
    monkeypatch.setattr(permission_jobs, "get_job", lambda: {"state": "running"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/settings/backups/restore", content=b"ignored")
    assert result.status_code == 409


async def test_invalid_upload_and_cancel(app, monkeypatch):
    monkeypatch.setattr(permission_jobs, "get_job", lambda: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/settings/backups/restore", content=b"not a zip")
        assert result.status_code == 400
        assert (await client.get("/settings/backups/restore")).json() == {"pending": False}
        assert (await client.delete("/settings/backups/restore")).status_code == 204
        assert (await client.get("/settings/backups/not-a-token")).status_code == 404


async def test_backup_routes_require_app_auth():
    from mangarr.main import api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.post("/settings/backups")).status_code in {401,403}
        assert (await client.post("/settings/backups/restore", content=b"bad")).status_code in {401,403}


async def test_list_backups(app, tmp_path):
    from tests.test_backups import library
    library(tmp_path).close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/settings/backups")).json() == []
        made = (await client.post("/settings/backups")).json()
        listed = (await client.get("/settings/backups")).json()
    assert [(b["id"], b["reason"]) for b in listed] == [(made["id"], "manual")]
