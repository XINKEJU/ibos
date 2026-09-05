# -*- coding: utf-8 -*-
"""用户介入守卫 —— 长期无人值守运行的安全核心。

设计目标
--------
1. 人一碰鼠标/键盘, 自动化立即让出控制权并暂停, 不与人抢设备
2. 人离开后自动恢复, 无需人工干预
3. 被打断的单个任务会完整重做, 绝不留半截状态

检测原理(三信号融合, 任一触发即判定介入)
-----------------------------------------
S1 鼠标漂移:  自动化每次操作后记录自己留下的鼠标坐标;
              操作前若发现坐标被改动 → 一定是人动的(后台程序不会移动鼠标, 零误报)

S2 事件时间戳: 记录我们自己最后一次操作的系统时间戳 T_self;
              用 CGEventSourceSecondsSinceLastEventType 反推系统最后一次输入事件时间戳 T_last;
              若 T_last > T_self + 容差 → 说明在我们操作之后又有人输入过。
              ⚠️ 关键点: PyAutoGUI 产生的合成事件同样会重置该系统计数器,
                 因此不能直接用"空闲时间"判断, 必须用两个时间戳的差值。

S3 前台应用:  当前前台应用不是目标浏览器 → 人切走了窗口, 此时任何点击都会打到别的 App

暂停与恢复
----------
- 暂停: 记录状态 → 日志 + 系统通知 + GUI 回调 → 进入等待循环(响应外部停止)
- 恢复: 连续 resume_idle_s 秒内无新输入 且 前台为浏览器 → 重新激活窗口并同步基准
- 恢复后抛出 InterventionPause, 由上层完整重做当前任务

降级
----
Quartz / AppKit 不可用时自动关闭对应信号, 仅用剩余信号检测, 绝不因守卫自身异常中断任务。
"""
import time

# ---------------------------------------------------------------- 常量
# CGEvent 类型常量(pyobjc 未导出 kCGEventType*, 使用官方文档数值)
_K_CG_EVENT_MOUSE_MOVED = 5
_K_CG_EVENT_KEY_DOWN = 10

_MOUSE_TOLERANCE_PX = 6        # 鼠标位置容差(像素), 超出视为被移动
_EVENT_TOLERANCE_S = 1.5       # 时间戳容差(秒), 覆盖合成事件的读数延迟
_CONFIRM_DELAY_S = 0.35        # 二次确认间隔, 抑制瞬时误报
_MAX_IDLE_S = 86400.0          # 空闲读数上限, 超过视为无效(系统从未收到事件)


class InterventionPause(Exception):
    """发生过用户介入暂停。抛出后由上层完整重做当前任务(不计入失败)。"""


class InterventionAbort(Exception):
    """暂停等待期间收到外部停止指令。"""


