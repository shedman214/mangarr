import time

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr import settings_service
from mangarr.api.library import resync_chapters
from mangarr.jobs import tasks
from mangarr.models import Base, Chapter, Download, DownloadKind, DownloadStatus, RootFolder, Series, SeriesSourceLink
from mangarr.sources import registry
from mangarr.sources.base import DirectSource, SourceChapter


class Source(DirectSource):
    name = "mangafire"

    def __init__(self):
        self.calls = 0
        self.error = None
        self.chapters = [SourceChapter(self.name, "new-chapter", 2.0)]

    async def search_series(self, query):
        return []

    async def list_chapters(self, external_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.chapters

    async def get_pages(self, chapter_external_id):
        return []


@pytest.fixture
async def setup(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    source = Source()
    values = dict(settings_service.DEFAULTS)
    monkeypatch.setitem(registry.DIRECT_SOURCES, source.name, source)
    monkeypatch.setattr(registry, "enabled_direct_sources", lambda _: [source] if values["source_mangafire_enabled"] == "true" else [])
    monkeypatch.setattr(tasks, "_source_retry_at", {})

    async def configure(session):
        return values
    monkeypatch.setattr(registry, "apply_settings", configure)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        series = Series(title="Example", root_folder=RootFolder(path=str(tmp_path)), folder_name="Example")
        series.chapters.append(Chapter(number=1.0, title="Keep this chapter"))
        series.source_links.append(SeriesSourceLink(source_name=source.name, external_id="abc"))
        session.add(series)
        await session.commit()
        download = Download(series_id=series.id, chapter_id=series.chapters[0].id, source_name=source.name,
                            payload="old-chapter", title="Original", kind=DownloadKind.DIRECT, status=DownloadStatus.FAILED)
        session.add(download)
        await session.commit()
        yield session, series, download, source, values
    await engine.dispose()


@pytest.mark.parametrize("failure", ["403", "empty", "disabled"])
async def test_resync_keeps_chapters_and_downloads_when_source_unavailable(setup, failure):
    session, series, download, source, values = setup
    if failure == "403":
        request = httpx.Request("GET", "https://mangafire.to/")
        source.error = httpx.HTTPStatusError("403 Forbidden", request=request, response=httpx.Response(403, request=request))
    elif failure == "empty":
        source.chapters = []
    else:
        values["source_mangafire_enabled"] = "false"
    with pytest.raises(HTTPException) as error:
        await resync_chapters(series.id, session)
    assert error.value.status_code == (502 if failure == "403" else 422)
    assert "Existing chapters were kept" in error.value.detail
    assert (await session.scalars(select(Chapter))).one().title == "Keep this chapter"
    assert (await session.scalars(select(Download))).one().id == download.id


async def test_manual_resync_checks_cooling_source_and_reuses_validated_listing(setup):
    session, series, _, source, _ = setup
    tasks._source_retry_at[source.name] = time.monotonic() + 600
    result = await resync_chapters(series.id, session)
    assert result.chapters == 1
    assert source.calls == 1
    assert (await session.scalars(select(Chapter))).one().number == 2.0


async def test_manual_resync_reports_real_error_during_cooldown(setup):
    session, series, _, source, _ = setup
    tasks._source_retry_at[source.name] = time.monotonic() + 600
    source.error = RuntimeError("403 Forbidden")
    with pytest.raises(HTTPException, match="403 Forbidden"):
        await resync_chapters(series.id, session)
    assert source.calls == 1
