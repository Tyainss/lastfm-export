import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from lastfm_export.errors import ConfigError, HttpRequestError, RateLimitError


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 5
    backoff_base_secs: float = 0.5
    backoff_max_secs: float = 10.0
    jitter_ratio: float = 0.2
    max_retry_after_secs: int = 120

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if (
            min(
                self.backoff_base_secs,
                self.backoff_max_secs,
                self.jitter_ratio,
                self.max_retry_after_secs,
            )
            < 0
        ):
            raise ValueError("Retry delays and jitter must be nonnegative")


@dataclass(frozen=True, slots=True)
class JsonResponse:
    payload: Any
    status_code: int
    attempts: int


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Remove credentials while retaining the surrounding response structure."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if str(key).lower()
            in {
                "api_key",
                "apikey",
                "authorization",
                "access_token",
                "client_secret",
                "password",
                "token",
                "api_sig",
                "sk",
            }
            else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def validate_base_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(
            "API base URL must be HTTP(S) without credentials, query or fragment"
        )


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class HttpClient:
    """Synchronous JSON transport with one bounded retry budget per request."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_secs: float = 30.0,
        retry: RetryConfig | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._timeout_secs = float(timeout_secs)
        self._retry = retry or RetryConfig()
        self._session = session or requests.Session()
        self._base_headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.get_response(url, params=params, headers=headers).payload

    def post_json(
        self,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request_response(
            "POST", url, params=params, headers=headers, data=data
        ).payload

    def get_response(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        response_error: Callable[[JsonResponse], HttpRequestError | None] | None = None,
        before_request: Callable[[], None] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> JsonResponse:
        return self._request_response(
            "GET",
            url,
            params=params,
            headers=headers,
            response_error=response_error,
            before_request=before_request,
            secrets=secrets,
        )

    def _request_response(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        response_error: Callable[[JsonResponse], HttpRequestError | None] | None = None,
        before_request: Callable[[], None] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> JsonResponse:
        merged_headers = dict(self._base_headers)
        if headers:
            merged_headers.update(headers)
        error_secrets = secrets + tuple(
            str(value)
            for mapping in (params or {}, headers or {}, data or {})
            for key, value in mapping.items()
            if key.lower()
            in {"api_key", "authorization", "client_secret", "access_token"}
        )
        safe_url = _safe_url(url)
        for attempt in range(1, self._retry.max_attempts + 1):
            if before_request is not None:
                before_request()
            delay = self._compute_backoff(attempt)
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=merged_headers,
                    timeout=self._timeout_secs,
                )
            except (requests.Timeout, requests.ConnectionError):
                error = HttpRequestError(
                    method,
                    safe_url,
                    message="Network error after retries",
                    attempts=attempt,
                    retryable=True,
                )
            except requests.RequestException:
                raise HttpRequestError(
                    method,
                    safe_url,
                    message="Request error",
                    attempts=attempt,
                ) from None
            else:
                try:
                    payload = resp.json()
                    parsed = True
                except ValueError:
                    payload = None
                    parsed = False
                safe_payload = redact(payload, error_secrets)
                status = resp.status_code
                response = JsonResponse(redact(payload, secrets), status, attempt)
                error = response_error(response) if response_error and parsed else None
                if error is not None:
                    error.url = safe_url
                    error.payload = safe_payload
                    error.status_code = status
                    error.attempts = attempt
                    error.message = redact(error.message, error_secrets)
                if status == 429 or 500 <= status <= 599:
                    retry_after = self._parse_retry_after(
                        resp.headers.get("Retry-After")
                    )
                    if retry_after is not None:
                        delay = min(
                            retry_after, float(self._retry.max_retry_after_secs)
                        )
                    if error is None:
                        cls = RateLimitError if status == 429 else HttpRequestError
                        error = cls(
                            method,
                            safe_url,
                            status_code=status,
                            message="Rate limited (429)"
                            if status == 429
                            else "Server error (5xx) after retries",
                            payload=safe_payload,
                            attempts=attempt,
                            retryable=True,
                        )
                elif error is None and not 200 <= status <= 299:
                    error = HttpRequestError(
                        method,
                        safe_url,
                        status,
                        "Non-success response",
                        payload=safe_payload,
                        attempts=attempt,
                        fatal=status in {401, 403},
                    )
                elif error is None and not parsed:
                    error = HttpRequestError(
                        method,
                        safe_url,
                        status,
                        "Invalid JSON response",
                        attempts=attempt,
                    )
                if error is None:
                    return response
            if not error.retryable or attempt == self._retry.max_attempts:
                raise error from None
            time.sleep(delay)
        raise AssertionError("Retry budget must include at least one attempt")

    def _compute_backoff(self, attempt: int) -> float:
        raw = self._retry.backoff_base_secs * (2 ** max(0, attempt - 1))
        capped = min(raw, self._retry.backoff_max_secs)
        jitter = capped * self._retry.jitter_ratio
        return max(0.0, capped + random.uniform(-jitter, jitter))

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None
