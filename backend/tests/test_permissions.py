import json
import os
import stat
from pathlib import Path

import pytest

from mangarr import permissions as p
from mangarr.download.cbz import write_cbz
from mangarr.library.importer import place_file


@pytest.fixture(autouse=True)
def reset_policy(monkeypatch):
    monkeypatch.setattr(p, "_policy", p.Policy())


def test_modes_and_ids_are_validated():
    policy = p.parse({"permissions_enabled": "true", "permissions_folder_mode": "775", "permissions_user": "99", "permissions_group": "100"})
    assert policy.file_mode == 0o664
    assert (policy.uid, policy.gid) == (99, 100)
    assert p.parse({"permissions_user": "", "permissions_group": ""}).uid == -1
    for mode in ("0755", "644", "999", "7777", "bad"):
        with pytest.raises(ValueError):
            p.parse({"permissions_folder_mode": mode})
    for value in ("-2", "4294967295", "bad/name"):
        with pytest.raises(ValueError):
            p.resolve_id(value, "user")


def test_only_created_directories_are_applied(tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(p, "apply_created", lambda path, **kw: applied.append(path))
    parent = tmp_path / "existing"
    parent.mkdir()
    leaf = parent / "new" / "nested"
    p.mkdir_created(leaf)
    assert applied == [parent / "new", leaf]
    p.mkdir_created(leaf)
    assert len(applied) == 2


def test_directory_creation_rejects_an_existing_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("preserve")
    with pytest.raises(FileExistsError):
        p.mkdir_created(path)
    assert path.read_text() == "preserve"


linux = pytest.mark.skipif(not hasattr(os, "fchown"), reason="Unix ownership and file modes")


def enable(mode=0o755):
    p._policy = p.Policy(True, mode, os.getuid(), os.getgid())


@linux
def test_new_cbz_and_new_folder_permissions_leave_existing_directories_alone(tmp_path):
    enable()
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o700)
    dest = existing / "new" / "chapter.cbz"
    write_cbz(dest, [b"\xff\xd8\xffimage"], "<ComicInfo/>")
    assert stat.S_IMODE(existing.stat().st_mode) == 0o700
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644


@linux
def test_shared_hardlink_is_never_chmodded(tmp_path):
    enable()
    source = tmp_path / "seed.cbz"
    source.write_bytes(b"archive")
    source.chmod(0o600)
    dest = tmp_path / "library.cbz"
    place_file(source, dest, "hardlink")
    p.apply_created(dest)
    assert source.stat().st_ino == dest.stat().st_ino
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert p.preview([(str(dest), str(tmp_path))], tmp_path / "config")["skipped"] == 1


@linux
def test_copy_import_applies_policy_without_changing_source(tmp_path):
    enable()
    source = tmp_path / "seed.cbz"
    source.write_bytes(b"archive")
    source.chmod(0o600)
    dest = tmp_path / "copy.cbz"
    place_file(source, dest, "copy")
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644


@linux
def test_preview_fix_undo_and_replaced_file_guard(tmp_path):
    enable()
    root = tmp_path / "library"
    root.mkdir()
    file = root / "chapter.cbz"
    file.write_bytes(b"original archive")
    file.chmod(0o600)
    data = tmp_path / "config"
    preview = p.preview([(str(file), str(root))], data)
    assert preview["count"] == 1
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    result = p.repair(data, preview["preview_id"])
    assert (result["changed"], result["error_count"]) == (1, 0)
    assert stat.S_IMODE(file.stat().st_mode) == 0o644
    assert p.repair(data, result["undo_id"], undo=True)["changed"] == 1
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    file.write_bytes(b"changed since preview")
    assert p.repair(data, preview["preview_id"])["skipped"] == 1
    assert stat.S_IMODE(file.stat().st_mode) == 0o600


@linux
def test_symlinks_parent_links_and_traversal_are_excluded(tmp_path):
    enable()
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "private.cbz"
    secret.write_bytes(b"do not change")
    secret.chmod(0o600)
    (root / "leaf.cbz").symlink_to(secret)
    (root / "alias").symlink_to(outside, target_is_directory=True)
    inputs = [(str(root / "leaf.cbz"), str(root)), (str(root / "alias" / "private.cbz"), str(root)), (str(root / ".." / "outside" / "private.cbz"), str(root))]
    preview = p.preview(inputs, tmp_path / "config")
    assert preview["count"] == 0 and preview["skipped"] == 3
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


@linux
def test_changed_policy_and_expired_preview_require_a_fresh_preview(tmp_path):
    enable()
    file = tmp_path / "chapter.cbz"
    file.write_bytes(b"archive")
    file.chmod(0o600)
    data = tmp_path / "config"
    preview = p.preview([(str(file), str(tmp_path))], data)
    p._policy = p.Policy(True, 0o775, os.getuid(), os.getgid())
    with pytest.raises(ValueError, match="preview again"):
        p.repair(data, preview["preview_id"])
    enable()
    path = p._store_path(data, preview["preview_id"], "preview")
    record = json.loads(path.read_text())
    record["created"] = 0
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="expired"):
        p.repair(data, preview["preview_id"])

@linux
def test_large_preview_streams_all_results_with_bounded_examples(tmp_path, monkeypatch):
    enable()
    sample = tmp_path / 'sample.cbz'
    sample.write_bytes(b'archive')
    sample.chmod(0o600)
    fd = os.open(sample, os.O_RDONLY)
    monkeypatch.setattr(p._PreviewStats, 'stat', lambda *args, **kwargs: os.fstat(fd))
    progress = []
    try:
        result = p.preview(((str(tmp_path / f'{n}.cbz'), str(tmp_path)) for n in range(50002)),
                           tmp_path / 'config', progress=lambda value: progress.append(value['processed']) if value['processed'] in (1, 50002) else None)
    finally:
        os.close(fd)
    assert result['count'] == result['scanned'] == 50002
    assert len(result['examples']) == 50 and progress == [1, 50002]
    manifest = p._store_path(tmp_path / 'config', result['preview_id'], 'preview')
    assert 'entries' not in json.loads(manifest.read_text())
    with manifest.with_suffix('.jsonl').open() as handle:
        assert sum(1 for _ in handle) == 50002


@linux
def test_legacy_preview_remains_repairable(tmp_path):
    enable()
    file = tmp_path / 'chapter.cbz'
    file.write_bytes(b'archive')
    file.chmod(0o600)
    result = p.preview([(str(file), str(tmp_path))], tmp_path / 'config')
    manifest = p._store_path(tmp_path / 'config', result['preview_id'], 'preview')
    record = json.loads(manifest.read_text())
    record.pop('version')
    record['entries'] = [json.loads(line) for line in manifest.with_suffix('.jsonl').read_text().splitlines()]
    manifest.write_text(json.dumps(record))
    assert p.repair(tmp_path / 'config', result['preview_id'])['changed'] == 1

@linux
def test_preview_folder_cache_never_follows_a_replacement_symlink(tmp_path):
    root = tmp_path / 'root'
    folder = root / 'series'
    folder.mkdir(parents=True)
    original = folder / 'chapter.cbz'
    original.write_bytes(b'original')
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'chapter.cbz'
    target.write_bytes(b'unrelated')
    expected = original.stat().st_ino
    cache = p._PreviewStats()
    try:
        assert cache.stat(original, root).st_ino == expected
        folder.rename(root / 'moved')
        folder.symlink_to(outside, target_is_directory=True)
        assert cache.stat(original, root).st_ino == expected
        with pytest.raises(OSError):
            p._open_managed(original, root)
    finally:
        cache.close()
    assert cache.folders == {}
