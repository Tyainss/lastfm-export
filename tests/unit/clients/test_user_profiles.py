import pytest
import responses

from lastfm_export.clients.http import HttpClient, RetryConfig
from lastfm_export.clients.lastfm import LastFMClient
from lastfm_export.errors import ConfigError, HttpRequestError

URL = "https://ws.audioscrobbler.com/2.0/"
OPERATIONS = ["friends", "top_artists"]


def client(**kwargs):
    return LastFMClient(
        api_key="private-key",
        username="Listener",
        user_agent="tests",
        **kwargs,
    )


def records(operation, **kwargs):
    return getattr(client(), f"iter_user_{operation}")(**kwargs)


def item(name, operation):
    result = {"name": name}
    if operation == "top_artists":
        result.update(playcount="12", mbid="", **{"@attr": {"rank": "1"}})
    return result


def payload(operation, items, *, page=1, pages=1, total=None):
    container, field = (
        ("friends", "user") if operation == "friends" else ("topartists", "artist")
    )
    return {
        container: {
            field: items,
            "@attr": {
                "user": "listener",
                "page": str(page),
                "totalPages": str(pages),
                "total": str(len(items) if total is None else total),
            },
        }
    }


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_pagination_preserves_names_order_and_raw(operation):
    first = [item("Björk", operation), item("A", operation)]
    last = item("Same spelling", operation)
    responses.get(URL, json=payload(operation, first, pages=2, total=3))
    responses.get(URL, json=payload(operation, last, page=2, pages=2, total=3))

    result = list(records(operation, page_size=2))

    assert [record.raw for record in result] == [*first, last]
    name_field = "username" if operation == "friends" else "artist_name"
    assert [getattr(record, name_field) for record in result] == [
        "Björk",
        "A",
        "Same spelling",
    ]
    for page, call in enumerate(responses.calls, 1):
        params = call.request.params
        assert params["page"] == str(page)
        assert params["limit"] == "2"
        assert params["user"] == "Listener"
        assert params["method"] == (
            "user.getFriends" if operation == "friends" else "user.getTopArtists"
        )
        if operation == "friends":
            assert params["recenttracks"] == "0"
        else:
            assert params["period"] == "overall"
            assert result[0].playcount == 12
            assert result[0].rank == 1
            assert result[0].mbid is None


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_limit_stops_without_requesting_remaining_pages(operation):
    responses.get(
        URL,
        json=payload(
            operation, [item("A", operation), item("B", operation)], pages=3, total=6
        ),
    )
    responses.get(
        URL,
        json=payload(
            operation,
            [item("C", operation), item("D", operation)],
            page=2,
            pages=3,
            total=6,
        ),
    )
    assert len(list(records(operation, page_size=2, limit=3))) == 3
    assert len(responses.calls) == 2
    assert [call.request.params["limit"] for call in responses.calls] == ["2", "2"]


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_limit_reduces_request_size(operation):
    responses.get(
        URL, json=payload(operation, [item("A", operation)], pages=10, total=10)
    )
    assert len(list(records(operation, page_size=50, limit=1))) == 1
    assert responses.calls[0].request.params["limit"] == "1"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("pages", [0, 1])
@responses.activate
def test_empty_result(operation, pages):
    responses.get(URL, json=payload(operation, [], pages=pages))
    assert list(records(operation)) == []


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("bad", [None, {}, {"name": " "}, {"name": 7}])
@responses.activate
def test_validates_whole_page_before_yielding(operation, bad):
    responses.get(URL, json=payload(operation, [item("A", operation), bad]))
    with pytest.raises(HttpRequestError):
        next(records(operation, limit=1))


