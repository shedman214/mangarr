"""MangaBaka (api.mangabaka.org): the extra series metadata Kavita reads from
ComicInfo.xml — writers, artists, publishers, tags, age rating, its page as a
web link — and a third place to add series from. Add New Series lists the
MangaBaka entries MangaUpdates and AniList don't have (western comics and
webtoons such as Dungeon Crawler Carl); series added that way take their
titles, status and totals from MangaBaka.

Series are looked up by the AniList, MangaUpdates or MangaBaka id mangarr
already has, so there is no title guessing. The API is free for personal,
non-commercial use with credit to MangaBaka, which ComicInfo's Notes carries.
"""

from dataclasses import asdict, dataclass, field

import httpx

from .. import USER_AGENT
from ..util import RateLimiter, rl_request
from .base import SeriesMetadata

API_URL = "https://api.mangabaka.org/v1"

# the API allows 180 requests/minute per IP for uncached lookups
_limiter = RateLimiter(rate=2, per_seconds=1)

# MangaBaka tag weights, strongest first; incidental and unweighted tags are
# too many (Demon Slayer has 190) to be useful in Kavita
_TAG_WEIGHTS = ("core", "defining")
MAX_TAGS = 50

# ComicInfo AgeRating values Kavita understands; "safe" says nothing about age
AGE_RATINGS = {
    "suggestive": "Teen",
    "erotica": "Mature 17+",
    "pornographic": "Adults Only 18+",
}

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

# MangaBaka status → models.SeriesStatus value
_STATUSES = {
    "releasing": "releasing",
    "completed": "finished",
    "hiatus": "hiatus",
    "cancelled": "cancelled",
    "upcoming": "not_yet_released",
}
MAX_SEARCH = 50  # the search endpoint's largest page


def mangaupdates_slug(series_id: int) -> str:
    """MangaUpdates' numeric series id in the base-36 form MangaBaka keys on
    (36271319846 → "gnv09ie")."""
    if series_id < 0:
        raise ValueError(f"MangaUpdates ids are not negative: {series_id}")
    out = ""
    while series_id:
        series_id, digit = divmod(series_id, 36)
        out = _B36[digit] + out
    return out or "0"


@dataclass
class ComicExtras:
    """What mangarr stores per series (Series.comicinfo_extra, as JSON)."""

    mangabaka_id: int | None = None
    web: str = ""
    writers: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    age_rating: str = ""
    # English front covers by volume number ("3" → {"id": …, "url": …}); only
    # filled while Settings → English volume covers is on
    volume_covers: dict[str, dict] = field(default_factory=dict)
    volume_covers_checked: bool = False  # looked up, even if none were listed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ComicExtras":
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in names})


def _unique(values) -> list[str]:
    seen, out = set(), []
    for value in values:
        value = (value or "").strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


def publisher_names(publishers: list[dict]) -> list[str]:
    """Original publishers first, then English ones; other languages' are
    left out (Demon Slayer lists Panini México, Norma Editorial, …)."""
    rank = {"Original": 0, "English": 1}
    picked = sorted(
        (p for p in publishers or [] if p.get("type") in rank),
        key=lambda p: rank[p["type"]],
    )
    return _unique(p.get("name") or p.get("publisher_name") for p in picked)


def parse_series(record: dict) -> ComicExtras:
    tags = [
        t for t in record.get("tags_v2") or []
        if t.get("weight") in _TAG_WEIGHTS and not t.get("is_spoiler") and not t.get("is_genre")
    ]
    tags.sort(key=lambda t: _TAG_WEIGHTS.index(t["weight"]))  # stable: keeps MangaBaka's order
    return ComicExtras(
        mangabaka_id=record.get("id"),
        web=record.get("canonical_url") or "",
        writers=_unique(record.get("authors") or []),
        artists=_unique(record.get("artists") or []),
        publishers=publisher_names(record.get("publishers") or []),
        genres=_unique(g.replace("_", " ").title() for g in record.get("genres") or []),
        tags=_unique(t.get("name") for t in tags)[:MAX_TAGS],
        age_rating=AGE_RATINGS.get(record.get("content_rating") or "", ""),
    )


