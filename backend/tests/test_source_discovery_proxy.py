import httpx
import pytest

from mangarr import settings_service
from mangarr.sources import base, registry


SOURCES = {**registry.DIRECT_SOURCES, **registry.TORRENT_INDEXERS}


@pytest.mark.parametrize("name", list(SOURCES))
@pytest.mark.parametrize("enabled", [False, True])
async def test_every_source_has_independent_discovery_routing(monkeypatch, name, enabled):
    real_client = httpx.AsyncClient
    direct_requests, proxy_requests, proxy_clients = [], [], []

    def direct(request):
        direct_requests.append(request)
        return httpx.Response(200, text="direct")

    def proxy(request):
        proxy_requests.append(request)
        return httpx.Response(200, text="proxy", headers={"Set-Cookie": "session=abc; Path=/"})

    def factory(**kwargs):
        assert kwargs["proxy"] == "http://chosen-proxy:8888"
        assert kwargs["trust_env"] is False
        assert kwargs["headers"]["User-Agent"] == "Source test"
        client = real_client(transport=httpx.MockTransport(proxy))
        proxy_clients.append(client)
        return client

    async with real_client(headers={"User-Agent": "Source test"}, transport=httpx.MockTransport(direct)) as client:
        source = type(SOURCES[name])(client=client)
        source.discovery_proxy_enabled = enabled
        source.discovery_proxy_url = "http://chosen-proxy:8888" if enabled else ""
        monkeypatch.setattr(base.httpx, "AsyncClient", factory)
        response = await source.discovery_request("GET", "https://source.example/search", retries=0)
        assert response.text == ("proxy" if enabled else "direct")
        assert len(direct_requests) == (0 if enabled else 1)
        assert len(proxy_requests) == (1 if enabled else 0)
        assert all(c.is_closed for c in proxy_clients)
        assert not client.is_closed
        if enabled:
            assert client.cookies["session"] == "abc"


@pytest.mark.parametrize("name", list(SOURCES))
def test_every_discovery_switch_requires_a_proxy_address(name):
    key = f"source_{name}_discovery_proxy_enabled"
    assert settings_service.DEFAULTS[key] == "false"
    with pytest.raises(ValueError, match="download_proxy_url is required"):
        settings_service.validate({**settings_service.DEFAULTS, key: "true"})


async def test_settings_configure_each_discovery_route_without_enabling_image_proxy(monkeypatch):
    values = {**settings_service.DEFAULTS, "download_proxy_url": "http://chosen-proxy:8888"}
    for i, name in enumerate(SOURCES):
        values[f"source_{name}_discovery_proxy_enabled"] = "true" if i % 2 else "false"
    async def get_all(session):
        return values
    monkeypatch.setattr(settings_service, "get_all", get_all)
    async with httpx.AsyncClient() as client:
        direct = {name: type(src)(client=client) for name, src in registry.DIRECT_SOURCES.items()}
        indexers = {name: type(src)(client=client) for name, src in registry.TORRENT_INDEXERS.items()}
        monkeypatch.setattr(registry, "DIRECT_SOURCES", direct)
        monkeypatch.setattr(registry, "TORRENT_INDEXERS", indexers)
        await registry.apply_settings(None)
        for name, src in {**direct, **indexers}.items():
            enabled = values[f"source_{name}_discovery_proxy_enabled"] == "true"
            assert src.discovery_proxy_enabled == enabled
            assert src.discovery_proxy_url == (values["download_proxy_url"] if enabled else "")
            if name in direct:
                assert not src.content_proxy_enabled
