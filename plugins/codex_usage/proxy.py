"""Codex 插件的代理探测：环境变量、系统设置和旧版项目配置。"""
from __future__ import annotations

import configparser
import logging
import os
import re
import sys
from pathlib import Path

log = logging.getLogger("codex_usage.proxy")

_ENV_KEYS = (
    "HTTPS_PROXY", "https_proxy",
    "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)
_KIOSLAVERC = Path.home() / ".config" / "kioslaverc"
_LEGACY_CONFIG = Path.home() / ".config" / "usage-widget" / "config.toml"


def _clean_proxy(value: str | None) -> str | None:
    value = str(value or "").strip().strip('"').strip("'")
    if not value or value.lower() in {"none", "no", "direct", "off"}:
        return None
    return value


def detect_proxy() -> str | None:
    """返回代理 URL；优先环境变量，再读系统和旧版项目配置。"""
    for key in _ENV_KEYS:
        proxy = _clean_proxy(os.environ.get(key))
        if proxy:
            return proxy

    if sys.platform == "win32":
        proxy = _win_registry_proxy()
    else:
        proxy = _kde_proxy()
    return proxy or _legacy_config_proxy()


def _win_registry_proxy() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not server:
        return None
    for part in str(server).split(";"):
        if part.startswith(("https=", "http=")):
            return _clean_proxy(part.split("=", 1)[1])
    return _clean_proxy(server)


def _kde_proxy() -> str | None:
    if not _KIOSLAVERC.is_file():
        return None
    try:
        cp = configparser.ConfigParser(strict=False)
        cp.read(_KIOSLAVERC, encoding="utf-8")
        if cp.getint("Proxy Settings", "ProxyType", fallback=0) == 1:
            for key in ("httpProxy", "httpsProxy"):
                proxy = _clean_proxy(cp.get("Proxy Settings", key, fallback=""))
                if proxy:
                    return proxy
    except Exception:
        log.debug("解析 KDE 代理配置失败", exc_info=True)
    return None


def _legacy_config_proxy() -> str | None:
    """兼容旧版 usage-widget 的 [ui] proxy 配置。"""
    if not _LEGACY_CONFIG.is_file():
        return None
    try:
        section = ""
        for line in _LEGACY_CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip()
                continue
            if section != "ui":
                continue
            match = re.match(r"proxy\s*=\s*(['\"])(.*?)\1", stripped)
            if match:
                proxy = _clean_proxy(match.group(2))
                if proxy:
                    log.debug("使用旧版 usage-widget 的代理配置")
                    return proxy
    except OSError:
        log.debug("读取旧版 usage-widget 代理配置失败", exc_info=True)
    return None
