import httpx
import pytest

from mangarr import settings_service
from mangarr.sources import mangafire


@pytest.mark.parametrize("proxy_fails", [False, True])
async def test_signer_and_api_use_the_same_explicit_proxy_without_direct_fallback(monkeypatch, proxy_fails):
    real_client = httpx.AsyncClient
    direct_calls = []
    proxy_calls = []
    clients = []
    signed_clients = []

    def direct(request):
        direct_calls.append(request)
        return httpx.Response(200, json={"items": []})

    def proxy(request):
        proxy_calls.append(request)
        if proxy_fails:
            raise httpx.ProxyError("Proxy unreachable")
        return httpx.Response(200, json={"items": []})

    def factory(**options):
        assert options["proxy"] == "http://configured-proxy:8888"
        assert options["trust_env"] is False
        client = real_client(transport=httpx.MockTransport(proxy))
        clients.append(client)
        return client

    class Signer:
        async def sign(self, client, path, params):
            signed_clients.append(client)
            return "signed-token"

    async with real_client(transport=httpx.MockTransport(direct)) as client:
        source = mangafire.MangaFireSource(client=client, signer=Signer())
        source.discovery_proxy_enabled = True
        source.discovery_proxy_url = "http://configured-proxy:8888"
        monkeypatch.setattr(mangafire.httpx, "AsyncClient", factory)
        if proxy_fails:
            with pytest.raises(httpx.ProxyError):
                await source.search_series("Example")
        else:
            assert await source.search_series("Example") == []
    assert direct_calls == []
    assert len(proxy_calls) == (5 if proxy_fails else 1)
    assert signed_clients == clients
    assert all(c.is_closed for c in clients)


async def test_missing_discovery_proxy_url_fails_closed():
    async with httpx.AsyncClient() as client:
        source = mangafire.MangaFireSource(client=client)
        source.discovery_proxy_enabled = True
        with pytest.raises(RuntimeError, match="has no URL"):
            await source.search_series("Example")


def test_discovery_proxy_requires_configured_url():
    with pytest.raises(ValueError, match="download_proxy_url is required"):
        settings_service.validate({**settings_service.DEFAULTS, "source_mangafire_discovery_proxy_enabled": "true"})

async def test_bundled_fallback_keeps_explicit_discovery_proxy(monkeypatch):
    from mangarr.sources._mangafire_vrf import MangaFireTokenError
    real_client = httpx.AsyncClient
    direct_calls, proxy_calls = [], []
    class Signer:
        async def sign(self, client, path, params):
            raise MangaFireTokenError('frontend unavailable')
    def direct(request):
        direct_calls.append(request)
        return httpx.Response(200, json={'items': []})
    def proxy(request):
        proxy_calls.append(request)
        return httpx.Response(200, json={'items': []})
    def factory(**options):
        assert options['proxy'] == 'http://configured-proxy:8888'
        return real_client(transport=httpx.MockTransport(proxy))
    async with real_client(transport=httpx.MockTransport(direct)) as client:
        source = mangafire.MangaFireSource(client=client, signer=Signer())
        source.discovery_proxy_enabled = True
        source.discovery_proxy_url = 'http://configured-proxy:8888'
        monkeypatch.setattr(mangafire.httpx, 'AsyncClient', factory)
        await source.search_series('Solo Leveling')
    assert direct_calls == [] and len(proxy_calls) == 1
    assert proxy_calls[0].url.params['keyword'] == 'Solo Leveling'