class InterventionGuard:
    def __init__(self, cfg: dict, log, stopped=None, on_state=None):
        self.cfg = cfg
        self.log = log
        self.on_state = on_state
        # 兼容 threading.Event 与可调用对象
        if hasattr(stopped, "is_set"):
            self._stopped = stopped.is_set
        elif stopped is None:
            self._stopped = lambda: False
        else:
            self._stopped = stopped

        self.browser = cfg.get("browser", "Microsoft Edge")
        self.enabled = bool(cfg.get("pause_on_intervention", True))
        self.mode = cfg.get("intervention_check", "auto")   # auto|mouse|frontmost|off
        self.resume_idle_s = float(cfg.get("resume_idle_s", 5.0))
        self.wait_timeout_s = float(cfg.get("intervention_wait_timeout_s", 0))  # 0=无限等待

        self.paused = False
        self.pause_count = 0
        self.total_pause_s = 0.0
        self._self_ts = 0.0          # 我们自己最后一次操作的时间戳
        self._last_mouse = None      # 我们自己最后一次留下的鼠标坐标
        self._manual_pause = False   # GUI「暂停」按钮 / 紧急制动标志

        # 信号可用性探测(任一不可用时静默降级)
        self._idle_fn = self._probe_idle()
        self._frontmost_fn = self._probe_frontmost()

    # ------------------------------------------------------------ 探测
    def _probe_idle(self):
        try:
            from Quartz import (CGEventSourceSecondsSinceLastEventType,
                                kCGEventSourceStateHIDSystemState)
            # 预热一次, 确认真的可用
            v = CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, _K_CG_EVENT_MOUSE_MOVED)
            if v is None:
                return None
            return lambda etype: CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, etype)
        except Exception:
            return None

    def _probe_frontmost(self):
        try:
            from AppKit import NSWorkspace
            ws = NSWorkspace.sharedWorkspace()
            name = ws.frontmostApplication().localizedName()
            if name is None:
                return None
            return lambda: ws.frontmostApplication().localizedName()
        except Exception:
            return None

    # ------------------------------------------------------------ 基准记录
    def mark(self, mouse=None) -> None:
        """记录"这次操作是我们自己做的"。每个原子操作之后调用。

        mouse: 本次操作后鼠标停留的坐标(若有)。缺省时自动取当前位置。
        """
        self._self_ts = time.time()
        if mouse is not None:
            self._last_mouse = tuple(mouse)
        else:
            try:
                import pyautogui
                self._last_mouse = tuple(pyautogui.position())
            except Exception:
                self._last_mouse = None

    def sync(self) -> None:
        """重新同步基准(启动时 / 恢复后调用), 避免把历史输入误判为介入。"""
        self._self_ts = time.time()
        self._last_mouse = None
        try:
            import pyautogui
            self._last_mouse = tuple(pyautogui.position())
        except Exception:
            pass

    # ------------------------------------------------------------ 检测
    def _signal_mouse(self):
        """S1 鼠标漂移。返回原因字符串或 None。"""
        if self._last_mouse is None:
            return None
        try:
            import pyautogui
            cur = tuple(pyautogui.position())
        except Exception:
            return None
        dx = abs(cur[0] - self._last_mouse[0])
        dy = abs(cur[1] - self._last_mouse[1])
        if dx > _MOUSE_TOLERANCE_PX or dy > _MOUSE_TOLERANCE_PX:
            return f"检测到鼠标被移动({self._last_mouse} → {cur})"
        return None

    def _signal_events(self):
        """S2 事件时间戳反推。返回原因字符串或 None。"""
        if self._idle_fn is None or self._self_ts <= 0:
            return None
        now = time.time()
        try:
            idle_m = self._idle_fn(_K_CG_EVENT_MOUSE_MOVED)
            idle_k = self._idle_fn(_K_CG_EVENT_KEY_DOWN)
        except Exception:
            return None
        if idle_m is None or idle_k is None:
            return None
        # 取两者中较近的事件(更近 = 距今秒数更小)
        idle = min(float(idle_m), float(idle_k))
        if idle < 0 or idle > _MAX_IDLE_S:
            return None                      # 读数异常, 放弃该信号
        last_event_ts = now - idle
        if last_event_ts > self._self_ts + _EVENT_TOLERANCE_S:
            return f"检测到键盘/鼠标输入(距上次输入 {idle:.1f}s, 晚于本工具操作)"
        return None

    def _signal_frontmost(self):
        """S3 前台应用。返回原因字符串或 None。"""
        if self._frontmost_fn is None:
            return None
        try:
            front = self._frontmost_fn()
        except Exception:
            return None
        if not front:
            return None
        if front != self.browser:
            return f"检测到前台应用切换为「{front}」"
        return None

    def _detect(self):
        """按模式融合信号。返回介入原因, 无介入返回 None。"""
        if not self.enabled or self.mode == "off":
            return None
        checks = []
        if self.mode in ("auto", "mouse"):
            checks.append(self._signal_mouse)
        if self.mode in ("auto",):
            checks.append(self._signal_events)
        if self.mode in ("auto", "frontmost"):
            checks.append(self._signal_frontmost)
        for fn in checks:
            try:
                r = fn()
            except Exception:
                continue           # 守卫自身异常绝不中断任务
            if r:
                return r
        return None

    # ------------------------------------------------------------ 主入口
    def check(self) -> None:
        """操作前调用。若检测到用户介入则暂停等待, 恢复后抛出 InterventionPause。

        未检测到介入时直接返回(开销 < 1ms)。
        """
        if not self.enabled or self.mode == "off":
            return
        # 手动暂停(GUI 按钮): 无需二次确认, 立即让出控制权
        if self._manual_pause:
            self._pause("用户手动暂停")
            return
        reason = self._detect()
        if not reason:
            return
        time.sleep(_CONFIRM_DELAY_S)          # 二次确认, 抑制瞬时抖动
        reason2 = self._detect()
        if not reason2:
            return
        self._pause(reason2)

    # ------------------------------------------------------------ 外部控制
    def request_pause(self) -> None:
        """请求暂停(GUI「暂停」按钮)。在下一次操作前生效, 不打断正在进行的原子操作。"""
        self._manual_pause = True

    def clear_pause(self) -> None:
        """解除手动暂停, 下一轮等待循环即恢复。"""
        self._manual_pause = False

    def pause_manual(self, reason: str) -> None:
        """立即暂停(如 PyAutoGUI 紧急制动), 恢复后抛 InterventionPause。"""
        self._pause(reason)

    def _pause(self, reason: str) -> None:
        """进入暂停并等待用户离开。恢复后抛出 InterventionPause。"""
        self.paused = True
        self.pause_count += 1
        t0 = time.time()
        self.log.warn(f"⏸ 检测到有人使用电脑({reason}) → 已暂停所有操作, 等待 {self.resume_idle_s:.0f} 秒无操作后自动继续")
        self._notify("自动化已暂停", f"{reason}。停止操作 {self.resume_idle_s:.0f} 秒后自动继续")
        self._set_state("paused")

        try:
            while True:
                if self._stopped():
                    raise InterventionAbort("暂停期间收到停止指令")
                if self.wait_timeout_s and (time.time() - t0) > self.wait_timeout_s:
                    self.log.warn(f"暂停等待超时({self.wait_timeout_s:.0f}s), 停止本次任务")
                    raise InterventionAbort("暂停等待超时")
                if self._manual_pause:
                    pass       # 手动暂停: 仅等待 GUI 解除, 不要求空闲(用户可能正在点「继续」)
                elif not self._detect() and self._quiet_enough():
                    break      # 自动暂停: 无介入信号 且 输入已静止超过 resume_idle_s
                time.sleep(1.0)
        finally:
            self.paused = False
            self.total_pause_s += time.time() - t0
            self._set_state("running")

        self._resume()

    def _quiet_enough(self) -> bool:
        """系统输入已静止超过 resume_idle_s 秒。"""
        if self._idle_fn is None:
            # 无时间戳信号时, 退化为连续两次采样均无介入信号(由外层循环间隔保证)
            return True
        try:
            idle = min(float(self._idle_fn(_K_CG_EVENT_MOUSE_MOVED) or 0.0),
                       float(self._idle_fn(_K_CG_EVENT_KEY_DOWN) or 0.0))
        except Exception:
            return True
        return idle >= self.resume_idle_s

    def _resume(self) -> None:
        """恢复运行: 重新激活浏览器窗口并同步基准, 然后通知上层重做当前任务。"""
        try:
            from . import mac_perms
            mac_perms.activate_app(self.browser)
        except Exception:
            pass
        time.sleep(0.5)
        self.sync()
        self.log.ok(f"▶ 已恢复运行, 当前任务将完整重做(累计暂停 {self.total_pause_s:.0f}s)")
        self._notify("自动化已恢复", "继续未完成的查询, 当前任务将重新执行")
        raise InterventionPause("用户介入后恢复, 当前任务重做")

    # ------------------------------------------------------------ 辅助
    def _notify(self, title: str, msg: str) -> None:
        try:
            from . import mac_perms
            mac_perms.notify(title, msg)
        except Exception:
            pass

    def _set_state(self, state: str) -> None:
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    def summary(self) -> str:
        """暂停统计, 用于运行报告。"""
        if not self.pause_count:
            return "无中断"
        return f"{self.pause_count} 次, 累计 {self.total_pause_s:.0f} 秒"
