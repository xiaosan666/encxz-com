#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「英语初学者 A1」短视频封面。

版式参考用户提供的示例缩略图：上方人物照片 + 下方浅色文字带（左侧超大等级 A1，右侧文字块）。

用法：
    .venv/bin/python tools/make_cover.py                      # 默认参考照片 → 16:9 与 4:3
    .venv/bin/python tools/make_cover.py --photo 高清原图.jpg   # 换高清原图（含/不含文字带均可）
    .venv/bin/python tools/make_cover.py --level A2 --title 英语初级 ...

设计比例全部以「文字带高度」为基准推导，因此 16:9 与 4:3 两种尺寸视觉一致。
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- 参考图实测比例
BAND_FRAC = 111 / 330        # 文字带高度 / 画面高度
A1_LEFT = 0.280              # A1 左边距 / 带高
A1_INK_H = 0.676             # A1 字面高度 / 带高
A1_INK_W = 0.946             # A1 字面宽度 / 带高
BLOCK_GAP = 0.279            # A1 与右侧文字块间距 / 带高
BLOCK_PAD_R = 0.171          # 右侧留白 / 带高
BLOCK_PAD_T = 0.126          # 文字块顶部留白 / 带高
BLOCK_PAD_B = 0.108          # 文字块底部留白 / 带高
TITLE_MAX = 0.44             # 标题字面高度上限 / A1 字面高度

BAND_COLOR = (219, 239, 214)  # 示例图取样：A1 浅绿
INK = (10, 10, 10)

FONT_A1 = ("/System/Library/Fonts/HelveticaNeue.ttc", 9)          # Condensed Black（贴近示例）
FONT_CN_BOLD = ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2)   # W6

DEFAULT_PHOTO = (
    "/Users/xiaojun/.dsh/attachments/v1/objects/8a/"
    "8a4b24aedcde82a4125b6e40dcd6446b3b9852581e3f6df6b0e36dbcef8e3fc3"
)


@dataclass
class Spec:
    name: str
    width: int
    height: int


SPECS = [Spec("16x9", 1920, 1080), Spec("4x3", 1440, 1080)]


# ---------------------------------------------------------------- 基础工具
def load_font(spec, size):
    path, index = spec
    return ImageFont.truetype(path, size, index=index)


def text_ink(font, text, fill=INK):
    """渲染文字并裁到字面（ink）边界，返回 RGBA。"""
    pad = max(8, font.size // 4)
    l, t, r, b = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - l, pad - t), text, font=font, fill=fill + (255,))
    return img.crop(img.getbbox())


def text_block(rows):
    """rows: [(文字, 字体, 与上一行间距), ...] → 左对齐、裁到 ink 的整块文字。"""
    parts = [(text_ink(font, text), gap) for text, font, gap in rows]
    width = max(p.width for p, _ in parts)
    height = sum(p.height + gap for p, gap in parts)
    block = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = 0
    for ink, gap in parts:
        y += gap
        block.alpha_composite(ink, (0, y))
        y += ink.height
    return block.crop(block.getbbox())


def fit_min(img, box_w, box_h, scale_cap=None):
    """等比缩放到框内。"""
    scale = min(box_w / img.width, box_h / img.height)
    if scale_cap:
        scale = min(scale, scale_cap)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def fit_exact(img, box_w, box_h):
    """拉伸到恰好填满目标框（用于把等级字对齐示例图的字面比例）。"""
    return img.resize((max(1, round(box_w)), max(1, round(box_h))), Image.LANCZOS)


