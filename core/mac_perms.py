# -*- coding: utf-8 -*-
"""macOS 系统权限检测与窗口辅助(基于 pyobjc)。"""
import subprocess
import time

# 内部缓存(避免频繁遍历 NSWorkspace.getRunningApplications)
_cache = {}
_CACHE_TTL = 5.0  # 秒

# ---- 常量(替代魔术数字) ----
_PANEL_MIN_W, _PANEL_MAX_W = 200, 1200   # 保存面板尺寸区间(实测面板约 364x247)
_PANEL_MIN_H, _PANEL_MAX_H = 150, 700
_WIN_MIN_W, _WIN_MIN_H = 200, 100        # 有效窗口最小尺寸


def screen_recording_ok() -> bool | None:
    """屏幕录制权限(截图需要)。"""
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def accessibility_ok() -> bool | None:
    """辅助功能权限(模拟鼠标键盘需要)。"""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def check_all() -> dict:
    return {
        "screen_recording": screen_recording_ok(),
        "accessibility": accessibility_ok(),
    }


def _escape_applescript(s: str) -> str:
    """转义 AppleScript 字符串中的特殊字符,防止注入。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def activate_app(app_name: str = "Microsoft Edge") -> bool:
    """将指定应用调到前台。"""
    try:
        safe = _escape_applescript(app_name)
        subprocess.run(
            ["osascript", "-e", f'tell application "{safe}" to activate'],
            check=False, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def notify(title: str, message: str) -> None:
    """发送 macOS 通知中心提醒(尽力而为,失败静默)。"""
    try:
        safe_t = _escape_applescript(title)
        safe_m = _escape_applescript(message)
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}"'],
            check=False, capture_output=True, timeout=10)
    except Exception:
        pass


def open_privacy_pane(accessibility: bool = True) -> None:
    """打开 macOS 隐私设置页(辅助功能 / 屏幕录制)。"""
    key = "Privacy_Accessibility" if accessibility else "Privacy_ScreenCapture"
    try:
        subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{key}"])
    except Exception:
        pass


def edge_window_bounds() -> tuple[int, int, int, int] | None:
    """返回 Edge 主窗口 (x, y, w, h);未找到返回 None。"""
    try:
        import Quartz
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        for w in wins:
            if w.get(Quartz.kCGWindowOwnerName) == "Microsoft Edge" \
                    and w.get(Quartz.kCGWindowLayer, 0) == 0:
                b = w.get(Quartz.kCGWindowBounds) or {}
                try:
                    def g(k):
                        v = b.get(k)
                        if v is None:
                            try:
                                v = getattr(b, k)
                            except Exception:
                                v = None
                        return v
                    x, y = int(g("X")), int(g("Y"))
                    wd, ht = int(g("Width")), int(g("Height"))
                    if wd > _WIN_MIN_W and ht > _WIN_MIN_H:
                        return (x, y, wd, ht)
                except Exception:
                    continue
    except Exception:
        pass
    return None


def edge_running() -> bool | None:
    """Edge 是否在运行(基于 NSWorkspace,不依赖窗口/权限)。
    返回 True/False;读取失败返回 None(调用方应视为可用,不阻断)。"""
    now = time.time()
    if now - _cache.get("edge_running_ts", 0) < _CACHE_TTL:
        return _cache.get("edge_running_val")
    try:
        from AppKit import NSWorkspace
        for a in NSWorkspace.sharedWorkspace().runningApplications():
            if a.localizedName() == "Microsoft Edge":
                _cache["edge_running_val"] = True
                _cache["edge_running_ts"] = now
                return True
        _cache["edge_running_val"] = False
        _cache["edge_running_ts"] = now
        return False
    except Exception:
        return None


def frontmost_app_name() -> str | None:
    """返回最前端应用名称;读取失败返回 None。"""
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace().frontmostApplication().localizedName()
    except Exception:
        return None


def frontmost_window_title() -> str | None:
    """返回最前端应用窗口的标题(优先辅助功能 API,未授权时回退 CGWindow,可能返回 None)。"""
    t = _ax_frontmost_title()
    if t is not None:
        return t
    try:
        import Quartz
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        for w in wins:
            if w.get(Quartz.kCGWindowLayer, 0) == 0:
                return w.get(Quartz.kCGWindowName)
    except Exception:
        pass
    return None


def _ax_frontmost_windows():
    """返回最前端应用的 (应用名, [(role, title, has_sheet)]) 列表;读取失败返回 None。

    依赖「辅助功能」权限,已授权时可靠(不依赖屏幕录制)。
    属性名用字符串形式("AXWindows"/"AXSheet" 等),兼容不同 pyobjc 版本。
    """
    try:
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCreateApplication, AXUIElementCopyAttributeValue)
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        ax = AXUIElementCreateApplication(app.processIdentifier())
        err, windows = AXUIElementCopyAttributeValue(ax, "AXWindows", None)
        if err != 0 or not windows:
            return (app.localizedName() or "", [])
        out = []
        for w in windows:
            _e1, role = AXUIElementCopyAttributeValue(w, "AXRole", None)
            _e2, title = AXUIElementCopyAttributeValue(w, "AXTitle", None)
            _e3, sheet = AXUIElementCopyAttributeValue(w, "AXSheet", None)
            out.append((str(role or ""), str(title or ""), bool(sheet)))
        return (app.localizedName() or "", out)
    except Exception:
        return None


def _ax_frontmost_title():
    info = _ax_frontmost_windows()
    if not info:
        return None
    for _role, title, _s in info[1]:
        if title:
            return title
    return None


def edge_windows_cg() -> list[tuple[int, int, int, int]] | None:
    """返回 Edge 名下所有普通窗口的 (x, y, w, h) 列表。

    基于 CGWindowList(无需屏幕录制权限)。实测:Edge 的保存面板会作为一个
    独立小窗口(约 500x466)出现在该列表里,而 AX 辅助功能树里看不到它。
    失败返回 None。
    """
    try:
        import Quartz
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        out = []
        for w in (Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []):
            if w.get(Quartz.kCGWindowOwnerName) == "Microsoft Edge" \
                    and w.get(Quartz.kCGWindowLayer, 0) == 0:
                b = w.get(Quartz.kCGWindowBounds) or {}

                def g(k):
                    v = b.get(k)
                    if v is None:
                        try:
                            v = getattr(b, k)
                        except Exception:
                            v = 0
                    return int(v or 0)

                out.append((g("X"), g("Y"), g("Width"), g("Height")))
        return out
    except Exception:
        return None


def save_panel_open(baseline=None) -> bool | None:
    """判断 Edge 是否弹出了「保存类」面板。

    信号1(辅助功能 API):最前端应用出现 AXSheet 或标题含「保存/另存/Save」。
    信号2(CGWindow,实测可靠):⌘S 后 Edge 名下出现新的小窗口(保存面板实测约 364x247),
         baseline 为 ⌘S 前记录的 Edge 窗口列表;无 baseline 时按尺寸启发式判断。
    返回 True/False;两个信号都不可用时返回 None。
    """
    info = _ax_frontmost_windows()
    if info is not None:
        for role, title, has_sheet in info[1]:
            if role == "AXSheet" or has_sheet:
                return True
            if any(k in title for k in ("保存", "另存", "存为", "Save")):
                return True
    wins = edge_windows_cg()
    if wins is None:
        return None
    if baseline is not None:
        new = [w for w in wins if w not in baseline]
    else:
        new = wins
    for _x, _y, wd, ht in new:
        if _is_panel_size(wd, ht):
            return True
    return False


def leftover_panel_windows() -> list[tuple[int, int, int, int]]:
    """检测 Edge 名下是否有疑似「遗留保存/前往文件夹」面板的小窗口(用于保存前清理)。

    判定:窗口尺寸落在面板尺寸区间,且不是主窗口(主窗口一般 >800x600)。
    返回这些小窗口的 (x, y, w, h) 列表;检测失败返回空列表。
    """
    wins = edge_windows_cg()
    if not wins:
        return []
    return [w for w in wins if _is_panel_size(w[2], w[3])]


def _is_panel_size(wd: int, ht: int) -> bool:
    """保存面板/前往文件夹面板的尺寸区间(实测保存面板约 364x247)。"""
    return 200 <= wd <= 1200 and 150 <= ht <= 700
