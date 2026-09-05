# -*- coding: utf-8 -*-
"""exporter.py 核心解析逻辑的单元测试。

使用脱敏后的模拟 MHTML 样本验证解析正确性,不依赖真实 IBOS 数据。
运行: python -m pytest tests/test_exporter.py -v
或:   python tests/test_exporter.py
"""
import os
import sys
import tempfile
import unittest

# 将项目根目录加入 sys.path
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.exporter import (
    _norm, _parse_pairs, _extract_links, _template_row,
    _extract_products, _LABEL_RE, _PHONE_RE, TEMPLATE_COLS,
    parse_mhtml, export_to_excel, find_result_files,
    write_text_report,
)


class TestNorm(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_norm("  hello   world  "), "hello world")

    def test_nbsp(self):
        self.assertEqual(_norm("\xa0\u3000test"), "test")

    def test_empty(self):
        self.assertEqual(_norm(""), "")
        self.assertEqual(_norm("   "), "")


class TestParsePairs(unittest.TestCase):
    def test_basic_pairs(self):
        text = "订单ID：123456\n号码：13812345678\n订单状态：已完成"
        pairs = _parse_pairs(text)
        self.assertEqual(pairs.get("订单ID"), "123456")
        self.assertEqual(pairs.get("号码"), "13812345678")
        self.assertEqual(pairs.get("订单状态"), "已完成")

    def test_halfwidth_colon(self):
        """半角冒号也应正确匹配(兼容性)。"""
        text = "订单ID: 999\n号码: 13900001111"
        pairs = _parse_pairs(text)
        self.assertEqual(pairs.get("订单ID"), "999")
        self.assertEqual(pairs.get("号码"), "13900001111")

    def test_missing_label(self):
        text = "这是一段没有标签的文本"
        pairs = _parse_pairs(text)
        self.assertEqual(pairs, {})


class TestExtractLinks(unittest.TestCase):
    def test_single(self):
        self.assertEqual(_extract_links("短链详情：https://example.com/abc"),
                         "https://example.com/abc")

    def test_multiple_dedup(self):
        text = "https://a.com https://b.com https://a.com"
        self.assertEqual(_extract_links(text), "https://a.com\nhttps://b.com")

    def test_empty(self):
        self.assertEqual(_extract_links(""), "")
        self.assertEqual(_extract_links(None), "")

    def test_trailing_punct(self):
        self.assertEqual(_extract_links("链接：https://example.com/abc。"),
                         "https://example.com/abc")


class TestPhoneRe(unittest.TestCase):
    def test_extract_11digit(self):
        m = _PHONE_RE.search("号码：13812345678(杭州市)")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "13812345678")

    def test_no_match(self):
        m = _PHONE_RE.search("无号码")
        self.assertIsNone(m)


class TestTemplateRow(unittest.TestCase):
    def test_basic_mapping(self):
        order = {
            "成交时间": "2026-07-16 10:30:00",
            "联系人": "张三",
            "号码": "13812345678",
            "__联系人电话": "13900001111",
            "订单ID": "ORD001",
            "订单状态": "已完成",
            "物流单号": "SF123456",
            "短链详情": "https://short.link/abc",
            "__产品信息": "流量王畅享版",
        }
        row = _template_row(order)
        self.assertEqual(row["日期"], "2026-07-16")
        self.assertEqual(row["姓名"], "张三")
        self.assertEqual(row["手机号"], "13812345678")
        self.assertEqual(row["联系电话"], "13900001111")
        self.assertEqual(row["订单编号"], "ORD001")
        self.assertEqual(row["订单状态是否通过审核"], "已完成")
        self.assertEqual(row["运单号"], "SF123456")
        self.assertEqual(row["短链"], "https://short.link/abc")
        self.assertEqual(row["套餐"], "流量王畅享版")
        # 未映射的列应留空
        self.assertEqual(row["开卡人"], "")
        self.assertEqual(row["团队长"], "")

    def test_empty_order(self):
        row = _template_row({})
        for col in TEMPLATE_COLS:
            self.assertEqual(row[col], "")


