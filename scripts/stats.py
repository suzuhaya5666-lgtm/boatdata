#!/usr/bin/env python3
"""保存済みの競走成績から、場平均・選手別成績・モーター成績を計算する。

  data/stats/venues.json          対象5場のコース別・レース番号別成績(総合/一般戦/G2・G3/SG・G1/女子戦)
  data/stats/racers/<登番>.json   選手ごとのコース別成績(他艇含む)・平均ST・枠別着率・決まり手
  data/stats/motors.json          対象5場のモーター成績(今のモーターの使い始めから)

グレード区分(重ならない): ippan=一般戦(男女混合), g2g3=G2・G3, sgg1=SG・G1, women=女子戦(ヴィーナス・オールレディース)
all=総合 はすべて
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
from collect import DATA, JST, VENUES, load_grades, parse_results, sections  # noqa: E402

ALL_VENUES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川", "06": "浜名湖",
    "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国", "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門", "15": "丸亀", "16": "児島", "17": "宮島", "18": "徳山",
    "19": "下関", "20": "若松", "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}
# ナイター開催の場(レースの時刻が成績ファイルに無いため、場で判定する)
NIGHT = {"01", "07", "12", "15", "19", "20", "24"}
POINTS = {1: 10, 2: 8, 3: 6, 4: 4, 5: 2, 6: 1}
CATS = ("all", "ippan", "g2g3", "sgg1", "women")


def grade_cat(g):
    if g is None:
        return None
    if g["type"] in ("ヴィーナス", "レディース"):
        return "women"
    if g["grade"] in ("SG", "G1"):
        return "sgg1"
    if g["grade"] in ("G2", "G3"):
        return "g2g3"
    return "ippan"


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


def in_cat(cat, rec_cat):
    return cat == "all" or cat == rec_cat


# ---------------------------------------------------------------- 場平均
def venue_stats(races):
    v = {"course": {str(k): counter() for k in range(1, 7)},
         "kimarite": {str(k): {} for k in range(1, 7)}, "race": {}}
    for _, _, race, ents, _ in races:
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
    out = {}
    for no, r in sorted(v["race"].items(), key=lambda x: int(x[0])):
        yen = [t[0] for t in r["tri"]]
        out[no] = {
            "n": r["n"], "boat_win": r["boat_win"], "course_win": r["course_win"],
            "tri_avg": round(statistics.mean(yen)) if yen else None,
            "tri_median": round(statistics.median(yen)) if yen else None,
            "man_rate": round(sum(y >= 10000 for y in yen) / len(yen) * 100, 1) if yen else None,
            "fav_rate": round(sum(t[1] == 1 for t in r["tri"]) / len(yen) * 100, 1) if yen else None,
        }
    return {"course": v["course"], "kimarite": v["kimarite"], "race": out}


# ---------------------------------------------------------------- 選手
# 1走の記録: (日付, 場, コース, 着, 自分の決まり手, 負けた決まり手, 1コースが勝ったか, 区分, ST, 他艇[(コース, 着)])
def sheet(recs):
    """コース別全艇成績と平均ST。"""
    course, others = {}, {}
    st = {k: [] for k in ("total", "slow", "dash", "day", "night", "c1", "c2", "c3", "c4", "c5", "c6")}
    f = 0
    for date, code, c, place, _, _, _, _, st_raw, oth in recs:
        add(course.setdefault(str(c), counter()), place)
        o = others.setdefault(str(c), {})
        for oc, op in oth:
            add(o.setdefault(str(oc), counter()), op)
        if (st_raw or "").startswith("F"):
            f += 1
        v = st_value(st_raw)
        if v is not None:
            for k in ("total", f"c{c}", "slow" if c <= 3 else "dash", "night" if code in NIGHT else "day"):
                st[k].append(v)
    return {"course": course, "others": others, "f": f,
            "st": {k: {"n": len(x), "avg": round(statistics.mean(x), 3) if x else None} for k, x in st.items()}}


def bucket(recs):
    """枠別着率と決まり手。"""
    b = {"n": 0, "p1": 0, "p2": 0, "p3": 0, "win": {}, "lost": {}, "nigashi": 0}
    for _, _, _, place, own, lost, c1won, _, _, _ in recs:
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


def bycourse(recs, last):
    six = last - dt.timedelta(days=182)
    out = {k: {} for k in ("10", "6m", "1y", "local")}
    for c in range(1, 7):
        rc = [r for r in recs if r[2] == c]
        if not rc:
            continue
        sets = {"10": rc[-10:], "1y": rc, "6m": [r for r in rc if r[0] >= six]}
        for cat in CATS[1:]:
            sets[f"1y_{cat}"] = [r for r in rc if r[7] == cat]
            sets[f"6m_{cat}"] = [r for r in sets["6m"] if r[7] == cat]
        for k, rs in sets.items():
            if rs:
                out.setdefault(k, {})[str(c)] = bucket(rs)
        for code in VENUES:
            rl = [r for r in rc if r[1] == code]
            if rl:
                out["local"].setdefault(code, {})[str(c)] = bucket(rl)
    return out


# ---------------------------------------------------------------- モーター
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
    grades = load_grades()
    races = []
    for date, code, race in load_races():
        ents = [e for e in race["entries"].values() if started(e)]
        if len(ents) < 2:
            continue
        g = grades.get((code, date.isoformat()))
        cat = grade_cat(g)
        races.append((date, code, race, ents, cat))
    first = min(r[0] for r in races)
    last = max(r[0] for r in races)
    period = {"from": first.isoformat(), "to": last.isoformat(), "races": len(races),
              "graded": sum(r[4] is not None for r in races),
              "updated": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")}
    os.makedirs(os.path.join(DATA, "stats", "racers"), exist_ok=True)

    # ---- 場平均
    out = {"period": period, "venues": {}}
    for code, name in VENUES.items():
        mine = [r for r in races if r[1] == code]
        out["venues"][code] = {"name": name}
        for cat in CATS:
            out["venues"][code][cat] = venue_stats([r for r in mine if in_cat(cat, r[4])])
    with open(os.path.join(DATA, "stats", "venues.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    # ---- 選手・モーターの記録
    hist, names, venues_count, motors = {}, {}, {}, {}
    for date, code, race, ents, cat in races:
        km = race.get("kimarite")
        winner = next((e for e in ents if e.get("place") == 1), None)
        c1won = winner is not None and winner["course"] == 1
        for e in ents:
            oth = tuple((o["course"], o.get("place")) for o in ents if o is not e)
            hist.setdefault(e["regno"], []).append((
                date, code, e["course"], e.get("place"),
                km if e.get("place") == 1 and km else None,
                km if e["course"] == 1 and winner is not None and winner is not e and km else None,
                e["course"] >= 2 and c1won, cat, e.get("st"), oth))
            if e.get("name"):
                names[e["regno"]] = e["name"]
            vc = venues_count.setdefault(e["regno"], {})
            vc[code] = vc.get(code, 0) + 1
            if code in VENUES and e.get("motor_no") is not None:
                motors.setdefault(code, {}).setdefault(e["motor_no"], []).append(
                    (date, POINTS.get(e.get("place"), 0), e.get("place")))

    # ---- 選手
    for regno, recs in hist.items():
        rr = {"regno": regno, "name": names.get(regno, ""), "period": period,
              "last": recs[-1][0].isoformat(),
              "venues": {ALL_VENUES.get(k, k): n for k, n in sorted(venues_count[regno].items(), key=lambda x: -x[1])},
              "female": any(r[7] == "women" for r in recs),
              "sheet": {cat: sheet(rs) for cat in CATS
                        for rs in [[r for r in recs if in_cat(cat, r[7])]] if rs},
              "bycourse": bycourse(recs, last)}
        with open(os.path.join(DATA, "stats", "racers", f"{regno}.json"), "w", encoding="utf-8") as f:
            json.dump(rr, f, ensure_ascii=False, separators=(",", ":"))

    # ---- モーター
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

    print(f"集計: {period['from']}〜{period['to']} {len(races)}レース"
          f"(グレード判明 {period['graded']})、選手 {len(hist)}人")


if __name__ == "__main__":
    main()
