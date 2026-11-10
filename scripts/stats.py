#!/usr/bin/env python3
"""保存済みの競走成績から、場平均と選手別成績を計算する。

  data/stats/venues.json          対象5場のコース別・レース番号別成績
  data/stats/racers/<登番>.json   選手ごとのコース別成績(他艇含む)と平均ST
"""
import datetime as dt
import glob
import gzip
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, VENUES, parse_results, sections  # noqa: E402

ALL_VENUES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川", "06": "浜名湖",
    "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国", "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門", "15": "丸亀", "16": "児島", "17": "宮島", "18": "徳山",
    "19": "下関", "20": "若松", "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}
# ナイター開催の場(レースの時刻が成績ファイルに無いため、場で判定する)
NIGHT = {"01", "07", "12", "15", "19", "20", "24"}


def load_races():
    """(日付, 場コード, レース) を全期間ぶん返す。"""
    for p in sorted(glob.glob(os.path.join(DATA, "raw", "K", "*", "k*.txt*"))):
        name = os.path.basename(p)
        if not name.endswith(".all.txt.gz"):
            # 5場だけの古い保存は、全場版が同じ日にあれば使わない
            if os.path.exists(p.replace(".txt", ".all.txt.gz")):
                continue
        m = re.match(r"k(\d{6})", name)
        date = dt.datetime.strptime(m.group(1), "%y%m%d").date()
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt", encoding="utf-8") as f:
            text = f.read()
        for code, lines in sections(text, "K", all_venues=True).items():
            for race in parse_results(lines).values():
                yield date, code, race


def counter():
    return {"n": 0, "p1": 0, "p2": 0, "p3": 0}


def add(c, place):
    c["n"] += 1
    if place in (1, 2, 3):
        c[f"p{place}"] += 1


def st_value(s):
    """ST の数値。フライング・出遅れは平均に入れない。"""
    if not s or not re.match(r"^\d?\.\d+$", s):
        return None
    return float(s)


def started(e):
    """欠場(K0/K1)は出走に数えない。"""
    return e.get("course") is not None and not (e.get("status") or "").startswith("K")


POINTS = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2, 6: 1}
KM_KEYS = ("逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ")


def bucket(recs):
    """記録 [(日付, 場, コース, 着, 自分の決まり手, 負けた決まり手, 1コースが勝ったか)] を集計。"""
    b = {"n": 0, "p1": 0, "p2": 0, "p3": 0, "win": {}, "lost": {}, "nigashi": 0}
    for _, _, _, place, own, lost, c1won in recs:
        b["n"] += 1
        if place in (1, 2, 3):
            b[f"p{place}"] += 1
        if own:
            b["win"][own] = b["win"].get(own, 0) + 1
        if lost:
            b["lost"][lost] = b["lost"].get(lost, 0) + 1
        if c1won:
            b["nigashi"] += 1
    if not b["win"]:
        del b["win"]
    if not b["lost"]:
        del b["lost"]
    return b


def motor_starts():
    """番組表のモーター2連率が一斉に0に戻った日を、今のモーターの使い始めとする。"""
    starts = {}
    for code in VENUES:
        prev = None
        for p in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
            with open(p, encoding="utf-8") as f:
                day = json.load(f)
            v = day["venues"].get(code)
            if not v:
                continue
            m = [e["motor_2"] for r in v["races"].values() for e in r["entries"] if e.get("motor_2") is not None]
            if not m:
                continue
            med = statistics.median(m)
            if prev is not None and prev > 20 and med < 3:
                starts[code] = day["date"]
            prev = med
    return starts


