"""系统监控插件测试：指标采集器返回合理值、格式化函数、插件刷新。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.system_monitor.collector import (
    NetSampler,
    cpu_percent,
    disk_percent,
    mem_percent,
    uptime_hours,
)
from plugins.system_monitor.plugin import _fmt_speed, _fmt_uptime


# ---- 指标采集 ----

def test_cpu_percent_range():
    for _ in range(2):
        v = cpu_percent()
        assert 0 <= v <= 100


def test_mem_percent_range():
    v = mem_percent()
    assert 0 <= v <= 100


def test_disk_percent_range():
    v = disk_percent()
    assert 0 <= v <= 100


def test_uptime_positive():
    assert uptime_hours() > 0


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
    plugin.on_stop()
    assert plugin._sparks == {}
    assert plugin._net_spark is None
    widget.deleteLater()
