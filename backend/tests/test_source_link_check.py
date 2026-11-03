from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mangarr import source_status
from mangarr.api import library
from mangarr.jobs import tasks


@pytest.fixture
def setup(monkeypatch):
    link = SimpleNamespace(series_id=1, source_name="mangadex", external_id="sample")
    session = SimpleNamespace(get=AsyncMock(return_value=link))
    src = SimpleNamespace(name="mangadex", list_chapters=AsyncMock(return_value=[]),
                          chapter_diagnostics=AsyncMock(return_value={"language": "en", "external_releases": 24}))
    monkeypatch.setattr(library.registry, "DIRECT_SOURCES", {"mangadex": src})
    monkeypatch.setattr(library.registry, "apply_settings", AsyncMock(return_value={"source_mangadex_enabled": "true"}))
    monkeypatch.setattr(source_status, "_observations", {})
    monkeypatch.setattr(tasks, "_source_retry_at", {})
    return session, src


async def test_check_reports_external_only_without_modifying_library(setup):
    session, src = setup
    result = await library.check_source_link(1, 10, session)
    assert result["state"] == "empty" and result["chapters"] == 0
    assert "24 external" in result["message"] and "kept" in result["message"]
    src.list_chapters.assert_awaited_once_with("sample")
    assert source_status.snapshot()["mangadex"]["last_success"]


async def test_check_exposes_sanitized_failure_and_retry_time(setup):
    session, src = setup
    request = httpx.Request("GET", "https://secret:password@example.test")
    src.list_chapters.side_effect = httpx.HTTPStatusError("private", request=request, response=httpx.Response(503, request=request))
    result = await library.check_source_link(1, 10, session)
    assert result == {"state": "error", "message": "Source returned HTTP 503."}
    assert source_status.snapshot()["mangadex"]["retry_at"]


async def test_check_rejects_link_from_another_series(setup):
    session, _ = setup
    with pytest.raises(library.HTTPException) as exc:
        await library.check_source_link(2, 10, session)
    assert exc.value.status_code == 404
