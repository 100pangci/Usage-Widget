"""GPU 采集（跨厂商：NVIDIA / AMD / Intel / 国产）。

psutil 无 GPU 能力，本模块沿用旧实现的三后端方案：
- Windows + NVIDIA：NVML（nvml.dll）
- Windows + AMD/Intel/国产：WMI Win32_PerfFormattedData_GPUPerformanceCounters
- Linux + NVIDIA：NVML（libnvidia-ml.so）
- Linux + AMD/Intel：sysfs gpu_busy_percent（amdgpu/xe 驱动）

注意：Windows 非 NVML 后端的枚举/采样不能阻塞 UI 线程——显卡名走
注册表（快），利用率采样在后台线程跑 PowerShell 并缓存结果。
"""
import sys
import threading

_WINDOWS = sys.platform == "win32"

_gpu_backend: str | None = None  # "nvml" / "wmi" / "sysfs" / ""(无卡)
_gpu_names_list: list[str] = []  # 每块 GPU 的型号名
_gpu_nvml_handles: list[object] = []
_gpu_nvml_lib: object | None = None

# WMI 利用率采样（PowerShell 查询要 1~3s，异步跑 + 缓存上次结果）
_wmi_stats_cache: list[dict] = []
_wmi_stats_fetching = threading.Event()


def gpu_count() -> int:
    """可用 GPU 数量（无卡返回 0）。"""
    _gpu_init()
    return len(_gpu_names_list)


def gpu_names() -> list[str]:
    """每块 GPU 的型号名（短名），无 GPU 返回 []。"""
    _gpu_init()
    return list(_gpu_names_list)


def _gpu_init() -> None:
    """初始化 GPU 采集后端，填充 _gpu_names_list 和 handle 列表。"""
    global _gpu_backend, _gpu_names_list, _gpu_nvml_handles, _gpu_nvml_lib
    if _gpu_backend is not None:
        return
    try:
        if _WINDOWS:
            if _nvml_load():
                _gpu_backend = "nvml"
                return
            if _wmi_load():
                _gpu_backend = "wmi"
                return
        else:  # Linux
            if _nvml_load():
                _gpu_backend = "nvml"
                return
            if _sysfs_load():
                _gpu_backend = "sysfs"
                return
    except Exception:
        pass
    _gpu_backend = ""  # 无可用后端


def _nvml_load() -> bool:
    """加载 NVML（NVIDIA 驱动自带），枚举所有 GPU。"""
    global _gpu_nvml_lib, _gpu_nvml_handles, _gpu_names_list
    import ctypes

    try:
        if _WINDOWS:
            lib = ctypes.WinDLL("nvml.dll")
        else:
            for path in (
                "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so",
                "/usr/lib/libnvidia-ml.so",
                "/usr/lib64/libnvidia-ml.so",
                "/usr/lib/aarch64-linux-gnu/libnvidia-ml.so",
            ):
                try:
                    lib = ctypes.CDLL(path)
                    break
                except OSError:
                    continue
            else:
                return False
    except OSError:
        return False

    if lib.nvmlInit() != 0:
        return False
    count = ctypes.c_uint()
    if lib.nvmlDeviceGetCount(ctypes.byref(count)) != 0 or count.value == 0:
        lib.nvmlShutdown()
        return False
    handles = []
    names = []
    for i in range(count.value):
        h = ctypes.c_void_p()
        if lib.nvmlDeviceGetHandleByIndex(i, ctypes.byref(h)) != 0:
            continue
        buf = ctypes.create_string_buffer(128)
        if lib.nvmlDeviceGetName(h, buf, 128) == 0:
            name = buf.value.decode("utf-8", "replace").strip()
            # 缩短：NVIDIA GeForce RTX 4060 Laptop GPU → RTX 4060 Laptop
            for prefix in ("NVIDIA GeForce ", "NVIDIA "):
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
        else:
            # 名字拿不到也要占位，保持 names 与 handles 数量一致
            name = f"GPU {i}"
        names.append(name)
        handles.append(h)
    if not handles:
        lib.nvmlShutdown()
        return False
    _gpu_nvml_lib = lib
    _gpu_nvml_handles = handles
    _gpu_names_list = names
    return True


def _wmi_load() -> bool:
    """Windows 非 NVIDIA 后端：枚举显卡名（注册表优先，WMI 兜底）。

    注册表读取是毫秒级（PowerShell 冷启动要 1~3s+），避免
    create_widget 在 UI 线程里长时间卡顿。
    """
    global _gpu_names_list

    names = _gpu_names_from_registry() or _gpu_names_from_wmi()
    if not names:
        return False
    _gpu_names_list = names
    # 预热一次利用率采样（后台线程），首个降频 tick 就有缓存可用
    _wmi_stats_refresh_async()
    return True


def _filter_virtual_gpus(names: list[str]) -> list[str]:
    """过滤虚拟显示驱动（OrayIddDriver/GameViewer 等远程/虚拟显卡）。"""
    return [n for n in names if not any(
        v in n.lower() for v in ("virtual", "iddd", "oray", "gameviewer", "remote"))]


def _gpu_names_from_registry() -> list[str]:
    """从注册表显示适配器类驱动项读显卡名（等价 Win32_VideoController）。"""
    import winreg

    class_key = (r"SYSTEM\CurrentControlSet\Control\Class"
                 r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_key) as key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        desc = winreg.QueryValueEx(sk, "DriverDesc")[0]
                except OSError:
                    continue  # 非适配器子项（无 DriverDesc）
                name = str(desc).strip()
                if name:
                    names.append(name)
    except OSError:
        return []
    return _filter_virtual_gpus(names)


