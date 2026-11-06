"""English volume covers: each volume's first chapter file carries its English
cover as `000_cover.*` (Kavita's cover name, first in reading order), no other
file of the volume does, and nothing else in the archive changes."""
import json
import os
import zipfile
from types import SimpleNamespace

import pytest

from mangarr.jobs import tasks
from mangarr.library import volume_covers as vc
from mangarr.library.matcher import archive_page_signature, page_bytes_signature
from mangarr.models import Chapter, Series

JPG = b"\xff\xd8\xff\xe0" + b"cover" * 50
JPG2 = b"\xff\xd8\xff\xe0" + b"other" * 50
PAGES = [b"\x89PNG\r\n\x1a\n" + bytes([i]) * 40 for i in range(3)]


def cbz(path, pages=PAGES, compress=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, "w", compress) as z:
        z.writestr("ComicInfo.xml", "<ComicInfo><Volume>1</Volume></ComicInfo>")
        for i, page in enumerate(pages, 1):
            z.writestr(f"{i:03d}.png", page)
    os.chmod(path, 0o664)
    return path


def names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def chapter(number, volume, path, downloaded=True):
    return SimpleNamespace(number=number, volume=volume, file_path=str(path), downloaded=downloaded)


COVERS = {"1": {"id": 11, "url": "https://images.mangabaka.dev/a"},
          "2": {"id": 22, "url": "https://images.mangabaka.dev/b"}}


def test_cover_goes_first_into_each_volumes_lowest_chapter(tmp_path):
    files = {n: cbz(tmp_path / f"c{n}.cbz") for n in (1, 2, 3, 4)}
    chapters = [chapter(2, 1, files[2]), chapter(1, 1, files[1]), chapter(3, 2, files[3]),
                chapter(4, None, files[4])]
    fetched = []
    result = vc.sync(chapters, COVERS, lambda url: fetched.append(url) or JPG)
    assert result.added == [1, 2] and result.missing == [] and not result.failed
    assert names(files[1])[0] == "000_cover.jpg" and names(files[1])[1:] == names(files[2])
    assert names(files[3])[0] == "000_cover.jpg"
    assert "000_cover.jpg" not in names(files[2]) + names(files[4])
    # pages, ComicInfo and the mode are kept
    with zipfile.ZipFile(files[1]) as z:
        assert [z.read(f"{i:03d}.png") for i in (1, 2, 3)] == PAGES
        assert z.read("000_cover.jpg") == JPG and z.getinfo("000_cover.jpg").comment == b"11"
    assert files[1].stat().st_mode & 0o777 == 0o664
    assert not list(tmp_path.glob(".mangarr-cover-*"))
    # a second pass downloads and rewrites nothing
    before = files[1].stat().st_mtime_ns
    again = vc.sync(chapters, COVERS, lambda url: pytest.fail("fetched again"))
    assert again.added == [] and again.unchanged == 2 and files[1].stat().st_mtime_ns == before


def test_an_earlier_chapter_arriving_later_takes_the_cover(tmp_path):
    c2, c1 = cbz(tmp_path / "c2.cbz"), cbz(tmp_path / "c1.cbz")
    vc.sync([chapter(2, 1, c2)], COVERS, lambda url: JPG)
    assert names(c2)[0] == "000_cover.jpg"
    result = vc.sync([chapter(2, 1, c2), chapter(1, 1, c1)], COVERS, lambda url: JPG)
    assert result.added == [1] and result.removed == 1
    assert names(c1)[0] == "000_cover.jpg" and "000_cover.jpg" not in names(c2)


def test_a_changed_cover_replaces_the_old_one(tmp_path):
    c1 = cbz(tmp_path / "c1.cbz")
    vc.sync([chapter(1, 1, c1)], COVERS, lambda url: JPG)
    vc.sync([chapter(1, 1, c1)], {"1": {"id": 99, "url": "https://images.mangabaka.dev/n"}}, lambda url: JPG2)
    with zipfile.ZipFile(c1) as z:
        assert [n for n in z.namelist() if n.startswith("000_cover")] == ["000_cover.jpg"]
        assert z.read("000_cover.jpg") == JPG2


def test_volumes_without_an_english_cover_and_multi_volume_files_are_left_alone(tmp_path):
    single, multi = cbz(tmp_path / "v3.cbz"), cbz(tmp_path / "v1-2.cbz")
    before = {p: p.read_bytes() for p in (single, multi)}
    result = vc.sync([chapter(30, 3, single), chapter(1, 1, multi), chapter(9, 2, multi)],
                     COVERS, lambda url: JPG)
    assert result.missing == [3] and result.added == []
    assert all(p.read_bytes() == data for p, data in before.items())


