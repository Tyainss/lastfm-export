import sys
import time
from collections.abc import Callable
from typing import TextIO


class ProgressReporter:
    """Write compact command progress without changing normal command output."""

    def __init__(
        self,
        mode: str,
        *,
        interval_seconds: float = 15,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        normalized_mode = mode.lower()
        if normalized_mode not in {"auto", "on", "off"}:
            raise ValueError("progress mode must be 'auto', 'on', or 'off'")

        self._stream = stream or sys.stderr
        self._clock = clock
        self._interval_seconds = interval_seconds
        self._interactive = self._stream.isatty()
        self._enabled = normalized_mode == "on" or (
            normalized_mode == "auto" and self._interactive
        )
        self._last_update: float | None = None
        self._live_line = False

    def start(self, message: str) -> None:
        self._write_line(message)

    def update(self, message: str, *, force: bool = False) -> None:
        if not self._enabled:
            return
        now = self._clock()
        if not force and self._last_update is not None:
            if now - self._last_update < self._interval_seconds:
                return
        self._last_update = now

        if self._interactive:
            self._stream.write(f"\r{message}")
            self._stream.flush()
            self._live_line = True
            return
        self._write_line(message)

    def milestone(self, message: str) -> None:
        self._write_line(message)

    def finish(self, message: str) -> None:
        self._write_line(message)

    def close(self) -> None:
        if self._enabled and self._live_line:
            self._stream.write("\n")
            self._stream.flush()
            self._live_line = False

    def _write_line(self, message: str) -> None:
        if not self._enabled:
            return
        self.close()
        self._stream.write(f"{message}\n")
        self._stream.flush()
