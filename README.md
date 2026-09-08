# usage-widget

可拓展的桌面悬浮窗监控框架。基于 Python + PySide6（Qt 6），单进程运行，
插件化设计：框架负责悬浮窗与插件生命周期，插件只管自己的分区 UI 和数据刷新，
单个插件出错不影响整体。

## 特性

- 无边框半透明悬浮窗，左键拖动、右键菜单、Esc 关闭
- 插件化：`plugins/<id>/plugin.py` 一个文件即可接入，支持热重载（右键重新加载）
- 插件自动发现：新放进 `plugins/`（或发行版 `plugin/`）目录的插件自动启用，
  无需改配置；用户从 `enabled` 里删掉的插件不会被自动拉回来
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

> 依赖声明为 PySide6 + psutil（见 `pyproject.toml`）；也可直接
> `.venv/bin/pip install pyside6 psutil` 后运行。

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
系统监控插件自身拆分参考：`plugins/system_monitor/base.py`（采集注册 +
节流 + 环形历史的基类，借鉴 Glances 插件架构）、`collector/`（按指标域
拆分的 psutil 采集器）、`widgets.py`（QPainter 自绘曲线控件）。

### 主题适配（必须提供两套颜色）

插件里的文字/进度条颜色必须同时提供**深色/浅色**两套，跟随框架主题
（右键 → 系统设置 → 主题）自动切换。约定：定义 `_COLORS_DARK` /
`_COLORS_LIGHT` 两个字典，再提供取色函数（内部读 `core.theme`），
独立运行（无框架）时回退深色：

```python
_COLORS_DARK = {"dim": "#9aa3b5", "text": "#dfe3ea"}
_COLORS_LIGHT = {"dim": "#5a6270", "text": "#1f2430"}

def _theme_colors() -> dict:
    try:
        from core.theme import is_dark
        return _COLORS_DARK if is_dark() else _COLORS_LIGHT
    except ImportError:
        return _COLORS_DARK

def DIM() -> str:   return _theme_colors()["dim"]
def TEXT() -> str:  return _theme_colors()["text"]
```

UI 里用 `{DIM()}` / `{TEXT()}` 代替硬编码颜色。语义色（绿/黄/红）
两种主题下也要提供对应变体。参考 `plugins/system_monitor/plugin.py`。

### 插件设置对话框

实现 `settings_dialog(parent)` 返回 `QDialog`，右键菜单「插件设置」会弹出；
对话框保存后**只重建该插件自己的分区**（不重载其他插件）：

```python
class MyPlugin(Plugin):
    def settings_dialog(self, parent=None):
        return MySettingsDialog(self, parent)   # QDialog 子类
```

设置持久化建议放插件数据目录 `~/.usage-widget/plugin/<id>/`（框架自动创建），
参考 `plugins/system_monitor/settings_dialog.py`（顺序 + 显隐的自定义设置）。

### 单个分区重建

框架提供 `_rebuild_plugin_section(plugin)`（`core/window.py`）：只重建指定
插件的分区 widget，保留折叠状态，**不影响其他插件**。插件设置保存后框架
自动调用，无需手动处理；若需从插件内主动触发，可自行在对话框 accept 后
调用窗口的该方法。

### 插件数据目录

`self.data_dir` 指向 `~/.usage-widget/plugin/<id>/`，cookie、settings.json 等
持久化数据放这里（发行版同样适用，权限 600）。

### 启用/排序

`config.json` 中 `plugins.enabled` 控制启用列表，`plugins.order` 控制分区顺序，
`plugins.settings.<id>` 存放插件私有配置。新插件（目录里出现但不在配置中的）
会被自动补到 `enabled`/`order` 末尾并持久化；已不存在的插件自动从配置清理。

### 调试

```bash
.venv/bin/python main.py -v   # 日志带插件加载/启动详情
```

改完插件代码后，悬浮窗右键 →「重新加载插件」即时生效。
插件自己的设置保存后，对应分区会自动重建（无需手动重载全部插件）。

## 配置说明

首次运行自动生成配置文件（Linux: `~/.config/usage-widget/config.json`，
Windows: `%APPDATA%\usage-widget\config.json`）：

