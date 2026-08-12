#!/usr/bin/env bash
# ============================================================
#  usage-widget Linux 构建脚本（PyInstaller）
#  用法: ./build_linux.sh [onefile] [console]
#    onefile  - 单文件模式（默认 onedir，启动更快）
#    console  - 保留终端输出（默认 --noconsole，从桌面/自启动启动）
#  产物: dist/usage-widget/usage-widget
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

MODE=onedir
CONSOLE_FLAG=(--noconsole)
for arg in "$@"; do
    case "$arg" in
        onefile) MODE=onefile ;;
        console) CONSOLE_FLAG=() ;;
    esac
done

VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
    echo "[1/3] 创建虚拟环境 .venv ..."
    python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"

echo "[2/3] 安装依赖（PySide6 + pyinstaller）..."
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -e . pyinstaller

echo "[3/3] PyInstaller 打包（$MODE）..."
EXTRA=()
[ "$MODE" = onefile ] && EXTRA+=(--onefile)
"$PY" -m PyInstaller --noconfirm --clean --name usage-widget \
    "${CONSOLE_FLAG[@]}" \
    --hidden-import urllib.request \
    --hidden-import urllib.error \
    --hidden-import configparser \
    --hidden-import concurrent.futures \
    --exclude-module pytest \
    "${EXTRA[@]}" \
    main.py

echo "[4/4] 复制外部插件目录（plugin/）..."
OUT="dist/usage-widget"
mkdir -p "$OUT/plugin"
cp -r plugins/. "$OUT/plugin/"
find "$OUT/plugin" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo
echo "完成: dist/usage-widget/usage-widget"
echo "外部插件目录（可改不重打包）: dist/usage-widget/plugin"
echo "配置目录: ~/.config/usage-widget"
echo "插件数据: ~/.usage-widget/plugin"
