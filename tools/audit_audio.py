#!/usr/bin/env python3
"""批量核查「en.xlsx 文稿」与「data/ 音频内容」是否一致。

对每一行做 ASR，转写结果与英文稿做词级模糊匹配，输出匹配率。
匹配率低的说明该行的音频与文稿对不上（拿错音频 / 文稿写错 / 缺失）。

    python tools/audit_audio.py                 # 全部有音频的行
    python tools/audit_audio.py --min-coverage 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_gen import config                                  # noqa: E402
from video_gen.align import probe_duration                     # noqa: E402
from video_gen.asr import align_by_asr, transcribe_words       # noqa: E402
from video_gen.data import load_dataset                       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small")
    ap.add_argument("--min-coverage", type=float, default=0.90)
    ap.add_argument("--out", default=str(config.CACHE_DIR / "audio-audit.json"))
    args = ap.parse_args()

    rows = [r for r in load_dataset() if r.mp3_path]
    print(f"待核查 {len(rows)} 行（模型 {args.model}）\n", flush=True)

    results = []
    t0 = time.time()
    for n, row in enumerate(rows, 1):
        try:
            words, lang = transcribe_words(row.mp3_path, args.model)
            if not words:
                raise RuntimeError("未识别到任何词")
            duration = probe_duration(row.mp3_path)     # 音频真实长度（不是最后一个词的结束）
            _pairs, cov = align_by_asr(row.sentences, words, duration)
        except Exception as exc:                                # noqa: BLE001
            print(f"[{n}/{len(rows)}] row{row.excel_row} 识别失败：{exc}", flush=True)
            results.append({"row": row.excel_row, "mp3": row.mp3_path.name,
                            "coverage": -1.0, "duration": 0.0, "error": str(exc)})
            continue

        results.append({"row": row.excel_row, "level": row.level,
                        "title": row.title_en, "mp3": row.mp3_path.name,
                        "coverage": round(cov, 3), "duration": round(duration, 2),
                        "asr_words": len(words)})
        flag = "  <== 文稿与音频可能不符" if cov < args.min_coverage else ""
        print(f"[{n}/{len(rows)}] row{row.excel_row:>4} {row.level:<3} "
              f"匹配率 {cov * 100:5.1f}%  时长 {duration:6.1f}s{flag}", flush=True)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")

    bad = [r for r in results if r["coverage"] < args.min_coverage]
    print(f"\n耗时 {time.time() - t0:.0f}s，结果已写入 {args.out}")
    print(f"匹配率低于 {args.min_coverage:.0%} 的行：{len(bad)}")
    for r in sorted(bad, key=lambda x: x["coverage"]):
        print(f"  row{r['row']:>4} {r.get('level', ''):<3} {r['coverage'] * 100:5.1f}%  "
              f"{r['mp3']}")
        print(f"        {r.get('title', '')[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
