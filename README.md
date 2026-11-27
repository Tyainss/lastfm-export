# lastfm-export

Export your **Last.fm** listening history and collect music information for your own analysis.

- Save your scrobbles as CSV, JSON or NDJSON.
- Collect artist information from Last.fm and MusicBrainz.
- Add Spotify track information to an existing export.

Use it from the command line or in Python.

## Quick Start

### 1. Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't already have it. It takes care of Python and the package installation for you.

**Windows (PowerShell):**

```powershell
winget install --id=astral-sh.uv -e
```

**macOS / Linux (Terminal):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install the tool (reopen your terminal if `uv` isn't found):

```bash
uv tool install lastfm-export
```

### 2. Configure

[Get a Last.fm API key](https://www.last.fm/api/account/create) and run the commands below, replacing the example values with your own.

From December 1, 2026, the API key owner needs an active Last.fm Pro subscription to export listening history ([API changes](https://support.last.fm/t/api-update-pro-will-not-be-required-to-use-scrobbling-methods/121188)).

**Windows (PowerShell):**

```powershell
$env:LASTFM_API_KEY = "your-api-key"
$env:LASTFM_USERNAME = "your-username"
```

**macOS / Linux:**

```bash
export LASTFM_API_KEY="your-api-key"
export LASTFM_USERNAME="your-username"
```

### 3. Export your listening history

In the same terminal, run:

```bash
lastfm-export scrobbles export --out scrobbles.csv
```

You'll find `scrobbles.csv` in your current folder, ready to open in Excel or any spreadsheet application.

Run the same command again to add new scrobbles to your existing export. Repeat step 2 if you've opened a new terminal.

## Export options

Exports check Last.fm's responses by default and stop if they detect inconsistent data.

To export a particular date range (both dates included, in UTC):

```bash
lastfm-export scrobbles export --out july.csv --from 2026-07-01 --to 2026-07-31
```

Choose the output format through the filename (`.csv`, `.json` or `.ndjson`). Add `--include-raw` to keep the original API data alongside the exported fields.

## Collect artist information

Collect Last.fm tags and similar artists for a list of names. Only `LASTFM_API_KEY` is needed.

Create `artists.json`:

```json
[
  {"artist_name": "Radiohead"},
  {"artist_name": "Portishead"}
]
```

```bash
lastfm-export enrich artists --in artists.json --out artist_metadata
```

Results are saved in `artist_metadata/results.ndjson`, including the original API responses. Input can also be CSV or NDJSON.

To continue an interrupted collection:

```bash
lastfm-export enrich artists --out artist_metadata --resume
```

Add `--retry-failed` to retry failed requests. Use a new output folder to refresh completed results.

### MusicBrainz (optional)

Collect artist genres, tags, country and other details. Identify your application with a name, version and contact email or URL:

```bash
export MUSICBRAINZ_USER_AGENT="my-music-app/1.0 (you@example.com)"
lastfm-export enrich artists --in artists.json --out artist_search --operation musicbrainz_search
```

To collect details for a match, add its MusicBrainz artist ID as `mbid` in the input record and use `--operation musicbrainz_lookup` with a new output folder.

## Add Spotify information

Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and set its credentials:

```bash
export SPOTIFY_CLIENT_ID="your-client-id"
export SPOTIFY_CLIENT_SECRET="your-client-secret"
```

```bash
lastfm-export enrich spotify --in scrobbles.csv --out scrobbles_spotify.csv
```

PS: Some tracks may not match.

## Use from Python

Requires Python 3.10+.

To add the package to your project:
```bash
uv add lastfm-export
```

```python
import os

from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.pipelines.lastfm_export import export_scrobbles

lastfm = LastFMClient(
    api_key=os.environ["LASTFM_API_KEY"],
    username=os.getenv("LASTFM_USERNAME"),
    user_agent="my-music-app",
)

for listen in export_scrobbles(lastfm=lastfm):
    print(listen.to_record())
```

This example does not perform the CLI's completeness checks.

Using the same client, collect artist information:

```python
from pathlib import Path

from lastfm_export.models import ArtistInput
from lastfm_export.pipelines.artist_metadata import collect_artist_metadata

summary = collect_artist_metadata(
    artists=[ArtistInput("Portishead")],
    lastfm=lastfm,
    out=Path("artist_metadata"),
)
print(summary)
```

## Help and contributing

Use `lastfm-export --help` or add `--help` to any command to see its options.

If Last.fm denies access, check that **Hide recent listening information** is disabled in your [privacy settings](https://www.last.fm/settings/privacy).

If you have any questions or suggestions, feel free to [open an issue](https://github.com/Tyainss/lastfm-export/issues)!

For development setup and contribution guidelines, see [CONTRIBUTING.md](https://github.com/Tyainss/lastfm-export/blob/main/CONTRIBUTING.md).

## License

[MIT](https://github.com/Tyainss/lastfm-export/blob/main/LICENSE).
