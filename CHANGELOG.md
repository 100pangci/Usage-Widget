# Changelog

## [Unreleased]

暂无。

## [v1.1.2] - 2026-09-08

### 变更

- Codex 用量插件：套餐计划显示位置调整到更新时间上方，信息层次更清晰

## [v1.1.1] - 2026-09-08

### 新增

- 新增 Codex 用量插件：读取 `/api/auth/session` JSON、Cookie 或 Codex `auth.json`，
  显示与 CLI `/status` 对应的短周期/每周用量百分比及重置倒计时；网络请求在后台线程执行

### 变更

- **系统监控插件架构重构（v0.4.0）**：单文件采集器拆分为 `collector/`
  包（cpu/memory/disk/net/gpu/system 按指标域分模块），新增
  `SystemPlugin` 基类（注册式采样 + 节流 + 环形历史）与 `widgets.py`
  （自绘曲线组件）；采集改用 psutil，刷新间隔 1s → 2s
- GPU 采集不再阻塞 UI 线程：显卡名枚举改读注册表（PowerShell 仅作
  兜底），利用率采样移入后台线程并缓存上次结果；NVML 利用率改用
  正确的 `nvmlUtilization` 结构体（旧实现传两个 c_uint 指针属未定义
  行为）；NVML 显卡名获取失败时占位，保持名字与句柄数量一致
- 主窗口拆分后自动隐藏（空窗口无意义），合并回主窗口时恢复显示；
  子窗口的置顶状态持久化并在启动时恢复

### 修复

- **Codex 用量测试连接偶发误报过期**：`chatgpt.com/backend-api` 按 Codex CLI
  规则优先请求 `/wham/usage`；有 `accessToken` 时不再与 `sessionToken` Cookie
  混合认证，并在备用路径核验后按真实 HTTP 401/403 给出诊断
- **插件加载/重载竞态**：worker 线程回调里触发 reload 可能与主线程
  `load_all` 重入并发清空 `self.plugins`；加载全程加互斥锁
- **全部插件禁用后残留僵尸实例**：`stop_all`（重载/退出路径）停完不
  清 `self.plugins`，重新加载为空的窗口还持有已停止的旧插件实例与
  菜单引用；停完即清空，加载改走持锁内部路径
- **插件设置保存后折叠状态丢失**：分区重建只恢复隐藏、没恢复折叠；
  `SectionsContainer` 新增折叠查询/恢复接口，重建时保留折叠状态
  （顺带修复 `collapse_section` 只作用于第一个分区的断句 bug）
- **「退出」在有独立窗口时失效**：菜单走 `self.close()`，主窗口
  closeEvent 有子窗口时只隐藏不退出；改 `QApplication.quit()` 结束进程
- **合并/拆分缺防御**：合并不能以主窗口为源（防入口丢失）；拆分窗口
  加幂等标记防重复触发把插件随源窗口销毁
- **KWin 置顶脚本误伤其他窗口（Linux/KDE）**：脚本按标题含
  `usage-widget` 的宽泛子串（或资源类名）匹配，标题恰好含该字符串的
  任何窗口（文件管理器/终端打开相关目录等）都会被一起置顶，且本应用
  其他窗口会被任意一个窗口的脚本连带置顶、无法独立开关；现在每个窗口
  标题带唯一标记 `usage-widget/<window_id>`，脚本只精确匹配自己的标记
- **重新显示分区后「插件设置」菜单缺少该插件**：显示分区勾选后只更新
  了分区可见性，没有重建主窗口菜单——先隐藏再显示的插件（如 opencode）
  不会出现在「插件设置」子菜单里，直到下次菜单重建
- **关闭主窗口必崩溃**：`closeEvent` 调用 `unload_keepabove_script()`
  漏传脚本名参数触发 TypeError（实测进程直接崩溃）；切换置顶、
  `always_on_top=false` 时的启动、KDE 启动路径同理——全部补上按窗口
  区分的脚本名（kwin 函数另加默认脚本名兜底），并修正启动时置顶
  状态应用反了的问题
- **子窗口「拆分」丢失第一个插件**：拆分只把第 2 个起的插件拆成独立窗口，
  第 1 个还挂在被关闭的源窗口里——随 `deleteLater` 一起销毁，实例从所有
  窗口与 `config.windows` 中永久消失，且 timer 变僵尸（每秒 tick 已删除的
  控件抛 RuntimeError）；现在与主窗口行为一致，全部插件各自拆成独立窗口
- **Linux 磁盘 I/O 读数约虚高一倍**：刷新间隔 1s→2s 后忙时增量仍按 1 秒
  窗口归一化；改用真实采样间隔归一，并优先用 psutil `busy_time`
  （/proc/diskstats io_ticks 口径，并发读写不双计），无该字段的平台回退
  `read_time + write_time`
- Windows PDH 磁盘计数器不可用（如未启用 diskperf）时磁盘 I/O 恒为 0 且
  无任何提示：现在告警一次（提示管理员运行 `diskperf -Y`）并不再每 tick
  重试打开查询
- `always_on_top=false` 的启动路径会 `show()` 主窗口：全部插件分离后
  重启（空主窗口）也会弹出空窗口；启动时只清置顶 flag 不显示
- 主窗口右键菜单出现两个「插件设置」入口：基类的单项版只打开第一个
  插件的对话框、易误导，只保留给子窗口；主窗口用自己的逐插件子菜单
