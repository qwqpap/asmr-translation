"""Cross-platform Qt front-end for the ASMR translation pipeline.

The GUI runs inside the same interpreter as :mod:`asmr_lrc`, so there is no IPC
layer: pages call :mod:`asmr_lrc.session` directly and background work happens in
``QThread`` workers.  The JSONL protocol in :mod:`asmr_lrc.gui_worker` remains for
the native Windows build and for scripting.
"""

from __future__ import annotations

from .qt_bootstrap import preload_system_icu

# Runs before any submodule can pull in PySide6; see qt_bootstrap for why.
preload_system_icu()

__all__ = ["main", "preload_system_icu"]


def main(argv: list[str] | None = None) -> int:
    """Entry point that defers importing Qt until it is actually needed."""
    from .app import main as run

    return run(argv)
