# -*- coding: utf-8 -*-
"""IBOS 批量查询主流程。

纯外部操作:通过 PyAutoGUI 在操作系统层面模拟鼠标点击与键盘输入,
不向页面注入任何脚本、不改写任何接口,因此不会触发 IBOS 风控。
"""
import datetime
import glob
import json
import os
import re
import shutil
import threading
import time

import pyautogui
import pyperclip

from .human import Human
from .guard import InterventionGuard, InterventionPause, InterventionAbort
from . import mac_perms

_DIALOG_KEYS = ("保存", "另存", "Save", "saved")
_DIGITS_RE = re.compile(r"[0-9]+")

# ---- 模块级常量(替代散布各处的魔术数字) ----
_MIN_FILE_SIZE = 8 * 1024          # 保存文件最小有效大小(字节)
_READ_CHUNK = 128 * 1024           # 二进制搜索读取块大小
_AX_CHILD_LIMIT = 20              # AX 子元素最大取数(深度1遍历)
_POLL_INTERVAL = 0.12             # AX 内容哈希轮询间隔(秒)
_FILE_POLL_INTERVAL = 0.1         # 文件出现轮询间隔(秒)
_PANEL_MIN_W, _PANEL_MAX_W = 200, 1200   # 保存面板尺寸区间
_PANEL_MIN_H, _PANEL_MAX_H = 150, 700
_WIN_MIN_W, _WIN_MIN_H = 200, 100        # 有效窗口最小尺寸


class SaveError(RuntimeError):
    """保存失败。no_retry=True 表示重试无意义(如文件被存到别处),应直接停止该姓名。"""

    def __init__(self, msg, no_retry=False):
        super().__init__(msg)
        self.no_retry = no_retry


class FatalError(RuntimeError):
    """致命错误:不解决就无法继续(如 Edge 被关闭、导出目录不可写)。

    与 SaveError 的区别:SaveError 只影响当前这一条,可跳过继续;
    FatalError 影响整批任务,默认进入「等待人工恢复」而不是直接终止整个批次。
    """


