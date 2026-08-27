"""commandcode 用量插件设置对话框：cookie / 代理 / 刷新间隔。

- cookie：session_token 与 session_data 分开填写，或粘贴完整 Cookie 由
  「从剪贴板/从文件读取」自动拆分；持久化到插件数据目录（0600）
- 代理：自动（系统代理）/ 无 / 自定义
- 测试连接：验证 cookie 并拉取本月花费与剩余 credits
"""
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .api import DATA_COOKIE, TOKEN_COOKIE, CommandCodeClient, CommandCodeError, parse_cookie_text
from .format import format_credits, format_usd
from .proxy import detect_proxy

log = logging.getLogger("commandcode.settings")


class _TaskThread(QThread):
    """后台跑一个任务，完成时发 done(结果) 信号（避免 UI 卡死）。"""

    done = Signal(object)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):
        try:
            self.done.emit(("ok", self._task()))
        except Exception as e:
            self.done.emit(("error", str(e)))


class SettingsDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"{plugin.name} · 设置")
        self.setMinimumWidth(480)

        settings = plugin.settings
        self._task = None

        # -- cookie：token / data 分开填 --
        self._token_edit = QLineEdit()
        self._token_edit.setPlaceholderText("__Secure-commandcode_prod_.session_token 的值")
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._data_edit = QLineEdit()
        self._data_edit.setPlaceholderText("__Secure-commandcode_prod_.session_data 的值")
        self._data_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self._cookie_from_clip = QPushButton("从剪贴板解析")
        self._cookie_from_clip.clicked.connect(self._cookie_from_clipboard)
        self._cookie_from_file = QPushButton("从文件读取")
        self._cookie_from_file.clicked.connect(self._cookie_from_file_picker)
        paste_row = QWidget()
        paste_lay = QHBoxLayout(paste_row)
        paste_lay.setContentsMargins(0, 0, 0, 0)
        paste_lay.addWidget(self._cookie_from_clip)
        paste_lay.addWidget(self._cookie_from_file)

        self._cookie_path_edit = QLineEdit(str(settings["cookie_path"]))
        self._cookie_hint = QLabel(
            "分开填：浏览器 F12 → 网络 → 请求头 Cookie 里两个值分别复制；"
            "也可粘贴完整 Cookie 整串后点「从剪贴板解析」自动拆分。"
            "cookie 将保存到此文件（权限 600）")
        self._cookie_hint.setWordWrap(True)
        self._cookie_hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        # -- 代理 --
        self._proxy_combo = QComboBox()
        self._proxy_combo.setEditable(True)
        self._proxy_combo.addItems(["auto（系统代理）", "none（直连）"])
        if settings["proxy"] not in ("auto", "none"):
            self._proxy_combo.addItem(settings["proxy"])
        idx = 0 if settings["proxy"] == "auto" else (1 if settings["proxy"] == "none" else 2)
        self._proxy_combo.setCurrentIndex(idx)

        # -- 刷新 --
        self._refresh_spin = QSpinBox()
        self._refresh_spin.setRange(30, 600)
        self._refresh_spin.setSingleStep(30)
        self._refresh_spin.setValue(int(settings["refresh_interval_ms"]) // 1000)
        self._refresh_spin.setSuffix(" s")

        # -- 测试连接 --
        self._test_btn = QPushButton("测试连接")
        self._test_btn.clicked.connect(self._test_connection)
        self._test_label = QLabel("")
        self._test_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("session_token", self._token_edit)
        form.addRow("session_data", self._data_edit)
        form.addRow("", paste_row)
        form.addRow("保存到", self._cookie_path_edit)
        form.addRow("", self._cookie_hint)
        form.addRow("代理", self._proxy_combo)
        form.addRow("刷新间隔", self._refresh_spin)
        form.addRow(self._test_btn, self._test_label)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.setLayout(lay)

        self._load_cookie_preview()

    # ---- cookie ----

    def _fill_from_text(self, text: str) -> None:
        """把一段文本拆分填入两个输入框；识别不了就整个放进 token 框。"""
        parsed = parse_cookie_text(text)
        if parsed is not None:
            token, data = parsed
            if token:
                self._token_edit.setText(token)
            if data:
                self._data_edit.setText(data)
        else:
            self._token_edit.setText(text)

    def _combined_cookie(self) -> str:
        """合并两个输入框为完整 Cookie header（有值的才加）。"""
        token = self._token_edit.text().strip()
        data = self._data_edit.text().strip()
        parts = []
        if token:
            parts.append(f"{TOKEN_COOKIE}={token}")
        if data:
            parts.append(f"{DATA_COOKIE}={data}")
        return "; ".join(parts)

    def _cookie_from_clipboard(self):
        from PySide6.QtWidgets import QApplication

        clip = QApplication.clipboard().text().strip()
        if clip:
            self._fill_from_text(clip)

    def _cookie_from_file_picker(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "选择 cookie 文件")
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError as e:
            self._test_label.setText(f"读取失败: {e}")
            return
        self._fill_from_text(text)

    def _load_cookie_preview(self):
        path = self.plugin.settings["cookie_path"]
        try:
            text = open(path, encoding="utf-8", errors="replace").read().strip()
        except OSError:
            return
        if text:
            self._fill_from_text(text)

    # ---- 测试连接 ----

    def _test_connection(self):
        self._test_btn.setEnabled(False)
        self._test_label.setText("测试中…")
        cookie_text = self._combined_cookie()
        cookie_path = self._cookie_path_edit.text().strip()
        if not cookie_text:
            self._test_label.setText("请先填写 session_token / session_data")
            self._test_btn.setEnabled(True)
            return
        if parse_cookie_text(cookie_text) is None:
            self._test_label.setStyleSheet("color: #e06c5a;")
            self._test_label.setText("cookie 格式无法识别")
            self._test_btn.setEnabled(True)
            return

        def task():
            client = self._make_client_from_cookie(cookie_text, cookie_path)
            session = client.session()
            summary = client.usage_summary()
            credits = client.credits()
            name = (session.get("user") or {}).get("name") or "未知用户"
            return (f"连接成功：{name} · 本月已用 {format_usd(summary.total_cost)} · "
                    f"剩余 {format_credits(credits.monthly_remaining)} credits")

        self._start_task(task, self._on_test_result)

    def _on_test_result(self, result):
        self._test_btn.setEnabled(True)
        status, msg = result
        if status == "ok":
            self._test_label.setStyleSheet("color: #7cc76b;")
        else:
            self._test_label.setStyleSheet("color: #e06c5a;")
        self._test_label.setText(msg)

    # ---- 保存 ----

    def _save(self):
        cookie_text = self._combined_cookie()
        cookie_path = self._cookie_path_edit.text().strip()
        if not cookie_path:
            self._test_label.setText("cookie 保存路径不能为空")
            return
        try:
            import os

            if cookie_text:
                if parse_cookie_text(cookie_text) is None:
                    self._test_label.setText("cookie 格式无法识别")
                    return
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(cookie_text + "\n")
                if os.name != "nt":
                    os.chmod(cookie_path, 0o600)
            elif not os.path.isfile(cookie_path):
                self._test_label.setText("请先填写 session_token / session_data")
                return
        except OSError as e:
            self._test_label.setText(f"保存 cookie 失败: {e}")
            return

        proxy = self._proxy_combo.currentText().strip()
        if proxy.startswith("auto"):
            proxy = "auto"
        elif proxy.startswith("none"):
            proxy = "none"

        self.plugin.save_settings({
            "cookie_path": cookie_path,
            "proxy": proxy,
            "refresh_interval_ms": int(self._refresh_spin.value()) * 1000,
        })
        self.plugin.refresh_now()
        self.accept()

    # ---- 工具 ----

    def _current_proxy(self) -> str | None:
        """按代理下拉框当前值返回代理 URL（None = 直连）。"""
        text = self._proxy_combo.currentText().strip()
        if text.startswith("auto"):
            return detect_proxy()
        if text.startswith("none"):
            return None
        return text or None

    def _make_client_from_cookie(self, cookie_text: str, cookie_path: str):
        """先把 cookie 写入目标文件，再建客户端（保存前测试连接用）。"""
        path = Path(cookie_path or self.plugin.settings["cookie_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cookie_text + "\n", encoding="utf-8")
        return CommandCodeClient(cookie_path=path, proxy=self._current_proxy())

    def _start_task(self, task, on_done):
        if self._task is not None and self._task.isRunning():
            return
        self._task = _TaskThread(task, self)
        self._task.done.connect(on_done)
        self._task.finished.connect(self._on_task_finished)
        self._task.start()

    def _on_task_finished(self):
        thread = self._task
        self._task = None
        if thread is not None:
            thread.deleteLater()