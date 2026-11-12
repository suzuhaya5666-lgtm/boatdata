"""節間(その節の前日まで)の展示タイム・オリジナル展示の平均を選手ごとにまとめる。

  python scripts/series.py YYYYMMDD [場コード...]   (省略時は 07)

data/series/YYYYMMDD/<場>.json に {"day": n日目, "racers": {登番: {exh, lap, turn, straight, n}}} を書く。
朝の(展示前の)出目確率で、節の2日目以降に使う。1日目は書かない。
"""
import datetime as dt
import gzip
import json
import os
import sys

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def load(path):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def races_of(day, code):
    d = load(os.path.join(DATA, "races", f"{day:%Y}", f"{day:%Y%m%d}.json"))
    return (d or {}).get("venues", {}).get(code)


def org_of(day, code, no):
    b = load(os.path.join(DATA, "before", f"{day:%Y%m%d}", f"{code}_{no:02d}.json"))
    if b and b.get("org"):
        return b["org"]
    p = load(os.path.join(DATA, "org_past", f"{day:%Y}", f"{day:%Y%m%d}.json.gz")) or {}
    return p.get(f"{code}_{no:02d}") or {}


def build(day, code):
    v = races_of(day, code)
    if not v:
        return None
    n = v.get("day") or 1
    if n <= 1:
        return None
    acc = {}
    for back in range(1, n):
        d = day - dt.timedelta(days=back)
        pv = races_of(d, code)
        if not pv or pv.get("title") != v.get("title"):
            continue
        for no, r in pv.get("races", {}).items():
            org = org_of(d, code, int(no))
            for e in r.get("entries", []):
                rg = e.get("regno")
                if not rg:
                    continue
                o = org.get(str(e["boat"])) or {}
                a = acc.setdefault(rg, {k: [] for k in ("exh", "lap", "turn", "straight")})
                for k, x in (("exh", e.get("exh")), ("lap", o.get("lap")), ("turn", o.get("turn")), ("straight", o.get("straight"))):
                    if x:
                        a[k].append(float(x))
    racers = {}
    for rg, a in acc.items():
        racers[rg] = {k: round(sum(x) / len(x), 4) for k, x in a.items() if x}
        racers[rg]["n"] = len(a["exh"])
    # 節間成績(出目確率の材料): 前日までの(着順 − 場×コース平均着順)の平均を走数で0に寄せる。model.py と同じ
    w = load(os.path.join(DATA, "model", "weights.json")) or {}
    cm, kk = (w.get("setsu_cm") or {}).get(code), w.get("setsu_k", 3)
    if cm:
        today = {e.get("regno") for r in v.get("races", {}).values() for e in r.get("entries", [])}
        hist = {}
        for back in range(1, 11):
            pv = races_of(day - dt.timedelta(days=back), code)
            hist[back] = {}
            for r in (pv or {}).get("races", {}).values():
                for e in r.get("entries", []):
                    c = e.get("course") or e.get("boat")
                    if e.get("place") is None and not e.get("status"):
                        continue  # 欠場・未確定
                    hist[back].setdefault(e.get("regno"), []).append((e.get("place") or 6) - cm[c])
        for rg in today:
            vals = []
            for back in range(1, 11):
                x = hist[back].get(rg)
                if not x:
                    break
                vals += x
            if vals:
                racers.setdefault(rg, {})["setsu"] = round(sum(vals) / len(vals) * len(vals) / (len(vals) + kk), 4)
                racers[rg]["setsu_n"] = len(vals)
    return {"day": n, "title": v.get("title"), "racers": racers}


def main():
    day = dt.datetime.strptime(sys.argv[1], "%Y%m%d").date()
    for code in sys.argv[2:] or ["07", "08", "11", "12", "19"]:
        out = build(day, code)
        if not out:
            print(f"節間平均 {code}: なし(1日目か開催なし)")
            continue
        path = os.path.join(DATA, "series", f"{day:%Y%m%d}", f"{code}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        print(f"節間平均 {code}: {out['day']}日目 {len(out['racers'])}人")


if __name__ == "__main__":
    main()