class TestExtractProducts(unittest.TestCase):
    def test_basic(self):
        text = "【TZ】流量王畅享线上版 异网"
        products = _extract_products(text)
        self.assertTrue(len(products) > 0)
        self.assertIn("流量王", products[0])

    def test_with_end_mark(self):
        text = "【TZ】流量王畅享版 号码：13812345678"
        products = _extract_products(text)
        self.assertTrue(len(products) > 0)
        self.assertNotIn("号码", products[0])

    def test_no_product(self):
        text = "这段文本不含产品关键词"
        products = _extract_products(text)
        self.assertEqual(products, [])


class TestLabelRe(unittest.TestCase):
    def test_fullwidth_colon(self):
        m = _LABEL_RE.search("订单ID：123")
        self.assertIsNotNone(m)

    def test_halfwidth_colon(self):
        m = _LABEL_RE.search("订单ID: 123")
        self.assertIsNotNone(m)

    def test_long_label_first(self):
        """长标签应优先匹配(登录号码 vs 号码)。"""
        text = "登录号码：13812345678"
        m = _LABEL_RE.search(text)
        self.assertIsNotNone(m)
        self.assertIn("登录号码", m.group(0))


class TestExportToExcel(unittest.TestCase):
    """端到端测试:构造简易 MHTML → 导出 Excel → 验证结果。"""

    def _make_mhtml(self, path, order_id, phone, name, status="已完成"):
        """生成一个极简 MHTML 文件(含主表格 + 一个订单行)。"""
        html = f"""Content-Type: multipart/related; boundary="----=_boundary"

------=_boundary
Content-Type: text/html

<html><body>
<table>
<tr><th>订单标识</th><th>商品信息</th><th>订单费用</th><th>买家信息</th></tr>
<tr>
<td>订单ID：{order_id} 号码：{phone} 订单状态：{status}</td>
<td>证件类型：18位身份证 流量王畅享版 联系人：{name} 13900001111</td>
<td>费用金额：99元</td>
<td>短链详情：https://short.link/{order_id}</td>
</tr>
</table>
</body></html>
------=_boundary--
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    def test_export_basic(self):
        with tempfile.TemporaryDirectory() as d:
            # 生成两个测试 MHTML
            self._make_mhtml(os.path.join(d, "张三__查询结果.mhtml"), "ORD001", "13812345678", "张三")
            self._make_mhtml(os.path.join(d, "李四__查询结果.mhtml"), "ORD002", "13987654321", "李四")

            files = find_result_files(d)
            self.assertEqual(len(files), 2)

            out = os.path.join(d, "汇总.xlsx")
            from core.logger import Logger
            res = export_to_excel(files, out, Logger(), query_by="name")

            self.assertEqual(res["files_total"], 2)
            self.assertEqual(res["files_ok"], 2)
            self.assertEqual(res["orders"], 2)
            self.assertTrue(os.path.exists(out))

    def test_export_order_mode(self):
        """按订单号查询模式:有标识列,按名单顺序,无结果保留空行。"""
        with tempfile.TemporaryDirectory() as d:
            self._make_mhtml(os.path.join(d, "ORD001__查询结果.mhtml"), "ORD001", "13812345678", "张三")
            # ORD002 不生成文件(模拟查不到结果)

            files = find_result_files(d)
            self.assertEqual(len(files), 1)

            out = os.path.join(d, "汇总.xlsx")
            from core.logger import Logger
            res = export_to_excel(
                files, out, Logger(),
                query_by="order",
                query_order=["ORD001", "ORD002", "ORD001"])

            self.assertEqual(res["orders"], 2)  # ORD001 有 1 个订单 × 2 次 - 但去重文件只有 1 个
            self.assertTrue(os.path.exists(out))

    def test_export_order_phone_mode(self):
        """按订购号码查询模式:同样有标识列(与 order 模式一致)。"""
        with tempfile.TemporaryDirectory() as d:
            self._make_mhtml(os.path.join(d, "13812345678__查询结果.mhtml"),
                             "ORD001", "13812345678", "张三")

            files = find_result_files(d)
            out = os.path.join(d, "汇总.xlsx")
            from core.logger import Logger
            res = export_to_excel(
                files, out, Logger(),
                query_by="order_phone",
                query_order=["13812345678"])

            self.assertEqual(res["orders"], 1)
            self.assertTrue(os.path.exists(out))


class TestParseMhtml(unittest.TestCase):
    def test_parse_basic(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test__查询结果.mhtml")
            html = """Content-Type: multipart/related; boundary="----=_boundary"

