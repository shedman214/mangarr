"""New series and recommendations outside the Mangarr library."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import kavita, settings_service
from ..adding import LibraryIndex, title_keys
from ..db import get_session
from ..metadata.anilist import provider as anilist
from ..models import DiscoveryHidden, Series
from ..util import normalize_title

router = APIRouter(prefix="/discovery", tags=["discovery"])

RESULT_LIMIT = 60
CATALOG_PAGE_LIMIT = 3
SEED_LIMIT = 12
SEED_LOOKUP_LIMIT = 4
# at most this many of the top picks come from one read before any repeats, so one
# series with many recommendations can't fill the whole list
PER_SEED_FIRST = 5
UPSTREAM_ERRORS = (httpx.HTTPError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError)


class DiscoveryTitle(BaseModel):
    provider: Literal["anilist"] = "anilist"
    provider_id: str
    title: str
    english_title: str
    alt_titles: list[str]
    cover_url: str
    year: int | None = None
    status: str
    genres: list[str]
    description: str
    # AniList format (MANGA, ONE_SHOT, …) and country of origin (JP, KR, CN, TW) for the type filter
    format: str = ""
    country: str = ""
    release_date: date | None = None
    # why a pick was suggested: the Kavita series it comes from, how far it was read
    # ("finished" / "read 45%") and its star rating when rated
    read_title: str | None = None
    read_detail: str | None = None
    read_rating: float | None = None


class DiscoveryRecommendations(BaseModel):
    items: list[DiscoveryTitle]
    state: Literal["ready", "needs_kavita", "no_history", "no_matches"] = "ready"


class HiddenIn(BaseModel):
    provider_id: str = Field(pattern=r"^[0-9]{1,10}$")
    title: str = Field(default="", max_length=300)


class HiddenOut(BaseModel):
    provider_id: str
    title: str
    hidden_at: datetime


async def _hidden_ids(session: AsyncSession) -> set[int]:
    return set((await session.execute(select(DiscoveryHidden.anilist_id))).scalars())


def _candidate(
    media: dict, index: LibraryIndex, adult_content: bool, hidden: set[int] = frozenset(),
) -> DiscoveryTitle | None:
    if media.get("type") != "MANGA" or media.get("format") == "NOVEL":
        return None
    if not adult_content and media.get("isAdult") is not False:
        return None
    if media.get("id") in hidden:
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
        cover_url=meta.cover_url, year=meta.year, status=meta.status,
        genres=meta.genres, description=meta.description,
        format=media.get("format") or "", country=media.get("countryOfOrigin") or "",
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
    hidden = await _hidden_ids(session)
    items: dict[str, DiscoveryTitle] = {}
    try:
        async with asyncio.timeout(20):
            for page in range(1, CATALOG_PAGE_LIMIT + 1):
                nodes, more = await anilist.new_releases(after, before, adult, page)
                for media in nodes:
                    if not media or media.get("status") == "CANCELLED":
                        continue
                    release_date = _start_date(media)
                    if not release_date or not first <= release_date < next_month:
                        continue
                    if item := _candidate(media, index, adult, hidden):
                        item.release_date = release_date
                        items.setdefault(item.provider_id, item)
                if len(items) >= RESULT_LIMIT or not more:
                    break
    except UPSTREAM_ERRORS:
        if not items:
            raise HTTPException(502, "AniList new releases are temporarily unavailable.") from None
    return sorted(list(items.values())[:RESULT_LIMIT], key=lambda item: item.release_date, reverse=True)


def seed_weight(entry: kavita.ReadEntry) -> float:
    """How much a read series counts: finished > well into it > just started (progress unknown
    counts as half-read); a 4–5★ rating adds weight. 0 = not used (rated 1–2★, or not read)."""
    if entry.rated and 0 < entry.rating <= 2:
        return 0.0
    if entry.finished:
        weight = 1.0
    elif entry.pages:
        weight = 0.3 + 0.7 * entry.progress if entry.pages_read else 0.0
    else:
        weight = 0.65
    if weight and entry.rated and entry.rating >= 4:
        weight += 0.5
    return weight


def read_detail(entry: kavita.ReadEntry) -> str | None:
    if entry.finished:
        return "finished"
    if entry.pages and entry.pages_read:
        return f"read {max(1, round(entry.progress * 100))}%"
    return None


async def _reading_seeds(
    reads: dict[str, kavita.ReadEntry], index: LibraryIndex, session: AsyncSession,
) -> dict[int, kavita.ReadEntry]:
    """AniList ids for the most recent reads (skipping disliked ones): Kavita's own AniList
    link first, then the library's, then a unique exact title match on AniList for a few
    of the rest. How strongly each counts is seed_weight's job, not the choice of seeds."""
    seeds: dict[int, kavita.ReadEntry] = {}
    unresolved = []
    for key, entry in reads.items():  # most recently read first
        if not seed_weight(entry):
            continue
        anilist_id = entry.anilist_id
        if anilist_id is None:
            series_id = index.find(titles=[entry.name])
            series = await session.get(Series, series_id) if series_id is not None else None
            anilist_id = series.anilist_id if series else None
        if anilist_id:
            seeds.setdefault(anilist_id, entry)
        else:
            unresolved.append((key, entry))
        if len(seeds) == SEED_LIMIT:
            return seeds

    # Search only a few unmatched titles and require a unique exact alias.
    # Kavita's username, key and reading sessions stay on the local server.
    for key, entry in unresolved[:SEED_LOOKUP_LIMIT]:
        results = await anilist.search(entry.name, limit=8)
        ids = {
            int(item.provider_id) for item in results
            if key in title_keys([item.title, *item.alt_titles])
        }
        if len(ids) == 1:
            seeds.setdefault(ids.pop(), entry)
        if len(seeds) == SEED_LIMIT:
            break
    return seeds


