"""ComicInfo.xml carries what Kavita shows: publisher, people, genres, tags,
age rating, year, publication status and id links — MangaBaka for people and
tags, MangaUpdates for publishers."""
import json
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import httpx
import pytest

from mangarr.download.cbz import build_comicinfo, series_fields
from mangarr.jobs import tasks
from mangarr.metadata import mangabaka
from mangarr.metadata.mangabaka import ComicExtras, MangaBakaClient, mangaupdates_slug, parse_series
from mangarr.metadata.mangaupdates import MangaUpdatesProvider
from mangarr.models import Series, SeriesStatus

RECORD = {
    "id": 3403,
    "state": "active",
    "canonical_url": "https://mangabaka.org/manga/3403/Demon-Slayer-Kimetsu-no-Yaiba",
    "authors": ["Koyoharu Gotouge"],
    "artists": ["Koyoharu Gotouge"],
    "genres": ["action", "supernatural", "slice_of_life"],
    "content_rating": "erotica",
    "publishers": [
        {"name": "Devir", "type": "Other"},
        {"name": "MANGA Plus", "type": "English"},
        {"name": "Shueisha", "type": "Original"},
    ],
    "tags_v2": [
        {"name": "Incidental thing", "weight": "incidental", "is_spoiler": False, "is_genre": False},
        {"name": "Swordplay", "weight": "defining", "is_spoiler": False, "is_genre": False},
        {"name": "Demon Hunter", "weight": "core", "is_spoiler": False, "is_genre": False},
        {"name": "Big reveal", "weight": "core", "is_spoiler": True, "is_genre": False},
        {"name": "Fantasy", "weight": "core", "is_spoiler": False, "is_genre": True},
    ],
}


def test_mangaupdates_id_becomes_mangabakas_base36_key():
    assert mangaupdates_slug(36271319846) == "gnv09ie"
    assert mangaupdates_slug(69874234582) == "w3lbcgm"
    assert mangaupdates_slug(0) == "0"
    with pytest.raises(ValueError):
        mangaupdates_slug(-5)  # would never reach 0


async def test_a_negative_mangaupdates_id_is_not_looked_up():
    client = MangaBakaClient(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: pytest.fail(f"requested {r.url}"))))
    assert await client.lookup(mangaupdates_id=-1) is None


def test_parse_keeps_core_and_defining_tags_without_spoilers():
    extras = parse_series(RECORD)
    assert extras.mangabaka_id == 3403
    assert extras.writers == ["Koyoharu Gotouge"] and extras.artists == ["Koyoharu Gotouge"]
    assert extras.tags == ["Demon Hunter", "Swordplay"]
    assert extras.genres == ["Action", "Supernatural", "Slice Of Life"]
    assert extras.publishers == ["Shueisha", "MANGA Plus"]  # original first, other languages dropped
    assert extras.age_rating == "Mature 17+"
    assert parse_series({**RECORD, "content_rating": "safe"}).age_rating == ""


def test_mangaupdates_publishers_original_then_english():
    meta = MangaUpdatesProvider()._to_metadata({
        "series_id": 1, "title": "Kimetsu no Yaiba",
        "publishers": [
            {"publisher_name": "Viz", "type": "English"},
            {"publisher_name": "Shueisha", "type": "Original"},
            {"publisher_name": "Manga Plus", "type": "English"},
        ],
    })
    assert meta.publishers == ["Shueisha", "Viz", "Manga Plus"]


async def test_lookup_by_anilist_then_mangaupdates_and_follows_merges():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/v1/source/anilist/5":
            return httpx.Response(404, json={"status": 404})
        if request.url.path == "/v1/source/manga-updates/gnv09ie":
            return httpx.Response(200, json={"data": {"series": [{"id": 1, "state": "merged", "merged_with": 3403}]}})
        if request.url.path == "/v1/series/3403":
            return httpx.Response(200, json={"data": RECORD})
        return httpx.Response(500)
    client = MangaBakaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    extras = await client.lookup(anilist_id=5, mangaupdates_id=36271319846)
    assert extras.mangabaka_id == 3403
    assert seen == ["/v1/source/anilist/5", "/v1/source/manga-updates/gnv09ie", "/v1/series/3403"]
    assert await MangaBakaClient(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(404, json={"status": 404})))).lookup(anilist_id=1) is None


def series(**kw):
    base = dict(id=1, title="Kimetsu no Yaiba", anilist_id=87216, mangaupdates_id=36271319846,
                genres="Action,Drama", year=2016, status=SeriesStatus.FINISHED,
                total_volumes=23, total_chapters=207,
                comicinfo_extra=json.dumps(parse_series(RECORD).to_dict()))
    return Series(**{**base, **kw})


