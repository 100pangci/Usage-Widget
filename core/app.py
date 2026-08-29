"""应用装配：QApplication、日志、主窗口。"""
import logging
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.config import Config
from core.plugin_manager import PluginManager
from core.window import FloatingWindow


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def create_app(config: Config) -> FloatingWindow:
    QCoreApplication.setApplicationName("usage-widget")
    app = QApplication(sys.argv[:1])
    # 分离的插件窗口独立存活：关闭主窗口不能连带退出应用
    app.setQuitOnLastWindowClosed(False)

    manager = PluginManager(config)
    manager.load_all()

    window = FloatingWindow(config, manager)
    window.populate_sections()
    manager.start_all()

    # 兜底退出清理：不经窗口 closeEvent 的退出路径（如 Ctrl+C）也
    # 要停掉后台取数线程，避免带着活线程卡死在进程清理阶段
    def _stop_on_quit() -> None:
        manager.stop_all(grace_ms=2500)
    app.aboutToQuit.connect(_stop_on_quit)
    return window
