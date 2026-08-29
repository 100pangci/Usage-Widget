"""系统指标采集：纯标准库，不依赖 psutil 等第三方包（发行版插件可独立运行）。

Windows 用 ctypes 调系统 API（GetSystemTimes / GlobalMemoryStatusEx / GetDiskFreeSpaceEx），
Linux 读 /proc 伪文件。返回 dict：
    cpu_percent: int  0-100
    mem_percent: int  0-100（已用比例）
    disk_percent: int 0-100（根分区/系统盘）
    net_up_kb / net_down_kb: float 累计收发 KB（显示时做差值）
    uptime_hours: float 开机时长
"""
import os
import sys
import time
from pathlib import Path

_WINDOWS = sys.platform == "win32"


def _windows_cpu_percent() -> int:
    """GetSystemTimes 两次采样差值算 CPU 占用率。"""
    import ctypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                    ("dwHighDateTime", ctypes.c_uint32)]

    def _read() -> tuple[int, int]:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            raise OSError("GetSystemTimes failed")
        # kernel 包含 idle，CPU 总时间 = kernel + user，空闲 = idle
        def _tot(ft: FILETIME) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        total = _tot(kernel) + _tot(user)
        idle_t = _tot(idle)
        return total, idle_t

    if not hasattr(_windows_cpu_percent, "_last"):
        _windows_cpu_percent._last = _read()
        time.sleep(0.2)
    t2, i2 = _read()
    t1, i1 = _windows_cpu_percent._last
    _windows_cpu_percent._last = (t2, i2)
    dt = t2 - t1
    if dt <= 0:
        return 0
    return max(0, min(100, int((dt - (i2 - i1)) * 100 / dt)))


def _linux_cpu_percent() -> int:
    """读 /proc/stat 两次采样（间隔 0.2s）算 CPU 占用。"""
    def _read() -> tuple[int, int]:
        with open("/proc/stat", encoding="ascii") as f:
            parts = f.readline().split()
        # cpu  user nice system idle iowait irq softirq steal guest guest_nice
        idle = int(parts[4]) + int(parts[5])  # idle + iowait
        total = sum(int(p) for p in parts[1:9])
        return total, idle

    if not hasattr(_linux_cpu_percent, "_last"):
        _linux_cpu_percent._last = _read()
        time.sleep(0.2)
    t2, i2 = _read()
    t1, i1 = _linux_cpu_percent._last
    _linux_cpu_percent._last = (t2, i2)
    dt = t2 - t1
    if dt <= 0:
        return 0
    return max(0, min(100, int((dt - (i2 - i1)) * 100 / dt)))


def cpu_percent() -> int:
    try:
        if _WINDOWS:
            return _windows_cpu_percent()
        return _linux_cpu_percent()
    except Exception:
        return 0


def _windows_mem() -> tuple[int, int]:
    """返回 (总内存 MB, 可用内存 MB)。"""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        raise OSError("GlobalMemoryStatusEx failed")
    return st.ullTotalPhys // (1024 * 1024), st.ullAvailPhys // (1024 * 1024)


def _linux_mem() -> tuple[int, int]:
    total = avail = 0
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            k, _, v = line.partition(":")
            v = v.strip().split()[0]
            if k == "MemTotal":
                total = int(v) // 1024
            elif k == "MemAvailable":
                avail = int(v) // 1024
    return total, avail


def mem_percent() -> int:
    try:
        if _WINDOWS:
            total, avail = _windows_mem()
        else:
            total, avail = _linux_mem()
        if total <= 0:
            return 0
        return max(0, min(100, int((total - avail) * 100 / total)))
    except Exception:
        return 0


def _windows_disk() -> int:
    """系统盘（C:）已用百分比。"""
    import ctypes

    free, total = ctypes.c_uint64(), ctypes.c_uint64()
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p("C:\\"), None, ctypes.byref(total), ctypes.byref(free))
    if total.value <= 0:
        return 0
    used = total.value - free.value
    return max(0, min(100, int(used * 100 / total.value)))


