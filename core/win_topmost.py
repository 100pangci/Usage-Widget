"""Windows 原生置顶维护。

Qt 的 ``WindowStaysOnTopHint`` 只在窗口创建/切换 flag 时调用一次
``SetWindowPos``。Windows 的置顶是一个 z-order 层级，其他程序可能
把层级弄乱（例如自己临时置顶后又只清了样式位、没有正确退出置顶层，
或者全屏/合成窗口被系统提升到置顶窗口之上）：此时悬浮窗的
``WS_EX_TOPMOST`` 还在，对大多数普通窗口仍然置顶，却会被某一个
窗口压住，且不会自愈。

本模块提供原生的检测与修复：确认当前窗口仍带置顶属性；沿 z-order
向上找到第一个真正遮挡它的窗口，若那是其他进程的普通（非置顶）
窗口，就用 ``SetWindowPos(HWND_TOPMOST)`` 把本窗口重新压回置顶层的
顶部。非 Windows 平台全部为无操作。
"""
import ctypes
import os
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
DWMWA_CLOAKED = 14

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
_SWP_FLAGS = (SWP_NOMOVE | SWP_NOSIZE
              | SWP_NOACTIVATE | SWP_NOOWNERZORDER)


class _WinApi:
    """user32/dwmapi 的最小封装（便于测试替换）。"""

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except OSError:  # 极简系统/Server Core 可能没有 dwmapi
            self._dwmapi = None

        # 32 位 Python 没有 GetWindowLongPtrW 这个导出符号
        self._get_window_long = getattr(
            self._user32, "GetWindowLongPtrW", self._user32.GetWindowLongW)
        self._get_window_long.restype = ctypes.c_ssize_t
        self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]

        self._set_window_pos = self._user32.SetWindowPos
        self._set_window_pos.restype = wintypes.BOOL
        self._set_window_pos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]

        self._get_window = self._user32.GetWindow
        self._get_window.restype = wintypes.HWND
        self._get_window.argtypes = [wintypes.HWND, wintypes.UINT]

        self._is_window_visible = self._user32.IsWindowVisible
        self._is_window_visible.restype = wintypes.BOOL
        self._is_window_visible.argtypes = [wintypes.HWND]

        self._is_iconic = self._user32.IsIconic
        self._is_iconic.restype = wintypes.BOOL
        self._is_iconic.argtypes = [wintypes.HWND]

        self._get_window_rect = self._user32.GetWindowRect
        self._get_window_rect.restype = wintypes.BOOL
        self._get_window_rect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

        self._get_window_thread_process_id = \
            self._user32.GetWindowThreadProcessId
        self._get_window_thread_process_id.restype = wintypes.DWORD
        self._get_window_thread_process_id.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

        self._get_window_text = self._user32.GetWindowTextW
        self._get_window_text.restype = ctypes.c_int
        self._get_window_text.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

        if self._dwmapi is not None:
            self._dwm_get_window_attribute = self._dwmapi.DwmGetWindowAttribute
            self._dwm_get_window_attribute.restype = ctypes.c_long
            self._dwm_get_window_attribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                wintypes.DWORD]

    # ---- 查询 ----

    def has_topmost(self, hwnd: int) -> bool:
        return bool(self._get_window_long(hwnd, GWL_EXSTYLE)
                    & WS_EX_TOPMOST)

    def set_topmost(self, hwnd: int, on: bool) -> bool:
        insert_after = HWND_TOPMOST if on else HWND_NOTOPMOST
        return bool(self._set_window_pos(
            hwnd, insert_after, 0, 0, 0, 0, _SWP_FLAGS))

    def window_above(self, hwnd: int) -> int:
        return int(self._get_window(hwnd, GW_HWNDPREV) or 0)

    def is_visible(self, hwnd: int) -> bool:
        return bool(self._is_window_visible(hwnd))

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self._is_iconic(hwnd))

    def is_cloaked(self, hwnd: int) -> bool:
        """DWM cloak：窗口仍在 z-order 里，但不在当前虚拟桌面上显示。"""
        if self._dwmapi is None:
            return False
        value = wintypes.DWORD(0)
        hres = self._dwm_get_window_attribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(value),
            ctypes.sizeof(value))
        return hres == 0 and value.value != 0

    def rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        if not self._get_window_rect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def pid(self, hwnd: int) -> int:
        value = wintypes.DWORD(0)
        self._get_window_thread_process_id(hwnd, ctypes.byref(value))
        return value.value

    def window_title(self, hwnd: int, limit: int = 60) -> str:
        buffer = ctypes.create_unicode_buffer(limit + 1)
        length = self._get_window_text(hwnd, buffer, limit + 1)
        return buffer.value if length > 0 else ""


_api: _WinApi | None = _WinApi() if _IS_WINDOWS else None


def is_supported() -> bool:
    """当前平台是否使用原生置顶维护（仅 Windows）。"""
    return _api is not None


def hwnd_of(widget) -> int | None:
    """取 Qt 顶层控件当前的原生句柄（窗口重建后会变化）。"""
    if _api is None or widget is None:
        return None
    try:
        return int(widget.winId()) or None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def native_topmost(hwnd: int) -> bool:
    if _api is None or not hwnd:
        return False
    return _api.has_topmost(hwnd)


def set_native_topmost(hwnd: int, on: bool = True) -> bool:
    """直接按原生 z-order 层级设置/取消置顶。"""
    if _api is None or not hwnd:
        return False
    return _api.set_topmost(hwnd, on)


def _intersects(a: tuple[int, int, int, int],
                b: tuple[int, int, int, int]) -> bool:
    return (a[0] < b[2] and b[0] < a[2]
            and a[1] < b[3] and b[1] < a[3])


def occluding_intruder(hwnd: int) -> int | None:
    """返回压在本窗口上方、与之相交的其他进程非置顶窗口。

    沿 z-order 从本窗口往上找第一个真正遮挡它的窗口（跳过不可见、
    最小化、其他虚拟桌面以及无面积的窗口）：
    - 置顶窗口或本进程窗口（自己的对话框/菜单）属于正常层级，停止；
    - 其他进程的普通窗口出现在置顶窗口上方属于异常层级，返回它。
    """
    if _api is None or not hwnd:
        return None
    mine = _api.rect(hwnd)
    if mine is None:
        return None
    my_pid = os.getpid()
    above = _api.window_above(hwnd)
    while above:
        if (not _api.is_visible(above) or _api.is_minimized(above)
                or _api.is_cloaked(above)):
            above = _api.window_above(above)
            continue
        other = _api.rect(above)
        if other is None or not _intersects(other, mine):
            above = _api.window_above(above)
            continue
        if _api.has_topmost(above) or _api.pid(above) == my_pid:
            return None
        return above
    return None


def repair_if_needed(widget) -> str | None:
    """检查并修复控件窗口的原生置顶状态，返回修复原因（无需修复为 None）。"""
    if _api is None:
        return None
    hwnd = hwnd_of(widget)
    if not hwnd:
        return None
    if not _api.has_topmost(hwnd):
        if _api.set_topmost(hwnd, True):
            return "原生置顶属性丢失"
        return None
    intruder = occluding_intruder(hwnd)
    if intruder is None:
        return None
    if not _api.set_topmost(hwnd, True):
        return None
    title = _api.window_title(intruder)
    label = f"「{title}」" if title else ""
    return f"被非置顶窗口{label} 0x{intruder:X} 遮挡"
