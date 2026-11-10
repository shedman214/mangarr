"""Wanted → search by name: filters all wanted chapters on the server by series title or any
alternative title, keeps the total right, and treats % and _ as plain characters."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangarr.api import system as system_api
from mangarr.models import Base, Chapter, Series


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def series(title, alt="", chapters=2, monitored=True):
    s = Series(title=title, sort_title=title.lower(), alt_titles=alt, monitored=monitored)
    s.chapters = [Chapter(number=float(i + 1), monitored=True) for i in range(chapters)]
    return s


async def test_search_by_title_and_alt_title(session):
    session.add_all([series("Alpha Quest", chapters=3), series("Beta Road", alt="Alpha Saga\nOther"),
                     series("Gamma", chapters=1), series("Alpha Hidden", monitored=False)])
    await session.commit()
    everything = await system_api.wanted_page(limit=100, offset=0, q="", session=session)
    assert everything["total"] == 6  # the unmonitored series is never wanted
    hits = await system_api.wanted_page(limit=100, offset=0, q="  alpha ", session=session)
    assert hits["total"] == 5 and {i.series_title for i in hits["items"]} == {"Alpha Quest", "Beta Road"}
    page2 = await system_api.wanted_page(limit=2, offset=2, q="alpha", session=session)
    assert page2["total"] == 5 and len(page2["items"]) == 2


async def test_wildcards_are_plain_text(session):
    session.add_all([series("100% Real"), series("1000 Real")])
    await session.commit()
    hits = await system_api.wanted_page(limit=100, offset=0, q="100%", session=session)
    assert {i.series_title for i in hits["items"]} == {"100% Real"}
    none = await system_api.wanted_page(limit=100, offset=0, q="_", session=session)
    assert none["total"] == 0
