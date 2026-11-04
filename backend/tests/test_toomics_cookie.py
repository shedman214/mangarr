import httpx
import pytest

from mangarr.sources.toomics import ToomicsSource
from mangarr.api import settings


@pytest.mark.parametrize("html,status", [
    ('<a href="/en/member/logout">Sign out</a>', "signed_in"),
    ("<button onclick=\"Base.popup('modal-login-header')\">Sign in</button>", "signed_out"),
    ('<html>Public catalog</html>', "unknown"),
])
async def test_session_requires_login_evidence(html, status):
    def handle(request):
        assert request.url == "https://toomics.com/en"
        assert request.headers["Cookie"] == "test=private"
        return httpx.Response(200, text=html)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        source = ToomicsSource(client)
        source.configure("test=private")
        result = await source.test_session()
        assert result["status"] == status
        assert "test=private" not in str(result)


@pytest.mark.parametrize("cookie", ["••••••••", "draft=value"])
async def test_cookie_endpoint_isolated_and_uses_saved_mask(monkeypatch, cookie):
    async def get_all(session):
        return {"toomics_session_cookie": "saved=value", "source_toomics_discovery_proxy_enabled": "true", "download_proxy_url": "http://proxy:8888"}
    async def check(source):
        assert source.session_cookie == ("saved=value" if cookie == settings.MASK else cookie)
        assert source.discovery_proxy_enabled
        assert source.discovery_proxy_url == "http://proxy:8888"
        return {"status": "unknown", "message": "Unconfirmed"}
    monkeypatch.setattr(settings.settings_service, "get_all", get_all)
    monkeypatch.setattr(ToomicsSource, "test_session", check)
    assert (await settings.toomics_cookie_test(settings.ToomicsCookieTestIn(session_cookie=cookie), None))["status"] == "unknown"


async def test_cookie_endpoint_does_not_echo_exception(monkeypatch):
    async def get_all(session):
        return {}
    async def check(source):
        raise RuntimeError("Cookie: private-secret")
    monkeypatch.setattr(settings.settings_service, "get_all", get_all)
    monkeypatch.setattr(ToomicsSource, "test_session", check)
    with pytest.raises(settings.HTTPException) as error:
        await settings.toomics_cookie_test(settings.ToomicsCookieTestIn(session_cookie="test=private"), None)
    assert error.value.status_code == 502
    assert "private" not in error.value.detail


@pytest.mark.parametrize("cookie", ["", "bad\r\nInjected: value", "x" * 16385])
async def test_empty_or_invalid_cookie_is_rejected_without_request(monkeypatch, cookie):
    async def get_all(session):
        return {}
    async def never(source):
        pytest.fail("Invalid cookies must not be sent")
    monkeypatch.setattr(settings.settings_service, "get_all", get_all)
    monkeypatch.setattr(ToomicsSource, "test_session", never)
    with pytest.raises(settings.HTTPException) as error:
        await settings.toomics_cookie_test(settings.ToomicsCookieTestIn(session_cookie=cookie), None)
    assert error.value.status_code == 422
