#!/bin/bash
# 联通 IBOS 批量查询助手 - macOS 启动器
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Python 运行环境:Framework 构建(GUI 最佳,系统 python.org 安装包)
#                -> 项目虚拟环境 -> 用户 venv -> 系统 python3
PY=""
for candidate in \
  "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" \
  "$SCRIPT_DIR/.venv/bin/python" \
  "$HOME/.workbuddy/binaries/python/envs/ibos-scraper/bin/python" \
  "$(which python3 2>/dev/null)"; do
  if [ -x "$candidate" ]; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "未找到 Python 运行环境,请安装 Python 3.10+ 或创建虚拟环境 .venv"
  exit 1
fi

# 静默补装依赖(优先用 requirements.txt)
"$PY" -c "import pyautogui, pyperclip" 2>/dev/null || {
  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$PY" -m pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null
  else
    "$PY" -m pip install -q pyautogui pyperclip Pillow pyobjc-framework-ApplicationServices pyobjc-framework-Quartz 2>/dev/null
  fi
}

# 修复 tkinter 在非交互式环境找不到 Tcl/Tk 库的问题
# 自动探测 Tcl/Tk 路径(兼容不同 Python 安装位置)
PY_PREFIX="$("$PY" -c 'import sys; print(sys.prefix)' 2>/dev/null)"
for tcl_ver in tcl9.0 tcl8.6 tcl8.5; do
  if [ -d "$PY_PREFIX/lib/$tcl_ver" ]; then
    export TCL_LIBRARY="$PY_PREFIX/lib/$tcl_ver"
    break
  fi
done
for tk_ver in tk9.0 tk8.6 tk8.5; do
  if [ -d "$PY_PREFIX/lib/$tk_ver" ]; then
    export TK_LIBRARY="$PY_PREFIX/lib/$tk_ver"
    break
  fi
done

# 后台启动 GUI,终端窗口立即退出(Dock 不会多出一个终端)
nohup "$PY" "$SCRIPT_DIR/gui/app.py" > /dev/null 2>&1 &
