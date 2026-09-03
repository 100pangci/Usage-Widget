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
        self._window_factory = window_factory  # (window_id, wc, title) -> PluginWindow
        self._refresh_callback = None  # 窗口内容变化时刷新所有窗口菜单

    # ---- 窗口创建 ----

    @property
    def main(self):
        return self._main_window

    def create_main(self, window):
        """注册主窗口实例（已在外部构造）。"""
        self._main_window = window
        self.windows[MAIN_ID] = window
        return window

    def set_refresh_callback(self, callback) -> None:
        """注入回调：窗口/插件分配变化后刷新所有窗口的菜单。"""
        self._refresh_callback = callback

    def _notify_change(self) -> None:
        if self._refresh_callback is not None:
            self._refresh_callback()

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
        self._notify_change()
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
            # 插件回到主窗口时恢复显示（主窗口可能因拆分被隐藏）
            main = self.windows.get(MAIN_ID)
            if main is not None and not main.isVisible():
                main.show()
        win.deleteLater()
        self._notify_change()
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
        if src_id == MAIN_ID:
            # 主窗口不能作为被合并源（合并菜单里也不列主窗口为目标的对端，
            # 这里兜底防御：防止把主窗口合并掉导致系统菜单/入口丢失）
            return
        if not self.windows[src_id].plugin_ids:
            self.close_window(src_id, merge_plugins=False)
            return
        for pid in list(self.windows[src_id].plugin_ids):
            self.move_plugin(src_id, dst_id, pid)
        self.close_window(src_id, merge_plugins=False)
        # 合并回隐藏的主窗口时恢复显示
        if dst_id == MAIN_ID:
            main = self.windows.get(MAIN_ID)
            if main is not None and not main.isVisible():
                main.show()
        self._notify_change()
        self.persist()

    def split_window(self, window_id: str) -> None:
        """把窗口里每个插件拆成独立窗口（源窗口清空后关闭）。"""
        win = self.windows.get(window_id)
        if win is None:
            return
        plugins = [win.get_plugin(pid) for pid in list(win.plugin_ids)]
        plugins = [p for p in plugins if p is not None]
        if not plugins:
            return
        # 幂等：每个插件最多拆一次（防御重复触发/回调竞态）
        if getattr(win, "_split_done", False):
            return
        win._split_done = True
        if window_id == MAIN_ID:
            # 主窗口：每个插件拆成独立窗口，主窗口保留但隐藏（空窗口无意义）
            for plugin in plugins:
                self.move_plugin(MAIN_ID, self._new_detached(plugin), plugin.id)
            main = self.windows.get(MAIN_ID)
            if main is not None:
                main.hide()
            self._notify_change()
            self.persist()
        else:
            # 子窗口：每个插件拆成独立窗口（与主窗口行为一致），
            # 最后关闭空窗口。注意必须全部拆走——留任何一个在
            # close_window 的 deleteLater 里都会随窗口一起销毁，
            # 实例从配置中丢失且 timer 变僵尸（每秒 tick 已删对象）
            for plugin in plugins:
                self.move_plugin(window_id, self._new_detached(plugin), plugin.id)
            self.close_window(window_id, merge_plugins=False)
            self._notify_change()
            self.persist()

    def _new_detached(self, plugin) -> str:
        """创建新的独立窗口并挂载插件，返回 window_id。"""
        wid = self.next_window_id()
        self._spawn_window(wid, {}, title=plugin.name or plugin.id)
        self.attach_plugin(wid, plugin)
        win = self.windows.get(wid)
        if win is not None:
            # 新建的窗口没有持久化历史，先清 flag 再 set（避免 show 时
            # 带着 setWindowFlags 触发的重建被 KWin 脚本以旧状态补齐）
            win.setWindowFlags(
                win.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            win.setWindowFlags(
                win.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            win.show()
        return wid

    # ---- 启动恢复 ----

    def restore(self) -> None:
        """按配置恢复全部窗口：windows.<id> = {plugins, pos, topmost}。"""
        # 先清空所有窗口已挂载的插件（重载场景：旧实例需移除，
        # 否则 add_plugin 幂等判断会保留旧实例引用）
        for win in self.windows.values():
            for pid in list(win.plugin_ids):
                win.remove_plugin(pid)
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
                    # 窗口标题用首个插件的名字（与「分离」产生的窗口一致）
                    self._spawn_window(
                        wid, wc, title=plugin.name or pid)
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
                # 窗口默认带置顶 flag，只需恢复持久化为 False 的
                if wc.get("topmost") is False:
                    win.apply_topmost(False)
                win.show()
                # 已拆过标记复位（窗口集已重建）
                win._split_done = False
        self.persist()

    def _spawn_window(self, window_id: str, wc: dict, title: str | None = None) -> None:
        """按配置创建独立窗口（由 FloatingWindow 注入 factory）。"""
        if self._window_factory is not None:
            win = self._window_factory(window_id, wc, title)
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