def main():
    venues = {c: {"name": n, "course": {str(k): counter() for k in range(1, 7)},
                  "kimarite": {str(k): {} for k in range(1, 7)}, "race": {}}
              for c, n in VENUES.items()}
    racers = {}
    hist = {}
    motors = {}
    first = last = None
    nraces = 0

    for date, code, race in load_races():
        ents = [e for e in race["entries"].values() if started(e)]
        if len(ents) < 2:
            continue
        nraces += 1
        first = date if first is None or date < first else first
        last = date if last is None or date > last else last

        # ---- 場平均(対象5場)
        if code in venues:
            v = venues[code]
            for e in ents:
                add(v["course"][str(e["course"])], e.get("place"))
                if e.get("place") == 1 and race.get("kimarite"):
                    km = v["kimarite"][str(e["course"])]
                    km[race["kimarite"]] = km.get(race["kimarite"], 0) + 1
            r = v["race"].setdefault(str(race["no"]), {
                "n": 0, "boat_win": {str(b): 0 for b in range(1, 7)},
                "course_win": {str(c): 0 for c in range(1, 7)}, "tri": []})
            r["n"] += 1
            for e in ents:
                if e.get("place") == 1:
                    r["boat_win"][str(e["boat"])] += 1
                    r["course_win"][str(e["course"])] += 1
            tri = race.get("payouts", {}).get("3連単")
            if tri:
                r["tri"].append((tri[0]["yen"], tri[0]["pop"]))

        km = race.get("kimarite")
        winner = next((e for e in ents if e.get("place") == 1), None)
        c1won = winner is not None and winner["course"] == 1

        # ---- モーター(対象5場)
        if code in VENUES:
            for e in ents:
                if e.get("motor_no") is not None:
                    motors.setdefault(code, {}).setdefault(e["motor_no"], []).append(
                        (date, POINTS.get(e.get("place"), 0), e.get("place")))

        # ---- 選手別(全24場)
        for e in ents:
            hist.setdefault(e["regno"], []).append((
                date, code, e["course"], e.get("place"),
                km if e.get("place") == 1 and km else None,
                km if e["course"] == 1 and winner is not None and winner is not e and km else None,
                e["course"] >= 2 and c1won))
            rr = racers.setdefault(e["regno"], {
                "regno": e["regno"], "name": "", "course": {}, "others": {},
                "st": {k: [] for k in ("total", "slow", "dash", "day", "night",
                                        "c1", "c2", "c3", "c4", "c5", "c6")},
                "f": 0, "last": "", "venues": {}})
            if e.get("name"):
                rr["name"] = e["name"]
            c = str(e["course"])
            add(rr["course"].setdefault(c, counter()), e.get("place"))
            oth = rr["others"].setdefault(c, {})
            for o in ents:
                if o is not e:
                    add(oth.setdefault(str(o["course"]), counter()), o.get("place"))
            st = st_value(e.get("st"))
            if (e.get("st") or "").startswith("F"):
                rr["f"] += 1
            if st is not None:
                keys = ["total", f"c{c}", "slow" if e["course"] <= 3 else "dash",
                        "night" if code in NIGHT else "day"]
                for k in keys:
                    rr["st"][k].append(st)
            rr["venues"][code] = rr["venues"].get(code, 0) + 1
            rr["last"] = max(rr["last"], date.isoformat())

    period = {"from": first.isoformat() if first else None, "to": last.isoformat() if last else None,
              "races": nraces, "updated": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")}

    # ---- 書き出し: 場平均
    out = {"period": period, "venues": {}}
    for code, v in venues.items():
        races = {}
        for no, r in sorted(v["race"].items(), key=lambda x: int(x[0])):
            yen = [t[0] for t in r["tri"]]
            races[no] = {
                "n": r["n"], "boat_win": r["boat_win"], "course_win": r["course_win"],
                "tri_avg": round(statistics.mean(yen)) if yen else None,
                "tri_median": round(statistics.median(yen)) if yen else None,
                "man_rate": round(sum(y >= 10000 for y in yen) / len(yen) * 100, 1) if yen else None,
                "fav_rate": round(sum(t[1] == 1 for t in r["tri"]) / len(yen) * 100, 1) if yen else None,
            }
        out["venues"][code] = {"name": v["name"], "course": v["course"],
                               "kimarite": v["kimarite"], "race": races}
    os.makedirs(os.path.join(DATA, "stats", "racers"), exist_ok=True)
    with open(os.path.join(DATA, "stats", "venues.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    # ---- 書き出し: モーター
    starts = motor_starts()
    mout = {"period": period, "venues": {}}
    for code, ms in motors.items():
        start = dt.date.fromisoformat(starts[code]) if code in starts else first
        spans = {"1m": last - dt.timedelta(days=30), "3m": last - dt.timedelta(days=91),
                 "6m": last - dt.timedelta(days=182), "all": start}
        res = {}
        for no, recs in ms.items():
            res[str(no)] = {}
            for key, since in spans.items():
                rs = [r for r in recs if r[0] >= max(since, start)]
                if rs:
                    res[str(no)][key] = {"n": len(rs), "pts": sum(r[1] for r in rs),
                                         "p1": sum(r[2] == 1 for r in rs), "p2": sum(r[2] == 2 for r in rs),
                                         "p3": sum(r[2] == 3 for r in rs)}
        mout["venues"][code] = {"start": start.isoformat(), "motors": res}
    with open(os.path.join(DATA, "stats", "motors.json"), "w", encoding="utf-8") as f:
        json.dump(mout, f, ensure_ascii=False, separators=(",", ":"))

    # ---- 書き出し: 選手別
    six = last - dt.timedelta(days=182)
    for regno, rr in racers.items():
        recs = hist.get(regno, [])
        byc = {"10": {}, "6m": {}, "1y": {}, "local": {}}
        for c in range(1, 7):
            rc = [r for r in recs if r[2] == c]
            if not rc:
                continue
            byc["10"][str(c)] = bucket(rc[-10:])
            byc["1y"][str(c)] = bucket(rc)
            r6 = [r for r in rc if r[0] >= six]
            if r6:
                byc["6m"][str(c)] = bucket(r6)
            for code in VENUES:
                rl = [r for r in rc if r[1] == code]
                if rl:
                    byc["local"].setdefault(code, {})[str(c)] = bucket(rl)
        rr["bycourse"] = byc
        st = {}
        for k, vals in rr["st"].items():
            st[k] = {"n": len(vals), "avg": round(statistics.mean(vals), 3) if vals else None}
        rr["st"] = st
        rr["venues"] = {ALL_VENUES.get(k, k): n for k, n in sorted(rr["venues"].items(), key=lambda x: -x[1])}
        rr["period"] = period
        with open(os.path.join(DATA, "stats", "racers", f"{regno}.json"), "w", encoding="utf-8") as f:
            json.dump(rr, f, ensure_ascii=False, separators=(",", ":"))
    print(f"集計: {period['from']}〜{period['to']} {nraces}レース、選手 {len(racers)}人")


if __name__ == "__main__":
    main()
