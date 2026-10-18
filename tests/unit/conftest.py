import pytest

from lastfm_export.models import ArtistMetadataResponse


class FakeLastFM:
    base_url = "https://ws.audioscrobbler.com/2.0/"
    api_key = "not-a-real-key"

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def _get(self, operation, kwargs):
        self.calls.append((operation, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if isinstance(outcome, BaseException):
            raise outcome
        return ArtistMetadataResponse(
            outcome, {"source": operation, "artist": kwargs["artist_name"]}, 200, 1
        )

    def get_artist_top_tags(self, **kwargs):
        return self._get("tags", kwargs)

    def get_similar_artists(self, **kwargs):
        return self._get("similar", kwargs)


@pytest.fixture
def metadata_client():
    return FakeLastFM
