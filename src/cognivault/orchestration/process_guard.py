"""Contain executors before their first instruction, then reap their lifetime.

Windows uses a kill-on-close Job with a single active process. The child starts
suspended, joins the Job, and only then resumes. No descendant can be created.
POSIX uses a fresh session/process group; cleanup also works after leader exit.
"""
import asyncio
import os
import signal

from .models import AdapterError


if os.name == "nt":
    import ctypes
    from ctypes import wintypes as w

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", w.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Thread(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                    ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                    ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for _name, _args, _restype in (
        ("CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
        ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
        ("Thread32First", [w.HANDLE, ctypes.POINTER(_Thread)], w.BOOL),
        ("Thread32Next", [w.HANDLE, ctypes.POINTER(_Thread)], w.BOOL),
        ("OpenThread", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("ResumeThread", [w.HANDLE], w.DWORD),
        ("CloseHandle", [w.HANDLE], w.BOOL),
    ):
        _function = getattr(_kernel, _name)
        _function.argtypes, _function.restype = _args, _restype

    class _Job:
        def __init__(self):
            self.handle = _kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise AdapterError("EXECUTOR_BLOCKED")
            limits = _Extended()
            # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS. One process forbids even a
            # brief descendant that could outlive an exiting parent.
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x8
            limits.BasicLimitInformation.ActiveProcessLimit = 1
            if not _kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise AdapterError("EXECUTOR_BLOCKED")

        def assign_and_resume(self, pid):
            process = _kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process:
                raise AdapterError("EXECUTOR_BLOCKED")
            try:
                if not _kernel.AssignProcessToJobObject(self.handle, process):
                    raise AdapterError("EXECUTOR_BLOCKED")
            finally:
                _kernel.CloseHandle(process)
            snapshot = _kernel.CreateToolhelp32Snapshot(0x4, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                raise AdapterError("EXECUTOR_BLOCKED")
            try:
                entry = _Thread()
                entry.dwSize = ctypes.sizeof(entry)
                valid = _kernel.Thread32First(snapshot, ctypes.byref(entry))
                ids = []
                while valid:
                    if entry.th32OwnerProcessID == pid:
                        ids.append(entry.th32ThreadID)
                    valid = _kernel.Thread32Next(snapshot, ctypes.byref(entry))
                # A suspended initial process has exactly its initial thread.
                if len(ids) != 1:
                    raise AdapterError("EXECUTOR_BLOCKED")
                thread = _kernel.OpenThread(0x0002, False, ids[0])
                if not thread:
                    raise AdapterError("EXECUTOR_BLOCKED")
                try:
                    if _kernel.ResumeThread(thread) != 1:
                        raise AdapterError("EXECUTOR_BLOCKED")
                finally:
                    _kernel.CloseHandle(thread)
            finally:
                _kernel.CloseHandle(snapshot)

        def close(self):
            if self.handle:
                _kernel.CloseHandle(self.handle)
                self.handle = None


async def spawn(*args, **kwargs):
    """Trusted argv only. Containment failure never resumes the application."""
    job, process = None, None
    try:
        if os.name == "nt":
            job = _Job()
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000 | 0x4
        else:
            kwargs["start_new_session"] = True
        # Pipe/transport setup awaits after the OS process already exists. Keep
        # its task alive on cancellation so the suspended process handle cannot
        # be lost before it can be assigned or killed.
        launch = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            process = await launch
            raise
        if job is not None:
            process._cognivault_job = job
            job.assign_and_resume(process.pid)
        else:
            process._cognivault_pgid = process.pid
        return process
    except BaseException:
        if job is not None:
            job.close()
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
        raise


async def stop(process):
    """Release containment even if the process leader already exited."""
    job = getattr(process, "_cognivault_job", None)
    if job is not None:
        job.close()
    pgid = getattr(process, "_cognivault_pgid", None)
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()