def test_compressed_archives_and_non_images_are_handled(tmp_path):
    c1 = cbz(tmp_path / "c1.cbz", compress=zipfile.ZIP_DEFLATED)
    result = vc.sync([chapter(1, 1, c1)], COVERS, lambda url: JPG)
    with zipfile.ZipFile(c1) as z:
        assert z.testzip() is None and z.getinfo("001.png").compress_type == zipfile.ZIP_DEFLATED
    assert result.added == [1]
    c2 = cbz(tmp_path / "c2.cbz")
    bad = vc.sync([chapter(1, 1, c2)], COVERS, lambda url: b"<html>blocked</html>")
    assert bad.added == [] and bad.failed and "000_cover.jpg" not in names(c2)


def test_the_added_cover_is_not_a_chapter_page(tmp_path):
    c1 = cbz(tmp_path / "c1.cbz")
    vc.sync([chapter(1, 1, c1)], COVERS, lambda url: JPG)
    # duplicate-chapter detection compares pages only
    assert archive_page_signature(c1) == page_bytes_signature(PAGES)


def test_covers_only_come_from_mangabakas_image_host():
    for url in ("http://images.mangabaka.dev/a", "https://evil.example/a",
                "https://mangabaka.dev.evil.example/a"):
        with pytest.raises(ValueError):
            vc.fetch_cover(url)


@pytest.fixture
def settings(monkeypatch):
    values = {"english_volume_covers": "true", "mangabaka_metadata": "true"}

    async def get(_session, key):
        return values[key]
    monkeypatch.setattr(tasks.settings_service, "get", get)
    monkeypatch.setattr(tasks, "_notify_kavita", lambda *a: None)
    return values


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        pass


async def test_apply_is_off_by_default_and_looks_covers_up_once(tmp_path, monkeypatch, settings):
    assert tasks.settings_service.DEFAULTS["english_volume_covers"] == "false"
    c1 = cbz(tmp_path / "c1.cbz")
    series = Series(id=1, title="X", anilist_id=5, mangaupdates_id=None,
                    comicinfo_extra=json.dumps({"publishers": ["Shueisha"]}))
    series.chapters = [Chapter(id=1, number=1, volume=1, downloaded=True, file_path=str(c1), excluded=False)]
    assert await tasks.apply_volume_covers(FakeSession(), series, {"english_volume_covers": "false"}) is None

    lookups = []

    async def lookup(anilist_id, mangaupdates_id):
        lookups.append(anilist_id)
        return tasks.ComicExtras(mangabaka_id=7, writers=["W"])

    async def english_volume_covers(mangabaka_id):
        return {"1": {"id": 11, "url": "https://images.mangabaka.dev/a"}}
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    monkeypatch.setattr(tasks.mangabaka, "english_volume_covers", english_volume_covers)
    monkeypatch.setattr(vc, "fetch_cover", lambda url: JPG)
    session = FakeSession()
    result = await tasks.apply_volume_covers(session, series, {"english_volume_covers": "true"})
    assert result.added == [1] and names(c1)[0] == "000_cover.jpg"
    stored = json.loads(series.comicinfo_extra)
    assert stored["publishers"] == ["Shueisha"]  # the MangaUpdates publishers are kept
    assert stored["volume_covers_checked"] and stored["writers"] == ["W"]
    assert [e.event for e in session.added] == ["volume_covers"]
    # later passes use the stored list
    await tasks.apply_volume_covers(FakeSession(), series, {"english_volume_covers": "true"})
    assert lookups == [5]


async def test_covers_without_mangabaka_metadata_store_only_the_covers(monkeypatch, settings):
    settings["mangabaka_metadata"] = "false"

    async def lookup(*_):
        return tasks.ComicExtras(mangabaka_id=7, writers=["W"], tags=["T"])

    async def english_volume_covers(mangabaka_id):
        return {}
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    monkeypatch.setattr(tasks.mangabaka, "english_volume_covers", english_volume_covers)
    series = Series(id=1, title="X", anilist_id=5)
    await tasks.refresh_comicinfo_extra(None, series, None)
    stored = json.loads(series.comicinfo_extra)
    assert stored["writers"] == [] and stored["tags"] == [] and stored["volume_covers_checked"]
