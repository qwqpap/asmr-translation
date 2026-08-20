"""Cross-platform subprocess-tree lifetime management.

The Win32 GUI wrapped every child in a Job Object with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so a cancelled or crashed run could never
leave an ASR process holding the GPU.  That guarantee matters just as much on
Linux, where a killed ``python -m asmr_lrc.asr_worker`` can otherwise leave CUDA
memory pinned until reboot.

Windows keeps the Job Object (now via ctypes).  POSIX gets the equivalent
guarantee from a new session plus ``killpg``, which reaches grandchildren that a
plain ``Popen.terminate()`` would miss.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

from .platform_paths import is_windows

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _windows_job_api():  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return ctypes, kernel, JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def spawn_kwargs() -> dict[str, Any]:
    """Popen keyword arguments that make the child the root of its own tree."""
    if is_windows():
        # A new process group lets us deliver CTRL_BREAK without hitting our own
        # process, and keeps the child out of the console's Ctrl+C broadcast.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class ProcessTree:
    """Owns a child process and everything it spawns.

    ``close()`` is idempotent and always safe to call from a cancellation path.
    """

    __slots__ = ("_process", "_job", "_ctypes", "_kernel", "_closed")

    def __init__(self, process: subprocess.Popen[Any]) -> None:
        self._process = process
        self._job = None
        self._ctypes = None
        self._kernel = None
        self._closed = False
        if is_windows():
            self._attach_job_object()

    def _attach_job_object(self) -> None:  # pragma: no cover - Windows only
        pid = getattr(self._process, "pid", None)
        if pid is None or self._process.poll() is not None:
            # Nothing to guard: the child already exited, or this is a stand-in
            # object from a test that never started a real process.
            return
        try:
            ctypes, kernel, extended = _windows_job_api()
        except OSError:
            return
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return
        limits = extended()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assigned = False
        handle = kernel.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if handle:
            assigned = bool(
                kernel.SetInformationJobObject(
                    job,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                )
                and kernel.AssignProcessToJobObject(job, handle)
            )
            kernel.CloseHandle(handle)
        if not assigned:
            kernel.CloseHandle(job)
            return
        self._job = job
        self._ctypes = ctypes
        self._kernel = kernel

    @property
    def process(self) -> subprocess.Popen[Any]:
        return self._process

    def terminate_tree(self) -> None:
        """Ask the whole tree to exit, preferring a graceful signal first."""
        if self._process.poll() is not None:
            return
        if is_windows():
            self._process.terminate()
            return
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            self._process.terminate()

    def kill_tree(self) -> None:
        """Unconditionally destroy the tree, including orphaned grandchildren."""
        if is_windows():
            if self._job is not None and self._kernel is not None:
                self._kernel.TerminateJobObject(self._job, 130)
            elif self._process.poll() is None:
                self._process.kill()
            return
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            if self._process.poll() is None:
                self._process.kill()

    def _wait(self, timeout: float) -> bool:
        """Wait for exit; return False if the process is still alive afterwards."""
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def stop(self, *, grace_seconds: float = 5.0) -> None:
        """Terminate, wait out the grace period, then kill whatever survives."""
        if self._process.poll() is not None:
            return
        self.terminate_tree()
        if self._wait(grace_seconds):
            return
        self.kill_tree()
        self._wait(grace_seconds)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        if self._job is not None and self._kernel is not None:  # pragma: no cover
            self._kernel.CloseHandle(self._job)
            self._job = None

    def __enter__(self) -> ProcessTree:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def popen_tree(command: list[str], **kwargs: Any) -> ProcessTree:
    """Start ``command`` as a self-contained, cancellable process tree."""
    merged = {**spawn_kwargs(), **kwargs}
    return ProcessTree(subprocess.Popen(command, **merged))


def child_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for children, carrying the CUDA library path on POSIX."""
    environment = dict(os.environ)
    if not is_windows():
        from .environment import cuda_library_directories

        directories = [str(path) for path in cuda_library_directories()]
        if directories:
            variable = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
            existing = environment.get(variable, "")
            parts = [item for item in existing.split(os.pathsep) if item]
            for directory in directories:
                if directory not in parts:
                    parts.insert(0, directory)
            environment[variable] = os.pathsep.join(parts)
    if extra:
        environment.update(extra)
    return environment
