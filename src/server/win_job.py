"""Windows Job Object — 父进程退出时由 OS 自动杀死所有子进程。

Python 持有 Job 句柄；Python 进程无论以何种方式退出（正常退出、崩溃、
taskkill /F 强杀、终端被关闭），OS 都会关闭该句柄，Job 内全部进程
（含 Electron 的 renderer/GPU 等孙进程）被立即终止，避免窗口残留。

非 Windows 平台为 no-op。
"""
import sys

_is_win = sys.platform == "win32"

if _is_win:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.windll.kernel32

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),          # ULONG_PTR
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    _job_handle = None

    def init_job() -> bool:
        """创建 kill-on-close Job（幂等）。成功返回 True。

        Job 内进程在 Job 句柄关闭（即本 Python 进程终止）时被 OS 全部杀死。
        """
        global _job_handle
        if _job_handle is not None:
            return True
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            _kernel32.CloseHandle(job)
            return False
        _job_handle = job
        return True

    def add_to_job(proc) -> bool:
        """把 subprocess.Popen 进程加入 Job（失败静默，不影响主流程）。

        注意：加入 Job 的进程再 spawn 的子进程（如 Electron 的 renderer/GPU）
        会自动继承同一 Job。
        """
        if _job_handle is None or proc is None:
            return False
        try:
            h = _kernel32.OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, proc.pid)
            if not h:
                return False
            try:
                return bool(_kernel32.AssignProcessToJobObject(_job_handle, h))
            finally:
                _kernel32.CloseHandle(h)
        except Exception:
            return False

    def job_ok() -> bool:
        """Job 是否已创建且可用（决定 _kill_procs 是否还需要按映像名兜底）。"""
        return _job_handle is not None

else:
    # 非 Windows：no-op
    def init_job() -> bool:
        return False

    def add_to_job(proc) -> bool:
        return False

    def job_ok() -> bool:
        return False
