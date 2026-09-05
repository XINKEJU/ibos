# -*- coding: utf-8 -*-
"""生成 IBOS 批量查询助手 的 macOS 应用图标。

设计语义
--------
- Squircle 外形:  macOS Big Sur+ 规范超椭圆 (|x|^n + |y|^n = 1, n=4.2)
- 蓝色渐变底:    联通/系统蓝, 顶部亮 -> 底部深, 营造体积感
- 白色数据卡片:  订单表格 (表头条 + 4 条数据行), 代表批量订单数据
- 放大镜:        查询语义, 与卡片重叠处做 knock-out 描边 (图标设计标准手法)

产出
----
assets/AppIcon.png      1024x1024 主图
assets/AppIcon.icns     macOS 图标包 (Dock / 访达 / 启动台)
assets/icon_128.png     运行时窗口标题栏备用
"""
import os
import subprocess

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(BASE, "assets")
ICONSET = os.path.join(ASSETS, "AppIcon.iconset")

SIZE = 1024          # 输出尺寸
SS = 4               # 超采样倍数 (4x -> 4096 画布后 LANCZOS 下采样, 边缘无锯齿)
S = SIZE * SS

# ---------------------------------------------------------------- 配色
GRAD_TOP = (90, 168, 255)      # #5AA8FF 亮天蓝
GRAD_BOTTOM = (14, 62, 190)    # #0E3EBE 深联通蓝
WHITE = (255, 255, 255)
HL_PEAK = 30                   # 顶部高光峰值 alpha


def k(v: float) -> float:
    """1024 基准坐标 -> 超采样画布坐标。"""
    return v * SS


def squircle_mask(size: int, n: float = 4.2) -> Image.Image:
    """超椭圆 squircle 遮罩。逐行解析求 x 边界, 避免逐像素 O(n^2)。"""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    c = size / 2.0
    r = size / 2.0
    for y in range(size):
        dy = abs(y - c + 0.5) / r
        if dy >= 1.0:
            continue
        dx = (1.0 - dy ** n) ** (1.0 / n)
        x0 = int(round(c - dx * r))
        x1 = int(round(c + dx * r))
        if x1 >= x0:
            d.line([(x0, y), (x1, y)], fill=255)
    return m


def vertical_gradient(size: int, top, bottom) -> Image.Image:
    """垂直线性渐变: 先生成 1 x size 色带再横向拉伸, 高效且平滑。"""
    strip = Image.new("RGB", (1, size))
    px = strip.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return strip.resize((size, size), Image.Resampling.BILINEAR)


def top_highlight(size: int, peak: int = HL_PEAK) -> Image.Image:
    """顶部柔光层 (macOS 图标惯例): 白色 alpha 自上而下衰减。"""
    hl = Image.new("L", (1, size), 0)
    px = hl.load()
    fade_end = int(size * 0.52)
    for y in range(size):
        if y >= fade_end:
            continue
        t = y / fade_end
        px[0, y] = int(peak * (1.0 - t) ** 1.6)
    return hl.resize((size, size), Image.Resampling.BILINEAR)