def _gpu_names_from_wmi() -> list[str]:
    """注册表读不到时回退 PowerShell WMI（慢，仅初始化时一次）。"""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=10)
        names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
        names = _filter_virtual_gpus(names)
        return names
    except Exception:
        return []


def _sysfs_load() -> bool:
    """Linux sysfs：amdgpu / xe 驱动的 gpu_busy_percent。"""
    global _gpu_names_list
    from pathlib import Path

    cards = []
    for p in sorted(Path("/sys/class/drm").glob("card*")):
        if not (p / "device" / "gpu_busy_percent").exists():
            continue
        cards.append(f"GPU {len(cards)}")
    if not cards:
        return False
    _gpu_names_list = cards
    return True


def gpu_stats() -> list[dict]:
    """每块 GPU 的 (利用率%, 显存已用/总 MB)。无 GPU 返回 []。

    统一格式：
        {util: int, mem_percent: int, mem_used_mb: int, mem_total_mb: int}
    显存无法获取时 mem_* 为 0（UI 显示「-」）。
    """
    _gpu_init()
    if _gpu_backend == "nvml":
        return _gpu_stats_nvml()
    if _gpu_backend == "wmi":
        return _gpu_stats_wmi()
    if _gpu_backend == "sysfs":
        return _gpu_stats_sysfs()
    return []


def _gpu_stats_nvml() -> list[dict]:
    lib = _gpu_nvml_lib
    if not lib or not _gpu_nvml_handles:
        return []
    import ctypes

    class MemInfo(ctypes.Structure):
        _fields_ = [("total", ctypes.c_uint64),
                    ("used", ctypes.c_uint64),
                    ("free", ctypes.c_uint64)]

    class Utilization(ctypes.Structure):
        # nvmlUtilization_t：nvmlDeviceGetUtilizationRates 只收这一个指针
        _fields_ = [("gpu", ctypes.c_uint),
                    ("memory", ctypes.c_uint)]

    out = []
    for h in _gpu_nvml_handles:
        try:
            util = Utilization()
            if lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(util)) != 0:
                continue
            mem = MemInfo()
            lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(mem))
            out.append({
                "util": int(util.gpu),
                "mem_percent": (int(mem.used * 100 / mem.total)
                                if mem.total > 0 else 0),
                "mem_used_mb": int(mem.used // (1024 * 1024)),
                "mem_total_mb": int(mem.total // (1024 * 1024)),
            })
        except Exception:
            continue
    return out


def _gpu_stats_wmi() -> list[dict]:
    """Windows WMI：GPU Engine 利用率按 phys_N 聚合（异步采集）。

    PowerShell 查询要 1~3s，tick 定时器跑在 UI 线程上不能同步等：
    立即返回上次缓存，同时起后台线程刷新，下个采样周期生效。
    """
    _wmi_stats_refresh_async()
    return list(_wmi_stats_cache)


def _wmi_stats_refresh_async() -> None:
    """在后台线程执行一次 WMI 采样（进行中则跳过，避免堆线程）。"""
    if _wmi_stats_fetching.is_set():
        return
    _wmi_stats_fetching.set()

    def worker() -> None:
        global _wmi_stats_cache
        try:
            _wmi_stats_cache = _wmi_stats_query()
        finally:
            _wmi_stats_fetching.clear()

    threading.Thread(target=worker, daemon=True,
                     name="gpu-wmi-stats").start()


def _wmi_stats_query() -> list[dict]:
    """同步执行 PowerShell GPU Engine 查询（仅供后台线程调用）。"""
    import subprocess

    script = r'''
$engines = Get-CimInstance -Namespace root/cimv2 `
    -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue
$groups = @{}
foreach ($e in $engines) {
    if ($e.Name -match 'phys_(\d+)') {
        $g = $matches[1]
        if (-not $groups.ContainsKey($g)) { $groups[$g] = 0 }
        $groups[$g] += [double]$e.UtilizationPercentage
    }
}
$keys = $groups.Keys | Sort-Object
$result = @()
foreach ($k in $keys) {
    $result += [PSCustomObject]@{phys=$k; util=[math]::Min(100, [int]$groups[$k])}
}
$result | ConvertTo-Json -Compress
'''
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        import json

        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        stats = []
        for item in data:
            stats.append({
                "util": int(item.get("util", 0)),
                "mem_percent": 0,
                "mem_used_mb": 0,
                "mem_total_mb": 0,
            })
        return stats
    except Exception:
        return []


def _gpu_stats_sysfs() -> list[dict]:
    """Linux sysfs：gpu_busy_percent（AMD/Intel）。显存不可读。"""
    from pathlib import Path

    out = []
    for p in sorted(Path("/sys/class/drm").glob("card*")):
        busy_path = p / "device" / "gpu_busy_percent"
        if not busy_path.exists():
            continue
        try:
            busy = int(busy_path.read_text().strip())
            out.append({
                "util": max(0, min(100, busy)),
                "mem_percent": 0,
                "mem_used_mb": 0,
                "mem_total_mb": 0,
            })
        except (OSError, ValueError):
            continue
    return out
