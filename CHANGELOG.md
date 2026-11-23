# Changelog

## [Unreleased]

### Added

- Added `--dated-only` to exclude timestamps before 2002 from scrobble exports.

### Fixed

- Avoided scanning empty decades when verifying exports with pre-2002 records.

## [0.6.1] - 2026-11-19

### Fixed

- Recognized existing Spotify information in CSV files with `--only-missing`.
- Preserved existing CSV columns when adding new listens.
- Rejected invalid export date ranges before replacing output files.
- Included listens before registration in full command-line exports.
- Fixed in-place Spotify enrichment and preserved output files when enrichment fails.
- Avoided scanning empty years when listening history predates registration.
- Fixed verification failing when repeated listens cross page boundaries.
- Fixed collecting connections from users with an empty following list.

## [0.6.0] - 2026-11-18

### Added

- Added monthly listening-history collection in Python.
- Added optional delays between API requests.
- Added user connections and top artists in Python.

### Fixed

- Fixed exports missing listens at the requested end time.
- Fixed Last.fm errors being treated as empty listening histories.

## [0.5.0] - 2026-11-14

### Added

- Added artist biographies from Last.fm.

## [0.4.0] - 2026-10-19

### Added

- Added artist tags and similar artists from Last.fm, and artist details from MusicBrainz.
- Added artist collection through the command line and Python, with support for resuming interrupted runs and retrying failed requests.

### Fixed
- Fixed leftover text in terminal progress updates.

## [0.3.0] - 2026-08-11
### Added

- Added verified daily scrobble exports as the default.
- Added `--integrity-policy strict|warn` for verified exports.
- Added `--integrity-report auto|always|never`; detailed reports default to verification problems only.
- Added `--acquisition-mode fast` for quick, unverified exports.
- Added compact `--progress auto|on|off` feedback to scrobble exports and Spotify enrichment.

### Changed

- Exports now freeze the end time when they start and use the account registration date when no start date is given.
- NDJSON, JSON, and CSV exports now write safely before replacing the destination file.
- `--page-limit` is now available only in fast mode.

### Fixed

- Verified exports avoid a known Last.fm paging problem that can repeat or miss tracks.
- Verified exports report unexpected totals, empty pages, out-of-range tracks, timestamp order problems, and repeated page records.

## [0.2.0] - 2026-08-09
### Added
- Added optional per-track Last.fm and Spotify source payloads via `--include-raw` and `to_record(include_raw=True)`.


## [0.1.2] - 2026-07-13
### Fixed
- Added actionable CLI guidance when Last.fm denies access to hidden recent listening history.

### Changed
- Documented the Last.fm privacy setting required to export scrobbles.

## [0.1.1] - 2026-03-04
### Changed
- Release workflow now uses a GitHub Actions `pypi` environment approval gate for Trusted Publishing.

## [0.1.0] - 2026-03-01
### Added
- Last.fm scrobbles export (client + pipeline) with retries/backoff and pagination.
- Optional Spotify enrichment (client + pipeline) via track search.
- CLI commands:
  - `lastfm-export scrobbles export`
  - `lastfm-export enrich spotify`
- Output support: NDJSON (recommended), JSON, and CSV.
- Optional `.env` loading via the `dotenv` extra (`lastfm-export[dotenv]`).
