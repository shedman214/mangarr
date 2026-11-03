import zipfile
from unittest.mock import AsyncMock

import pytest

from mangarr import source_checks
from mangarr.sources.base import SourceChapter, SourceSeries


async def test_toonily_check_verifies_isolated_archive_without_library_changes(tmp_path, monkeypatch):
    source = AsyncMock()
    source.search_series.return_value = [SourceSeries("toonily", source_checks.TOONILY_SAMPLE, "Solo Leveling")]
    source.list_chapters.return_value = [SourceChapter("toonily", "sample/chapter-1", 1)]
    monkeypatch.setitem(source_checks.registry.DIRECT_SOURCES, "toonily", source)
    monkeypatch.setattr(source_checks.registry, "apply_settings", AsyncMock())
    monkeypatch.setattr(source_checks.config, "data_dir", tmp_path)
    async def download(src, identifier, series, chapter, dest, **kwargs):
        assert identifier == "sample/chapter-1"
        dest.parent.mkdir(parents=True)
        with zipfile.ZipFile(dest, "w") as archive:
            archive.writestr("001.webp", b"RIFF0000WEBPsample")
            archive.writestr("ComicInfo.xml", "<ComicInfo/>")
    monkeypatch.setattr(source_checks, "download_chapter_to_cbz", download)
    result = await source_checks.check_toonily(None)
    assert result["ok"] and result["pages_downloaded"] == 1
    assert result["chapters_found"] == 1
    assert str(tmp_path / "source-tests" / "toonily") in result["archive_path"]
