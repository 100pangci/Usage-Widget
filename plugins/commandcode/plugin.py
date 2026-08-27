"""commandcode 用量插件：5小时/每周窗口用量、本月花费与剩余 credits。

数据流（后台线程，不阻塞 UI）：
    /internal/usage/summary          → 本月请求数/花费/tokens
    /internal/billing/credits        → 剩余 credits + 5小时/每周窗口用量
    /internal/billing/subscriptions  → 订阅计划与计费周期
配置持久化在插件数据目录 ~/.usage-widget/plugin/commandcode/settings.json，
cookie 由用户在设置对话框粘贴（保存到 cookie.txt）。
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from plugins.base import Plugin

from .api import CommandCodeClient, CommandCodeError, Credits, Subscription, UsageSummary
from .format import (
    format_credits,
    format_plan_name,
    format_reset_time,
    format_tokens,
    format_usd,
    plan_total_credits,
)
from .proxy import detect_proxy

log = logging.getLogger("commandcode.plugin")

DEFAULT_SETTINGS = {
    "cookie_path": "",          # 保存到 data_dir/cookie.txt（见 load_settings）
    "proxy": "auto",            # auto / none / http://...
    "refresh_interval_ms": 60000,  # 轮询；1 分钟一次，倒计时本地递减
}

ACCENT = "#4f8cff"
DIM = "#9aa3b5"
GREEN = "#7cc76b"
AMBER = "#e5b94d"
RED = "#e06c5a"

# 仍在跑的取数线程驻留表：插件停止/重载时不在 UI 线程 wait()（那会
# 卡死界面），而是把引用移交到这里直到线程自然结束，避免「QThread
# 销毁时线程还在运行」；线程结束后统一释放。
_RUNNING_WORKERS: set = set()


def _release_worker(worker) -> None:
    """取数线程结束后的统一清理：排定删除并等销毁后再撤驻留引用。

    引用必须在 destroyed 之后才能撤销，否则 wrapper 可能先于事件
    循环删除线程对象而被 GC，绕开安全的 deleteLater 路径。
    """
    worker.destroyed.connect(
        lambda *_: _RUNNING_WORKERS.discard(worker))
    worker.deleteLater()


def percent_color(percent: int) -> str:
    """使用率颜色：<50% 绿 / <80% 黄 / ≥80% 红。"""
    if percent >= 80:
        return RED
    if percent >= 50:
        return AMBER
    return GREEN


class UsageBar(QWidget):
    """细进度条（QPainter 自绘，避免 QSS 在分层窗口下的合成问题）。"""

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


class FetchWorker(QThread):
    """后台拉取汇总 + credits + 订阅，完成后发 ok/fail 信号。

    不设父对象：stop 时可能要脱离插件实例独立跑完当前网络请求，
    存活期由模块级 _RUNNING_WORKERS 驻留表保证。
    """

    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            stats = self._fetch()
            if stats is not None and not self.isInterruptionRequested():
                self.ok.emit(stats)
        except CommandCodeError as e:
            if not self.isInterruptionRequested():
                self.fail.emit(str(e))
        except Exception as e:
            log.exception("取数失败")
            if not self.isInterruptionRequested():
                self.fail.emit(str(e))

    def _fetch(self) -> dict | None:
        client = self._client
        summary = client.usage_summary()
        if self.isInterruptionRequested():
            return None
        credits = client.credits()
        if self.isInterruptionRequested():
            return None
        sub = client.subscription()
        return {
            "summary": summary,
            "credits": credits,
            "subscription": sub,
            "fetched_at": datetime.now().strftime("%H:%M"),
        }


class CommandCodePlugin(Plugin):
    id = "commandcode"
    name = "commandcode 用量"
    version = "0.1.0"
    description = "5小时/每周用量、本月花费与剩余 credits"
    refresh_interval = 60000

    def __init__(self, context=None):
        super().__init__(context)
        self.settings = dict(DEFAULT_SETTINGS)
        self._client = None
        self._worker = None
        self._labels = {}
        # 重置倒计时本地递减：记录窗口 resetAt（epoch 毫秒）与计费周期结束，每秒刷新
        self._limits: dict[str, object] = {}
        self._period_end_ms: int | None = None
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick_countdown)

    @staticmethod
    def _sanitize_refresh_ms(value) -> int:
        try:
            return max(30000, int(value))
        except (TypeError, ValueError):
            return 60000

    # ---- 设置持久化 ----

    @property
    def settings_path(self):
        return self.data_dir / "settings.json"

    def load_settings(self) -> None:
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings["cookie_path"] = str(self.data_dir / "cookie.txt")
        if self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                self.settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
            except (json.JSONDecodeError, OSError):
                log.warning("settings.json 解析失败，使用默认值")
        self.refresh_interval = self._sanitize_refresh_ms(self.settings["refresh_interval_ms"])

    def save_settings(self, new_settings: dict) -> None:
        self.settings.update(new_settings)
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_interval = self._sanitize_refresh_ms(self.settings["refresh_interval_ms"])
        self._client = None

    # ---- 客户端 ----

    def cookie_configured(self) -> bool:
        """cookie 已持久化且非空才算配置完成。"""
        path = self.settings.get("cookie_path")
        if not path:
            return False
        try:
            return bool(Path(path).read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            return False

    def make_client(self):
        if not self.cookie_configured():
            return None
        if self._client is None:
            proxy = None
            if self.settings["proxy"] == "auto":
                proxy = detect_proxy()
                log.info("系统代理: %s", proxy or "无")
            elif self.settings["proxy"] != "none":
                proxy = self.settings["proxy"]
            self._client = CommandCodeClient(
                cookie_path=self.settings["cookie_path"],
                proxy=proxy,
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
            # 先摘引用：随后的 finished 不会误伤重建后的新实例；
            # 标记中断即可返回。绝不能在 UI 线程 wait()——一次网络
            # 请求最长 15s、多个接口顺序执行可达 30s+，用户会当成
            # 「重新加载卡死」。线程存活期由 _RUNNING_WORKERS 保证。
            self._worker = None
            worker.requestInterruption()
        # 清掉标签引用：迟到的取数结果不再触碰已销毁的控件
        self._labels = {}
        self._limits = {}
        self._period_end_ms = None

    def tick(self) -> None:
        if self._worker is not None:
            return
        if not self.cookie_configured():
            self._set_status("未配置 cookie：右键 → 插件设置 粘贴后启用")
            return
        client = self.make_client()
        if client is None:
            self._set_status("未配置 cookie：右键 → 插件设置 粘贴后启用")
            return
        self._set_status("获取中…")
        worker = FetchWorker(client)
        worker.ok.connect(self._apply_stats)
        worker.fail.connect(self._apply_error)
        worker.finished.connect(self._on_worker_finished)
        # finished 无参信号不会给普通函数传参，需用默认参数绑定 worker
        worker.finished.connect(
            lambda w=worker: _release_worker(w))
        _RUNNING_WORKERS.add(worker)
        self._worker = worker
        worker.start()

    def refresh_now(self) -> None:
        self.tick()

    def _on_worker_finished(self) -> None:
        if self._worker is not None and self._worker.isFinished():
            self._worker = None

    # ---- 数据落地 ----

    def _apply_stats(self, stats: dict) -> None:
        if not self._labels:
            return
        summary: UsageSummary = stats["summary"]
        credits: Credits = stats["credits"]
        sub: Subscription | None = stats["subscription"]

        self._limits = {}
        now_ms = int(time.time() * 1000)
        for key, limit in (("five_hour", credits.five_hour), ("weekly", credits.weekly)):
            percent = limit.percent if limit else 0
            self._labels[f"{key}_bar"].set_percent(percent)
            self._labels[f"{key}_pct"].setText(f"{percent}%")
            self._labels[f"{key}_pct"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(percent)};")
            self._labels[f"{key}_reset"].setText(
                f"重置于 {format_reset_time(max(0, (limit.reset_at_ms - now_ms) // 1000))}"
                if limit else "")
            if limit:
                self._limits[key] = limit

        # 每月用量：总额 - 剩余 = 已用，重置时间 = 计费周期结束
        total = plan_total_credits(sub.plan_id) if sub is not None else None
        self._period_end_ms = None
        if total and total > 0:
            used = max(0.0, total - credits.monthly_remaining)
            percent = int(used / total * 100)
            self._labels["monthly_bar"].set_percent(percent)
            self._labels["monthly_pct"].setText(f"{percent}%")
            self._labels["monthly_pct"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(percent)};")
            if sub is not None and sub.period_end is not None:
                self._period_end_ms = int(sub.period_end.timestamp() * 1000)
                left = max(0, (self._period_end_ms - now_ms) // 1000)
                self._labels["monthly_reset"].setText(f"重置于 {format_reset_time(left)}")
        else:
            self._labels["monthly_bar"].set_percent(0)
            self._labels["monthly_pct"].setText("--")
            self._labels["monthly_pct"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {DIM};")
            self._labels["monthly_reset"].setText("")

        self._labels["month_value"].setText(format_usd(summary.total_cost))
        self._labels["stats"].setText(
            f"{summary.total_count} 次请求 · {format_tokens(summary.total_tokens)} tokens")

        if sub is not None:
            self._labels["plan"].setText(format_plan_name(sub.plan_id))
        else:
            self._labels["plan"].setText("无订阅")

        self._set_status(f"更新 {stats['fetched_at']} · 代理 {'✓' if self._proxy_active() else '—'}")

    def _tick_countdown(self) -> None:
        """本地每秒递减重置倒计时（不重新请求网络）。"""
        if not self._limits and self._period_end_ms is None:
            return
        now_ms = int(time.time() * 1000)
        for key, limit in self._limits.items():
            left = max(0, (limit.reset_at_ms - now_ms) // 1000)
            self._labels[f"{key}_reset"].setText(f"重置于 {format_reset_time(left)}")
        if self._period_end_ms is not None:
            left = max(0, (self._period_end_ms - now_ms) // 1000)
            self._labels["monthly_reset"].setText(f"重置于 {format_reset_time(left)}")

    def _apply_error(self, message: str) -> None:
        self._set_status(f"获取失败：{message}")

    def _proxy_active(self) -> bool:
        return self.settings["proxy"] != "none" and detect_proxy() is not None

    def _set_status(self, text: str) -> None:
        if "status" in self._labels:
            self._labels["status"].setText(text)

    # ---- UI ----

    def create_widget(self, parent) -> QWidget:
        if not self.settings.get("cookie_path"):
            self.load_settings()
        widget = QWidget(parent)
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(5)

        def make_window(key: str, label: str) -> None:
            name = QLabel(label)
            name.setStyleSheet(f"font-size: 12px; color: {DIM};")
            pct = QLabel("--")
            pct.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {DIM};")
            head = QWidget()
            head_lay = QHBoxLayout(head)
            head_lay.setContentsMargins(0, 0, 0, 0)
            head_lay.setSpacing(6)
            head_lay.addWidget(name)
            head_lay.addStretch(1)
            head_lay.addWidget(pct)

            bar = UsageBar()
            reset = QLabel("")
            reset.setStyleSheet(f"font-size: 11px; color: {DIM};")
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

        make_window("five_hour", "滚动用量")
        make_window("weekly", "每周用量")
        make_window("monthly", "每月用量")

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(255,255,255,26);")
        lay.addWidget(line)

        month_caption = QLabel("本月蹬了：")
        month_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        month_caption.setStyleSheet(f"font-size: 11px; color: {DIM}; letter-spacing: 1px;")
        lay.addWidget(month_caption)

        month_value = QLabel("--")
        month_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        month_value.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {ACCENT};")
        lay.addWidget(month_value)
        self._labels["month_value"] = month_value

        stats = QLabel("")
        stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats.setStyleSheet(f"font-size: 11px; color: {DIM};")
        lay.addWidget(stats)
        self._labels["stats"] = stats

        plan = QLabel("")
        plan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plan.setStyleSheet(f"font-size: 11px; color: {DIM};")
        lay.addWidget(plan)
        self._labels["plan"] = plan

        status = QLabel("初始化…")
        status.setStyleSheet(f"font-size: 11px; color: {DIM};")
        status.setWordWrap(True)
        lay.addWidget(status)
        self._labels["status"] = status
        return widget

    def settings_dialog(self, parent=None):
        from .settings_dialog import SettingsDialog

        return SettingsDialog(self, parent)


def create_plugin() -> CommandCodePlugin:
    return CommandCodePlugin()