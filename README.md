<p align="center">
  <img src="frontend/public/mangarr-icon.svg" width="160" height="160" alt="Mangarr icon">
</p>

# mangarr

> This is a fork of [KiranTheRam/mangarr](https://github.com/KiranTheRam/mangarr) with additional sources,
> source diagnostics, permission tools and backups. Report issues for this fork at
> <https://github.com/shedman214/mangarr/issues>.

Radarr/Sonarr-style automation for manga. Monitor series, automatically grab
new chapters from direct sources or torrents, and organize everything as CBZ
files with `ComicInfo.xml` — ready for [Komga](https://komga.org) or
[Kavita](https://www.kavitareader.com). Mangarr has no built-in reader by
design; it is the automation half of your manga stack.

![stack](https://img.shields.io/badge/backend-FastAPI-009688) ![stack](https://img.shields.io/badge/frontend-React-61dafb) ![stack](https://img.shields.io/badge/db-SQLite-003b57)

## Features

- **Library management** — add series via MangaUpdates metadata search
  (covers, descriptions, status; AniList remains available as a fallback
  provider), poster-grid library, per-series chapter tables with monitor
  toggles, wanted/missing view. MangaUpdates tracks scanlation releases per
  chapter, so chapter/volume counts stay current for ongoing series where
  AniList lags, and its release feed fills in chapters no direct source
  serves yet.
- **Sources** — grabbed in a configurable priority order (fast scanlation
  sources first, archive sources as fallback):
  - **MangaPlus** (Shueisha, official) — the true same-day source for Shonen
    Jump titles (One Piece, Kagurabachi, Dandadan, Jujutsu Kaisen…). Only the
    first and latest few chapters of each title are free, which is exactly the
    new-chapter use case. **Off by default**: the API bans datacenter IPs, so
    it only works from a residential IP (e.g. a home server). Enable it in
    Settings once you've confirmed it reaches the API from your host.
  - **WEBTOON** (official) — English WEBTOON Originals. Only episodes that
    are currently free are listed (coin-locked Daily Pass / Fast Pass
    episodes are skipped), so it's most useful for ongoing series.
  - **TCB Scans** (scraper) — fastest scanlations of the big Jump titles,
    usually within hours. Small catalog of major series only.
  - **MangaDex** (API) — huge, well-tagged archive; supplies volume data. Works
    anonymously but limits guests to 10 chapters/day — add a free account +
    personal API client in Settings for normal use.
  - **MangaFire** (API) — broad English archive and an additional direct source
    for decimal-numbered bonus and special chapters.
  - **WeebCentral** (scraper) — broad catalog, no account needed.
  - **Atsumaru** (API) — broad archive of manga, manhwa, and manhua. Where
    several groups translated a chapter, the best-rated one is used. Recent
    uploads are AVIF images, which Kavita and Komga read natively.
  - **Asura Scans** (API) — Korean/Chinese webtoons and manhwa (not Japanese
    manga). Skips locked early-access (premium) chapters automatically.
  - **Toonily** (experimental scraper, off by default) — title search,
    chapter lists, and chapter images. Enable it under Settings → Sources.
    Each image request carries its own chapter page as the Referer, including
    when several chapters download concurrently. Access may vary by network;
    HTTP 403 responses are reported as failures. Live downloads still need
    verification from a reachable host. No browser or cookies are required
    by the adapter. Separate side-story URL routes are not yet mapped to
    main-series chapter numbers.
  - **Nyaa.si** (torrents) — Literature/English-translated category, sent to
    qBittorrent; completed downloads are imported automatically (volume packs,
    chapter archives, or loose-image folders). Imports **hardlink** by
    default — the torrent keeps seeding and the library copy costs no extra
    space (downloads and library must share a filesystem; falls back to copy
    automatically, and a copy mode setting is available).
- **Discovery** — import lists follow AniList, MyAnimeList, MangaDex or
  MangaUpdates reading lists and add new entries automatically; a calendar
  shows recent releases and when each ongoing series' next chapter is due
  (from its usual release rhythm); series pages list sequels, side stories
  and recommendations from AniList and MangaUpdates, one click from adding.
- **Automation** — a monitor loop checks linked sources for new chapters of
  monitored series and grabs them by configurable source priority. Manual
  per-chapter interactive search included.
- **Output** — one CBZ per chapter, `ComicInfo.xml` embedded, Komga/Kavita
  naming: `Series Title/Series Title - Vol. 01 Ch. 0021.cbz`.
- **Kavita scans** — optionally tell Kavita to scan after an import, so new
  chapters appear in the reader immediately. Scans the affected series when
  Kavita already knows it, and the whole library when it does not (a series
  mangarr just created).
- **Webhooks** — optionally POST a small JSON event to a URL of your choice
  whenever chapters are imported, so a request manager (e.g. NextPanel) sees
  them right away. Set it up under **Settings → Connect — Webhook**; an
  optional secret is sent as `X-Webhook-Secret`, and a dead endpoint never
  fails a download.
- ***arr-style API** — everything under `/api/v1` with `X-Api-Key` auth.

## Quick start (Docker)

```bash
git clone <this repo> mangarr && cd mangarr
docker compose up -d
```

The default Compose file runs this fork's image,
`ghcr.io/shedman214/mangarr:test`, and stores configuration and media under
`./data`. The image is published by this repository's GitHub Actions only after
the backend tests and the frontend build pass; each build is also tagged
`sha-<commit>` if you want to pin an exact version. Unraid users can add
`templates/mangarr.xml` from this repository, which uses the same image. Open
<http://localhost:6996> after it starts.
The image's healthcheck polls `GET /ping` (no API key needed), so
`docker ps` shows the container as `healthy` once Mangarr is serving.

First-run checklist, in the mangarr UI:

1. **Settings → Root Folders**: add `/media/manga`.
2. **Settings → MangaDex Account** (recommended): create a free account at
   mangadex.org, then *Settings → API Clients* there to make a personal
   client; paste client id/secret and your username/password.
3. **Add New**: search a title, pick a root folder, add. Chapters appear after
   the automatic source-linking pass (a few seconds). If **Search now** and
   qBittorrent are enabled, Mangarr also inspects Nyaa torrent file lists,
   queues the seeded release with the most missing-chapter coverage, then uses
   direct sources for anything that release does not contain. Automatic
   torrents are capped at 30 GiB by default and can be tuned in Settings.

### Using an existing qBittorrent container

Mangarr does not bundle qBittorrent. To enable torrent downloads, connect it
to a qBittorrent instance you already run:

1. Mount the same host media directory into both containers at the same
   container path. With the default Compose file, mount `./data/media` in
   qBittorrent as `/media` too. On a NAS or unRAID system, both containers
   could instead use a shared mapping such as `/mnt/user/media:/media`.
2. Make qBittorrent reachable from Mangarr. Put both containers on the same
   Docker network and use a URL such as `http://qbittorrent:8080`, or use the
   qBittorrent host's LAN address and WebUI port.
3. In **Settings → Download Client**, enter that URL and the qBittorrent WebUI
   credentials, then select **Test Connection**.
4. Set **Downloads folder** to `/media/torrents`. Because qBittorrent and
   Mangarr see the same path, Mangarr can import completed downloads and
   hardlink them into `/media/manga` without duplicating data while the
   torrent continues seeding.

### Running as a non-root user (PUID / PGID / UMASK)

By default the container runs as root, so the files and folders Mangarr
creates are owned by root (files `0644`, folders `0755`). To have it create
them as your own user instead, for example so they stay editable over an SMB
share, set these on the container:

| Variable | Example | Description                                                |
| -------- | ------- | ---------------------------------------------------------- |
| `PUID`   | `99`    | User id to run as                                          |
| `PGID`   | `100`   | Group id to run as                                         |
| `UMASK`  | `002`   | umask for new files and folders (`002`: `664` / `775`)     |

Set `PUID` and `PGID` together; `UMASK` also works on its own. On unRAID,
`PUID=99` and `PGID=100` (`nobody:users`) are the usual choice, and
`UMASK=002` keeps new files group-writable. Leave them unset and nothing
changes.

### Permissions in Settings

Settings → Permissions adds optional chmod/chown controls for files and
folders Mangarr creates. It starts off, leaving PUID/PGID/UMASK behavior
unchanged. The defaults when enabled are folder `775`, file `664`, UID `99`,
and GID `100`. File modes omit execute bits. Blank owner/group fields keep
the current owner/group; names must exist inside the container.

New downloads, merged CBZs, copied imports, and newly created directories use
the selected settings. Existing directories and files adopted from disk are
not changed automatically. Hardlinked imports retain their permissions,
since changing a hardlink would also change its seeding file.

**Preview permissions** checks only tracked, Mangarr-created CBZ files under
registered library roots. A repair requires saved, enabled settings and an
explicit confirmation of its preview. Symlinks, shared hardlinks, and files
changed since preview are skipped. Repairs write an undo journal under
`/config/logs/permissions`; **Undo this repair** restores recorded modes and
ownership only while the file's identity and permission state still match.
Preview does not modify library permissions, and existing folders are never
included in repairs. Mangarr must own a file or have sufficient privileges
to change its permissions or owner.

### Test Toonily search and downloads

Settings → Sources → **Test Toonily download** checks a non-adult sample:
Solo Leveling chapter 1. It searches the site, lists chapters, downloads the
whole chapter, and validates the resulting CBZ. The sample goes into a unique
folder under `/config/source-tests/toonily`, separate from the library. The
result reports the chapter/page counts, archive size, ownership, and mode.
Save proxy settings before running the test. The test does not enable
Toonily globally or queue other downloads.

On start, Mangarr makes its data directory (`/config`) owned by `PUID:PGID`
automatically. It never changes your library or download folders, so when
switching an existing install from root, give the new user those once, e.g.
on the host:

```bash
chown -R 99:100 /mnt/user/media/manga /mnt/user/media/torrents
```

Use your own root folder(s) and qBittorrent's downloads folder. Run
qBittorrent with the same `PUID`/`PGID` too, so Mangarr can still hardlink
the files it downloads.

### Triggering Kavita scans

Kavita only picks up new CBZ files when it scans. To have imports show up in
the reader immediately:

1. In Kavita, go to **Settings → Account** and copy the **API Key**.
2. In Mangarr, go to **Settings → Connect — Kavita**, turn it on, enter
   Kavita's URL (e.g. `http://kavita:5000`) and the API key, then select
   **Test Connection** — this also loads Kavita's libraries.
3. Check the **Library mapping** rows. Mangarr matches a root folder to a
   Kavita library by folder name, so `/media/manga` finds a library at
   `/data/manga` even when the containers mount it differently. Pick a library
   explicitly if the two paths have nothing in common.

**Scan scope** decides how much Kavita re-reads. *Series* scans only the
affected series folder, which is much cheaper on a large library. Kavita cannot
scan a series it has never indexed, so the first import for a newly added
series scans its whole library — as does a title Kavita holds more than once,
since guessing between them could scan the wrong one. *Whole library every
time* skips the lookup. Bursts of imports are batched into a single scan, and a
Kavita that is down or misconfigured never fails a download.

## Using an existing library

Mangarr can sit on top of a library you already have and adopt it in place —
it won't re-download what's on disk.

1. **Mount your library** into the mangarr container and add it as a root
   folder. In `docker-compose.yml`, add a volume (e.g.
   `- /mnt/nas/manga:/library`), then Settings → Root Folders → add `/library`.
2. **Import the folders**: Add New → **Library import** lists every folder in
   the root that no series uses yet, matches each to a MangaUpdates entry by
   name, and adds the ones you confirm in one go (each keeps its folder). You
   can also add series one at a time: on add (and on every refresh) mangarr
   finds the matching folder in the root. Either way it scans the folder and
   marks chapters/volumes you already have as owned, so the Wanted list shows
   only what's genuinely missing.
3. **Per-series tools** (on the series page):
   - **Scan Disk** — re-scan the folder and adopt any new files.
   - **Files** — see everything found, with unmatched files you can map to a
     chapter by hand.
   - **Rename** — preview and apply mangarr's naming convention
     (`Series - Vol. 01 Ch. 0021.cbz`). Renaming **preserves the original
     format** (a `.cbr` stays `.cbr`), never overwrites an existing file, and
     never deletes anything.
   - **Change folder** — browse the filesystem to point a series at its folder,
     including a subfolder for collections (e.g. `Attack On Titan/Attack On
     Titan`).
   - **Edit sources** — search by title with a selected source, or paste a
     series website link to detect its source automatically. Review the
     recognized source and URL, select **Use**, then **Re-sync chapters from
     links**. A recognized URL is a link candidate; chapter availability is
     checked during sync. Links work for all current direct-source adapters,
     including MangaFire and Toonily. Chapter and search-page links are
     rejected where a series link is required. Enable the source in Settings
     before syncing.

Matching is filename-based and handles the usual variety: `Series - Ch. 12.cbz`,
`Series ch. 12`, `Volume 03.cbr`, `Series v03 (2019).cbz`, whole-volume archives
(which mark every chapter in that volume), and folders of loose images.

## Local development

Backend (Python ≥3.11):

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn mangarr.main:app --port 6996 --reload
```

Frontend (Node 22.12+; the jsdom used by the frontend tests needs Node 22):

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173, proxies API to :6996
```

Tests:

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm test   # Vitest + Testing Library behavior tests in frontend/tests/
```

`npm run build` writes the production bundle to `backend/static/`, which the
FastAPI app serves when present.

## Configuration

Environment variables (all optional):

| Variable             | Default       | Description                                       |
| -------------------- | ------------- | ------------------------------------------------- |
| `MANGARR_HOST`       | `0.0.0.0`     | Address to listen on                              |
| `MANGARR_PORT`       | `6996`        | HTTP port                                         |
| `MANGARR_DATA_DIR`   | `data`        | SQLite DB, API key, cached settings               |
| `MANGARR_SQLITE_WAL` | `true`        | Use SQLite WAL mode (see below)                   |
| `MANGARR_PREMIGRATION_BACKUP` | `true` | Back up the database before an upgrade changes its layout (see Backup and restore) |
| `MANGARR_LOGIN_DISABLED` | `false` | Ignore the built-in login (recovery from a forgotten password; see Login) |
| `MANGARR_LOG_LEVEL`  | `INFO`        | Starting log level: `INFO`, `DEBUG` or `TRACE` (a level chosen in Settings → Logs takes priority) |
| `MANGARR_API_KEY`    | *(generated)* | Fixed web UI API key instead of a generated one   |

`MANGARR_HOST` and `MANGARR_PORT` apply when Mangarr is started with
`python -m mangarr.main`; a `uvicorn` command line like the one under Local
development uses its own `--host`/`--port` flags instead.

**Database storage.** Mangarr runs SQLite in WAL mode, so next to
`mangarr.db` you will also see `mangarr.db-wal` and `mangarr.db-shm`. To back
up, stop the app and copy all three files, or use `sqlite3 mangarr.db ".backup
copy.db"`. WAL needs the config directory on local storage (a bind mount or
Docker volume on a local disk). If `/config` is on NFS or SMB, set
`MANGARR_SQLITE_WAL=false`; the database is then switched back to the
rollback journal on the next start.

Everything else (sources, credentials, naming templates, qBittorrent,
monitor interval) lives in the UI under Settings and is stored in the DB.

**A source returning 403 during search or chapter discovery:** the normal
content-proxy toggle only routes image downloads. In Settings → Sources,
enable that source's **Discovery** toggle to also route its searches,
chapter lists, page manifests, metadata, and request-signing assets through
the configured proxy. Each direct source, metadata source, and the Nyaa
indexer has an independent switch. MangaDex password login stays direct;
authenticated API requests use the selected discovery route. These switches
apply equally to automatically matched and manually linked series. This is
a separate opt-in and never falls back to direct requests if the proxy fails.
After saving, re-sync the linked series.
Re-sync checks linked content sources before removing existing chapter
records, and reports failed, disabled, or empty sources instead of reporting
a successful empty rebuild.

The web UI's API key is generated on first start at `<data dir>/api_key` (or
taken from `MANGARR_API_KEY` when set, in which case no file is written) and
handed to the UI via `GET /initialize.json`. To give external clients (e.g.
NextPanel or scripts) their own keys, create named keys under **Settings → API
Keys**; any of them authenticates `/api/v1` calls via `X-Api-Key` and can be
revoked independently.

## Logs

**Settings → Logs** shows the most recent log lines, downloads all log files as
one ZIP, and sets the level (applied immediately, remembered across restarts):

- **Info** (default): Mangarr's own messages — downloads, imports, warnings, errors.
- **Debug**: adds Mangarr's detailed steps, web UI requests and scheduled job runs.
- **Trace**: adds one line per outgoing web request (sources, Kavita, proxies).

Logs are written to the container log and to `<data dir>/logs/mangarr.txt`
(rotated at 5 MB, five files kept). Saved secrets, API keys, passwords, tokens,
cookies, `Authorization` values and `user:password@` in URLs are replaced with
`<redacted>` before anything is written, and the download is checked again with
the current secrets. Third-party libraries stay at Info, so Debug does not fill
the log with database driver output.

## Login

Login is off by default: anyone who can reach the port can open the web UI,
because `/initialize.json` hands it the API key. **Settings → Login** turns on
a single username and password. While it is on:

- `/initialize.json` returns the key only to a browser signed in through the
  login page (an HttpOnly, SameSite=Strict session cookie valid for 30 days;
  marked Secure over HTTPS, including behind a proxy sending
  `X-Forwarded-Proto: https`). Everyone else gets the sign-in page.
- Turning login on replaces the generated browser key, so copies handed out
  earlier stop working. Scripts and apps should use keys from **Settings → API
  Keys**, which keep working. A fixed `MANGARR_API_KEY` cannot be replaced, and
  anyone who knows it can still use the API; Settings warns about this.
- The generated browser key also requires a valid session on API requests, so
  signing out, session expiry, and password changes revoke that browser's access.
  Bootstrap responses are not cached. User-managed script keys remain independent.
- Changing the password or turning login off requires the current password, and
  signs out every other browser. Five failed sign-ins from one address within
  15 minutes block further attempts for a minute.
- The password is stored as an scrypt hash in `<data dir>/auth.json` (mode
  0600) with the session list. It is not part of the settings API or backups,
  so restoring a backup does not change the login.

Forgot the password? Set `MANGARR_LOGIN_DISABLED=true`, restart, choose a new
password in Settings → Login, then remove the variable and restart again.
Deleting `auth.json` and restarting also turns login off. `/ping` stays public
for health checks. For access from outside your network, still prefer a VPN or
an authenticating reverse proxy.

## How grabbing works

1. When you add a series, mangarr links it to each enabled source by title
   (including MangaUpdates associated titles). Links are per-source, so a
   site changing its layout breaks one source, never the app.
2. The monitor job (default: every 60 min) diffs source chapter lists against
   the library. New monitored, missing chapters are grabbed from the highest
   priority source that has them (`Settings → Sources → priority`).
3. Direct grabs download pages with per-source rate limits and pack them into
   a CBZ. Add-time torrent searches inspect the small `.torrent` metadata
   first and rank releases by exact file coverage, special-chapter coverage,
   seeders, and size. The selected release goes to qBittorrent under the
   `mangarr` category and is imported when complete.

### Adult content

**Settings → Sources → Adult content** is off by default. When you turn it on,
searches and downloads include adult titles on sources that filter them:
MangaDex adds its `pornographic` rating (search, chapter lists and chapter
checks), FanFox and Toonily receive their own over-18 confirmation, and
WeebCentral searches adult titles too. It applies as soon as you save. Toomics
keeps using your own session's Family Safe setting.

### Upgrades (like Sonarr's "Upgrades Allowed" / "Upgrade Until")

**Settings → Sources → Upgrades** sets the defaults for newly added series:

- **Upgrades allowed** — when on, a downloaded chapter is downloaded again when a
  source higher in the priority list has it, or when its file is from a blocked
  scanlation group. Off by default.
- **Upgrade until** — once a chapter's file comes from this source or a better
  one, it is not downloaded again (default: the top source).
- **Apply to all series** sets every existing series to the saved values (after a
  confirmation); each series can still be changed under its Automation settings.

Only chapter files Mangarr downloaded itself from a direct source are replaced.
Files you added, torrent imports and volume archives are never touched. Upgrades
are queued during the normal monitor passes, at most 25 per series per pass.

Please be a good citizen: keep the honest User-Agent, don't lower the rate
limits, and use a MangaDex account so their team can see the traffic is
legitimate.

## Roadmap

### Background permission operations

Permission previews, repairs, and undo operations now run as background jobs.
Settings shows their progress, and reopening the page reconnects to the latest
job. Library paths and preview mismatches are streamed to disk without a file
count limit; the browser receives only 50 example rows. A repair still applies
to every file in the completed preview, with identity checks before changes.
Job progress and undo IDs are saved under `/config/logs/permissions`; an
interrupted repair can be undone after a restart. Previews expire one hour
after completion. Existing synchronous API endpoints remain compatible.

### Checking source availability

MangaFire normally loads its current request signer from the site's frontend.
If that frontend is unavailable, Mangarr tries a bundled FMD2-compatible API
signer on the same configured discovery route. It caches this fallback for ten
minutes, then probes the dynamic signer again. A rejected token triggers at
most one refresh/retry; other API errors still fail normally. No cookies,
browser challenge solving, or alternate network route is added. Bundled
protocol constants may need an update if MangaFire changes its signing keys.
**Test MangaFire download** in Settings verifies Solo Leveling search,
chapter discovery, and a complete chapter-1 CBZ in an isolated appdata folder.

In a series' **Edit sources** dialog, **Check chapters** tests the exact
saved link without rebuilding chapters, changing files, or queuing downloads.
It reports distinct downloadable chapter numbers and release counts. MangaDex
checks also report external publisher listings separately: those links are
not MangaDex-hosted downloads. Checks use the saved source/proxy settings and
configured language. A manual check probes immediately, even during cooldown.

**Settings → Sources → Source status** shows the latest observations, last
successful check, last failure, and next retry time. Observations reset when
Mangarr restarts; an empty result is not reported as a network failure. Error
summaries omit request URLs, credentials, and response bodies.

- Western comics support (ComicVine metadata + GetComics source) — the
  source/metadata plugin interfaces are already in place.
- Discord notifications, and notifications on grab (a webhook on import
  already exists — see Features).
# Backup and restore

Settings includes **Backup and restore**. Download backup takes an online SQLite
snapshot, including committed WAL changes, without stopping downloads. The ZIP
contains the database and the installation bootstrap key. Settings, source links,
library records, history, queue state, and database API keys are included. Media,
logs, permission undo journals, container environment variables, and Docker mounts
are excluded. Backups contain credentials: keep them private. A server copy is
retained under `/config/backups`, and Settings lists every saved backup with a
Download button. Manual backups are never deleted automatically.

To restore, upload a Mangarr backup ZIP from the same application version and
confirm replacement. Mangarr checks entries, size limits, checksum, database
integrity, and schema compatibility before scheduling it. Restore is blocked while
a permission operation is running. You can cancel a scheduled restore in Settings.
Finish active work before restarting the container: restore runs before database
initialization and background jobs on the next startup. A safety backup is created
first, and restored archives are retained in `/config/backups`. The installation's
current bootstrap key stays unchanged; database API keys and queue records come
from the backup. Keep the same library mounts. No media files are restored or
deleted.

## Automatic backup before database upgrades

When a new version needs to add tables or columns, Mangarr first saves a backup of
the existing database (read-only check, then the same online snapshot, integrity
check, and checksum as a manual backup). It is labelled "Before database update" in
Settings; the newest 5 of these are kept and older automatic ones are removed.
Nothing is backed up on a fresh install or when the layout is already current. If
this backup fails, Mangarr stops before changing the database and logs the reason;
fix the cause (usually free space or permissions on `/config`) and start it again.
`MANGARR_PREMIGRATION_BACKUP=false` skips the backup and lets the upgrade run
without it, which is not recommended. A pre-upgrade backup can be restored on the
same version through Settings; startup then upgrades it again.


## Additional experimental sources

MangaKatana, FanFox (MangaFox), Manganato.gg, and English Toomics are opt-in sources in Settings.
Each has separate discovery and content proxy controls and accepts manual series
links. Existing source priorities and proxy settings are preserved.

- MangaKatana: title search, chapter lists including decimals, ordered reader
  images, and chapter-specific image Referer. A local live Red Blue check found
  213 chapters, 36 chapter-one pages, and downloaded the first image successfully.
- FanFox (MangaFox): title search, chapter lists with volumes and decimals, and
  every page of a chapter from the public desktop reader. The reader's packed
  scripts are decoded as text, never executed; images use the chapter page as
  Referer. Series links from fanfox.net and m.fanfox.net are accepted. Checked live
  from the Unraid server on 2026-11-04: search, a 469-chapter list (1–240 with
  decimals and volumes) and a full 12-page chapter download. FanFox's mobile site
  shows "licensed and not available" for some titles while its desktop reader
  still serves them; Mangarr uses whatever the desktop reader serves.
- Manganato.gg: paginated chapter API (no 50-chapter cutoff), decimal chapter
  numbers, title search, and reader images. Live Red Blue chapter lookup found
  215 entries including decimals. Search and CDN download returned HTTP 403 from
  the test network; this source is experimental and not verified end to end.
- Toomics: English series links, search parser, explicit free episodes only, and
  reader images. VIP/coin episodes are excluded. An optional masked session-cookie
  setting uses the user's existing Toomics session. To use Family Safe off,
  complete age verification and disable it on the Toomics website first. Mangarr
  does not bypass verification or paid access. HTTP requests returned 404 from the
  test network despite working browser pages; live search/download remain unverified.
  Never paste session cookies into issues or logs.

These adapters have local parser/routing tests. Unraid network verification is
still required before treating the experimental sources as fully working.