def test_comicinfo_has_the_kavita_fields():
    root = fromstring(build_comicinfo("Kimetsu no Yaiba", number=1, volume=1,
                                      web="https://source/ch1", **series_fields(series())))
    get = root.findtext
    assert get("Publisher") == "Shueisha, MANGA Plus"
    assert get("Writer") == "Koyoharu Gotouge"
    for role in ("Penciller", "Inker", "Colorist", "Letterer", "CoverArtist"):
        assert get(role) == "Koyoharu Gotouge"
    assert get("Genre") == "Action, Drama"  # the series' own genres win
    assert get("Tags") == "Demon Hunter, Swordplay"
    assert get("AgeRating") == "Mature 17+"
    assert get("Year") == "2016"
    assert get("Count") == "23"
    assert get("Web") == ("https://source/ch1,https://anilist.co/manga/87216,"
                          "https://mangabaka.org/manga/3403/Demon-Slayer-Kimetsu-no-Yaiba")
    assert get("Notes") == "Metadata: MangaUpdates, MangaBaka"


def test_missing_data_is_left_out():
    root = fromstring(build_comicinfo("X", number=1, **series_fields(series(
        anilist_id=None, mangaupdates_id=None, comicinfo_extra="", genres="", year=None,
        status=SeriesStatus.RELEASING))))
    for tag in ("Publisher", "Writer", "Penciller", "Genre", "Tags", "AgeRating", "Year", "Count", "Web", "Notes"):
        assert root.find(tag) is None, tag
    # finished without volumes: the chapter total is the count
    fields = series_fields(series(total_volumes=None, comicinfo_extra="{bad json"))
    assert fields["count"] == 207 and fields["publishers"] == []


def test_stored_values_that_are_not_text_are_skipped():
    root = fromstring(build_comicinfo("X", publishers=["Viz", 5, None, ""], tags=[{"a": 1}, "Swords"]))
    assert root.findtext("Publisher") == "Viz" and root.findtext("Tags") == "Swords"


def test_a_comma_inside_a_name_does_not_split_it():
    root = fromstring(build_comicinfo("X", publishers=["Kodansha, Ltd.", "Viz"]))
    assert root.findtext("Publisher") == "Kodansha  Ltd., Viz"


@pytest.fixture
def setting(monkeypatch):
    value = {"mangabaka_metadata": "true", "english_volume_covers": "false"}

    async def get(_session, key):
        return value[key]
    monkeypatch.setattr(tasks.settings_service, "get", get)
    return value


async def test_refresh_stores_extras_with_mangaupdates_publishers(monkeypatch, setting):
    async def lookup(anilist_id, mangaupdates_id):
        assert (anilist_id, mangaupdates_id) == (87216, 36271319846)
        return parse_series(RECORD)
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    s = series(comicinfo_extra="")
    await tasks.refresh_comicinfo_extra(None, s, SimpleNamespace(publishers=["Shueisha", "Viz"]))
    stored = json.loads(s.comicinfo_extra)
    assert stored["publishers"] == ["Shueisha", "Viz"] and stored["mangabaka_id"] == 3403


async def test_refresh_off_still_writes_mangaupdates_publishers(monkeypatch, setting):
    setting["mangabaka_metadata"] = "false"

    async def lookup(*_):
        raise AssertionError("MangaBaka must not be called when off")
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    s = series()
    await tasks.refresh_comicinfo_extra(None, s, SimpleNamespace(publishers=["CLLENN"]))
    assert ComicExtras.from_dict(json.loads(s.comicinfo_extra)) == ComicExtras(publishers=["CLLENN"])


async def test_mangabaka_outage_keeps_what_was_stored(monkeypatch, setting):
    async def lookup(*_):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    s = series()
    before = s.comicinfo_extra
    await tasks.refresh_comicinfo_extra(None, s, SimpleNamespace(publishers=["Other"]))
    assert s.comicinfo_extra == before


async def test_a_series_mangabaka_stops_finding_keeps_its_data(monkeypatch, setting):
    async def lookup(*_):
        return None
    monkeypatch.setattr(tasks.mangabaka, "lookup", lookup)
    s = series()
    before = json.loads(s.comicinfo_extra)
    await tasks.refresh_comicinfo_extra(None, s, SimpleNamespace(publishers=["Shueisha", "Viz"]))
    after = json.loads(s.comicinfo_extra)
    assert after["publishers"] == ["Shueisha", "Viz"]  # MangaUpdates still updates them
    assert {**after, "publishers": before["publishers"]} == before
    # one it never found stays empty, with MangaUpdates' publishers
    fresh = series(comicinfo_extra="")
    await tasks.refresh_comicinfo_extra(None, fresh, SimpleNamespace(publishers=["Viz"]))
    assert json.loads(fresh.comicinfo_extra)["publishers"] == ["Viz"]


def test_mangabaka_module_shares_one_client():
    assert isinstance(mangabaka.provider, MangaBakaClient)
