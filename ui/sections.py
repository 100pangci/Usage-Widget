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
        try:
            from core.theme import color

            _dim = color("dim")
        except ImportError:
            _dim = "#9aa3b5"
        self._title.setStyleSheet(
            f"font-size: 11px; font-weight: 600; letter-spacing: 1px; color: {_dim};"
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

    def set_content(self, new_content: QWidget) -> None:
        """替换内容 widget（保留折叠状态与标题栏）。"""
        lay = self.layout()
        old = self._content
        lay.replaceWidget(old, new_content)
        old.deleteLater()
        self._content = new_content
        # 保持折叠状态
        new_content.setVisible(not self._collapsed)


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

    def collapsed_keys(self) -> set[str]:
        """当前处于折叠状态的分区 key 集合。"""
        return {s.key for s in self._sections if s._collapsed}

    def collapse_section(self, key: str) -> None:
        """把分区折叠（不触发 toggle，用于重建后恢复折叠状态）。"""
        for section in self._sections:
            if section.key == key:
                if not section._collapsed:
                    section.toggle_collapse()
                break

    def replace_section(self, key: str, content: QWidget) -> bool:
        """替换单个分区的内容 widget（其他分区不受影响）。"""
        for section in self._sections:
            if section.key == key:
                section.set_content(content)
                return True
        return False

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
