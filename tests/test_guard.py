# -*- coding: utf-8 -*-
"""InterventionGuard 用户介入守卫的单元测试。

通过桩替换真实系统信号(鼠标位置 / 前台应用 / 时间戳),
验证「检测到介入→暂停」「超时→中止」「off 模式跳过」三条核心路径,
不触发真实鼠标/键盘,也不依赖真实图形会话。
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.guard import InterventionGuard, InterventionPause, InterventionAbort


class _FakeLog:
    def __getattr__(self, _name):
        def _(*_a, **_k):
            pass
        return _


def _make(mode="auto", **over):
    cfg = {"pause_on_intervention": True, "intervention_check": mode,
           "resume_idle_s": 5.0, "intervention_wait_timeout_s": 0}
    cfg.update(over)
    g = InterventionGuard(cfg, _FakeLog())
    # 关闭真实系统信号,改用可控桩:极久无输入 / 前台恒为浏览器
    g._idle_fn = lambda etype: 999.0
    g._frontmost_fn = lambda: "Microsoft Edge"
    return g


class TestGuardLogic(unittest.TestCase):
    def test_off_mode_skips_detect(self):
        g = _make(mode="off")
        g._signal_mouse = lambda: "should-not-fire"
        g.check()                      # 不抛异常、不暂停
        self.assertFalse(g.paused)

    def test_clean_signals_no_pause(self):
        g = _make()
        g._last_mouse = (100, 100)
        g._signal_mouse = lambda: None
        g.check()
        self.assertFalse(g.paused)

    def test_mouse_drift_triggers_pause_then_resume(self):
        g = _make()
        g._last_mouse = (100, 100)
        # 前两次返回介入信号(匹配 check 的二次确认 + 暂停循环首轮),之后清零 → 满足恢复条件
        st = {"n": 0}

        def _mouse():
            st["n"] += 1
            return "检测到鼠标被移动" if st["n"] <= 2 else None

        g._signal_mouse = _mouse
        with self.assertRaises(InterventionPause):
            g.check()                  # 暂停→恢复后抛出 InterventionPause(上层据此重做)
        self.assertTrue(g.pause_count >= 1)
        self.assertFalse(g.paused)

    def test_wait_timeout_raises_abort(self):
        # 介入信号持续存在 + 等待超时 → 抛 InterventionAbort
        g = _make(intervention_wait_timeout_s=0.2)
        g._last_mouse = (100, 100)
        g._signal_mouse = lambda: "持续介入"
        with self.assertRaises(InterventionAbort):
            g.check()
        self.assertTrue(g.pause_count >= 1)

    def test_manual_pause_request(self):
        g = _make()
        self.assertFalse(g._manual_pause)
        g.request_pause()
        self.assertTrue(g._manual_pause)
        g.clear_pause()
        self.assertFalse(g._manual_pause)

    def test_summary_no_interrupt(self):
        g = _make()
        self.assertEqual(g.summary(), "无中断")


if __name__ == "__main__":
    unittest.main(verbosity=2)
