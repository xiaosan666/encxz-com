"""音频对齐：ffmpeg silencedetect 找停顿 + 动态规划分配句级时间轴。

思路
----
1. 用 `silencedetect` 找出音频里的所有停顿区间；
2. 按每句英文的词数估算它"应该"占多长，得到 N-1 个期望切点；
3. 用动态规划从候选切点（停顿的中点 / 语音恢复点）里挑一组单调递增的切点，
   使它们与期望切点的偏差总和最小；
4. 偏差过大的切点回退到按比例分配的期望位置。

结果写入 cache/<行号>.timings.json，可人工微调；把 locked 设为 true 即可复现。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .data import Sentence

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


# --------------------------------------------------------------------------- 探测


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def detect_silences(path: Path, noise_db: float, min_dur: float) -> list[tuple[float, float]]:
    """返回 [(start, end), ...]（已按时间排序）。"""
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = proc.stderr

    starts = [float(x) for x in _SIL_START.findall(log)]
    ends = [float(x) for x in _SIL_END.findall(log)]
    pairs: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None:
            break
        if e > s:
            pairs.append((max(0.0, s), e))
    pairs.sort()
    return pairs


# --------------------------------------------------------------------------- 时间轴


@dataclass
class Timing:
    i: int
    start: float
    end: float
    en: str
    cn: str
    para: int
    expect: float = 0.0
    source: str = ""      # pause / expect
    dev: float = 0.0


@dataclass
class Timings:
    mp3: str
    duration: float
    method: str = "silencedetect+dp"
    engine: str = "silence"          # silence / asr
    fingerprint: str = ""            # 句表指纹：文稿 / 译文改动后缓存自动失效
    params: dict = field(default_factory=dict)
    silences: list = field(default_factory=list)
    sentences: list[Timing] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    locked: bool = False

    def to_json(self) -> dict:
        return {
            "mp3": self.mp3,
            "duration": self.duration,
            "method": self.method,
            "engine": self.engine,
            "fingerprint": self.fingerprint,
            "params": self.params,
            "silences": self.silences,
            "sentences": [asdict(t) for t in self.sentences],
            "quality": self.quality,
            "locked": self.locked,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Timings":
        return cls(
            mp3=d.get("mp3", ""),
            duration=float(d.get("duration", 0.0)),
            method=d.get("method", "cached"),
            engine=d.get("engine", "silence"),
            fingerprint=d.get("fingerprint", ""),
            params=d.get("params", {}),
            silences=d.get("silences", []),
            sentences=[Timing(**t) for t in d.get("sentences", [])],
            quality=d.get("quality", {}),
            locked=bool(d.get("locked", False)),
        )


def sentences_fingerprint(sentences) -> str:
    """句表指纹：英文/中文任一改动都会变，从而让旧的时间轴缓存自动失效。"""
    import hashlib

    h = hashlib.sha1()
    for s in sentences:
        h.update((s.en or "").encode("utf-8"))
        h.update(b"\x1f")
        h.update((s.cn or "").encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()[:16]


def _choose_cuts(cands: list[float], expects: list[float],
                 seg_lo: float, seg_hi: float) -> tuple[list[float], list[str]]:
    """DP 从候选切点里挑 len(expects) 个单调切点，最小化与期望位置的总偏差。"""
    k = len(expects)
    if k == 0:
        return [], []
    n = len(cands)
    if n == 0:
        return list(expects), ["expect"] * k

    INF = float("inf")
    # dp[i][j] = 前 i+1 个切点、第 i 个用了 cands[j] 的最小代价
    dp = [[INF] * n for _ in range(k)]
    prev = [[-1] * n for _ in range(k)]

    for j, c in enumerate(cands):
        if c < seg_lo:
            continue
        dp[0][j] = abs(c - expects[0])
    for i in range(1, k):
        for j in range(n):
            if dp[i - 1][j] == INF:
                continue
            cj = cands[j]
            for jj in range(j + 1, n):
                c = cands[jj]
                if c - cj < config.MIN_SEGMENT:
                    continue
                cost = dp[i - 1][j] + abs(c - expects[i])
                if cost < dp[i][jj]:
                    dp[i][jj] = cost
                    prev[i][jj] = j

    best_j, best = -1, INF
    for j in range(n):
        c = cands[j]
        if c > seg_hi:
            continue
        if dp[k - 1][j] < best:
            best, best_j = dp[k - 1][j], j

    if best_j < 0:
        return list(expects), ["expect"] * k

    picks: list[float] = [0.0] * k
    j = best_j
    for i in range(k - 1, -1, -1):
        picks[i] = cands[j]
        j = prev[i][j]
    return picks, ["pause"] * k


def _monotonic_fix(cuts: list[float], expects: list[float], start: float, end: float,
                   tol: float) -> tuple[list[float], list[str]]:
    """偏差过大的切点回退到期望位置，并保证单调且间隔不小于 MIN_SEGMENT。"""
    n = len(cuts)
    vals: list[float] = []
    src: list[str] = []
    for i in range(n):
        if abs(cuts[i] - expects[i]) > tol:
            vals.append(expects[i])
            src.append("expect")
        else:
            vals.append(cuts[i])
            src.append("pause")

    low = start + config.MIN_SEGMENT
    for i in range(n):                                # 前向：保证递增
        vals[i] = max(vals[i], low)
        low = vals[i] + config.MIN_SEGMENT
    high = end - config.MIN_SEGMENT
    for i in range(n - 1, -1, -1):                    # 后向：不要越过结尾
        vals[i] = min(vals[i], high)
        high = vals[i] - config.MIN_SEGMENT
    return vals, src


def align_sentences(mp3: Path, sentences: list[Sentence],
                    noise_db: float = config.SILENCE_NOISE_DB,
                    min_sil_dur: float = config.SILENCE_MIN_DUR) -> Timings:
    duration = probe_duration(mp3)
    silences = detect_silences(mp3, noise_db, min_sil_dur)

    # 语音区间 = 静音区间的补集
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor + 0.02:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration - 0.02:
        speech.append((cursor, duration))

    start = speech[0][0] if speech else 0.0
    end = speech[-1][1] if speech else duration
    if end - start < 0.5:                       # 极端情况兜底
        start, end = 0.0, duration

    # 期望切点：按词数比例分配
    weights = [max(1, len(s.words)) for s in sentences]
    total = sum(weights) or 1
    expects: list[float] = []
    acc = 0
    for w in weights[:-1]:
        acc += w
        expects.append(start + (end - start) * acc / total)

    # 候选切点：语音恢复点 + 长停顿的中点
    cands: list[float] = []
    for s, e in silences:
        if e - s < min_sil_dur:
            continue
        if start - 0.05 <= e <= end + 0.05:
            cands.append(e)
        if e - s > 0.9:
            cands.append((s + e) / 2)
    cands = sorted(set(round(c, 3) for c in cands))

    seg_len = (end - start) / max(1, len(sentences))
    tol = min(config.ALIGN_TOL_MAX,
              max(config.ALIGN_TOL_MIN, seg_len * config.ALIGN_TOL_FACTOR))
    cuts, src = _choose_cuts(cands, expects, start + config.MIN_SEGMENT, end - config.MIN_SEGMENT)
    raw_devs = [abs(cuts[i] - expects[i]) for i in range(len(cuts))]
    cuts, src = _monotonic_fix(cuts, expects, start, end, tol)

    bounds = [start] + cuts + [end]
    timings: list[Timing] = []
    for i, s in enumerate(sentences):
        # expects[k] 是第 k+1 个切点（即第 k 句的结束 / 第 k+1 句的开始）
        exp_start = start if i == 0 else expects[i - 1]
        timings.append(Timing(
            i=i, start=round(bounds[i], 3), end=round(bounds[i + 1], 3),
            en=s.en, cn=s.cn, para=s.para,
            expect=round(exp_start, 3),
            source="start" if i == 0 else (src[i - 1] if i - 1 < len(src) else "end"),
            dev=round(raw_devs[i - 1], 3) if i > 0 else 0.0,
        ))

    devs = [t.dev for t in timings[1:]] or [0.0]
    covered = sum(1 for x in src if x == "pause")
    quality = {
        "mean_dev": round(sum(devs) / len(devs), 3),
        "max_dev": round(max(devs), 3),
        "pause_cuts": covered,
        "total_cuts": len(src),
        "cut_coverage": round(covered / len(src), 3) if src else 0.0,
        "speech_span": [round(start, 3), round(end, 3)],
    }

    return Timings(
        mp3=mp3.name,
        duration=round(duration, 3),
        fingerprint=sentences_fingerprint(sentences),
        params={"noise_db": noise_db, "min_sil_dur": min_sil_dur, "tol": round(tol, 3)},
        silences=[[round(s, 3), round(e, 3)] for s, e in silences],
        sentences=timings,
        quality=quality,
    )


# --------------------------------------------------------------------------- 缓存


def cache_path(row, engine: str = "asr") -> Path:
    """时间轴缓存路径。按引擎分开存放，切换引擎不会互相覆盖。"""
    return config.CACHE_DIR / f"{row.id}-{_safe(row.stem)}.{engine}.timings.json"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:60]


def _full_fingerprint(row) -> str:
    """句表指纹 + 音频文件指纹：文稿、译文、音频任一变化都会让缓存失效。"""
    fp = sentences_fingerprint(row.sentences)
    try:
        st = row.mp3_path.stat()
        fp += f":{st.st_size}:{int(st.st_mtime)}"
    except (OSError, AttributeError):
        pass
    return fp


def sync_duration(timings: Timings, row) -> bool:
    """把时间轴里的 duration 校准成音频文件的真实长度，返回是否改动过。

    历史版本的 ASR 引擎误把「最后一个词的结束时间」当成音频长度，时间轴因此比音频短，
    表现为：第二遍字幕比语音早、结尾被 -shortest 剪掉。这类缓存的句表指纹和现在完全
    一样，光靠指纹分辨不出来，所以每次加载都跟音频文件对一次（只改 duration，
    不动任何句子时间，人工微调过的句子不受影响）。
    """
    try:
        real = probe_duration(row.mp3_path)
    except Exception:
        return False
    if not real or abs(real - timings.duration) <= 0.02:
        return False
    timings.params["duration_fixed_from"] = timings.duration
    timings.params["duration_fixed_to"] = round(real, 3)
    timings.duration = round(real, 3)
    return True


def _write_cache(path: Path, timings: Timings) -> None:
    try:
        path.write_text(json.dumps(timings.to_json(), ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def load_or_align(row, engine: str = "asr", realign: bool = False, **kw) -> Timings:
    """有缓存且引擎一致就直接用（locked 的永不重算），否则重新对齐并写回缓存。"""
    asr_model = kw.pop("asr_model", "small")
    asr_min_coverage = kw.pop("asr_min_coverage", 0.95)
    path = cache_path(row, engine)
    fingerprint = _full_fingerprint(row)

    if path.exists() and not realign:
        try:
            cached = Timings.from_json(json.loads(path.read_text("utf-8")))
            fresh = (cached.fingerprint == fingerprint)
            if cached.sentences and fresh and (cached.locked or cached.engine == engine):
                if sync_duration(cached, row):
                    _write_cache(path, cached)
                return cached
        except Exception:
            pass

    if engine == "asr":
        from . import asr                       # 延迟导入，避免无谓的依赖加载
        timings = asr.asr_align(row.mp3_path, row.sentences,
                                model_size=asr_model, min_coverage=asr_min_coverage)
    else:
        timings = align_sentences(row.mp3_path, row.sentences, **kw)

    sync_duration(timings, row)
    timings.fingerprint = fingerprint
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _write_cache(path, timings)
    return timings


def format_report(t: Timings) -> str:
    q = t.quality
    lines = [
        f"  音频时长 {t.duration:.2f}s，静音段 {len(t.silences)} 个，句数 {len(t.sentences)}",
        f"  切点命中停顿 {q.get('pause_cuts')}/{q.get('total_cuts')}"
        f"（{q.get('cut_coverage', 0) * 100:.0f}%），"
        f"平均偏差 {q.get('mean_dev')}s，最大偏差 {q.get('max_dev')}s",
    ]
    if q.get("cut_coverage", 0) < 0.6:
        lines.append("  ⚠ 停顿切点命中率偏低，建议听一遍并手工微调 cache 里的时间轴")
    return "\n".join(lines)
