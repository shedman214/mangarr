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
