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
_mangafire_lock = asyncio.Lock()
TOONILY_SAMPLE = "solo-leveling-574def58"


async def check_toonily(session) -> dict:
    return await _check_sample(session, "toonily", TOONILY_SAMPLE, _toonily_lock)


async def check_mangafire(session) -> dict:
    return await _check_sample(session, "mangafire", "lrmyz", _mangafire_lock)


async def _check_sample(session, name: str, sample: str, lock: asyncio.Lock) -> dict:
    if lock.locked():
        raise ValueError(f"A {name} download test is already running")
    async with lock:
        await registry.apply_settings(session)
        source = registry.DIRECT_SOURCES[name]
        results = await source.search_series("Solo Leveling")
        matched = next((r for r in results if r.external_id == sample), None)
        if matched is None:
            raise ValueError(f"{name} search did not find the sample series")
        chapters = await source.list_chapters(sample)
        chapter = next((c for c in chapters if c.number == 1), None)
        if chapter is None:
            raise ValueError(f"{name} did not list sample chapter 1")
        destination = config.data_dir / "source-tests" / name / uuid.uuid4().hex / "Solo Leveling - Chapter 001.cbz"
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