@pytest.mark.parametrize(
    "field, value",
    [
        ("playcount", None),
        ("playcount", -1),
        ("playcount", True),
        ("playcount", "1.5"),
        ("playcount", 1.5),
        ("mbid", 7),
        ("@attr", []),
        ("@attr", {"rank": "0"}),
        ("@attr", {"rank": True}),
        ("@attr", {"rank": -1}),
    ],
)
@responses.activate
def test_invalid_artist_fields(field, value):
    artist = item("A", "top_artists")
    artist[field] = value
    responses.get(URL, json=payload("top_artists", [artist]))
    with pytest.raises(HttpRequestError):
        list(records("top_artists"))


@responses.activate
def test_optional_artist_fields_and_period():
    responses.get(URL, json=payload("top_artists", [{"name": "A", "playcount": 0}]))
    result = list(records("top_artists", period="7day"))
    assert result[0].playcount == 0
    assert result[0].rank is None
    assert result[0].mbid is None
    assert responses.calls[0].request.params["period"] == "7day"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "field, value",
    [
        ("total", True),
        ("total", "-1"),
        ("totalPages", "1.5"),
        ("totalPages", 0),
        ("page", 2),
        ("user", "someone-else"),
    ],
)
@responses.activate
def test_invalid_pagination_metadata(operation, field, value):
    data = payload(operation, [item("A", operation)])
    next(iter(data.values()))["@attr"][field] = value
    responses.get(URL, json=data)
    with pytest.raises(HttpRequestError):
        list(records(operation))


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("failure", ["changed", "empty", "repeated", "short"])
@responses.activate
def test_incomplete_or_unstable_pagination_fails(operation, failure):
    responses.get(
        URL, json=payload(operation, [item("A", operation)], pages=2, total=2)
    )
    second = payload(operation, [item("B", operation)], page=2, pages=2, total=2)
    block = next(iter(second.values()))
    field = "user" if operation == "friends" else "artist"
    if failure == "changed":
        block["@attr"]["total"] = "3"
    elif failure == "empty":
        block[field] = []
    elif failure == "repeated":
        block[field] = [item("A", operation)]
    else:
        # A stable but incorrect advertised count must fail at the final page.
        responses.reset()
        responses.get(
            URL, json=payload(operation, [item("A", operation)], pages=2, total=3)
        )
        block["@attr"]["total"] = "3"
    responses.get(URL, json=second)
    iterator = records(operation)
    next(iterator)
    with pytest.raises(HttpRequestError):
        next(iterator)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "failure", ["container", "metadata", "total", "records", "missing_records"]
)
@responses.activate
def test_malformed_response_is_not_an_empty_result(operation, failure):
    data = payload(operation, [])
    block = next(iter(data.values()))
    field = "user" if operation == "friends" else "artist"
    if failure == "container":
        data = {}
    elif failure == "metadata":
        block["@attr"] = None
    elif failure == "total":
        del block["@attr"]["total"]
    elif failure == "records":
        block[field] = ""
    else:
        del block[field]
    responses.get(URL, json=data)
    with pytest.raises(HttpRequestError):
        list(records(operation))


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_preserves_duplicate_records_without_capping_page_size(operation):
    duplicate = item("A", operation)
    responses.get(URL, json=payload(operation, [duplicate, duplicate]))
    result = list(records(operation, page_size=500))
    assert len(result) == 2
    assert result[0] == result[1]
    assert responses.calls[0].request.params["limit"] == "500"


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": 0},
        {"page_size": True},
        {"page_size": 1.5},
        {"limit": 0},
        {"limit": True},
        {"limit": -1},
    ],
)
@responses.activate
def test_invalid_arguments_make_no_requests(operation, kwargs):
    with pytest.raises(ValueError):
        list(records(operation, **kwargs))
    assert not responses.calls


@pytest.mark.parametrize("period", ["weekly", None, []])
def test_invalid_period(period):
    with pytest.raises(ValueError):
        list(records("top_artists", period=period))


