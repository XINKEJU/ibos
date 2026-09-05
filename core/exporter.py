# -*- coding: utf-8 -*-
"""解析保存的查询结果网页(MHTML) → Excel。

纯本地文件解析:不访问平台、不注入任何脚本,因此不受 IBOS 风控影响。
数据位于 MHTML 的 iframe 部件内,主表格一行 = 一个订单,单元格内为「字段名：值」堆叠文本。
"""
import datetime
import email
import glob
import os
import re
import time
from email import policy

from bs4 import BeautifulSoup

# 已知字段(按长度降序排列,保证长标签优先匹配,如「登录号码」先于「号码」)
LABELS = [
    "订单ID", "订单编号", "订单来源", "订单渠道", "渠道编码", "成交时间", "订单参考级别",
    "登录号码", "证件类型", "AI外呼模型", "AI外呼次数", "AI外呼结果", "AI外呼状态",
    "号码", "订单状态", "风控打标", "配送方式", "物流公司", "物流单号", "客服标注",
    "上游下单触点", "费用金额", "支付方式", "联系人", "配送地址", "装机地址", "短链详情",
    "入网姓名", "联系电话", "证件号码", "订购号码",
]
_LABEL_RE = re.compile(
    "(" + "|".join(re.escape(x) for x in sorted(LABELS, key=len, reverse=True)) + r")\s*[：:]")

_PHONE_RE = re.compile(r"1\d{10}")
_PRODUCT_KWS = ("流量王", "畅享", "异网", "本网", "宽带", "套餐")
# 产品名称后遇到这些字段标志即截断(产品文本到此为止)
_PRODUCT_END_MARKS = ("号码", "订单状态", "风控打标", "配送方式", "客服标注", "上游下单触点",
                      "费用金额", "支付方式", "联系人", "配送地址", "装机地址", "短链详情")
# 短信/服务提醒:以「【服务提醒】【系统验证】」等提醒类前缀开头,到下一个「【」、
# 字段标签(如「号码：」)或操作列「详 情」前结束(防止吞掉后续字段文本)
_SMS_STOP = "|".join(re.escape(x) + r"\s*：" for x in sorted(LABELS, key=len, reverse=True))
_SMS_RE = re.compile(r"【(?:服务提醒|系统验证|验证码|提醒|通知)[^】]*】[^【]*?(?=【|" +
                     _SMS_STOP + r"|详\s*情|$)")
# 商品信息单元格内「证件类型：XX 产品名」格式的拆分:证件类型词在前,剩余为产品名
_ID_TYPE_RE = re.compile(r"^(18位身份证|身份证|港澳台居民居住证|外国人永久居住证|"
                         r"港澳居民来往内地通行证|台湾居民来往大陆通行证|护照|军官证|临时身份证)")

RAW_CELL_NAMES = ["订单标识原文", "商品信息原文", "订单费用原文", "买家信息原文"]

# ---------------------------------------------------------------------------
# 导出列模板(用户指定表头:日期 姓名 开卡人 团队长 归属地 学校 手机号 联系电话
# 订单编号 订单状态是否通过审核 运单号 短链 用户办理方式 是否新生 套餐 发出日期 激活日期 宽带)
# 数据能填的字段自动填入,填不了的列留空;订单号模式在最前面额外保留「查询订单号」标识列
# ---------------------------------------------------------------------------
TEMPLATE_COLS = ["日期", "姓名", "开卡人", "团队长", "归属地", "学校", "手机号",
                 "联系电话", "订单编号", "订单状态是否通过审核",
                 "运单号", "短链", "用户办理方式", "是否新生", "套餐",
                 "发出日期", "激活日期", "宽带"]

# 提取短链(https:// 链接,去重保序,多个换行分隔)
_SHORT_LINK_RE = re.compile(r"https?://[^\s)）】\"'<>]+")


def _extract_links(text: str | None) -> str:
    if not text:
        return ""
    links = []
    for m in _SHORT_LINK_RE.finditer(text):
        u = m.group(0).rstrip(".,;!?。，；！？")
        if u and u not in links:
            links.append(u)
    return "\n".join(links)


