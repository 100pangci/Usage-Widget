"""内存采集：psutil.virtual_memory()。

percent = (total - available) / total * 100，与旧实现
（Windows GlobalMemoryStatusEx / Linux /proc/meminfo）口径一致。
"""
import psutil


def mem_percent() -> int:
    try:
        vm = psutil.virtual_memory()
        if vm.total <= 0:
            return 0
        pct = (vm.total - vm.available) * 100 / vm.total
        return max(0, min(100, int(pct)))
    except Exception:
        return 0


def mem_stats() -> dict:
    """内存明细（总/可用/已用，MB），供 UI 显示。"""
    try:
        vm = psutil.virtual_memory()
        return {
            "total_mb": vm.total // (1024 * 1024),
            "available_mb": vm.available // (1024 * 1024),
            "used_mb": (vm.total - vm.available) // (1024 * 1024),
            "percent": mem_percent(),
        }
    except Exception:
        return {"total_mb": 0, "available_mb": 0, "used_mb": 0, "percent": 0}
