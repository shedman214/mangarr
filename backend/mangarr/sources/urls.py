"""Recognize public series links without requesting arbitrary user URLs."""

import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .base import SourceSeries


# These paths mirror the public URLs and identifiers emitted by each adapter.
_ROUTES = {
    "toonily.com": ("toonily", r"/serie/([A-Za-z0-9_-]+)"),
    "mangafire.to": ("mangafire", r"/title/([A-Za-z0-9]+)(?:-([A-Za-z0-9-]+))?"),
    "mangadex.org": ("mangadex", r"/title/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?:/[^/]+)?"),
    "weebcentral.com": ("weebcentral", r"/series/([A-Za-z0-9]+)(?:/[^/]+)?"),
    "atsu.moe": ("atsumaru", r"/manga/([A-Za-z0-9_-]+)"),
    "asurascans.com": ("asura", r"/comics/([A-Za-z0-9_-]+)"),
    "tcbonepiecechapters.com": ("tcbscans", r"/mangas/([0-9]+/[^/]+)"),
    "mangaplus.shueisha.co.jp": ("mangaplus", r"/titles/([0-9]+)"),
    "viz.com": ("viz", r"/((?:shonenjump|vizmanga)/chapters/[^/?#]+)"),
    "en.wikipedia.org": ("wikipedia", r"/wiki/([^/]+)"),
}


def series_from_url(query: str) -> SourceSeries | None:
    """Return a link candidate, None for a title, or a useful error for bad URLs.

    A candidate identifies a series; existence and chapter availability are
    checked by the normal source sync after the user chooses it.
    """
    query = query.strip()
    if not query:
        raise ValueError("Enter a title or a series website link.")
    # Support links copied without their scheme, too.
    looks_like_url = bool(re.match(r"^(?:[a-z][a-z0-9+.-]*://|//|(?:www\.)?[^\s/]+\.[a-z]{2,}/)", query, re.I))
    if not looks_like_url:
        return None
    if "://" not in query and not query.startswith("//"):
        query = "https://" + query
    try:
        parts = urlsplit(query if not query.startswith("//") else "https:" + query)
        if parts.scheme.lower() not in {"http", "https"} or parts.username or parts.password or parts.port:
            raise ValueError
        host = (parts.hostname or "").lower().removeprefix("www.")
    except ValueError:
        raise ValueError("Enter a valid public HTTP or HTTPS series link.") from None
    path = parts.path.rstrip("/")
    if host == "webtoons.com":
        ids = parse_qs(parts.query).get("title_no", [])
        if not re.fullmatch(r"/en/(?!canvas/)[^/]+/[^/]+/list", path) or len(ids) != 1 or not ids[0].isdigit():
            raise ValueError("Use an English WEBTOON Originals series list link with title_no.")
        name, external_id = "webtoons", ids[0]
        clean_query = f"title_no={external_id}"
    else:
        route = _ROUTES.get(host)
        if route is None:
            raise ValueError("This website is not supported. Paste a series link from a supported source.")
        name, pattern = route
        match = re.fullmatch(pattern, path)
        if not match:
            raise ValueError(f"Use a {name} series page link, rather than a chapter or search link.")
        external_id = match.group(1)
        if name == "viz":
            external_id = "/" + external_id
        elif name == "wikipedia":
            external_id = unquote(external_id).replace("_", " ")
            if any(c in external_id for c in "/:#?\\"):
                raise ValueError("Use a Wikipedia article link.")
        clean_query = ""
    url = urlunsplit(("https", host, path, clean_query, ""))
    # Do not invent metadata from a slug. Show the recognized identifier.
    return SourceSeries(name, external_id, f"{name} series ({external_id})", url)
