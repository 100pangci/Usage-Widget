"""Codex 用量插件设置：Cookie、账户 ID、代理和刷新间隔。"""
from __future__ import annotations

import json
import os
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .api import CodexClient, CodexError, parse_auth_text, serialize_auth


class _TaskThread(QThread):
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
        self.setMinimumWidth(500)
        self._task = None

        # 用多行文本框而不是 QLineEdit：QLineEdit 默认最多 32767 字符，
        # /api/auth/session 的完整 JSON 已超过该长度，会被静默截断成非法 JSON。
        self._cookie_edit = QPlainTextEdit()
        self._cookie_edit.setPlaceholderText(
            "粘贴 /api/auth/session 完整 JSON、Cookie、access token 或 auth.json"
            "（保存时自动只保留必要字段）")
        self._cookie_edit.setFixedHeight(84)
        self._cookie_edit.setTabChangesFocus(True)
        self._cookie_from_clip = QPushButton("从剪贴板")
        self._cookie_from_clip.clicked.connect(self._cookie_from_clipboard)
        self._cookie_from_file = QPushButton("从文件读取")
        self._cookie_from_file.clicked.connect(self._cookie_from_file_picker)
        cookie_row = QWidget()
        cookie_lay = QHBoxLayout(cookie_row)
        cookie_lay.setContentsMargins(0, 0, 0, 0)
        cookie_lay.addWidget(self._cookie_edit, 1)
        cookie_lay.addWidget(self._cookie_from_clip, 0, Qt.AlignmentFlag.AlignTop)
        cookie_lay.addWidget(self._cookie_from_file, 0, Qt.AlignmentFlag.AlignTop)

        self._cookie_path_edit = QLineEdit(str(plugin.settings["cookie_path"]))
        self._cookie_hint = QLabel(
            "推荐：登录 chatgpt.com 后打开 /api/auth/session，全选复制整段 JSON；"
            "插件会自动提取 accessToken，写盘时只保留必要字段（完整 JSON 里的"
            "user、实验开关等都会丢弃）。auto 代理会读取系统/项目配置；认证信息"
            "只保存在本机此文件（权限 600）。")
        self._cookie_hint.setWordWrap(True)
        self._cookie_hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._account_edit = QLineEdit(str(plugin.settings.get("account_id", "")))
        self._account_edit.setPlaceholderText("可留空；Cookie/JWT 能识别时会自动发现")

        self._proxy_combo = QComboBox()
        self._proxy_combo.setEditable(True)
        self._proxy_combo.addItems(["auto（系统代理）", "none（直连）"])
        proxy = plugin.settings.get("proxy", "auto")
        if proxy not in ("auto", "none"):
            self._proxy_combo.addItem(proxy)
        self._proxy_combo.setCurrentIndex(
            0 if proxy == "auto" else (1 if proxy == "none" else 2))

        self._refresh_spin = QSpinBox()
        self._refresh_spin.setRange(30, 600)
        self._refresh_spin.setSingleStep(30)
        self._refresh_spin.setValue(int(plugin.settings["refresh_interval_ms"]) // 1000)
        self._refresh_spin.setSuffix(" s")

        self._test_btn = QPushButton("测试连接")
        self._test_btn.clicked.connect(self._test_connection)
        self._test_label = QLabel("")
        self._test_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Cookie / token", cookie_row)
        form.addRow("保存到", self._cookie_path_edit)
        form.addRow("ChatGPT account ID", self._account_edit)
        form.addRow("代理", self._proxy_combo)
        form.addRow("刷新间隔", self._refresh_spin)
        form.addRow("", self._cookie_hint)
        form.addRow(self._test_btn, self._test_label)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.setLayout(lay)
        self._load_cookie_preview()

    def _load_cookie_preview(self):
        path = Path(self._cookie_path_edit.text().strip()).expanduser()
        if path.is_file():
            self._cookie_edit.setPlaceholderText("已配置 Cookie；粘贴新值可替换")

    @staticmethod
    def _validate_auth(text: str):
        """解析认证文本；失败时给出可操作的中文错误。"""
        auth = parse_auth_text(text)
        if auth.configured:
            return auth
        if text.lstrip().startswith(("{", "[")):
            try:
                json.loads(text)
            except (json.JSONDecodeError, TypeError):
                raise CodexError(
                    "粘贴的 JSON 不完整或格式错误（内容可能被截断）："
                    "请重新复制完整的 /api/auth/session JSON") from None
            raise CodexError("JSON 中未找到 accessToken 或 Cookie：请确认复制的是完整会话内容")
        raise CodexError("Cookie/token 格式无法识别")

    def _fill_from_text(self, text: str):
        text = text.strip()
        if not text:
            return
        auth = parse_auth_text(text)
        if auth.configured:
            # 粘贴的是完整 session JSON 时，立即精简成只含必要字段的形式。
            text = serialize_auth(auth)
        self._cookie_edit.setPlainText(text)
        if auth.account_id and not self._account_edit.text().strip():
            self._account_edit.setText(auth.account_id)

    def _cookie_from_clipboard(self):
        from PySide6.QtWidgets import QApplication

        self._fill_from_text(QApplication.clipboard().text())

    def _cookie_from_file_picker(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "选择 Cookie 或 auth.json 文件")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._test_label.setText(f"读取失败：{e}")
            return
        self._fill_from_text(text)

    def _proxy_value(self) -> str:
        value = self._proxy_combo.currentText().strip()
        if value.startswith("auto"):
            return "auto"
        if value.startswith("none"):
            return "none"
        return value or "auto"

    def _make_client(self):
        path = self._cookie_path_edit.text().strip()
        text = self._cookie_edit.toPlainText().strip()
        if text:
            self._validate_auth(text)
        if not text and not Path(path).expanduser().is_file():
            raise CodexError("请先粘贴 Cookie，或选择已有 Cookie 文件")
        return CodexClient(
            cookie_path=path,
            cookie_text=text or None,
            account_id=self._account_edit.text().strip(),
            proxy=self._proxy_value(),
        )

    def _test_connection(self):
        self._test_btn.setEnabled(False)
        self._test_label.setStyleSheet("")
        self._test_label.setText("测试中…")
        try:
            client = self._make_client()
        except CodexError as e:
            self._test_label.setText(str(e))
            self._test_btn.setEnabled(True)
            return

        def task():
            usage = client.usage()
            plan = usage.plan_type or "未知计划"
            return f"连接成功：{plan} · 已读取短周期/每周用量"

        self._start_task(task, self._on_test_result)

    def _on_test_result(self, result):
        self._test_btn.setEnabled(True)
        status, message = result
        self._test_label.setStyleSheet(
            "color: #7cc76b;" if status == "ok" else "color: #e06c5a;")
        self._test_label.setText(message)

    def _save(self):
        path_text = self._cookie_path_edit.text().strip()
        if not path_text:
            self._test_label.setText("Cookie 保存路径不能为空")
            return
        cookie_text = self._cookie_edit.toPlainText().strip()
        if cookie_text:
            try:
                # 只把必要字段写盘：完整 session JSON 几十 KB，没必要落盘。
                cookie_text = serialize_auth(self._validate_auth(cookie_text))
            except CodexError as e:
                self._test_label.setText(str(e))
                return
            self._cookie_edit.setPlainText(cookie_text)
        try:
            path = Path(path_text).expanduser()
            if cookie_text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(cookie_text + "\n", encoding="utf-8")
                if os.name != "nt":
                    os.chmod(path, 0o600)
            elif not path.is_file():
                self._test_label.setText("请先填写 Cookie，或选择已有 Cookie 文件")
                return
        except OSError as e:
            self._test_label.setText(f"保存 Cookie 失败：{e}")
            return

        self.plugin.save_settings({
            "cookie_path": path_text,
            "account_id": self._account_edit.text().strip(),
            "proxy": self._proxy_value(),
            "refresh_interval_ms": int(self._refresh_spin.value()) * 1000,
        })
        self.plugin.refresh_now()
        self.accept()

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
