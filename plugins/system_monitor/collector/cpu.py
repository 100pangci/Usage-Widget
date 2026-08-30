"""CPU 采集：psutil.cpu_percent(interval=None) 非阻塞。

interval=None 时 psutil 内部做两次采样差值，首次调用返回 0 并打底，
语义与旧实现（Windows GetSystemTimes / Linux /proc/stat）一致。
"""
import psutil


def cpu_percent() -> int:
    try:
        return max(0, min(100, int(psutil.cpu_percent(interval=None))))
    except Exception:
        return 0
