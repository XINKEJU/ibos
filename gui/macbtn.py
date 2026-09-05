# -*- coding: utf-8 -*-
"""macOS 风格圆角胶囊按钮(tk.Canvas 自绘)。

解决 tk.Button 在 macOS 下的三大问题:
1. 自定义配色不稳定(Aqua 渲染会忽略 bg,导致文字看不清);
2. disabled 状态系统强制灰底、浅字对比度差;
3. 无圆角、无 hover/按下反馈,不像原生控件。

Canvas 自绘:文字用 create_text 纯文本渲染,必定清晰;
样式(bg/fg/hover/pressed/disabled)完全可控,呈 macOS 圆角胶囊观感。
"""
import tkinter as tk

FONT = "SF Pro"

# 样式配色: (底色, 文字色, hover底色, 按下底色, 禁用底, 禁用字)
# 主色经过 WCAG 对比度校验:白字在蓝/红底上 ≥4.5,保证文字清晰可读
_STYLES = {
    "primary": ("#0066D9", "#FFFFFF", "#005AC0", "#004FA9", "#E5EBF4", "#7FA3CC"),
    "danger":  ("#E02A22", "#FFFFFF", "#C9251E", "#B3201A", "#F6E1DF", "#D69A94"),
    "plain":   ("#ECECEF", "#1D1D1F", "#E0E0E5", "#D5D5DB", "#F4F4F6", "#A2A2AA"),
}


def _round_path(x1, y1, x2, y2, r):
    """生成圆角矩形路径点(配合 smooth=True 得到平滑圆角)。"""
    r = max(2, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    return [x1 + r, y1, x2 - r, y1, x2, y1,
            x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2,
            x1, y2 - r, x1, y1 + r, x1, y1]


class MacButton(tk.Canvas):
    """macOS 风格按钮。用法兼容:set_text() / set_state() / config()(text/state)。"""

    def __init__(self, master, text, command=None, kind="plain",
                 font_size=12, bold=False, width=120, height=38,
                 state="normal", parent_bg=None):
        bg = parent_bg if parent_bg is not None else (
            master["bg"] if isinstance(master, tk.Widget) else "#F5F5F7")
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._base, self._fg, self._hover, self._pressed, \
            self._dis_bg, self._dis_fg = _STYLES[kind]
        self._text = text
        self._command = command
        self._fs = font_size
        self._bold = bold
        self._state = state
        self._bg = self._base
        self._inside = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    # ------------------------------------------------------------ 绘制
    def _redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 12 or h < 12:
            return
        self.delete("all")
        if self._state == "disabled":
            bg, fg = self._dis_bg, self._dis_fg
        else:
            bg, fg = self._bg, self._fg
        r = h / 2.0
        pts = _round_path(1.5, 1.5, w - 1.5, h - 1.5, r)
        self.create_polygon(pts, smooth=True, splinesteps=24,
                            fill=bg, outline=bg, tags="bg")
        weight = "bold" if self._bold else "normal"
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg,
                         font=(FONT, self._fs, weight), tags="txt")

    # ------------------------------------------------------------ 事件
    def _on_enter(self, _e):
        if self._state == "normal":
            self._bg = self._hover
            self._redraw()

    def _on_leave(self, _e):
        if self._state == "normal":
            self._bg = self._base
            self._redraw()

    def _on_press(self, _e):
        if self._state == "normal":
            self._bg = self._pressed
            self._redraw()
            self._inside = True

    def _on_release(self, e):
        if self._state == "normal":
            self._bg = self._hover
            self._redraw()
            inside = (0 <= e.x <= self.winfo_width() and
                      0 <= e.y <= self.winfo_height())
            if inside and self._command:
                try:
                    self._command()
                except Exception:
                    pass

    # ------------------------------------------------------------ 接口
    def set_text(self, text):
        self._text = text
        self._redraw()

    def set_state(self, state):
        """state: normal | disabled"""
        self._state = state
        self.configure(cursor="arrow" if state == "disabled" else "hand2")
        self._redraw()

    def get_state(self):
        return self._state

    # 兼容旧代码 .config(state=...)/.config(text=...)
    def config(self, **kw):
        if "state" in kw:
            self.set_state(kw.pop("state"))
        if "text" in kw:
            self.set_text(kw.pop("text"))
        if kw:
            super().config(**kw)

    configure = config
