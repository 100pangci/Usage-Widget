# Changelog

## [v1.0.1] - 2026-08-27

### 新增

- commandcode 用量插件：5小时/每周/每月用量进度条（百分比 + 绿/黄/红颜色分级）、
  重置倒计时（本地每秒递减）、本月花费与请求次数/tokens、订阅计划与计费周期
- commandcode 设置对话框：session_token / session_data 分开填写，
  支持从剪贴板解析、从文件导入，保存后合并为完整 Cookie（权限 600）
- KDE Plasma 置顶支持：置顶改由 KWin 脚本控制（`keepAbove` 属性 + `windowAdded`
  监听，窗口映射/重建后自动补设），解决 Wayland 下 Qt 置顶 hint 被忽略的问题；
  自动探测 `qdbus`/`qdbus6`/`qdbus-qt6`，非 KDE 环境回退 Qt hint，Windows 逻辑不变

### 修复

- 折叠/隐藏插件分区后窗口和半透明背景不收缩的问题（窗口布局改用
  `SetNoConstraint` 手动控制尺寸；`setVisible` 的 LayoutRequest 是异步的，
  延迟一帧再按新内容收缩窗口）
- 隐藏分区后「插件设置」菜单仍显示该插件的问题（按可见性过滤，显隐时重建菜单）
- `TextRow` 组件在缺少父窗口时崩溃的问题
- 设置对话框测试连接按钮卡死的问题（cookie 未配置时按钮状态未恢复）
- Windows 上保存 cookie 时 `os.chmod` 报错的问题
- QSS 模板字符串转义错误导致样式解析失败的问题
- 配置防御：config.json 编码损坏（`UnicodeDecodeError`）不再导致启动崩溃；
  刷新间隔配置非法值时回退默认值
- opencode 用量解析：RSC 解析器对畸形数字会明确报错而非崩溃

## [v1.0.0] - 2026-08-13

### 修复

- 设置对话框工作区自动检测无效的问题（`_TaskThread` 返回的 `(status, result)` 元组未解包，导致下拉框被清空且无提示）
- 工作区检测失败时现在会显示具体错误原因

## [v0.1.0] - 2026-08-12

### 新增

- 悬浮窗监控框架：无边框半透明、左键拖动、右键菜单、Esc 关闭
- 插件系统：目录扫描加载、热重载、分区折叠与显隐、单个插件失败不影响整体
- 时钟示例插件
- 配置自动生成与持久化（深合并默认值）、插件设置访问
- 冒烟测试（pytest）
- opencode 用量插件：滚动/每周/每月用量百分比与重置倒计时（本地每秒递减）、本月费用
- 插件设置对话框：cookie 粘贴/从文件导入、工作区自动检测与切换、代理与刷新间隔配置、测试连接
- 插件数据持久化到 `~/.usage-widget/plugin/<id>/`（cookie 权限 600，程序启动时为未配置状态）
- 右键菜单：分区显隐与窗口位置记忆、置顶开关（切换不跳位）
- Windows 支持：`%APPDATA%` 配置目录、注册表系统代理探测、QPainter 半透明面板
- 打包：PyInstaller 构建脚本（`build_windows.bat` / `build_linux.sh`）+ GitHub Actions
  手动构建 / `v*` 标签自动发版（Release 正文自动取 CHANGELOG 对应版本段，附校验和）
- 用量数据解析：SolidStart RSC 序列化解析器、`lite.subscription.get` 等 RPC 客户端

### 修复

- Windows 分层窗口下 QSS 半透明背景不上屏的问题（改用 QPainter 绘制）
- 关闭插件分区后窗口背景无法收回的问题（布局最小尺寸约束）
- Windows 上无边框窗口无法拖动的问题（改用手动 move）
- Windows 上切换置顶导致窗口跳位的问题
- 插件相对导入失败、打包后标准库模块缺失的问题
- 退出时后台取数线程导致进程无法结束的问题（支持中断）
