"""Background work bridged onto Qt signals.

Every pipeline stage already reports through an ``event_callback``, so the whole
bridge is one adapter: run the blocking call in a ``QThread``, re-emit each event
dict as a signal, and let Qt deliver it to the GUI thread.  Nothing in the GUI
thread ever blocks on the pipeline, and cancellation stays cooperative via the
same :class:`~asmr_lrc.control.CancelToken` the CLI uses.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from asmr_lrc import session
from asmr_lrc.control import CancelledError, CancelToken
from asmr_lrc.pipeline import run_pipeline


class _Task(QThread):
    """A cancellable unit of work that reports through Qt signals."""

    event = Signal(dict)
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.token = CancelToken()

    def cancel(self) -> None:
        self.token.cancel()

    def _execute(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self) -> None:
        try:
            self._execute()
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(type(exc).__name__, str(exc))


class ProbeTask(_Task):
    """Check dependencies and models without installing anything."""

    finished_probe = Signal(dict)

    def __init__(self, config: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config

    def _execute(self) -> None:
        self.finished_probe.emit(session.probe(session.build_app_config(self._config)))


class PipelineTask(_Task):
    """Run the transcription/translation pipeline over one root."""

    finished_run = Signal(int, dict)
    consent_requested = Signal(int)

    def __init__(
        self,
        root: Path,
        config: dict[str, Any],
        *,
        dry_run: bool = False,
        transcribe_only: bool = False,
        translate_only: bool = False,
        keep_model: bool = False,
        consent: Callable[[int], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = root
        self._config = config
        self._dry_run = dry_run
        self._transcribe_only = transcribe_only
        self._translate_only = translate_only
        self._keep_model = keep_model
        self._consent = consent

    def _execute(self) -> None:
        report = run_pipeline(
            self._root,
            session.build_app_config(self._config),
            dry_run=self._dry_run,
            transcribe_only=self._transcribe_only,
            translate_only=self._translate_only,
            release_ollama=not self._keep_model,
            event_callback=lambda event: self.event.emit(dict(event)),
            cancel_token=self.token,
            quiet=True,
            external_consent_callback=self._consent,
        )
        self.finished_run.emit(
            report.exit_code,
            {
                "processed": getattr(report, "processed", 0),
                "skipped": getattr(report, "skipped", 0),
                "failed": getattr(report, "failed", 0),
            },
        )


class DownloadPlanTask(_Task):
    """Fetch work metadata; media URLs never leave this thread."""

    finished_plan = Signal(dict)

    def __init__(self, rj: str, download: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rj = rj
        self._download = download

    def _execute(self) -> None:
        config = session.build_download_config(self._download)
        self.token.raise_if_cancelled()
        self.finished_plan.emit(session.plan_download(self._rj, config))


class DownloadTask(_Task):
    """Download the selected files into ``root``."""

    finished_download = Signal(str)

    def __init__(
        self,
        rj: str,
        selected_ids: set[str],
        root: Path,
        download: dict[str, Any],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._rj = rj
        self._selected = set(selected_ids)
        self._root = root
        self._download = download

    def _execute(self) -> None:
        config = session.build_download_config(self._download)
        plan = session.resolve_download_plan(None, self._rj, config)

        def callback(event: dict[str, object]) -> None:
            # curl diagnostics can echo a signed media URL; those must not reach
            # the GUI log.
            event.pop("detail", None)
            self.event.emit(dict(event))

        target = session.run_download(
            plan, self._selected, self._root, config, token=self.token, callback=callback
        )
        self.finished_download.emit(str(target))


class PlaybackPrepareTask(_Task):
    """Render the PCM proxy the player needs, off the GUI thread."""

    finished_prepare = Signal(dict)

    def __init__(
        self,
        audio: Path,
        cache_root: Path,
        ffmpeg_path: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._audio = audio
        self._cache_root = cache_root
        self._ffmpeg = ffmpeg_path

    def _execute(self) -> None:
        result = session.prepare_playback(
            self._audio, self._cache_root, ffmpeg_path=self._ffmpeg
        )
        result["cues"] = session.load_cues(self._audio, self._cache_root)["cues"]
        self.finished_prepare.emit(result)
