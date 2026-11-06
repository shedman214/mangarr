"""Remove → "Delete files and folders on disk": off by default; when on, only
the series' own folders go — never a root folder, anything outside the roots,
a symlink, a shared folder, or a folder another series uses."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import series as series_api
from mangarr.models import Base, RootFolder, Series, SeriesFolder
from mangarr.schemas import SeriesBulkIn, SeriesDeleteIn


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def folder(path, files=("Ch. 0001.cbz",)):
    path.mkdir(parents=True, exist_ok=True)
    for name in files:
        (path / name).write_bytes(b"x")
    return path


async def add(session, root, title, folder_name=None, extras=()):
    series = Series(title=title, sort_title=title.lower(), root_folder=root, folder_name=folder_name or title)
    series.extra_folders = [SeriesFolder(path=p, volume_offset=o) for p, o in extras]
    session.add(series)
    await session.commit()
    return series


async def titles(session):
    return (await session.execute(select(Series.title))).scalars().all()


async def test_files_are_kept_unless_asked(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    one = folder(tmp_path / "manga" / "Alpha")
    series = await add(session, root, "Alpha")
    out = await series_api.delete_series(series.id, session)
    assert out.deleted == [] and one.exists() and await titles(session) == []


async def test_deletes_the_series_own_folders_with_preview(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    primary = folder(tmp_path / "manga" / "Alpha")
    extra = folder(tmp_path / "manga" / "Alpha Extras")
    series = await add(session, root, "Alpha", extras=[("Alpha Extras", None)])
    keep = folder(tmp_path / "manga" / "Beta")
    await add(session, root, "Beta")

    preview = await series_api.delete_series_preview(series.id, session)
    assert preview.folders == [str(primary), str(extra)] and preview.refused == []
    assert primary.exists()  # a preview changes nothing

    out = await series_api.delete_series(series.id, session, delete_files=True)
    assert sorted(out.deleted) == sorted([str(primary), str(extra)]) and out.problems == []
    assert not primary.exists() and not extra.exists() and keep.exists()
    assert await titles(session) == ["Beta"]


async def test_root_outside_symlink_shared_and_used_folders_are_refused(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    root_dir = folder(tmp_path / "manga")
    outside = folder(tmp_path / "elsewhere" / "Gamma")
    shared = folder(tmp_path / "manga" / "Shared Volumes")
    used = folder(tmp_path / "manga" / "Delta")
    real = folder(tmp_path / "manga" / "Real")
    (tmp_path / "manga" / "Link").symlink_to(real)
    doomed = await add(session, root, "Gamma", folder_name="",
                       extras=[(str(outside), None), ("Shared Volumes", 0), ("Delta", None), ("Link", None)])
    doomed.folder_name = "."  # resolves to the root itself
    await session.commit()
    await add(session, root, "Delta")

    preview = await series_api.delete_series_preview(doomed.id, session)
    reasons = {p: r for p, r in ((x.path, x.reason) for x in preview.refused)}
    assert preview.folders == []
    assert reasons[str(root_dir / ".")] == "it is a root folder"
    assert reasons[str(outside)] == "it is outside the root folders"
    assert reasons[str(shared)] == "it is a shared folder"
    assert reasons[str(used)] == "“Delta” also uses it"
    assert reasons[str(tmp_path / "manga" / "Link")] == "it is a symbolic link"

    out = await series_api.delete_series(doomed.id, session, delete_files=True)
    assert out.deleted == [] and len(out.refused) == 5
    assert all(p.exists() for p in (root_dir, outside, shared, used, real))


async def test_bulk_remove_with_files(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    a, b = folder(tmp_path / "manga" / "Alpha"), folder(tmp_path / "manga" / "Beta")
    sa, sb = await add(session, root, "Alpha"), await add(session, root, "Beta")
    preview = await series_api.delete_series_bulk_preview(SeriesBulkIn(series_ids=[sa.id, sb.id]), session)
    assert sorted(preview.folders) == sorted([str(a), str(b)])
    out = await series_api.delete_series_bulk(SeriesDeleteIn(series_ids=[sa.id, sb.id], delete_files=True), session)
    assert out.count == 2 and not a.exists() and not b.exists()


async def test_bulk_never_deletes_a_folder_refused_for_another_series(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    shared = folder(tmp_path / "manga" / "Omnibus")
    # Alpha reads Omnibus as a shared volume folder; Omnibus is Beta's own primary folder
    sa = await add(session, root, "Alpha", extras=[("Omnibus", 0)])
    sb = await add(session, root, "Omnibus")
    out = await series_api.delete_series_bulk(SeriesDeleteIn(series_ids=[sa.id, sb.id], delete_files=True), session)
    assert str(shared) not in out.deleted and shared.exists()
    assert [r.path for r in out.refused].count(str(shared)) == 1 and out.problems == []


async def test_nested_folders_are_deleted_once_through_their_parent(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    parent = folder(tmp_path / "manga" / "Epsilon")
    child = folder(tmp_path / "manga" / "Epsilon" / "Specials")
    series = await add(session, root, "Epsilon", extras=[("Epsilon/Specials", None)])
    preview = await series_api.delete_series_preview(series.id, session)
    assert preview.folders == [str(parent)]
    out = await series_api.delete_series(series.id, session, delete_files=True)
    assert out.deleted == [str(parent)] and out.problems == [] and not child.exists()


async def test_a_folder_reached_through_a_symlinked_parent_is_one_folder(session, tmp_path):
    root = RootFolder(path=str(tmp_path / "manga"))
    real = folder(tmp_path / "manga" / "Zeta")
    (tmp_path / "manga" / "alias").symlink_to(tmp_path / "manga")
    series = await add(session, root, "Zeta", extras=[("alias/Zeta", None)])
    out = await series_api.delete_series(series.id, session, delete_files=True)
    assert out.deleted == [str(real)] and out.problems == [] and not real.exists()
