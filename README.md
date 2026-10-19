# lastfm-export

Export your **Last.fm** listening history and collect music information for your own analysis.

- Save your recorded listens (scrobbles) as CSV, JSON or NDJSON.
- Get artist tags and similar artists from Last.fm, or artist details from MusicBrainz.
- Add Spotify track information to an existing export.

Use it from the command line or in Python.

## Install

Requires Python 3.10+. Install the command-line tool with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install lastfm-export
```

Optional `.env` support is available by installing `lastfm-export[dotenv]` instead.
For use in a Python project, see [Use from Python](#use-from-python).

## Export listening history

[Get a Last.fm API key](https://www.last.fm/api/account/create), then set your key and username as environment variables:

```bash
export LASTFM_API_KEY="your-api-key"
export LASTFM_USERNAME="your-username"
```

Export your listening history:

```bash
lastfm-export scrobbles export --out scrobbles.csv
```

The file contains the artist, track, album and listening time for each recorded listen.
Run the same command again to add new listens to the existing export.

Exports check Last.fm's responses by default and stop if they find inconsistent data.
If Last.fm denies access, check that **Hide recent listening information** is disabled in your Last.fm privacy settings.

To export a particular date range:

```bash
lastfm-export scrobbles export --out july.csv --from 2026-07-01 --to 2026-07-31
```

Dates use UTC and include both the start and end date.

## Collect artist information

Collect Last.fm tags and similar artists for a list of names. This needs only `LASTFM_API_KEY` as an environment variable.

Create `artists.json`:

```json
[
  {"artist_name": "Radiohead"},
  {"artist_name": "Portishead"}
]
```

Then run:

```bash
lastfm-export enrich artists --in artists.json --out artist_metadata
```

The collected data is saved in `artist_metadata/results.ndjson`. Use a new output folder for each collection.

To continue an interrupted collection:

```bash
lastfm-export enrich artists --out artist_metadata --resume
```

Add `--retry-failed` to retry failed requests when resuming. To refresh a finished collection, choose a new output folder.

### MusicBrainz (optional)

MusicBrainz provides artist genres, tags, country and other details. Identify your application with a name, version and contact email or URL:

```bash
export MUSICBRAINZ_USER_AGENT="my-music-app/1.0 (you@example.com)"
lastfm-export enrich artists --in artists.json --out artist_search --operation musicbrainz_search
```

Search returns possible matches for you to check. To collect details for a chosen match, add its MusicBrainz artist ID as `mbid` in each input record and use `--operation musicbrainz_lookup` with a new output folder.

## Add Spotify information

Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and set its credentials:

```bash
export SPOTIFY_CLIENT_ID="your-client-id"
export SPOTIFY_CLIENT_SECRET="your-client-secret"
```

Add Spotify information to your exported listens:

```bash
lastfm-export enrich spotify --in scrobbles.csv --out scrobbles_spotify.csv
```

Some tracks may not match. Spotify credentials are needed only for this command.

## File formats

| Format | Useful for |
| --- | --- |
| CSV | Spreadsheets and tabular analysis |
| JSON | A single list of records |
| NDJSON | One JSON record per line, convenient for processing records individually |

By default, scrobble and Spotify exports use the format indicated by the output filename.
Artist collection accepts these three input formats and saves results as NDJSON.

Add `--include-raw` to scrobble or Spotify commands to keep the original API data alongside the exported fields. Artist collection always keeps the source responses.

## Use from Python

Add the package to your project:

```bash
uv add lastfm-export
```

Create a client using the environment variables set above:

```python
import os

from lastfm_export.clients.lastfm import LastFMClient

lastfm = LastFMClient(
    api_key=os.environ["LASTFM_API_KEY"],
    username=os.getenv("LASTFM_USERNAME"),
    user_agent="my-music-app",
)
```

Read listens directly. This simple Python export does not perform the CLI's completeness checks:

```python
from lastfm_export.pipelines.lastfm_export import export_scrobbles

for listen in export_scrobbles(lastfm=lastfm):
    print(listen.to_record())
```

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

See the available commands and options:

```bash
lastfm-export --help
lastfm-export scrobbles export --help
lastfm-export enrich artists --help
lastfm-export enrich spotify --help
```

For development setup and contribution guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
