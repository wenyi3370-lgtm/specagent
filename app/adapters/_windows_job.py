"""Kill-on-close Windows job for the worker and all its descendants."""
import ctypes
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


def attach(process):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        error = ctypes.WinError(ctypes.get_last_error())
        api.CloseHandle(handle)
        raise error
    return lambda: api.CloseHandle(handle)
