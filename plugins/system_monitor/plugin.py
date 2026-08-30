"""系统监控插件：CPU/内存/磁盘/GPU/网络实时曲线 + 开机时长。

架构（参考 Glances）：
- 采集层 plugins/system_monitor/collector/：按指标域拆分，psutil 实现
- 基类 plugins/system_monitor/base.py：注册式采集 + 节流 + 环形历史
- UI：plugins/system_monitor/widgets.py 的自绘曲线 + 本文件的分区构建

设置（右键 → 插件设置）：
- 指标顺序：上下拖动调整
- 显隐：每项勾选框，隐藏项不显示
"""
import json
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .base import SystemPlugin, throttled
from .collector import (
    NetSampler,
    cpu_percent,
    disk_io_percent,
    gpu_names,
    gpu_stats,
    mem_percent,
    uptime_hours,
)
from .widgets import (
    COLORS,
    GPU_COLOR,
    NetSpark,
    SparkLine,
    percent_color,
    DIM,
    TEXT,
)

log = logging.getLogger("system_monitor.plugin")

# 指标 key 顺序（默认显示顺序，设置里可改）
ALL_ITEMS = [
    ("cpu", "CPU"),
    ("gpu", "GPU"),
    ("mem", "内存"),
    ("disk", "磁盘"),
    ("net", "网络"),
    ("uptime", "开机"),
]

DEFAULT_ORDER = [key for key, _ in ALL_ITEMS]

# 合法指标 key（设置持久化校验用）
_KNOWN_KEYS = {key for key, _ in ALL_ITEMS}


def _fmt_mb(mb: int) -> str:
    """MB → 自适应单位（GB 显示小数，MB 显示整数）。"""
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:.1f}G" if gb < 10 else f"{gb:.0f}G"
    return f"{mb}M"


def _fmt_speed(kb_per_s: float) -> str:
    """KB/s → 自适应单位（KB/s / MB/s / GB/s）。"""
    if kb_per_s >= 1024 * 1024:
        return f"{kb_per_s / 1024 / 1024:.1f}GB/s"
    if kb_per_s >= 1024:
        return f"{kb_per_s / 1024:.1f}MB/s"
    return f"{kb_per_s:.0f}KB/s"


def _fmt_uptime(hours: float) -> str:
    total_minutes = int(hours * 60)
    days = total_minutes // (24 * 60)
    h = total_minutes % (24 * 60) // 60
    m = total_minutes % 60
    parts = []
    if days >= 1:
        parts.append(f"{days}天")
    if h >= 1:
        parts.append(f"{h}小时")
    if m > 0 and days < 1:
        parts.append(f"{m}分")
    if not parts:
        parts.append(f"{m}分")
    return " ".join(parts)


