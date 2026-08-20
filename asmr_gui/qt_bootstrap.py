"""Make Qt loadable before anything imports PySide6.

Qt6Core.dll links against Windows' own ICU by the unversioned name
``icuuc.dll``.  Conda/Anaconda installs ship an ICU build of their own under
``<prefix>\\Library\\bin`` with versioned symbols (``ucnv_open_73``), and that
directory sits on the DLL search path ahead of ``System32`` whenever the
interpreter comes from such an install.  The loader then hands Qt the wrong
``icuuc.dll`` and every PySide6 import dies with a bare
``ImportError: DLL load failed while importing QtCore`` (WinError 127).

Loading the system copy first pins the name for the rest of the process, which
costs nothing when the search path was already correct.  This has to run before
the first ``import PySide6``, so :mod:`asmr_gui` calls it at package import.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_done = False


def preload_system_icu() -> Path | None:
    """Pin ``icuuc.dll`` to the copy in ``System32``.  Returns the path used."""
    global _done
    if _done or sys.platform != "win32":
        _done = True
        return None
    _done = True
    # os.environ upper-cases its keys on Windows, so SYSTEMROOT finds SystemRoot.
    system_root = os.environ.get("SYSTEMROOT") or "C:\\Windows"
    library = Path(system_root) / "System32" / "icuuc.dll"
    if not library.is_file():
        # Windows older than 10 1703 has no system ICU; Qt cannot run there
        # anyway, so let the real import produce the real error.
        return None
    try:
        ctypes.WinDLL(str(library))
    except OSError:
        return None
    return library
