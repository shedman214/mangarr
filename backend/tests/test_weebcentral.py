import httpx
import pytest
import respx

from mangarr.sources.weebcentral import BASE_URL, WeebCentralSource
from mangarr import __version__

# Real WeebCentral chapter-list structure: each link wraps a leaf <span> with
# the label, a "Last Read" span, and a <time> whose date digits must be ignored.
CHAPTER_LIST = """
<html><body>
  <a class="flex-1" href="https://weebcentral.com/chapters/01KW7C7HP8RPGDEMRKQVMQ32ZD">
    <span class="me-2"><svg></svg></span>
    <span class="grow flex items-center gap-2">
      <span>Days 265</span>
      <span class="link-info"><span class="hidden md:inline">Last Read</span></span>
      <time datetime="2026-06-28T15:05:39Z">2026-06-28T15:05:39.272196Z</time>
    </span>
  </a>
  <a class="flex-1" href="https://weebcentral.com/chapters/01KVNBE65CF5HJASF0000000000">
    <span class="grow">
      <span>Days 264</span>
      <time>2026-06-21T15:05:28Z</time>
    </span>
  </a>
</body></html>
"""


@respx.mock
async def test_search_and_chapters_identify_app_without_rejected_url_comment():
    source = WeebCentralSource()

    def response(request):
        if request.headers.get("User-Agent") != f"Mangarr/{__version__}":
            return httpx.Response(403)
        assert request.headers["Referer"] == f"{BASE_URL}/"
        if request.url.path == "/search/data":
            return httpx.Response(200, text='''
                <a href="/series/ABC/Example"><img alt="Example cover"></a>
                <a href="/series/ABC/Example">Example</a>
            ''')
        return httpx.Response(200, text='<a href="/chapters/CH1"><span>Chapter 1</span></a>')

    respx.get(f"{BASE_URL}/search/data").mock(side_effect=response)
    respx.get(f"{BASE_URL}/series/ABC/full-chapter-list").mock(side_effect=response)
    try:
        results = await source.search_series("Example")
        assert [(r.title, r.external_id) for r in results] == [("Example", "ABC")]
        assert [c.number for c in await source.list_chapters("ABC")] == [1.0]
        # Proxy discovery copies these same headers; routing stays opt-in.
        assert source.discovery_proxy_enabled is False
    finally:
        await source._client.aclose()


@respx.mock
async def test_list_chapters_ignores_last_read_and_timestamp():
    src = WeebCentralSource(client=httpx.AsyncClient())
    respx.get(f"{BASE_URL}/series/ABC/full-chapter-list").respond(text=CHAPTER_LIST)
    chapters = await src.list_chapters("ABC")
    # both chapters parsed as 264/265 — NOT the 2026 year from the <time>
    assert [c.number for c in chapters] == [264.0, 265.0]
    assert chapters[1].external_id == "01KW7C7HP8RPGDEMRKQVMQ32ZD"


@respx.mock
async def test_list_chapters_converts_season_resets_to_one_based_overall_numbers():
    source = WeebCentralSource(client=httpx.AsyncClient())
    html = """
    <a href="/chapters/S2B"><span>S2 - Chapter 1</span></a>
    <a href="/chapters/S2A"><span>S2 - Chapter 0</span></a>
    <a href="/chapters/S1B"><span>S1 - Chapter 1</span></a>
    <a href="/chapters/S1A"><span>S1 - Chapter 0</span></a>
    """
    respx.get(f"{BASE_URL}/series/ABC/full-chapter-list").respond(text=html)

    chapters = await source.list_chapters("ABC")

    assert [(chapter.number, chapter.external_id) for chapter in chapters] == [
        (1.0, "S1A"), (2.0, "S1B"), (3.0, "S2A"), (4.0, "S2B"),
    ]
    await source._client.aclose()
