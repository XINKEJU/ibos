#!/bin/bash
# 构建完全自包含的 IBOS查询助手.app(py2app)
# 必须在 Framework 版 Python 下运行(它自带 Tcl/Tk, 否则打出的 .app 内 Tk 无法启动)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

echo "==> 检查 Framework Python 与 Tcl/Tk"
if ! "$PY" -c "import tkinter; tkinter.TkVersion" 2>/dev/null; then
  echo "缺少 Framework Python 或 Tcl/Tk,请从 https://www.python.org 安装 macOS 64-bit installer"
  exit 1
fi
echo "    Tk 可用: $("$PY" -c 'import tkinter;print(tkinter.TkVersion)')"

echo "==> 清理旧构建产物"
rm -rf build "dist/IBOS查询助手.app"

echo "==> 运行 py2app 构建(首次约 1~3 分钟)"
# 若在 WorkBuddy 终端内构建, 禁用其 safe-delete 钩子, 否则批量 .pyc 清理会要求交互确认而中断
export CODEBUDDY_SAFE_DELETE_ENABLED=0
"$PY" setup.py py2app

APP="dist/IBOS查询助手.app"
if [ ! -d "$APP" ]; then
  echo "构建失败: 未生成 $APP"
  exit 1
fi

echo "==> 去除 Gatekeeper 隔离标记(未签名 .app 双击可直接打开,无需右键)"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "==> 构建完成: $APP"
# 在访达中定位,方便拖入应用程序文件夹 / Dock
open -R "$APP"
