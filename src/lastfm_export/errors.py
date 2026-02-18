from dataclasses import dataclass
from typing import Any


class LastFMExportError(Exception):
    """Base exception for lastfm-export."""


class ConfigError(LastFMExportError):
    """Raised when configuration is invalid or missing."""


class RateLimitError(LastFMExportError):
    """Raised when the remote API rate-limits and we cannot recover."""


@dataclass(slots=True)
class HttpRequestError(LastFMExportError):
    """
    Raised when an HTTP request fails after retries.

    Keep this small and stable: callers should not need to know the underlying HTTP library.
    """

    method: str
    url: str
    status_code: int | None = None
    message: str | None = None
    response_text: str | None = None
    payload: dict[str, Any] | None = None

    def __str__(self) -> str:
        parts: list[str] = [f"{self.method} {self.url}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.message:
            parts.append(self.message)
        return " | ".join(parts)
