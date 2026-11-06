"""English volume covers (Settings → Media Management, off by default).

Kavita has no separate cover file for a volume made of chapter archives: the
volume's cover is the cover of its first chapter, and an archive's cover is
the image named "cover" (else its first image). So mangarr puts the English
cover from MangaBaka into the lowest-numbered chapter file of each volume as
`000_cover.<ext>` — Kavita's cover pattern, sorted first so it is also the
first page, as in a printed volume — and takes it out of that volume's other
files when an earlier chapter arrives later. Volumes without an English cover
are left as they are.

The MangaBaka image id is stored as the entry's ZIP comment, so a file that
already carries the right cover is not rewritten. Each archive is rewritten
to a temporary file beside it and swapped in, keeping its mode and owner."""

import logging
import os
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx

from .. import USER_AGENT

from ..download.cbz import guess_extension
from .matcher import VOLUME_COVER_STEM, is_volume_cover

log = logging.getLogger(__name__)

ARCHIVE_SUFFIXES = {".cbz", ".zip"}
COVER_HOSTS = ("mangabaka.dev", "mangabaka.org")
MAX_COVER_BYTES = 20 * 1024 * 1024


def fetch_cover(url: str) -> bytes:
    """Download a cover image from MangaBaka's image host (blocking)."""
    host = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in COVER_HOSTS):
        raise ValueError(f"not a MangaBaka image address: {host}")
    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=60,
                      follow_redirects=False) as resp:
        resp.raise_for_status()
        data = bytearray()
        for chunk in resp.iter_bytes():
            data += chunk
            if len(data) > MAX_COVER_BYTES:
                raise ValueError("the cover image is too large")
    return bytes(data)


@dataclass
class CoverResult:
    added: list[int] = field(default_factory=list)  # volumes given (or moved to) a cover
    removed: int = 0  # covers taken out of a volume's later chapter files
    unchanged: int = 0
    missing: list[int] = field(default_factory=list)  # on disk, no English cover listed
    failed: list[str] = field(default_factory=list)


def plan(chapters) -> dict[int, tuple[Path, list[Path]]]:
    """Per volume on disk: the archive that should carry the cover (the
    lowest-numbered chapter's) and the volume's other archives. An archive
    holding chapters of several volumes (a multi-volume torrent file) is
    never touched."""
    files: dict[int, dict[Path, float]] = {}
    volumes_of: dict[Path, set[int]] = {}
    for chapter in chapters:
        if not chapter.downloaded or not chapter.file_path or chapter.volume is None:
            continue
        path = Path(chapter.file_path)
        if path.suffix.lower() not in ARCHIVE_SUFFIXES:
            continue
        lowest = files.setdefault(chapter.volume, {})
        lowest[path] = min(lowest.get(path, chapter.number), chapter.number)
        volumes_of.setdefault(path, set()).add(chapter.volume)
    out = {}
    for volume, paths in files.items():
        if any(len(volumes_of[p]) > 1 for p in paths):
            continue
        ordered = sorted(paths, key=lambda p: (paths[p], str(p)))
        out[volume] = (ordered[0], ordered[1:])
    return out


def cover_id(path: Path) -> str | None:
    """The MangaBaka id of the cover mangarr put in this archive, "" for a
    cover without one, None for no cover."""
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if is_volume_cover(info.filename):
                return info.comment.decode("utf-8", "replace")
    return None


def rewrite(path: Path, cover: tuple[bytes, str] | None) -> None:
    """Replace the archive with a copy whose added cover is `cover`
    ((image bytes, MangaBaka id)) or none at all."""
    stat = path.stat()
    with NamedTemporaryFile(dir=path.parent, prefix=".mangarr-cover-", suffix=".partial",
                            delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as out:
            if cover is not None:
                data, image_id = cover
                info = zipfile.ZipInfo(VOLUME_COVER_STEM + guess_extension(data))
                info.compress_type = zipfile.ZIP_STORED
                info.comment = image_id.encode()
                out.writestr(info, data)
            for info in source.infolist():
                if is_volume_cover(info.filename):
                    continue
                # streamed entry by entry: memory stays at one copy buffer,
                # whatever the archive's size
                copy = zipfile.ZipInfo(info.filename, info.date_time)
                copy.compress_type = info.compress_type
                copy.external_attr = info.external_attr
                copy.comment = info.comment
                copy.file_size = info.file_size  # lets zipfile pick ZIP64 up front
                with source.open(info) as src, out.open(copy, "w") as dst:
                    shutil.copyfileobj(src, dst, 256 * 1024)
        os.chmod(temporary, stat.st_mode & 0o7777)
        try:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass  # not running as root: the file stays ours, as new downloads are
        # the swap fails rather than clobbering a file that changed meanwhile
        if path.stat().st_mtime_ns != stat.st_mtime_ns:
            raise RuntimeError("the file changed while its cover was being written")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sync(
    chapters,
    covers: dict[str, dict],
    fetch: Callable[[str], bytes],
) -> CoverResult:
    """Make each volume's first chapter file carry its English cover, and no
    other file of the volume. `fetch(url)` returns image bytes (blocking; it
    is only called for a file that needs a new cover)."""
    result = CoverResult()
    for volume, (target, others) in sorted(plan(chapters).items()):
        cover = covers.get(str(volume))
        if cover is None:
            result.missing.append(volume)
            continue
        wanted = str(cover.get("id"))
        try:
            if cover_id(target) == wanted:
                result.unchanged += 1
            else:
                data = fetch(cover["url"])
                if guess_extension(data, "") not in {".jpg", ".png", ".webp", ".gif", ".avif"}:
                    raise ValueError("the cover is not an image")
                rewrite(target, (data, wanted))
                result.added.append(volume)
            for other in others:
                if cover_id(other) is not None:
                    rewrite(other, None)
                    result.removed += 1
        except Exception as exc:
            log.warning("volume %d cover for %s skipped: %s", volume, target, exc)
            result.failed.append(f"Volume {volume}: {exc}")
    return result