def _template_row(o: dict) -> dict:
    """按参考模板从订单 dict 生成一行(内部字段 → 模板列)。"""
    date = (o.get("成交时间") or "").strip()[:10]   # 仅日期: 2026-07-16
    return {
        "日期": date,
        "姓名": o.get("联系人") or "",
        "开卡人": "",
        "团队长": "",
        "归属地": "",
        "学校": "",
        "手机号": o.get("号码") or "",
        "联系电话": o.get("__联系人电话") or "",
        "订单编号": o.get("订单ID") or "",
        "订单状态是否通过审核": o.get("订单状态") or "",
        "运单号": o.get("物流单号") or "",
        "短链": _extract_links(o.get("短链详情") or ""),
        "用户办理方式": "",
        "是否新生": "",
        "套餐": o.get("__产品信息") or "",
        "发出日期": "",
        "激活日期": "",
        "宽带": "",
    }


def _extract_products(text: str) -> list[str]:
    """按行提取产品信息(如「【TZ】 流量王畅享线上版 异网」)。

    前边界:优先取关键词前最近的「【…】」前缀;否则取关键词前最近空格后(套餐名独立成词)。
    后边界:遇到「号码：」「订单状态：」等字段标志即截断。
    """
    out = []
    for line in text.split("\n"):
        for kw in _PRODUCT_KWS:
            i = line.find(kw)
            if i < 0:
                continue
            end = len(line)
            for mk in _PRODUCT_END_MARKS:
                for sep in ("：", ":"):
                    j = line.find(mk + sep, i + len(kw))
                    if j > 0:
                        end = min(end, j)
            lb = line.rfind("【", 0, i)
            if lb >= 0 and i - lb <= 40:
                start = lb
            else:
                sp = line.rfind(" ", 0, i)
                start = sp + 1 if (sp >= 0 and i - sp <= 40) else max(0, i - 30)
            seg = _norm(line[start:end])
            if seg and seg not in out:
                out.append(seg)
            break
    return out


def _mhtml_html_docs(path: str) -> list[str]:
    """从 MHTML 提取所有 text/html 文档文本(主文档 + iframe 文档)。

    若文件不是 MHTML(如普通 .html 保存),按普通 HTML 直接读取兜底。
    """
    try:
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        docs = []
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                raw = part.get_payload(decode=True)
                if raw:
                    try:
                        docs.append(raw.decode("utf-8", errors="replace"))
                    except Exception:
                        pass
        if docs:
            return docs
    except Exception:
        pass
    # 普通 HTML 文件兜底
    try:
        with open(path, "rb") as f:
            return [f.read().decode("utf-8", errors="replace")]
    except Exception:
        return []


def _find_main_table(html: str):
    """在 HTML 中找到含「订单标识」的主表格;未找到返回 None。"""
    soup = BeautifulSoup(html, "lxml")
    for tb in soup.find_all("table"):
        rows = tb.find_all("tr")
        if rows and "订单标识" in rows[0].get_text(" ", strip=True):
            return tb
    return None


