# -*- coding: utf-8 -*-
"""py2app 打包配置: 生成完全自包含的 IBOS查询助手.app。

运行(必须用 Framework 版 Python, 它自带 Tcl/Tk, 否则 .app 内 Tk 无法启动):
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 setup.py py2app

产物: dist/IBOS查询助手.app
    - 内嵌 Python 解释器 + 全部第三方依赖(pyautogui/pyobjc/Pillow/bs4/openpyxl...)
    - 不依赖任何外部 Python 或项目目录, 换机器 / 升级系统后仍可直接双击运行
    - 数据(config.json / 名单 / 导出结果 / 日志)放在 ~/Documents/ibos, 首次运行自动初始化
"""
from setuptools import setup

# ---------------------------------------------------------------------------
# 禁用 py2app 的 Qt recipe(本应用为 Tk 技术栈, 不兼容也不需 Qt)
# 构建用的 Framework Python 若残留 PyQt6/PySide, qt6 recipe 会因无法解析 sip 模块名
# 而抛 InvalidRelativeImportError 导致构建中断。直接禁用这两个 recipe 即可, 无需改动
# 用户 Python 环境(同时配合下方 excludes 避免 PyQt6 被收集进 .app)。
# ---------------------------------------------------------------------------
try:
    import py2app.recipes.qt6 as _qt6
    _qt6.check = lambda self, mf: None
except Exception:
    pass
try:
    import py2app.recipes.qt5 as _qt5
    _qt5.check = lambda self, mf: None
except Exception:
    pass

# 主入口(py2app 会把 gui/app.py 编译为 .app 的 Mach-O 可执行)
APP = ["gui/app.py"]

# 随 .app 打包的静态资源: 拷到 Contents/Resources, 代码用 gui/app.py 的 _resource_path 读取
RESOURCES = [
    "assets/AppIcon.icns",
    "assets/AppIcon.png",
    "config.example.json",
    "使用说明.txt",
]

OPTIONS = {
    # 图标(可执行文件名通过 plist 的 CFBundleExecutable 指定, py2app 据此命名 Mach-O)
    "iconfile": "assets/AppIcon.icns",
    # 不用 argv_emulation: 本应用是 GUI, 不需要处理 Finder 打开文件的 NSAppleEvent
    "argv_emulation": False,
    # 继承 shell 的 PATH/LANG 等环境, 避免打包后子进程找不到命令
    "emulate_shell_environment": True,
    # 显式包含业务包与第三方库, 杜绝 py2app 递归收集漏掉 pyobjc framework 绑定
    "packages": ["core", "gui", "pyautogui", "pyperclip", "PIL",
                 "bs4", "lxml", "openpyxl"],
    "includes": ["Quartz", "Cocoa", "AppKit", "ApplicationServices",
                 "CoreFoundation", "Foundation",
                 "pyautogui", "pyperclip", "PIL", "bs4", "lxml", "openpyxl"],
    # 排除与本项目无关、且会触发 py2app 依赖扫描崩溃 / 体积爆炸的第三方包。
    # 构建用的 Framework Python 环境被混入 PyInstaller/PyQt6/numpy 等:
    # py2app 的 detect_dunder_file recipe 会尝试收集所有顶层包, PyInstaller 的
    # hooks 子包含非法模块名(hook-PyQt5.QtMultimediaWidgets)导致 ImportError 崩溃;
    # numpy/matplotlib/pandas/PyQt 等即使不崩也会把 .app 撑到数百 MB。
    "excludes": [
        "PyInstaller", "pyinstaller", "pyinstaller_hooks_contrib",
        "numpy", "matplotlib", "pandas", "scipy", "scipy.spatial",
        "tornado", "IPython", "ipykernel", "jupyter_client", "jedi", "parso",
        "debugpy", "zmq", "notebook", "widgetsnbextension",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "PyQt6.Qt6", "PyQt6.sip", "PyQt6_Qt6", "PyQt5.QtWidgets",
    ],
    "resources": RESOURCES,
    # Info.plist(覆盖 py2app 默认值)
    "plist": {
        "CFBundleName": "IBOS查询助手",
        "CFBundleDisplayName": "IBOS批量查询助手",
        "CFBundleIdentifier": "com.xinkeju.ibos-query-assistant",
        "CFBundleExecutable": "ibos-assistant",
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        # 系统会在首次需要时弹出授权请求, 这里的描述会在请求框中显示
        "NSAppleEventsUsageDescription": "需要辅助功能权限,以模拟鼠标键盘操作 Edge 完成自动查询。",
        "NSScreenCaptureUsageDescription": "可选:开启屏幕录制权限后可启用画面变化检测,加快查询并支持截图。",
    },
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
