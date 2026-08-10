from io import StringIO

from lastfm_export.cli.progress import ProgressReporter


class _Stream(StringIO):
    def __init__(self, *, interactive: bool) -> None:
        super().__init__()
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


def test_auto_progress_is_quiet_for_non_interactive_streams():
    stream = _Stream(interactive=False)
    reporter = ProgressReporter("auto", stream=stream)

    reporter.start("Starting")
    reporter.update("Working")
    reporter.finish("Completed")

    assert stream.getvalue() == ""


def test_off_progress_is_quiet_for_interactive_streams():
    stream = _Stream(interactive=True)
    reporter = ProgressReporter("off", stream=stream)

    reporter.start("Starting")
    reporter.update("Working")
    reporter.finish("Completed")

    assert stream.getvalue() == ""


def test_on_progress_uses_normal_lines_and_throttles_non_interactive_streams():
    stream = _Stream(interactive=False)
    now = [0.0]
    reporter = ProgressReporter(
        "on", stream=stream, clock=lambda: now[0], interval_seconds=15
    )

    reporter.start("Starting")
    reporter.update("Working 1")
    now[0] = 10
    reporter.update("Working 2")
    now[0] = 15
    reporter.update("Working 3")
    reporter.finish("Completed")

    assert stream.getvalue().splitlines() == [
        "Starting",
        "Working 1",
        "Working 3",
        "Completed",
    ]
    assert "\r" not in stream.getvalue()


def test_auto_progress_uses_a_live_line_for_interactive_streams():
    stream = _Stream(interactive=True)
    reporter = ProgressReporter("auto", stream=stream)

    reporter.start("Starting")
    reporter.update("Working")
    reporter.milestone("Completed 2026 Q2")
    reporter.finish("Completed")

    assert stream.getvalue() == "Starting\n\rWorking\nCompleted 2026 Q2\nCompleted\n"