class IbosAutomation:
    def __init__(self, cfg, log, stop_event):
        self.cfg = cfg
        self.log = log
        # 兼容 threading.Event 与普通可调用对象
        if hasattr(stop_event, "is_set"):
            self._stopped = stop_event.is_set
        elif stop_event is None:
            self._stopped = lambda: False
        else:
            self._stopped = stop_event
        # 用户介入守卫:检测到有人使用电脑时暂停全部操作,空闲后自动恢复
        self.guard = InterventionGuard(cfg, log, stop_event,
                                       on_state=self._on_guard_state)
        self.guard_state = "idle"        # idle | running | paused(供 GUI 轮询显示)
        self.h = Human(cfg.get("type_interval_s", [0.03, 0.09]),
                       cfg.get("action_delay_s", [0.6, 1.6]),
                       guard=self.guard)
        self._title_before_detail = None
        self._last_saved = ""
        self._warned_offset = False
        # 是否已确认保存面板停留在导出目录(goto_folder 懒修正:首次/跑偏时 ⌘⇧G,其余直接保存)
        self._folder_set = False
        # 实例级缓存(替代类属性,避免多实例共享状态)
        self._stray_cache = {}
        # 后台导出状态(供 GUI 轮询检查)
        self._export_status = "pending"   # pending | ok | failed

    def _on_guard_state(self, state: str) -> None:
        """守卫状态回调:同步到实例属性,供 GUI 轮询显示「暂停中」。"""
        self.guard_state = state

    # ---------------------------------------------------------------- 入口
    def run_all(self, names: list[str], start_index: int = 0) -> None:
        stats = {
            "start": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "end": "", "duration_s": 0.0,
            "processed": 0, "ok": 0, "fail": 0,
            "orders": 0, "files_ok": 0, "files_total": 0,
            "per_name": [], "status_dist": {}, "fatal_events": [],
            "xlsx": "", "report": "", "out_dir": "",
            "pauses": 0, "pause_s": 0.0,
        }
        self._stats = stats
        stats["query_order"] = [n.strip() for n in names if n.strip()]   # 名单顺序(导出按此排)
        self._last_saved = ""
        self._warned_offset = False
        t0 = time.time()
        self.log.info("=== 批量查询开始 ===")
        self.log.info(f"名单共 {len(names)} 人,从第 {start_index + 1} 人开始")
        self.log.info("运行期间请勿移动鼠标/切换窗口,保持 Edge 在前台且窗口位置不变")

        # ---- 环境预检(防呆) ----
        if mac_perms.edge_running() is False:
            raise RuntimeError("Microsoft Edge 未在运行!请打开 Edge 并登录 IBOS 查询页后再开始")
        out = os.path.abspath(self.cfg.get("output_dir", "导出结果"))
        try:
            os.makedirs(out, exist_ok=True)
            probe = os.path.join(out, ".write_test.tmp")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as e:
            raise RuntimeError(f"导出目录不可写: {out} ({e})\n请检查磁盘空间与目录权限")

        # ---- 批次隔离(防交叉污染):新批次开始前自动归档上次查询的所有文件 ----
        if start_index == 0:   # 仅全新批次归档;断点续跑不归档(同批次)
            self._archive_previous_batch(out)
        # 标定坐标合理性(超屏提示,不阻断;多显示器副屏坐标可能大于主屏)
        try:
            import pyautogui as _pg
            sw, sh = _pg.size()
            pts = self.cfg["points"]
            for k in ("name_input", "query_button", "order_input"):
                p = pts.get(k) or [0, 0]
                if p[0] and (p[0] < -100 or p[1] < -100 or p[0] > sw + 3000 or p[1] > sh + 3000):
                    self.log.warn(f"标定点「{k}」({p[0]},{p[1]}) 疑似超出屏幕范围(主屏 {sw}x{sh}),"
                                  f"如未使用副屏请重新「标定坐标」")
        except Exception:
            pass
        # 订单号模式:名单中非纯数字行提示
        if self.cfg.get("query_by", "name") == "order":
            bad = [n for n in names if n and not _DIGITS_RE.fullmatch(n)]
            if bad:
                self.log.warn(f"按订单号查询但名单含非数字行 {len(bad)} 个(如 {bad[:3]}),请检查名单文件")

        bounds = mac_perms.edge_window_bounds()
        if bounds:
            self.log.info(f"Edge 窗口: x={bounds[0]} y={bounds[1]} 宽={bounds[2]} 高={bounds[3]}")
            base = self.cfg.get("points_calibrated_at_bounds") or []
            if base and (bounds[0] != base[0] or bounds[1] != base[1]):
                self.log.warn(f"检测到 Edge 窗口位置与标定时不同(位移 Δx={bounds[0] - base[0]} Δy={bounds[1] - base[1]}),"
                              f"已自动平移坐标,无需重新标定")
        else:
            self.log.warn("未读取到 Edge 窗口,请确认 Edge 已打开")
        mac_perms.activate_app(self.cfg.get("browser", "Microsoft Edge"))
        self.h.pause(0.2, 0.4)
        self._first_query = True   # 首条查询前自动点击「重置」按钮清空页面状态

        # 不停止策略:单条失败默认跳过继续;只有「连续失败达阈值」或「停止指令」才结束
        # stop_on_error=true 时退化为"任一失败即停"(max_consec=1);否则走熔断阈值
        if self.cfg.get("stop_on_error", False):
            max_consec = 1
        else:
            max_consec = max(1, int(self.cfg.get("max_consecutive_failures", 10)))
        consec_fail = 0
        i = start_index
        while i < len(names):
            if self._stopped():
                self.log.warn("收到停止指令,任务中断")
                break
            name = names[i].strip()
            if not name:
                i += 1
                continue
            self.log.progress(f"[{i + 1}/{len(names)}] 查询: {name}")
            item = {"name": name, "ok": False, "error": "", "file": "", "orders": 0}
            try:
                self.process_one_retry(name)
                self._write_progress(i + 1, name, len(names))
                item["ok"] = True
                item["file"] = self._last_saved or ""
                consec_fail = 0
                self.log.ok(f"[{i + 1}/{len(names)}] {name} 完成")
            except InterventionAbort as e:
                # 暂停等待期间用户点了停止 / 等待超时
                self.log.warn(f"任务已停止: {e}")
                break
            except InterventionPause:
                # 介入暂停后已恢复:当前任务完整重做,不计入失败、不前进索引
                self.log.info(f"[{i + 1}/{len(names)}] {name} 介入后重做(不计入失败)")
                continue
            except pyautogui.FailSafeException:
                # 鼠标被甩到屏幕角落(紧急制动):转为暂停等待,不崩溃、不前进
                self.log.warn("⏸ 触发紧急制动(鼠标移到屏幕角落)→ 暂停等待人工")
                try:
                    self.guard.pause_manual("紧急制动:鼠标移动到屏幕角落")
                except InterventionPause:
                    pass        # 恢复后重做当前任务
                self.log.info(f"[{i + 1}/{len(names)}] {name} 制动后重做(不计入失败)")
                continue
            except FatalError as e:
                # 环境级故障(Edge 被关闭等):等待人工恢复后重试当前这条,不前进索引
                # 单独记入 fatal_events,不占用 per_name(避免同一条被计两次)
                self.log.error(f"[{i + 1}/{len(names)}] {name} 环境异常: {e}")
                stats["fatal_events"].append({"name": name, "error": str(e)[:200]})
                if not self._wait_recovery(str(e)):
                    self.log.error("恢复未成功,任务结束(可稍后「从上次进度继续」)")
                    break
                self.log.info(f"环境已恢复,重试 [{i + 1}/{len(names)}] {name}")
                continue
            except Exception as e:
                consec_fail += 1
                item["error"] = str(e)[:200]
                self.log.error(f"[{i + 1}/{len(names)}] {name} 失败: {e}")
                if consec_fail >= max_consec:
                    self.log.error(
                        f"连续失败 {consec_fail} 次(阈值 {max_consec}),"
                        f"疑似页面结构变化或登录失效 → 停止。可处理后「从上次进度继续」")
                    stats["per_name"].append(item)
                    break
                self.log.warn(f"跳过该条,继续下一个(连续失败 {consec_fail}/{max_consec})")
            stats["per_name"].append(item)
            i += 1

        stats["processed"] = len(stats["per_name"])
        stats["ok"] = sum(1 for x in stats["per_name"] if x["ok"])
        stats["fail"] = stats["processed"] - stats["ok"]
        stats["pauses"] = self.guard.pause_count
        stats["pause_s"] = round(self.guard.total_pause_s, 1)
        stats["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats["duration_s"] = round(time.time() - t0, 1)
        self.log.info("=== 批量查询结束 ===")
        if stats["pauses"]:
            self.log.info(f"期间因检测到人工操作自动暂停 {stats['pauses']} 次, "
                          f"累计 {stats['pause_s']} 秒")
        self._finalize(stats)

    def _finalize(self, stats):
        """查询结束后:日志汇总 + macOS 通知(即时);
        Excel 导出与查询报告在后台线程并行执行,不阻塞 GUI 还原。
        导出结果通过 self._export_status 暴露,供 GUI 轮询检查。"""
        out = os.path.abspath(self.cfg.get("output_dir", "导出结果"))
        stats["out_dir"] = out
        try:
            os.makedirs(out, exist_ok=True)
        except OSError:
            pass
        auto_export = self.cfg.get("auto_export_excel", True)
        # 日志汇总(立即)
        self.log.info(f"汇总: 处理 {stats['processed']} 人, 成功 {stats['ok']}, "
                      f"失败 {stats['fail']}, 订单 {stats.get('orders', 0)} 个, "
                      f"用时 {stats['duration_s']} 秒")
        if auto_export:
            self.log.info("Excel 导出与报告正在后台生成…")
        # macOS 通知
        try:
            mac_perms.notify(
                "联通 IBOS 批量查询完成",
                f"处理 {stats['processed']} 个, 成功 {stats['ok']}, "
                f"失败 {stats['fail']}, 订单 {stats.get('orders', 0)} 个")
        except Exception:
            pass
        # Excel 导出 + 文本报告 → 后台线程,不阻塞 GUI
        if auto_export:
            def _export_bg():
                try:
                    from .exporter import export_to_excel, find_result_files, write_text_report
                    files = find_result_files(out)
                    if files:
                        res = export_to_excel(
                            files, os.path.join(out, "查询结果汇总.xlsx"),
                            self.log, run_stats=stats,
                            query_by=self.cfg.get("query_by", "name"),
                            query_order=stats.get("query_order") or [])
                        self.log.ok(f"已自动导出 Excel: {len(files)} 个文件, {res['orders']} 个订单 → 查询结果汇总.xlsx")
                        self._export_status = "ok"
                    else:
                        self.log.warn("未找到查询结果网页文件,跳过自动导出")
                        self._export_status = "failed"
                    rp = os.path.join(out, "查询报告_" +
                                      datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
                    write_text_report(rp, stats)
                    stats["report"] = rp
                    self.log.ok(f"查询报告已生成: {rp}")
                except Exception as e:
                    self.log.warn(f"后台导出失败: {e}")
                    self._export_status = "failed"
            threading.Thread(target=_export_bg, daemon=True).start()

    # ------------------------------------------------------------ 单个姓名
    def process_one_retry(self, name: str) -> None:
        """处理单个姓名,失败时按 auto_retry 配置自动完整重查(重新输入→查询→保存)。

        若第一次其实已保存成功(文件已在导出目录),自动识别并跳过重试,绝不重复保存。
        no_retry 类错误(文件被存到别处)不重试,直接上报。
        """
        retries = max(0, int(self.cfg.get("auto_retry", 1)))
        for attempt in range(retries + 1):
            try:
                self.process_one(name)
                return
            except SaveError as e:
                # 文件其实已保存成功 → 视为成功,不重试
                if self._file_already_saved(name):
                    self.log.warn(f"{name} 的文件已存在(上次保存实际成功),按成功处理")
                    return
                if e.no_retry or attempt >= retries:
                    raise
                self.log.warn(f"{name} 保存未确认,自动重试 {attempt + 1}/{retries} …")
                self._cancel_dialog()
                self.h.pause(0.6, 1.2)
            except Exception:
                raise

    def _file_already_saved(self, name: str) -> bool:
        """检查导出目录是否已有该姓名的查询结果文件(任意格式)。

        文件名格式为 `{name}__查询结果.{ext}`,须匹配 __查询结果 后缀(而非仅匹配 name)。
        """
        out = os.path.abspath(self.cfg.get("output_dir", "导出结果"))
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("_") or "未命名"
        prefix = safe + "__"
        try:
            for f in os.listdir(out):
                if f.startswith(prefix) and \
                        (f.endswith(".html") or f.endswith(".htm") or f.endswith(".mhtml")):
                    return True
        except OSError:
            pass
        return False

    def _window_offset(self) -> tuple[int, int]:
        """返回 Edge 主窗口相对标定时的位移 (dx, dy);未记录/检测失败返回 (0, 0)。"""
        base = self.cfg.get("points_calibrated_at_bounds") or []
        if len(base) != 4:
            return (0, 0)
        cur = mac_perms.edge_window_bounds()
        if not cur:
            return (0, 0)
        return (cur[0] - base[0], cur[1] - base[1])

    def _points(self) -> dict:
        """返回窗口位移补偿后的坐标字典;未移动/未记录时返回原始坐标。"""
        dx, dy = self._window_offset()
        if dx == 0 and dy == 0:
            return self.cfg["points"]
        if not self._warned_offset:
            self._warned_offset = True
            self.log.warn(f"窗口位移补偿生效: 全部坐标平移 (Δx={dx}, Δy={dy})")
        pts = {}
        for k, v in self.cfg["points"].items():
            if isinstance(v, list) and len(v) == 2 and v[0]:
                pts[k] = [v[0] + dx, v[1] + dy]
            elif isinstance(v, list) and len(v) == 4 and v[2]:
                pts[k] = [v[0] + dx, v[1] + dy, v[2], v[3]]
            else:
                pts[k] = v
        return pts

    def _input_point(self) -> list[int | None]:
        """返回当前查询方式的输入框坐标(含窗口补偿)。

        query_by=name → 姓名输入框;query_by=order → 订单号输入框;
        query_by=order_phone → 订购号码输入框。
        """
        pts = self._points()
        mode = self.cfg.get("query_by", "name")
        if mode == "order":
            pt = pts.get("order_input") or [0, 0]
            if pt[0]:
                return pt
            ni = pts.get("name_input") or [0, 0]
            off = self.cfg.get("order_input_offset_y", 48)
            est = [ni[0], max(0, ni[1] - off)]
            self.log.warn(f"订单号输入框未标定,已按姓名框上方推算为 {est}(建议标定时补点订单号输入框精调)")
            return est
        if mode == "order_phone":
            pt = pts.get("order_phone_input") or [0, 0]
            if pt[0]:
                return pt
            self.log.warn("订购号码输入框未标定,请先「标定坐标」或在 config.json 中手动填写 order_phone_input 坐标")
            # 兜底:回退到姓名输入框(不应出现,但避免崩溃)
            return pts["name_input"]
        return pts["name_input"]

    def process_one(self, value: str) -> None:
        p = self._points()
        self._ensure_page()

        # 首次查询前点击「重置」按钮,清空页面残留输入/筛选状态,确保查询干净
        if getattr(self, "_first_query", False):
            self._first_query = False
            rb = p.get("reset_button") or [0, 0]
            if rb[0] > 0:
                self.h.move_click(rb[0], rb[1])
                self.log.info("已点击「重置」按钮,清空页面输入框")
                self.h.pause(0.3, 0.5)

        is_order = self.cfg.get("query_by", "name") == "order"
        is_order_phone = self.cfg.get("query_by", "name") == "order_phone"
        label = "订单号" if is_order else ("订购号码" if is_order_phone else "姓名")

        # 1. 输入查询内容(姓名 or 订单号)
        ipt = self._input_point()
        self.h.clear_and_type(ipt[0], ipt[1], value)
        self.log.info(f"已输入{label}: {value}")
        self.h.pause()

        # 2. 点击查询
        self.h.move_click(p["query_button"][0], p["query_button"][1])
        self.log.info("已点击「查询」")

        # 3. 等待结果(页面加载等待,刻意保持不加速)
        self._wait_results()

        # 4. 保存查询结果页(第 1 个页面)
        self._save_page(f"{value}__查询结果")

        # 5. (可选)详情页
        #    注:保存后浏览器弹出的下载提示条,会在下一次点击(如点输入框)时自动关闭,无需单独处理
        if self.cfg.get("enable_detail", True):
            self._title_before_detail = mac_perms.frontmost_window_title()
            self.h.move_click(p["detail_button"][0], p["detail_button"][1])
            self.log.info("已点击「详情」")
            self.h.pause(1.0, 1.8)
            self._save_page(f"{value}__详情")
            self._close_detail()

        self.h.pause(0.2, 0.4)

    # ------------------------------------------------------------ 等待恢复
    def _wait_recovery(self, reason: str) -> bool:
        """致命错误(如 Edge 被关闭)后等待人工恢复。

        返回 True=环境已恢复(可重试当前这条,不前进索引);
        False=收到停止指令(结束整批,可稍后「从上次进度继续」)。

        - 每 recovery_poll_s 秒检查一次 Edge 是否重新运行,期间不消耗任何鼠标/键盘
        - 响应外部停止指令(用户点「停止」) → 立即返回 False
        - 不依赖用户介入守卫:恢复阶段本就期望人来处理,故守卫不介入
        """
        self.guard_state = "paused"
        self.log.warn(f"⏸ 环境异常,进入等待恢复: {reason}")
        self.log.warn("修复后(如重新打开 Edge 并登录 IBOS 查询页)将自动继续;或点「停止」结束本批")
        self._notify("自动化已暂停", f"{reason}。修复后自动继续;或点「停止」结束")
        try:
            t0 = time.time()
            interval = max(2.0, float(self.cfg.get("recovery_poll_s", 10)))
            while True:
                if self._stopped():
                    self.log.warn("等待恢复期间收到停止指令,结束本批")
                    return False
                if mac_perms.edge_running() is True:
                    self.log.ok("环境已恢复(Edge 重新运行),准备重试当前条目")
                    time.sleep(1.0)
                    self.guard.sync()       # 重新同步基准,避免把修复操作误判为介入
                    return True
                if (time.time() - t0) >= interval:
                    self.log.info(f"仍在等待环境恢复…(已 {int(time.time() - t0)}s,可随时修复后自动继续)")
                    t0 = time.time()
                time.sleep(2.0)
        finally:
            self.guard_state = "running"

    def _ensure_page(self):
        """确保 Edge 在运行且在前台。Edge 崩溃/被关闭时抛 FatalError,由上层进入「等待人工恢复」。"""
        if mac_perms.edge_running() is False:
            raise FatalError(
                "Microsoft Edge 未在运行!请打开 Edge 并登录 IBOS 查询页后,工具会自动继续")
        if mac_perms.frontmost_app_name() != self.cfg.get("browser", "Microsoft Edge"):
            mac_perms.activate_app(self.cfg.get("browser", "Microsoft Edge"))
        self.h.pause(0.15, 0.3)
        if self.cfg.get("reload_before_each", False):
            pyautogui.hotkey("command", "r")
            self.h.pause(2.0, 3.5)

    # ------------------------------------------------------------ 等待结果
    def _wait_results(self):
        """根据 wait_mode 配置选择等待策略。

        - screen_change: 强制画面变化检测(需屏幕录制权限 + 结果区域标定)
        - auto: 有屏幕录制权限且标定了结果区域 → 画面检测;否则用 AX 内容哈希;AX 不可读退回固定等待
        - fixed(或其他): 固定等待 result_wait_s 秒
        """
        mode = self.cfg.get("wait_mode", "auto")
        if mode == "screen_change":
            self._wait_screen_change()
        elif mode == "auto":
            area = self.cfg["points"].get("results_area") or [0, 0, 0, 0]
            if area[2] > 0 and mac_perms.screen_recording_ok():
                self._wait_screen_change()
            else:
                self._wait_page_stable()
        else:
            lo, hi = self.cfg.get("result_wait_s", [1.2, 1.8])
            self.h.pause(lo, hi)

    def _get_page_text_hash(self):
        """通过辅助功能 API 获取 Edge 主窗口的内容区文本哈希。
        深度 1(仅直接子元素):快过深遍历,但能捕获 iframe 结果区的 DOM 变化。
        """
        try:
            from AppKit import NSWorkspace
            from ApplicationServices import AXUIElementCreateApplication, AXUIElementCopyAttributeValue
            for a in NSWorkspace.sharedWorkspace().runningApplications():
                if a.localizedName() == "Microsoft Edge":
                    ax = AXUIElementCreateApplication(a.processIdentifier())
                    err, wins = AXUIElementCopyAttributeValue(ax, "AXWindows", None)
                    if not wins:
                        return None
                    win = wins[0]
                    # 窗口自身:标题 + 值(聚合文本) + 直接子元素的值
                    parts = []
                    for attr in ("AXTitle", "AXValue", "AXDescription"):
                        e, v = AXUIElementCopyAttributeValue(win, attr, None)
                        if v and isinstance(v, str):
                            parts.append(v)
                    # 深度 1:仅读直接子元素(不递归,快于深遍历)
                    e2, children = AXUIElementCopyAttributeValue(win, "AXChildren", None)
                    if children:
                        for ch in children[:_AX_CHILD_LIMIT]:   # 最多取前 N 个直接子元素
                            for attr in ("AXValue", "AXDescription", "AXTitle"):
                                e3, v = AXUIElementCopyAttributeValue(ch, attr, None)
                                if isinstance(v, str) and len(v) > 2:
                                    parts.append(v)
                    return hash(tuple(parts)) if parts else None
        except Exception:
            pass
        return None

    def _wait_page_stable(self):
        """轮询直到页面 AX 内容变化 → 查询完成。
        若 AX 完全读不到 Edge 内容(返回 None)→ 直接走固定等待,不浪费时间轮询。
        """
        baseline = self._get_page_text_hash()
        # AX 完全不可读:Edge 的 AXWindows 为空 → 不空转,立即固定等待
        if baseline is None:
            self.log.info("Edge AX 内容不可读,使用固定等待")
            lo, hi = self.cfg.get("result_wait_s", [1.2, 1.8])
            self.h.pause(lo, hi)
            return
        deadline = time.time() + self.cfg.get("max_wait_s", 4)
        while time.time() < deadline:
            if self._stopped():
                return
            time.sleep(_POLL_INTERVAL)
            h = self._get_page_text_hash()
            if h is not None and h != baseline:
                self.log.info("页面内容已变化(查询结果已返回)")
                return
            # 内容变空也可能表示页面刷新中,继续等
        lo, hi = self.cfg.get("result_wait_s", [1.2, 1.8])
        self.h.pause(lo, hi)

    def _wait_screen_change(self):
        area = self.cfg["points"].get("results_area") or [0, 0, 0, 0]

        def grab():
            if area[2] > 0:
                return pyautogui.screenshot(region=tuple(area)).tobytes()
            return pyautogui.screenshot().tobytes()

        try:
            base = grab()
        except Exception as e:
            self.log.warn(f"截图失败({e}),退回固定等待")
            lo, hi = self.cfg.get("result_wait_s", [2.5, 4.0])
            self.h.pause(lo, hi)
            return
        t0 = time.time()
        changed = False
        while time.time() - t0 < self.cfg.get("max_wait_s", 15):
            if self._stopped():
                break
            self.guard.check()        # 画面变化等待期间若有人介入,立即暂停
            time.sleep(0.5)
            try:
                if grab() != base:
                    changed = True
                    break
            except Exception:
                pass
        self.log.info("检测到页面变化" if changed else f"等待超时({self.cfg.get('max_wait_s')}s)")
        self.h.pause(0.3, 0.5)

    # ---------------------------------------------------------------- 保存
    def _save_page(self, base_name):
        out = os.path.abspath(self.cfg.get("output_dir", "导出结果"))
        os.makedirs(out, exist_ok=True)
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", base_name).strip("_") or "未命名"
        self._pre_delete(out, safe)
        goto = self.cfg.get("save_folder_method", "goto_folder") == "goto_folder"
        # 成功返回真实文件名;失败抛 SaveError(含分类原因与是否可重试标记)
        fname = self._do_save_dialog(out, safe, goto)
        self._last_saved = fname
        self.log.ok(f"已保存: {fname} → {out}")
        # 事后验证:保存的文件应包含本次查询值(防"查询未生效却保存了旧页面")
        if self.cfg.get("verify_saved", True):
            value = base_name.rsplit("__", 1)[0] if "__" in base_name else base_name
            self._verify_saved_file(os.path.join(out, fname), value)
        if self.cfg.get("save_screenshots", False):
            self._save_screenshot(out, safe)

    def _verify_saved_file(self, path, value):
        """事后验证保存的文件(二进制搜索,快速;仅确认值是否存在于文件中)。"""
        try:
            size = os.path.getsize(path)
            if size < _MIN_FILE_SIZE:
                self.log.warn(f"保存文件偏小({size}B),可能不是有效的查询结果页,请留意")
                return
        except OSError:
            return
        if not value:
            return
        try:
            needle = value.encode("utf-8")
            nlen = len(needle)
            with open(path, "rb") as f:
                buf = b""
                while True:
                    chunk = f.read(_READ_CHUNK)
                    if not chunk:
                        break
                    buf = buf[-nlen:] + chunk
                    if needle in buf:
                        return
            self.log.warn(f"保存内容中未找到查询值「{value}」,可能是查询无结果,或查询未生效保存了旧页面,请留意")
        except Exception:
            pass

    def _pre_delete(self, out, safe):
        """删除同名旧文件,避免另存为时弹出「替换」确认框。"""
        for f in os.listdir(out):
            if f == safe or f.startswith(safe + "_files") or f.startswith(safe + "."):
                fp = os.path.join(out, f)
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                else:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass

    def _do_save_dialog(self, out, safe, goto_folder):
        """Cmd+S → 等保存面板就绪 → (goto_folder: 前往目录) → 输入文件名 → 回车 → 确认完成。

        关键:必须等系统保存面板真正弹出后再按键,否则按键会落到页面上导致保存失败;
        失败时给出分类原因(面板未弹/文件未写入/存到别处)与针对性建议。
        返回真实文件名;失败抛 SaveError(no_retry=True 表示重试无意义)。
        """
        # 0) 清理可能遗留的保存/前往文件夹面板,避免 ⌘S 无响应
        try:
            if mac_perms.leftover_panel_windows():
                self.log.warn("检测到疑似遗留面板,先按 Esc 清理")
                self._cancel_dialog()
        except Exception:
            pass

        last_reason = ""
        for attempt in (1, 2):
            if attempt > 1:
                self.log.warn(f"重试保存({attempt}/2)…")
                self._cancel_dialog()
                self.h.pause(0.2, 0.4)
                self._pre_delete(out, safe)   # 清掉上次可能留下的同名文件,避免「是否替换」弹窗
            # Edge 已在前台则跳过激活(省 osascript 开销)
            if mac_perms.frontmost_app_name() != self.cfg.get("browser", "Microsoft Edge"):
                mac_perms.activate_app(self.cfg.get("browser", "Microsoft Edge"))
            self.h.pause(0.15, 0.3)
            baseline = mac_perms.edge_windows_cg()   # ⌘S 前的窗口基线
            pyautogui.hotkey("command", "s")

            # 1) 等保存面板真正打开(实测:面板=Edge名下新增的小窗口;连续2次采样确认防误判)
            wait_panel = self.cfg.get("save_dialog_delay_s", 2.0) + 4.0
            if not self._wait_panel_open(wait_panel, baseline, confirm=2):
                last_reason = ("保存面板未弹出。请确认:①Edge 在前台且已激活;②「辅助功能」权限已开启;"
                               "③⌘S 快捷键未被其他应用占用")
                self._cancel_dialog()
                continue

            # 2) 目录策略(goto_folder):首次保存或重试时 ⌘⇧G 跳导出目录;
            #    其余情况利用 Edge「记住上次目录」——保存面板已停在导出目录,直接保存,省一次跳转
            needs_jump = goto_folder and (attempt > 1 or not self._folder_set)
            if needs_jump:
                pyautogui.hotkey("command", "shift", "g")
                if not self._wait_panel_open(3.5, baseline, confirm=2):
                    last_reason = "「前往文件夹」面板未弹出"
                    self._cancel_dialog()
                    continue
                time.sleep(0.3)
                pyautogui.hotkey("command", "a")
                pyperclip.copy(out)
                pyautogui.hotkey("command", "v")
                self.h.pause(0.15, 0.25)
                pyautogui.press("return")
                time.sleep(0.6)

            # 3) 输入文件名并保存
            pyautogui.hotkey("command", "a")
            self.h.pause(0.06, 0.1)
            pyperclip.copy(safe)
            pyautogui.hotkey("command", "v")
            self.h.pause(0.12, 0.2)
            pyautogui.press("return")
            self.log.info("已确认保存,等待文件写入完成…")

            # 4) 等文件出现在导出目录(支持 .html/.htm/.mhtml;大页面写入可能需 20s+)
            fname = self._wait_file(out, safe)
            if fname:
                # 5) 校验文件非空(防止识别到刚创建的空文件)
                fp = os.path.join(out, fname)
                for _ in range(10):
                    try:
                        if os.path.getsize(fp) > 0:
                            break
                    except OSError:
                        break
                    time.sleep(0.2)
                time.sleep(0.3)   # 等对话框完成收尾(网页全部模式下会写入资源文件夹)
                self._folder_set = True
                return fname

            # 6) 没等到:先看是不是存到别处了(目录跑偏)→ 自动 ⌘⇧G 修正后重试,不再直接放弃
            stray = self._find_stray_save(safe)
            if stray:
                if attempt == 1:
                    self._folder_set = False
                    last_reason = f"文件被保存到其他位置({stray}),目录可能跑偏,自动 ⌘⇧G 修正后重试"
                    self.log.warn(last_reason)
                    self._cancel_dialog()
                    continue
                else:
                    self.log.error(
                        f"修正目录后文件仍被保存到其他位置: {stray}。"
                        f"请确认浏览器「另存为」默认目录,或把 save_folder_method 改为 goto_folder。")
                    self._cancel_dialog()
                    raise SaveError(f"保存失败:{safe}(文件被保存到其他位置: {stray})", no_retry=True)
            last_reason = (f"保存完成检测超时({self.cfg.get('save_complete_timeout_s', 30)}s)。"
                           f"若页面较大属正常,可适当调大 save_complete_timeout_s;"
                           f"若一直超时,请检查浏览器「另存为」格式是否被改动")
            self._cancel_dialog()
        raise SaveError(f"保存失败:{safe}({last_reason})")

    def _wait_panel_open(self, timeout: float, baseline=None, confirm: int = 1) -> bool:
        """轮询检测保存类面板是否出现;读不到(权限缺失)时按已打开处理,避免卡死。

        confirm: 连续采样确认次数(≥2 可防瞬时误判)。
        """
        t0 = time.time()
        hits = 0
        while time.time() - t0 < timeout:
            if self._stopped():
                return False
            st = mac_perms.save_panel_open(baseline)
            if st is True:
                hits += 1
                if hits >= confirm:
                    return True
            elif st is False:
                hits = 0
            else:  # None = 检测信号完全不可用,退回固定等待 save_dialog_delay_s(而非 0.2s,避免按键落空)
                delay = self.cfg.get("save_dialog_delay_s", 2.0)
                time.sleep(delay)
                return True
            time.sleep(0.15)
        return False

    def _find_stray_save(self, safe: str) -> str | None:
        """在 Downloads/Desktop 里找是否已被浏览器保存到别处(返回文件路径或 None)。

        仅扫描 Downloads(99% 的跑偏落点),Desktop 仅当 Downloads 未命中时兜底。
        使用实例级缓存避免每次重试都遍历大目录。
        """
        for d in (os.path.expanduser("~/Downloads"),
                  os.path.expanduser("~/Desktop")):
            ck = (safe, d)
            if ck in self._stray_cache:
                return self._stray_cache[ck]
            try:
                for f in os.listdir(d):
                    if f.startswith(safe + ".") and \
                            (f.endswith(".html") or f.endswith(".htm") or f.endswith(".mhtml")):
                        self._stray_cache[ck] = os.path.join(d, f)
                        return self._stray_cache[ck]
            except OSError:
                pass
            self._stray_cache[ck] = None  # 未命中也缓存,下轮跳过
        return None

    # ------------------------------------------------------------ 归档
    def _archive_previous_batch(self, out):
        """新批次开始前,将上一次查询的结果文件归档到时间戳子目录,避免批次间数据交叉。

        归档内容: *__查询结果.* / *_详情.* / 查询结果汇总.xlsx / 查询报告_*.txt / 进度.txt
        同名文件(极短时间内跑两批)追加时间戳后缀,避免覆盖。
        """
        patterns = ["*__查询结果.*", "*__详情.*", "查询结果汇总.xlsx",
                    "查询报告_*.txt", "进度.txt"]
        to_move = []
        for pat in patterns:
            for fp in glob.glob(os.path.join(out, pat)):
                to_move.append(fp)
        if not to_move:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = os.path.join(out, f"归档_{ts}")
        os.makedirs(archive_dir, exist_ok=True)
        moved = 0
        for fp in to_move:
            try:
                dst = os.path.join(archive_dir, os.path.basename(fp))
                # 同名文件已存在时追加时间戳,避免覆盖
                if os.path.exists(dst):
                    stem, ext = os.path.splitext(os.path.basename(fp))
                    dst = os.path.join(archive_dir, f"{stem}_{ts}{ext}")
                shutil.move(fp, dst)
                moved += 1
            except OSError:
                pass
        if moved:
            self.log.info(f"已将上次查询的 {moved} 个文件归档至 归档_{ts}/,本次查询结果互不干扰")
        # 重置保存目录标记(新批次重新确认目录)
        self._folder_set = False

    # ---------------------------------------------------------------- 等待文件
    def _wait_file(self, out: str, safe: str) -> str | None:
        """轮询导出目录,等待 `safe.html/.htm/.mhtml` 出现,返回真实文件名;超时返回 None。

        使用 os.path.exists 逐扩展名检查,避免遍历大目录(导出目录可能含 1000+ 文件)。
        """
        deadline = time.time() + self.cfg.get("save_complete_timeout_s", 30)
        exts = (".html", ".htm", ".mhtml")
        while time.time() < deadline:
            if self._stopped():
                break
            time.sleep(_FILE_POLL_INTERVAL)
            for ext in exts:
                fp = os.path.join(out, safe + ext)
                if os.path.exists(fp):
                    return safe + ext
        return None

    def _cancel_dialog(self):
        pyautogui.press("escape")
        self.h.pause(0.15, 0.3)

    def _save_screenshot(self, out, safe):
        try:
            shot_dir = os.path.join(out, "截图")
            os.makedirs(shot_dir, exist_ok=True)
            img = pyautogui.screenshot()
            img.save(os.path.join(shot_dir, safe + ".png"))
            self.log.ok(f"截图: 截图/{safe}.png")
        except Exception as e:
            self.log.warn(f"截图失败(不影响主流程): {e}")

    # ------------------------------------------------------------ 关闭详情
    def _close_detail(self):
        mode = self.cfg.get("detail_mode", "new_tab")
        if mode == "new_tab":
            # 安全校验:若窗口标题无变化,疑似没有真正打开详情,则跳过关闭标签页
            after = mac_perms.frontmost_window_title()
            if self._title_before_detail and after and self._title_before_detail == after:
                self.log.warn("未检测到详情页标题变化,跳过关闭标签页(避免误关列表页)")
                return
            mac_perms.activate_app(self.cfg.get("browser", "Microsoft Edge"))
            self.h.pause(0.2, 0.4)
            pyautogui.hotkey("command", "w")
            self.log.info("已关闭详情标签页(⌘W)")
            self.h.pause(0.5, 1.0)
        elif mode == "dialog":
            pyautogui.press("escape")
            self.log.info("已按 Esc 关闭弹窗")
            self.h.pause(0.5, 1.0)
        else:  # same_page
            b = self.cfg["points"].get("back_button") or [0, 0]
            if b[0] > 0:
                self.h.move_click(b[0], b[1])
                self.log.info("已点击返回按钮")
            else:
                pyautogui.hotkey("command", "[")
                self.log.info("已按 ⌘[ 返回")
            self.h.pause(0.8, 1.5)

    # ---------------------------------------------------------------- 进度
    def _write_progress(self, idx: int, name: str = "", total: int = 0) -> None:
        """写入断点进度(JSON,含姓名与时间)。"""
        try:
            data = {
                "index": int(idx),
                "name": name or "",
                "total": int(total),
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.cfg.get("progress_file", "进度.txt"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            self.log.warn(f"写入进度文件失败(不影响主流程): {e}")

    @staticmethod
    def read_progress(path: str = "进度.txt") -> dict:
        """读取断点进度。返回 dict:{index, name, total, time};兼容旧的纯数字格式。"""
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read().strip()
            if raw.lstrip("-").isdigit():   # 旧格式:纯数字
                return {"index": int(raw), "name": "", "total": 0, "time": ""}
            data = json.loads(raw)
            return {"index": int(data.get("index", 0)), "name": data.get("name", ""),
                    "total": int(data.get("total", 0)), "time": data.get("time", "")}
        except Exception:
            return {"index": 0, "name": "", "total": 0, "time": ""}
