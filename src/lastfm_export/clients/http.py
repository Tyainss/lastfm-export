import random
import time
from dataclasses import dataclass
from typing import Any

import requests

from lastfm_export.errors import HttpRequestError, RateLimitError


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 5
    backoff_base_secs: float = 0.5
    backoff_max_secs: float = 10.0
    jitter_ratio: float = 0.2
    max_retry_after_secs: int = 120


class HttpClient:
    """
    Small sync HTTP client with sensible retry behavior.

    Design goals:
    - Stable surface for the package (clients should not expose requests directly)
    - Handles transient errors + rate limits in one place
    - Keeps dependencies minimal
    """

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
        self._base_headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request_json("GET", url, params=params, headers=headers)

    def post_json(
        self,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request_json("POST", url, params=params, headers=headers, data=data)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged_headers = dict(self._base_headers)
        if headers:
            merged_headers.update(headers)

        last_error: Exception | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=merged_headers,
                    timeout=self._timeout_secs,
                )

                # Rate limit handling
                if resp.status_code == 429:
                    retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                    if retry_after is None:
                        retry_after = self._compute_backoff(attempt)
                    retry_after = min(float(retry_after), float(self._retry.max_retry_after_secs))

                    if attempt >= self._retry.max_attempts:
                        raise RateLimitError(f"Rate limited (429) and max attempts reached: {method} {url}")

                    time.sleep(retry_after)
                    continue

                # Retry on transient 5xx
                if 500 <= resp.status_code <= 599:
                    if attempt >= self._retry.max_attempts:
                        raise HttpRequestError(
                            method=method,
                            url=url,
                            status_code=resp.status_code,
                            message="Server error (5xx) after retries",
                            response_text=_safe_text(resp),
                        )
                    time.sleep(self._compute_backoff(attempt))
                    continue

                # Non-success
                if not (200 <= resp.status_code <= 299):
                    raise HttpRequestError(
                        method=method,
                        url=url,
                        status_code=resp.status_code,
                        message="Non-success response",
                        response_text=_safe_text(resp),
                    )

                # Parse JSON
                try:
                    payload = resp.json()
                except ValueError as e:
                    raise HttpRequestError(
                        method=method,
                        url=url,
                        status_code=resp.status_code,
                        message="Invalid JSON response",
                        response_text=_safe_text(resp),
                    ) from e

                return payload

            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                if attempt >= self._retry.max_attempts:
                    raise HttpRequestError(
                        method=method,
                        url=url,
                        status_code=None,
                        message="Network error after retries",
                    ) from e
                time.sleep(self._compute_backoff(attempt))

            except requests.RequestException as e:
                # Other request-layer errors are generally not recoverable
                raise HttpRequestError(
                    method=method,
                    url=url,
                    status_code=None,
                    message="Request error",
                ) from e

        # Should never hit
        if last_error is not None:
            raise HttpRequestError(method=method, url=url, message="Request failed") from last_error
        raise HttpRequestError(method=method, url=url, message="Request failed")

    def _compute_backoff(self, attempt: int) -> float:
        # Exponential backoff: base * 2^(attempt-1), capped
        raw = self._retry.backoff_base_secs * (2 ** max(0, attempt - 1))
        capped = min(raw, self._retry.backoff_max_secs)

        # Add jitter: +/- jitter_ratio
        jitter = capped * self._retry.jitter_ratio
        return max(0.0, capped + random.uniform(-jitter, jitter))

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            # Retry-After is usually seconds for APIs
            return float(value)
        except ValueError:
            return None


def _safe_text(resp: requests.Response) -> str | None:
    try:
        txt = resp.text
        if txt is None:
            return None
        return txt[:2000]  # cap: keep error objects small
    except Exception:
        return None
