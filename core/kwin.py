"""KDE KWin 置顶工具：主窗口与独立窗口共用。"""
import logging
import os
import shutil
import subprocess
import tempfile
import time

log = logging.getLogger("usage-widget.kwin")

_KDE_ENV_MARKERS = ("KDE_FULL_SESSION", "KDE_SESSION_VERSION")

_POSITION_SERVICE = "org.usagewidget.Position"
_POSITION_PATH = "/org/usagewidget/Position"
_POSITION_INTERFACE = "org.usagewidget.Position"
_position_receiver = None
_position_listener_registered = False
_active_script_names: dict[str, str] = {}


def caption_token(window_id: str) -> str:
    """窗口标题里的唯一置顶标记（PluginWindow 构造时写进标题）。"""
    return f"usage-widget/{window_id}"


def script_name(window_id: str) -> str:
    """KWin 脚本名（按窗口区分，卸载/重载互不影响）。"""
    return f"usage-widget-{window_id}-keepabove"


# KWin 脚本：只匹配本窗口标题里的唯一标记并设置 keepAbove。
# 不能按 "usage-widget" 子串或资源类名匹配——那会把标题恰好含
# "usage-widget" 的其他应用窗口（文件管理器/终端打开相关目录等）
# 一起置顶，也会让本应用的其他窗口被任意窗口的脚本连带置顶
# （各窗口置顶状态独立）。
_KWIN_KEEP_ABOVE_SCRIPT = """
%s

function applyKeepAbove(w) {
    if (!w) return;
    var c = String(w.caption || "");
    if (c.indexOf("%s") >= 0) {
        w.keepAbove = %s;
%s        reportPosition(w);
        w.frameGeometryChanged.connect(function() {
            reportPosition(w);
        });
    }
}
var windows = workspace.windowList();
for (var i = 0; i < windows.length; ++i) {
    if (windows[i]) applyKeepAbove(windows[i]);
}
workspace.windowAdded.connect(applyKeepAbove);
"""

_QDBUS_BIN: str | None = None


def register_position_listener(callback) -> bool:
    """通过会话 D-Bus 接收 KWin 回传的窗口坐标。

    Wayland 不把窗口的全局坐标暴露给客户端。KWin 脚本读取自己的
    ``frameGeometry`` 后调用这个对象，程序再把坐标写入 config.json。
    """
    global _position_receiver, _position_listener_registered
    if not is_kde_session():
        return False
    if _position_receiver is not None:
        return _position_listener_registered
    try:
        from PySide6.QtCore import QObject, Slot
        from PySide6.QtDBus import QDBusConnection
    except ImportError:
        log.debug("PySide6.QtDBus 不可用，无法记录 Wayland 窗口位置")
        return False

    class PositionReceiver(QObject):
        # Math.round() is exported by KWin as a D-Bus int32.
        @Slot(str, int, int)
        def updateWindowPosition(self, window_id: str, x: int, y: int) -> None:
            try:
                callback(str(window_id), int(x), int(y))
            except Exception:
                log.exception("处理 KWin 窗口位置失败")

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        log.debug("会话 D-Bus 不可用，无法记录 Wayland 窗口位置")
        return False
    if not bus.registerService(_POSITION_SERVICE):
        log.warning("注册窗口位置 D-Bus 服务失败: %s", _POSITION_SERVICE)
        return False

    receiver = PositionReceiver()
    options = QDBusConnection.RegisterOption.ExportAllSlots
    if not bus.registerObject(
            _POSITION_PATH, _POSITION_INTERFACE, receiver, options):
        log.warning("注册窗口位置 D-Bus 对象失败: %s", _POSITION_PATH)
        bus.unregisterService(_POSITION_SERVICE)
        return False
    _position_receiver = receiver
    _position_listener_registered = True
    return True


def is_kde_session() -> bool:
    return any(os.environ.get(key) for key in _KDE_ENV_MARKERS)


def _find_qdbus() -> str | None:
    global _QDBUS_BIN
    if _QDBUS_BIN is None:
        for name in ("qdbus", "qdbus6", "qdbus-qt6"):
            path = shutil.which(name)
            if path:
                _QDBUS_BIN = path
                break
    return _QDBUS_BIN