------=_boundary
Content-Type: text/html

<html><body>
<table>
<tr><th>订单标识</th><th>商品信息</th></tr>
<tr>
<td>订单ID：ORD123 号码：13812345678 订单状态：已完成</td>
<td>证件类型：18位身份证 流量王畅享版</td>
</tr>
</table>
</body></html>
------=_boundary--
"""
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            orders = parse_mhtml(path)
            self.assertEqual(len(orders), 1)
            self.assertEqual(orders[0].get("订单ID"), "ORD123")
            self.assertEqual(orders[0].get("号码"), "13812345678")
            self.assertEqual(orders[0].get("订单状态"), "已完成")
            self.assertEqual(orders[0].get("证件类型"), "18位身份证")
            self.assertIn("流量王", orders[0].get("__产品信息", ""))

    def test_parse_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "empty__查询结果.mhtml")
            with open(path, "w") as f:
                f.write("")
            orders = parse_mhtml(path)
            self.assertEqual(orders, [])

    def test_parse_no_table(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notable__查询结果.mhtml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html><body><p>no table here</p></body></html>")
            orders = parse_mhtml(path)
            self.assertEqual(orders, [])


class TestWriteTextReport(unittest.TestCase):
    """测试文本报告生成,特别是 per_key_orders 缺失时的兜底处理。"""

    def test_with_per_key_orders(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "报告.txt")
            stats = {
                "start": "2026-08-08 10:00:00",
                "end": "2026-08-08 10:30:00",
                "duration_s": 1800,
                "processed": 3, "ok": 2, "fail": 1,
                "files_ok": 2, "files_total": 3,
                "orders": 5,
                "per_name": [
                    {"name": "张三", "ok": True, "error": ""},
                    {"name": "李四", "ok": False, "error": "保存失败"},
                    {"name": "王五", "ok": True, "error": ""},
                ],
                "per_key_orders": {"张三": 3, "王五": 2},
                "status_dist": {"已完成": 4, "处理中": 1},
            }
            write_text_report(path, stats)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("联通 IBOS 查询报告", content)
            self.assertIn("张三", content)
            self.assertIn("成功", content)
            self.assertIn("失败", content)
            self.assertIn("保存失败", content)
            self.assertIn("已完成: 4 单", content)

    def test_without_per_key_orders(self):
        """per_key_orders 不在 stats 中时不应崩溃(导出失败仍生成报告)。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "报告.txt")
            stats = {
                "start": "2026-08-08 10:00:00",
                "end": "2026-08-08 10:30:00",
                "duration_s": 1800,
                "processed": 1, "ok": 1, "fail": 0,
                "per_name": [{"name": "张三", "ok": True, "error": ""}],
                # 故意不提供 per_key_orders
            }
            write_text_report(path, stats)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("张三", content)

    def test_empty_stats(self):
        """空 stats 不应崩溃。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "报告.txt")
            write_text_report(path, {})
            self.assertTrue(os.path.exists(path))


class TestExtractProductsHalfwidthColon(unittest.TestCase):
    """半角冒号兼容性测试。"""

    def test_halfwidth_colon_end_mark(self):
        """产品信息后跟半角冒号字段标志时也应正确截断。"""
        text = "【TZ】流量王畅享版 号码: 13812345678"
        products = _extract_products(text)
        self.assertTrue(len(products) > 0)
        self.assertNotIn("号码", products[0])

    def test_fullwidth_colon_end_mark(self):
        """全角冒号字段标志截断(回归验证)。"""
        text = "【TZ】流量王畅享版 号码：13812345678"
        products = _extract_products(text)
        self.assertTrue(len(products) > 0)
        self.assertNotIn("号码", products[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
