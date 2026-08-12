# usage-widget

可拓展的桌面悬浮窗监控框架。基于 Python + PySide6（Qt 6），单进程运行，
插件化设计：框架负责悬浮窗与插件生命周期，插件只管自己的分区 UI 和数据刷新，
单个插件出错不影响整体。

## 特性

- 无边框半透明悬浮窗，左键拖动、右键菜单、Esc 关闭
- 插件化：`plugins/<id>/plugin.py` 一个文件即可接入，支持热重载（右键重新加载）
- 单窗口分区显示，右键菜单可单独显隐各分区
- 配置自动生成与持久化（`~/.config/usage-widget/config.json`）
- 插件加载失败自动跳过，不影响其他插件

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e .      # 安装依赖（PySide6）
.venv/bin/python main.py          # 启动
.venv/bin/python main.py -v       # 调试日志
.venv/bin/python main.py -c /path/to/config.json   # 指定配置
```

> 依赖只声明了 PySide6（见 `pyproject.toml`）；也可直接
> `.venv/bin/pip install pyside6` 后运行。

## 插件开发

### 目录约定

```
plugins/<插件id>/
├── plugin.py     # 插件实现（推荐）
├── __init__.py   # 或把实现放这里
└── main.py       # 或这里
```

框架按 `plugin.py` → `__init__.py` → `main.py` 顺序寻找入口模块。

### 插件 API

继承 `plugins/base.py` 的 `Plugin`：

```python
from PySide6.QtWidgets import QLabel, QWidget
from plugins.base import Plugin

class MyPlugin(Plugin):
    id = "my_plugin"          # 唯一标识，缺省用目录名
    name = "我的插件"          # 分区标题
    version = "0.1.0"
    description = "示例"
    refresh_interval = 1000   # tick 间隔 ms，0 表示不自动 tick

    def create_widget(self, parent) -> QWidget:
        # 返回本插件的分区 UI（只做构建，不要在这里取数据）
        return QLabel("hello", parent)

    def on_start(self) -> None:   pass   # 启动钩子
    def on_stop(self) -> None:    pass   # 停止钩子
    def tick(self) -> None:       pass   # 定时刷新数据

    def get_setting(self, key, default=None):
        # 读 config.json 中 plugins.settings.<id> 下自己的配置
        ...
```

可选提供 `create_plugin()` 工厂函数（带参数构造插件实例时使用），
缺省则取模块内第一个 `Plugin` 子类。

通用控件见 `ui/base_widgets.py`：`TextRow`（标签+数值行）、`BarGauge`（带标签进度条）。

### 启用/排序

`config.json` 中 `plugins.enabled` 控制启用列表，`plugins.order` 控制分区顺序，
`plugins.settings.<id>` 存放插件私有配置。

### 调试

```bash
.venv/bin/python main.py -v   # 日志带插件加载/启动详情
```

改完插件代码后，悬浮窗右键 →「重新加载插件」即时生效。

## 配置说明

首次运行自动生成配置文件（Linux: `~/.config/usage-widget/config.json`，
Windows: `%APPDATA%\usage-widget\config.json`）：

```json
{
  "window": { "width": 300, "opacity": 0.92, "always_on_top": true, "position": [] },
  "plugins": {
    "enabled": ["clock", "opencode_usage"],
    "order": ["clock", "opencode_usage"],
    "settings": {}
  }
}
```

## 交互

| 操作 | 行为 |
| --- | --- |
| 左键拖动 | 移动窗口（Wayland 下走系统移动） |
| 右键 | 分区显隐、置顶开关、重新加载插件、打开配置目录、退出 |
| Esc | 关闭 |

## 平台说明

- **Linux + KDE Plasma（Wayland）**：首选环境。Qt 6.5+ 的 `WindowStaysOnTopHint`
  在 KDE Wayland 下不可靠，若需要稳定置顶，请在
  「系统设置 → 窗口管理 → 窗口规则」添加一条规则：窗口类 `usage-widget`，
  强制「保持在上」。
- **X11**：置顶 hint 与位置记忆（`window.position`）均正常生效。
- Wayland 协议限制下程序无法读写窗口位置，位置记忆仅 X11 有效。

## 目录结构

```
usage-widget/
├── main.py                 # 入口
├── core/
│   ├── app.py              # QApplication 装配与启动顺序
│   ├── config.py           # 配置读写（深合并默认值）
│   ├── plugin_manager.py   # 插件扫描/加载/启停/重载
│   └── window.py           # 主悬浮窗（无边框/透明/拖动/右键菜单）
├── ui/
│   ├── sections.py         # 分区容器（垂直堆叠）
│   └── base_widgets.py     # 通用控件 TextRow / BarGauge
├── plugins/
│   ├── base.py             # Plugin 基类（插件 API）
│   └── clock/              # 示例插件：时钟
└── tests/                  # pytest 冒烟测试
```

## 打包（PyInstaller）

主程序用 PyInstaller 打包，**插件不打包进 exe**，放在可执行文件旁的 `plugin/`
目录——新增/修改插件无需重新打包，重启应用即生效。

```bash
# Linux（产物 dist/usage-widget/，插件在 dist/usage-widget/plugin/）
./build_linux.sh

# Windows（产物 dist\usage-widget\，插件在 dist\usage-widget\plugin\）
build_windows.bat

# 可选参数：onefile（单文件，启动稍慢）/ console（保留控制台看日志）
```

GitHub Actions（`.github/workflows/build.yml`）：

- **手动构建**：Actions 页面 Run workflow，产物 `usage-widget_<版本>_win.zip` /
  `usage-widget_<版本>_linux.tar.gz`（版本取自 pyproject.toml）
- **发版**：CHANGELOG 里写好 `## [vX.Y.Z] - 日期` 段落后推标签
  `git tag v1.0.0 && git push origin v1.0.0`，自动构建并创建 Release
  （正文取自 CHANGELOG 对应版本段，附带两个平台产物与校验和）

插件数据（cookie 等）保存在 `~/.usage-widget/plugin/<id>/`
（Windows: `C:\Users\xxx\.usage-widget\...`，权限 600）。

## 测试

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/
```
