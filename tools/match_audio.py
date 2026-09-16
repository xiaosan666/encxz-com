#!/usr/bin/env python3
"""全库音频 ↔ 文稿反向匹配。

把 data/ 下**所有** mp3 转写一遍（结果缓存在 cache/asr-cache/），
再与 en.xlsx 全部行的英文稿做词级比对，回答两个问题：

1. 某一行需要的音频，是不是其实存在于 data/ 里、只是文件名不对？
2. data/ 里那些没被表格引用的孤立音频，分别对应哪一行（或不属于任何一行）？

    python tools/match_audio.py            # 用缓存，只做匹配
    python tools/match_audio.py --refresh  # 重新转写所有音频（约 20 分钟）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_gen import config                                  # noqa: E402
from video_gen.asr import transcribe_words, norm_tokens        # noqa: E402
from video_gen.data import index_audio, load_dataset          # noqa: E402

CACHE = config.CACHE_DIR / "asr-cache"


def transcribe_all(entries: list[Path], refresh: bool) -> dict[str, list[str]]:
    """把每个音频转写成 token 列表，带磁盘缓存。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[str]] = {}
    todo = []
    for p in entries:
        cf = CACHE / f"{p.stem}.json"
        if cf.exists() and not refresh:
            out[p.name] = json.loads(cf.read_text("utf-8"))
        else:
            todo.append(p)

    print(f"缓存命中 {len(out)} 个，需转写 {len(todo)} 个", flush=True)
    for n, p in enumerate(todo, 1):
        t0 = time.time()
        try:
            words, _ = transcribe_words(p, "small")
            toks = [w[0] for w in words]
        except Exception as exc:                              # noqa: BLE001
            print(f"  [{n}/{len(todo)}] {p.name} 失败：{exc}", flush=True)
            toks = []
        (CACHE / f"{p.stem}.json").write_text(
            json.dumps(toks, ensure_ascii=False), "utf-8")
        out[p.name] = toks
        print(f"  [{n}/{len(todo)}] {p.name}  {len(toks)} 词  "
              f"{time.time() - t0:.1f}s", flush=True)
    return out


def dice(a: Counter, b: Counter) -> float:
    """Dice 相似度：2|交集| / (|a|+|b|)，对长度差异不敏感。"""
    if not a or not b:
        return 0.0
    inter = sum((a & b).values())
    return 2 * inter / (sum(a.values()) + sum(b.values()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="重新转写所有音频")
    ap.add_argument("--top", type=int, default=3, help="每项显示前几名候选")
    args = ap.parse_args()

    audio = index_audio()                       # norm_name -> path
    files = sorted({p for p in audio.values()})
    rows = load_dataset()

    print(f"data/ 下 {len(files)} 个音频，表格 {len(rows)} 行\n")
    asr = transcribe_all(files, args.refresh)

    asr_cnt = {name: Counter(toks) for name, toks in asr.items()}
    row_cnt = {r.excel_row: Counter(norm_tokens(r.script_en)) for r in rows}
    row_of = {r.excel_row: r for r in rows}

    # ---------------- 每行找最佳音频 ----------------
    print("\n" + "=" * 78)
    print("按「行」看：最佳匹配音频与当前 D 列是否一致")
    print("=" * 78)
    suspicious = []
    for r in rows:
        if not r.mp3_path:
            continue
        scores = sorted(((dice(row_cnt[r.excel_row], c), n) for n, c in asr_cnt.items()),
                        reverse=True)
        best = scores[0]
        cur = dice(row_cnt[r.excel_row], asr_cnt.get(r.mp3_path.name, Counter()))
        tag = ""
        if best[1] != r.mp3_path.name and best[0] - cur > 0.15:
            tag = f"   <== 更像 {best[1]}（{best[0]:.2f} vs 当前 {cur:.2f}）"
            suspicious.append((r, cur, best))
        if cur < 0.75 or tag:
            print(f"row{r.excel_row:>4} {r.level:<3} 当前 {r.mp3_path.name[:44]:<46} "
                  f"{cur:.2f}{tag}")

    print("\n" + "=" * 78)
    print("按「音频」看：每个音频最像哪一行（含未被引用的孤立音频）")
    print("=" * 78)
    used = {r.mp3_path.name for r in rows if r.mp3_path}
    orphans = [n for n in asr if n not in used]
    print(f"\n孤立音频 {len(orphans)} 个：")
    for n in sorted(orphans):
        scores = sorted(((dice(row_cnt[rn], asr_cnt[n]), rn) for rn in row_cnt),
                        reverse=True)
        top = ", ".join(f"row{rn}({s:.2f})" for s, rn in scores[:args.top])
        verdict = "无对应行" if scores[0][0] < 0.35 else "可能是"
        print(f"  {n[:52]:<54} {verdict} {top}")

    print(f"\n存在更优匹配的行：{len(suspicious)}")
    Path(config.CACHE_DIR / "audio-match.json").write_text(json.dumps({
        "suspicious": [{"row": r.excel_row, "level": r.level, "title": r.title_en,
                        "assigned": r.mp3_path.name, "assigned_score": round(c, 3),
                        "better": b[1], "better_score": round(b[0], 3)}
                       for r, c, b in suspicious],
        "orphans": sorted(orphans),
    }, ensure_ascii=False, indent=2), "utf-8")
    print(f"结果已写入 {config.CACHE_DIR / 'audio-match.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
