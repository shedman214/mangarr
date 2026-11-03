import httpx
import pytest

from mangarr import source_status


@pytest.fixture(autouse=True)
def clear(monkeypatch):
    monkeypatch.setattr(source_status, "_observations", {})


def test_status_records_success_failure_recovery_without_leaking_credentials():
    source_status.success("example", 12)
    request = httpx.Request("GET", "https://user:secret@example.com/?token=private")
    error = httpx.HTTPStatusError("secret response", request=request, response=httpx.Response(403, request=request))
    source_status.failure("example", error, 600)
    row = source_status.snapshot()["example"]
    assert row["last_success"] and row["last_failure"] and row["retry_at"]
    assert row["error"] == "Source returned HTTP 403."
    assert "secret" not in str(row) and "private" not in str(row)
    source_status.success("example", 0)
    row = source_status.snapshot()["example"]
    assert row["chapters"] == 0 and row["error"] is None and row["retry_at"] is None
    assert row["last_failure"]


def test_timeout_and_network_failures_are_distinct():
    assert "timed out" in source_status.failure("example", TimeoutError())
    assert "proxy" in source_status.failure("example", httpx.ConnectError("private credentials"))
