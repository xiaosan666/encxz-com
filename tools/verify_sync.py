#!/usr/bin/env python3
"""校验「时间轴 / 视频」与「音频」是否真的对齐。

合轨音频永远是 [封面静音][音频][间隔静音][音频][片尾静音]，所以任何一项不过，
都会表现为"字幕快于语音"或"结尾被剪"：

1. **第二遍起点**：时间轴里的 pass2 起点，必须等于音频里第二遍的真实起点
   （`封面 + 音频真实长度 + 间隔`）。差值为正 = 字幕比语音早。
2. **总长**：成品长度必须 ≥ 合轨音频总长，否则 `-shortest` 会把音频尾巴剪掉。
3. **截掉多少语音**：音频最后一段语音的结束时刻，是否被成品结尾切在中间。
4. **成品音轨**：把 `output/*.mp4` 的音轨与源 mp3 做互相关，量出第二遍音频在成品里的
   真实位置（避免只验公式、没验成品）。
5. **成品画面（第一遍翻页）**：二分找出成品里页面真正翻过去的时刻，与人声起点比。
6. **成品画面（第二遍字幕切换）**：同上，量滚动歌词/高亮真正切换的时刻。
   5/6 直接对应"字幕切换跟不跟得上语音"，是端到端的判据。

    .venv/bin/python tools/verify_sync.py --rows 2-4
    .venv/bin/python tools/verify_sync.py --level A1
    .venv/bin/python tools/verify_sync.py --rows 2-4 --no-video   # 只验 1~3（快）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_gen import config                                    # noqa: E402
from video_gen.align import detect_silences, load_or_align, probe_duration  # noqa: E402
from video_gen.data import load_dataset, select_rows             # noqa: E402
from video_gen.renderer import Renderer                          # noqa: E402

SR = 16000
TOL = 0.06                    # 音频/公式类判定的容差（秒）
TOL_FRAME = 0.12              # 抽帧判定的容差（秒）：受 1/30s 帧格限制
PROBE_SIZE = (480, 270)       # 抽帧比对用的降采样尺寸


# --------------------------------------------------------------------------- 音频工具


def decode(path: Path, sr: int = SR) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).astype(np.float64)


def speech_end(mp3: Path, duration: float) -> float:
    """最后一段语音的结束时间：音频末尾那段静音的起点（沿用对齐用的同一套检测）。"""
    for s, e in reversed(detect_silences(mp3, config.SILENCE_NOISE_DB,
                                         config.SILENCE_MIN_DUR)):
        if e >= duration - 0.05:
            return float(s)                   # 结尾是静音，语音到它为止
    return float(duration)


def find_copy_offsets(ref: np.ndarray, tgt: np.ndarray, t0: float = 10.0,
                      t1: float = 40.0) -> list[tuple[float, float]]:
    """把 ref[t0:t1] 当模板在 tgt 里滑动，返回按分数降序的 [(偏移秒, 归一化分)]。"""
    if len(ref) < int(t1 * SR) or len(tgt) < int(t1 * SR):
        return []
    tmpl = ref[int(t0 * SR):int(t1 * SR)].copy()
    tmpl -= tmpl.mean()
    m, n = len(tmpl), len(tgt)
    size = 1 << (m + n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(tmpl, size).conj() * np.fft.rfft(tgt, size),
                        size)[:n]
    cs = np.concatenate(([0.0], np.cumsum(tgt ** 2)))
    norm = np.sqrt(np.maximum(cs[m:] - cs[:-m], 0) + 1e-6)
    score = corr[:len(norm)] / norm
    picked: list[tuple[int, float, float]] = []          # (样本位置, 偏移秒, 分数)
    for i in np.argsort(score)[::-1]:
        if all(abs(int(i) - p) > 5 * SR for p, _, _ in picked):
            picked.append((int(i), float(i) / SR - t0, float(score[i])))
        if len(picked) >= 3:
            break
    return [(off, sc) for _, off, sc in picked]


# --------------------------------------------------------------------------- 画面对齐


def grab_frame(mp4: Path, t: float) -> np.ndarray:
    """从成品视频里取 t 秒处的一帧（灰度、降采样），返回 float 数组。"""
    w, h = PROBE_SIZE
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1",
         "-vf", f"scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    if len(out) < w * h:
        return np.zeros((h, w), dtype=np.float64)
    return np.frombuffer(out[:w * h], dtype=np.uint8).reshape(h, w).astype(np.float64)


def as_ref(img) -> np.ndarray:
    from PIL import Image
    return np.asarray(img.convert("L").resize(PROBE_SIZE), dtype=np.float64)


def measure_switch(mp4: Path, ref_before: np.ndarray, lo: float, hi: float,
                   steps: int = 9) -> float | None:
    """二分找出画面"开始离开 ref_before"的时刻（秒）。

    切换前画面是静止的，与 ref_before 的 MAE 只有 ~0.1（编码噪声）；一开始切换
    （翻页 / 滚动歌词动画）就跳到 1.7 以上，所以以"突然不像 ref_before"为判据二分，
    精度到 1 帧（1/30s）。返回 None 表示窗口内没有发生切换。
    """
    if hi - lo < 0.1:
        return None
    base = float(np.abs(grab_frame(mp4, lo) - ref_before).mean())
    thr = base + 0.4
    if float(np.abs(grab_frame(mp4, hi) - ref_before).mean()) <= thr:
        return None
    for _ in range(steps):
        mid = (lo + hi) / 2
        if float(np.abs(grab_frame(mp4, mid) - ref_before).mean()) > thr:
            hi = mid
        else:
            lo = mid
    return hi


# --------------------------------------------------------------------------- 校验


def build_audio_timeline(real: float, cover: float, gap: float, tail: float) -> dict:
    """合轨音频 [封面静音][音频][间隔静音][音频][片尾静音] 的真实时间点。"""
    return {
        "copy1": cover,
        "copy2": cover + real + gap,
        "end": cover + 2 * real + gap + tail,
    }


def check_row(row, args) -> bool:
    timings = load_or_align(row, engine=args.align_engine)
    real = probe_duration(row.mp3_path)
    audio_tl = build_audio_timeline(real, args.cover_sec, args.gap_sec, config.TAIL_SEC)
    renderer = Renderer(row, timings, args.cover_sec, args.gap_sec)
    tl = renderer.timeline()

    spoken = speech_end(row.mp3_path, real)

    print("=" * 78)
    print(f"{row.id}  {row.stem}")
    print(f"  音频：ffprobe {real:.3f}s，最后一段语音到 {spoken:.3f}s"
          f"（尾部静音 {real - spoken:.2f}s），句数 {len(timings.sentences)}")
    print(f"  时间轴 duration={timings.duration:.3f}s"
          + (f"（已按音频校准，原值 {timings.params.get('duration_fixed_from')}）"
             if "duration_fixed_from" in timings.params else ""))

    ok = True
    drift = tl["pass2_start"] - audio_tl["copy2"]
    ok &= abs(drift) <= TOL
    print(f"  [1] 第二遍起点：时间轴 {tl['pass2_start']:.3f}s，音频 {audio_tl['copy2']:.3f}s"
          f"  → 字幕{'早' if drift > 0 else '晚'} {abs(drift):.3f}s  "
          f"{'OK' if abs(drift) <= TOL else '✗ 超差'}")

    mp4 = Path(args.outdir) / f"{row.stem}.mp4"
    if not mp4.exists():
        print(f"  [2..4] 未找到 {mp4}，只校验时间轴")
        return bool(ok)

    video_end = probe_duration(mp4)
    lost = max(0.0, audio_tl["copy2"] + spoken - video_end)
    ok &= lost <= TOL
    print(f"  [2] 总长：成品 {video_end:.3f}s，音频需要 {audio_tl['end']:.3f}s"
          f"  → {'OK' if video_end + TOL >= audio_tl['end'] else '✗ 偏短'}")
    print(f"  [3] 第二遍结尾被切掉的语音：{lost:.3f}s  "
          f"{'OK' if lost <= TOL else '✗ 音频没播完'}")

    # [4] 成品音轨里第二遍音频的真实位置（互相关）。注意：这一段只验证音轨本身，
    #     音轨永远是 [封面][音频][间隔][音频][片尾]，画面是否跟着音轨走由 [5][6] 验证。
    copy2 = audio_tl["copy2"]
    if args.no_video or args.probes <= 0:
        return bool(ok)

    tgt = decode(mp4)
    found = find_copy_offsets(decode(row.mp3_path), tgt)
    big = [p for p in found if p[1] > 0.5 * found[0][1]] if found else []
    if len(big) >= 2:
        copy2 = sorted(big[:2], key=lambda p: p[0])[1][0]
        err = copy2 - audio_tl["copy2"]
        ok &= abs(err) <= TOL
        print(f"  [4] 成品音轨：第二遍音频实际在 {copy2:.3f}s，偏差 {err:+.3f}s  "
              f"{'OK' if abs(err) <= TOL else '✗ 超差'}")

    # [5][6] 端到端：直接量画面真正切换的时刻，与人声起点比。
    sents = timings.sentences
    n = len(sents)
    picks = sorted({max(1, min(n - 1, k)) for k in (n // 4, n // 2, n * 3 // 4)})
    picks = picks[:max(1, args.probes)]
    prep = renderer.prepare_pass2()
    p1 = renderer.pass1_frames()

    for k in picks:
        if k >= n:
            continue
        settle = config.P2_SCROLL_ANIM + 0.15      # 滚动动画走完 + 余量
        # 第一遍：页是瞬间换的，切换时刻 = 人声起点
        want1 = audio_tl["copy1"] + sents[k].start
        lo = max(audio_tl["copy1"] + sents[k - 1].start + 0.3, want1 - 4.0, 0.0)
        got1 = measure_switch(mp4, as_ref(p1[k - 1]), lo, want1 + 0.4)
        if got1 is not None:
            e1 = got1 - want1
            ok &= abs(e1) <= TOL_FRAME
            print(f"  [5] 第 {k + 1} 句翻页：实测 {got1:.3f}s，人声 {want1:.3f}s"
                  f"  → 画面{'早' if e1 < 0 else '晚'} {abs(e1):.3f}s  "
                  f"{'OK' if abs(e1) <= TOL_FRAME else '✗ 超差'}")

        # 第二遍：滚动歌词，切换时刻 = 音频里第二遍对应的人声起点。
        # 起点必须等上一句的滚动动画停下，否则参考帧本身就不是静止画面。
        want2 = copy2 + sents[k].start
        lo = max(copy2 + sents[k - 1].start + settle, want2 - 4.0, audio_tl["copy1"])
        ref_before = as_ref(renderer.pass2_frame(prep, sents[k - 1].start + settle))
        got2 = measure_switch(mp4, ref_before, lo, min(want2 + 0.4, video_end - 0.1))
        if got2 is not None:
            e2 = got2 - want2
            ok &= abs(e2) <= TOL_FRAME
            print(f"  [6] 第 {k + 1} 句字幕切换：实测 {got2:.3f}s，人声 {want2:.3f}s"
                  f"  → 字幕{'早' if e2 < 0 else '晚'} {abs(e2):.3f}s  "
                  f"{'OK' if abs(e2) <= TOL_FRAME else '✗ 超差'}")
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验视频时间轴与音频是否对齐")
    ap.add_argument("--rows", help="Excel 行号，支持 2 / 2,3 / 2-21")
    ap.add_argument("--level", help="按等级筛选，如 A1")
    ap.add_argument("--all", action="store_true", help="全部有音频的行")
    ap.add_argument("--outdir", default=str(config.OUTPUT_DIR))
    ap.add_argument("--align-engine", choices=("asr", "silence"), default="asr")
    ap.add_argument("--cover-sec", type=float, default=config.COVER_SEC)
    ap.add_argument("--gap-sec", type=float, default=config.GAP_SEC)
    ap.add_argument("--no-video", action="store_true",
                    help="只校验时间轴与音轨长度，不做抽帧/互相关")
    ap.add_argument("--probes", type=int, default=2,
                    help="每行抽几处量「画面真正切换的时刻」（0 = 不量，默认 2；越大越慢）")
    args = ap.parse_args()

    rows = [r for r in load_dataset() if r.mp3_path]
    if not (args.rows or args.level or args.all):
        print("请指定要校验的行：--rows / --level / --all")
        return 2
    picked = select_rows(rows, spec=args.rows, level=args.level, only_with_audio=True)
    if not picked:
        print("没有符合条件的行。")
        return 2

    print(f"校验 {len(picked)} 行（容差 {TOL}s）\n")
    bad = [r.id for r in picked if not check_row(r, args)]

    print("\n" + "=" * 78)
    if bad:
        print(f"✗ {len(bad)}/{len(picked)} 行不合格：{', '.join(bad)}")
        return 1
    print(f"✓ {len(picked)}/{len(picked)} 行通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
