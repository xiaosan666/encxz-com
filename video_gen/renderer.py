"""渲染层：封面页、第一遍逐句页、第二遍滚动歌词页。"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

from . import config
from .align import Timings
from .data import Row
from .template import base_canvas, fit_block, font_book, wrap_text

Mask = Image.Image


# --------------------------------------------------------------------------- 小工具


def _draw_block_center(img: Image.Image, lines: list[str], font, size: int,
                       ratio: float, center_y: int, color, anchor_x: int | None = None) -> None:
    """把多行文本整体垂直居中绘制在 center_y。"""
    d = ImageDraw.Draw(img)
    lh = size * ratio
    y0 = center_y - (len(lines) - 1) * lh / 2
    for k, line in enumerate(lines):
        y = y0 + k * lh
        if anchor_x is None:
            d.text((config.W // 2, y), line, font=font, fill=color, anchor="mm")
        else:
            d.text((anchor_x, y), line, font=font, fill=color, anchor="lm")


def _draw_block_bottom(img: Image.Image, lines: list[str], font, size: int,
                       ratio: float, bottom_baseline: int, color) -> None:
    """把多行文本以最后一行基线对齐 bottom_baseline，整体向上堆叠。"""
    d = ImageDraw.Draw(img)
    lh = size * ratio
    n = len(lines)
    for k, line in enumerate(lines):
        y = bottom_baseline - (n - 1 - k) * lh
        d.text((config.W // 2, y), line, font=font, fill=color, anchor="ms")


def _line_sprite(text: str, font, color) -> tuple[Image.Image, int]:
    """把一行文字渲染成紧贴内容的 RGBA 小图，返回 (图, 基线在图中行号)。"""
    ascent, descent = font.getmetrics()
    width = int(font.getlength(text)) + 2
    pad = 2
    img = Image.new("RGBA", (max(1, width + pad * 2), ascent + descent + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad, ascent + pad), text, font=font, fill=color, anchor="ls")
    return img, ascent + pad


# --------------------------------------------------------------------------- 页式渲染


@dataclass
class Pass2Layout:
    """第二遍歌词的静态排版结果。"""

    wrapped: list[list[str]]          # 每句的视觉行
    slot_y: list[float]               # 每句第一视觉行的槽位起点
    baselines: list[list[float]]      # 每句每个视觉行的基线（版面坐标系）
    base_row: int

    @property
    def total_height(self) -> float:
        last = self.baselines[-1][-1] if self.baselines else 0.0
        return last


class Renderer:
    def __init__(self, row: Row, timings: Timings, cover_sec: float, gap_sec: float) -> None:
        self.row = row
        self.t = timings
        self.cover_sec = cover_sec
        self.gap_sec = gap_sec
        self.fonts = font_book()
        self.duration = timings.duration

    # ------------------------------------------------------------------ 封面

    def cover_frame(self) -> Image.Image:
        img = base_canvas(self.row.level).copy()

        if self.row.title_en:
            size, lines = fit_block(self.row.title_en, self.fonts, "en_reg",
                                    config.COVER_MAX_WIDTH, 2, config.COVER_TITLE_SIZE,
                                    64, config.COVER_TITLE_RATIO)
            _draw_block_center(img, lines, self.fonts.get("en_reg", size), size,
                               config.COVER_TITLE_RATIO, config.COVER_TITLE_CENTER, config.INK)

        if self.row.title_cn:
            size, lines = fit_block(self.row.title_cn, self.fonts, "cn_bold",
                                    config.COVER_MAX_WIDTH, 2, config.COVER_CN_SIZE,
                                    40, config.COVER_CN_RATIO)
            _draw_block_center(img, lines, self.fonts.get("cn_bold", size), size,
                               config.COVER_CN_RATIO, config.COVER_CN_CENTER, config.MAROON)

        f_tag = self.fonts.get("en_reg", config.COVER_TAG_SIZE)
        d = ImageDraw.Draw(img)
        d.text((config.W // 2, config.COVER_TAG_CENTER), "English Listening Practice",
               font=f_tag, fill=config.GRAY, anchor="mm")
        return img

    # ------------------------------------------------------------------ 第一遍

    def pass1_frames(self) -> list[Image.Image]:
        # 第一遍同样在页眉中间显示英文标题
        base = base_canvas(self.row.level, self.row.title_en)
        total = len(self.t.sentences)
        frames: list[Image.Image] = []
        f_counter = self.fonts.get("en_reg", 33)

        for idx, sent in enumerate(self.t.sentences):
            img = base.copy()
            size, lines = fit_block(sent.en, self.fonts, "en_reg",
                                    config.P1_EN_MAX_WIDTH, config.P1_EN_MAX_LINES,
                                    config.P1_EN_SIZE, config.P1_EN_MIN_SIZE,
                                    config.P1_EN_LINE_RATIO)
            _draw_block_center(img, lines, self.fonts.get("en_reg", size), size,
                               config.P1_EN_LINE_RATIO, config.P1_EN_CENTER_Y, config.INK)

            if sent.cn:
                csize, clines = fit_block(sent.cn, self.fonts, "cn_reg",
                                          config.P1_CN_MAX_WIDTH, config.P1_CN_MAX_LINES,
                                          config.P1_CN_SIZE, config.P1_CN_MIN_SIZE,
                                          config.COVER_CN_RATIO)
                _draw_block_bottom(img, clines, self.fonts.get("cn_reg", csize), csize,
                                   config.COVER_CN_RATIO, config.P1_CN_BASELINE, config.INK)

            d = ImageDraw.Draw(img)
            d.text((config.P1_COUNTER_X_RIGHT, config.P1_COUNTER_BASELINE),
                   f"{idx + 1:02d}/{total:02d}", font=f_counter,
                   fill=config.PAGE_GRAY, anchor="rs")
            frames.append(img)
        return frames

    # ------------------------------------------------------------------ 第二遍

    def pass2_layout(self) -> Pass2Layout:
        f_active = self.fonts.get("en_bold", config.P2_ACTIVE_SIZE)
        ascent = f_active.getmetrics()[0]
        base_row = ascent + 6

        wrapped: list[list[str]] = []
        slot_y: list[float] = []
        baselines: list[list[float]] = []
        y = 0.0
        prev_para = None
        for sent in self.t.sentences:
            lines = wrap_text(sent.en, f_active, config.P2_LINE_MAX_WIDTH)
            if prev_para is not None and sent.para != prev_para:
                y += config.P2_PARA_EXTRA
            slot_y.append(y)
            baselines.append([y + k * config.P2_LINE_STEP + base_row for k in range(len(lines))])
            wrapped.append(lines)
            y += len(lines) * config.P2_LINE_STEP
            prev_para = sent.para

        return Pass2Layout(wrapped=wrapped, slot_y=slot_y, baselines=baselines, base_row=base_row)

    def _pass2_sprites(self, layout: Pass2Layout):
        idle_font = self.fonts.get("en_reg", config.P2_BASE_SIZE)
        act_font = self.fonts.get("en_bold", config.P2_ACTIVE_SIZE)
        idle, act = [], []
        for lines in layout.wrapped:
            row_idle, row_act = [], []
            for ln in lines:
                row_idle.append(_line_sprite(ln, idle_font, config.GRAY))
                row_act.append(_line_sprite(ln, act_font, config.MAROON))
            idle.append(row_idle)
            act.append(row_act)
        return idle, act

    def _fade_mask(self) -> Image.Image:
        """歌词可视区内的上下渐隐遮罩（尺寸仅视口高度）。"""
        vh = config.P2_VIEW_BOTTOM - config.P2_VIEW_TOP
        col = Image.new("L", (1, vh), 255)
        px = col.load()
        for y in range(vh):
            a = min(1.0, y / max(1, config.P2_FADE_TOP))
            b = min(1.0, (vh - y) / max(1, config.P2_FADE_BOTTOM))
            px[0, y] = int(round(255 * min(a, b)))
        return col.resize((config.W, vh))

    def prepare_pass2(self):
        """预渲染歌词精灵与遮罩，避免逐帧重复排版。"""
        layout = self.pass2_layout()
        idle, act = self._pass2_sprites(layout)
        return {
            "layout": layout,
            "idle": idle,
            "act": act,
            "base": base_canvas(self.row.level, self.row.title_en),
            "mask": self._fade_mask(),
        }

    def _active_index(self, t: float) -> int:
        idx = 0
        for i, s in enumerate(self.t.sentences):
            if t >= s.start - 1e-6:
                idx = i
            else:
                break
        return idx

    def _target_scroll(self, layout: Pass2Layout, idx: int) -> float:
        """把当前句的第一条视觉行钉在屏幕坐标 P2_PIN_BASELINE 上。

        注意 layout 的第一条视觉行基线在 layout 坐标系里是 base_row，
        而 layout 坐标系 0 对应屏幕的 P2_VIEW_TOP，因此这里要做一次换算。
        """
        pin_layout = config.P2_PIN_BASELINE - config.P2_VIEW_TOP
        return layout.baselines[idx][0] - pin_layout

    def _scroll_at(self, prep, t: float) -> float:
        layout = prep["layout"]
        sentences = self.t.sentences
        idx = self._active_index(t)
        target = self._target_scroll(layout, idx)
        start_t = sentences[idx].start
        if idx > 0 and t < start_t + config.P2_SCROLL_ANIM:
            prev = self._target_scroll(layout, idx - 1)
            u = (t - start_t) / config.P2_SCROLL_ANIM
            u = max(0.0, min(1.0, u))
            u = u * u * (3 - 2 * u)                    # smoothstep
            return prev + (target - prev) * u
        return target

    def pass2_frame(self, prep, t: float) -> Image.Image:
        layout: Pass2Layout = prep["layout"]
        scroll = self._scroll_at(prep, t)
        idx = self._active_index(t)

        top = config.P2_VIEW_TOP
        vh = config.P2_VIEW_BOTTOM - top
        layer = Image.new("RGBA", (config.W, vh), (0, 0, 0, 0))
        for i, lines in enumerate(layout.wrapped):
            sprites = prep["act"][i] if i == idx else prep["idle"][i]
            for k in range(len(lines)):
                sprite, base_row = sprites[k]
                # layout 坐标系里 0 就是可视区顶部，所以这里已经是 layer 内坐标
                baseline = layout.baselines[i][k] - scroll
                y = int(round(baseline - base_row))
                if y + sprite.height < 0 or y > vh:
                    continue
                layer.alpha_composite(sprite, (config.P2_TEXT_X - 2, y))

        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), prep["mask"]))
        out = prep["base"].copy()
        out.paste(layer, (0, top), layer)
        return out

    # ------------------------------------------------------------------ 时间轴

    def timeline(self) -> dict:
        """返回 cover / pass1 / gap / pass2 的时间参数。"""
        return {
            "cover": self.cover_sec,
            "pass1_start": self.cover_sec,
            "pass1_end": self.cover_sec + self.duration,
            "gap": self.gap_sec,
            "pass2_start": self.cover_sec + self.duration + self.gap_sec,
            "pass2_end": self.cover_sec + self.duration + self.gap_sec + self.duration,
            "total": self.cover_sec + self.duration + self.gap_sec + self.duration + config.TAIL_SEC,
        }
