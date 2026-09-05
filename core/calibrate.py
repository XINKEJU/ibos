# -*- coding: utf-8 -*-
"""标定模式:全屏半透明十字线,按提示依次点击 姓名输入框/查询按钮/详情按钮/(可选)返回按钮/(可选)结果区域。

标定得到的坐标即 pyautogui 全局坐标,与后续自动点击使用同一坐标系。
"""
import datetime
import os

import pyautogui
import tkinter as tk

from .config import save as save_cfg
from gui.macbtn import MacButton

STEPS = [
    ("name_input",    "① 请点击「姓名输入框」的中心位置",                       "姓名输入框",   True),
    ("order_input",   "② 请点击「订单号输入框」的中心位置(在姓名框上方;仅按订单号查询时用到,Enter 跳过)", "订单号输入框", False),
    ("query_button",  "③ 请点击「查询」按钮的中心位置",                         "查询按钮",     True),
    ("detail_button", "④ 可选:请点击结果中「详情」按钮中心(仅在开启详情时用到);没有则 Enter 跳过", "详情按钮", False),
    ("back_button",   "⑤ 可选:请点击「返回/关闭详情」按钮中心;没有则按 Enter 跳过", "返回按钮", False),
    ("results_area",  "⑥ 可选:请点击「结果区域」左上角,再点右下角(用于页面变化检测);按 Enter 跳过", "结果区域", False),
]


class Calibrator:
    def __init__(self, cfg: dict, log, on_done=None, parent=None):
        self.cfg = cfg
        self.log = log
        self.on_done = on_done
        self.parent = parent   # 主窗口 root(传入时用 Toplevel,避免多 Tk() 实例)
        self.step = 0
        self.area_click = 0
        self.area_corner = None

    def start(self):
        # 传入 parent 时用 Toplevel(复用主窗口的事件循环);否则独立创建 Tk()
        if self.parent is not None:
            self.root = tk.Toplevel(self.parent)
        else:
            self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.30)
        # 深色半透明遮罩:透过 30% alpha 可看到浏览器内容,同时白色十字线/文字保持高对比可读
        _bg = "#2A2A2E"
        self.root.configure(bg=_bg)
        self.cv = tk.Canvas(self.root, bg=_bg, highlightthickness=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)

        self.txt = tk.Label(self.root, text="", bg=_bg, fg="#ffffff",
                            font=("PingFang SC", 15), wraplength=760, justify="center")
        self.txt.place(relx=0.5, rely=0.04, anchor="n")
        self.pos = tk.Label(self.root, text="", bg=_bg, fg="#aaaaaa",
                            font=("Menlo", 11))
        self.pos.place(relx=0.5, rely=0.11, anchor="n")

        self.skip_btn = MacButton(self.root, "跳过该项 / 完成并保存 (Enter)",
                                  command=self.next_or_finish, kind="primary",
                                  font_size=13, bold=True, height=44, width=320,
                                  parent_bg=_bg)
        self.skip_btn.place(relx=0.5, rely=0.93, anchor="s")

        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Return>", lambda e: self.next_or_finish())
        self.root.bind("<Escape>", lambda e: self.finish())

        self.render()
        # Toplevel 模式下不调用 mainloop(主窗口事件循环已在运行);Tk 模式需 mainloop
        if self.parent is None:
            self.root.mainloop()

    def render(self):
        self.cv.delete("all")
        if self.step < len(STEPS):
            self.txt.config(text=STEPS[self.step][1])
        else:
            self.txt.config(text="标定完成,点击下方按钮保存 (Enter)")
        self.draw_cross(None)

    def draw_cross(self, xy):
        self.cv.delete("cross")
        if xy:
            x, y = xy
            w = self.cv.winfo_width()
            h = self.cv.winfo_height()
            self.cv.create_line(0, y, w, y, fill="#ffcc00", tags="cross")
            self.cv.create_line(x, 0, x, h, fill="#ffcc00", tags="cross")
            self.cv.create_oval(x - 12, y - 12, x + 12, y + 12, outline="#ffcc00",
                                width=2, tags="cross")
            self.pos.config(text=f"当前位置: ({x}, {y})")

    def on_motion(self, e):
        self.draw_cross((e.x_root, e.y_root))

    def on_press(self, e):
        self._press_xy = (e.x_root, e.y_root)

    def on_release(self, e):
        if self.step >= len(STEPS):
            return
        key = STEPS[self.step][0]
        if key == "results_area":
            if self.area_click == 0:
                self.area_corner = (e.x_root, e.y_root)
                self.area_click = 1
                self.log.info(f"结果区域 左上角: {self.area_corner}")
                self.txt.config(text="⑥ 请再点击「结果区域」右下角 (Enter 跳过)")
            else:
                x1, y1 = self.area_corner
                x2, y2 = e.x_root, e.y_root
                area = [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]
                self.cfg["points"]["results_area"] = area
                self.log.info(f"结果区域已标定: {area}")
                self.area_click = 0
                self.area_corner = None
                self.step += 1
        else:
            x, y = e.x_root, e.y_root
            self.cfg["points"][key] = [x, y]
            self.log.info(f"{STEPS[self.step][2]} → ({x}, {y})")
            self.step += 1
        self.render()

    def next_or_finish(self):
        if self.step < len(STEPS) and STEPS[self.step][0] == "results_area":
            self.area_click = 0
            self.area_corner = None
        self.step += 1
        self.render()
        if self.step >= len(STEPS):
            self.finish()

    def finish(self):
        # 记录标定时的 Edge 窗口位置,供运行时检测位移自动补偿坐标
        try:
            from . import mac_perms
            b = mac_perms.edge_window_bounds()
            if b:
                self.cfg["points_calibrated_at_bounds"] = list(b)
        except Exception:
            pass
        save_cfg(self.cfg)
        try:
            shot_dir = os.path.join(self.cfg.get("output_dir", "导出结果"), "标定截图")
            os.makedirs(shot_dir, exist_ok=True)
            img = pyautogui.screenshot()
            from PIL import ImageDraw
            d = ImageDraw.Draw(img)
            marks = {"name_input": "#00ff00", "query_button": "#ff0000",
                     "detail_button": "#00ccff", "back_button": "#ffaa00"}
            for k, col in marks.items():
                pt = self.cfg["points"].get(k) or [0, 0]
                if pt[0]:
                    r = 10
                    d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r],
                              outline=col, width=3)
            fp = os.path.join(shot_dir, "标定参考_" +
                              datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
            img.save(fp)
            self.log.ok(f"标定参考截图: {fp}")
        except Exception as e:
            self.log.warn(f"参考截图失败(不影响标定): {e}")
        self.log.ok("标定完成,坐标已保存到 config.json")
        if self.on_done:
            try:
                self.on_done(self.cfg)
            except Exception:
                pass
        self.root.destroy()