class SystemMonitorPlugin(SystemPlugin):
    id = "system_monitor"
    name = "系统监控"
    version = "0.4.0"
    description = "CPU/内存/磁盘/GPU/网络实时曲线、开机时长"
    refresh_interval = 2000

    def __init__(self, context=None):
        super().__init__(context)
        self.settings = {
            "order": list(DEFAULT_ORDER),
            "hidden": [],
        }
        self._sparks: dict[str, SparkLine] = {}
        self._pct_labels: dict[str, QLabel] = {}
        self._gpu_sparks: list[tuple[str, SparkLine, QLabel, QLabel]] = []
        self._net_spark: NetSpark | None = None
        self._net_labels: dict[str, QLabel] = {}
        self._uptime_label: QLabel | None = None
        self._net = NetSampler()

        # 注册各域采集器（对应 Glances 各插件的 update_local）
        self.register_sample("cpu", cpu_percent)
        self.register_sample("mem", mem_percent)
        self.register_sample("disk", disk_io_percent)
        # GPU：多卡自动枚举 + 降频（每 5 次 tick ≈10s）
        self.register_sample("gpu", self._gpu_util)

    # ---- GPU 采集（降频 + 多卡） ----

    @throttled(5)
    def _gpu_util(self) -> list[dict]:
        return gpu_stats()

    # ---- 设置持久化 ----

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def load_settings(self) -> None:
        """读取设置；默认顺序 + 空 hidden，缺项补齐。"""
        self.settings = {
            "order": list(DEFAULT_ORDER),
            "hidden": [],
        }
        if self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                order = saved.get("order")
                if isinstance(order, list) and order:
                    # 只保留已知 key，缺失的补到末尾
                    known = [k for k in order if k in _KNOWN_KEYS]
                    for k in DEFAULT_ORDER:
                        if k not in known:
                            known.append(k)
                    self.settings["order"] = known
                hidden = saved.get("hidden")
                if isinstance(hidden, list):
                    self.settings["hidden"] = [k for k in hidden if k in _KNOWN_KEYS]
            except (json.JSONDecodeError, OSError):
                log.warning("settings.json 解析失败，使用默认值")

    def save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(self.settings, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            log.exception("保存设置失败")

    def visible_items(self) -> list[str]:
        """按设置顺序返回可见的指标 key（hidden 里的跳过）。"""
        hidden = set(self.settings.get("hidden") or [])
        return [k for k in self.settings.get("order") or DEFAULT_ORDER if k not in hidden]

    # ---- UI ----

    def create_widget(self, parent) -> QWidget:
        self.load_settings()
        widget = QWidget(parent)
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(5)

        def make_gauge(key: str, label: str, color: str) -> None:
            name = QLabel(label)
            name.setStyleSheet(f"font-size: 12px; color: {DIM()};")
            pct = QLabel("--")
            pct.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {DIM()};")
            head = QWidget()
            head_lay = QHBoxLayout(head)
            head_lay.setContentsMargins(0, 0, 0, 0)
            head_lay.setSpacing(6)
            head_lay.addWidget(name)
            head_lay.addStretch(1)
            head_lay.addWidget(pct)
            lay.addWidget(head)

            spark = SparkLine(color)
            lay.addWidget(spark)
            self._sparks[key] = spark
            self._pct_labels[key] = pct

        def make_net() -> None:
            net_head = QWidget()
            net_head_lay = QHBoxLayout(net_head)
            net_head_lay.setContentsMargins(0, 0, 0, 0)
            net_head_lay.setSpacing(6)
            net_label = QLabel("网络")
            net_label.setStyleSheet(f"font-size: 12px; color: {DIM()};")
            net_head_lay.addWidget(net_label)
            net_head_lay.addStretch(1)
            down_lbl = QLabel("↓--")
            down_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT()};")
            up_lbl = QLabel("↑--")
            up_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT()};")
            net_head_lay.addWidget(down_lbl)
            net_head_lay.addWidget(up_lbl)
            lay.addWidget(net_head)
            self._net_labels["down"] = down_lbl
            self._net_labels["up"] = up_lbl

            net_spark = NetSpark()
            lay.addWidget(net_spark)
            self._net_spark = net_spark

        def make_uptime() -> None:
            uptime_box = QFrame()
            uptime_box.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;")
            uptime_lay = QHBoxLayout(uptime_box)
            uptime_lay.setContentsMargins(8, 4, 8, 4)
            uptime_lay.setSpacing(6)
            up_label = QLabel("开机")
            up_label.setStyleSheet(f"font-size: 11px; color: {DIM()};")
            uptime_lay.addWidget(up_label)
            uptime_lay.addStretch(1)
            self._uptime_label = QLabel("--")
            self._uptime_label.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {TEXT()};")
            uptime_lay.addWidget(self._uptime_label)
            lay.addWidget(uptime_box)

        # GPU 分区构建（自动枚举，每块一行曲线）
        gpu_list = gpu_names()

        def make_gpu() -> None:
            for i, gname in enumerate(gpu_list):
                name = QLabel(gname)
                name.setStyleSheet(f"font-size: 11px; color: {DIM()};")
                name.setToolTip(gname)
                pct = QLabel("--")
                pct.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {DIM()};")
                mem_lbl = QLabel("")
                mem_lbl.setStyleSheet(f"font-size: 10px; color: {DIM()};")
                head = QWidget()
                head_lay = QHBoxLayout(head)
                head_lay.setContentsMargins(0, 0, 0, 0)
                head_lay.setSpacing(6)
                head_lay.addWidget(name)
                head_lay.addStretch(1)
                head_lay.addWidget(pct)
                head_lay.addWidget(mem_lbl)
                lay.addWidget(head)

                spark = SparkLine(GPU_COLOR)
                lay.addWidget(spark)
                self._gpu_sparks.append((f"gpu{i}", spark, pct, mem_lbl))

        for key in self.visible_items():
            if key == "gpu":
                if gpu_list:
                    make_gpu()
            elif key in COLORS:
                label = dict(ALL_ITEMS)[key]
                make_gauge(key, label, COLORS[key])
            elif key == "net":
                make_net()
            elif key == "uptime":
                make_uptime()

        widget.setLayout(lay)
        return widget

    # ---- 刷新 ----

    def tick(self) -> None:
        # 任一分区可见（含开机时长）都要刷新；全隐藏时才跳过采样
        if not (self._sparks or self._gpu_sparks
                or self._net_spark is not None
                or self._uptime_label is not None):
            return
        self.collect()

        if "cpu" in self._sparks:
            cpu = int(self._stats.get("cpu", 0))
            self._sparks["cpu"].add(cpu)
            self._pct_labels["cpu"].setText(f"{cpu}%")
            self._pct_labels["cpu"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(cpu)};")

        if "mem" in self._sparks:
            mem = int(self._stats.get("mem", 0))
            self._sparks["mem"].add(mem)
            self._pct_labels["mem"].setText(f"{mem}%")
            self._pct_labels["mem"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(mem)};")

        if "disk" in self._sparks:
            disk = int(self._stats.get("disk", 0))
            self._sparks["disk"].add(disk)
            self._pct_labels["disk"].setText(f"{disk}%")
            self._pct_labels["disk"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(disk)};")

        # GPU：多卡自动更新（采集已由基类节流降频，见 _gpu_util）
        gpu_data = self._stats.get("gpu", [])
        if not isinstance(gpu_data, list):
            gpu_data = []
        for idx, (key, spark, pct_lbl, mem_lbl) in enumerate(self._gpu_sparks):
            if idx < len(gpu_data):
                d = gpu_data[idx]
                util = int(d["util"])
                spark.add(util)
                pct_lbl.setText(f"{util}%")
                pct_lbl.setStyleSheet(
                    f"font-size: 12px; font-weight: 700; color: {percent_color(util)};")
                if d["mem_total_mb"] > 0:
                    mem_lbl.setText(
                        f"{_fmt_mb(d['mem_used_mb'])}/{_fmt_mb(d['mem_total_mb'])}")
                else:
                    mem_lbl.setText("")

        if self._net_spark is not None:
            down, up = self._net.sample()
            self._net_spark.add(down, up)
            self._net_labels["down"].setText(f"↓{_fmt_speed(down)}")
            self._net_labels["up"].setText(f"↑{_fmt_speed(up)}")

        if self._uptime_label is not None:
            hours = uptime_hours()
            self._uptime_label.setText(_fmt_uptime(hours))

    def on_stop(self) -> None:
        super().on_stop()
        self._sparks = {}
        self._pct_labels = {}
        self._gpu_sparks = []
        self._net_spark = None
        self._net_labels = {}
        self._uptime_label = None
        self._net.reset()

    def settings_dialog(self, parent=None):
        from .settings_dialog import SettingsDialog

        return SettingsDialog(self, parent)


def create_plugin() -> SystemMonitorPlugin:
    return SystemMonitorPlugin()