- 「拆分」与重启恢复产生的独立窗口标题显示 w2/w3，与「分离」产生的
  窗口（插件名）不一致；统一用插件名
- 曲线历史注释「90 点 = 90 秒」过期：tick 已改 2s，实际约 180 秒
- **重新显示分区后插件不工作**：`show_section` 只重启了 tick 定时器、
  没走 `plugin.start()`，opencode/commandcode 这类在 `on_start` 里启动
  倒计时/加载设置的插件重新显示后不会恢复；现在恢复走完整启动流程
- 系统监控只显示「开机」分区时开机时长不更新（`tick` 提前返回漏判了
  uptime 标签）
- `pyproject.toml` 缺 `py-modules = ["main"]`，pip 安装后
  `usage-widget` 命令入口解析不到 main 模块

## [v1.1.0] - 2026-08-29

### 新增

- **系统监控插件**：CPU / 内存用滚动实时曲线（90 点/90 秒历史，各自
  颜色区分），磁盘用 I/O 使用率曲线（读写繁忙程度，Windows PDH
  `% Disk Time`、Linux `/proc/diskstats io_ticks`），GPU 实时监控
  （跨厂商：NVIDIA 走 NVML、Windows 其他卡走 WMI GPU Engine、Linux
  AMD/Intel 走 sysfs `gpu_busy_percent`；多卡自动枚举、每块卡一行
  紫色曲线 + 显存占用、无卡自动隐藏），网络用迷你双向曲线（下行蓝/
  上行绿 + 实时速率），开机用半透明圆角卡片展示（X天 X小时 X分
  自适应）；纯标准库实现（Windows 用 ctypes 调系统 API、Linux 读
  /proc），不依赖第三方包，发行版插件目录可直接运行；使用率 <50%
  绿 / <80% 黄 / ≥80% 红，每秒刷新
- 系统监控插件设置对话框：各指标（CPU/GPU/内存/磁盘/网络/开机）可
  自定义显示顺序（拖拽列表）与显隐（勾选），持久化到插件数据目录

## [v1.0.4] - 2026-08-29

### 新增

- **插件自动发现与启用**：新插件（升级后新增或用户自己放进 `plugin/`
  目录的）会自动补到 `plugins.enabled/order` 末尾并持久化，老用户不用
  改配置就能用上新插件；用户主动从 `enabled` 里删掉的插件不会被自动
  拉回来（`order` 记录「曾经见过」，见过但不在 `enabled` 视为显式禁用）；
  已不存在的插件会自动从配置里清理

### 修复

- **Windows 发行版 commandcode 插件不加载**：commandcode 插件自 v1.0.1
  发布以来一直没进默认配置——`DEFAULT_CONFIG` 的 `plugins.enabled/order`
  只有 `clock` 和 `opencode_usage`，老用户升级后新插件被静默跳过。现在
  默认启用 `commandcode`（老用户也无需手动改配置，自动发现逻辑会补上）
- **点击「重新加载插件」后文字上下被裁切（Linux 复现）**：两个叠加的
  时序问题——`clear()` 里旧分区 `removeWidget` 后仍占用布局位置直到
  `deleteLater` 真正销毁，新分区插进来导致布局混乱；且 `populate_sections`
  末尾同步调用 `_fit_to_content()`，新分区刚 `addWidget` 时 `sizeHint` 还
  没计算完成（Qt 布局异步），按旧/空值 resize 导致窗口高度不足、文字被
  上下边框裁切。现在 `clear()` 立即 `setParent(None)` 脱离布局，尺寸收缩
  统一改为延迟一帧等布局完成后再执行

## [v1.0.3] - 2026-08-27

### 修复

- **点击「重新加载插件」卡死界面**：两个网络插件的 `on_stop` 在 UI 线程
  `wait(8000)` 等后台取数线程（慢网络下线程要跑完最长 30s+ 的请求链，
  wait 必然打满 8 秒，两个插件叠加界面冻结 16 秒以上）。现在停止时只标记
  中断、零阻塞返回；线程存活期由插件模块内的驻留表保证直到自然结束，
  结束后自动清理
- opencode 插件工作区自动检测在 UI 线程做同步网络请求（15s 超时）导致
  卡顿的问题：检测移入后台取数线程，结果回到主线程后再持久化
- 重载/停止后迟到的取数结果可能触碰已销毁控件的问题（停止时清空标签引用）
- 应用退出时后台取数线程可能导致进程卡死在清理阶段：退出路径给 2.5s 共享
  收尾预算，超时强制终止，并在 `aboutToQuit` 兜底清理
- `pyproject.toml` 的 packages 列表遗漏 `plugins.commandcode`
  （v1.0.1 新增的插件，pip 安装场景会缺包）

## [v1.0.2] - 2026-08-27

### 修复

- **发行版（PyInstaller 打包）SSL 证书验证失败**（`CERTIFICATE_VERIFY_FAILED`）：
  打包后系统 CA 证书路径不可用，改用 certifi 的 cacert.pem（新增 `certifi` 依赖，
  构建时通过 `--hidden-import certifi` 收集进包）
- **发行版 KDE 置顶失效**：PyInstaller bootloader 会把 `_internal/` 下的 Qt 库
  写入子进程的 `LD_LIBRARY_PATH`，导致 `qdbus` 加载到版本不匹配的 Qt 库直接
  崩溃；调用子进程时剔除 `_internal` 路径
- 插件加载失败被静默跳过的问题：KWin 脚本加载失败的详细信息现在会写入日志

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
