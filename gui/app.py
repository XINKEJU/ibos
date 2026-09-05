# -*- coding: utf-8 -*-
"""悬浮窗控制台:开始/停止、实时日志、坐标标定、环境检查、名单管理。

自动化在后台线程运行(PyAutoGUI 操作 Edge),主线程保持悬浮窗响应。
运行期间主窗口自动最小化,屏幕左上角显示迷你控制条(可拖动)。
"""
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.config import load as load_cfg, save as save_cfg
from core.logger import Logger
from core.ibos_auto import IbosAutomation
from core.calibrate import Calibrator
from core import mac_perms
from gui.macbtn import MacButton, FONT as BTN_FONT

APP_NAME = "联通IBOS批量查询助手"

# ---------------------------------------------------------------- macOS 原生风格
C_BG        = "#EDEDF0"   # 窗口背景(系统标准灰,浅色模式)
C_CARD      = "#FFFFFF"   # 卡片白
C_BORDER    = "#D5D5DB"   # 分隔线/描边
C_TEXT      = "#1D1D1F"   # 主文本
C_SUB       = "#82828C"   # 次要文本(macOS 标准灰度)
C_ACCENT    = "#007AFF"   # 系统蓝(主按钮/进度条)
C_GREEN     = "#34C759"   # 系统绿(就绪)
C_ORANGE    = "#FF9500"   # 系统橙(运行中)
C_DOT_IDLE  = "#C7C7CC"   # 状态点-待机
C_LOG_BG    = "#1A1A1C"   # 日志区深色(macOS 暗色)
C_LOG_FG    = "#D6D6D8"
# SF Pro = macOS 11+ 系统字体,视觉一致性远优于 PingFang
FONT        = "SF Pro"
FONT_MONO   = "SF Mono"


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.log_q = queue.Queue()
        self.stop_evt = threading.Event()
        self.worker = None
        self.names = []
        self.mini = None
        self.running = False
        log_path = os.path.join(BASE, self.cfg.get("log_file", "运行日志.log"))
        self.logger = Logger(log_path, sink=self.log_q,
                             max_mb=self.cfg.get("log_max_mb", 5))
        self.build_ui()
        self._setup_menubar()
        self.perm_lbl.config(text=self._perm_text(mac_perms.check_all()))
        self.input_status.config(text="未加载,请粘贴查询列表后点「加载并校验」")
        self._log("SYS", f"{APP_NAME} 已启动,工作目录: {BASE}")
        self._startup_selfcheck()
        self.root.after(120, self._poll_queue)

    def _startup_selfcheck(self):
        """启动自检:权限 + Edge 状态 → 状态点颜色与提示。"""
        perms = mac_perms.check_all()
        ok_ax = bool(perms.get("accessibility"))
        ok_sr = bool(perms.get("screen_recording"))
        edge = mac_perms.edge_running()
        if edge is False:
            self._set_dot("#E0A33A")
            self._log("WARN", "Edge 未在运行,请先打开 Edge 并登录 IBOS 查询页")
        elif ok_ax and ok_sr:
            self._set_dot(C_GREEN)
            self._log("OK", "环境就绪:辅助功能+屏幕录制权限已开,Edge 运行中")
        elif ok_ax:
            self._set_dot(C_ORANGE)
            self._log("WARN", "屏幕录制权限未开(截图/画面检测不可用),请点「检查环境」授权")
        else:
            self._set_dot(C_ORANGE)
            self._log("WARN", "辅助功能权限未开,点击/输入将无效,请点「检查环境」处理")

    # ---------------------------------------------------------------- UI
    def _mk_btn(self, parent, text, command, kind="plain",
                font_size=12, bold=False, padx=12, pady=6):
        """统一按钮工厂 → macOS 风格圆角胶囊按钮(Canvas 自绘,文字必定清晰)。
        kind: primary=系统蓝 | danger=系统红 | plain=浅灰。"""
        height = 26 + pady * 2
        return MacButton(parent, text, command, kind=kind,
                         font_size=font_size, bold=bold,
                         height=height, parent_bg=parent["bg"])

    def _card(self, parent):
        """白色卡片容器(带细描边,模拟 macOS 分组卡片)。"""
        f = tk.Frame(parent, bg=C_CARD,
                     highlightbackground=C_BORDER, highlightthickness=1)
        return f

    def build_ui(self):
        r = self.root
        r.title(APP_NAME)
        r.attributes("-topmost", True)
        r.geometry("500x680+60+50")
        r.minsize(440, 580)
        r.configure(bg=C_BG)

        # ---------------- 自定义标题区域(状态点+检查环境,不模拟 window chrome) ----------------
        head = tk.Frame(r, bg=C_BG)
        head.pack(fill="x", padx=14, pady=(10, 2))
        self.dot = tk.Canvas(head, width=12, height=12, bg=C_BG, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_id = self.dot.create_oval(2, 2, 10, 10, fill=C_DOT_IDLE, outline="")
        tk.Label(head, text=APP_NAME, bg=C_BG, fg=C_TEXT,
                 font=(FONT, 13, "bold")).pack(side="left", padx=8)
        self.env_btn = self._mk_btn(head, "检查环境", self.check_env,
                                    kind="plain", font_size=11, padx=10, pady=3)
        self.env_btn.pack(side="right")

        # ---------------- 主体 ----------------
        body = tk.Frame(r, bg=C_BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        # ---- 状态卡(权限 / 查询方式) ----
        card = self._card(body)
        card.pack(fill="x", pady=(0, 8))
        self.perm_lbl = tk.Label(card, text="权限: 待检查(点右上「检查环境」)",
                                 bg=C_CARD, fg=C_SUB, font=(FONT, 11), anchor="w")
        self.perm_lbl.pack(fill="x", padx=12, pady=(8, 3))
        nf = tk.Frame(card, bg=C_CARD)
        nf.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(nf, text="查询方式", bg=C_CARD, fg=C_SUB,
                 font=(FONT, 10)).pack(side="left")
        self.qmode = ttk.Combobox(nf, state="readonly", width=9,
                                  font=(FONT, 11), values=["按姓名", "按订单号", "按订购号码"])

        init_mode = self.cfg.get("query_by", "name")
        if init_mode == "order_phone":
            self.qmode.set("按订购号码")
        elif init_mode == "order":
            self.qmode.set("按订单号")
        else:
            self.qmode.set("按姓名")
        self.qmode.bind("<<ComboboxSelected>>", self._on_qmode)
        self.qmode.pack(side="left", padx=(8, 0))

        # ---- 输入卡片(批量粘贴查询列表,替代单独的文件选择) ----
        input_card = self._card(body)
        input_card.pack(fill="x", pady=(0, 8))
        tk.Label(input_card, text="查询列表（每行一条,支持批量粘贴）",
                 bg=C_CARD, fg=C_SUB, font=(FONT, 10)).pack(anchor="w", padx=12, pady=(6, 0))
        # 输入区域
        input_frame = tk.Frame(input_card, bg=C_CARD)
        input_frame.pack(fill="x", padx=12, pady=(2, 4))
        self.input_area = scrolledtext.ScrolledText(
            input_frame, height=4, font=(FONT_MONO, 11),
            bg="#FAFAFB", fg=C_TEXT, relief="solid", bd=1,
            highlightthickness=0, wrap="none", padx=6, pady=4)
        self.input_area.pack(fill="x")
        # 底栏: 状态 + 加载按钮 + 清空 + 选文件
        bar = tk.Frame(input_card, bg=C_CARD)
        bar.pack(fill="x", padx=12, pady=(0, 6))
        self.input_status = tk.Label(bar, text="未加载", bg=C_CARD, fg=C_SUB,
                                     font=(FONT, 10))
        self.input_status.pack(side="left")
        self.load_btn = self._mk_btn(bar, "加载并校验", self._load_input,
                                     kind="primary", font_size=11, padx=10, pady=3)
        self.load_btn.pack(side="right", padx=(4, 0))
        self.clear_btn = self._mk_btn(bar, "清空", self._clear_input,
                                      kind="plain", font_size=11, padx=8, pady=3)
        self.clear_btn.pack(side="right", padx=4)
        self.pick_btn = self._mk_btn(bar, "从文件…", self.pick_names,
                                     kind="plain", font_size=10, padx=6, pady=3)
        self.pick_btn.pack(side="right", padx=4)

        # ---- 主操作(开始/停止) ----
        btns = tk.Frame(body, bg=C_BG)
        btns.pack(fill="x", pady=(0, 8))
        self.start_btn = self._mk_btn(btns, "开始查询", self.start_run,
                                      kind="primary", font_size=13, bold=True, pady=9)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.stop_btn = self._mk_btn(btns, "停止", self.request_stop,
                                     kind="danger", font_size=13, bold=True, pady=9)
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(5, 0))
        self.stop_btn.config(state="disabled")

        # ---- 辅助操作(等宽三按钮) ----
        btns2 = tk.Frame(body, bg=C_BG)
        btns2.pack(fill="x", pady=(0, 10))
        self.calib_btn = self._mk_btn(btns2, "标定坐标", self.start_calibrate,
                                      kind="plain", font_size=11, pady=5)
        self.open_btn = self._mk_btn(btns2, "导出目录", self.open_outdir,
                                     kind="plain", font_size=11, pady=5)
        self.excel_btn = self._mk_btn(btns2, "导出 Excel", self.export_excel,
                                      kind="plain", font_size=11, pady=5)
        for i, b in enumerate((self.calib_btn, self.open_btn, self.excel_btn)):
            pad = (0, 4) if i == 0 else (4, 0) if i == 2 else (4, 4)
            b.pack(side="left", expand=True, fill="x", padx=pad)

        # ---- 进度 ----
        prog = tk.Frame(body, bg=C_BG)
        prog.pack(fill="x", pady=(0, 10))
        self.prog_lbl = tk.Label(prog, text="进度: 未开始", bg=C_BG, fg=C_TEXT,
                                 font=(FONT, 11), anchor="w")
        self.prog_lbl.pack(fill="x")
        style = ttk.Style()
        try:
            style.theme_use("aqua")
        except Exception:
            pass
        try:
            style.configure("Mac.Horizontal.TProgressbar",
                            troughcolor="#E5E5EA", background=C_ACCENT, thickness=8)
            self.bar = ttk.Progressbar(prog, maximum=100, style="Mac.Horizontal.TProgressbar")
        except Exception:
            self.bar = ttk.Progressbar(prog, maximum=100)
        self.bar.pack(fill="x", pady=(5, 0))

        # ---- 日志区(卡片 + 深色终端) ----
        logf = self._card(body)
        logf.pack(fill="both", expand=True)
        tk.Label(logf, text="运行日志", bg=C_CARD, fg=C_SUB,
                 font=(FONT, 10)).pack(anchor="w", padx=12, pady=(6, 2))
        self.log_txt = scrolledtext.ScrolledText(
            logf, bg=C_LOG_BG, fg=C_LOG_FG, font=(FONT_MONO, 11),
            relief="flat", bd=0, highlightthickness=0,
            state="disabled", wrap="word", padx=8, pady=6,
            insertbackground=C_LOG_FG)
        self.log_txt.pack(fill="both", expand=True, padx=6, pady=(2, 8))
        for tag, col in (("SYS", "#98989E"), ("INFO", C_LOG_FG), ("OK", "#32D74B"),
                         ("WARN", "#FFD60A"), ("ERROR", "#FF453A"), ("PROGRESS", "#5AC8FA")):
            self.log_txt.tag_configure(tag, foreground=col)

    def _set_dot(self, color):
        """状态点:外圈细描边 + 内圆状态色。"""
        try:
            self.dot.itemconfig(self.dot_id, fill=color)
        except Exception:
            pass

    # ---------------------------------------------------------------- 日志
    def _log(self, level, msg):
        method = getattr(self.logger, level.lower(), None)
        if method is None:
            method = self.logger.info
        method(msg)

    def _poll_queue(self):
        try:
            while True:
                level, line = self.log_q.get_nowait()
                self.log_txt.configure(state="normal")
                self.log_txt.insert("end", line + "\n", level)
                self.log_txt.configure(state="disabled")
                self.log_txt.see("end")
                if level == "PROGRESS":
                    tail = line.split("] ", 1)[-1] if "] " in line else line
                    self.prog_lbl.config(text="进度: " + tail)
                    m = re.search(r"(\d+)\s*/\s*(\d+)", line)
                    if m:
                        done, total = int(m.group(1)), int(m.group(2))
                        self.bar["value"] = done / max(total, 1) * 100
                        if self.mini:
                            self.mini_prog.config(text=f"{done} / {total}")
            # 同步用户介入守卫状态到迷你条(有人接手→显示已暂停)
            auto = getattr(self, "_last_auto", None)
            if self.mini and auto is not None and getattr(auto, "guard", None):
                gs = getattr(auto, "guard_state", "running")
                if gs == "paused":
                    self.mini_lbl.config(text="⏸ 已暂停(等待人工)")
                    self.mini_pause_btn.config(text="继续")
                else:
                    self.mini_lbl.config(text="查询运行中…")
                    self.mini_pause_btn.config(text="暂停")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    # ---------------------------------------------------------------- 名单
    def load_names(self):
        path = self.cfg.get("names_file", "名单.txt")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8-sig") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]

    def _qmode_name(self):
        q = self.qmode.get()
        if q == "按订单号":
            return "order"
        if q == "按订购号码":
            return "order_phone"
        return "name"

    def _on_qmode(self, _e=None):
        self.cfg["query_by"] = self._qmode_name()
        save_cfg(self.cfg)
        mode = self._qmode_name()
        label_map = {"order": "按订单号", "order_phone": "按订购号码", "name": "按姓名"}
        self._log("OK", f"查询方式已切换为: {label_map.get(mode, mode)}")
        # 如已加载列表,提示重新校验
        if self.names:
            self._log("INFO", "查询方式已变更,建议点「加载并校验」重新检查格式")
        self._refresh_names_info()
        if mode == "order":
            pt = self.cfg["points"].get("order_input") or [0, 0]
            if not pt[0]:
                self._log("WARN", "订单号输入框未标定:暂按姓名框上方自动推算,建议「标定坐标」时在②步点一下订单号输入框精调")
        elif mode == "order_phone":
            pt = self.cfg["points"].get("order_phone_input") or [0, 0]
            if not pt[0]:
                self._log("WARN", "订购号码输入框未标定,请先在 config.json 中设置 order_phone_input 坐标")

    def _refresh_names_info(self):
        """刷新输入区域统计(已加载的条数),不改变输入框内容。"""
        cnt = len(self.names)
        mode = self._qmode_name()
        unit_map = {"order_phone": "个号码", "order": "条", "name": "人"}
        if cnt:
            self.input_status.config(text=f"已加载 {cnt} {unit_map.get(mode, '条')}")
        else:
            self.input_status.config(text="未加载,请在输入框中粘贴内容后点「加载并校验」")

    def _load_input(self):
        """解析输入区内容,校验合规性,更新 self.names。"""
        raw = self.input_area.get("1.0", "end-1c")
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip() and not ln.strip().startswith("#")]
        total_raw = len(lines)
        if total_raw == 0:
            messagebox.showwarning(APP_NAME, "请输入查询列表,每行一条。\n\n可从 Excel/网页 直接复制粘贴,自动过滤空行与 # 注释。")
            return
        # 去重(保序)
        deduped = list(dict.fromkeys(lines))
        dup_count = total_raw - len(deduped)
        self.names = deduped
        # 模式合规校验
        mode = self._qmode_name()
        self._validate_input(mode)
        # 自动写入临时文件(供 run_all 使用)
        self._save_names_to_file()
        self._refresh_names_info()
        self._log("OK", f"已加载 {len(deduped)} 条 (去重 {dup_count} 条)"
                  + (f", 含不合规条目将在开始前提示" if hasattr(self, "_bad_inputs") and self._bad_inputs else ""))

    def _validate_input(self, mode):
        """校验输入合法性与格式警告(不阻断,日志提示)"""
        warnings = []
        self._bad_inputs = []
        if mode == "order":
            for i, item in enumerate(self.names, 1):
                if not re.fullmatch(r"[0-9]+", item):
                    self._bad_inputs.append(item)
                    warnings.append(f"第{i}行「{item[:20]}」非纯数字(订单号应为纯数字)")
        elif mode == "order_phone":
            for i, item in enumerate(self.names, 1):
                if not re.fullmatch(r"1[0-9]{10}", item):
                    self._bad_inputs.append(item)
                    warnings.append(f"第{i}行「{item[:20]}」不是 11 位手机号")
        # 姓名模式宽松:只提示空
        non_empty = [n for n in self.names if n]
        if len(non_empty) != len(self.names):
            warnings.append(f"检测到 {len(self.names) - len(non_empty)} 个空值(已自动过滤)")
        for w in warnings[:5]:
            self._log("WARN", w)
        if len(warnings) > 5:
            self._log("WARN", f"…共 {len(warnings)} 条不合规,详情见日志")

    def _save_names_to_file(self):
        """将当前名单写入临时文件(供自动化模块读取,兼容原有文件加载逻辑)"""
        path = self.cfg.get("names_file", os.path.join(BASE, "临时名单.txt"))
        try:
            with open(path, "w", encoding="utf-8") as f:
                for n in self.names:
                    f.write(n + "\n")
            self.cfg["names_file"] = path
        except Exception as e:
            self._log("WARN", f"写入临时名单文件失败: {e}")

    def _clear_input(self):
        self.input_area.delete("1.0", "end")
        self.names = []
        self._refresh_names_info()
        self._log("INFO", "已清空输入区")

    def pick_names(self):
        title = "选择名单文件(每行一条)"
        p = filedialog.askopenfilename(title=title,
                                       filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if p:
            try:
                with open(p, encoding="utf-8-sig") as f:
                    content = f.read()
                self.input_area.delete("1.0", "end")
                self.input_area.insert("1.0", content)
                self._load_input()
                self._log("OK", f"已从文件加载: {os.path.basename(p)}")
            except Exception as e:
                self._log("ERROR", f"读取文件失败: {e}")

    # ------------------------------------------------------------ 环境检查
    def _perm_text(self, perms):
        sr, ax = perms.get("screen_recording"), perms.get("accessibility")
        return (f"权限: 屏幕录制 {'已开' if sr else '未开'}   "
                f"辅助功能 {'已开' if ax else '未开'}")

    def check_env(self):
        self._set_dot(C_DOT_IDLE)
        perms = mac_perms.check_all()
        self.perm_lbl.config(text=self._perm_text(perms))
        ax = perms.get("accessibility")
        sr = perms.get("screen_recording")
        self._log("INFO", f"辅助功能权限: {ax}  |  屏幕录制权限: {sr}")
        missing = []
        if not ax:
            missing.append("辅助功能 (模拟点击/键盘输入)")
        if not sr:
            missing.append("屏幕录制 (截图与画面检测)")
        if missing:
            self._log("WARN", f"缺少权限: {', '.join(missing)}")
            msg = "缺少以下必要权限:\n\n• " + "\n• ".join(missing)
            msg += "\n\n是否现在打开系统设置授权?"
            if messagebox.askyesno(APP_NAME, msg):
                mac_perms.open_privacy_pane(accessibility=not ax)
                self._log("INFO", "已打开系统权限设置页")
        bounds = mac_perms.edge_window_bounds()
        if bounds:
            self._log("INFO", f"Edge 窗口: x={bounds[0]} y={bounds[1]} 宽={bounds[2]} 高={bounds[3]}")
            base = self.cfg.get("points_calibrated_at_bounds") or []
            if len(base) == 4 and (bounds[0] != base[0] or bounds[1] != base[1]):
                self._log("WARN", f"⚠ Edge 窗口位置与标定时不同(Δx={bounds[0] - base[0]} Δy={bounds[1] - base[1]}),"
                                  f"运行时将自动平移坐标,可继续使用")
            self._set_dot(C_GREEN)
        else:
            self._log("WARN", "未检测到 Edge 窗口,请先打开并登录 IBOS 查询页")
        try:
            leftover = mac_perms.leftover_panel_windows()
            if leftover:
                self._log("WARN", f"检测到疑似遗留的保存面板窗口 {len(leftover)} 个,建议重启 Edge 或手动关闭")
        except Exception:
            pass
        self._log("INFO", "环境检查完成")

    # -------------------------------------------------------------- 标定
    def start_calibrate(self):
        if self.running:
            messagebox.showwarning(APP_NAME, "运行中无法标定,请先停止")
            return
        msg = ("标定说明:\n\n"
               "1. 请把 Edge 窗口放到【主显示器】并打开 IBOS 查询页,窗口位置保持不动;\n"
               "2. 进入全屏标定后,按提示依次点击:姓名输入框 → 订单号输入框(可选) → 查询按钮;\n"
               "3. ②订单号输入框仅「按订单号查询」时用到,可按 Enter 跳过(会自动按姓名框上方推算);\n"
               "4. ④详情按钮、⑤返回按钮、⑥结果区域 均为可选,按 Enter 跳过;\n"
               "5. 标定完成自动保存到 config.json,并生成参考截图。\n\n"
               "提示:屏幕录制权限未开启时无法生成参考截图,但不影响坐标标定。")
        if not messagebox.askokcancel(APP_NAME, msg):
            return
        self.root.iconify()
        try:
            cal = Calibrator(self.cfg, self.logger, parent=self.root)
            cal.on_done = lambda c: (self.root.deiconify(), self._show_points())
            cal.start()
        except Exception as e:
            self._log("ERROR", f"标定异常: {e}")
            self.root.deiconify()

    def _show_points(self):
        p = self.cfg["points"]
        order = p.get("order_input") or [0, 0]
        order_txt = f"订单号({order[0]},{order[1]})" if order[0] else "订单号(未标定,自动推算)"
        self._log("OK", f"标定坐标: 姓名输入({p['name_input'][0]},{p['name_input'][1]}) "
                        f"查询({p['query_button'][0]},{p['query_button'][1]}) "
                        f"{order_txt}")

    # ------------------------------------------------------------ 开始/停止
    def start_run(self):
        if self.running:
            return
        if not self.names:
            self._load_input()   # 自动加载输入区内容
            if not self.names:
                messagebox.showwarning(APP_NAME, "请先在查询列表中粘贴内容,然后点「加载并校验」")
                return
        # 不合规条目提示(不阻断)
        if getattr(self, "_bad_inputs", None):
            msg = (f"检测到 {len(self._bad_inputs)} 条不符当前查询方式的条目 "
                   f"(如 {self._bad_inputs[:3]}),将跳过这些行。是否继续?")
            if not messagebox.askyesno(APP_NAME, msg):
                return
        pts = self.cfg["points"]
        # 必要标定点: 姓名框(订单号模式未标定时由姓名框推算) + 查询按钮
        need = ["name_input", "query_button"]
        if self.cfg.get("enable_detail", True):
            need.append("detail_button")
        if any(not pts[k][0] for k in need):
            if messagebox.askyesno(APP_NAME, "尚未标定坐标,现在开始标定吗?"):
                self.start_calibrate()
                pts = self.cfg["points"]
                if any(not pts[k][0] for k in need):
                    return
            else:
                return

        perms = mac_perms.check_all()
        if not perms.get("accessibility"):
            messagebox.showwarning(
                APP_NAME,
                "辅助功能权限未开启,模拟点击/输入将无效!\n\n"
                "请到 系统设置 → 隐私与安全性 → 辅助功能,\n"
                "为「终端」(或启动本工具的应用)开启权限后重启本工具。")

        start_idx = 0
        if self.cfg.get("resume_enabled", True):
            prog = IbosAutomation.read_progress(
                os.path.join(BASE, self.cfg.get("progress_file", "进度.txt")))
            idx = prog["index"]
            if 0 < idx < len(self.names):
                last_name = prog.get("name") or self.names[idx - 1]
                msg = (f"检测到上次进度: 已完成 {idx} 人"
                       + (f"(最后完成: {last_name})" if last_name else "")
                       + f"。\n是否从第 {idx + 1} 人继续?")
                if messagebox.askyesno(APP_NAME, msg):
                    start_idx = idx

        self.running = True
        self.stop_evt.clear()
        self.start_btn.config(state="disabled")
        self.calib_btn.config(state="disabled")
        self.pick_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.excel_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._set_dot(C_ORANGE)
        self._log("INFO", f"开始批量查询:共 {len(self.names)} 人,从第 {start_idx + 1} 人开始")
        self._show_mini(len(self.names))

        def job():
            try:
                auto = IbosAutomation(self.cfg, self.logger, self.stop_evt)
                self._last_auto = auto
                auto.run_all(self.names, start_idx)
            except Exception as e:
                self._log("ERROR", f"运行异常: {e}")
            finally:
                try:
                    self.root.after(0, self._on_run_done)
                except Exception:
                    pass

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def request_stop(self):
        if self.running:
            self.stop_evt.set()
            self._log("WARN", "停止指令已发送(处理完当前姓名后停止)")
            if self.mini:
                self.mini_btn.config(state="disabled")
                if hasattr(self, "mini_pause_btn"):
                    self.mini_pause_btn.config(state="disabled")

    def _toggle_pause(self):
        """迷你条「暂停 / 继续」按钮:手动请求守卫暂停或解除暂停。

        暂停生效于下一次原子操作前(不强行打断正在进行的保存/输入),安全且不抢设备。
        """
        auto = getattr(self, "_last_auto", None)
        guard = getattr(auto, "guard", None) if auto else None
        if guard is None:
            return
        if guard.paused or guard._manual_pause:
            guard.clear_pause()
            auto.guard_state = "running"
            self._log("INFO", "已解除暂停,下一操作前恢复运行")
        else:
            guard.request_pause()
            self._log("INFO", "已发送暂停指令,下一操作前生效(有人接手时也会自动暂停)")

    def _on_run_done(self):
        self.running = False
        self.start_btn.config(state="normal")
        self.calib_btn.config(state="normal")
        self.pick_btn.config(state="normal")
        self.open_btn.config(state="normal")
        self.excel_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._set_dot(C_GREEN)
        self.bar["value"] = 0
        try:
            self.root.deiconify()
        except Exception:
            pass
        self._destroy_mini()
        self._log("SYS", "运行结束,悬浮窗已还原")
        # 后台导出可能仍在进行,延迟检查状态并提示用户
        self._check_export_status()

    def _check_export_status(self, _count=0):
        """轮询检查后台导出状态,完成后给出反馈(最多等 60 秒)。"""
        auto = getattr(self, "_last_auto", None)
        if auto is None:
            return
        status = getattr(auto, "_export_status", "pending")
        if status == "ok":
            self._log("OK", "Excel 与查询报告已自动生成完毕")
        elif status == "failed":
            self._log("WARN", "Excel 自动导出失败,可点「导出 Excel」手动重试")
        elif _count < 60:
            # 仍在后台导出中,1 秒后再查
            self.root.after(1000, lambda: self._check_export_status(_count + 1))
        else:
            self._log("WARN", "Excel 自动导出超时,可点「导出 Excel」手动检查")

    # ----------------------------------------------------------- 迷你控制条
    def _show_mini(self, total):
        self._destroy_mini()
        self.mini = tk.Toplevel(self.root)
        self.mini.overrideredirect(True)
        self.mini.attributes("-topmost", True)
        # 基于主屏尺寸计算位置(左上角偏移),多显示器时仍落在主屏
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            mx = min(8, sw - 260)
            my = min(40, sh - 120)
        except Exception:
            mx, my = 8, 40
        self.mini.geometry(f"252x128+{mx}+{my}")
        mf = tk.Frame(self.mini, bg=C_CARD,
                      highlightbackground=C_BORDER, highlightthickness=1)
        mf.pack(fill="both", expand=True)
        self.mini_lbl = tk.Label(mf, text="查询运行中…", bg=C_CARD, fg=C_TEXT,
                                 font=(FONT, 12, "bold"))
        self.mini_lbl.pack(pady=(8, 1))
        self.mini_prog = tk.Label(mf, text=f"进度 0 / {total}", bg=C_CARD,
                                  fg=C_ACCENT, font=(FONT_MONO, 10))
        self.mini_prog.pack()
        # 按钮行:暂停/继续 + 停止
        mb = tk.Frame(mf, bg=C_CARD)
        mb.pack(pady=(6, 8))
        self.mini_pause_btn = self._mk_btn(mb, "暂停", self._toggle_pause,
                                           kind="plain", font_size=11, padx=14, pady=3)
        self.mini_pause_btn.pack(side="left", padx=(0, 6))
        self.mini_btn = self._mk_btn(mb, "停止", self.request_stop,
                                     kind="danger", font_size=11, padx=14, pady=3)
        self.mini_btn.pack(side="left")
        # 支持拖动
        self.mini.bind("<ButtonPress-1>", self._drag_start)
        self.mini.bind("<B1-Motion>", self._drag_move)
        self.root.iconify()

    def _drag_start(self, e):
        self._drag_off = (e.x_root - self.mini.winfo_x(), e.y_root - self.mini.winfo_y())

    def _drag_move(self, e):
        self.mini.geometry(f"+{e.x_root - self._drag_off[0]}+{e.y_root - self._drag_off[1]}")

    def _destroy_mini(self):
        if self.mini:
            try:
                self.mini.destroy()
            except Exception:
                pass
            self.mini = None

    # ---------------------------------------------------------------- 其他
    def open_outdir(self):
        d = os.path.abspath(self.cfg.get("output_dir", "导出结果"))
        os.makedirs(d, exist_ok=True)
        subprocess.run(["open", d], check=False)

    def export_excel(self):
        """解析 导出结果 下的查询结果网页(MHTML)并生成 Excel。"""
        # 未加载名单时尝试从输入区自动加载(order/order_phone 模式需要名单顺序)
        if not self.names:
            self._load_input()

        def job():
            try:
                from core.exporter import export_to_excel, find_result_files
                out_dir = os.path.join(BASE, self.cfg.get("output_dir", "导出结果"))
                files = find_result_files(out_dir)
                if not files:
                    self._log("WARN", "未找到 导出结果 下的查询结果网页文件,请先运行查询")
                    return
                out = os.path.join(out_dir, "查询结果汇总.xlsx")
                self._log("INFO", f"开始解析 {len(files)} 个查询结果文件…")
                res = export_to_excel(files, out, self.logger,
                                      query_by=self._qmode_name(),
                                      query_order=list(self.names))
                if res["orders"] == 0:
                    self._log("WARN", "解析完成,但未提取到订单数据(可能查询都无结果)")
                else:
                    self._log("OK", f"导出完成:{res['files_ok']}/{len(files)} 个文件,"
                                     f"共 {res['orders']} 个订单 → {out}")
                    subprocess.run(["open", out], check=False)
            except Exception as e:
                self._log("ERROR", f"导出失败: {e}")
        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------ 菜单栏
    def _setup_menubar(self):
        """macOS 原生菜单栏(应用菜单 + 编辑菜单 + 帮助)。"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # ---- 应用菜单(Apple 菜单) ----
        app_menu = tk.Menu(menubar, name="apple")
        menubar.add_cascade(menu=app_menu, label="IBOS查询助手")
        app_menu.add_command(label="关于 IBOS查询助手", command=self._show_about)
        app_menu.add_separator()
        app_menu.add_command(label="检查环境", command=self.check_env)
        app_menu.add_command(label="导出目录", command=self.open_outdir)
        app_menu.add_separator()
        app_menu.add_command(label="退出 IBOS查询助手", command=self._app_quit)

        # ---- 编辑菜单 ----
        edit_menu = tk.Menu(menubar, name="edit")
        menubar.add_cascade(menu=edit_menu, label="编辑")
        edit_menu.add_command(label="复制", accelerator="⌘C",
                              command=lambda: self.root.focus_get().event_generate("<<Copy>>") if self.root.focus_get() else None)
        edit_menu.add_command(label="粘贴", accelerator="⌘V",
                              command=lambda: self.root.focus_get().event_generate("<<Paste>>") if self.root.focus_get() else None)
        edit_menu.add_command(label="全选", accelerator="⌘A",
                              command=lambda: self.root.focus_get().event_generate("<<SelectAll>>") if self.root.focus_get() else None)

        # ---- 窗口菜单(标准 macOS 行为) ----
        win_menu = tk.Menu(menubar, name="window")
        menubar.add_cascade(menu=win_menu, label="窗口")
        win_menu.add_command(label="置顶", command=self._toggle_topmost)
        win_menu.add_command(label="最小化", accelerator="⌘M",
                              command=lambda: self.root.iconify())

        # ---- 帮助菜单 ----
        help_menu = tk.Menu(menubar, name="help")
        menubar.add_cascade(menu=help_menu, label="帮助")
        help_menu.add_command(label="使用说明", command=self._show_help)

    def _show_about(self):
        from tkinter import messagebox
        messagebox.showinfo("关于 IBOS查询助手",
                            "联通 IBOS 批量查询助手\n\n"
                            "纯外部操作 PyAutoGUI 自动化方案\n"
                            "不注入页面、不劫持接口,绕过 IBOS 风控\n\n"
                            "版本 1.0")

    def _show_help(self):
        path = os.path.join(BASE, "使用说明.txt")
        if os.path.exists(path):
            subprocess.run(["open", path], check=False)
        else:
            from tkinter import messagebox
            messagebox.showinfo("使用说明",
                                "1. 粘贴查询列表(每行一条)→ 加载并校验\n"
                                "2. 选择查询方式 → 开始查询\n"
                                "3. 查询结束自动导出 Excel + 报告")

    def _toggle_topmost(self):
        current = self.root.attributes("-topmost")
        self.root.attributes("-topmost", not current)

    def _app_quit(self):
        if self.running:
            if not messagebox.askyesno(APP_NAME, "查询正在运行中,确定退出吗?"):
                return
            self.stop_evt.set()
        self.root.destroy()


def _apply_app_icon(root):
    """注入应用图标: Dock(PyObjC) + 窗口标题栏(tkinter iconphoto)。

    macOS 上 tk 窗口默认无图标;Dock 图标需经 NSApplication 设置。
    任一环节失败(缺 Pillow/PyObjC)仅跳过,不影响启动。
    """
    icon_png = os.path.join(BASE, "assets", "AppIcon.png")
    if not os.path.exists(icon_png):
        return
    # --- 窗口标题栏/程序坞弹跳图标 ---
    try:
        from PIL import Image, ImageTk
        img = Image.open(icon_png).resize((128, 128), Image.LANCZOS)
        root._icon_photo = ImageTk.PhotoImage(img)  # 持引用防 GC
        root.iconphoto(True, root._icon_photo)
    except Exception:
        pass
    # --- Dock 图标 (AppKit) ---
    try:
        from AppKit import NSApplication, NSImage
        nsimg = NSImage.alloc().initWithContentsOfFile_(icon_png)
        if nsimg:
            NSApplication.sharedApplication().setApplicationIconImage_(nsimg)
    except Exception:
        pass


def main():
    cfg_path = os.path.join(BASE, "config.json")
    cfg = load_cfg(cfg_path)
    root = tk.Tk()
    _apply_app_icon(root)
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
