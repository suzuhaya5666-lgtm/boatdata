#!/usr/bin/env python3
"""古い成績ファイル(全24場)のまとめ取り。分析用で、サイトや出目確率の学習には使わない。

保存先: data/raw_old/K/<年>/k<YYMMDD>.all.txt.gz(data/raw/K と同じ形。stats.py・model.py は読まない)

  python scripts/k_old.py --start 20200101 --end 20231109 --workers 3 --max-minutes 300
"""
import argparse
import datetime as dt
import gzip
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import download, DATA  # noqa: E402


def path(d):
    return os.path.join(DATA, "raw_old", "K", f"{d:%Y}", f"k{d:%y%m%d}.all.txt.gz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-minutes", type=float, default=300)
    a = ap.parse_args()
    t0 = time.time()
    d = dt.datetime.strptime(a.start, "%Y%m%d").date()
    end = dt.datetime.strptime(a.end, "%Y%m%d").date()
    todo = []
    while d <= end:
        if not os.path.exists(path(d)) and not os.path.exists(path(d) + ".none"):
            todo.append(d)
        d += dt.timedelta(days=1)
    print(f"取得する日 {len(todo)}件", flush=True)

    def one(d):
        if time.time() - t0 > a.max_minutes * 60:
            return d, False
        try:
            text = download("K", d)
        except Exception as e:
            print(f"{d} 失敗 {e}", flush=True)
            return d, None
        os.makedirs(os.path.dirname(path(d)), exist_ok=True)
        if text is None:   # その日のファイルが無い(開催なしなど)
            open(path(d) + ".none", "w").close()
        else:
            with gzip.open(path(d), "wt", encoding="utf-8") as f:
                f.write(text)
        return d, True

    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for d, ok in ex.map(one, todo):
            done += ok is True
            if done and done % 100 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
    left = len(todo) - done
    print(f"取得 {done}日、残り {left}日")


if __name__ == "__main__":
    main()
