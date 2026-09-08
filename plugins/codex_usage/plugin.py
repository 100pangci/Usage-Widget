"""Codex CLI 用量悬浮窗插件。

显示内容与 `codex` 内 `/status` 的订阅用量一致：短周期窗口、每周窗口、
百分比和重置倒计时。Cookie 读取和网络请求均在插件内部完成。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from plugins.base import Plugin

from .api import CodexClient, CodexError, CodexUsage, UsageWindow
from .format import format_plan_name, format_reset_time

log = logging.getLogger("codex_usage.plugin")

DEFAULT_SETTINGS = {
    "cookie_path": "",
    "account_id": "",
    "proxy": "auto",
    "refresh_interval_ms": 60000,
}

_FALLBACK_COLORS = {
    "dim": "#9aa3b5",
    "text": "#dfe3ea",
    "green": "#7cc76b",
    "amber": "#e5b94d",
    "red": "#e06c5a",
}


def _theme_colors() -> dict:
    try:
        from core.theme import color

        return {k: color(k) for k in _FALLBACK_COLORS}
    except ImportError:
        return _FALLBACK_COLORS


def DIM() -> str:
    return _theme_colors()["dim"]


def TEXT() -> str:
    return _theme_colors()["text"]


def GREEN() -> str:
    return _theme_colors()["green"]


def AMBER() -> str:
    return _theme_colors()["amber"]


def RED() -> str:
    return _theme_colors()["red"]


def percent_color(percent: int) -> str:
    if percent >= 80:
        return RED()
    if percent >= 50:
        return AMBER()
    return GREEN()


class UsageBar(QWidget):
    """自绘细进度条，兼容悬浮窗的透明/分层合成。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_percent(self, percent: int) -> None:
        self._percent = max(0, min(100, int(percent)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2.0
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(self.rect(), radius, radius)
        if self._percent > 0:
            p.setBrush(QColor(percent_color(self._percent)))
            width = max(self.height(), int(self.width() * self._percent / 100))
            p.drawRoundedRect(0, 0, width, self.height(), radius, radius)
        p.end()


_RUNNING_WORKERS: set = set()


def _release_worker(worker) -> None:
    worker.destroyed.connect(lambda *_: _RUNNING_WORKERS.discard(worker))
    worker.deleteLater()


class FetchWorker(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            usage = self._client.usage()
            if not self.isInterruptionRequested():
                self.ok.emit(usage)
        except CodexError as e:
            if not self.isInterruptionRequested():
                self.fail.emit(str(e))
        except Exception as e:
            log.exception("Codex 用量获取失败")
            if not self.isInterruptionRequested():
                self.fail.emit(str(e))


class CodexUsagePlugin(Plugin):
    id = "codex_usage"
    name = "Codex 用量"
    version = "0.1.0"
    description = "Codex CLI /status 用量与重置时间"
    refresh_interval = 60000

    def __init__(self, context=None):
        super().__init__(context)
        self.settings = dict(DEFAULT_SETTINGS)
        self._client = None
        self._worker = None
        self._labels: dict[str, QLabel | UsageBar] = {}
        self._windows: dict[str, UsageWindow | None] = {}
        self._plan_type = ""
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick_countdown)

    @staticmethod
    def _sanitize_refresh_ms(value) -> int:
        try:
            return max(30000, int(value))
        except (TypeError, ValueError):
            return 60000

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def load_settings(self) -> None:
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings["cookie_path"] = str(self.data_dir / "cookie.txt")
        if self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.settings.update({
                        key: value for key, value in saved.items()
                        if key in DEFAULT_SETTINGS
                    })
            except (json.JSONDecodeError, OSError):
                log.warning("Codex settings.json 解析失败，使用默认值")
        self.refresh_interval = self._sanitize_refresh_ms(
            self.settings["refresh_interval_ms"])

    def save_settings(self, new_settings: dict) -> None:
        self.settings.update({
            key: value for key, value in new_settings.items()
            if key in DEFAULT_SETTINGS
        })
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2),
            encoding="utf-8")
        self.refresh_interval = self._sanitize_refresh_ms(
            self.settings["refresh_interval_ms"])
        self._client = None

    def cookie_configured(self) -> bool:
        path = self.settings.get("cookie_path")
        if not path:
            return False
        try:
            return bool(Path(path).expanduser().read_text(
                encoding="utf-8", errors="replace").strip())
        except OSError:
            return False

    def make_client(self):
        if not self.cookie_configured():
            return None
        if self._client is None:
            self._client = CodexClient(
                cookie_path=self.settings["cookie_path"],
                account_id=self.settings.get("account_id", ""),
                proxy=self.settings.get("proxy", "auto"),
            )
        return self._client

    # ---- 生命周期 ----

    def on_start(self) -> None:
        self.load_settings()
        self._ticker.start()

    def on_stop(self) -> None:
        self._ticker.stop()
        worker = self._worker
        if worker is not None:
            self._worker = None
            worker.requestInterruption()
        self._labels = {}
        self._windows = {}
        self._plan_type = ""

    def tick(self) -> None:
        if self._worker is not None:
            return
        if not self.cookie_configured():
            self._set_status("未配置 Cookie：右键 → 插件设置")
            return
        client = self.make_client()
        if client is None:
            self._set_status("未配置 Cookie：右键 → 插件设置")
            return
        self._set_status("获取中…")
        worker = FetchWorker(client)
        worker.ok.connect(self._apply_usage)
        worker.fail.connect(self._apply_error)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(lambda w=worker: _release_worker(w))
        _RUNNING_WORKERS.add(worker)
        self._worker = worker
        worker.start()

    def refresh_now(self) -> None:
        self.tick()

    def _on_worker_finished(self) -> None:
        if self._worker is not None and self._worker.isFinished():
            self._worker = None

    # ---- 数据刷新 ----

    def _apply_usage(self, usage: CodexUsage) -> None:
        if not self._labels:
            return
        self._windows = {
            "primary": usage.primary_window,
            "secondary": usage.secondary_window,
        }
        self._plan_type = usage.plan_type
        self._labels["plan"].setText(format_plan_name(usage.plan_type))
        self._render_window("primary")
        self._render_window("secondary")
        suffix = " · 已达到限制" if usage.limit_reached else ""
        self._set_status(f"更新 {datetime.now():%H:%M}{suffix}")

    def _render_window(self, key: str) -> None:
        window = self._windows.get(key)
        pct = self._labels[f"{key}_pct"]
        bar = self._labels[f"{key}_bar"]
        reset = self._labels[f"{key}_reset"]
        if window is None:
            pct.setText("--")
            pct.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {DIM()};")
            bar.set_percent(0)
            reset.setText("")
            reset.setToolTip("")
            return
        pct.setText(f"{window.used_percent}%")
        pct.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {percent_color(window.used_percent)};")
        bar.set_percent(window.used_percent)
        self._render_reset(key)

    def _render_reset(self, key: str) -> None:
        window = self._windows.get(key)
        reset = self._labels.get(f"{key}_reset")
        if window is None or window.reset_at is None or reset is None:
            return
        left = max(0, int(window.reset_at - time.time()))
        reset.setText(f"重置于 {format_reset_time(left)}")
        try:
            exact = datetime.fromtimestamp(window.reset_at).strftime(
                "%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            exact = ""
        reset.setToolTip(exact)

    def _tick_countdown(self) -> None:
        for key in ("primary", "secondary"):
            if self._windows.get(key) is not None:
                self._render_reset(key)

    def _apply_error(self, message: str) -> None:
        self._set_status(f"获取失败：{message}")

    def _set_status(self, text: str) -> None:
        label = self._labels.get("status")
        if label is not None:
            label.setText(text)

    # ---- UI ----

    def create_widget(self, parent) -> QWidget:
        if not self.settings.get("cookie_path"):
            self.load_settings()
        self._labels = {}
        widget = QWidget(parent)
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(5)

        def make_window(key: str, title: str) -> None:
            name = QLabel(title)
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

            bar = UsageBar()
            reset = QLabel("")
            reset.setStyleSheet(f"font-size: 11px; color: {DIM()};")
            foot = QWidget()
            foot_lay = QHBoxLayout(foot)
            foot_lay.setContentsMargins(0, 0, 0, 0)
            foot_lay.setSpacing(8)
            foot_lay.addWidget(bar, 1)
            foot_lay.addWidget(reset)

            lay.addWidget(head)
            lay.addWidget(foot)
            self._labels[f"{key}_pct"] = pct
            self._labels[f"{key}_bar"] = bar
            self._labels[f"{key}_reset"] = reset

        make_window("primary", "5 小时窗口")
        make_window("secondary", "每周窗口")

        plan = QLabel("--")
        plan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plan.setStyleSheet(f"font-size: 12px; color: {DIM()};")
        lay.addWidget(plan)
        self._labels["plan"] = plan

        status = QLabel("初始化…")
        status.setWordWrap(True)
        status.setStyleSheet(f"font-size: 11px; color: {DIM()};")
        lay.addWidget(status)
        self._labels["status"] = status
        return widget

    def settings_dialog(self, parent=None):
        from .settings_dialog import SettingsDialog

        return SettingsDialog(self, parent)


def create_plugin() -> CodexUsagePlugin:
    return CodexUsagePlugin()
