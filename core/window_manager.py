"""窗口管理器：统一管理主窗口与独立插件窗口。

权威模型：
- 所有窗口都是 PluginWindow（window_id="main" 为主窗口）
- 插件实例全局唯一：一个插件一个实例，任一时刻恰好属于一个窗口
- 窗口内插件顺序 = 显示顺序（config windows.<id>.plugins）
- _persist() 是唯一写配置的入口
- 启动时按配置恢复全部窗口（组合/顺序/位置/置顶）
"""
import logging
from pathlib import Path

from PySide6.QtCore import Qt

log = logging.getLogger("usage-widget.window_manager")

MAIN_ID = "main"


class WindowManager:
    def __init__(self, config, plugin_manager, window_factory=None):
        self.config = config
        self.manager = plugin_manager  # PluginManager（发现/加载插件）
        self.windows: dict[str, object] = {}  # window_id -> PluginWindow
        self._main_window = None
        self._window_factory = window_factory  # (window_id, wc) -> PluginWindow

    # ---- 窗口创建 ----

    @property
    def main(self):
        return self._main_window

    def create_main(self, window):
        """注册主窗口实例（已在外部构造）。"""
        self._main_window = window
        self.windows[MAIN_ID] = window
        return window

    def create_window(self, window_id: str, window_cls, *args, **kwargs):
        self.windows[window_id] = window_cls(*args, **kwargs)
        return self.windows[window_id]

    def next_window_id(self) -> str:
        n = 1
        while f"w{n}" in self.windows:
            n += 1
        return f"w{n}"

    # ---- 插件分配 ----

    def attach_plugin(self, window_id: str, plugin) -> None:
        """把插件挂到窗口（create_widget + start，幂等）。"""
        win = self.windows.get(window_id)
        if win is None:
            return
        win.add_plugin(plugin)

    def detach_plugin(self, window_id: str, pid: str):
        """从窗口卸载插件（stop + 清引用），返回实例。"""
        win = self.windows.get(window_id)
        if win is None:
            return None
        return win.remove_plugin(pid)

    def move_plugin(self, src_id: str, dst_id: str, pid: str) -> bool:
        """把插件实例从 src 窗口迁移到 dst 窗口（实例不重建）。"""
        inst = self.detach_plugin(src_id, pid)
        if inst is None:
            return False
        self.attach_plugin(dst_id, inst)
        return True

    # ---- 窗口管理 ----

    def close_window(self, window_id: str, merge_plugins: bool = True) -> None:
        """关闭窗口：插件回主窗口（可选），从配置移除。"""
        win = self.windows.pop(window_id, None)
        if win is None or window_id == MAIN_ID:
            return
        if merge_plugins:
            for pid in list(win.plugin_ids):
                inst = win.get_plugin(pid)
                if inst is not None:
                    win.remove_plugin(pid)
                    self.attach_plugin(MAIN_ID, inst)
        win.deleteLater()
        self.persist()

    def all_plugin_ids(self) -> list[str]:
        """所有窗口的插件 id（去重）。"""
        ids = []
        for win in self.windows.values():
            for pid in win.plugin_ids:
                if pid not in ids:
                    ids.append(pid)
        return ids

    # ---- 合并 ----

    def merge_window(self, src_id: str, dst_id: str) -> None:
        """把 src 窗口全部插件合并到 dst 窗口，src 关闭。"""
        if src_id == dst_id or src_id not in self.windows or dst_id not in self.windows:
            return
        for pid in list(self.windows[src_id].plugin_ids):
            self.move_plugin(src_id, dst_id, pid)
        self.close_window(src_id, merge_plugins=False)
        self.persist()

    # ---- 启动恢复 ----

    def restore(self) -> None:
        """按配置恢复全部窗口：windows.<id> = {plugins, pos, topmost}。"""
        windows_cfg = self.config.get("windows", default={}) or {}
        main_plugins = windows_cfg.get(MAIN_ID, {}).get("plugins") or []
        # 1. 先恢复非 main 窗口（按配置顺序，插件顺序与配置一致）
        detached_seen = set()
        for wid, wc in windows_cfg.items():
            if wid == MAIN_ID:
                continue
            for pid in (wc.get("plugins") or []):
                plugin = self.manager.plugins.get(pid)
                if plugin is None:
                    continue
                if wid not in self.windows:
                    self._spawn_window(wid, wc)
                self.attach_plugin(wid, plugin)
                detached_seen.add(pid)
        # 2. 主窗口：配置顺序 + 未配置的补末尾
        ordered = list(main_plugins) + [
            pid for pid in self.manager.plugins
            if pid not in main_plugins and pid not in detached_seen]
        for pid in ordered:
            plugin = self.manager.plugins.get(pid)
            if plugin is None:
                continue
            self.attach_plugin(MAIN_ID, plugin)
        # 3. 恢复各窗口位置/置顶
        for wid, win in self.windows.items():
            wc = windows_cfg.get(wid, {})
            pos = wc.get("pos")
            if pos and len(pos) == 2 and wid != MAIN_ID:
                win.move(int(pos[0]), int(pos[1]))
            if wid != MAIN_ID:
                win.show()
        self.persist()

    def _spawn_window(self, window_id: str, wc: dict) -> None:
        """按配置创建独立窗口（由 FloatingWindow 注入 factory）。"""
        if self._window_factory is not None:
            win = self._window_factory(window_id, wc)
            if win is not None:
                self.windows[window_id] = win
                return
        raise NotImplementedError

    # ---- 配置 ----

    def persist(self) -> None:
        """唯一写配置入口：持久化所有窗口状态。"""
        windows = {}
        for wid, win in self.windows.items():
            topmost = True
            try:
                topmost = bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            except Exception:
                pass
            windows[wid] = {
                "plugins": win.plugin_ids,
                "pos": [win.x(), win.y()],
                "topmost": topmost,
            }
        self.config.set("windows", value=windows)
        self.config.save()
