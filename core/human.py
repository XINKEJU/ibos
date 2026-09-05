# -*- coding: utf-8 -*-
"""人类化操作:随机停顿、渐进移动、逐字输入;中文姓名通过剪贴板粘贴(与人工 Ctrl+V 一致,不触发风控)。

所有原子操作均接入介入守卫(guard):
- 操作前 guard.check()  → 检测到有人使用电脑则暂停, 恢复后抛 InterventionPause(上层重做当前任务)
- 操作后 guard.mark()   → 记录"这次输入是我们自己产生的", 作为后续检测的基准
"""
import random
import re
import time

import pyautogui
import pyperclip

_ASCII_NAME = re.compile(r"[A-Za-z0-9_\-. ]+")


class Human:
    def __init__(self, type_interval_s: tuple[float, float] = (0.03, 0.09),
                 action_delay_s: tuple[float, float] = (0.6, 1.6),
                 guard=None):
        self.ti = type_interval_s
        self.ad = action_delay_s
        self.guard = guard

    # ------------------------------------------------------------ 守卫钩子
    def _pre(self) -> None:
        """操作前: 若有人介入则暂停等待, 恢复后抛异常由上层重做。"""
        if self.guard is not None:
            self.guard.check()

    def _post(self, mouse=None) -> None:
        """操作后: 记录基准, 标记本次操作为本工具所为。"""
        if self.guard is not None:
            self.guard.mark(mouse)

    # ------------------------------------------------------------ 基础操作
    def pause(self, lo: float | None = None, hi: float | None = None) -> None:
        """随机停顿。停顿结束后检查一次, 避免停顿期间被人接管后仍继续操作。"""
        time.sleep(random.uniform(
            lo if lo is not None else self.ad[0],
            hi if hi is not None else self.ad[1]))
        self._pre()

    def move_to(self, x: int, y: int) -> None:
        self._pre()
        pyautogui.moveTo(x, y, duration=random.uniform(0.05, 0.14),
                         tween=pyautogui.easeInOutQuad)
        self._post((x, y))

    def move_click(self, x: int, y: int, clicks: int = 1) -> None:
        self._pre()
        pyautogui.moveTo(x, y, duration=random.uniform(0.05, 0.14),
                         tween=pyautogui.easeInOutQuad)
        self.pause(0.02, 0.06)
        pyautogui.click(clicks=clicks)
        self._post((x, y))
        self.pause(0.1, 0.22)

    def type_ascii(self, text: str) -> None:
        # 逐字输入时只在开头检测一次: 单次输入全程 < 1 秒, 结束后立即由下一动作复检,
        # 避免每个字符都做一次系统状态检测(16 位订单号 = 16 次, 明显拖慢速度)
        self._pre()
        for ch in text:
            pyautogui.write(ch, interval=random.uniform(*self.ti))
            self._post()
            if random.random() < 0.01:
                time.sleep(random.uniform(0.1, 0.25))   # 偶发"思考"停顿
        self._post()

    def type_name(self, text: str) -> None:
        # 短 ASCII(英文名/短编号)逐字输入更"真人";中文与长数字(如16位订单号)走剪贴板粘贴,更快
        if _ASCII_NAME.fullmatch(text) and len(text) <= 8:
            self.type_ascii(text)
        else:
            self._pre()
            pyperclip.copy(text)
            self.pause(0.08, 0.15)
            pyautogui.hotkey("command", "v")
            self._post()
            self.pause(0.1, 0.2)

    def clear_and_type(self, x: int, y: int, text: str) -> None:
        """点击输入框 → ⌘A 全选(粘贴直接覆盖选中内容,无需再按 backspace)→ 输入。"""
        self.move_click(x, y)
        self.pause(0.1, 0.18)
        self._pre()
        pyautogui.hotkey("command", "a")
        self._post()
        self.pause(0.04, 0.08)
        self.type_name(text)