@pytest.mark.parametrize("operation", OPERATIONS)
def test_username_is_required(operation):
    missing_user = LastFMClient(api_key="key", user_agent="tests")
    with pytest.raises(ConfigError):
        list(getattr(missing_user, f"iter_user_{operation}")())


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_transient_provider_error_is_retried(operation):
    responses.get(URL, json={"error": 29, "message": "Try again"})
    responses.get(URL, json=payload(operation, []))
    transport = HttpClient(
        user_agent="tests",
        retry=RetryConfig(max_attempts=2, backoff_base_secs=0, jitter_ratio=0),
    )
    assert list(getattr(client(http=transport), f"iter_user_{operation}")()) == []
    assert len(responses.calls) == 2


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_permanent_provider_error_is_redacted(operation):
    responses.get(
        URL,
        json={"error": 10, "message": "Invalid private-key", "api_key": "private-key"},
    )
    with pytest.raises(HttpRequestError) as caught:
        list(records(operation))
    assert caught.value.fatal
    assert "private-key" not in str(caught.value)
    assert "private-key" not in repr(caught.value.payload)
    assert len(responses.calls) == 1


@pytest.mark.parametrize("status", [200, 400])
@responses.activate
def test_empty_friends_error_confirms_user_before_returning_empty(status):
    responses.get(URL, status=status, json={"error": 6, "message": "no such page"})
    responses.get(URL, json={"user": {"name": "listener"}})
    assert list(records("friends")) == []
    assert len(responses.calls) == 2
    assert responses.calls[1].request.params["method"] == "user.getinfo"


@responses.activate
def test_missing_user_is_not_treated_as_empty_friends():
    responses.get(URL, status=400, json={"error": 6, "message": "no such page"})
    responses.get(URL, status=400, json={"error": 6, "message": "User not found"})
    with pytest.raises(HttpRequestError) as caught:
        list(records("friends"))
    assert caught.value.provider_code == 6
    assert caught.value.payload["message"] == "User not found"


@pytest.mark.parametrize("user", [{"name": "other"}, {}, {"name": None}])
@responses.activate
def test_unconfirmed_identity_is_not_treated_as_empty_friends(user):
    responses.get(URL, status=400, json={"error": 6, "message": "no such page"})
    responses.get(URL, json={"user": user})
    with pytest.raises(HttpRequestError) as caught:
        list(records("friends"))
    assert caught.value.payload["message"] == "no such page"


@responses.activate
def test_later_missing_friends_page_remains_an_error():
    responses.get(
        URL, json=payload("friends", [item("A", "friends")], pages=2, total=2)
    )
    responses.get(URL, status=400, json={"error": 6, "message": "no such page"})
    iterator = records("friends", page_size=1)
    assert next(iterator).username == "A"
    with pytest.raises(HttpRequestError):
        next(iterator)
    assert len(responses.calls) == 2


@pytest.mark.parametrize("operation", OPERATIONS)
@responses.activate
def test_other_invalid_parameters_do_not_trigger_empty_friends_handling(operation):
    responses.get(URL, status=400, json={"error": 6, "message": "Invalid parameters"})
    with pytest.raises(HttpRequestError):
        list(records(operation))
    assert len(responses.calls) == 1


@responses.activate
def test_empty_page_error_does_not_hide_user_verification_failure():
    responses.get(URL, status=400, json={"error": 6, "message": "no such page"})
    responses.get(URL, json={"error": 10, "message": "Invalid private-key"})
    with pytest.raises(HttpRequestError) as caught:
        list(records("friends"))
    assert caught.value.fatal
    assert "private-key" not in str(caught.value)
    assert "private-key" not in repr(caught.value.payload)


@responses.activate
def test_top_artists_error_is_not_treated_as_empty_friends():
    responses.get(URL, status=400, json={"error": 6, "message": "no such page"})
    with pytest.raises(HttpRequestError):
        list(records("top_artists"))
    assert len(responses.calls) == 1


@responses.activate
def test_server_error_is_not_treated_as_empty_friends():
    responses.get(URL, status=503, json={"error": 6, "message": "no such page"})
    with pytest.raises(HttpRequestError):
        list(records("friends"))
    assert len(responses.calls) == 1
