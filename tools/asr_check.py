#!/usr/bin/env python3
"""用语音识别核对 / 生成句级时间轴（诊断与人工确认用）。

用途
----
1. **核对**：把 ASR 的词级时间戳与 en.xlsx 脚本对齐，和「静音检测」方案逐句对比，
   一眼看出差在哪里；
2. **写入**：`--write` 把 ASR 结果写进 cache/<row>.timings.json（engine=asr），
   之后 `make_video.py` 会直接复用。

注意：`make_video.py` 默认已经使用 ASR 引擎，本工具主要用于对比诊断。

用法
----
    python tools/asr_check.py --rows 2                 # 打印对比表
    python tools/asr_check.py --rows 2-6 --write       # 写入 ASR 时间轴
    python tools/asr_check.py --rows 2 --model medium  # 换更大的模型
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_gen import config                                  # noqa: E402
from video_gen.align import cache_path, load_or_align          # noqa: E402
from video_gen.asr import asr_align, transcribe_words          # noqa: E402
from video_gen.data import load_dataset, select_rows           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="用 ASR 核对/生成句级时间轴")
    ap.add_argument("--rows", required=True, help="行号，支持 2-6 这样的范围")
    ap.add_argument("--model", default="small", help="faster-whisper 模型：base/small/medium")
    ap.add_argument("--write", action="store_true", help="用 ASR 结果覆盖时间轴缓存")
    ap.add_argument("--show-text", action="store_true", help="打印 ASR 转写文本")
    args = ap.parse_args()

    rows = [r for r in select_rows(load_dataset(), spec=args.rows) if r.mp3_path]
    if not rows:
        print("没有可用的行（缺音频）")
        return 1

    print(f"模型 {args.model}（缓存于 {config.MODELS_DIR}，"
          f"镜像 {__import__('os').environ.get('HF_ENDPOINT')}）\n")

    for row in rows:
        print(f"── row{row.excel_row}  {row.title_en[:44]}  ({row.mp3_path.name})")
        words, lang = transcribe_words(row.mp3_path, args.model)
        if not words:
            print("  ASR 没识别到任何词，跳过\n")
            continue
        if args.show_text:
            print("  ASR: " + " ".join(w[0] for w in words[:60]) + " ...")

        asr = asr_align(row.mp3_path, row.sentences, args.model)
        coverage = asr.quality["token_coverage"]
        print(f"  ASR 词数 {len(words)}（语言 {lang}），与脚本词匹配率 {coverage * 100:.0f}%")

        old = load_or_align(row, engine="silence")
        print(f"  {'句':>3} {'静音方案':>9} {'ASR 对齐':>9} {'差值':>7}   英文")
        worst = []
        for a, s in zip(asr.sentences, old.sentences):
            d = a.start - s.start
            worst.append((abs(d), a.i, d))
            print(f"  {a.i + 1:>3} {s.start:9.2f} {a.start:9.2f} {d:+7.2f}   {a.en[:44]}"
                  + ("  <<<" if abs(d) > 0.6 else ""))
        worst.sort(reverse=True)
        print("  → 偏差最大的三处："
              + ", ".join(f"第{i + 1}句 {d:+.2f}s" for _, i, d in worst[:3]) + "\n")

        if args.write:
            cache_path(row).write_text(
                json.dumps(asr.to_json(), ensure_ascii=False, indent=2), "utf-8")
            print(f"  已写入 {cache_path(row).name}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
