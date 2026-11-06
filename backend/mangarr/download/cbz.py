"""CBZ packaging with ComicInfo.xml (Anansi/ComicRack schema, as read by
Komga and Kavita)."""

import json
import re
import zipfile
from ..permissions import apply_created, mkdir_created
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

EXT_BY_SIGNATURE = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
}

# XML 1.0 allows tab, newline and carriage return but no other control
# characters (nor U+FFFE/U+FFFF or lone surrogates). Metadata providers do
# occasionally send one — a stray form feed in a summary — and a single one
# makes the whole ComicInfo unparseable.
XML_ILLEGAL_CHARS = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def guess_extension(data: bytes, fallback: str = ".jpg") -> str:
    for sig, ext in EXT_BY_SIGNATURE.items():
        if data.startswith(sig):
            return ext
    # RIFF alone is any RIFF container (wav/avi/…); webp is RIFF????WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    # ISO-BMFF: size + "ftyp" + brand; avif/avis are the AVIF brands
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return ".avif"
    return fallback


# ComicInfo roles that carry MangaBaka's artists; manga artists usually draw,
# ink, tone and letter their own pages
_ARTIST_ROLES = ("Penciller", "Inker", "Colorist", "Letterer", "CoverArtist")


def series_fields(series) -> dict:
    """The series-level ComicInfo fields Kavita/Komga read beyond Series and
    Summary — for build_comicinfo(**series_fields(series)). `series` is a
    models.Series (or anything with some of its attributes); whatever it
    doesn't know is left out of the XML."""
    def attr(name):
        return getattr(series, name, None)

    try:
        extra = json.loads(attr("comicinfo_extra") or "{}")
    except (TypeError, ValueError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    genres = [g.strip() for g in (attr("genres") or "").split(",") if g.strip()] or extra.get("genres") or []
    count = None
    status = attr("status")
    if getattr(status, "value", status) == "finished":
        # Kavita: Completed when the highest volume (or chapter) on disk
        # reaches Count, Ended below it
        count = attr("total_volumes") or attr("total_chapters")
    anilist_id = attr("anilist_id")
    sources = ["MangaUpdates" if attr("mangaupdates_id") else "", "MangaBaka" if extra.get("mangabaka_id") else ""]
    return {
        "year": attr("year"),
        "count": count,
        "writers": extra.get("writers") or [],
        "artists": extra.get("artists") or [],
        "publishers": extra.get("publishers") or [],
        "genres": genres,
        "tags": extra.get("tags") or [],
        "age_rating": extra.get("age_rating") or "",
        # Kavita reads the AniList and MangaBaka ids from these links (first
        # chapter) to match the series exactly, Kavita+ included
        "links": [
            f"https://anilist.co/manga/{anilist_id}" if anilist_id else "",
            extra.get("web") or "",
        ],
        "notes": "Metadata: " + ", ".join(filter(None, sources)) if any(sources) else "",
    }


def build_comicinfo(
    series: str,
    number: float | None = None,
    volume: int | None = None,
    title: str = "",
    summary: str = "",
    web: str = "",
    page_count: int | None = None,
    *,
    year: int | None = None,
    count: int | None = None,
    writers: list[str] = (),
    artists: list[str] = (),
    publishers: list[str] = (),
    genres: list[str] = (),
    tags: list[str] = (),
    age_rating: str = "",
    links: list[str] = (),
    notes: str = "",
) -> str:
    root = Element("ComicInfo")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")

    def add(tag: str, value) -> None:
        if value is None or value == "":
            return
        SubElement(root, tag).text = XML_ILLEGAL_CHARS.sub("", str(value))

    # list fields are comma-separated, which Kavita and Komga split on —
    # so a comma inside one name would split it in two
    def names(values) -> str:
        return ", ".join(v.replace(",", " ").strip() for v in values if v and v.strip())

    add("Series", series)
    if number is not None:
        add("Number", int(number) if float(number).is_integer() else number)
    add("Count", count)
    add("Volume", volume)
    add("Title", title)
    add("Summary", summary)
    add("Notes", notes)
    add("Year", year)
    add("Writer", names(writers))
    for role in _ARTIST_ROLES:
        add(role, names(artists))
    add("Publisher", names(publishers))
    add("Genre", names(genres))
    add("Tags", names(tags))
    # several links are comma-separated in v2.1; the chapter's own page first
    add("Web", ",".join(dict.fromkeys(u for u in (web, *links) if u)))
    add("PageCount", page_count)
    add("Manga", "YesAndRightToLeft")
    add("AgeRating", age_rating)
    rough = tostring(root, encoding="unicode")
    return minidom.parseString(rough).toprettyxml(indent="  ")


def write_cbz(dest: Path, pages: list[bytes], comicinfo_xml: str) -> Path:
    """Writes ordered page images + ComicInfo.xml into a .cbz at dest."""
    mkdir_created(dest.parent)
    tmp = dest.with_suffix(".cbz.partial")
    width = max(3, len(str(len(pages))))
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("ComicInfo.xml", comicinfo_xml)
            for i, data in enumerate(pages, start=1):
                zf.writestr(f"{i:0{width}d}{guess_extension(data)}", data)
        apply_created(tmp)
        tmp.rename(dest)
    finally:
        # a failed write (e.g. a full disk) must not leave the partial archive
        # in the library; after a successful rename there is nothing to remove
        tmp.unlink(missing_ok=True)
    return dest
