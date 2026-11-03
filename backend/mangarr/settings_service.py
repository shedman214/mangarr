"""Runtime-editable settings stored in the Settings table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

CONTENT_SOURCE_NAMES = (
    "mangaplus",
    "webtoons",
    "mangadex",
    "mangafire",
    "weebcentral",
    "atsumaru",
    "tcbscans",
    "asura",
    "toonily",
    "mangakatana",
    "manganato",
    "toomics",
)
DISCOVERY_SOURCE_NAMES = (*CONTENT_SOURCE_NAMES, "viz", "wikipedia", "nyaa")

DEFAULTS: dict[str, str] = {
    "permissions_enabled": "false",
    "permissions_folder_mode": "775",
    "permissions_user": "99",
    "permissions_group": "100",
    # Media management
    "naming_template": "{series} - Ch. {chapter:04.1f}",
    "naming_template_no_volume": "{series} - Ch. {chapter:04.1f}",
    # Source priority: comma-separated source names, first = preferred.
    # Official sources first, then fast scanlation sources (tcbscans) ahead
    # of archive sources so new chapters are grabbed as soon as they appear.
    "source_priority": "mangaplus,webtoons,tcbscans,mangadex,mangafire,weebcentral,atsumaru,asura,toonily,viz,wikipedia,nyaa",
    # MangaDex credentials (personal API client)
    "mangadex_client_id": "",
    "mangadex_client_secret": "",
    "mangadex_username": "",
    "mangadex_password": "",
    "mangadex_language": "en",
    # qBittorrent
    "qbittorrent_url": "http://localhost:8080",
    "qbittorrent_username": "admin",
    "qbittorrent_password": "",
    "qbittorrent_category": "mangarr",
    "qbittorrent_enabled": "false",
    # Automatic add-time torrent selection inspects .torrent metadata first,
    # then chooses one seeded release with the most missing-chapter coverage.
    "torrent_auto_max_size_gib": "30",
    "torrent_auto_min_seeders": "1",
    # Downloads root shared by mangarr and qBittorrent. The category is
    # appended unless the path already ends with it (backward compatibility
    # for installations that stored the final category directory here).
    # Empty = qBittorrent's default save path.
    "downloads_dir": "",
    # Torrent import: "hardlink" keeps seeding without using double the
    # space (needs downloads + library on one filesystem); "copy" is the
    # safe cross-filesystem fallback.
    "import_mode": "hardlink",
    # Sources on/off
    "source_mangadex_enabled": "true",
    "source_mangafire_enabled": "true",
    "source_weebcentral_enabled": "true",
    "source_atsumaru_enabled": "true",
    # Official WEBTOON Originals; only currently-free episodes are listed
    "source_webtoons_enabled": "true",
    "source_tcbscans_enabled": "true",
    "source_asura_enabled": "true",
    # Experimental until verified from the user's server/network.
    "source_toonily_enabled": "false",
    "source_mangakatana_enabled": "false",
    "source_manganato_enabled": "false",
    "source_toomics_enabled": "false",
    "toomics_session_cookie": "",
    # MangaPlus needs a residential IP (bans datacenters); off until the user
    # confirms it reaches the API from their host
    "source_mangaplus_enabled": "false",
    "source_nyaa_enabled": "false",
    # Wikipedia serves no chapters — it only contributes chapter→volume maps
    "source_wikipedia_enabled": "true",
    # Official metadata-only source for VIZ-licensed properties.
    "source_viz_enabled": "true",
    # Optional HTTP(S) proxy used only for chapter page/image bytes. Source
    # discovery and manifests have independent per-source toggles below.
    "download_proxy_url": "",
    # Legacy test-build key, migrated to the per-source key by get_all().
    "mangafire_discovery_proxy_enabled": "false",
    **{f"source_{name}_discovery_proxy_enabled": "false" for name in DISCOVERY_SOURCE_NAMES},
    **{f"source_{name}_proxy_enabled": "false" for name in CONTENT_SOURCE_NAMES},
    # Jobs
    "monitor_interval_minutes": "60",
    # Finished series whose monitored chapters are all on disk: "keep"
    # checking every pass, "slow" down to once per finished_series_check_days,
    # or "unmonitor" them
    "finished_series_mode": "slow",
    "finished_series_check_days": "7",
    # how often enabled import lists are synced
    "import_list_sync_hours": "6",
    # Library
    "library_scan_on_add": "true",  # adopt existing on-disk files on add/refresh
    # Outbound webhook fired when chapters are imported (e.g. NextPanel's
    # /api/v1/webhooks/mangarr endpoint). Secret is sent as X-Webhook-Secret.
    "webhook_enabled": "false",
    "webhook_url": "",
    "webhook_secret": "",
    # Kavita: ask the reader to scan after imports so new chapters appear
    # without waiting for its own scheduled scan.
    "kavita_enabled": "false",
    "kavita_url": "",
    "kavita_api_key": "",
    # "series" scans just the affected series when Kavita already knows it
    # (falling back to its library otherwise); "library" always scans whole.
    "kavita_scan_mode": "series",
    # JSON {"<root_folder_id>": <kavita library id>}; empty = match by path
    "kavita_library_map": "",
}

SECRET_KEYS = {
    "toomics_session_cookie",
    "mangadex_client_secret", "mangadex_password", "qbittorrent_password",
    "webhook_secret", "kavita_api_key",
}


def validate(values: dict[str, str]) -> None:
    """Reject values that would break things later if stored: a bad naming
    template fails every download at rename time, and a non-numeric monitor
    interval would abort scheduler startup. Raises ValueError."""
    from .library.naming import chapter_filename
    from urllib.parse import urlsplit
    from .permissions import parse
    parse(values)
    cookie = values.get("toomics_session_cookie", "")
    if "\r" in cookie or "\n" in cookie or len(cookie) > 16384:
        raise ValueError("Invalid Toomics session cookie")

    for key in ("naming_template", "naming_template_no_volume"):
        if key not in values:
            continue
        template = values[key]
        try:
            # render with and without a volume — both paths must work
            chapter_filename(template, template, "Sample Series", 12.5, 3, "Title")
            chapter_filename(template, template, "Sample Series", 12.5, None, "Title")
        except (KeyError, ValueError, IndexError) as exc:
            raise ValueError(
                f"{key} is not a valid template (use {{series}}, {{volume}}, "
                f"{{chapter}}, {{title}}): {exc}"
            ) from exc
    if "monitor_interval_minutes" in values:
        raw = values["monitor_interval_minutes"]
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            raise ValueError("monitor_interval_minutes must be a whole number") from None
        if minutes < 1:
            raise ValueError("monitor_interval_minutes must be at least 1")
    for key, minimum in (
        ("torrent_auto_max_size_gib", 1),
        ("torrent_auto_min_seeders", 0),
        ("finished_series_check_days", 1),
        ("import_list_sync_hours", 1),
    ):
        if key not in values:
            continue
        try:
            number = int(values[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number") from None
        if number < minimum:
            raise ValueError(f"{key} must be at least {minimum}")

    if "finished_series_mode" in values:
        from .automation import FINISHED_SERIES_MODES

        if values["finished_series_mode"] not in FINISHED_SERIES_MODES:
            raise ValueError(
                "finished_series_mode must be one of " + ", ".join(FINISHED_SERIES_MODES)
            )

    if "download_proxy_url" in values and values["download_proxy_url"]:
        parsed = urlsplit(values["download_proxy_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "download_proxy_url must be a valid http:// or https:// proxy URL"
            )

    proxy_enabled = any(
        values.get(
            f"source_{name}_enabled",
            DEFAULTS[f"source_{name}_enabled"],
        ) == "true"
        and values.get(f"source_{name}_proxy_enabled") == "true"
        for name in CONTENT_SOURCE_NAMES
    )
    discovery_enabled = any(
        values.get(f"source_{name}_discovery_proxy_enabled") == "true"
        for name in DISCOVERY_SOURCE_NAMES
    )
    if (proxy_enabled or discovery_enabled) and not values.get("download_proxy_url"):
        raise ValueError(
            "download_proxy_url is required when a source content or discovery proxy is enabled"
        )

    from .kavita import validate_settings as validate_kavita

    validate_kavita(values)


async def get_all(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Setting))).scalars().all()
    values = dict(DEFAULTS)
    stored = {r.key: r.value for r in rows if r.key in DEFAULTS}
    values.update(stored)
    # Preserve the MangaFire-only option from the earlier test build unless
    # the new per-source switch has been explicitly saved.
    if "source_mangafire_discovery_proxy_enabled" not in stored:
        values["source_mangafire_discovery_proxy_enabled"] = values["mangafire_discovery_proxy_enabled"]
    from .permissions import configure
    configure(values)
    return values


async def get(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, "")


async def set_many(session: AsyncSession, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    await session.commit()