def _windows_disk_io() -> int:
    """磁盘 I/O 占用率（% Disk Time，0-100）。

    PDH 计数器 \PhysicalDisk(_Total)\% Disk Time：磁盘处理读写的
    时间占比（多盘取总，可超 100% 后归一化到 100）。
    """
    import ctypes

    # PDH 初始化
    pdh = ctypes.windll.pdh
    hquery = ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hquery)) != 0:
        return 0
    hcounter = ctypes.c_void_p()
    path = r"\PhysicalDisk(_Total)\% Disk Time"
    if pdh.PdhAddCounterW(hquery, path, 0, ctypes.byref(hcounter)) != 0:
        pdh.PdhCloseQuery(hquery)
        return 0
    # 首次采集
    pdh.PdhCollectQueryData(hquery)
    time.sleep(0.2)
    pdh.PdhCollectQueryData(hquery)
    val = ctypes.c_double()
    if pdh.PdhGetFormattedCounterValue(
            hcounter, 0x8000, None, ctypes.byref(val)) != 0:
        pdh.PdhCloseQuery(hquery)
        return 0
    pdh.PdhCloseQuery(hquery)
    return max(0, min(100, int(val.value)))


def _linux_disk_io() -> int:
    """磁盘 I/O 占用率：/proc/diskstats io_ticks（盘忙 ms）两次采样差值。"""
    def _read() -> dict[str, int]:
        out = {}
        with open("/proc/diskstats", encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                # 跳过分区（如 sda1），只统计整盘（sda/nvme0n1/vda 等）
                name = parts[2]
                if name[-1].isdigit() and any(ch.isdigit() for ch in name[:-1]):
                    continue
                io_ticks = int(parts[13]) if len(parts) > 13 else 0
                out[name] = io_ticks
        return out

    if not hasattr(_linux_disk_io, "_last"):
        _linux_disk_io._last = _read()
        time.sleep(0.2)
        _linux_disk_io._last = _read()
        return 0
    t2 = _read()
    t1 = _linux_disk_io._last
    _linux_disk_io._last = t2
    if not t1 or not t2:
        return 0
    # 所有盘 io_ticks 增量之和 / (采样间隔 * 盘数) 近似磁盘占用率
    delta = sum(t2.get(k, 0) - t1.get(k, 0) for k in t2)
    if delta <= 0:
        return 0
    # delta 单位是毫秒；间隔 0.2s = 200ms，单个盘最多 200ms 忙
    # 多盘并行时 delta 可能超 200ms，归一化到 100
    pct = delta / (200 * 1)  # 简化：视作单盘
    return max(0, min(100, int(pct * 100)))


def disk_io_percent() -> int:
    """磁盘 I/O 使用率（0-100）：读写繁忙程度。"""
    try:
        if _WINDOWS:
            return _windows_disk_io()
        return _linux_disk_io()
    except Exception:
        return 0


def _linux_disk_space() -> int:
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return 0
    return max(0, min(100, int((total - free) * 100 / total)))


def disk_space_percent() -> int:
    """磁盘空间使用率（0-100，兼容旧调用，UI 不再使用）。"""
    try:
        if _WINDOWS:
            return _windows_disk()
        return _linux_disk_space()
    except Exception:
        return 0


def _net_counters() -> tuple[int, int]:
    """返回 (累计下行字节, 累计上行字节)。

    Windows 用 GetIfEntry2（64 位计数器，避免 32 位溢出），
    Linux 累加 /sys/class/net 下非 lo 接口的 rx/tx_bytes。
    """
    if _WINDOWS:
        import ctypes

        class MIB_IF_ROW2(ctypes.Structure):
            _fields_ = [
                ("InterfaceLuid", ctypes.c_uint64),
                ("InterfaceIndex", ctypes.c_uint32),
                ("InterfaceGuid", ctypes.c_ubyte * 16),
                ("Alias", ctypes.c_wchar * 257),
                ("Description", ctypes.c_wchar * 257),
                ("PhysicalAddressLength", ctypes.c_uint32),
                ("PhysicalAddress", ctypes.c_ubyte * 32),
                ("PermanentPhysicalAddress", ctypes.c_ubyte * 32),
                ("Mtu", ctypes.c_uint32),
                ("Type", ctypes.c_uint32),
                ("TunnelType", ctypes.c_uint32),
                ("MediaType", ctypes.c_uint32),
                ("PhysicalMediumType", ctypes.c_uint32),
                ("AccessType", ctypes.c_uint32),
                ("DirectionType", ctypes.c_uint32),
                ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
                ("OperStatus", ctypes.c_uint32),
                ("AdminStatus", ctypes.c_uint32),
                ("MediaConnectState", ctypes.c_uint32),
                ("NetworkGuid", ctypes.c_ubyte * 16),
                ("ConnectionType", ctypes.c_uint32),
                ("TransmitLinkSpeed", ctypes.c_uint64),
                ("ReceiveLinkSpeed", ctypes.c_uint64),
                ("InOctets", ctypes.c_uint64),
                ("InUcastPkts", ctypes.c_uint64),
                ("InNUcastPkts", ctypes.c_uint64),
                ("InDiscards", ctypes.c_uint64),
                ("InErrors", ctypes.c_uint64),
                ("InUnknownProtos", ctypes.c_uint64),
                ("InUcastOctets", ctypes.c_uint64),
                ("InMulticastOctets", ctypes.c_uint64),
                ("InBroadcastOctets", ctypes.c_uint64),
                ("OutOctets", ctypes.c_uint64),
                ("OutUcastPkts", ctypes.c_uint64),
                ("OutNUcastPkts", ctypes.c_uint64),
                ("OutDiscards", ctypes.c_uint64),
                ("OutErrors", ctypes.c_uint64),
                ("OutUcastOctets", ctypes.c_uint64),
                ("OutMulticastOctets", ctypes.c_uint64),
                ("OutBroadcastOctets", ctypes.c_uint64),
                ("OutQLen", ctypes.c_uint64),
            ]

        iphlp = ctypes.windll.iphlpapi
        down = up = 0
        # 只统计真实物理网卡：虚拟/回环/隧道接口会让速率虚高
        # （VMware/ZeroTier/WAN Miniport 等计数器高速增长）。
        # 判据（启发式，跨厂商通用）：
        #   1. 链路速率 > 0（虚拟隧道接口多为 0）
        #   2. 非回环（Type != 24）
        #   3. Description 不含常见虚拟接口标记
        VIRTUAL_MARKERS = (
            "loopback", "vmware", "virtual", "zerotier", "wan miniport",
            "teredo", "6to4", "ip-https", "iphttps", "bluetooth", "kernel debug",
            "wifi direct", "pppoe", "pptp", "sstp", "ikev2", "l2tp",
        )
        idx = 1
        while True:
            row = MIB_IF_ROW2()
            row.InterfaceIndex = idx
            if iphlp.GetIfEntry2(ctypes.byref(row)) != 0:
                break
            idx += 1
            if idx > 256:  # 安全上限
                break
            if row.OperStatus != 1:  # 只统计 Up 的接口
                continue
            if row.Type == 24:  # IF_TYPE_SOFTWARE_LOOPBACK
                continue
            if row.TransmitLinkSpeed <= 0 and row.ReceiveLinkSpeed <= 0:
                continue  # 无链路速率：虚拟隧道/未连接
            desc = (row.Description or "").lower()
            if any(m in desc for m in VIRTUAL_MARKERS):
                continue
            down += row.InOctets
            up += row.OutOctets
        return down, up
    # Linux：只统计物理网卡。虚拟接口（docker0/veth/br-/virbr-/tun/tap/
    # wg/lo 等）没有 /sys/class/net/<if>/device 目录（不对应 PCI/USB
    # 设备），物理网卡一定有。这与 Windows 侧过滤虚拟接口的目的一致。
    down = up = 0
    for f in Path("/sys/class/net").iterdir():
        if not (f / "device").exists():
            continue  # 虚拟接口
        try:
            d = int((f / "statistics" / "rx_bytes").read_text().strip())
            u = int((f / "statistics" / "tx_bytes").read_text().strip())
            down += d
            up += u
        except OSError:
            continue
    return down, up


class NetSampler:
    """网络速率采样：两次调用间隔差值算 KB/s。"""

    def __init__(self):
        self._last_down = 0
        self._last_up = 0
        self._last_t = 0.0

    def sample(self) -> tuple[float, float]:
        """返回 (下行 KB/s, 上行 KB/s)。首次调用返回 0。"""
        try:
            down, up = _net_counters()
        except Exception:
            return 0.0, 0.0
        now = time.monotonic()
        if self._last_t == 0.0:
            self._last_down, self._last_up, self._last_t = down, up, now
            return 0.0, 0.0
        dt = now - self._last_t
        if dt <= 0:
            return 0.0, 0.0
        down_kb = (down - self._last_down) / 1024 / dt
        up_kb = (up - self._last_up) / 1024 / dt
        self._last_down, self._last_up, self._last_t = down, up, now
        return max(0, down_kb), max(0, up_kb)

    def reset(self) -> None:
        self._last_t = 0.0


# ---- GPU（跨厂商：NVIDIA / AMD / Intel / 国产）----

# 采集后端：
# - Windows + NVIDIA：NVML（nvml.dll）
# - Windows + AMD/Intel/国产：WMI Win32_PerfFormattedData_GPUPerformanceCounters
# - Linux + NVIDIA：NVML（libnvidia-ml.so）
# - Linux + AMD/Intel：sysfs gpu_busy_percent（amdgpu/xe 驱动）

_gpu_backend: str | None = None  # "nvml" / "wmi" / "sysfs" / None(无卡)
_gpu_names_list: list[str] = []  # 每块 GPU 的型号名
_gpu_nvml_handles: list[object] = []
_gpu_nvml_lib: object | None = None


def gpu_count() -> int:
    """可用 GPU 数量（无卡返回 0）。"""
    _gpu_init()
    return len(_gpu_names_list)


def gpu_names() -> list[str]:
    """每块 GPU 的型号名（短名），无 GPU 返回 []。"""
    _gpu_init()
    return list(_gpu_names_list)


def _gpu_init() -> None:
    """初始化 GPU 采集后端，填充 _gpu_names_list 和 handle 列表。"""
    global _gpu_backend, _gpu_names_list, _gpu_nvml_handles, _gpu_nvml_lib
    if _gpu_backend is not None:
        return
    try:
        if _WINDOWS:
            if _nvml_load():
                _gpu_backend = "nvml"
                return
            if _wmi_load():
                _gpu_backend = "wmi"
                return
        else:  # Linux
            if _nvml_load():
                _gpu_backend = "nvml"
                return
            if _sysfs_load():
                _gpu_backend = "sysfs"
                return
    except Exception:
        pass
    _gpu_backend = ""  # 无可用后端


def _nvml_load() -> bool:
    """加载 NVML（NVIDIA 驱动自带），枚举所有 GPU。"""
    global _gpu_nvml_lib, _gpu_nvml_handles, _gpu_names_list
    import ctypes

    try:
        if _WINDOWS:
            lib = ctypes.WinDLL("nvml.dll")
        else:
            for path in (
                "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so",
                "/usr/lib/libnvidia-ml.so",
                "/usr/lib64/libnvidia-ml.so",
                "/usr/lib/aarch64-linux-gnu/libnvidia-ml.so",
            ):
                try:
                    lib = ctypes.CDLL(path)
                    break
                except OSError:
                    continue
            else:
                return False
    except OSError:
        return False

    if lib.nvmlInit() != 0:
        return False
    count = ctypes.c_uint()
    if lib.nvmlDeviceGetCount(ctypes.byref(count)) != 0 or count.value == 0:
        lib.nvmlShutdown()
        return False
    handles = []
    names = []
    for i in range(count.value):
        h = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex(i, ctypes.byref(h)) != 0:
            continue
        buf = ctypes.create_string_buffer(128)
        if lib.nvmlDeviceGetName(h, buf, 128) == 0:
            name = buf.value.decode("utf-8", "replace").strip()
            # 缩短：NVIDIA GeForce RTX 4060 Laptop GPU → RTX 4060 Laptop
            for prefix in ("NVIDIA GeForce ", "NVIDIA "):
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            names.append(name)
        handles.append(h)
    if not handles:
        lib.nvmlShutdown()
        return False
    _gpu_nvml_lib = lib
    _gpu_nvml_handles = handles
    _gpu_names_list = names
    return True


def _wmi_load() -> bool:
    """Windows WMI GPU Engine：跨厂商（AMD/Intel/国产），按 phys_N 分组。"""
    global _gpu_names_list
    import subprocess

    # 拿显卡名（WMI Win32_VideoController）
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=10)
        names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
        # 过滤掉虚拟显示驱动（OrayIddDriver/GameViewer 等远程/虚拟显卡）
        names = [n for n in names if not any(
            v in n.lower() for v in ("virtual", "iddd", "oray", "gameviewer", "remote"))]
        if not names:
            return False
    except Exception:
        return False
    _gpu_names_list = names
    return True


