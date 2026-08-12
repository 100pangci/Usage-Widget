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
    app.setQuitOnLastWindowClosed(True)

    manager = PluginManager(config)
    manager.load_all()

    window = FloatingWindow(config, manager)
    window.populate_sections()
    manager.start_all()
    return window
