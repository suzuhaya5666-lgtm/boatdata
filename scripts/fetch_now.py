#!/usr/bin/env python3
"""画面の「取得」ボタンから呼ぶ: 1レース分の直前情報・結果(速報)・オッズをその場で取って保存する。

使い方: python scripts/fetch_now.py --kind before|result|odds --venue 07 --race 5 [--date 20261113]
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST  # noqa: E402
import before  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["before", "result", "odds"])
    ap.add_argument("--venue", required=True)
    ap.add_argument("--race", type=int, required=True)
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    now = dt.datetime.now(JST)
    day = dt.datetime.strptime(a.date, "%Y%m%d").date() if a.date else now.date()
    code = a.venue.zfill(2)
    if a.kind == "before":
        ok = before.save_before(code, a.race, day, now)
    elif a.kind == "result":
        ok = before.save_result(code, a.race, day)
    else:
        from odds import get_race_full
        o = get_race_full(a.race, code, f"{day:%Y%m%d}")
        ok = bool(o and o.get("3t"))
        if ok:
            o["time"] = dt.datetime.now(JST).strftime("%H:%M")
            outdir = os.path.join(DATA, "live", f"{day:%Y%m%d}")
            os.makedirs(outdir, exist_ok=True)
            with open(os.path.join(outdir, f"{code}_{a.race:02d}.json"), "w", encoding="utf-8") as f:
                json.dump(o, f, separators=(",", ":"))
    print(f"{code} {a.race}R {a.kind}: {'保存しました' if ok else 'まだ公開されていません'}")
    if ok:
        before.push()


if __name__ == "__main__":
    main()
