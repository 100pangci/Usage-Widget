"""opencode 用量插件设置对话框：cookie / 工作区 / 代理 / 采样。

- 工作区：自动检测（getWorkspaces）+ 下拉切换，也可手动输入
- cookie：粘贴或从文件导入，持久化到插件数据目录（0600）
- 代理：自动（系统代理）/ 无 / 自定义
"""
import logging

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

from .api import OpencodeClient, OpencodeError
from .format import format_usd

log = logging.getLogger("opencode.settings")


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
        self.setMinimumWidth(460)

        settings = plugin.settings
        self._task = None

        # -- 工作区 --
        self._ws_combo = QComboBox()
        self._ws_combo.setEditable(True)
        self._ws_combo.setMinimumWidth(300)
        self._ws_combo.setPlaceholderText("自动检测中…")
        self._ws_detect_btn = QPushButton("检测")
        self._ws_detect_btn.clicked.connect(self._detect_workspaces)
        ws_row = QWidget()
        ws_lay = QHBoxLayout(ws_row)
        ws_lay.setContentsMargins(0, 0, 0, 0)
        ws_lay.addWidget(self._ws_combo, 1)
        ws_lay.addWidget(self._ws_detect_btn)

        # -- cookie --
        self._cookie_edit = QLineEdit()
        self._cookie_edit.setPlaceholderText("粘贴 auth cookie 值（浏览器 F12 → 网络 → Cookie）")
        self._cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._cookie_from_clip = QPushButton("从剪贴板")
        self._cookie_from_clip.clicked.connect(self._cookie_from_clipboard)
        self._cookie_from_file = QPushButton("从文件读取")
        self._cookie_from_file.clicked.connect(self._cookie_from_file_picker)
        cookie_row = QWidget()
        cookie_lay = QHBoxLayout(cookie_row)
        cookie_lay.setContentsMargins(0, 0, 0, 0)
        cookie_lay.addWidget(self._cookie_edit, 1)
        cookie_lay.addWidget(self._cookie_from_clip)
        cookie_lay.addWidget(self._cookie_from_file)

        self._cookie_path_edit = QLineEdit(str(settings["cookie_path"]))
        self._cookie_hint = QLabel("cookie 将持久化保存到此文件（权限 600）")
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
        form.addRow("工作区", ws_row)
        form.addRow("", QLabel("自动检测全部工作区，下拉切换；也可直接输入 id"))
        form.addRow("cookie", cookie_row)
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

        # 预填当前值
        ws_id = settings["workspace_id"]
        if ws_id:
            self._ws_combo.addItem(f"{ws_id}（当前）", ws_id)
            self._ws_combo.setCurrentIndex(0)
        self._load_cookie_preview()
        self._detect_workspaces()

    # ---- 工作区自动检测 ----

    def _detect_workspaces(self):
        self._ws_detect_btn.setEnabled(False)
        self._ws_detect_btn.setText("检测中…")
        client = self._make_client()
        if client is None:
            self._ws_detect_btn.setEnabled(True)
            self._ws_detect_btn.setText("检测")
            return
        self._start_task(
            lambda: client.workspaces(),
            lambda result: self._on_workspaces(result),
        )

    def _on_workspaces(self, result):
        self._ws_detect_btn.setEnabled(True)
        self._ws_detect_btn.setText("检测")
        status, workspaces = result
        if status != "ok":
            self._ws_combo.clear()
            self._test_label.setText(f"工作区检测失败：{workspaces}")
            return
        self._ws_combo.clear()
        if not workspaces:
            self._test_label.setText("没有检测到工作区")
            return
        for ws in workspaces:
            label = f"{ws.name} · {ws.id}" if ws.name else ws.id
            self._ws_combo.addItem(label, ws.id)
        current = self.plugin.settings["workspace_id"]
        for i in range(self._ws_combo.count()):
            if self._ws_combo.itemData(i) == current:
                self._ws_combo.setCurrentIndex(i)
                break
        self._test_label.setText(f"检测到 {len(workspaces)} 个工作区")

    # ---- cookie ----

    def _cookie_from_clipboard(self):
        from PySide6.QtWidgets import QApplication

        clip = QApplication.clipboard().text().strip()
        if clip:
            self._cookie_edit.setText(clip)

    def _cookie_from_file_picker(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "选择 cookie 文件")
        if not path:
            return
        try:
            text = open(path, encoding="utf-8", errors="replace").read().strip()
        except OSError as e:
            self._test_label.setText(f"读取失败: {e}")
            return
        self._cookie_edit.setText(text)

    def _load_cookie_preview(self):
        path = self.plugin.settings["cookie_path"]
        try:
            text = open(path, encoding="utf-8", errors="replace").read().strip()
        except OSError:
            return
        if text:
            import re

            m = re.search(r"auth=([^;,\s]+)", text)
            preview = (m.group(1) if m else text.split(";")[0].split("=", 1)[-1]).strip()
            self._cookie_edit.setText(preview)

    # ---- 测试连接 ----

    def _test_connection(self):
        self._test_btn.setEnabled(False)
        self._test_label.setText("测试中…")
        client = self._make_client()
        if client is None:
            return

        def task():
            ws = client.workspaces()
            if not ws:
                return "连接成功，但没有工作区"
            import datetime

            today = datetime.date.today()
            costs = client.month_costs(today.year, today.month - 1)
            total = sum(c.cost_units for c in costs)
            return (f"连接成功：{len(ws)} 个工作区，"
                    f"本月 ${total / 1e8:,.2f}（工作区: {ws[0].name}）")

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
        cookie_text = self._cookie_edit.text().strip()
        cookie_path = self._cookie_path_edit.text().strip()
        if not cookie_path:
            self._test_label.setText("cookie 保存路径不能为空")
            return
        try:
            import os

            if cookie_text:
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(cookie_text + "\n")
                os.chmod(cookie_path, 0o600)
            elif not os.path.isfile(cookie_path):
                self._test_label.setText("请先粘贴 cookie 值")
                return
        except OSError as e:
            self._test_label.setText(f"保存 cookie 失败: {e}")
            return

        proxy = self._proxy_combo.currentText().strip()
        if proxy.startswith("auto"):
            proxy = "auto"
        elif proxy.startswith("none"):
            proxy = "none"
        if self._ws_combo.currentData() is not None:
            workspace_id = str(self._ws_combo.currentData())
        else:
            workspace_id = self._ws_combo.currentText().strip()

        self.plugin.save_settings({
            "cookie_path": cookie_path,
            "workspace_id": workspace_id,
            "proxy": proxy,
            "refresh_interval_ms": int(self._refresh_spin.value()) * 1000,
            "timezone": self.plugin.settings.get("timezone") or self.plugin._system_tz(),
        })
        self.plugin.refresh_now()
        self.accept()

    # ---- 工具 ----

    def _make_client(self):
        client = self.plugin.make_client()
        if client is None:
            self._test_label.setText("cookie 未配置：先填写 cookie 并保存")
        return client

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
