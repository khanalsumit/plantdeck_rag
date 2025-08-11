#!/usr/bin/env python3
import json, collections
from pathlib import Path

RAW = Path("build/raw_pages.jsonl")
stats = collections.defaultdict(lambda: {"pages":0,"with_text":0})

with open(RAW, "r", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        if "_meta" in rec: 
            continue
        pdf = rec["pdf"]
        stats[pdf]["pages"] += 1
        if (rec.get("text") or "").strip():
            stats[pdf]["with_text"] += 1

print(f"{'PDF':42}  pages  with_text  coverage")
for pdf, s in stats.items():
    cov = (s['with_text']/s['pages']*100) if s['pages'] else 0
    print(f"{pdf[:42]:42}  {s['pages']:5d}     {s['with_text']:5d}    {cov:6.1f}%")
