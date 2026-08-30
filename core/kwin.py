"""KDE KWin 置顶工具：主窗口与独立窗口共用。"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger("usage-widget.kwin")

_KDE_ENV_MARKERS = ("KDE_FULL_SESSION", "KDE_SESSION_VERSION")

# KWin 脚本：匹配 usage-widget 前缀标题 + 资源类名，设置 keepAbove。
_KWIN_KEEP_ABOVE_SCRIPT = """
function applyKeepAbove(w) {
    if (!w) return;
    var c = String(w.caption || "");
    var r = String(w.resourceClass || "");
    if (r === "usage-widget" || c.indexOf("usage-widget") >= 0) {
        w.keepAbove = %s;
    }
}
var windows = workspace.windowList();
for (var i = 0; i < windows.length; ++i) {
    if (windows[i]) applyKeepAbove(windows[i]);
}
workspace.windowAdded.connect(applyKeepAbove);
"""

_QDBUS_BIN: str | None = None


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
    _run_qdbus([
        "org.kde.KWin", "/Scripting",
        "org.kde.kwin.Scripting.unloadScript",
        script_name,
    ])


def set_keepabove(on: bool, script_name: str = "usage-widget-keepabove") -> bool:
    """通过 KWin 脚本设置窗口置顶（脚本名按窗口区分）。"""
    if not is_kde_session():
        return False
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_KWIN_KEEP_ABOVE_SCRIPT % ("true" if on else "false"))
            script_path = f.name
    except OSError as e:
        log.warning("写入 KWin 脚本失败: %s", e)
        return False
    try:
        unload_keepabove_script(script_name)
        code, out = _run_qdbus([
            "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.loadScript",
            script_path, script_name,
        ])
        if code != 0 or not out.isdigit():
            log.warning("KWin 脚本加载失败（%s）：%s", code, out)
            return False
        script_id = out
        code, out = _run_qdbus([
            "org.kde.KWin", f"/Scripting/Script{script_id}",
            "org.kde.kwin.Script.run",
        ])
        if code != 0:
            log.warning("KWin 脚本执行失败（%s）：%s", code, out)
            return False
        return True
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
