"""分区容器：垂直堆叠插件分区，支持折叠与整体布局。"""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class Section(QFrame):
    """单个插件分区：可折叠标题栏 + 内容区。"""

    collapsed_changed = Signal()

    def __init__(self, key: str, title: str, content: QWidget, parent=None):
        super().__init__(parent)
        self.key = key
        self._collapsed = False

        self._toggle = QToolButton()
        self._toggle.setText("▾")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setAutoRaise(True)
        self._toggle.setFixedWidth(20)
        self._toggle.clicked.connect(self.toggle_collapse)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            "font-size: 11px; font-weight: 600; letter-spacing: 1px; color: #9aa3b5;"
        )

        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(4, 2, 4, 2)
        head_lay.addWidget(self._title)
        head_lay.addStretch(1)
        head_lay.addWidget(self._toggle)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 4)
        lay.setSpacing(0)
        lay.addWidget(head)
        lay.addWidget(content)
        self._content = content
        self.setLayout(lay)

    def toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._toggle.setText("▸" if self._collapsed else "▾")
        self.collapsed_changed.emit()


class SectionsContainer(QWidget):
    """所有插件分区的垂直容器。

    面板背景用 QPainter 绘制（圆角 + 半透明）：Windows 分层窗口下
    QSS 的 rgba 背景不会合成上屏，但 paintEvent 的绘制可以。
    """

    layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sections: list[Section] = []
        self._bg_color = QColor(40, 45, 56, 235)
        self._border_color = QColor(255, 255, 255, 26)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.setLayout(lay)
        self._lay = lay

    def set_panel_colors(self, background: QColor, border: QColor) -> None:
        self._bg_color = background
        self._border_color = border
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self._border_color, 1))
        p.setBrush(self._bg_color)
        p.drawRoundedRect(
            QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 12, 12)
        p.end()

    def add_section(self, key: str, title: str, content: QWidget) -> Section:
        section = Section(key, title, content, self)
        section.collapsed_changed.connect(self.layout_changed.emit)
        self._lay.insertWidget(self._lay.count() - 1, section)
        self._sections.append(section)
        return section

    def section_visibility(self) -> dict[str, bool]:
        return {s.key: s.isVisibleTo(self) for s in self._sections}

    def set_section_visible(self, key: str, visible: bool) -> None:
        for section in self._sections:
            if section.key == key:
                section.setVisible(visible)
                break

    def clear(self) -> None:
        for section in self._sections:
            self._lay.removeWidget(section)
            section.hide()
            section.setParent(None)
            section.deleteLater()
        self._sections.clear()
        # removeWidget + setParent(None) 后旧 section 立即脱离布局，
        # 不再占用位置；deleteLater 只是延迟销毁对象本体，不影响布局。
        # 之前用 processEvents() 会中途跑旧 _apply_stats 等信号，反而
        # 干扰重建后的布局计算。
