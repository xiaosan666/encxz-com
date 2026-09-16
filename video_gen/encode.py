"""编码层：拼接音频 + 把逐帧画面通过管道喂给 ffmpeg 编码。"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import config
from .data import Row
from .renderer import Renderer

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，请先安装（brew install ffmpeg）")
    out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    if "libx264" not in out:
        raise RuntimeError("当前 ffmpeg 没有 libx264 编码器，无法输出 H.264")


# --------------------------------------------------------------------------- 音频响度归一化

_LOUDNORM_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)

# 语音段的统一表示：源一律先转成 44.1kHz 单声道，测量和增益都在这上面做。
# 必须保持一致：立体声降单声道时响度基本不变，但真峰值最多会抬高约 3 dB（左右声道
# 峰值同时出现的瞬间），若拿立体声的测量值去给单声道信号定增益，峰值上限就会少算
# ~3 dB，安静的音源会被推爆成削波。
SPEECH_FMT = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono"


@dataclass
class AudioLevel:
    """一次响度归一化的结果，供逐行报告回显。

    source_lufs / source_peak_dbtp 都是**转成 44.1kHz 单声道之后**的测量值（即增益
    真正作用的信号）。source_lufs 为 None 表示没能量出响度（文件损坏 / 全静音），
    此时按原音量输出，只是不做归一化。
    """

    target_lufs: float
    source_lufs: float | None = None
    source_peak_dbtp: float | None = None
    gain_db: float = 0.0
    peak_limited: bool = False          # True = 为防削波没能加满，响度未达目标

    @property
    def ok(self) -> bool:
        return self.source_lufs is not None

    @property
    def result_lufs(self) -> float:
        return (self.source_lufs or 0.0) + self.gain_db

    @property
    def result_peak_dbtp(self) -> float:
        return (self.source_peak_dbtp or 0.0) + self.gain_db

    def describe(self) -> str:
        if not self.ok:
            return "音量归一化：源响度测量失败，按原音量输出"
        if abs(self.gain_db) < 0.05:
            return f"音量归一化：{self.result_lufs:.1f} LUFS，已在目标附近，未调整"
        note = "，已按峰值上限收窄（响度未达目标）" if self.peak_limited else ""
        return (f"音量归一化：{self.source_lufs:.1f} → {self.result_lufs:.1f} LUFS"
                f"（增益 {self.gain_db:+.1f} dB，峰值 {self.source_peak_dbtp:.1f} →"
                f" {self.result_peak_dbtp:.1f} dBTP{note}）")


def measure_loudness(mp3: Path) -> tuple[float, float] | None:
    """量一遍源音频的 EBU R128 整体响度与真峰值，返回 (LUFS, dBTP)。

    量的是**转成 44.1kHz 单声道之后**的信号，也就是 build_audio 里真正被施加增益的
    那一路（原因见 SPEECH_FMT 的注释）。

    loudnorm 第一遍只测不改（结果丢给 null），报告以 JSON 混在 stderr 里，抠出来即可。
    量不出来就返回 None，让调用方退回「不归一化」而不是让整行渲染失败。
    """
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", str(mp3),
           "-af", f"{SPEECH_FMT},loudnorm=print_format=json", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    m = _LOUDNORM_RE.search(proc.stderr or "")
    if not m:
        return None
    try:
        report = json.loads(m.group(0))
        lufs, peak = float(report["input_i"]), float(report["input_tp"])
    except (ValueError, KeyError):
        return None
    # 全静音时 loudnorm 会报 -inf，增益会算成 +inf，必须挡掉
    if not (math.isfinite(lufs) and math.isfinite(peak)):
        return None
    return lufs, peak


def loudness_gain_db(mp3: Path, target_lufs: float,
                     peak_dbtp: float) -> AudioLevel:
    """算出把该音频抬到目标响度所需的静态增益（dB），并守住峰值上限。

    这里是纯线性增益，不做动态压缩、不做限幅，所以音频时长一个采样都不变，
    逐句时间轴（以及已经人工校对过的缓存）不会被影响。
    增益会把真峰值推过上限时，以峰值上限为准——宁可响度略低于目标，也不削波。
    """
    measured = measure_loudness(mp3)
    if measured is None:
        return AudioLevel(target_lufs=target_lufs)
    lufs, peak = measured
    gain = target_lufs - lufs
    limited = False
    if peak + gain > peak_dbtp:
        gain = peak_dbtp - peak
        limited = True
    return AudioLevel(target_lufs=target_lufs, source_lufs=lufs,
                      source_peak_dbtp=peak, gain_db=gain, peak_limited=limited)


def build_audio(mp3: Path, cover_sec: float, gap_sec: float, tail_sec: float,
                out_wav: Path, normalize: bool | None = None) -> AudioLevel | None:
    """生成 [封面静音][音频][间隔静音][音频][片尾静音] 的合轨。

    normalize 打开时（默认跟随 config.AUDIO_NORMALIZE），先量源音频响度，再给两段语音
    施加同一个静态增益。注意两点：

    * 增益加在**语音段**上，而不是加在拼接好的整轨上——静音段不参与测量，也不会被放大；
    * 第一遍和第二遍用**同一个**增益，否则两遍之间会出现音量跳变。
    """
    if normalize is None:
        normalize = config.AUDIO_NORMALIZE

    level: AudioLevel | None = None
    gain = ""
    if normalize:
        level = loudness_gain_db(mp3, config.AUDIO_TARGET_LUFS, config.AUDIO_TRUE_PEAK_DBTP)
        # 已经到位的就别为 0.0x dB 多挂一个滤镜
        if level.ok and abs(level.gain_db) >= 0.05:
            gain = f",volume={level.gain_db:.3f}dB"
        elif level.ok:
            level.gain_db = 0.0

    chain = (
        f"anullsrc=r=44100:cl=mono,atrim=0:{cover_sec:.3f},asetpts=N/SR/TB[s0];"
        f"[0:a]{SPEECH_FMT}{gain},asetpts=N/SR/TB[a0];"
        f"anullsrc=r=44100:cl=mono,atrim=0:{gap_sec:.3f},asetpts=N/SR/TB[s1];"
        f"[0:a]{SPEECH_FMT}{gain},asetpts=N/SR/TB[a1];"
        f"anullsrc=r=44100:cl=mono,atrim=0:{tail_sec:.3f},asetpts=N/SR/TB[s2];"
        f"[s0][a0][s1][a1][s2]concat=n=5:v=0:a=1[out]"
    )
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(mp3),
           "-filter_complex", chain, "-map", "[out]",
           "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "1", str(out_wav)]
    subprocess.run(cmd, check=True)
    return level


def encode_video(frames: Iterable[bytes], audio: Path, out_path: Path,
                 fps: int = config.FPS, size: tuple[int, int] = (config.W, config.H),
                 preset: str = "veryfast", crf: int = 20) -> None:
    """把 RGB 帧序列与音频合成 mp4。"""
    w, h = size
    cmd = [
        FFMPEG, "-y", "-v", "error", "-nostats",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "1",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for buf in frames:
            proc.stdin.write(buf)
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg 编码失败，退出码 {code}")


# --------------------------------------------------------------------------- 时间轴 → 帧

def frame_stream(renderer: Renderer, fps: int = config.FPS) -> tuple[Iterator[bytes], int]:
    """按时间轴产出每一帧的 RGB 数据。"""
    tl = renderer.timeline()
    # 帧数向上取整：音轨长度 = 时间轴长度，帧数宁可多半帧（-shortest 会丢掉多余画面），
    # 也不要少半帧把音频结尾剪掉。
    total_frames = int(math.ceil(round(tl["total"] * fps, 6)))
    duration = renderer.duration

    cover = renderer.cover_frame()
    pass1 = renderer.pass1_frames()
    prep = renderer.prepare_pass2()
    last = renderer.pass2_frame(prep, duration)

    def gen() -> Iterator[bytes]:
        for n in range(total_frames):
            t = n / fps
            if t < tl["pass1_start"]:
                img = cover
            elif t < tl["pass1_end"]:
                idx = renderer._active_index(t - tl["pass1_start"])
                img = pass1[min(idx, len(pass1) - 1)]
            elif t < tl["pass2_start"]:
                img = cover                       # 间隔：标题页静止不动
            elif t < tl["pass2_end"]:
                img = renderer.pass2_frame(prep, t - tl["pass2_start"])
            else:
                img = last
            yield img.tobytes()

    return gen(), total_frames


def render_row(row: Row, timings, opts) -> tuple[Path, AudioLevel | None]:
    """渲染一行数据，返回 (产出的 mp4 路径, 响度归一化结果)。"""
    check_ffmpeg()
    renderer = Renderer(row, timings, opts.cover_sec, opts.gap_sec)
    tl = renderer.timeline()

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(opts.outdir) / f"{row.stem}{getattr(opts, 'name_suffix', '')}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wav = config.CACHE_DIR / f"{row.id}-audio.wav"
    level = build_audio(row.mp3_path, opts.cover_sec, opts.gap_sec, config.TAIL_SEC, wav)

    frames, total = frame_stream(renderer, opts.fps)
    try:
        encode_video(frames, wav, out_path, fps=opts.fps, size=(opts.width, opts.height),
                     preset=opts.preset, crf=opts.crf)
    finally:
        if not opts.keep_audio and wav.exists():
            wav.unlink()

    return out_path, level


def snapshot_frames(row: Row, timings, opts, times: list[float]) -> list[Path]:
    """把指定时间点的画面导出成 PNG，便于人工核对效果。"""
    renderer = Renderer(row, timings, opts.cover_sec, opts.gap_sec)
    tl = renderer.timeline()
    out_dir = Path(opts.snapshot_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cover = renderer.cover_frame()
    pass1 = renderer.pass1_frames()
    prep = renderer.prepare_pass2()

    paths: list[Path] = []
    for t in times:
        if t < tl["pass1_start"]:
            img = cover
        elif t < tl["pass1_end"]:
            idx = renderer._active_index(t - tl["pass1_start"])
            img = pass1[min(idx, len(pass1) - 1)]
        elif t < tl["pass2_start"]:
            img = cover
        else:
            img = renderer.pass2_frame(prep, min(t - tl["pass2_start"], renderer.duration))
        p = out_dir / f"t{int(round(t * 1000)):07d}ms.png"
        img.save(p)
        paths.append(p)
    return paths