def _int(value) -> int | None:
    """MangaBaka sends totals as strings ("61"); keep whole positive numbers."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 and number.is_integer() else None


def to_metadata(record: dict) -> SeriesMetadata:
    """A MangaBaka series record as the provider-neutral SeriesMetadata."""
    title = record.get("title") or "Unknown"
    alt = [
        record.get("native_title"),
        record.get("romanized_title"),
        *((t or {}).get("title") for t in record.get("titles") or []),
        *(
            (t or {}).get("title")
            for group in (record.get("secondary_titles") or {}).values()
            for t in group or []
        ),
    ]
    year = record.get("year")
    return SeriesMetadata(
        provider="mangabaka",
        provider_id=str(record["id"]),
        title=title,
        alt_titles=[t for t in _unique(alt) if t.lower() != title.lower()],
        description=record.get("description") or "",
        status=_STATUSES.get(record.get("status") or "", "unknown"),
        year=year if isinstance(year, int) else None,
        cover_url=((record.get("cover") or {}).get("raw") or {}).get("url") or "",
        genres=_unique(g.replace("_", " ").title() for g in record.get("genres") or []),
        total_chapters=_int(record.get("total_chapters")),
        total_volumes=_int(record.get("final_volume")),
        publishers=publisher_names(record.get("publishers") or []),
    )


def linked_elsewhere(record: dict) -> bool:
    """Whether MangaUpdates or AniList knows this series too (then those are
    searched for it, so Add New Series doesn't list it twice)."""
    source = record.get("source") or {}
    return any(
        (source.get(name) or {}).get("id") not in (None, "")
        for name in ("manga_updates", "anilist")
    )


class MangaBakaClient:
    name = "mangabaka"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=30
        )

    async def _get(self, path: str) -> dict | None:
        resp = await rl_request(self._client, "GET", f"{API_URL}{path}", limiter=_limiter)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("data")

    async def _search_records(self, query: str, limit: int) -> list[dict]:
        resp = await rl_request(
            self._client, "GET", f"{API_URL}/series/search",
            params={"q": query, "limit": max(1, min(limit, MAX_SEARCH))}, limiter=_limiter,
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        return [r for r in data or [] if isinstance(r, dict) and r.get("id") is not None
                and r.get("state", "active") == "active"]

    async def search(self, query: str, limit: int = 20) -> list[SeriesMetadata]:
        return [to_metadata(r) for r in await self._search_records(query, limit)]

    async def search_unlinked(self, query: str, limit: int = 20) -> list[SeriesMetadata]:
        """Only the entries neither MangaUpdates nor AniList has."""
        records = await self._search_records(query, limit)
        return [to_metadata(r) for r in records if not linked_elsewhere(r)]

    async def get_series(self, provider_id: str) -> SeriesMetadata | None:
        record = await self._record(int(provider_id))
        return to_metadata(record) if record else None

    async def _record(self, mangabaka_id: int) -> dict | None:
        record = await self._get(f"/series/{int(mangabaka_id)}")
        if record and record.get("state") not in (None, "active") and record.get("merged_with"):
            # a merged entry points at the one that replaced it
            record = await self._get(f"/series/{int(record['merged_with'])}")
        return record

    async def _by_source(self, path: str) -> dict | None:
        data = await self._get(path)
        series = (data or {}).get("series") or []
        record = next((s for s in series if s.get("state") == "active"), None)
        if record is None:
            # a merged entry points at the one that replaced it
            merged = next((s.get("merged_with") for s in series if s.get("merged_with")), None)
            if merged:
                record = await self._get(f"/series/{int(merged)}")
        return record

    async def english_volume_covers(self, mangabaka_id: int) -> dict[str, dict]:
        """English front covers of whole-numbered volumes, the first one
        listed per volume (other editions' covers are skipped)."""
        covers: dict[str, dict] = {}
        for page in range(1, 21):  # 50 a page; 1,000 covers is far past any series
            data = await self._get(
                f"/series/{int(mangabaka_id)}/images?type=volume&language=en&limit=50&page={page}"
            )
            items = data if isinstance(data, list) else []
            for item in items:
                number = item.get("index_numeric")
                url = ((item.get("image") or {}).get("raw") or {}).get("url") or ""
                if (
                    item.get("type") == "volume" and item.get("language") == "en"
                    and isinstance(number, (int, float)) and float(number).is_integer()
                    and number > 0 and url.startswith("https://")
                ):
                    covers.setdefault(str(int(number)), {"id": item.get("id"), "url": url})
            if len(items) < 50:
                break
        return covers

    async def lookup(
        self, anilist_id: int | None = None, mangaupdates_id: int | None = None,
        mangabaka_id: int | None = None,
    ) -> ComicExtras | None:
        """The series' MangaBaka extras, or None when MangaBaka doesn't know
        it under any of the ids (its own id is asked first)."""
        if mangabaka_id is not None:
            record = await self._record(int(mangabaka_id))
            if record:
                return parse_series(record)
        paths = []
        if anilist_id is not None:
            paths.append(f"/source/anilist/{int(anilist_id)}")
        if mangaupdates_id is not None and mangaupdates_id >= 0:
            paths.append(f"/source/manga-updates/{mangaupdates_slug(int(mangaupdates_id))}")
        for path in paths:
            record = await self._by_source(path)
            if record:
                return parse_series(record)
        return None


provider = MangaBakaClient()