async def _kavita_reads(client: kavita.KavitaClient) -> list[kavita.ReadEntry]:
    """Progress and ratings when this Kavita lists them; older servers fall back to titles."""
    try:
        if reads := await client.reading_profile():
            return reads
    except kavita.KavitaError:
        pass
    return [kavita.ReadEntry(name=name) for name in await client.recently_read_titles()]


@router.get("/recommendations", response_model=DiscoveryRecommendations)
async def recommendations(session: AsyncSession = Depends(get_session)):
    values = await settings_service.get_all(session)
    if not kavita.is_configured(values):
        return DiscoveryRecommendations(items=[], state="needs_kavita")
    client = kavita.KavitaClient(values["kavita_url"].strip(), values["kavita_api_key"].strip())
    try:
        async with asyncio.timeout(15):
            history = await _kavita_reads(client)
    except (kavita.KavitaError, TimeoutError):
        raise HTTPException(502, "Could not read Kavita activity. Check the connection in Settings.") from None
    finally:
        await client.close()

    reads: dict[str, kavita.ReadEntry] = {}
    for entry in history:
        if key := normalize_title(entry.name):
            reads.setdefault(key, entry)  # most recent first
    if not reads:
        return DiscoveryRecommendations(items=[], state="no_history")

    index = await LibraryIndex.load(session)
    hidden = await _hidden_ids(session)
    adult = values.get("adult_content") == "true"
    # provider id -> (score, strongest contribution, item); a title several reads point to
    # scores the sum of their weighted AniList votes
    candidates: dict[str, tuple[float, float, DiscoveryTitle]] = {}
    try:
        async with asyncio.timeout(25):
            seeds = await _reading_seeds(reads, index, session)
            if not seeds:
                return DiscoveryRecommendations(items=[], state="no_matches")
            for page in range(1, CATALOG_PAGE_LIMIT + 1):
                results, more = await anilist.discovery_recommendations(list(seeds), page)
                for seed_id, entry in seeds.items():
                    weight = seed_weight(entry)
                    for row in results.get(seed_id, ()):
                        rating = row.get("rating") if row else None
                        media = (row.get("mediaRecommendation") or {}) if row else {}
                        if not rating or rating <= 0 or media.get("id") in seeds:
                            continue
                        item = _candidate(media, index, adult, hidden)
                        if not item or reads.keys() & title_keys([item.title, *item.alt_titles]):
                            continue
                        vote = rating * weight
                        score, best, current = candidates.get(item.provider_id, (0.0, 0.0, item))
                        if vote > best:
                            best, current = vote, item
                            current.read_title = entry.name
                            current.read_detail = read_detail(entry)
                            current.read_rating = entry.rating if entry.rated and entry.rating else None
                        candidates[item.provider_id] = score + vote, best, current
                if len(candidates) >= RESULT_LIMIT or not more:
                    break
    except UPSTREAM_ERRORS:
        if not candidates:
            raise HTTPException(502, "AniList recommendations are temporarily unavailable.") from None
    ranked = [item for _, _, item in sorted(candidates.values(), key=lambda result: result[0], reverse=True)]
    per_seed: dict[str | None, int] = {}
    varied, repeats = [], []
    for item in ranked:
        count = per_seed.get(item.read_title, 0)
        (varied if count < PER_SEED_FIRST else repeats).append(item)
        per_seed[item.read_title] = count + 1
    return DiscoveryRecommendations(items=(varied + repeats)[:RESULT_LIMIT])


@router.get("/hidden", response_model=list[HiddenOut])
async def hidden_titles(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(DiscoveryHidden).order_by(DiscoveryHidden.hidden_at.desc())
    )).scalars()
    return [HiddenOut(provider_id=str(r.anilist_id), title=r.title, hidden_at=r.hidden_at) for r in rows]


@router.post("/hidden", status_code=204)
async def hide_title(body: HiddenIn, session: AsyncSession = Depends(get_session)):
    """Discovery's "Not interested": the title is never suggested again until unhidden."""
    anilist_id = int(body.provider_id)
    row = await session.get(DiscoveryHidden, anilist_id)
    if row is None:
        session.add(DiscoveryHidden(anilist_id=anilist_id, title=body.title.strip()))
        await session.commit()
    return Response(status_code=204)


@router.delete("/hidden/{provider_id}", status_code=204)
async def unhide_title(provider_id: str, session: AsyncSession = Depends(get_session)):
    if not provider_id.isdigit() or len(provider_id) > 10:
        raise HTTPException(422, "Invalid AniList id")
    row = await session.get(DiscoveryHidden, int(provider_id))
    if row is not None:
        await session.delete(row)
        await session.commit()
    return Response(status_code=204)
