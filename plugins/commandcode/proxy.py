"""系统代理探测：环境变量 → Windows 注册表 / KDE Plasma kioslaverc。插件内自包含。"""
import configparser
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("commandcode.proxy")

_ENV_KEYS = (
    "HTTPS_PROXY", "https_proxy",
    "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)
_KIOSLAVERC = Path.home() / ".config" / "kioslaverc"


def detect_proxy() -> str | None:
    """返回代理 URL（http://host:port），探测不到返回 None。

    优先级：环境变量 → 平台系统代理（Windows 注册表 / KDE kioslaverc）。
    """
    for key in _ENV_KEYS:
        value = os.environ.get(key)
        if value and not value.lower().startswith(("no", "")):
            value = value.strip()
            if value and value.lower() != "direct":
                return value

    if sys.platform == "win32":
        return _win_registry_proxy()
    return _kde_proxy()


def _win_registry_proxy() -> str | None:
    """Windows WinINET 系统代理（HKCU Internet Settings）。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enable:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not server:
        return None
    # ProxyServer 可能是 "host:port" 或 "http=host:port;https=host:port"
    for part in server.split(";"):
        if part.startswith(("https=", "http=")):
            return part.split("=", 1)[1].strip() or None
    return server.strip() or None


def _kde_proxy() -> str | None:
    if not _KIOSLAVERC.is_file():
        log.debug("未探测到系统代理")
        return None
    try:
        cp = configparser.ConfigParser(strict=False)
        cp.read(_KIOSLAVERC, encoding="utf-8")
        if cp.getint("Proxy Settings", "ProxyType", fallback=0) == 1:
            for key in ("httpProxy", "httpsProxy"):
                value = cp.get("Proxy Settings", key, fallback="").strip().strip('"')
                if value:
                    return value
    except Exception:
        log.debug("解析 kioslaverc 失败", exc_info=True)
    log.debug("未探测到系统代理")
    return None