def _run_qdbus(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    binary = _find_qdbus()
    if binary is None:
        return 1, ""
    env = os.environ.copy()
    ld = env.get("LD_LIBRARY_PATH")
    if ld:
        env["LD_LIBRARY_PATH"] = ":".join(
            p for p in ld.split(":") if p and "_internal" not in p)
    try:
        proc = subprocess.run(
            [binary, *args], capture_output=True, text=True,
            timeout=timeout, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("qdbus 调用失败: %s", e)
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def unload_keepabove_script(script_name: str = "usage-widget-keepabove") -> None:
    active = _active_script_names.pop(script_name, None)
    _unload_script(active or script_name)
    if active and active != script_name:
        # 清理旧版本留下的固定名称脚本（若存在）。
        _unload_script(script_name)


def _unload_script(script_name: str) -> None:
    _run_qdbus([
        "org.kde.KWin", "/Scripting",
        "org.kde.kwin.Scripting.unloadScript",
        script_name,
    ])


def _wait_for_script_unloaded(name: str, timeout: float = 0.5) -> None:
    """KWin 的 unloadScript 使用 deleteLater，等待名称真正释放。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, out = _run_qdbus([
            "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.isScriptLoaded",
            name,
        ])
        if code != 0 or out.lower() != "true":
            return
        time.sleep(0.02)


def set_keepabove(
        on: bool,
        window_id: str = "main",
        position: tuple[int, int] | None = None,
) -> bool:
    """设置指定窗口置顶，并可由 KWin 恢复其坐标。

    ``position`` 只在 KWin 侧执行，适用于 Wayland；X11 下传入也安全。
    """
    if not is_kde_session():
        return False
    base_name = script_name(window_id)
    active_name = _active_script_names.get(base_name, base_name)
    # 不在卸载旧脚本后立即复用同名脚本：KWin 的脚本对象异步销毁，
    # 而且脚本 ID 可能复用。先用唯一名称加载新脚本，成功后再清旧脚本。
    name = f"{base_name}-{os.getpid()}-{time.monotonic_ns()}"
    position_code = ""
    if position is not None and len(position) == 2:
        try:
            x, y = int(position[0]), int(position[1])
        except (TypeError, ValueError):
            pass
        else:
            position_code = (
                "        w.frameGeometry = {x: %d, y: %d, "
                "width: w.width, height: w.height};\n" % (x, y)
            )
    if _position_listener_registered:
        report_function = (
            "function reportPosition(w) {\n"
            "    callDBus(\"%s\", \"%s\", \"%s\", "
            "\"updateWindowPosition\", \"%s\", "
            "Math.round(w.x), Math.round(w.y));\n"
            "}\n" % (
                _POSITION_SERVICE,
                _POSITION_PATH,
                _POSITION_INTERFACE,
                window_id,
            )
        )
    else:
        report_function = "function reportPosition(w) {}\n"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_KWIN_KEEP_ABOVE_SCRIPT % (
                report_function,
                caption_token(window_id),
                "true" if on else "false",
                position_code,
            ))
            script_path = f.name
    except OSError as e:
        log.warning("写入 KWin 脚本失败: %s", e)
        return False
    try:
        code, out = _run_qdbus([
            "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.loadScript",
            script_path, name,
        ])
        if code != 0 or not out.isdigit():
            log.warning("KWin 脚本加载失败（%s）：%s", code, out)
            return False
        # 不能按返回的 Script{id} 调 run：KWin 删除脚本后会复用列表
        # 长度作为新 id，可能指向仍存在的旧脚本。start() 只启动尚未
        # running 的脚本，刚加载的脚本会被执行且不会重复运行其他脚本。
        code, out = _run_qdbus([
            "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.start",
        ])
        if code != 0:
            log.warning("KWin 脚本执行失败（%s）：%s", code, out)
            return False
        _unload_script(active_name)
        if active_name != base_name:
            _unload_script(base_name)
        _wait_for_script_unloaded(active_name)
        _wait_for_script_unloaded(base_name)
        _active_script_names[base_name] = name
        return True
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
