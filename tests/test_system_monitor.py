"""系统监控插件测试：指标采集器返回合理值、格式化函数、插件刷新。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.system_monitor.collector import (
    NetSampler,
    cpu_percent,
    disk_io_percent,
    gpu_count,
    gpu_names,
    gpu_stats,
    mem_percent,
    uptime_hours,
)
from plugins.system_monitor.plugin import _fmt_mb, _fmt_speed, _fmt_uptime


# ---- 指标采集 ----

def test_cpu_percent_range():
    for _ in range(2):
        v = cpu_percent()
        assert 0 <= v <= 100


def test_mem_percent_range():
    v = mem_percent()
    assert 0 <= v <= 100


def test_disk_io_percent_range():
    v = disk_io_percent()
    assert 0 <= v <= 100


def test_uptime_positive():
    assert uptime_hours() > 0


def test_gpu_api_returns_lists():
    """GPU 采集 API：返回列表，多卡时每卡一项（无卡时为空列表）。"""
    count = gpu_count()
    names = gpu_names()
    stats = gpu_stats()
    assert isinstance(names, list)
    assert isinstance(stats, list)
    assert len(names) == count
    assert len(stats) == count
    for s in stats:
        assert 0 <= s["util"] <= 100
        assert s["mem_used_mb"] >= 0
        assert s["mem_total_mb"] >= 0


def test_net_sampler_returns_speed():
    sampler = NetSampler()
    first = sampler.sample()  # 首次调用应返回 0
    assert first == (0.0, 0.0)
    time.sleep(0.3)
    down, up = sampler.sample()
    assert down >= 0 and up >= 0


# ---- 格式化 ----

def test_fmt_speed():
    assert _fmt_speed(500) == "500KB/s"
    assert _fmt_speed(2048) == "2.0MB/s"
    assert _fmt_speed(1024 * 1024) == "1.0GB/s"


def test_fmt_uptime():
    assert _fmt_uptime(5) == "5小时"
    assert _fmt_uptime(30.5) == "1天 6小时"
    assert _fmt_uptime(50) == "2天 2小时"
    assert _fmt_uptime(0.5) == "30分"


def test_fmt_mb():
    assert _fmt_mb(512) == "512M"
    assert _fmt_mb(2048) == "2.0G"
    assert _fmt_mb(10240) == "10G"


# ---- UI 刷新 ----

def test_plugin_tick_updates_labels():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    from plugins.system_monitor.plugin import SystemMonitorPlugin

    plugin = SystemMonitorPlugin()
    widget = plugin.create_widget(None)
    assert len(plugin._sparks) == 3
    assert len(plugin._gpu_sparks) == gpu_count()
    assert plugin._net_spark is not None
    assert plugin._uptime_label is not None
    plugin.tick()
    for key, pct in plugin._pct_labels.items():
        assert pct.text() != "--", f"{key} 未刷新"
    assert "↓" in plugin._net_labels["down"].text()
    assert "↑" in plugin._net_labels["up"].text()
    assert plugin._uptime_label.text() != "--"
    # 曲线有数据点
    assert len(plugin._sparks["cpu"]._data) == 1
    assert len(plugin._net_spark._down) == 1
    # GPU 分区数量与 gpu_count 一致（无卡时为 0）
    assert len(plugin._gpu_sparks) == gpu_count()
    plugin.on_stop()
    assert plugin._sparks == {}
    assert plugin._net_spark is None
    widget.deleteLater()


def test_plugin_settings_order_and_hidden():
    """设置顺序/显隐生效：默认顺序、隐藏项、重排。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import tempfile
    from plugins.system_monitor.plugin import SystemMonitorPlugin

    with tempfile.TemporaryDirectory() as d:
        plugin = SystemMonitorPlugin({"data_dir": d})
        # 默认顺序
        plugin.load_settings()
        assert plugin.settings["order"] == ["cpu", "gpu", "mem", "disk", "net", "uptime"]
        assert plugin.settings["hidden"] == []

        # 自定义顺序 + 隐藏磁盘
        plugin.settings["order"] = ["net", "cpu", "mem", "uptime", "disk", "gpu"]
        plugin.settings["hidden"] = ["disk"]
        plugin.save_settings()

        plugin2 = SystemMonitorPlugin({"data_dir": d})
        plugin2.load_settings()
        assert plugin2.settings["order"] == ["net", "cpu", "mem", "uptime", "disk", "gpu"]
        assert plugin2.settings["hidden"] == ["disk"]
        # 可见项 = 顺序里去掉隐藏
        assert plugin2.visible_items() == ["net", "cpu", "mem", "uptime", "gpu"]

        # 重建 UI：隐藏项不出现
        w = plugin2.create_widget(None)
        assert "disk" not in plugin2._sparks
        assert "cpu" in plugin2._sparks
        assert plugin2._net_spark is not None
        assert plugin2._uptime_label is not None
        w.deleteLater()
