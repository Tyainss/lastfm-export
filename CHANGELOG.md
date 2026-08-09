# Changelog

## [Unreleased]


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