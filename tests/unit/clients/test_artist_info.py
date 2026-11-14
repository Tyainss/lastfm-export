import pytest

from lastfm_export.clients.http import JsonResponse
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import HttpRequestError
from lastfm_export.io.artist_metadata import read_collection
from lastfm_export.models import ArtistInput, ArtistMetadataResponse
from lastfm_export.pipelines.artist_metadata import collect_artist_metadata


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.params = None

    def get_response(self, url, *, params, response_error, secrets):
        self.params = params
        assert secrets == ("private",)
        return JsonResponse(self.payload, 200, 1)


def test_artist_info_retains_raw_biography_and_uses_expected_parameters():
    payload = {
        "artist": {
            "name": "Silvana Estrada",
            "bio": {
                "summary": "Mexican folk",
                "content": "A longer <a>description</a>.",
            },
        }
    }
    http = FakeHttp(payload)
    client = LastFMClient(api_key="private", user_agent="tests", http=http)
    result = client.get_artist_info(artist_name="Silvana Estrada")
    assert result.status == "success"
    assert result.raw_response == payload
    assert http.params["method"] == "artist.getInfo"
    assert http.params["lang"] == "en"
    assert http.params["autocorrect"] == 0


def test_artist_info_empty_and_invalid_biographies():
    client = LastFMClient(
        api_key="private",
        user_agent="tests",
        http=FakeHttp({"artist": {"name": "Alpha", "bio": {}}}),
    )
    assert client.get_artist_info(artist_name="Alpha").status == "empty"
    client.http = FakeHttp({"artist": {"name": "Alpha", "bio": ["invalid"]}})
    with pytest.raises(HttpRequestError, match="Malformed artist biography"):
        client.get_artist_info(artist_name="Alpha")


def test_info_collection_reuses_existing_resumable_metadata_pipeline(tmp_path):
    class Client:
        api_key = "private"
        base_url = "https://ws.audioscrobbler.com/2.0/"

        def get_artist_info(self, *, artist_name, mbid, autocorrect, lang):
            assert lang == "en"
            assert not autocorrect
            return ArtistMetadataResponse(
                status="success",
                raw_response={
                    "artist": {
                        "name": artist_name,
                        "bio": {"summary": "Folk", "content": "More"},
                    }
                },
                http_status=200,
                wire_attempts=1,
            )

    out = tmp_path / "info"
    summary = collect_artist_metadata(
        out=out,
        artists=[ArtistInput("Alpha")],
        operations=["lastfm_info"],
        lastfm=Client(),
    )
    assert summary.completed and summary.success == 1
    manifest, results = read_collection(out)
    assert manifest["plan"]["settings"]["operations"] == ["lastfm_info"]
    request = manifest["plan"]["requests"][0]["request"]
    assert request["endpoint"] == "artist.getInfo"
    assert request["request_params"]["lang"] == "en"
    assert (
        next(iter(results.values())).raw_response["artist"]["bio"]["content"] == "More"
    )
