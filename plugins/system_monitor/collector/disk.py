r"""磁盘采集：I/O 占用率 + 空间使用率。

I/O 占用率（0-100，盘忙时间占比）：
- Linux：psutil.disk_io_counters 的 busy_time（即 /proc/diskstats 的
  io_ticks，与旧实现同口径；并发读写不双计），无该字段的平台回退
  read_time + write_time。两次采样差值除以**真实采样间隔**归一化。
- Windows：psutil 的 read_time/write_time 在部分机器上不增量
  （见 nicolargo/psutil#1576），改用 PDH 计数器 "\PhysicalDisk(_Total)\% Disk Time"
  （与旧实现相同）；句柄跨调用复用，不 sleep 不阻塞。计数器不可用
  （如未启用 diskperf）时告警一次并恒返回 0。

空间使用率：psutil.disk_usage 系统盘/根分区。
"""
import logging
import sys
import time

import psutil

log = logging.getLogger("system_monitor.disk")

_WINDOWS = sys.platform == "win32"


def _disk_io_ticks() -> dict[str, int]:
    out = {}
    try:
        counters = psutil.disk_io_counters(perdisk=True)
    except Exception:
        return out
    for name, cnt in counters.items():
        # busy_time = io_ticks（盘忙毫秒），并发读写不双计；
        # 无该字段的平台（如 Windows）回退 read_time + write_time
        busy = getattr(cnt, "busy_time", None)
        if busy is None or busy < 0:
            busy = (getattr(cnt, "read_time", 0) or 0) + (
                getattr(cnt, "write_time", 0) or 0)
        out[name] = busy
    return out


_last_ticks: dict[str, int] = {}
_last_t: float = 0.0


def _io_delta_percent(delta_ms: float, dt_s: float) -> int:
    """忙时增量 → 占用率：delta_ms / (dt_s * 1000)，归一化到 0-100。

    tick 间隔是 2s（不是 1s），必须用真实间隔归一，否则读数虚高一倍。
    """
    if dt_s <= 0 or delta_ms <= 0:
        return 0
    pct = delta_ms / (dt_s * 1000.0)
    return max(0, min(100, int(pct * 100)))


def _linux_disk_io_percent() -> int:
    """Linux：全部盘 io 忙时增量 / 真实采样间隔，归一化到 100。"""
    global _last_ticks, _last_t
    try:
        t2 = _disk_io_ticks()
        now = time.monotonic()
        if not _last_ticks:
            _last_ticks, _last_t = t2, now
            return 0
        dt = now - _last_t
        delta = sum(
            t2.get(k, 0) - _last_ticks.get(k, 0)
            for k in set(t2) | set(_last_ticks)
        )
        _last_ticks, _last_t = t2, now
        return _io_delta_percent(delta, dt)
    except Exception:
        return 0


_pdh_warned = False


def _warn_pdh_unavailable(reason: str) -> None:
    """PDH 磁盘计数器不可用：告警一次（管理员运行 diskperf -Y 可启用）。"""
    global _pdh_warned
    if _pdh_warned:
        return
    _pdh_warned = True
    log.warning(
        "Windows PDH 磁盘计数器不可用（%s），磁盘 I/O 将显示 0%%；"
        "可在管理员终端运行 diskperf -Y 启用后重启应用", reason)


def _windows_disk_io_percent() -> int:
    """Windows：PDH \\% Disk Time（psutil 无盘忙时，见模块 docstring）。"""
    import ctypes

    if getattr(_windows_disk_io_percent, "_dead", False):
        return 0
    pdh = ctypes.windll.pdh
    if not hasattr(_windows_disk_io_percent, "_hquery"):
        hquery = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(hquery)) != 0:
            _warn_pdh_unavailable("PdhOpenQueryW 失败")
            _windows_disk_io_percent._dead = True
            return 0
        hcounter = ctypes.c_void_p()
        path = r"\PhysicalDisk(_Total)\% Disk Time"
        if pdh.PdhAddCounterW(hquery, path, 0, ctypes.byref(hcounter)) != 0:
            pdh.PdhCloseQuery(hquery)
            _warn_pdh_unavailable(f"计数器 {path} 不存在")
            _windows_disk_io_percent._dead = True
            return 0
        _windows_disk_io_percent._hquery = hquery
        _windows_disk_io_percent._hcounter = hcounter
    hquery = _windows_disk_io_percent._hquery
    hcounter = _windows_disk_io_percent._hcounter
    if not getattr(_windows_disk_io_percent, "_primed", False):
        # 首次调用：只打基准，不取数
        pdh.PdhCollectQueryData(hquery)
        _windows_disk_io_percent._primed = True
        return 0
    if pdh.PdhCollectQueryData(hquery) != 0:
        return 0
    val = ctypes.c_double()
    if pdh.PdhGetFormattedCounterValue(
            hcounter, 0x8000, None, ctypes.byref(val)) != 0:
        return 0
    return max(0, min(100, int(val.value)))


def disk_io_percent() -> int:
    """磁盘 I/O 使用率（0-100）：读写繁忙程度。首次调用返回 0。"""
    try:
        if _WINDOWS:
            return _windows_disk_io_percent()
        return _linux_disk_io_percent()
    except Exception:
        return 0


def disk_space_percent() -> int:
    """磁盘空间使用率（0-100，系统盘/根分区）。"""
    try:
        usage = psutil.disk_usage("C:\\" if _WINDOWS else "/")
        if usage.total <= 0:
            return 0
        used = usage.total - usage.free
        return max(0, min(100, int(used * 100 / usage.total)))
    except Exception:
        return 0
