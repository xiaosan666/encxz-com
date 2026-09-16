#!/usr/bin/env python3
"""按 en.xlsx 的行生成英语听力教学视频。

用法示例
--------
    # 看看每一行的数据齐不齐
    python make_video.py --list

    # 生成第 2 行
    python make_video.py --rows 2

    # 生成第 2~21 行（当前有中文译稿的那批）
    python make_video.py --rows 2-21

    # 生成所有 A1 且音频齐全的行
    python make_video.py --level A1 --only-audio

    # 只算时间轴不渲染（结果在 cache/ 里，可手工微调）
    python make_video.py --rows 2 --align-only

    # 导出若干时间点的画面 PNG 用来核对
    python make_video.py --rows 2 --snapshot auto

    # 缺音频时用系统 TTS 临时合成一段，方便先看效果
    python make_video.py --rows 2 --tts
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from video_gen import config
from video_gen.align import align_sentences, cache_path, format_report, load_or_align
from video_gen.asr import format_report as asr_report
from video_gen.data import Row, load_dataset, select_rows
from video_gen.encode import render_row, snapshot_frames


def describe(timings) -> str:
    """按使用的引擎选择对应的质量报告。"""
    return asr_report(timings) if timings.engine == "asr" else format_report(timings)


@dataclass
class Options:
    outdir: Path = config.OUTPUT_DIR
    snapshot_dir: Path = config.CACHE_DIR / "snapshots"
    cover_sec: float = config.COVER_SEC
    gap_sec: float = config.GAP_SEC
    fps: int = config.FPS
    width: int = config.W
    height: int = config.H
    preset: str = "veryfast"
    crf: int = 20
    keep_audio: bool = False
    quiet: bool = False
    name_suffix: str = ""


# --------------------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="根据 en.xlsx 的行生成两遍式英语听力视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sel = p.add_argument_group("选择要生成的行")
    sel.add_argument("--rows", help="Excel 行号，支持 2 / 2,3 / 2-21 / 也可写 mp3 文件名片段")
    sel.add_argument("--level", help="按等级筛选，如 A1 或 A1,A2")
    sel.add_argument("--all", action="store_true", help="全部行")
    sel.add_argument("--only-audio", action="store_true", help="只要音频齐全的行")
    sel.add_argument("--only-cn", action="store_true", help="只要中文译稿齐全的行")
    sel.add_argument("--limit", type=int, help="最多处理多少行")
    sel.add_argument("--list", action="store_true", help="只列出数据状态，不生成")

    out = p.add_argument_group("输出")
    out.add_argument("--outdir", default=str(config.OUTPUT_DIR), help="mp4 输出目录")
    out.add_argument("--fps", type=int, default=config.FPS)
    out.add_argument("--size", default=f"{config.W}x{config.H}", help="如 1920x1080")
    out.add_argument("--preset", default="veryfast", help="x264 preset")
    out.add_argument("--crf", type=int, default=20)
    out.add_argument("--jobs", type=int, default=1, help="并行渲染的行数")

    t = p.add_argument_group("时间轴 / 字幕同步")
    t.add_argument("--align-engine", choices=("asr", "silence"), default="asr",
                   help="asr：语音识别词级对齐（默认，推荐）；silence：静音检测+动态规划")
    t.add_argument("--asr-model", default="small",
                   help="whisper 模型：tiny/base/small/medium/large-v3（默认 small）")
    t.add_argument("--asr-min-coverage", type=float, default=0.95,
                   help="脚本与识别结果的词匹配率低于该值时给出警告（默认 0.95）")
    t.add_argument("--cover-sec", type=float, default=config.COVER_SEC, help="封面停留秒数")
    t.add_argument("--gap-sec", type=float, default=config.GAP_SEC, help="两遍之间的静默秒数")
    t.add_argument("--noise-db", type=float, default=config.SILENCE_NOISE_DB,
                   help="静音检测门限（dB，仅 silence 引擎）")
    t.add_argument("--min-sil", type=float, default=config.SILENCE_MIN_DUR,
                   help="最短静音时长（秒，仅 silence 引擎）")

    m = p.add_argument_group("模式")
    m.add_argument("--align-only", action="store_true", help="只计算/刷新时间轴，不渲染")
    m.add_argument("--realign", action="store_true", help="忽略已有缓存，重新对齐")
    m.add_argument("--show-align", action="store_true", help="打印逐句时间轴")
    m.add_argument("--export-lines", action="store_true",
                   help="把逐句英中对照导出到 cache/rowN.lines.json 供人工修订")
    m.add_argument("--snapshot", nargs="?", const="auto",
                   help="导出画面 PNG（auto 或 逗号分隔的秒数）")
    m.add_argument("--rebuild-template", action="store_true", help="重新生成纸张底图")
    m.add_argument("--fake-audio", action="store_true",
                   help="缺音频时合成测试音频（每句一个音块），用于先跑通/预览效果")
    m.add_argument("--tts", action="store_true",
                   help="缺音频时用 macOS say 合成（部分环境会截断，仅作兜底）")
    m.add_argument("--keep-audio", action="store_true", help="保留中间 wav")
    return p


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


# --------------------------------------------------------------------------- TTS 兜底


def synthesize_fake(row: Row) -> Path | None:
    """合成一段"每句一个音块"的测试音频，用于在没有真实 mp3 时先跑通流程。

    每句一个正弦音块，长度按词数估算，句间留出静音，这样静音检测对齐也能被真实验证。
    """
    import math
    import struct
    import wave

    out_dir = config.CACHE_DIR / "fake"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{row.stem}.wav"
    mp3_path = out_dir / f"{row.stem}.mp3"
    if mp3_path.exists():
        return mp3_path

    sr = 44100
    frames = bytearray()

    def tone(freq: float, dur: float, amp: float = 0.35):
        n = int(sr * dur)
        for i in range(n):
            # 两端做淡入淡出，避免爆音
            env = min(1.0, i / (0.02 * sr), (n - i) / (0.02 * sr))
            v = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / sr))
            frames.extend(struct.pack("<h", v))

    def silence(dur: float):
        frames.extend(b"\x00\x00" * int(sr * dur))

    silence(0.35)
    for k, s in enumerate(row.sentences):
        dur = max(0.45, len(s.en.split()) / 2.6)
        tone(300 + (k % 5) * 60, dur)
        silence(0.32)
    silence(0.6)

    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav_path),
                    "-c:a", "libmp3lame", "-q:a", "4", str(mp3_path)], check=True)
    wav_path.unlink(missing_ok=True)
    return mp3_path


def synthesize_tts(row: Row) -> Path | None:
    """用 macOS say 合成英文音频（沙箱里可能被截断，仅作兜底）。"""
    if not subprocess.run(["which", "say"], capture_output=True, text=True).stdout.strip():
        return None
    out_dir = config.CACHE_DIR / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3 = out_dir / f"{row.stem}.mp3"
    if mp3.exists():
        return mp3
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(row.script_en.replace("\n", " "))
        txt = fh.name
    aiff = out_dir / f"{row.stem}.aiff"
    subprocess.run(["say", "-r", "165", "-f", txt, "-o", str(aiff)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(aiff),
                    "-c:a", "libmp3lame", "-q:a", "4", str(mp3)], check=True)
    aiff.unlink(missing_ok=True)
    Path(txt).unlink(missing_ok=True)
    return mp3


# --------------------------------------------------------------------------- 单行处理


def process_row(row: Row, opts: Options, args) -> tuple[str, Path | None, str]:
    """返回 (行标识, 产物路径或 None, 说明文本)。"""
    if row.mp3_path is None:
        if args.fake_audio:
            row.mp3_path = synthesize_fake(row)
            opts = replace(opts, name_suffix="-预览")
        elif args.tts:
            row.mp3_path = synthesize_tts(row)
            opts = replace(opts, name_suffix="-预览")
        if row.mp3_path is None:
            return row.id, None, "跳过：没有音频"

    timings = load_or_align(row, engine=args.align_engine, realign=args.realign,
                            noise_db=args.noise_db, min_sil_dur=args.min_sil,
                            asr_model=args.asr_model,
                            asr_min_coverage=args.asr_min_coverage)

    report = f"{row.summary()}\n{describe(timings)}"
    if args.show_align:
        lines = ["    序号  起止时间        停顿  偏差   英文"]
        for t in timings.sentences:
            lines.append(f"    {t.i + 1:>3}  {t.start:7.2f}-{t.end:7.2f}  "
                         f"{t.source:<6} {t.dev:>4.2f}  {t.en[:52]}")
        report += "\n" + "\n".join(lines)

    if args.align_only:
        return row.id, cache_path(row), report

    if args.snapshot:
        times = snapshot_times(args.snapshot, timings, opts)
        paths = snapshot_frames(row, timings, opts, times)
        return row.id, paths[-1], report + f"\n  已导出 {len(paths)} 张 PNG 到 {opts.snapshot_dir}"

    out, level = render_row(row, timings, opts)
    if level is not None:
        report += f"\n  {level.describe()}"
    return row.id, out, report


def snapshot_times(spec: str, timings, opts: Options) -> list[float]:
    if spec != "auto":
        return [float(x) for x in spec.split(",") if x.strip()]
    tl_cover = opts.cover_sec
    sents = timings.sentences
    picks = [tl_cover * 0.5, tl_cover + 0.4]
    if sents:
        for frac in (0.25, 0.5, 0.75, 0.98):
            k = min(len(sents) - 1, int(len(sents) * frac))
            picks.append(tl_cover + sents[k].start + 0.4)
    gap_mid = tl_cover + timings.duration + opts.gap_sec / 2
    picks.append(gap_mid)
    p2 = tl_cover + timings.duration + opts.gap_sec
    picks.append(p2 + 0.4)
    if sents:
        for frac in (0.25, 0.5, 0.75, 0.98):
            k = min(len(sents) - 1, int(len(sents) * frac))
            picks.append(p2 + sents[k].start + 0.4)
    return sorted(set(round(x, 3) for x in picks))


# --------------------------------------------------------------------------- 主流程


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.rebuild_template:
        (config.ASSETS_DIR / "template_bg.png").unlink(missing_ok=True)
        import video_gen.template as tpl
        tpl.background()
        print(f"已重新生成底图：{config.ASSETS_DIR / 'template_bg.png'}")

    rows = load_dataset()

    if args.list:
        print(f"共 {len(rows)} 行数据（{config.XLSX_PATH}）\n")
        for r in rows:
            print("  " + r.summary())
        with_audio = [r for r in rows if r.mp3_path]
        with_cn = [r for r in rows if r.has_cn]
        both = [r for r in rows if r.mp3_path and r.has_cn]
        print(f"\n  音频齐全 {len(with_audio)} 行 / 中文齐全 {len(with_cn)} 行 / "
              f"两者都有 {len(both)} 行")
        if not both:
            print("  ⚠ 目前没有同时具备【音频 + 中文译稿】的行，第一遍的中文字幕会缺失。")
        return 0

    if not (args.rows or args.level or args.all):
        print("请指定要生成的行：--rows / --level / --all（用 --list 查看数据状态）")
        return 2

    picked = select_rows(rows, spec=args.rows, level=args.level,
                         only_with_audio=False, only_with_cn=args.only_cn)
    if args.only_audio:
        picked = [r for r in picked if r.mp3_path]
    if args.limit:
        picked = picked[:args.limit]

    if not picked:
        print("没有符合条件的行。")
        return 1

    opts = Options(
        outdir=Path(args.outdir),
        snapshot_dir=config.CACHE_DIR / "snapshots",
        cover_sec=args.cover_sec,
        gap_sec=args.gap_sec,
        fps=args.fps,
        width=parse_size(args.size)[0],
        height=parse_size(args.size)[1],
        preset=args.preset,
        crf=args.crf,
        keep_audio=args.keep_audio,
    )

    mode = ("导出逐句对照" if args.export_lines else
            "对齐" if args.align_only else
            "导出画面" if args.snapshot else "渲染视频")
    print(f"准备{mode}：{len(picked)} 行，封面 {opts.cover_sec}s，间隔 {opts.gap_sec}s，"
          f"{opts.width}x{opts.height}@{opts.fps}fps\n")

    if args.export_lines:
        from video_gen.data import export_lines
        for r in picked:
            p = export_lines(r)
            print(f"  row{r.excel_row:>4} {r.title_en[:40]:<42} → {p}")
        return 0

    results: list[tuple[str, Path | None, str]] = []
    if args.jobs > 1 and not args.snapshot and not args.align_only:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futs = {pool.submit(process_row, r, opts, args): r for r in picked}
            for fut in as_completed(futs):
                res = fut.result()
                results.append(res)
                _emit(res, opts)
    else:
        for r in picked:
            res = process_row(r, opts, args)
            results.append(res)
            _emit(res, opts)

    ok = [r for r in results if r[1] is not None]
    print(f"\n完成：{len(ok)}/{len(results)} 行。输出目录：{opts.outdir}")
    return 0 if len(ok) == len(results) else 1


def _emit(res: tuple[str, Path | None, str], opts: Options) -> None:
    rid, path, report = res
    print(f"── {rid} " + "─" * 46)
    print(report)
    if path is not None:
        print(f"  → {path}")
    print(flush=True)


if __name__ == "__main__":
    sys.exit(main())
