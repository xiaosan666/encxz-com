"""修正 cache/ 下的重命名：缓存文件名 = row<行号>-<mp3文件名主干>.<引擎>.timings.json，
只应替换其中的 mp3 主干，行号前缀与引擎后缀必须保持不变。
"""
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = sorted(glob.glob(str(ROOT / "work" / "titlecase-rename-*.json")))[-1]
log = json.load(open(LOG))

media = {Path(o).stem: Path(n).stem for o, n in log["files"] if o.endswith(".mp3")}
cache_entries = [(Path(o), Path(n)) for o, n in log["files"] if "/cache/" in o]

# 1) 先把缓存文件还原成旧名
reverted = 0
for old, new in cache_entries:
    if new.exists() and old.name != new.name:
        os.rename(new, old)
        reverted += 1
print(f"已还原缓存文件 {reverted} 个")

# 2) 按 mp3 主干做子串替换
fixed = []
for old, _ in cache_entries:
    name = old.name
    hit = None
    for o_stem, n_stem in sorted(media.items(), key=lambda kv: -len(kv[0])):
        if o_stem in name:
            hit = (o_stem, n_stem)
            break
    if not hit:
        print("  ! 未匹配到 mp3 主干：", name)
        continue
    target = old.with_name(name.replace(hit[0], hit[1]))
    if target != old:
        os.rename(old, target)
        fixed.append((str(old), str(target)))

print(f"已按主干修正 {len(fixed)} 个：")
for o, n in fixed[:5]:
    print("  ", Path(o).name, "->", Path(n).name)

log["cache_fix"] = fixed
Path(LOG).write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")

# 3) 校验：缓存文件名应与 video_gen.align.cache_path 预期一致
import sys
sys.path.insert(0, str(ROOT))
from video_gen.data import load_dataset
from video_gen.align import cache_path
rows = load_dataset()
missing = [r.id for r in rows if not cache_path(r).exists()]
present = [r.id for r in rows if cache_path(r).exists()]
print(f"\n时间轴缓存命中 {len(present)}/200，未命中 {missing}")
