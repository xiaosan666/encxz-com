"""模版层：字体加载、纸张底纹、页眉（logo / 等级 / 分隔线）、文本换行与绘制工具。

坐标全部基于 1920x1080，数值来自对示例视频的逐帧量测（见 config.py）。
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config


# --------------------------------------------------------------------------- 字体


def _resolve(cands) -> tuple[str, int]:
    for item in cands:
        if isinstance(item, tuple):
            path, idx = item
        else:
            path, idx = item, 0
        if Path(path).exists():
            return path, idx
    raise FileNotFoundError(f"找不到可用字体：{cands}")


class FontBook:
    """按 (角色, 字号) 缓存 PIL 字体对象。"""

    def __init__(self) -> None:
        self._base = {role: _resolve(c) for role, c in config.FONT_CANDIDATES.items()}
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, role: str, size: int) -> ImageFont.FreeTypeFont:
        key = (role, size)
        font = self._cache.get(key)
        if font is None:
            path, idx = self._base[role]
            font = ImageFont.truetype(path, size, index=idx)
            self._cache[key] = font
        return font

    def path_of(self, role: str) -> str:
        return self._base[role][0]


# --------------------------------------------------------------------------- 底纹背景

_BG_CACHE: Image.Image | None = None


def _make_texture() -> Image.Image:
    """生成淡淡的纸张云雾底纹（与示例视频的底色质感一致）。"""
    w, h = config.W, config.H
    noise = Image.effect_noise((w // 3, h // 3), 30).convert("L")
    noise = noise.resize((max(8, w // 26), max(8, h // 26)), Image.BICUBIC)
    noise = noise.resize((w, h), Image.BICUBIC)
    noise = noise.filter(ImageFilter.GaussianBlur(22))

    base = Image.new("RGB", (w, h), config.BG)
    px = base.load()
    np_ = noise.load()
    # 很轻的对角渐变，模拟示例里右下略暗的观感
    for y in range(0, h, 1):
        gy = y / h
        for x in range(0, w, 1):
            gx = x / w
            # 以 BG 为中心做很轻的起伏，整体亮度保持不变
            delta = ((np_[x, y] - 128) * 0.06
                     - ((gx - 0.5) * 2.2 + (gy - 0.5) * 2.8))
            px[x, y] = (
                _clamp(config.BG[0] + delta),
                _clamp(config.BG[1] + delta),
                _clamp(config.BG[2] + delta),
            )
    return base


def _clamp(v: float) -> int:
    return int(max(0, min(255, round(v))))


def background() -> Image.Image:
    """底图（带缓存；有 assets/template_bg.png 就直接用）。"""
    global _BG_CACHE
    if _BG_CACHE is not None:
        return _BG_CACHE
    cached = config.ASSETS_DIR / "template_bg.png"
    if cached.exists():
        _BG_CACHE = Image.open(cached).convert("RGB")
    else:
        _BG_CACHE = _make_texture()
        config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        _BG_CACHE.save(cached)
    return _BG_CACHE


# --------------------------------------------------------------------------- 文本工具

_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")


def tokenize(text: str) -> list[str]:
    """把句子切成排版单元：CJK/全角标点各自成 token，拉丁词整体成 token。"""
    out: list[str] = []
    buf = ""
    for ch in text:
        if _CJK.match(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
            out.append(" ")
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """按像素宽度换行（中英混排）。"""
    lines: list[str] = []
    cur = ""
    for tok in tokenize(text):
        if tok == " ":
            if cur:
                cur += " "
            continue
        trial = cur + tok
        if not cur or font.getlength(trial) <= max_width:
            cur = trial
        else:
            lines.append(cur.rstrip())
            cur = tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def fit_block(text: str, fonts: FontBook, role: str, max_width: int, max_lines: int,
              base_size: int, min_size: int, line_ratio: float,
              max_height: int | None = None, step: int = 2):
    """自动缩小字号直到文本能在给定宽高与行数内放下。返回 (字号, 行列表)。"""
    size = base_size
    while size >= min_size:
        font = fonts.get(role, size)
        lines = wrap_text(text, font, max_width)
        height = len(lines) * size * line_ratio
        if len(lines) <= max_lines and (max_height is None or height <= max_height):
            return size, lines
        size -= step
    font = fonts.get(role, min_size)
    return min_size, wrap_text(text, font, max_width)


def text_w(font: ImageFont.FreeTypeFont, s: str) -> float:
    return font.getlength(s)


# --------------------------------------------------------------------------- 页眉

# 标题里的系列前缀（"Intermediate English - 43 - "）只在封面保留，页眉里去掉
_SERIES_PREFIX = re.compile(
    r"^\s*(?:Beginner|Intermediate|High-Intermediate|Advanced)\s+English\s*-\s*\d+\s*-\s*",
    re.IGNORECASE)


def short_title(title: str) -> str:
    """页眉用的短标题：去掉 "XXX English - NN -" 这类系列前缀。"""
    stripped = _SERIES_PREFIX.sub("", title).strip()
    return stripped or title


def _draw_header(img: Image.Image, fonts: FontBook, level: str, header_title: str = "") -> None:
    d = ImageDraw.Draw(img)
    cx, cy, r = config.LOGO_CIRCLE

    # 圆形 EN 徽标
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=config.MAROON,
              width=config.LOGO_CIRCLE_W)
    f_en = fonts.get("en_reg", config.LOGO_EN_SIZE)
    d.text((cx, cy + 1), "EN", font=f_en, fill=config.MAROON, anchor="mm")

    # 站名与网址（所有视频固定不变）
    f_l1 = fonts.get("cn_bold", config.LOGO_LINE1_SIZE)
    d.text((config.LOGO_TEXT_X, config.LOGO_LINE1_BASELINE), "英语初学者",
           font=f_l1, fill=config.MAROON, anchor="ls")
    f_l2 = fonts.get("en_reg", config.LOGO_LINE2_SIZE)
    d.text((config.LOGO_TEXT_X, config.LOGO_LINE2_BASELINE), "encxz.com",
           font=f_l2, fill=config.MAROON, anchor="ls")

    # 右上角：语言等级（中文用黑体，A1 用衬线，与示例一致）
    f_lv = fonts.get("cn_bold", config.LEVEL_SIZE)
    f_lv_en = fonts.get("en_reg", config.LEVEL_SIZE)
    prefix = "级别："
    level_text = str(level)
    w_en = f_lv_en.getlength(level_text)
    w_cn = f_lv.getlength(prefix)
    x0 = config.LEVEL_X_RIGHT - w_en - w_cn
    d.text((x0, config.LEVEL_BASELINE), prefix, font=f_lv, fill=config.INK, anchor="ls")
    d.text((config.LEVEL_X_RIGHT, config.LEVEL_BASELINE), level_text,
           font=f_lv_en, fill=config.INK, anchor="rs")

    # 中间标题（两遍都有）：夹在 logo 与右上角等级之间，过长自动缩字号
    if header_title:
        text = short_title(header_title)
        max_w = config.HEADER_TITLE_MAX_WIDTH
        size = config.HEADER_TITLE_SIZE
        f_ht = fonts.get("en_bold", size)
        while size > config.HEADER_TITLE_MIN_SIZE and f_ht.getlength(text) > max_w:
            size -= 2
            f_ht = fonts.get("en_bold", size)
        if f_ht.getlength(text) > max_w:                 # 缩到最小还放不下就截断
            while len(text) > 4 and f_ht.getlength(text + "…") > max_w:
                text = text[:-1]
            text = text.rstrip() + "…"
        d.text((config.W // 2, config.HEADER_TITLE_BASELINE), text,
               font=f_ht, fill=config.MAROON, anchor="ms")

    # 分隔线：一条粗线 + 若干细装饰线
    d.rectangle([config.RULE_X0, config.RULE_THICK_Y,
                 config.RULE_X1, config.RULE_THICK_Y + config.RULE_THICK_H - 1],
                fill=config.RULE_DARK)
    for y in config.RULE_HAIRLINES:
        d.line([config.RULE_X0, y, config.RULE_X1, y], fill=(90, 88, 84), width=1)


@functools.lru_cache(maxsize=64)
def base_canvas(level: str, header_title: str = "") -> Image.Image:
    """带页眉的底图（按等级 + 是否带中间标题缓存）。"""
    fonts = font_book()
    img = background().copy()
    _draw_header(img, fonts, level, header_title)
    return img


@functools.lru_cache(maxsize=1)
def font_book() -> FontBook:
    return FontBook()
