from __future__ import annotations

import gc
import os
import platform
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False


@dataclass
class ThreadInfo:
    """Informacje o aktywnym wątku aplikacji."""
    name: str
    ident: int
    native_id: Optional[int]
    is_daemon: bool
    kind: str
    role: str


@dataclass
class SystemMetricsSnapshot:
    """Migawka telemetrii systemu oraz procesu aplikacji."""
    timestamp: float
    uptime_s: float
    cpu_system_percent: float
    cpu_per_core: List[float] = field(default_factory=list)
    cpu_process_percent: float = 0.0        # Znormalizowane do 0-100% całego CPU
    cpu_process_raw_percent: float = 0.0    # Względem 1 rdzenia (może przekraczać 100%)
    cpu_count_logical: int = 1
    cpu_count_physical: Optional[int] = None
    load_avg: Optional[Tuple[float, float, float]] = None
    cpu_temp_c: Optional[float] = None
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_available_mb: float = 0.0
    ram_percent: float = 0.0
    process_ram_mb: float = 0.0
    process_vms_mb: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    swap_percent: float = 0.0
    os_thread_count: int = 1
    python_thread_count: int = 1
    threads: List[ThreadInfo] = field(default_factory=list)
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_used_percent: float = 0.0
    gc_counts: Tuple[int, int, int] = (0, 0, 0)
    backend_name: str = "native"
    platform_label: str = ""


def _describe_thread_role(name: str, is_main: bool) -> str:
    """Zwraca czytelną rolę wątku na podstawie jego nazwy."""
    if is_main or name == "MainThread":
        return "Wątek główny GUI (PySide6 / renderowanie)"
    low = name.lower()
    if "cameraworker" in low:
        return "Akwizycja strumienia wideo z kamery"
    if "opcuaserver" in low:
        return "Serwer OPC UA (asyncio dla SCADA/Ignition)"
    if "barcodeworker" in low:
        return "Dekodowanie kodów (zxing-cpp / FFT)"
    if "snapshotsave" in low:
        return "Asynchroniczny zapis obrazów PNG"
    if name.startswith("Dummy-"):
        return "Wątek uruchomiony przez warstwę natywną (Qt/C++)"
    return "Wątek roboczy Python"


