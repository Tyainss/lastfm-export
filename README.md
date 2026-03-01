# lastfm-export

Export your **Last.fm** listening history (scrobbles) and optionally **enrich** it with **Spotify** metadata.

Designed for both:

* **CLI-first users** who want “just get my data”, and
* **Python users** who want reusable clients and pipelines.

---

## Features

* Export scrobbles from Last.fm with paging + retry/backoff.
* Optional Spotify enrichment (track metadata lookup).
* Streaming-friendly outputs (NDJSON recommended).
* Works with environment variables (no secrets in code).

---

## Install

Using `uv`:

```bash
uv pip install lastfm-export
```

Or with `.env` support for local development:

```bash
uv pip install "lastfm-export[dotenv]"
```

---

## Authentication

### Last.fm

Set these environment variables:

```bash
export LASTFM_API_KEY="..."
export LASTFM_USERNAME="..."
```

### Spotify (optional)

Set these environment variables:

```bash
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."
```

If you installed `lastfm-export[dotenv]`, you can also put these values in a local `.env` file.

---

## Quickstart (CLI)

### 1) Export scrobbles

Recommended output format: **NDJSON** (one JSON object per line).

```bash
lastfm-export scrobbles export \
  --out scrobbles.ndjson
```

Optional filters:

```bash
lastfm-export scrobbles export \
  --out scrobbles.ndjson \
  --from 2026-01-01 \
  --to 2026-02-01
```

You can also pass full datetimes:

```bash
lastfm-export scrobbles export \
  --out scrobbles.ndjson \
  --from 2026-01-01T00:00:00 \
  --to 2026-02-01T23:59:59
```

Resume (incremental export):

```bash
lastfm-export scrobbles export \
  --out scrobbles.ndjson \
  --resume auto
```

### 2) Enrich with Spotify

```bash
lastfm-export enrich spotify \
  --in scrobbles.ndjson \
  --out scrobbles_enriched.ndjson
```

Notes:

* Spotify enrichment is best-effort: some tracks may not match cleanly.
* Use NDJSON for large exports.

---

## Quickstart (Python)

### Export scrobbles

```python
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.pipelines.lastfm_export import export_scrobbles

lastfm = LastFMClient(
    api_key="...",
    username="...",
    user_agent="lastfm-export",
)

for scrobble in export_scrobbles(lastfm=lastfm):
    print(scrobble.to_record())
```

### Enrich with Spotify

```python
from lastfm_export.clients.spotify import SpotifyClient
from lastfm_export.pipelines.spotify_enrich import enrich_scrobbles_with_spotify

spotify = SpotifyClient(
    client_id="...",
    client_secret="...",
    user_agent="lastfm-export",
)

enriched = enrich_scrobbles_with_spotify(spotify=spotify, scrobbles=export_scrobbles(lastfm=lastfm))
for row in enriched:
    print(row.to_record())
```

---

## Output formats

Supported formats depend on command:

* **NDJSON**: recommended for streaming and incremental runs.
* JSON array: convenient for small exports.
* CSV: convenient for spreadsheets.

Tip: For large histories, use NDJSON to avoid loading everything into memory.

---

## Development

Clone the repo and set up the environment with `uv`:

```bash
uv venv
uv sync
```

Run checks:

```bash
uv run ruff check .
uv run pytest -q
```

Build:

```bash
uv build --no-sources
```

---

## License

See `LICENSE`.