def _sysfs_load() -> bool:
    """Linux sysfs：amdgpu / xe 驱动的 gpu_busy_percent。"""
    global _gpu_names_list
    from pathlib import Path

    cards = []
    for p in sorted(Path("/sys/class/drm").glob("card*")):
        if not (p / "device" / "gpu_busy_percent").exists():
            continue
        name = ""
        # 驱动名：看 /proc/driver 或设备名
        try:
            vender = (p / "device" / "vendor").read_text().strip()
            device = (p / "device" / "device").read_text().strip()
            name = f"GPU {len(cards)}"
        except OSError:
            name = f"GPU {len(cards)}"
        cards.append(name)
    if not cards:
        return False
    _gpu_names_list = cards
    return True


def gpu_stats() -> list[dict]:
    """每块 GPU 的 (利用率%, 显存已用/总 MB)。无 GPU 返回 []。

    不同后端返回值不同，但统一格式：
        {util: int, mem_percent: int, mem_used_mb: int, mem_total_mb: int}
    显存无法获取时 mem_* 为 0（UI 显示「-」）。
    """
    _gpu_init()
    if _gpu_backend == "nvml":
        return _gpu_stats_nvml()
    if _gpu_backend == "wmi":
        return _gpu_stats_wmi()
    if _gpu_backend == "sysfs":
        return _gpu_stats_sysfs()
    return []


