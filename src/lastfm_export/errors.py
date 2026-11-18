from dataclasses import dataclass
from typing import Any


class LastFMExportError(Exception):
    """Base exception for lastfm-export."""


class ConfigError(LastFMExportError):
    """Raised when configuration is invalid or missing."""


class LastFMRecentTracksAccessError(LastFMExportError):
    """Raised when Last.fm denies access to a user's recent listening history."""


class ScrobbleIntegrityError(LastFMExportError):
    """A bounded history window failed verification; its records are not usable."""

    def __init__(self, report: Any) -> None:
        self.report = report
        super().__init__(
            f"History window {report.from_unix}..{report.to_unix} failed verification: "
            + "; ".join(report.violations)
        )


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
    payload: Any = None
    attempts: int = 1
    retryable: bool = False
    provider_code: int | None = None
    fatal: bool = False

    def __str__(self) -> str:
        parts: list[str] = [f"{self.method} {self.url}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.message:
            parts.append(self.message)
        return " | ".join(parts)


class RateLimitError(HttpRequestError):
    """Raised when the remote API rate-limits and we cannot recover."""


class CollectionError(LastFMExportError):
    """Raised when a metadata collection cannot be read or written safely."""