```json
{
  "window": {
    "width": 300,
    "opacity": 0.92,
    "theme": "dark",
    "always_on_top": true,
    "position": []
  },
  "plugins": {
    "enabled": ["clock", "opencode_usage", "commandcode", "system_monitor", "codex_usage"],
    "order": ["clock", "opencode_usage", "commandcode", "system_monitor", "codex_usage"],
    "settings": {}
  }
}
```

`window.theme`: `"dark"` / `"light"`；`window.opacity`: 0.3~1.0 背景透明度。
两者可在右键 →「系统设置」里调整并即时生效。

### Codex 用量插件

右键 →「显示分区」勾选「Codex 用量」，再从右键 →「插件设置」配置认证。
最简单的方式是在已登录的同一浏览器打开
`https://chatgpt.com/api/auth/session`，全选复制整段 JSON，粘贴到插件输入框；
插件会自动提取 `accessToken` 和账户信息。也支持 `chatgpt.com` 请求头里的完整
Cookie、Netscape/JSON Cookie 文件，以及 Codex CLI 的 `~/.codex/auth.json`。
代理选择 `auto` 时会依次读取环境变量、系统代理和旧版
`~/.config/usage-widget/config.toml` 的 `[ui].proxy`。
认证信息会保存在插件数据目录的 `cookie.txt`（Linux 权限 600），插件显示与
`codex` 内 `/status` 对应的短周期、每周用量和重置倒计时。

## 交互

| 操作 | 行为 |
| --- | --- |
| 左键拖动 | 移动窗口（Wayland 下走系统移动） |
| 右键 | 分区显隐、置顶开关、插件设置、系统设置（主题/透明度）、重新加载插件、打开配置目录、退出 |
| Esc | 关闭 |

## 平台说明

- **Linux + KDE Plasma**：首选环境。KDE 下置顶由 KWin 脚本接管——
  启动/右键菜单切换时自动通过 KWin DBus 加载一次性脚本设置窗口
  `keepAbove` 属性（等价标题栏「保持在上」按钮），Wayland 下 Qt 的
  `WindowStaysOnTopHint` 被 KWin 忽略时仍能可靠置顶；窗口重建（如
  `setWindowFlags`）后脚本监听 `windowAdded` 自动补设。非 KDE 会话
  自动回退到 Qt hint。
- **Windows / 非 KDE Linux（X11）**：置顶走 Qt `WindowStaysOnTopHint`，
  X11 下位置记忆（`window.position`）正常生效。
- Wayland 协议限制下程序无法读写窗口位置，位置记忆仅 X11 有效。

## 目录结构

```
usage-widget/
├── main.py                 # 入口
├── core/
│   ├── app.py                 # QApplication 装配与启动顺序
│   ├── config.py              # 配置读写（深合并默认值）
│   ├── theme.py               # 全局主题（深色/浅色）与颜色表
│   ├── plugin_manager.py      # 插件扫描/加载/启停/重载
│   ├── system_settings_dialog.py  # 系统设置（主题/透明度）
│   └── window.py              # 主悬浮窗（无边框/透明/拖动/右键菜单）
├── ui/
│   ├── sections.py         # 分区容器（垂直堆叠）
│   └── base_widgets.py     # 通用控件 TextRow / BarGauge
├── plugins/
│   ├── base.py             # Plugin 基类（插件 API）
│   ├── clock/              # 示例插件：时钟
│   ├── opencode_usage/     # opencode 用量监控（滚动/每周/每月 + 本月费用）
│   ├── commandcode/        # commandcode 用量监控（5小时/每周/每月 + credits）
│   ├── codex_usage/        # Codex CLI 用量监控（短周期/每周 + 重置时间）
│   └── system_monitor/     # 系统监控（CPU/内存/磁盘 I/O/GPU/网络/开机）
│       ├── base.py             # 采集编排基类（注册式采集 + 节流 + 环形历史）
│       ├── collector/          # 采集层：cpu/memory/disk/net/gpu/system（psutil）
│       ├── widgets.py          # SparkLine / NetSpark 自绘曲线
│       ├── plugin.py           # 插件主体（UI 构建 + tick）
│       └── settings_dialog.py  # 设置对话框（顺序 + 显隐）
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

> 打包注意：系统监控插件运行时 `import psutil`，psutil 是编译扩展，
> 必须打进 exe（构建脚本已带 `--hidden-import psutil`），
> 插件目录里的是纯 Python 源码。

## 测试

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/
```
