# Initial fork review

Reviewed the checkout starting at `121dbd1`. This is a source review and local
test run, not a review of the running Unraid installation.

## Status (2026-11-03)

| # | Item | State |
| - | ---- | ----- |
| 1 | Fork-owned image, compose, template | Done: compose, Unraid template, CA profile, README and User-Agent point to `ghcr.io/shedman214/mangarr` / this repository |
| 2 | Tests gate publishing | Done: `test-image.yml` publishes only after `tests.yml` passes |
| 3 | Built-in login | Not started — design to be agreed first (changes network access) |
| 4 | Backup and restore | Done, including the automatic backup before database upgrades (`MANGARR_PREMIGRATION_BACKUP`) and a saved-backups list |
| 5 | Source failures in the interface | Done: source status table, per-link check, search warnings |
| 6 | Frontend behavior tests | Done: `npm test` (Vitest, jsdom) runs in CI; queue removal now asks for confirmation |

The original review follows unchanged.

## Improvements worth doing next

1. **Make releases belong to this fork.** `docker-compose.yml` and
   `templates/mangarr.xml` still install `kirantheram/mangarr:latest`.
   The Unraid template's project, support, README, and icon URLs also point
   upstream. Configure this fork's image publication, verify the image, and
   update the install instructions and template together. Until then, using
   the existing template will not install this fork's changes.
2. **Require tests to pass before publishing an image.**
   `.github/workflows/dockerhub.yml` builds and publishes independently of
   `.github/workflows/tests.yml`. A push can therefore publish while tests
   fail. Gate publication on successful backend tests and frontend builds.
3. **Add a built-in login option.** `backend/mangarr/main.py` exposes the
   web UI's master API key through public `/initialize.json`. The file
   explicitly describes a trusted-LAN model, rather than user login.
   Authentication should protect bootstrap and the API together; adding
   another API key alone would not change this behavior.
4. **Add backup and restore in Settings.** The README describes manual
   SQLite backups, but the current settings and system routes do not provide
   a backup workflow. An online SQLite backup, configuration export, and
   validated restore would make upgrades and recovery easier. Include an
   automatic backup before database migrations in `backend/mangarr/db.py`.
5. **Explain source failures in the interface.** Source cooldowns and
   failures are recorded in `backend/mangarr/jobs/tasks.py`, while the
   settings interface mainly exposes enable, priority, and proxy controls.
   Show the last successful check, latest failure, and next retry time so
   users can distinguish an unavailable site from an unmatched title.
6. **Add frontend behavior tests.** `frontend/package.json` and the test
   workflow build the interface, but have no frontend test command. Cover
   source linking, failed searches, queue retries, and destructive-action
   confirmation. These complement the existing backend regression suite.

## Changes requested during this review

- Source editing accepts a title or a supported series URL. URLs identify
  the source automatically and appear for confirmation before saving.
- An experimental Toonily adapter is registered in the source settings,
  including priority and optional content-proxy controls. It starts disabled.
  Per-image chapter Referers are carried in private URL fragments to avoid
  cross-chapter state during concurrent downloads. Existing image-signature
  detection stores WebP bytes as `.webp` even when the CDN URL ends in `.jpg`.

## Validation limits

- Direct Toonily search, series, and chapter requests from this machine
  returned HTTP 403. Parser and download-header tests use synthetic HTML and
  mocked HTTP responses. A real download from the Unraid network remains
  necessary before treating the adapter as verified.
- The full backend suite cannot collect unchanged on Windows because
  `tests/test_library_lost_mid_download.py` calls Linux-only `os.geteuid()`.
  Excluding that module produced **720 passing tests and six Windows-related
  failures**: five Unix file-mode assertions and one symlink privilege error.
  The focused source-link, Toonily, proxy, and automation run passed all
  **79 tests**. The frontend production build also passed.