def _gpu_stats_nvml() -> list[dict]:
    lib = _gpu_nvml_lib
    if not lib or not _gpu_nvml_handles:
        return []
    import ctypes

    class MemInfo(ctypes.Structure):
        _fields_ = [("total", ctypes.c_uint64),
                    ("used", ctypes.c_uint64),
                    ("free", ctypes.c_uint64)]

    out = []
    for h in _gpu_nvml_handles:
        gpu_u = ctypes.c_uint()
        mem_u = ctypes.c_uint()
        try:
            lib.nvmlDeviceGetUtilizationRates(
                h, ctypes.byref(gpu_u), ctypes.byref(mem_u))
            mem = MemInfo()
            lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(mem))
            out.append({
                "util": int(gpu_u.value),
                "mem_percent": (int(mem.used * 100 / mem.total)
                                if mem.total > 0 else 0),
                "mem_used_mb": int(mem.used // (1024 * 1024)),
                "mem_total_mb": int(mem.total // (1024 * 1024)),
            })
        except Exception:
            continue
    return out


def _gpu_stats_wmi() -> list[dict]:
    """Windows WMI：GPU Engine 利用率按 phys_N 聚合 + Adapter Memory 显存。"""
    import subprocess

    script = r'''
$engines = Get-CimInstance -Namespace root/cimv2 `
    -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue
$groups = @{}
foreach ($e in $engines) {
    if ($e.Name -match 'phys_(\d+)') {
        $g = $matches[1]
        if (-not $groups.ContainsKey($g)) { $groups[$g] = 0 }
        $groups[$g] += [double]$e.UtilizationPercentage
    }
}
$mems = Get-CimInstance -Namespace root/cimv2 `
    -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory -ErrorAction SilentlyContinue
$memGroups = @{}
foreach ($m in $mems) {
    if ($m.Name -match 'luid_(0x[0-9a-fA-F]+_){2}') { }
    if ($m.Name -match 'phys_(\d+)') {
        $g = $matches[1]
        if (-not $memGroups.ContainsKey($g)) { $memGroups[$g] = @{used=0; total=0} }
        $memGroups[$g].used += [double]$m.DedicatedUsage
    }
}
$keys = $groups.Keys | Sort-Object
$result = @()
foreach ($k in $keys) {
    $result += [PSCustomObject]@{phys=$k; util=[math]::Min(100, [int]$groups[$k])}
}
$result | ConvertTo-Json -Compress
'''
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        import json

        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        stats = []
        for item in data:
            stats.append({
                "util": int(item.get("util", 0)),
                "mem_percent": 0,
                "mem_used_mb": 0,
                "mem_total_mb": 0,
            })
        return stats
    except Exception:
        return []


def _gpu_stats_sysfs() -> list[dict]:
    """Linux sysfs：gpu_busy_percent（AMD/Intel）。显存不可读。"""
    from pathlib import Path

    out = []
    for p in sorted(Path("/sys/class/drm").glob("card*")):
        busy_path = p / "device" / "gpu_busy_percent"
        if not busy_path.exists():
            continue
        try:
            busy = int(busy_path.read_text().strip())
            out.append({
                "util": max(0, min(100, busy)),
                "mem_percent": 0,
                "mem_used_mb": 0,
                "mem_total_mb": 0,
            })
        except (OSError, ValueError):
            continue
    return out


def uptime_hours() -> float:
    """开机时长（小时）。"""
    try:
        if _WINDOWS:
            import ctypes

            ms = ctypes.windll.kernel32.GetTickCount64()
            return ms / 3600000.0
        with open("/proc/uptime", encoding="ascii") as f:
            return float(f.read().split()[0]) / 3600.0
    except Exception:
        return 0.0
