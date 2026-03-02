import time

import pytest
import responses
import requests

from lastfm_export.clients.http import HttpClient, RetryConfig
from lastfm_export.errors import HttpRequestError, RateLimitError


@responses.activate
def test_get_json_retries_on_429_and_honors_retry_after(monkeypatch):
    url = "https://example.com/api"
    call_count = {"n": 0}

    def _callback(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (429, {"Retry-After": "0"}, "rate limited")
        return (200, {"Content-Type": "application/json"}, '{"ok": true}')

    responses.add_callback(responses.GET, url, callback=_callback, content_type="application/json")

    slept = {"secs": []}

    def fake_sleep(secs):
        slept["secs"].append(secs)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    client = HttpClient(
        user_agent="tests",
        retry=RetryConfig(max_attempts=3, backoff_base_secs=0.01, backoff_max_secs=0.02),
    )
    out = client.get_json(url)

    assert out == {"ok": True}
    assert call_count["n"] == 2
    assert slept["secs"] == [0.0]


@responses.activate
def test_get_json_retries_on_5xx_then_succeeds(monkeypatch):
    url = "https://example.com/api"
    responses.add(responses.GET, url, status=500, body="server error")
    responses.add(responses.GET, url, status=200, json={"ok": True})

    monkeypatch.setattr(time, "sleep", lambda *_: None)

    client = HttpClient(
        user_agent="tests",
        retry=RetryConfig(max_attempts=3, backoff_base_secs=0.01, backoff_max_secs=0.02),
    )
    out = client.get_json(url)

    assert out == {"ok": True}


@responses.activate
def test_get_json_raises_on_4xx_without_retry():
    url = "https://example.com/api"
    responses.add(responses.GET, url, status=404, body="not found")

    client = HttpClient(user_agent="tests", retry=RetryConfig(max_attempts=3))

    with pytest.raises(HttpRequestError) as exc:
        client.get_json(url)

    assert exc.value.status_code == 404
    assert len(responses.calls) == 1


@responses.activate
def test_get_json_raises_on_invalid_json():
    url = "https://example.com/api"
    responses.add(
        responses.GET,
        url,
        status=200,
        body="not-json",
        content_type="application/json",
    )

    client = HttpClient(user_agent="tests", retry=RetryConfig(max_attempts=1))

    with pytest.raises(HttpRequestError) as exc:
        client.get_json(url)

    assert exc.value.message == "Invalid JSON response"


def test_get_json_retries_on_timeout_then_succeeds(monkeypatch):
    url = "https://example.com/api"
    call_count = {"n": 0}

    session = requests.Session()

    def fake_request(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.Timeout("timeout")
        return _FakeResponse(status_code=200, json_payload={"ok": True})

    monkeypatch.setattr(session, "request", fake_request)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    client = HttpClient(
        user_agent="tests",
        session=session,
        retry=RetryConfig(max_attempts=2, backoff_base_secs=0.01, backoff_max_secs=0.02),
    )
    out = client.get_json(url)

    assert out == {"ok": True}
    assert call_count["n"] == 2


def test_get_json_raises_rate_limit_error_when_429_persists(monkeypatch):
    url = "https://example.com/api"
    call_count = {"n": 0}

    session = requests.Session()

    def fake_request(*args, **kwargs):
        call_count["n"] += 1
        return _FakeResponse(status_code=429, headers={"Retry-After": "0"}, text="rate limited")

    monkeypatch.setattr(session, "request", fake_request)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    client = HttpClient(
        user_agent="tests",
        session=session,
        retry=RetryConfig(max_attempts=2, backoff_base_secs=0.01, backoff_max_secs=0.02),
    )

    with pytest.raises(RateLimitError):
        client.get_json(url)

    assert call_count["n"] == 2


class _FakeResponse:
    def __init__(self, status_code, json_payload=None, headers=None, text=None):
        self.status_code = status_code
        self._json_payload = json_payload
        self.headers = headers or {}
        self._text = text

    def json(self):
        if self._json_payload is None:
            raise ValueError("no json")
        return self._json_payload

    @property
    def text(self):
        return self._text
