"""SG・G1でのコース別成績(全24場、直近3年)。

一般戦とSG・G1では相手が違うので、同じ選手でもコースごとの1着率・連対率が変わる。
SG・G1のレースでだけ、その選手のSG・G1でのコース別 1着率・2連対率・3連対率を予想の材料に使う。
走数が少ないときは、その選手の全グレードのコース別成績に寄せる(K走ぶん)。

- model.py は index()/feature() で、レースの日より前の分だけを使って材料を作る
- 画面と pred.js は main() が書く data/model/gcourse.json(今日時点の値)を使う
"""
import bisect
import collections
import datetime as dt
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, load_grades, parse_results, sections  # noqa: E402

YEARS = 3
K = 8


def load_rows():
    """K成績ファイル(2020年〜、全場)から (日付文字列, 登番, コース, 着, SG・G1なら1)"""
    G = load_grades()
    rows = []
    files = sorted(glob.glob(os.path.join(DATA, "raw_old", "K", "*", "*.gz")) +
                   glob.glob(os.path.join(DATA, "raw", "K", "*", "*.all.txt.gz")))
    seen = set()
    for f in files:
        ds = "20" + os.path.basename(f)[1:7]
        if ds in seen:
            continue
        seen.add(ds)
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                txt = fh.read()
        except Exception:
            continue
        secs = sections(txt, "K", all_venues=True)
        for code, lines in (secs.items() if isinstance(secs, dict) else secs):
            g = (G.get((code, f"{ds[:4]}-{ds[4:6]}-{ds[6:]}")) or {}).get("grade")
            sg = 1 if g in ("SG", "G1", "PG1") else 0
            try:
                res = parse_results(lines)
            except Exception:
                continue
            for r in res.values():
                for e in (r.get("entries") or {}).values():
                    c, p = e.get("course"), e.get("place")
                    if e.get("regno") and c and 1 <= c <= 6 and p:
                        rows.append((ds, e["regno"], c, p, sg))
    return rows


def index(rows):
    """(登番, コース, SG・G1か) -> (日付の並び, 1着・2連対・3連対の累積)"""
    acc = collections.defaultdict(list)
    for ds, rg, c, p, sg in rows:
        acc[(rg, c, sg)].append((ds, p))
    idx = {}
    for key, xs in acc.items():
        xs.sort()
        d, a1, a2, a3 = [], [], [], []
        s1 = s2 = s3 = 0
        for ds, p in xs:
            s1 += p == 1
            s2 += p <= 2
            s3 += p <= 3
            d.append(ds); a1.append(s1); a2.append(s2); a3.append(s3)
        idx[key] = (d, a1, a2, a3)
    return idx


def _cnt(idx, rg, c, sg, since, until):
    t = idx.get((rg, c, sg))
    if not t:
        return [0, 0, 0, 0]
    a, b = bisect.bisect_left(t[0], since), bisect.bisect_left(t[0], until)
    if b <= a:
        return [0, 0, 0, 0]
    f = lambda arr: arr[b - 1] - (arr[a - 1] if a > 0 else 0)
    return [b - a, f(t[1]), f(t[2]), f(t[3])]


def feature(idx, rg, c, day, include_day=False):
    """その日より前(include_day=True ならその日まで)3年の SG・G1 コース別 1着・2連対・3連対率(寄せあり)×10。走ったことがなければ None"""
    since = (day - dt.timedelta(days=365 * YEARS)).strftime("%Y%m%d")
    until = (day + dt.timedelta(days=1 if include_day else 0)).strftime("%Y%m%d")
    s = _cnt(idx, rg, c, 1, since, until)
    o = _cnt(idx, rg, c, 0, since, until)
    n = s[0] + o[0]
    if n == 0:
        return None
    tot = [(s[i] + o[i]) / n for i in (1, 2, 3)]
    return [round((s[i + 1] + K * tot[i]) / (s[0] + K) * 10, 4) for i in range(3)]


def main():
    rows = load_rows()
    idx = index(rows)
    today = dt.datetime.now(JST).date()
    regs = {rg for ds, rg, *_ in rows if ds >= (today - dt.timedelta(days=365)).strftime("%Y%m%d")}
    out = {}
    for rg in sorted(regs):
        v = {str(c): feature(idx, rg, c, today, include_day=True) for c in range(1, 7)}
        v = {c: x for c, x in v.items() if x}
        if v:
            out[rg] = v
    p = os.path.join(DATA, "model", "gcourse.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(), "k": K, "years": YEARS, "racers": out}, f, ensure_ascii=False, separators=(",", ":"))
    print("SG・G1コース別成績", len(out), "人", len(rows), "走")


if __name__ == "__main__":
    main()
