#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
encxz.com / 英语初学者  —— 清新风格 Logo 资产生成器

一次定义几何形状，同时输出:
  * 真正的矢量 SVG (文字已转曲, 不依赖字体)
  * 高清 PNG (4x 超采样 + LANCZOS 缩放)
  * 多尺寸 favicon.ico

风格参考: assets/temp风格.jpg (夏日柠檬苏打: 晴空蓝 + 柠檬黄 + 白 + 水光)
"""

from __future__ import annotations

import math
import os
import re
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

# ---------------------------------------------------------------- 品牌定义 ---

BRAND = {
    "domain": "encxz.com",
    "wordmark": "encxz",
    "tagline": "英语初学者",
}

# 清新调色板 (取自参考图的晴空蓝 / 柠檬黄 / 叶绿 / 水白)
C_SKY_HI = "#5FD0F5"   # 晴空蓝(亮)
C_SKY_LO = "#1A83D4"   # 晴空蓝(深)
C_LEMON = "#FFD335"    # 柠檬黄
C_LEMON_HI = "#FFE886"  # 柠檬高光
C_LEAF = "#6FD05A"     # 嫩叶绿
C_LEAF_LO = "#3FA83F"
C_INK = "#1780C8"      # 字标蓝
C_INK_SOFT = "#6C8399"  # 中文副标灰蓝
C_WHITE = "#FFFFFF"

# 字体
F_ROUND = "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"
F_CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"

SS = 4  # 超采样倍数


# ------------------------------------------------------------ 路径工具函数 ---

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _tokenize(d: str):
    return re.findall(r"[MmLlCcQqZzHhVv]|" + _NUM.pattern, d)


def parse_path(d: str):
    """把 SVG path 解析成若干子路径: [[(kind, [pts...]), ...], ...] (绝对坐标)"""
    toks = _tokenize(d)
    i = 0
    subs: list[list] = []
    cur: list = []
    cx = cy = 0.0
    sx = sy = 0.0
    cmd = None

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append(("Z", []))
                    subs.append(cur)
                    cur = []
                cx, cy = sx, sy
                continue
        if cmd is None:
            raise ValueError("bad path: " + d[:40])
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            if cur:
                cur.append(("Z", []))
                subs.append(cur)
                cur = []
            cur.append(("M", [(x, y)]))
            cx, cy = sx, sy = x, y
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            cur.append(("L", [(x, y)]))
            cx, cy = x, y
        elif c == "H":
            x = num()
            if rel:
                x = cx + x
            cur.append(("L", [(x, cy)]))
            cx = x
        elif c == "V":
            y = num()
            if rel:
                y = cy + y
            cur.append(("L", [(cx, y)]))
            cy = y
        elif c == "C":
            p = [num() for _ in range(6)]
            if rel:
                p = [p[0] + cx, p[1] + cy, p[2] + cx, p[3] + cy, p[4] + cx, p[5] + cy]
            cur.append(("C", [(p[0], p[1]), (p[2], p[3]), (p[4], p[5])]))
            cx, cy = p[4], p[5]
        elif c == "Q":
            p = [num() for _ in range(4)]
            if rel:
                p = [p[0] + cx, p[1] + cy, p[2] + cx, p[3] + cy]
            cur.append(("Q", [(p[0], p[1]), (p[2], p[3])]))
            cx, cy = p[2], p[3]
        else:
            raise ValueError("unsupported command " + cmd)
    if cur:
        cur.append(("Z", []))
        subs.append(cur)
    return subs


def xform(subs, s: float, tx: float, ty: float, flip=True):
    """缩放+平移; flip=True 表示 y 轴翻转 (字体坐标 -> 屏幕坐标)"""
    out = []
    for sp in subs:
        ns = []
        for kind, pts in sp:
            np_ = [(x * s + tx, (-y * s + ty) if flip else (y * s + ty)) for x, y in pts]
            ns.append((kind, np_))
        out.append(ns)
    return out


def to_svg_d(subs) -> str:
    parts = []
    for sp in subs:
        for kind, pts in sp:
            if kind == "M":
                parts.append(f"M{pts[0][0]:.2f} {pts[0][1]:.2f}")
            elif kind == "L":
                parts.append(f"L{pts[0][0]:.2f} {pts[0][1]:.2f}")
            elif kind == "C":
                parts.append("C" + " ".join(f"{x:.2f} {y:.2f}" for x, y in pts))
            elif kind == "Q":
                parts.append("Q" + " ".join(f"{x:.2f} {y:.2f}" for x, y in pts))
            else:
                parts.append("Z")
    return "".join(parts)


def flatten(subs, steps: int = 14):
    """贝塞尔离散成多边形点列 (供 PIL 绘制)"""
    polys = []
    for sp in subs:
        cur: list[tuple[float, float]] = []
        start = None
        last = None
        for kind, pts in sp:
            if kind == "M":
                if len(cur) > 2:
                    polys.append(cur)
                cur = [pts[0]]
                start = pts[0]
                last = pts[0]
            elif kind == "L":
                cur.append(pts[0]); last = pts[0]
            elif kind == "Q":
                p0, p1 = last, pts[0]
                p2 = pts[1]
                for i in range(1, steps + 1):
                    t = i / steps
                    mt = 1 - t
                    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
                    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
                    cur.append((x, y))
                last = p2
            elif kind == "C":
                p0, p1, p2 = last, pts[0], pts[1]
                p3 = pts[2]
                for i in range(1, steps + 1):
                    t = i / steps
                    mt = 1 - t
                    x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
                         + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
                    y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
                         + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
                    cur.append((x, y))
                last = p3
            elif kind == "Z":
                if start and len(cur) > 2:
                    polys.append(cur + [start])
                cur = []
        if len(cur) > 2:
            polys.append(cur)
    return polys


# ------------------------------------------------------------- 文字转曲 ----

_font_cache: dict = {}


def _face(path: str, want: str = ""):
    key = (path, want)
    if key in _font_cache:
        return _font_cache[key]
    idx = 0
    if path.endswith(".ttc"):
        best = None
        for n in range(12):
            try:
                f = TTFont(path, fontNumber=n, lazy=True)
            except Exception:
                break
            cmap = f.getBestCmap() or {}
            if all(ord(c) in cmap for c in want if not c.isspace()):
                style = str(f["name"].getDebugName(2) or "")
                score = 1 + ("Bold" in style or "W6" in style or "W7" in style)
                if best is None or score > best[0]:
                    best = (score, n)
            if best and best[0] > 1:
                break
        idx = best[1] if best else 0
    face = TTFont(path, fontNumber=idx)
    _font_cache[key] = face
    return face


def text_paths(path: str, text: str, size: float, tracking: float = 0.0, cjk_index: int | None = None):
    """返回 (subs, total_width)，subs 已转换到: baseline 在 y=0, 向下为 +y"""
    face = _face(path, text)
    upem = face["head"].unitsPerEm
    gs = face.getGlyphSet()
    cmap = face.getBestCmap()
    s = size / upem
    subs_all = []
    x = 0.0
    for ch in text:
        gname = cmap.get(ord(ch))
        if gname is None:
            x += size * 0.5 + tracking
            continue
        pen = SVGPathPen(gs)
        gs[gname].draw(pen)
        d = pen.getCommands()
        if d.strip():
            subs_all += xform(parse_path(d), s, x, 0.0, flip=True)
        x += gs[gname].width * s + tracking
    return subs_all, x - (tracking if text else 0)


# --------------------------------------------------------------- 形状定义 ---

K = 0.5522847498  # 90° 圆弧的贝塞尔近似系数


def rrect_path(x, y, w, h, r):
    """圆角矩形 (顺时针, 用三次贝塞尔代替弧线命令, 保证 SVG/PIL 几何完全一致)"""
    k = r * K
    x1, y1, x2, y2 = x + w, y + h, x + w - r, y + h - r
    return (
        f"M{x + r} {y} "
        f"L{x2} {y} C{x2 + k} {y} {x1} {y + r - k} {x1} {y + r} "
        f"L{x1} {y2} C{x1} {y2 + k} {x2 + k} {y1} {x2} {y1} "
        f"L{x + r} {y1} C{x + r - k} {y1} {x} {y2 + k} {x} {y2} "
        f"L{x} {y + r} C{x} {y + r - k} {x + r - k} {y} {x + r} {y} Z"
    )


def circle_path(cx, cy, r):
    k = r * K
    return (
        f"M{cx} {cy - r} "
        f"C{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} "
        f"C{cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} "
        f"C{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} "
        f"C{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} Z"
    )


def leaf_path(cx, cy, w, h, rot_deg):
    """叶子/水滴: 两段二次贝塞尔"""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)

    def P(dx, dy):
        return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

    tip = P(0, -h / 2)
    bot = P(0, h / 2)
    lft = P(-w / 2, 0)
    rgt = P(w / 2, 0)
    return (f"M{tip[0]:.2f} {tip[1]:.2f} "
            f"Q{lft[0]:.2f} {lft[1]:.2f} {bot[0]:.2f} {bot[1]:.2f} "
            f"Q{rgt[0]:.2f} {rgt[1]:.2f} {tip[0]:.2f} {tip[1]:.2f} Z")


def ellipse_path(cx, cy, rx, ry):
    kx, ky = rx * K, ry * K
    return (
        f"M{cx} {cy - ry} "
        f"C{cx + kx} {cy - ry} {cx + rx} {cy - ky} {cx + rx} {cy} "
        f"C{cx + rx} {cy + ky} {cx + kx} {cy + ry} {cx} {cy + ry} "
        f"C{cx - kx} {cy + ry} {cx - rx} {cy + ky} {cx - rx} {cy} "
        f"C{cx - rx} {cy - ky} {cx - kx} {cy - ry} {cx} {cy - ry} Z"
    )


def subs_bbox(subs):
    xs, ys = [], []
    for sp in subs:
        for kind, pts in sp:
            for x, y in pts:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def star_path(cx, cy, r_out, r_in, points=4, rot=0.0):
    pts = []
    for i in range(points * 2):
        ang = math.radians(rot + i * 180 / points - 90)
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"


# ------------------------------------------------------------- 徽标(图标) ---

def build_mark(size: float = 512.0, tile=True):
    """
    徽标: 蓝天气泡(e) + 柠檬星光 + 嫩叶萌芽
    坐标系: size x size, 原点左上。fill 可以是颜色或 'grad:sky' / 'grad:leaf'
    """
    u = size / 512.0
    S = []

    if tile:
        # 对话气泡: 圆角方 (语言学习), 渐变填充
        S.append((rrect_path(66 * u, 84 * u, 380 * u, 348 * u, 104 * u), "grad:sky", []))
        # 尾巴: 从气泡左下角自然延伸的水滴形
        S.append((
            f"M{132 * u:.2f} {382 * u:.2f} "
            f"C{100 * u:.2f} {424 * u:.2f} {84 * u:.2f} {458 * u:.2f} {86 * u:.2f} {492 * u:.2f} "
            f"C{114 * u:.2f} {470 * u:.2f} {146 * u:.2f} {452 * u:.2f} {196 * u:.2f} {436 * u:.2f} "
            f"L{220 * u:.2f} {432 * u:.2f} Z", "grad:sky", []))
        # 水面高光 (柔边椭圆)
        S.append((ellipse_path(250 * u, 152 * u, 138 * u, 58 * u), "#FFFFFF",
                  ['opacity="0.22"', 'blur="16"']))

    # 字母 e (Arial Rounded Bold) —— 白色, 按实际字框精确居中
    e_size = 236 * u
    subs, _ = text_paths(F_ROUND, "e", e_size)
    bx0, by0, bx1, by1 = subs_bbox(subs)
    S.append((to_svg_d(xform(subs, 1.0,
                             248 * u - (bx0 + bx1) / 2,
                             262 * u - (by0 + by1) / 2, flip=False)), "#FFFFFF", []))

    # 柠檬四角星 (清新亮片)
    S.append((star_path(360 * u, 352 * u, 40 * u, 10 * u, 4, 0), C_LEMON, []))
    # 嫩叶 (成长, 从气泡右上角探出)
    S.append((leaf_path(414 * u, 118 * u, 92 * u, 150 * u, -34), C_LEAF, []))
    S.append((leaf_path(410 * u, 126 * u, 54 * u, 96 * u, -34), C_LEAF_LO, ['opacity="0.28"']))
    return S


# ------------------------------------------------------------ 渲染 (PIL) ---

def _grad_rgb(size, c0, c1, diag=True):
    import numpy as np
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype("float32")
    t = (x / max(w - 1, 1) * 0.55 + y / max(h - 1, 1) * 0.45) if diag else (y / max(h - 1, 1))
    a = np.array([int(c0[i:i + 2], 16) for i in (1, 3, 5)], dtype="float32")
    b = np.array([int(c1[i:i + 2], 16) for i in (1, 3, 5)], dtype="float32")
    img = a[None, None, :] * (1 - t[..., None]) + b[None, None, :] * t[..., None]
    return Image.fromarray(img.astype("uint8"), "RGB")


def render(shapes, W: int, H: int, ss: int = SS, src_size: float | None = None):
    """把形状列表渲染成 RGBA 图 (W*ss, H*ss) 后降采样。
    src_size: 形状自身坐标系边长; 为空表示形状已按目标尺寸定义。"""
    w, h = W * ss, H * ss
    k = (w / src_size) if src_size else float(ss)
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for d, fill, attrs in shapes:
        if fill == "none":
            continue
        subs = xform(parse_path(d), k, 0, 0, flip=False)
        polys = flatten(subs, steps=max(6, ss * 3))

        def _area(p):
            a = 0.0
            for i in range(len(p)):
                x0, y0 = p[i]
                x1, y1 = p[(i + 1) % len(p)]
                a += x0 * y1 - x1 * y0
            return a / 2.0

        cont = [(_area(p), p) for p in polys if len(p) >= 3]
        if not cont:
            continue
        # 最大面积轮廓 = 外轮廓; 与其同向的填充, 反向的挖空 (nonzero winding)
        outer_sign = 1.0 if max(cont, key=lambda t: abs(t[0]))[0] > 0 else -1.0
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        for a, p in sorted(cont, key=lambda t: -abs(t[0])):
            same = (a > 0) == (outer_sign > 0)
            md.polygon([(float(px), float(py)) for px, py in p], fill=255 if same else 0)
        if fill == "grad:sky":
            layer = _grad_rgb((w, h), C_SKY_HI, C_SKY_LO)
        elif fill == "grad:leaf":
            layer = _grad_rgb((w, h), C_LEAF, C_LEAF_LO)
        else:
            layer = Image.new("RGB", (w, h), fill)
        at = " ".join(attrs)
        if "blur=" in at:
            mask = mask.filter(ImageFilter.GaussianBlur(
                float(re.search(r'blur="([\d.]+)"', at).group(1)) * k))
        if "opacity=" in at:
            op = float(re.search(r'opacity="([\d.]+)"', at).group(1))
            mask = mask.point(lambda v, o=op: int(v * o))
        base.paste(layer, (0, 0), mask)
    return base.resize((W, H), Image.LANCZOS)


# --------------------------------------------------------------- SVG 输出 ---

def shapes_to_svg(shapes, W, H, extra_defs="", bg=None):
    defs = [
        '<linearGradient id="skyG" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" stop-color="{C_SKY_HI}"/>'
        f'<stop offset="100%" stop-color="{C_SKY_LO}"/></linearGradient>',
        '<linearGradient id="leafG" x1="0%" y1="0%" x2="60%" y2="100%">'
        f'<stop offset="0%" stop-color="{C_LEAF}"/>'
        f'<stop offset="100%" stop-color="{C_LEAF_LO}"/></linearGradient>',
    ]
    body = []
    for d, fill, attrs in shapes:
        if fill == "none":
            continue
        f = {"grad:sky": "url(#skyG)", "grad:leaf": "url(#leafG)"}.get(fill, fill)
        a = (" " + " ".join(attrs)) if attrs else ""
        body.append(f'  <path d="{d}" fill="{f}"{a}/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{BRAND["wordmark"]} {BRAND["tagline"]}">\n'
        f'  <title>{BRAND["wordmark"]} · {BRAND["tagline"]} ({BRAND["domain"]})</title>\n'
        f'  <defs>\n    ' + "\n    ".join(defs) + extra_defs + "\n  </defs>\n"
        + (f'  <rect width="{W}" height="{H}" fill="{bg}"/>\n' if bg else "")
        + "\n".join(body) + "\n</svg>\n"
    )


# -------------------------------------------------------- 组合: 顶部横版 ---

def shapes_bbox(shapes):
    x0 = y0 = 1e9
    x1 = y1 = -1e9
    for d, f, a in shapes:
        for sp in parse_path(d):
            for kind, pts in sp:
                for x, y in pts:
                    x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    return x0, y0, x1, y1


def header_lockup(mark_size=176, one_line=False, pad=16, gap=38):
    """
    横版 lockup (左上角网站 logo):
      两行:  [气泡徽标]  encxz
                        英语初学者
      一行:  [气泡徽标]  encxz.com
    返回 (shapes, W, H)，画布已按墨迹自动收紧、四周留白 pad。
    """
    m = mark_size
    shapes = list(build_mark(m))
    x_text = m + gap

    if one_line:
        subs, w = text_paths(F_ROUND, BRAND["domain"], 134, tracking=0)
        b = subs_bbox(subs)
        shapes.append((to_svg_d(xform(subs, 1, x_text, m / 2 - (b[1] + b[3]) / 2, flip=False)),
                       C_INK, []))
    else:
        subs_en, w_en = text_paths(F_ROUND, BRAND["wordmark"], 156, tracking=3)
        subs_cn, w_cn = text_paths(F_CJK, BRAND["tagline"], 58, tracking=9)
        be, bc = subs_bbox(subs_en), subs_bbox(subs_cn)
        eh, ch = be[3] - be[1], bc[3] - bc[1]
        vgap = 30.0
        top = (m - (eh + vgap + ch)) / 2
        shapes.append((to_svg_d(xform(subs_en, 1, x_text, top - be[1], flip=False)), C_INK, []))
        shapes.append((to_svg_d(xform(subs_cn, 1, x_text + 5,
                                     top + eh + vgap - bc[1], flip=False)), C_INK_SOFT, []))

    bb = shapes_bbox(shapes)
    dx, dy = pad - bb[0], pad - bb[1]
    out = [(to_svg_d(xform(parse_path(d), 1, dx, dy, flip=False)), f, a) for d, f, a in shapes]
    return out, int(bb[2] - bb[0] + 2 * pad + 0.5), int(bb[3] - bb[1] + 2 * pad + 0.5)


def render_header(one_line=False):
    return header_lockup(one_line=one_line)


# ------------------------------------------------------------------ 输出 ---

def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out, exist_ok=True)

    # 1) 横版 logo (两行)  @1x / @2x
    shapes, W, H = render_header(False)
    img = render(shapes, W, H, ss=3)
    img.save(os.path.join(out, "logo-header.png"))
    img.resize((W * 3, H * 3), Image.LANCZOS).save(os.path.join(out, "logo-header@3x.png"))
    img.resize((W * 2, H * 2), Image.LANCZOS).save(os.path.join(out, "logo-header@2x.png"))
    with open(os.path.join(out, "logo-header.svg"), "w") as f:
        f.write(shapes_to_svg(shapes, W, H))

    # 2) 横版 logo (一行, 域名)
    shapes1, W1, H1 = render_header(True)
    img1 = render(shapes1, W1, H1, ss=3)
    img1.save(os.path.join(out, "logo-header-oneline.png"))
    img1.resize((W1 * 2, H1 * 2), Image.LANCZOS).save(os.path.join(out, "logo-header-oneline@2x.png"))
    with open(os.path.join(out, "logo-header-oneline.svg"), "w") as f:
        f.write(shapes_to_svg(shapes1, W1, H1))

    # 3) 纯徽标
    mark_shapes = build_mark(512)
    mimg = render(mark_shapes, 512, 512, ss=2)
    mimg.save(os.path.join(out, "logo-mark.png"))
    with open(os.path.join(out, "logo-mark.svg"), "w") as f:
        f.write(shapes_to_svg(mark_shapes, 512, 512))

    # 4) favicon: 多尺寸 ico + png
    ico_sizes = [16, 32, 48, 64]
    imgs = []
    for s in ico_sizes:
        imgs.append(render(mark_shapes, s, s, ss=max(4, 128 // s), src_size=512))
    imgs[-1].save(os.path.join(out, "favicon.ico"),
                  format="ICO", sizes=[(s, s) for s in ico_sizes],
                  append_images=imgs[:-1])
    for s in (16, 32, 48):
        render(mark_shapes, s, s, ss=128 // s, src_size=512).save(os.path.join(out, f"icon-{s}.png"))
    for s, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        render(mark_shapes, s, s, ss=2, src_size=512).save(os.path.join(out, name))

    print("done ->", out)


if __name__ == "__main__":
    main()
