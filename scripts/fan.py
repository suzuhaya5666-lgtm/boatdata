#!/usr/bin/env python3
"""公式のファン手帳データ(選手の期別成績、半年ごと)から選手の性別を取る。

  python scripts/fan.py   → data/model/fan_sex.json に {登番: 1(男子)/2(女子)} を書く
解凍に lha(lhasa)を使う(GitHub Actions で apt install lhasa)。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
URL = "https://www.boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan{}.lzh"


def candidates(today):
    """新しい順に fanYY04 / fanYY10 の名前"""
    out = []
    for y in range(today.year, today.year - 3, -1):
        for m in (10, 4):
            if dt.date(y, m, 1) <= today:
                out.append(f"{y % 100:02d}{m:02d}")
    return out


def parse(raw):
    """1行1選手の固定長(cp932): 登番4・氏名16・カナ15・支部4・級別2・年号1・生年月日6・性別1 …"""
    sex = {}
    for line in raw.split(b"\n"):
        if len(line) < 49 or not line[:4].isdigit():
            continue
        s = line[48:49]
        if s in (b"1", b"2"):
            sex[line[:4].decode()] = int(s)
    return sex


def main():
    today = dt.date.today()
    for name in candidates(today):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, f"fan{name}.lzh")
            r = subprocess.run(["curl", "-sS", "-f", "-A", "Mozilla/5.0", "-o", p, URL.format(name)], capture_output=True)
            if r.returncode != 0 or not os.path.exists(p):
                continue
            subprocess.run(["lha", "-xqw=" + td, p], capture_output=True)
            txt = [f for f in os.listdir(td) if f.lower().endswith(".txt")]
            if not txt:
                continue
            with open(os.path.join(td, txt[0]), "rb") as f:
                sex = parse(f.read())
        if len(sex) < 1000:
            continue
        out = os.path.join(DATA, "model", "fan_sex.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"source": f"fan{name}", "sex": sex}, f, separators=(",", ":"))
        print(f"fan{name}: {len(sex)}人(女子 {sum(v == 2 for v in sex.values())}人)")
        return
    print("ファン手帳データが取れませんでした")
    sys.exit(0)


if __name__ == "__main__":
    main()
