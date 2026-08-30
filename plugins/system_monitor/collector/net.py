"""网络采集：psutil.net_io_counters(pernic=True) 差值算速率。

口径与旧实现一致：全部接口合计（物理 + 虚拟），唯一排除回环接口。
psutil 在 Windows 也提供 OperStatus，只统计 Up 的接口；Linux 无该字段，
全部计入（lo 除外）。
"""
import time

import psutil

_LAST_DOWN = "_last_down"
_LAST_UP = "_last_up"
_LAST_T = "_last_t"


def net_counters() -> tuple[int, int]:
    """返回 (累计下行字节, 累计上行字节)，接口 Down/回环被排除。"""
    down = up = 0
    try:
        counters = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
    except Exception:
        return 0, 0
    for name, cnt in counters.items():
        if name == "lo":
            continue
        if name in stats and not stats[name].isup:
            continue
        down += cnt.bytes_recv
        up += cnt.bytes_sent
    return down, up


class NetSampler:
    """网络速率采样：两次调用间隔差值算 KB/s（旧接口，行为不变）。"""

    def __init__(self):
        self._last_down = 0
        self._last_up = 0
        self._last_t = 0.0

    def sample(self) -> tuple[float, float]:
        """返回 (下行 KB/s, 上行 KB/s)。首次调用返回 0。"""
        try:
            down, up = net_counters()
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
