"""系统信息：开机时长（psutil.boot_time）。"""
import time

import psutil


def uptime_hours() -> float:
    """开机时长（小时）。"""
    try:
        return max(0.0, (time.time() - psutil.boot_time()) / 3600.0)
    except Exception:
        return 0.0
