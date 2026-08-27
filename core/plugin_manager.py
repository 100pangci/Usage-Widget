"""插件管理器：目录扫描、加载、启停、重载。

插件约定：plugins/<id>/plugin.py，模块内定义 Plugin 子类，或提供
create_plugin() 工厂函数返回插件实例。单个插件失败不影响其他。
"""
import importlib
import logging
import sys
import time
import types
from pathlib import Path

from plugins.base import Plugin

log = logging.getLogger("usage-widget.plugins")

HINTS = ("plugin.py", "__init__.py", "main.py")
_PKG = "usage_widget_plugins"


def default_plugins_dir() -> Path:
    """插件代码目录。

    打包（PyInstaller）后为可执行文件旁的 plugin/ 目录：插件不进主程序，
    放在外部文件夹，新增/修改插件无需重新打包。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "plugin"
    return Path(__file__).resolve().parent.parent / "plugins"


def discover_plugin_ids(plugins_dir: Path) -> list[str]:
    """扫描插件目录，返回按名字排序的插件 id 列表。"""
    if not plugins_dir.is_dir():
        return []
    ids = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if any((entry / hint).is_file() for hint in HINTS):
            ids.append(entry.name)
    return ids


def import_plugin(plugins_dir: Path, plugin_id: str):
    """导入插件入口模块（每次全新执行，支持插件内相对导入）。

    入口优先 plugin.py / main.py；只有 __init__.py 时导入包本身。
    包式插件目录里的其他模块可用相对导入（from .api import ...）。
    """
    _register_parent_pkg(plugins_dir)

    pkg_name = f"{_PKG}.{plugin_id}"
    dirpath = plugins_dir / plugin_id
    entry = None
    if (dirpath / "__init__.py").is_file():
        entry = pkg_name
        for hint in ("plugin.py", "main.py"):
            if (dirpath / hint).is_file():
                entry = f"{pkg_name}.{hint[:-3]}"
                break
    elif (dirpath / "plugin.py").is_file():
        entry = pkg_name + ".plugin"
    elif (dirpath / "main.py").is_file():
        entry = pkg_name + ".main"
    if entry is None:
        raise ImportError(f"插件 {plugin_id} 目录下没有 {HINTS} 任一文件")

    for name in list(sys.modules):
        if name == pkg_name or name.startswith(pkg_name + "."):
            del sys.modules[name]
    return importlib.import_module(entry)


def _register_parent_pkg(plugins_dir: Path) -> None:
    if _PKG in sys.modules:
        return
    parent = types.ModuleType(_PKG)
    parent.__path__ = [str(plugins_dir)]
    sys.modules[_PKG] = parent


def instantiate_plugin(module, plugin_id: str, context: dict) -> Plugin:
    factory = getattr(module, "create_plugin", None)
    if callable(factory):
        plugin = factory()
    else:
        cls = None
        for attr in dir(module):
            obj = getattr(module, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, Plugin)
                and obj is not Plugin
                and getattr(obj, "id", None)
            ):
                cls = obj
                break
        if cls is None:
            raise ImportError(f"插件 {plugin_id} 没有 Plugin 子类或 create_plugin()")
        plugin = cls()
    if not plugin.id:
        plugin.id = plugin_id
    plugin.context = context
    return plugin


class PluginManager:
    """管理已加载插件实例。"""

    def __init__(self, config, plugins_dir: Path | None = None):
        self.config = config
        self.plugins_dir = Path(plugins_dir) if plugins_dir else default_plugins_dir()
        self.plugins: dict[str, Plugin] = {}

    # ---- 加载 ----

    def load_all(self) -> list[str]:
        """按配置 enabled/order 加载插件。返回成功加载的 id 列表。"""
        self.stop_all()
        self.plugins.clear()

        conf = self.config.get("plugins", default={}) or {}
        enabled = conf.get("enabled") or []
        order = conf.get("order") or enabled
        known = set(discover_plugin_ids(self.plugins_dir))

        selected = []
        for pid in order:
            if pid in enabled and pid in known:
                selected.append(pid)
        for pid in enabled:
            if pid in known and pid not in selected:
                selected.append(pid)

        data_root = Path.home() / ".usage-widget" / "plugin"
        for pid in selected:
            try:
                data_dir = data_root / pid
                data_dir.mkdir(parents=True, exist_ok=True)
                context = {"config": self.config, "data_dir": data_dir}
                module = import_plugin(self.plugins_dir, pid)
                plugin = instantiate_plugin(module, pid, context)
                self.plugins[pid] = plugin
                log.info("已加载插件: %s（数据目录 %s）", pid, data_dir)
            except Exception:
                log.exception("插件 %s 加载失败，已跳过", pid)
        return list(self.plugins)

    # ---- 启停 ----

    def start_all(self) -> None:
        for pid, plugin in self.plugins.items():
            plugin.start()
            log.debug("插件已启动: %s", pid)

    def stop_all(self, grace_ms: int = 0) -> None:
        """停止全部插件。

        默认零阻塞（用于「重新加载插件」等 UI 路径，绝不卡界面）。
        grace_ms>0 时（应用退出）给全部在跑的后台线程共享这么多
        毫秒的收尾预算，超时部分不再等待。
        """
        deadline = (time.monotonic() + grace_ms / 1000) if grace_ms else 0.0
        for pid, plugin in self.plugins.items():
            remaining = 0
            if grace_ms:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
            plugin.stop(remaining)

    def reload(self) -> list[str]:
        """重新扫描并加载插件（先停旧实例）。"""
        self.stop_all()
        return self.load_all()