def draw_foreground(size: int) -> Image.Image:
    """前景层 (RGBA, 初始全透明): 数据卡片 + 带 knock-out 描边的放大镜。"""
    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)

    # ---- 数据卡片 ----
    cl, ct, cr, cb = k(228), k(176), k(672), k(704)
    d.rounded_rectangle([cl, ct, cr, cb], radius=k(54), fill=WHITE + (255,))

    # 表头条 (品牌蓝, 主层级; 白底上需用对比色才可见)
    d.rounded_rectangle([k(276), k(268), k(520), k(308)], radius=k(20),
                        fill=(32, 88, 224, 255))
    # 数据行 (半透, 次层级)
    for y in (380, 470, 560, 650):
        d.rounded_rectangle([k(276), k(y), k(624), k(y + 22)],
                            radius=k(11), fill=WHITE + (110,))

    # ---- 放大镜 (knock-out 描边: 先挖掉背景色间隙, 再画白色本体) ----
    cx, cy = k(688), k(676)
    ring_out, ring_in = k(148), k(114)      # 外半径 / 内半径 -> 环宽 34
    gap = k(26)                             # 挖空描边宽度

    import math
    ang = math.radians(45)
    ux, uy = math.cos(ang), math.sin(ang)

    def handle(draw, start_r, end_r, width, fill):
        """沿 45 度方向画圆头粗线作为放大镜手柄。"""
        x1 = cx + ux * start_r
        y1 = cy + uy * start_r
        x2 = cx + ux * end_r
        y2 = cy + uy * end_r
        draw.line([(x1, y1), (x2, y2)], fill=fill, width=width)
        rad = width / 2.0
        for (px_, py_) in ((x1, y1), (x2, y2)):
            draw.ellipse([px_ - rad, py_ - rad, px_ + rad, py_ + rad], fill=fill)

    # 1) 挖空: 圆 + 手柄, alpha=0 -> 露出渐变底, 形成分隔描边
    erase = (0, 0, 0, 0)
    d.ellipse([cx - ring_out - gap, cy - ring_out - gap,
               cx + ring_out + gap, cy + ring_out + gap], fill=erase)
    handle(d, k(120), k(232), k(42) + gap * 2, erase)

    # 2) 白色本体: 实心圆 -> 挖内圈成环
    d.ellipse([cx - ring_out, cy - ring_out, cx + ring_out, cy + ring_out],
              fill=WHITE + (255,))
    d.ellipse([cx - ring_in, cy - ring_in, cx + ring_in, cy + ring_in],
              fill=erase)
    handle(d, k(120), k(232), k(42), WHITE + (255,))

    return fg


def build(size: int = SIZE) -> Image.Image:
    """合成最终图标: 渐变底 -> 前景 -> 顶部高光 -> squircle 裁切。"""
    ss = size * SS
    base = vertical_gradient(ss, GRAD_TOP, GRAD_BOTTOM).convert("RGBA")

    # 顶部高光
    hl = top_highlight(ss)
    gloss = Image.new("RGBA", (ss, ss), WHITE + (255,))
    gloss.putalpha(hl)
    base = Image.alpha_composite(base, gloss)

    # 前景元素
    base = Image.alpha_composite(base, draw_foreground(ss))

    # squircle 裁切 (alpha 通道相乘)
    mask = squircle_mask(ss)
    a = base.split()[3]
    base.putalpha(ImageChops_multiply(a, mask))

    return base.resize((size, size), Image.Resampling.LANCZOS)


def ImageChops_multiply(a: Image.Image, b: Image.Image) -> Image.Image:
    """两个 L 通道相乘 (避免额外 import ImageChops)。"""
    from PIL import ImageChops
    return ImageChops.multiply(a, b)


def make_iconset(src: Image.Image) -> None:
    """生成 .iconset 全部尺寸, 再用 iconutil 打包为 .icns。"""
    os.makedirs(ICONSET, exist_ok=True)
    # (文件名, 像素尺寸)
    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    for name, px in specs:
        src.resize((px, px), Image.Resampling.LANCZOS).save(
            os.path.join(ICONSET, name))

    icns = os.path.join(ASSETS, "AppIcon.icns")
    if os.path.exists(icns):
        os.remove(icns)
    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", icns], check=True)
    print(f"[OK] {icns}")


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    icon = build(SIZE)

    png = os.path.join(ASSETS, "AppIcon.png")
    icon.save(png)
    print(f"[OK] {png}  ({SIZE}x{SIZE})")

    small = os.path.join(ASSETS, "icon_128.png")
    icon.resize((128, 128), Image.Resampling.LANCZOS).save(small)
    print(f"[OK] {small}")

    make_iconset(icon)


if __name__ == "__main__":
    main()
