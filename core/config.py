# -*- coding: utf-8 -*-
"""配置读写。配置文件为 config.json,points(标定坐标)由悬浮窗「标定坐标」功能自动写入。"""
import copy
import json
import os

DEFAULT = {
    "_说明": "本文件为自动查询工具配置。points 为屏幕标定坐标,通过悬浮窗「标定坐标」自动写入,一般无需手改。",
    "browser": "Microsoft Edge",
    "platform_url": "https://ibos.10010.com",
    "names_file": "名单.txt",
    "output_dir": "导出结果",
    "log_file": "运行日志.log",
    "progress_file": "进度.txt",
    "points": {
        "name_input": [0, 0],
        "query_button": [0, 0],
        "detail_button": [0, 0],
        "back_button": [0, 0],
        "results_area": [0, 0, 0, 0],
        "order_input": [0, 0],
        "order_phone_input": [0, 0],
        "reset_button": [1253, 496]
    },
    # 查询方式: name=按姓名查询(默认) | order=按订单号查询 | order_phone=按订购号码查询
    # 按订单号时,输入内容为名单文件中每行的订单号(纯数字);
    # 订单号输入框位于姓名框上方,可用标定点②精确定位,未标定时按姓名框上方推算
    "query_by": "name",
    # 订单号输入框未标定时,相对姓名框向上推算的偏移量(像素)
    "order_input_offset_y": 48,
    # 标定时 Edge 主窗口位置(x,y,w,h),用于运行时检测窗口位移并自动补偿坐标
    "points_calibrated_at_bounds": [],
    # 是否查询详情页(点击详情并保存第 2 个页面)。false=只保存查询结果页
    "enable_detail": False,
    # 详情打开方式: new_tab=新标签页 | same_page=当前页跳转 | dialog=弹窗
    "detail_mode": "new_tab",
    # 每个姓名查询前是否刷新页面(回到查询页)。same_page 模式建议开启
    "reload_before_each": False,
    # 模拟输入: 单个 ASCII 字符按键间隔(秒)
    "type_interval_s": [0.02, 0.05],
    # 常规操作间随机停顿(秒)(页面加载等待不受此影响,见 result_wait_s)
    # 已按"策略验证不触发风控"再次收紧(原 0.35-0.8);仍保留随机性,如遇网络慢可回调
    "action_delay_s": [0.25, 0.55],
    # 点击查询后等待结果(秒)区间 —— 页面加载等待,已多次缩短(2.5-4.0→2.0-2.8→1.8-2.6→1.2-1.8);
    # 有「保存后内容验证」兜底:若等太短导致保存了旧页面/未加载完,日志会警告"未找到查询值",
    # 届时把该值调大即可;若开启屏幕录制权限并标定⑤结果区域,wait_mode=auto 会自动改用画面检测,更快更准
    "result_wait_s": [1.2, 1.8],
    # 等待方式: auto=智能(有屏幕录制权限且标定⑤结果区域→检测页面变化,否则固定等待) |
    #           fixed=固定等待 | screen_change=强制画面变化检测(需屏幕录制权限与结果区域标定)
    "wait_mode": "auto",
    "max_wait_s": 15,
    # 保存方式: goto_folder=每次自动 ⌘⇧G 跳转导出目录(推荐,可靠) | remember=用浏览器记住的目录(需先手动另存一次到导出目录)
    "save_folder_method": "goto_folder",
    "save_dialog_delay_s": 2.0,
    # 等待保存完成的最大时间(秒);大页面(含图片/MHTML)写入可能需要 20s+
    "save_complete_timeout_s": 30,
    # 单个姓名失败后的自动重试次数(0=不重试)。重试=重新输入姓名→查询→保存;
    # 若文件其实已保存成功,会自动识别并跳过重试
    "auto_retry": 1,
    # 每个姓名额外保存一张屏幕截图(需屏幕录制权限)
    "save_screenshots": False,
    # 保存后验证文件内容(防呆:文件过小/不含本次查询值 → 警告提示)
    "verify_saved": True,
    # 查询结束后自动解析保存的网页并生成 Excel(含查询报告)
    "auto_export_excel": True,
    # ---- 错误容忍(不停止策略)----
    # 单条查询失败默认跳过并继续;连续失败达该次数(疑似页面结构变化/登录失效)才停止,
    # 避免单次网络抖动导致整批中断。设为 1 即"任何失败都停"(不推荐无人值守场景)
    "max_consecutive_failures": 10,
    # 兼容旧配置:true=任一失败立即停止(不推荐);false=走 max_consecutive_failures 熔断
    "stop_on_error": False,
    # 致命错误(如 Edge 被关闭)后,每隔 recovery_poll_s 秒检查一次环境是否恢复;
    # 恢复后自动重试当前条,不前进索引(无限等待,直到恢复或点「停止」)
    "recovery_poll_s": 10,
    # ---- 用户介入守卫(无人值守安全核心)----
    # 有人使用电脑(动鼠标/键盘/切窗)时立即暂停所有操作,人离开后自动恢复,不与人抢设备
    "pause_on_intervention": True,
    # 检测模式: auto=三信号融合 | mouse=仅鼠标漂移 | frontmost=仅前台应用 | off=关闭
    "intervention_check": "auto",
    # 人离开多少秒后自动恢复运行(秒);过小可能在人抖腿时误恢复,过大则人走后空等
    "resume_idle_s": 5.0,
    # 暂停后最长等待时间(秒);0=无限等待人工,直到人离开或点「停止」(推荐无人值守)
    "intervention_wait_timeout_s": 0,
    "resume_enabled": True,
    # 运行日志超过该大小(MB)后自动轮转归档
    "log_max_mb": 5
}


def _deep_merge(base: dict, upd: dict) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load(path: str = "config.json") -> dict:
    cfg = copy.deepcopy(DEFAULT)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            _deep_merge(cfg, user)
        except (json.JSONDecodeError, ValueError) as e:
            # JSON 语法错误:明确提示用户,而非静默回退默认值(否则标定坐标丢失却无感知)
            import sys
            print(f"[WARNING] config.json 解析失败({e}),已使用默认配置启动。"
                  f"请检查 {path} 的 JSON 语法(如多余逗号、缺少引号等)", file=sys.stderr)
        except Exception as e:
            import sys
            print(f"[WARNING] 读取 config.json 时出错({e}),已使用默认配置启动", file=sys.stderr)
    return cfg


def save(cfg: dict, path: str = "config.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
