"""Add New Series by id or link: "mangabaka:126821", "anilist:159441", "mal:20593",
"mangaupdates:z7zkmto" (or mb: / al: / mu:), or a pasted MangaBaka, AniList, MyAnimeList or
MangaUpdates link, as Kavita's matcher accepts.

MangaBaka links the sites' ids, so each one resolves to a single series. The result comes from
MangaUpdates when it lists the series (the library's primary provider, so a later add or import
of the same series matches), else from AniList, else from MangaBaka itself.
"""

import re

from .anilist import provider as anilist
from .base import SeriesMetadata
from .mangabaka import provider as mangabaka, to_metadata
from .mangaupdates import provider as mangaupdates

_SITES = {
    "mangabaka": "mangabaka", "mb": "mangabaka",
    "anilist": "anilist", "al": "anilist",
    "mal": "mal", "myanimelist": "mal",
    "mangaupdates": "mangaupdates", "mu": "mangaupdates",
}
_PREFIXED = re.compile(r"\A\s*([a-z]+)\s*:\s*([0-9a-z]{1,13})\s*\Z", re.I)
_LINKS = (
    (re.compile(r"\bmangabaka\.(?:org|dev)/(?:[a-z_-]+/)?(\d{1,9})(?:[/?#]|\Z)", re.I), "mangabaka"),
    (re.compile(r"\banilist\.co/manga/(\d{1,9})(?:[/?#]|\Z)", re.I), "anilist"),
    (re.compile(r"\bmyanimelist\.net/manga/(\d{1,9})(?:[/?#]|\Z)", re.I), "mal"),
    (re.compile(r"\bmangaupdates\.com/series/([0-9a-z]{1,13})(?:[/?#]|\Z)", re.I), "mangaupdates"),
)

# shown under the search box
EXAMPLES = ("mangabaka:1331", "anilist:159441", "mal:20593", "mangaupdates:gnv09ie")


def parse(query: str) -> tuple[str, str] | None:
    """(site, id) when the query is an id or a link instead of a title, else None."""
    text = (query or "").strip()
    if len(text) > 300:
        return None
    if match := _PREFIXED.match(text):
        site = _SITES.get(match.group(1).lower())
        value = match.group(2).lower()
        if site and (site == "mangaupdates" or value.isdigit()):
            return site, value
        return None
    for pattern, site in _LINKS:
        if match := pattern.search(text):
            return site, match.group(1).lower()
    return None


def mangaupdates_id(value: str) -> int:
    """MangaUpdates' numeric series id from either form: the number, or the base-36 key in its links."""
    return int(value) if value.isdigit() else int(value, 36)


async def resolve(site: str, value: str) -> SeriesMetadata | None:
    """The one series an id or link names, or None when no site knows it."""
    if site == "mangaupdates":
        return await mangaupdates.get_series(str(mangaupdates_id(value)))
    if site == "mangabaka":
        record = await mangabaka.record_by_id(int(value))
    else:
        record = await mangabaka.record_by_source(site, int(value))
    if record:
        source = record.get("source") or {}
        mu_key = (source.get("manga_updates") or {}).get("id")
        al_id = (source.get("anilist") or {}).get("id")
        if mu_key:
            if meta := await mangaupdates.get_series(str(mangaupdates_id(str(mu_key).lower()))):
                return meta
        if al_id:
            if meta := await anilist.get_series(str(al_id)):
                return meta
        return to_metadata(record)
    if site == "anilist":  # an AniList entry MangaBaka hasn't linked yet
        return await anilist.get_series(value)
    return None