def _norm(s: str) -> str:
    s = s.replace("\xa0", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _parse_pairs(text: str) -> dict:
    """把订单块文本解析成 {字段名: 值}。"""
    pairs = {}
    segs = list(_LABEL_RE.finditer(text))
    for i, m in enumerate(segs):
        label = m.group(0).rstrip("：: \u3000").strip()
        end = segs[i + 1].start() if i + 1 < len(segs) else len(text)
        pairs[label] = _norm(text[m.end():end])
    return pairs


def parse_mhtml(path: str) -> list[dict]:
    """解析一个查询结果 MHTML,返回订单列表(每单一个 dict)。"""
    docs = _mhtml_html_docs(path)
    orders = []
    for html in docs:
        tb = _find_main_table(html)
        if tb is None:
            continue
        # 按「订单ID」分组行:包含订单ID的行=新订单;其余为上个订单的延续内容(如短信明细)
        blocks = []
        cur = None
        for tr in tb.find_all("tr"):
            # 只取数据单元格文本,跳过「操作」列(按钮文字如「详 情 详 情」会污染字段值)
            parts = []
            for td in tr.find_all(["td", "th"]):
                t = _norm(td.get_text(" ", strip=True))
                if re.fullmatch(r"(?:详\s*情\s*)+", t):   # 操作列按钮
                    continue
                parts.append(_norm(td.get_text("\n", strip=True)))
            txt = _norm("\n".join(parts))
            if "订单ID" in txt:
                if cur is not None:
                    blocks.append(cur)
                cur = {"text": txt, "tr": tr}
            elif cur is not None and txt:
                cur["text"] += "\n" + txt
        if cur is not None:
            blocks.append(cur)

        for b in blocks:
            text = b["text"]
            pairs = _parse_pairs(text)

            # ---- 短信/服务提醒(完全相同去重,保序) ----
            sms = list(dict.fromkeys(_norm(m.group(0)) for m in _SMS_RE.finditer(text) if m.group(0).strip()))

            # ---- 产品信息 / 证件类型拆分 ----
            # 商品信息单元格格式:「证件类型：{证件类型词}{产品名}」;无证件类型词时整串即产品名
            id_type_raw = _norm(pairs.pop("证件类型", ""))
            id_type = ""
            product = ""
            if id_type_raw:
                m = _ID_TYPE_RE.match(id_type_raw)
                if m:
                    id_type = m.group(1)
                    product = _norm(id_type_raw[len(m.group(1)):])
                else:
                    product = id_type_raw
            if not product:   # 兜底:老格式按关键词法提取
                product = " | ".join(_extract_products(text))

            # 验证码下单 标志
            flag_verify = "是" if "验证码下单" in text else ""

            order = {"__产品信息": product,
                     "__短信提醒": "\n".join(sms),
                     "验证码下单": flag_verify}
            for k, v in pairs.items():
                order[k] = v
            order["证件类型"] = id_type

            # 从普通字段值里剔除 产品名/短信/验证码下单 等噪声(__ 开头的衍生字段除外;
            # 短链详情保留原文,仅去重其中重复的短信段)
            noise = [p for p in (product,) if p] + sms + (["验证码下单"] if flag_verify else [])
            for k in list(order):
                if k.startswith("__") or k == "验证码下单" or k == "短链详情":
                    continue
                v = order[k]
                if isinstance(v, str):
                    for n in noise:
                        if n:
                            v = v.replace(n, "")
                    order[k] = _norm(v)

            # ---- 号码:提取纯 11 位(去掉「(杭州市)」等地区后缀) ----
            num = order.get("号码", "")
            m = _PHONE_RE.search(num)
            if m:
                order["号码"] = m.group(0)

            # ---- 联系人:分离姓名与电话 ----
            contact = order.get("联系人", "")
            m = _PHONE_RE.search(contact)
            if m:
                order["__联系人电话"] = m.group(0)
                order["联系人"] = _norm(contact.replace(m.group(0), ""))

            # ---- 短链详情:去掉其中重复的短信段(保留第一次出现) ----
            detail = order.get("短链详情", "")
            if detail:
                seen = set()
                def _dedup(m):
                    t = m.group(0)
                    if t in seen:
                        return ""
                    seen.add(t)
                    return t
                order["短链详情"] = _norm(_SMS_RE.sub(_dedup, detail))

            # 原始四格内容
            tds = b["tr"].find_all("td")
            for idx, name in enumerate(RAW_CELL_NAMES):
                if idx < len(tds):
                    order[name] = _norm(tds[idx].get_text(" ", strip=True))
            orders.append(order)
    return orders


def export_to_excel(mhtml_files: list[str], out_path: str, log=None,
                    run_stats: dict | None = None,
                    query_by: str = "name",
                    query_order: list[str] | None = None) -> dict:
    """解析多个 MHTML 并输出 Excel。

    Args:
        mhtml_files: MHTML 文件路径列表
        out_path: 输出 Excel 文件路径
        log: Logger 实例(可选)
        run_stats: 运行时统计 dict,传入时在同一工作簿追加「查询报告」工作表
        query_by: name=按姓名(默认) | order=按订单号 | order_phone=按订购号码
        query_order: 按键查询时传入名单列表(保持顺序);无结果保留空行
    Returns:
        dict: {files_total, files_ok, orders, status_dist, per_key_orders}
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    # 按查询键(姓名/订单号)分组文件
    files_by_key = {}
    for fp in mhtml_files:
        base = os.path.basename(fp)
        key = base.split("__")[0] if "__" in base else base
        files_by_key.setdefault(key, []).append(fp)

    # 并行预解析所有去重文件(lxml 为 C 实现,多线程显著提速;单文件解析异常兜底为空)
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    unique_fps = list(dict.fromkeys(mhtml_files))

    def _parse_one(fp):
        try:
            return fp, parse_mhtml(fp)
        except Exception as e:
            if log:
                log.warn(f"解析失败: {os.path.basename(fp)}: {e}")
            return fp, []

    workers = min(8, max(2, (os.cpu_count() or 4)))
    if len(unique_fps) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fp, orders in ex.map(_parse_one, unique_fps):
                results[fp] = orders
    else:
        for fp in unique_fps:
            results[fp] = _parse_one(fp)[1]

    all_rows = []
    ok_files = sum(1 for o in results.values() if o)

    def _parse(fp):
        """按查询键取已解析结果(重复引用直接复用)。"""
        return results[fp]

    is_key_query = query_by in ("order", "order_phone")
    per_key_orders = {}   # 查询键 → 订单行数(报告用)

    def _add_row(key, o):
        """生成一行:模板字段 + (按键查询模式)查询键标识;统计 per_key_orders。"""
        row = _template_row(o)
        if is_key_query:
            row = {"查询订单号": key, **row}
        per_key_orders[key] = per_key_orders.get(key, 0) + 1
        return row

    if is_key_query and query_order:
        # 按名单中的订单号顺序输出;查不到结果的订单号保留一行(仅查询键列有值)
        covered = set()
        for key in query_order:
            key = str(key).strip()
            covered.add(key)
            fplist = files_by_key.get(key, [])
            orders = []
            for fp in fplist:
                orders.extend(_parse(fp))
            if orders:
                for o in orders:
                    all_rows.append(_add_row(key, o))
                if log:
                    log.ok(f"{key}: 解析出 {len(orders)} 个订单")
            else:
                all_rows.append({"查询订单号": key})   # 无结果 → 留空行
                if log:
                    log.warn(f"{key}: 未查到结果(文件无订单或缺失),保留空行")
        # 名单外的多余文件(理论上不应存在)追加,避免数据丢失
        for key, fplist in files_by_key.items():
            if key in covered:
                continue
            for fp in fplist:
                for o in _parse(fp):
                    all_rows.append(_add_row(key, o))
    else:
        for fp in mhtml_files:
            for o in _parse(fp):
                base = os.path.basename(fp)
                key = base.split("__")[0] if "__" in base else base
                all_rows.append(_add_row(key, o))

    # 有效数据行(排除"无结果空行"——仅含查询键标识列的行)
    data_rows = [r for r in all_rows if len(r) > 1]

    # 订单状态分布 + 每查询键订单数
    status_dist = {}
    for r in data_rows:
        st = (r.get("订单状态是否通过审核") or "").strip() or "未知"
        status_dist[st] = status_dist.get(st, 0) + 1

    # 输出列:订单号模式最前加「查询订单号」标识列
    cols = (["查询订单号"] if is_key_query else []) + list(TEMPLATE_COLS)

    wb = Workbook()
    ws = wb.active
    ws.title = "查询结果汇总"
    ws.append(cols)
    # 表头样式
    head_fill = PatternFill("solid", fgColor="2B3A67")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in all_rows:
        ws.append([r.get(c, "") for c in cols])

    # 全部按文本写入(避免订单ID/手机号变科学计数法),自动换行
    text_fmt = "@"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = text_fmt
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    # 列宽(模板列按内容自适应,长文本列给上限;标识列固定)
    width_map = {"查询订单号": 20, "日期": 14, "姓名": 12, "手机号": 14, "联系电话": 14,
                 "订单编号": 20, "订单状态是否通过审核": 16, "运单号": 18, "短链": 36,
                 "套餐": 46, "开卡人": 10, "团队长": 10, "归属地": 10, "学校": 14,
                 "用户办理方式": 12, "是否新生": 10, "发出日期": 10, "激活日期": 10,
                 "宽带": 10}
    for i, c in enumerate(cols, 1):
        w = max((len(str(r.get(c, ""))) for r in all_rows), default=8)
        width = width_map.get(c)
        if width is None:
            width = min(max(w + 4, 10), 50)
        else:
            width = max(width, min(w + 4, width + 10))
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    result = {"files_total": len(mhtml_files), "files_ok": ok_files,
              "orders": len(data_rows), "status_dist": status_dist,
              "per_key_orders": per_key_orders}

    if run_stats:
        run_stats.update(result)
        _add_report_sheet(wb, run_stats, per_key_orders, len(data_rows))

    # 保存(Excel 文件被占用时给出明确提示,重试一次)
    try:
        wb.save(out_path)
    except PermissionError:
        if log:
            log.warn("查询结果汇总.xlsx 被占用(可能正被 Excel/WPS 打开),稍后重试…")
        time.sleep(1.5)
        try:
            wb.save(out_path)
        except PermissionError:
            raise RuntimeError(
                "无法写入 查询结果汇总.xlsx:文件正被其他程序(Excel/WPS)打开。\n"
                "请关闭该文件后,点悬浮窗「导出 Excel」重新生成。")
    return result


def _add_report_sheet(wb, stats: dict, per_key_orders: dict, orders_total: int) -> None:
    """在 Excel 中追加「查询报告」工作表(运行概况 + 各查询键明细 + 订单状态分布)。"""
    from openpyxl.styles import Alignment, Font, PatternFill

    ws = wb.create_sheet("查询报告")
    title_font = Font(bold=True, size=14, color="2B3A67")
    head_fill = PatternFill("solid", fgColor="2B3A67")
    sec_font = Font(bold=True, size=11, color="2B3A67")

    def sec(row, text):
        ws.cell(row=row, column=1, value=text).font = sec_font

    ws.cell(row=1, column=1, value="联通 IBOS 查询报告").font = title_font
    ws.cell(row=2, column=1, value=f"开始时间: {stats.get('start', '')}   结束时间: {stats.get('end', '')}   用时: {stats.get('duration_s', 0)} 秒")
    ws.cell(row=3, column=1, value=f"导出目录: {stats.get('out_dir', '')}")

    # ---- 动态行号跟踪(不再硬编码,overview 条目数变化时自动适配) ----
    r = 5
    sec(r, "一、总览"); r += 1
    overview = [
        ("本次处理查询", f"{stats.get('processed', 0)} 个"),
        ("成功 / 失败", f"{stats.get('ok', 0)} / {stats.get('fail', 0)}"),
        ("保存网页文件", f"{stats.get('files_ok', 0)} / {stats.get('files_total', 0)}"),
        ("解析订单总数", f"{orders_total} 个"),
        ("Excel 汇总文件", "查询结果汇总.xlsx"),
    ]
    for k, v in overview:
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
        r += 1

    r += 1  # 空行
    sec(r, "二、各姓名明细"); r += 1
    ws.cell(row=r, column=1, value="姓名").font = Font(bold=True)
    ws.cell(row=r, column=2, value="状态").font = Font(bold=True)
    ws.cell(row=r, column=3, value="订单数").font = Font(bold=True)
    ws.cell(row=r, column=4, value="备注").font = Font(bold=True)
    r += 1
    for item in stats.get("per_name", []):
        cnt = per_key_orders.get(item["name"], 0)
        note = "" if item["ok"] else (item.get("error", "") or "查询无结果/保存失败")
        ws.cell(row=r, column=1, value=item["name"])
        ws.cell(row=r, column=2, value="成功" if item["ok"] else "失败")
        ws.cell(row=r, column=3, value=cnt)
        ws.cell(row=r, column=4, value=note)
        r += 1

    r += 1  # 空行
    sec(r, "三、订单状态分布"); r += 1
    ws.cell(row=r, column=1, value="订单状态").font = Font(bold=True)
    ws.cell(row=r, column=2, value="数量").font = Font(bold=True)
    r += 1
    for st, cnt in sorted(stats.get("status_dist", {}).items(), key=lambda x: -x[1]):
        ws.cell(row=r, column=1, value=st)
        ws.cell(row=r, column=2, value=cnt)
        r += 1

    for col in ("A", "B", "C", "D"):
        ws.column_dimensions[col].width = 22
    ws.freeze_panes = "A2"


def write_text_report(path: str, stats: dict) -> None:
    """生成纯文本查询报告。

    per_key_orders 可能不在 stats 中(如导出失败但仍生成报告时),
    此处从 per_name 统计作为兜底,确保报告始终有有效数据。
    """
    lines = []
    lines.append("=" * 44)
    lines.append("      联通 IBOS 查询报告")
    lines.append("=" * 44)
    lines.append(f"开始时间: {stats.get('start', '')}")
    lines.append(f"结束时间: {stats.get('end', '')}")
    lines.append(f"运行时长: {stats.get('duration_s', 0)} 秒")
    lines.append(f"导出目录: {stats.get('out_dir', '')}")
    lines.append("")
    lines.append("【一、总览】")
    lines.append(f"  本次处理: {stats.get('processed', 0)} 个")
    lines.append(f"  成功: {stats.get('ok', 0)}    失败: {stats.get('fail', 0)}")
    lines.append(f"  保存网页: {stats.get('files_ok', 0)}/{stats.get('files_total', 0)} 个")
    lines.append(f"  解析订单: {stats.get('orders', 0)} 个")
    lines.append(f"  Excel: 查询结果汇总.xlsx")
    lines.append("")
    # per_key_orders 兜底:不在 stats 中时用空 dict,订单数显示 0(导出未完成时合理)
    per_key = stats.get("per_key_orders") or {}
    lines.append("【二、各姓名明细】")
    lines.append(f"  {'姓名':<8}{'状态':<6}{'订单数':<6}备注")
    for item in stats.get("per_name", []):
        cnt = per_key.get(item["name"], 0)
        note = "" if item["ok"] else (item.get("error", "") or "查询无结果/保存失败")
        lines.append(f"  {item['name']:<8}{'成功' if item['ok'] else '失败':<6}{cnt:<6}{note}")
    lines.append("")
    lines.append("【三、订单状态分布】")
    for st, cnt in sorted(stats.get("status_dist", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {st}: {cnt} 单")
    lines.append("")
    lines.append(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def find_result_files(output_dir: str = "导出结果") -> list[str]:
    """查找所有查询结果网页文件,兼容 .mhtml/.html/.htm 三种保存格式。"""
    files = []
    for ext in (".mhtml", ".html", ".htm"):
        files.extend(glob.glob(os.path.join(output_dir, "*__查询结果" + ext)))
    return sorted(files)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.logger import Logger

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(base_dir, "导出结果")
    files = find_result_files(out_dir)
    if not files:
        print("未找到 导出结果/*__查询结果.mhtml 文件")
        sys.exit(1)
    out = os.path.join(out_dir, "查询结果汇总.xlsx")
    res = export_to_excel(files, out, Logger())
    print(f"完成: {res['files_ok']}/{res['files_total']} 个文件,共 {res['orders']} 个订单 → {out}")
