"""MangaBaka (api.mangabaka.org): the extra series metadata Kavita reads from
ComicInfo.xml — writers, artists, publishers, tags, age rating, its page as a
web link. Only used for ComicInfo; titles, status and totals still come from
MangaUpdates/AniList.

Series are looked up by the AniList or MangaUpdates id mangarr already has, so
there is no title guessing. The API is free for personal, non-commercial use
with credit to MangaBaka, which ComicInfo's Notes carries.
"""

from dataclasses import asdict, dataclass, field

import httpx

from .. import USER_AGENT
from ..util import RateLimiter, rl_request

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


def mangaupdates_slug(series_id: int) -> str:
    """MangaUpdates' numeric series id in the base-36 form MangaBaka keys on
    (36271319846 → "gnv09ie")."""
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


class MangaBakaClient:
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

    async def lookup(
        self, anilist_id: int | None = None, mangaupdates_id: int | None = None
    ) -> ComicExtras | None:
        """The series' MangaBaka extras, or None when MangaBaka doesn't know
        it under either id."""
        paths = []
        if anilist_id is not None:
            paths.append(f"/source/anilist/{int(anilist_id)}")
        if mangaupdates_id is not None:
            paths.append(f"/source/manga-updates/{mangaupdates_slug(int(mangaupdates_id))}")
        for path in paths:
            record = await self._by_source(path)
            if record:
                return parse_series(record)
        return None


provider = MangaBakaClient()
