"""语音识别（ASR）句级对齐。

用 faster-whisper 得到**词级时间戳**，再与 en.xlsx 里的英文稿做模糊匹配，
从而得到「每句话真实被念出来的起止时间」。这比"静音检测 + 长度估算"准确得多：
停顿只能说明"这里有换气"，而 ASR 能说明"这句话是在第几秒说的"。

对齐流程
--------
1. whisper 转写音频，拿到 [(词, 开始, 结束), ...]；
2. 把脚本和转写结果都切成小写词元，用 difflib 求最长匹配块；
3. 每个脚本词元取它匹配到的转写词元的开始时间，未匹配的用前后插值；
4. 每句的起点 = 该句第一个词元的时间，终点 = 下一句的起点。

如果匹配率过低（口音重 / 转写偏差大），会给出警告并可回退到静音检测方案。
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

# 必须在使用 huggingface_hub 之前设置：直连 huggingface.co 不通，改走镜像，
# 并禁用 Xet 协议（其 CAS 服务在本机不可达）。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from . import config                      # noqa: E402
from .align import Timing, Timings, probe_duration, sentences_fingerprint  # noqa: E402

_TOKEN = re.compile(r"[a-z0-9']+")

_MODELS: dict[tuple[str, str], object] = {}


def norm_tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def model_dir() -> Path:
    d = Path(config.MODELS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_model(model_size: str):
    """按需加载并缓存 whisper 模型（同一进程内只加载一次）。"""
    key = (model_size, str(model_dir()))
    if key in _MODELS:
        return _MODELS[key]
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:                     # pragma: no cover
        raise RuntimeError(
            "未安装 faster-whisper。请执行：\n"
            "    .venv/bin/pip install faster-whisper"
        ) from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8",
                         download_root=str(model_dir()))
    _MODELS[key] = model
    return model


def transcribe_words(audio: Path, model_size: str = "small",
                     beam_size: int = 1, vad: bool = True):
    """返回 (词列表 [(词, 开始, 结束)], 识别到的语言)。"""
    model = load_model(model_size)
    segments, info = model.transcribe(
        str(audio), language="en", word_timestamps=True,
        vad_filter=vad, beam_size=beam_size, condition_on_previous_text=False,
    )
    words: list[tuple[str, float, float]] = []
    for seg in segments:
        for w in (seg.words or []):
            for tok in norm_tokens(w.word):
                words.append((tok, float(w.start), float(w.end)))
    return words, getattr(info, "language", "en")


def align_by_asr(sentences, asr_words: list[tuple[str, float, float]],
                 duration: float) -> tuple[list[tuple[int, float, float]], float]:
    """把脚本句子对齐到 ASR 词时间戳。返回 ([(句号, 起, 止)], 词匹配率)。"""
    script_toks: list[str] = []
    owner: list[int] = []
    for s in sentences:
        for tok in norm_tokens(s.en):
            script_toks.append(tok)
            owner.append(s.index)

    asr_toks = [w[0] for w in asr_words]
    if not script_toks or not asr_toks:
        return [], 0.0

    sm = difflib.SequenceMatcher(a=script_toks, b=asr_toks, autojunk=False)
    times: list[float | None] = [None] * len(script_toks)
    matched = 0
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = asr_words[blk.b + k][1]
        matched += blk.size

    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        return [], 0.0
    for i in range(len(times)):
        if times[i] is not None:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is None:
            times[i] = times[nxt] if nxt is not None else 0.0
        elif nxt is None:
            times[i] = times[prev]
        else:
            frac = (i - prev) / (nxt - prev)
            times[i] = times[prev] + (times[nxt] - times[prev]) * frac

    starts: dict[int, float] = {}
    for i, t in enumerate(times):
        if owner[i] not in starts:
            starts[owner[i]] = t

    n = len(sentences)
    out: list[tuple[int, float, float]] = []
    for s in sentences:
        st = starts.get(s.index, 0.0)
        nxt = starts.get(s.index + 1, duration) if s.index + 1 < n else duration
        out.append((s.index, round(st, 3), round(max(nxt, st + 0.05), 3)))
    return out, matched / max(1, len(script_toks))


def asr_align(mp3: Path, sentences, model_size: str = "small",
              min_coverage: float = 0.95) -> Timings:
    """ASR 句级对齐，产出与静音检测方案同构的 Timings。

    `duration` 必须是**音频文件的真实长度**（ffprobe），不能用最后一个词的结束时间：
    录音结尾普遍带 1~3 秒静音，按最后一个词算会让时间轴比音频短，于是
    第二遍的起始时刻提前（字幕快于语音）、并且 -shortest 会把第二遍结尾剪掉。
    取 max 只是兜底：万一词时间戳越过了文件长度，也不至于把时间轴压短。
    """
    words, lang = transcribe_words(mp3, model_size)
    if not words:
        raise RuntimeError(f"{mp3.name}: ASR 未识别到任何词")
    duration = max(probe_duration(mp3), words[-1][2])
    pairs, coverage = align_by_asr(sentences, words, duration)

    timings = [
        Timing(i=i, start=st, end=en, en=s.en, cn=s.cn, para=s.para,
               expect=st, source="asr", dev=0.0)
        for (i, st, en), s in zip(pairs, sentences)
    ]
    return Timings(
        mp3=mp3.name,
        duration=round(duration, 3),
        method=f"asr:faster-whisper-{model_size}+difflib",
        engine="asr",
        fingerprint=sentences_fingerprint(sentences),
        params={"model": model_size, "language": lang,
                "min_coverage": min_coverage},
        sentences=timings,
        quality={"asr_words": len(words),
                 "token_coverage": round(coverage, 3),
                 "low_confidence": coverage < min_coverage},
    )


def format_report(t: Timings) -> str:
    q = t.quality
    cov = q.get("token_coverage", 0.0)
    line = (f"  ASR 对齐：识别到 {q.get('asr_words', 0)} 个词，"
            f"与脚本词匹配率 {cov * 100:.0f}%")
    if q.get("low_confidence"):
        line += "\n  ⚠ 匹配率偏低，建议用 --asr-model medium 或 --align-engine silence 重试"
    return line
