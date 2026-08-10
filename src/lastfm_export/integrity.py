"""Integrity data structures for verified Last.fm exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lastfm_export.models import Scrobble


@dataclass(slots=True)
class WindowReport:
    from_unix: int
    to_unix: int
    api_total: int | None = None
    materialized_count: int = 0
    page_count: int = 0
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_record(self) -> dict[str, Any]:
        return {
            "from_unix": self.from_unix,
            "to_unix": self.to_unix,
            "api_total": self.api_total,
            "materialized_count": self.materialized_count,
            "page_count": self.page_count,
            "violations": self.violations,
        }


@dataclass(slots=True)
class WindowResult:
    scrobbles: list[Scrobble]
    report: WindowReport
