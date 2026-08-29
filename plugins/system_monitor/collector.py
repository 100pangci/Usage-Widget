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


def _linux_disk() -> int:
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return 0
    return max(0, min(100, int((total - free) * 100 / total)))


def disk_percent() -> int:
    try:
        if _WINDOWS:
            return _windows_disk()
        return _linux_disk()
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
        # 索引从 1 开始试，遇到错误即止（索引通常连续到 N）
        idx = 1
        while True:
            row = MIB_IF_ROW2()
            row.InterfaceIndex = idx
            if iphlp.GetIfEntry2(ctypes.byref(row)) != 0:
                break
            if row.OperStatus == 1:  # IfOperStatusUp
                down += row.InOctets
                up += row.OutOctets
            idx += 1
            if idx > 256:  # 安全上限
                break
        return down, up
    # Linux
    down = up = 0
    for f in Path("/sys/class/net").iterdir():
        if f.name == "lo":
            continue
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
