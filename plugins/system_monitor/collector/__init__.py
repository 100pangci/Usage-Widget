"""指标采集层（psutil 实现），按指标域拆分：

    cpu.py     CPU 使用率
    memory.py  内存使用率 / 明细
    disk.py    磁盘 I/O 占用率 / 空间使用率
    net.py     网络速率（NetSampler）
    gpu.py     GPU 使用率 / 显存（NVML / WMI / sysfs）
    system.py  开机时长

本包对外保持旧模块级 API（collector.cpu_percent() 等），
兼容旧插件代码与测试；新代码建议按域导入。
"""
from .cpu import cpu_percent
from .disk import disk_io_percent, disk_space_percent
from .gpu import gpu_count, gpu_names, gpu_stats
from .memory import mem_percent, mem_stats
from .net import NetSampler, net_counters
from .system import uptime_hours

__all__ = [
    "cpu_percent",
    "mem_percent",
    "mem_stats",
    "disk_io_percent",
    "disk_space_percent",
    "NetSampler",
    "net_counters",
    "gpu_count",
    "gpu_names",
    "gpu_stats",
    "uptime_hours",
]
