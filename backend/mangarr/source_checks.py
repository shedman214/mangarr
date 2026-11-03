"""Explicit, isolated source download checks that never change the library."""

import asyncio
import os
import stat
import uuid
import zipfile
from types import SimpleNamespace

from .config import config
from .download.direct import download_chapter_to_cbz
from .sources import registry

_toonily_lock = asyncio.Lock()
TOONILY_SAMPLE = "solo-leveling-574def58"


async def check_toonily(session) -> dict:
    if _toonily_lock.locked():
        raise ValueError("A Toonily download test is already running")
    async with _toonily_lock:
        await registry.apply_settings(session)
        source = registry.DIRECT_SOURCES["toonily"]
        results = await source.search_series("Solo Leveling")
        matched = next((r for r in results if r.external_id == TOONILY_SAMPLE), None)
        if matched is None:
            raise ValueError("Toonily search did not find the sample series")
        chapters = await source.list_chapters(TOONILY_SAMPLE)
        chapter = next((c for c in chapters if c.number == 1), None)
        if chapter is None:
            raise ValueError("Toonily did not list sample chapter 1")
        destination = config.data_dir / "source-tests" / "toonily" / uuid.uuid4().hex / "Solo Leveling - Chapter 001.cbz"
        await download_chapter_to_cbz(
            source, chapter.external_id,
            SimpleNamespace(title="Solo Leveling", description=""),
            SimpleNamespace(number=1.0, id=None, volume=chapter.volume, title=chapter.title),
            destination, web_url=chapter.url,
        )

        def verify():
            with zipfile.ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise ValueError("The sample archive failed its integrity check")
                images = [name for name in archive.namelist() if name != "ComicInfo.xml"]
                if not images:
                    raise ValueError("The sample archive contains no images")
            info = destination.stat()
            return {"ok": True, "series": "Solo Leveling", "chapter": 1,
                    "chapters_found": len(chapters), "pages_downloaded": len(images),
                    "archive_path": str(destination), "archive_bytes": info.st_size,
                    "mode": format(stat.S_IMODE(info.st_mode), "03o"),
                    "uid": info.st_uid if hasattr(os, "getuid") else None,
                    "gid": info.st_gid if hasattr(os, "getgid") else None}
        return await asyncio.to_thread(verify)