class SystemMonitor:
    """
    Zbiera metryki zajętości CPU, RAM, swap, dysku, temperatury (Linux/Orange Pi)
    oraz listę aktywnych wątków procesu.
    Wykorzystuje `psutil`, a w razie jego braku działa w oparciu o natywne API
    Windows (`ctypes` Win32) lub Linuksa (`/proc`, `/sys`).
    """

    def __init__(self, watch_path: str | Path = ".") -> None:
        self.watch_path = Path(watch_path)
        self._start_mono = time.monotonic()
        self._platform_label = f"{platform.system()} {platform.release()} ({platform.machine()})"

        self._psutil_proc = None
        if _HAS_PSUTIL:
            try:
                self._psutil_proc = psutil.Process(os.getpid())
                psutil.cpu_percent(interval=None)
                psutil.cpu_percent(interval=None, percpu=True)
                self._psutil_proc.cpu_percent(interval=None)
            except Exception:
                self._psutil_proc = None

        # Poprzednie liczniki do obliczania delt bez psutil
        self._prev_win_sys: Optional[Tuple[int, int, int]] = None
        self._prev_win_proc: Optional[Tuple[float, int]] = None
        self._prev_linux_cpu_all: Optional[Tuple[int, int]] = None
        self._prev_linux_cpu_cores: Dict[str, Tuple[int, int]] = {}
        self._prev_linux_proc: Optional[Tuple[float, int]] = None
        self._linux_clk_tck: int = 100
        if not sys.platform.startswith("win") and hasattr(os, "sysconf"):
            try:
                self._linux_clk_tck = int(os.sysconf("SC_CLK_TCK"))
            except (ValueError, OSError):
                self._linux_clk_tck = 100

        self._prime_native_counters()

    def _prime_native_counters(self) -> None:
        """Inicjalizuje bazowe liczniki czasu CPU, aby pierwsze odświeżenie zwracało realne delty."""
        if sys.platform == "win32":
            self._prev_win_sys = self._win32_read_system_times()
            proc_t = self._win32_read_process_times()
            if proc_t is not None:
                self._prev_win_proc = (time.monotonic(), proc_t)
        elif sys.platform.startswith("linux"):
            all_t, cores_t = self._linux_read_proc_stat()
            self._prev_linux_cpu_all = all_t
            self._prev_linux_cpu_cores = cores_t
            proc_ticks = self._linux_read_proc_self_ticks()
            if proc_ticks is not None:
                self._prev_linux_proc = (time.monotonic(), proc_ticks)

    # =========================================================================
    # NATYWNY BACKEND WINDOWS (ctypes Win32)
    # =========================================================================
    @staticmethod
    def _win32_filetime_to_int(ft: Any) -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    def _win32_read_system_times(self) -> Optional[Tuple[int, int, int]]:
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

            k32 = ctypes.windll.kernel32
            idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
            if k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return (
                    self._win32_filetime_to_int(idle),
                    self._win32_filetime_to_int(kernel),
                    self._win32_filetime_to_int(user),
                )
        except Exception:
            pass
        return None

    def _win32_read_process_times(self) -> Optional[int]:
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME),
            ]
            k32.GetProcessTimes.restype = wintypes.BOOL

            c_t, e_t, k_t, u_t = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(c_t), ctypes.byref(e_t), ctypes.byref(k_t), ctypes.byref(u_t)):
                return self._win32_filetime_to_int(k_t) + self._win32_filetime_to_int(u_t)
        except Exception:
            pass
        return None

    def _win32_read_memory(self) -> Tuple[float, float, float, float, float, float]:
        """Zwraca (ram_total_mb, ram_used_mb, ram_avail_mb, ram_pct, proc_rss_mb, proc_vms_mb)."""
        ram_total = ram_used = ram_avail = ram_pct = proc_rss = proc_vms = 0.0
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE

            ms = MEMORYSTATUSEX()
            ms.dwLength = ctypes.sizeof(ms)
            if k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                ram_total = ms.ullTotalPhys / (1024.0 * 1024.0)
                ram_avail = ms.ullAvailPhys / (1024.0 * 1024.0)
                ram_used = max(0.0, ram_total - ram_avail)
                ram_pct = float(ms.dwMemoryLoad) if ram_total <= 0 else (ram_used / ram_total) * 100.0

            pm = PROCESS_MEMORY_COUNTERS()
            pm.cb = ctypes.sizeof(pm)
            fn = getattr(k32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                psapi = ctypes.windll.psapi
                fn = psapi.GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
            fn.restype = wintypes.BOOL
            if fn(k32.GetCurrentProcess(), ctypes.byref(pm), pm.cb):
                proc_rss = pm.WorkingSetSize / (1024.0 * 1024.0)
                proc_vms = pm.PagefileUsage / (1024.0 * 1024.0)
        except Exception:
            pass
        return ram_total, ram_used, ram_avail, ram_pct, proc_rss, proc_vms

    @staticmethod
    def _win32_count_os_threads() -> Optional[int]:
        try:
            import ctypes
            from ctypes import wintypes

            class THREADENTRY32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", ctypes.c_long),
                    ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                ]

            k32 = ctypes.windll.kernel32
            k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
            k32.Thread32First.restype = wintypes.BOOL
            k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
            k32.Thread32Next.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL

            pid = os.getpid()
            snap = k32.CreateToolhelp32Snapshot(0x00000004, 0)
            invalid_handle = wintypes.HANDLE(-1).value
            if snap == invalid_handle:
                return None

            try:
                te = THREADENTRY32()
                te.dwSize = ctypes.sizeof(te)
                count = 0
                ok = k32.Thread32First(snap, ctypes.byref(te))
                while ok:
                    if te.th32OwnerProcessID == pid:
                        count += 1
                    ok = k32.Thread32Next(snap, ctypes.byref(te))
                return count
            finally:
                k32.CloseHandle(snap)
        except Exception:
            return None

    # =========================================================================
    # NATYWNY BACKEND LINUX (/proc, /sys)
    # =========================================================================
    @staticmethod
    def _linux_read_proc_stat() -> Tuple[Optional[Tuple[int, int]], Dict[str, Tuple[int, int]]]:
        cpu_all = None
        cores: Dict[str, Tuple[int, int]] = {}
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        break
                    parts = line.split()
                    key = parts[0]
                    vals = [int(x) for x in parts[1:]]
                    if len(vals) < 4:
                        continue
                    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                    total = sum(vals)
                    if key == "cpu":
                        cpu_all = (idle, total)
                    elif key[3:].isdigit():
                        cores[key] = (idle, total)
        except Exception:
            pass
        return cpu_all, cores

    @staticmethod
    def _linux_read_proc_self_ticks() -> Optional[int]:
        try:
            with open("/proc/self/stat", "r", encoding="utf-8") as f:
                raw = f.read()
            rparen = raw.rfind(")")
            if rparen < 0:
                return None
            fields = raw[rparen + 2:].split()
            # utime=index 11 after state (field 14 total -> 11 after comm+space)
            utime = int(fields[11])
            stime = int(fields[12])
            return utime + stime
        except Exception:
            return None

    @staticmethod
    def _linux_read_meminfo_and_proc_status() -> Tuple[float, float, float, float, float, float, float, float, float, Optional[int]]:
        mem_total_kb = mem_avail_kb = mem_free_kb = buffers_kb = cached_kb = 0.0
        swap_total_kb = swap_free_kb = 0.0
        proc_rss_kb = proc_vms_kb = 0.0
        threads_cnt: Optional[int] = None

        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    k = parts[0].rstrip(":")
                    v = float(parts[1])
                    if k == "MemTotal":
                        mem_total_kb = v
                    elif k == "MemAvailable":
                        mem_avail_kb = v
                    elif k == "MemFree":
                        mem_free_kb = v
                    elif k == "Buffers":
                        buffers_kb = v
                    elif k == "Cached":
                        cached_kb = v
                    elif k == "SwapTotal":
                        swap_total_kb = v
                    elif k == "SwapFree":
                        swap_free_kb = v
        except Exception:
            pass

        if mem_avail_kb <= 0.0:
            mem_avail_kb = mem_free_kb + buffers_kb + cached_kb

        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        proc_rss_kb = float(line.split()[1])
                    elif line.startswith("VmSize:"):
                        proc_vms_kb = float(line.split()[1])
                    elif line.startswith("Threads:"):
                        threads_cnt = int(line.split()[1])
        except Exception:
            pass

        ram_total = mem_total_kb / 1024.0
        ram_avail = mem_avail_kb / 1024.0
        ram_used = max(0.0, ram_total - ram_avail)
        ram_pct = (ram_used / ram_total * 100.0) if ram_total > 0 else 0.0
        swap_total = swap_total_kb / 1024.0
        swap_used = max(0.0, (swap_total_kb - swap_free_kb) / 1024.0)
        swap_pct = (swap_used / swap_total * 100.0) if swap_total > 0 else 0.0
        return (
            ram_total,
            ram_used,
            ram_avail,
            ram_pct,
            proc_rss_kb / 1024.0,
            proc_vms_kb / 1024.0,
            swap_total,
            swap_used,
            swap_pct,
            threads_cnt,
        )

    @staticmethod
    def _read_cpu_temperature() -> Optional[float]:
        """Odczytuje temperaturę CPU/SoC w Linuksie (np. Orange Pi 5 RK3588)."""
        if not sys.platform.startswith("linux"):
            return None
        best_temp: Optional[float] = None
        try:
            for tz in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
                tfile = tz / "temp"
                if not tfile.exists():
                    continue
                val = float(tfile.read_text(encoding="utf-8").strip())
                c = val / 1000.0 if val > 200.0 else val
                if 5.0 <= c <= 130.0:
                    if best_temp is None or c > best_temp:
                        best_temp = c
        except Exception:
            pass
        return best_temp

    # =========================================================================
    # ZBIERANIE LISTY WĄTKÓW I GŁÓWNY SNAPSHOT
    # =========================================================================
    @staticmethod
    def _collect_threads(extra_workers: Optional[Sequence[Tuple[str, Any]]] = None) -> List[ThreadInfo]:
        main_thr = threading.main_thread()
        py_threads = threading.enumerate()
        infos: List[ThreadInfo] = []
        seen_names = set()

        for thr in py_threads:
            is_main = thr is main_thr
            ident = thr.ident or 0
            native_id = getattr(thr, "native_id", None)
            kind = "Główny" if is_main else ("Daemon" if thr.daemon else "Non-daemon")
            infos.append(
                ThreadInfo(
                    name=thr.name,
                    ident=ident,
                    native_id=native_id,
                    is_daemon=thr.daemon,
                    kind=kind,
                    role=_describe_thread_role(thr.name, is_main),
                )
            )
            seen_names.add(thr.name)

        if extra_workers:
            for worker_name, worker_obj in extra_workers:
                if worker_obj is None:
                    continue
                is_running = False
                try:
                    is_running = bool(worker_obj.isRunning())
                except Exception:
                    is_running = False
                if is_running and worker_name not in seen_names:
                    infos.append(
                        ThreadInfo(
                            name=worker_name,
                            ident=0,
                            native_id=None,
                            is_daemon=False,
                            kind="QThread",
                            role=_describe_thread_role(worker_name, False),
                        )
                    )
                    seen_names.add(worker_name)

        infos.sort(key=lambda t: (0 if t.kind == "Główny" else (1 if t.kind == "QThread" else 2), t.name))
        return infos

    def collect(self, extra_workers: Optional[Sequence[Tuple[str, Any]]] = None) -> SystemMetricsSnapshot:
        """Zwraca bieżącą migawkę telemetrii CPU, RAM, wątków i dysku."""
        now_wall = time.time()
        now_mono = time.monotonic()
        uptime_s = max(0.0, now_mono - self._start_mono)
        logical_cpus = os.cpu_count() or 1
        physical_cpus: Optional[int] = None

        cpu_sys = 0.0
        cpu_cores: List[float] = []
        cpu_proc_raw = 0.0
        cpu_proc_norm = 0.0
        ram_total = ram_used = ram_avail = ram_pct = 0.0
        proc_rss = proc_vms = 0.0
        swap_total = swap_used = swap_pct = 0.0
        os_threads = threading.active_count()
        backend = "stdlib"

        if _HAS_PSUTIL and self._psutil_proc is not None:
            backend = "psutil"
            try:
                cpu_sys = float(psutil.cpu_percent(interval=None))
                cpu_cores = [float(x) for x in psutil.cpu_percent(interval=None, percpu=True)]
                cpu_proc_raw = float(self._psutil_proc.cpu_percent(interval=None))
                cpu_proc_norm = min(100.0, cpu_proc_raw / max(1, logical_cpus))
                physical_cpus = psutil.cpu_count(logical=False)
                vm = psutil.virtual_memory()
                ram_total = vm.total / (1024.0 * 1024.0)
                ram_used = (vm.total - vm.available) / (1024.0 * 1024.0)
                ram_avail = vm.available / (1024.0 * 1024.0)
                ram_pct = float(vm.percent)
                sm = psutil.swap_memory()
                swap_total = sm.total / (1024.0 * 1024.0)
                swap_used = sm.used / (1024.0 * 1024.0)
                swap_pct = float(sm.percent)
                mi = self._psutil_proc.memory_info()
                proc_rss = mi.rss / (1024.0 * 1024.0)
                proc_vms = mi.vms / (1024.0 * 1024.0)
                os_threads = int(self._psutil_proc.num_threads())
            except Exception:
                backend = "native-fallback"

        if backend != "psutil":
            if sys.platform == "win32":
                backend = "win32-native"
                cur_sys = self._win32_read_system_times()
                if cur_sys is not None and self._prev_win_sys is not None:
                    d_idle = cur_sys[0] - self._prev_win_sys[0]
                    d_kern = cur_sys[1] - self._prev_win_sys[1]
                    d_user = cur_sys[2] - self._prev_win_sys[2]
                    d_total = d_kern + d_user
                    if d_total > 0:
                        cpu_sys = max(0.0, min(100.0, ((d_total - d_idle) / float(d_total)) * 100.0))
                if cur_sys is not None:
                    self._prev_win_sys = cur_sys

                cur_proc_t = self._win32_read_process_times()
                if cur_proc_t is not None and self._prev_win_proc is not None:
                    dt_wall = now_mono - self._prev_win_proc[0]
                    dt_cpu_s = (cur_proc_t - self._prev_win_proc[1]) / 10_000_000.0
                    if dt_wall > 0.01:
                        cpu_proc_raw = max(0.0, (dt_cpu_s / dt_wall) * 100.0)
                        cpu_proc_norm = min(100.0, cpu_proc_raw / max(1, logical_cpus))
                if cur_proc_t is not None:
                    self._prev_win_proc = (now_mono, cur_proc_t)

                ram_total, ram_used, ram_avail, ram_pct, proc_rss, proc_vms = self._win32_read_memory()
                cnt_thr = self._win32_count_os_threads()
                if cnt_thr is not None:
                    os_threads = cnt_thr

            elif sys.platform.startswith("linux"):
                backend = "linux-proc"
                cur_all, cur_cores = self._linux_read_proc_stat()
                if cur_all is not None and self._prev_linux_cpu_all is not None:
                    d_idle = cur_all[0] - self._prev_linux_cpu_all[0]
                    d_tot = cur_all[1] - self._prev_linux_cpu_all[1]
                    if d_tot > 0:
                        cpu_sys = max(0.0, min(100.0, ((d_tot - d_idle) / float(d_tot)) * 100.0))
                if cur_all is not None:
                    self._prev_linux_cpu_all = cur_all

                if cur_cores:
                    for cname in sorted(cur_cores.keys(), key=lambda s: int(s[3:])):
                        if cname in self._prev_linux_cpu_cores:
                            pi, pt = self._prev_linux_cpu_cores[cname]
                            ci, ct = cur_cores[cname]
                            dt = ct - pt
                            di = ci - pi
                            pct = max(0.0, min(100.0, ((dt - di) / float(dt)) * 100.0)) if dt > 0 else 0.0
                            cpu_cores.append(pct)
                    self._prev_linux_cpu_cores = cur_cores

                cur_ticks = self._linux_read_proc_self_ticks()
                if cur_ticks is not None and self._prev_linux_proc is not None:
                    dt_wall = now_mono - self._prev_linux_proc[0]
                    dt_cpu_s = (cur_ticks - self._prev_linux_proc[1]) / float(max(1, self._linux_clk_tck))
                    if dt_wall > 0.01:
                        cpu_proc_raw = max(0.0, (dt_cpu_s / dt_wall) * 100.0)
                        cpu_proc_norm = min(100.0, cpu_proc_raw / max(1, logical_cpus))
                if cur_ticks is not None:
                    self._prev_linux_proc = (now_mono, cur_ticks)

                (
                    ram_total,
                    ram_used,
                    ram_avail,
                    ram_pct,
                    proc_rss,
                    proc_vms,
                    swap_total,
                    swap_used,
                    swap_pct,
                    linux_threads,
                ) = self._linux_read_meminfo_and_proc_status()
                if linux_threads is not None:
                    os_threads = linux_threads

        # Load average (Linux / POSIX)
        load_avg = None
        if hasattr(os, "getloadavg"):
            try:
                load_avg = os.getloadavg()
            except OSError:
                load_avg = None

        # Miejsce na dysku dla katalogu zrzutów
        disk_total_gb = disk_free_gb = disk_used_pct = 0.0
        try:
            target_dir = self.watch_path if self.watch_path.exists() else Path(".")
            du = shutil.disk_usage(target_dir)
            disk_total_gb = du.total / (1024.0 ** 3)
            disk_free_gb = du.free / (1024.0 ** 3)
            disk_used_pct = (du.used / float(du.total) * 100.0) if du.total > 0 else 0.0
        except Exception:
            pass

        thread_list = self._collect_threads(extra_workers=extra_workers)
        py_threads_cnt = threading.active_count()
        os_threads = max(os_threads, len(thread_list))

        return SystemMetricsSnapshot(
            timestamp=now_wall,
            uptime_s=uptime_s,
            cpu_system_percent=cpu_sys,
            cpu_per_core=cpu_cores,
            cpu_process_percent=cpu_proc_norm,
            cpu_process_raw_percent=cpu_proc_raw,
            cpu_count_logical=logical_cpus,
            cpu_count_physical=physical_cpus,
            load_avg=load_avg,
            cpu_temp_c=self._read_cpu_temperature(),
            ram_total_mb=ram_total,
            ram_used_mb=ram_used,
            ram_available_mb=ram_avail,
            ram_percent=ram_pct,
            process_ram_mb=proc_rss,
            process_vms_mb=proc_vms,
            swap_total_mb=swap_total,
            swap_used_mb=swap_used,
            swap_percent=swap_pct,
            os_thread_count=os_threads,
            python_thread_count=py_threads_cnt,
            threads=thread_list,
            disk_total_gb=disk_total_gb,
            disk_free_gb=disk_free_gb,
            disk_used_percent=disk_used_pct,
            gc_counts=gc.get_count(),
            backend_name=backend,
            platform_label=self._platform_label,
        )