# ---------------------------------------------------------------- 照片处理
def detect_band_top(im: Image.Image) -> int:
    """自动检测参考图底部文字带的起始行（左下角为纯净底色）。"""
    a = np.asarray(im.convert("RGB")).astype(int)
    h, w, _ = a.shape
    probe = a[:, : max(4, int(w * 0.09))]
    top = h
    for y in range(h - 1, h // 2, -1):
        if probe[y].std() < 6 and probe[y].mean() > 185:
            top = y
        else:
            break
    return top if top < h else int(h * 0.664)


def compose_backdrop(canvas: Image.Image, photo: Image.Image, band_top: int, zoom: float, top_frac: float = 0.06):
    """照片按「文字带高度 × zoom」缩放并水平居中，四周用边缘像素延展，做到满幅无缝。"""
    target_h = round(band_top * zoom)
    scale = target_h / photo.height
    pw = round(photo.width * scale)
    big = photo.resize((pw, target_h), Image.LANCZOS)
    big = big.filter(ImageFilter.UnsharpMask(radius=3, percent=70, threshold=3))

    x0 = max(0, (canvas.width - pw) // 2)
    strip = Image.new("RGB", (canvas.width, target_h))
    if x0:
        strip.paste(big.crop((0, 0, 1, target_h)).resize((x0, target_h), Image.BILINEAR), (0, 0))
    right_w = canvas.width - x0 - pw
    if right_w > 0:
        strip.paste(big.crop((pw - 1, 0, pw, target_h)).resize((right_w, target_h), Image.BILINEAR), (x0 + pw, 0))
    strip.paste(big, (x0, 0))

    y0 = round(canvas.height * top_frac)
    if y0 > 0:  # 上方用照片首行像素纵向延展
        canvas.paste(strip.crop((0, 0, canvas.width, 1)).resize((canvas.width, y0), Image.BILINEAR), (0, 0))
    canvas.paste(strip, (0, y0))
    bottom = canvas.height - y0 - target_h
    if bottom > 0:
        canvas.paste(strip.crop((0, target_h - 1, canvas.width, target_h)).resize((canvas.width, bottom), Image.BILINEAR),
                     (0, y0 + target_h))
    return scale


# ---------------------------------------------------------------- 封面合成
def build(src_path, spec: Spec, level, title, sub, out_dir, band=True, zoom=1.25, top_frac=0.06):
    W, H = spec.width, spec.height
    band_h = round(H * BAND_FRAC)
    band_top = H - band_h

    # 1) 人物照片铺底
    src = Image.open(src_path).convert("RGB")
    top = detect_band_top(src) if band else src.height
    photo = src.crop((0, 0, src.width, top))
    canvas = Image.new("RGB", (W, H), BAND_COLOR)
    scale = compose_backdrop(canvas, photo, band_top, zoom, top_frac)
    print(f"  [{spec.name}] {W}x{H} 带高 {band_h} | 照片 {photo.width}x{photo.height} ×{scale:.2f}")

    # 2) 文字带
    ImageDraw.Draw(canvas).rectangle([0, band_top, W, H], fill=BAND_COLOR)

    # 3) 左侧超大等级
    a1_w, a1_h = round(A1_INK_W * band_h), round(A1_INK_H * band_h)
    a1 = fit_exact(text_ink(load_font(FONT_A1, a1_h * 2), level), a1_w, a1_h)
    a1_x = round(A1_LEFT * band_h)
    a1_y = band_top + (band_h - a1.height) // 2 + round(band_h * 0.012)

    # 4) 右侧标题块（两级字号，整体等比缩放）
    box_x = a1_x + a1.width + round(BLOCK_GAP * band_h)
    box_w = W - box_x - round(BLOCK_PAD_R * band_h)
    box_y = band_top + round(BLOCK_PAD_T * band_h)
    box_h = band_h - round((BLOCK_PAD_T + BLOCK_PAD_B) * band_h)

    base = 100
    f_title = load_font(FONT_CN_BOLD, base)
    block = text_block([
        (title, f_title, 0),
        (sub, load_font(FONT_CN_BOLD, round(base * 0.55)), round(base * 0.26)),
    ])
    cap = (TITLE_MAX * a1_h) / text_ink(f_title, title).height
    block = fit_min(block, box_w, box_h, scale_cap=cap)
    canvas.paste(a1, (a1_x, a1_y), a1)
    canvas.paste(block, (box_x + (box_w - block.width) // 2, box_y + (box_h - block.height) // 2), block)
    print(f"       A1 {a1.width}x{a1.height} | 标题块 {block.width}x{block.height}")

    # 5) 输出
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, f"{level}-{spec.name}-cover.png")
    jpg = os.path.join(out_dir, f"{level}-{spec.name}-cover.jpg")
    canvas.save(png)
    canvas.save(jpg, quality=93, subsampling=0, optimize=True)
    for p in (png, jpg):
        print(f"  ✓ {p}  ({os.path.getsize(p) / 1024:.0f} KB)")
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", default=DEFAULT_PHOTO, help="人物照片（含示例文字带会自动裁掉）")
    ap.add_argument("--no-band", action="store_true", help="照片本身不含文字带")
    ap.add_argument("--level", default="A1")
    ap.add_argument("--title", default="英语初学者")
    ap.add_argument("--sub", default="零基础 · 1分钟英语")
    ap.add_argument("--out-dir", default="output/covers")
    ap.add_argument("--zoom", type=float, default=1.25, help="人物大小：照片高 / 文字带顶部高度")
    ap.add_argument("--top", type=float, default=0.06, help="照片顶部留白 / 画面高度")
    args = ap.parse_args()

    for spec in SPECS:
        build(args.photo, spec, args.level, args.title, args.sub,
              args.out_dir, band=not args.no_band, zoom=args.zoom, top_frac=args.top)


if __name__ == "__main__":
    main()
