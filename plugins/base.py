"""插件基类：插件作者只需继承 Plugin 并实现 create_widget/tick。"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from core.config import Config

log = logging.getLogger("usage-widget.plugin")


class Plugin(QObject):
    """插件 API。

    约定：
    - id/name/version/description 为类属性，供框架展示与过滤
    - create_widget() 返回本插件的分区 UI（QWidget）
    - on_start()/on_stop() 生命周期钩子
    - tick() 按 refresh_interval 定时调用（0 表示不自动 tick）
    - get_setting() 读 config.json 里 plugins.settings.<id> 下的配置
    """

    id: str = ""
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    refresh_interval: int = 1000

    def __init__(self, context=None):
        super().__init__()
        self.context = context or {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)

    # ---- 框架提供 ----

    @property
    def data_dir(self) -> Path:
        """插件持久化数据目录：~/.usage-widget/plugin/<id>/（框架自动创建）。"""
        return Path(self.context.get("data_dir") or Path.home() / ".usage-widget" / "plugin" / self.id)

    def settings_dialog(self, parent=None):
        """可选：返回插件设置对话框（QDialog），右键菜单「插件设置」会调用。"""
        return None

    # ---- 生命周期 ----

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def tick(self) -> None:
        pass

    # ---- 框架调用 ----

    def start(self) -> None:
        try:
            self.on_start()
            if self.refresh_interval > 0:
                self._timer.start(self.refresh_interval)
                self.tick()
        except Exception:
            log.exception("插件 %s 启动失败", self.id)

    def stop(self, grace_ms: int = 0) -> None:
        """停止插件。

        默认完全不阻塞调用线程：后台线程只标记中断，存活期由插件
        模块内的驻留表保证（用于「重新加载插件」等 UI 路径）。
        应用退出时可传 grace_ms>0，给在跑的后台线程总计至多这么多
        毫秒的收尾时间，避免带着活线程退出触发 Qt 的致命断言。
        """
        try:
            self._timer.stop()
            worker = getattr(self, "_worker", None)
            self.on_stop()
            if grace_ms > 0 and worker is not None:
                worker.wait(min(int(grace_ms), 3000))
                if worker.isRunning():
                    # 收尾预算耗尽仍在跑（网络卡死等）：退出前强杀，
                    # 否则带着活线程退出可能让整个进程卡死在清理阶段
                    log.warning(
                        "插件 %s 的后台线程未在 %dms 内结束，强制终止",
                        self.id, grace_ms)
                    worker.terminate()
        except Exception:
            log.exception("插件 %s 停止出错", self.id)

    # ---- 工具 ----

    def get_setting(self, key: str, default=None):
        cfg: Config = self.context.get("config")
        if cfg is None:
            return default
        settings = cfg.plugin_settings(self.id)
        return settings.get(key, default)
