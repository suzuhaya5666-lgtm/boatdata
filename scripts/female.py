#!/usr/bin/env python3
"""女子選手の登番一覧。選手の性別は手元のデータにないので、
ヴィーナス・オールレディース(女子戦)に出たことのある選手を女子とする(全24場の成績ファイルから)。

  python scripts/female.py   → data/model/female.json に書く
"""
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import load_grades, sections, parse_results, DATA  # noqa: E402


def female_set():
    grades = load_grades()
    days = {}
    for (code, d), g in grades.items():
        if (g or {}).get("type") in ("ヴィーナス", "レディース"):
            days.setdefault(d, set()).add(code)
    out = set()
    for d, codes in days.items():
        p = os.path.join(DATA, "raw", "K", d[:4], f"k{d[2:4]}{d[5:7]}{d[8:10]}.all.txt.gz")
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            text = f.read()
        for code, lines in sections(text, "K", all_venues=True).items():
            if code not in codes:
                continue
            for race in parse_results(lines).values():
                for e in race["entries"].values():
                    if e.get("regno"):
                        out.add(e["regno"])
    return out


if __name__ == "__main__":
    s = female_set()
    os.makedirs(os.path.join(DATA, "model"), exist_ok=True)
    with open(os.path.join(DATA, "model", "female.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, separators=(",", ":"))
    print(f"女子 {len(s)}人")
