# -*- coding: utf-8 -*-
"""人类化操作:随机停顿、渐进移动、逐字输入;中文姓名通过剪贴板粘贴(与人工 Ctrl+V 一致,不触发风控)。"""
import random
import re
import time

import pyautogui
import pyperclip

_ASCII_NAME = re.compile(r"[A-Za-z0-9_\-. ]+")


class Human:
    def __init__(self, type_interval_s: tuple[float, float] = (0.03, 0.09),
                 action_delay_s: tuple[float, float] = (0.6, 1.6)):
        self.ti = type_interval_s
        self.ad = action_delay_s

    def pause(self, lo: float | None = None, hi: float | None = None) -> None:
        time.sleep(random.uniform(
            lo if lo is not None else self.ad[0],
            hi if hi is not None else self.ad[1]))

    def move_to(self, x: int, y: int) -> None:
        pyautogui.moveTo(x, y, duration=random.uniform(0.05, 0.14),
                         tween=pyautogui.easeInOutQuad)

    def move_click(self, x: int, y: int, clicks: int = 1) -> None:
        self.move_to(x, y)
        self.pause(0.02, 0.06)
        pyautogui.click(clicks=clicks)
        self.pause(0.1, 0.22)

    def type_ascii(self, text: str) -> None:
        for ch in text:
            pyautogui.write(ch, interval=random.uniform(*self.ti))
            if random.random() < 0.01:
                time.sleep(random.uniform(0.1, 0.25))   # 偶发"思考"停顿

    def type_name(self, text: str) -> None:
        # 短 ASCII(英文名/短编号)逐字输入更"真人";中文与长数字(如16位订单号)走剪贴板粘贴,更快
        if _ASCII_NAME.fullmatch(text) and len(text) <= 8:
            self.type_ascii(text)
        else:
            pyperclip.copy(text)
            self.pause(0.08, 0.15)
            pyautogui.hotkey("command", "v")
            self.pause(0.1, 0.2)

    def clear_and_type(self, x: int, y: int, text: str) -> None:
        """点击输入框 → ⌘A 全选(粘贴直接覆盖选中内容,无需再按 backspace)→ 输入。"""
        self.move_click(x, y)
        self.pause(0.1, 0.18)
        pyautogui.hotkey("command", "a")
        self.pause(0.04, 0.08)
        self.type_name(text)
