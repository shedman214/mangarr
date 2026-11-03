import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import settings
from mangarr.models import Base, Chapter, RootFolder, Series


async def test_permission_preview_selects_only_managed_downloaded_nonexcluded_files(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    captured = []
    def preview(candidates, data_dir):
        captured.extend(candidates)
        return {"count": len(candidates)}
    monkeypatch.setattr(settings.permissions, "preview", preview)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        series = Series(title="Example", root_folder=RootFolder(path=str(tmp_path)))
        for number, source, downloaded, excluded in [(1, "toonily", True, False), (2, "", True, False), (3, "toonily", False, False), (4, "toonily", True, True)]:
            series.chapters.append(Chapter(number=number, file_path=str(tmp_path / f"{number}.cbz"), file_source=source, downloaded=downloaded, excluded=excluded))
        session.add(series)
        await session.commit()
        assert await settings.permission_preview(session) == {"count": 1}
    await engine.dispose()
    assert captured == [(str(tmp_path / "1.cbz"), str(tmp_path))]


async def test_permission_preview_includes_the_entire_large_library(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    captured = []
    def preview(candidates, data_dir):
        captured.extend(candidates)
        return {"count": len(candidates)}
    monkeypatch.setattr(settings.permissions, "preview", preview)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        series = Series(title="Large library", root_folder=RootFolder(path=str(tmp_path)))
        session.add(series)
        await session.flush()
        # Multiple chapter records can refer to one archive. Scan it once,
        # including the last archive beyond the former 50,000-file cutoff.
        from sqlalchemy import insert
        rows = [{"series_id": series.id, "number": n, "file_path": str(tmp_path / f"{n}.cbz"),
                 "file_source": "toonily", "downloaded": True, "excluded": False}
                for n in range(50002)]
        await session.execute(insert(Chapter), rows)
        await session.execute(insert(Chapter), [{**rows[-1], "number": 60000}])
        await session.commit()
        assert await settings.permission_preview(session) == {"count": 50002}
    await engine.dispose()
    assert len(captured) == len(set(captured)) == 50002
    assert (str(tmp_path / "50001.cbz"), str(tmp_path)) in captured
