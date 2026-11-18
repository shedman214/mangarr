"""New series and recommendations outside the Mangarr library."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import kavita, settings_service
from ..adding import LibraryIndex, title_keys
from ..db import get_session
from ..metadata.anilist import provider as anilist
from ..models import Series
from ..util import normalize_title

router = APIRouter(prefix="/discovery", tags=["discovery"])

RESULT_LIMIT = 12
SEED_LIMIT = 4
UPSTREAM_ERRORS = (httpx.HTTPError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError)


class DiscoveryTitle(BaseModel):
    provider: Literal["anilist"] = "anilist"
    provider_id: str
    title: str
    english_title: str
    alt_titles: list[str]
    cover_url: str
    release_date: date | None = None
    read_title: str | None = None


class DiscoveryRecommendations(BaseModel):
    items: list[DiscoveryTitle]
    state: Literal["ready", "needs_kavita", "no_history", "no_matches"] = "ready"


def _candidate(
    media: dict, index: LibraryIndex, adult_content: bool,
) -> DiscoveryTitle | None:
    if media.get("type") != "MANGA" or media.get("format") == "NOVEL":
        return None
    if not adult_content and media.get("isAdult") is not False:
        return None
    titles = media.get("title") or {}
    if not any(titles.values()):
        return None
    meta = anilist.node_to_metadata(media)
    if index.find(anilist_id=int(meta.provider_id), titles=[meta.title, *meta.alt_titles]) is not None:
        return None
    return DiscoveryTitle(
        provider_id=meta.provider_id, title=meta.title,
        english_title=titles.get("english") or "", alt_titles=meta.alt_titles,
        cover_url=meta.cover_url,
    )


def _start_date(media: dict) -> date | None:
    start = media.get("startDate") or {}
    try:
        return date(start.get("year"), start.get("month"), start.get("day"))
    except (ValueError, TypeError):
        return None


def _fuzzy_date(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


@router.get("/releases", response_model=list[DiscoveryTitle])
async def releases(session: AsyncSession = Depends(get_session)):
    adult = await settings_service.get(session, "adult_content") == "true"
    first = datetime.now(timezone.utc).date().replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    before = _fuzzy_date(next_month)
    after = _fuzzy_date(first - timedelta(days=1))
    index = await LibraryIndex.load(session)
    items: dict[str, DiscoveryTitle] = {}
    try:
        async with asyncio.timeout(20):
            for page in (1, 2):
                nodes, more = await anilist.new_releases(after, before, adult, page)
                for media in nodes:
                    if not media or media.get("status") == "CANCELLED":
                        continue
                    release_date = _start_date(media)
                    if not release_date or not first <= release_date < next_month:
                        continue
                    if item := _candidate(media, index, adult):
                        item.release_date = release_date
                        items.setdefault(item.provider_id, item)
                if len(items) >= RESULT_LIMIT or not more:
                    break
    except UPSTREAM_ERRORS:
        if not items:
            raise HTTPException(502, "AniList new releases are temporarily unavailable.") from None
    return sorted(list(items.values())[:RESULT_LIMIT], key=lambda item: item.release_date, reverse=True)


async def _reading_seeds(
    names: dict[str, str], index: LibraryIndex, session: AsyncSession,
) -> dict[int, str]:
    seeds: dict[int, str] = {}
    unresolved = []
    for key, name in names.items():
        series_id = index.find(titles=[name])
        series = await session.get(Series, series_id) if series_id is not None else None
        if series and series.anilist_id:
            seeds.setdefault(series.anilist_id, name)
        else:
            unresolved.append((key, name))
        if len(seeds) == SEED_LIMIT:
            return seeds

    # Search only a few unmatched titles and require a unique exact alias.
    # Kavita's username, key and reading sessions stay on the local server.
    for key, name in unresolved[:SEED_LIMIT]:
        results = await anilist.search(name, limit=8)
        ids = {
            int(item.provider_id) for item in results
            if key in title_keys([item.title, *item.alt_titles])
        }
        if len(ids) == 1:
            seeds.setdefault(ids.pop(), name)
        if len(seeds) == SEED_LIMIT:
            break
    return seeds


@router.get("/recommendations", response_model=DiscoveryRecommendations)
async def recommendations(session: AsyncSession = Depends(get_session)):
    values = await settings_service.get_all(session)
    if not kavita.is_configured(values):
        return DiscoveryRecommendations(items=[], state="needs_kavita")
    client = kavita.KavitaClient(values["kavita_url"].strip(), values["kavita_api_key"].strip())
    try:
        async with asyncio.timeout(15):
            history = await client.recently_read_titles()
    except (kavita.KavitaError, TimeoutError):
        raise HTTPException(502, "Could not read Kavita activity. Check the connection in Settings.") from None
    finally:
        await client.close()

    names: dict[str, str] = {}
    for name in history:
        if key := normalize_title(name):
            names.setdefault(key, name)
    if not names:
        return DiscoveryRecommendations(items=[], state="no_history")

    index = await LibraryIndex.load(session)
    adult = values.get("adult_content") == "true"
    try:
        async with asyncio.timeout(25):
            seeds = await _reading_seeds(names, index, session)
            if not seeds:
                return DiscoveryRecommendations(items=[], state="no_matches")
            results = await anilist.discovery_recommendations(list(seeds))
        candidates: dict[str, tuple[int, DiscoveryTitle]] = {}
        for seed_id, seed_title in seeds.items():
            for row in results.get(seed_id, ()):
                rating = row.get("rating") if row else None
                media = (row.get("mediaRecommendation") or {}) if row else {}
                if not rating or rating <= 0 or media.get("id") in seeds:
                    continue
                item = _candidate(media, index, adult)
                if not item or names.keys() & title_keys([item.title, *item.alt_titles]):
                    continue
                existing = candidates.get(item.provider_id)
                if existing is None or rating > existing[0]:
                    item.read_title = seed_title
                    candidates[item.provider_id] = rating, item
    except UPSTREAM_ERRORS:
        raise HTTPException(502, "AniList recommendations are temporarily unavailable.") from None
    ranked = sorted(candidates.values(), key=lambda result: result[0], reverse=True)
    return DiscoveryRecommendations(items=[item for _, item in ranked[:RESULT_LIMIT]])
