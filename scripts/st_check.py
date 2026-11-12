#!/usr/bin/env python3
"""想定ST(出走表タブの赤字)と実際のSTを比べる(月ごとの確認用)。

  python scripts/st_check.py            # 先月
  python scripts/st_check.py 2026-11    # 指定した月

画面(index.html の stLine)と同じ作り方で、そのレースの時点までの成績から想定STを出し直す:
  ふだんのコース別平均(F持ちでないとき) + F持ちの遅れ(fdelta) + 前期F + 初日/2日目の遅れ(data/model/stdelay.json)
  + 初日の展示遅(展示.40以上 +.01)。3日目以降のF持ちで、F持ち中の実績が5本以上ならその平均。
fdelta は data/stats/fdelta.json(stats.py で作る)。なければ F持ちの遅れは 0 で計算する。
実際のSTは F・出遅れ・欠場を除く。表は Markdown で出す。
比較用に「補正0.5倍」(初日/2日目の遅れの補正を半分にした想定)の列も出す。
"""
import collections
import datetime as dt
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA  # noqa: E402
import stats  # noqa: E402

VENUES = ("07", "08", "11", "12", "19")
LOCALB = {"07": "愛知", "08": "愛知", "11": "滋賀", "12": "大阪", "19": "山口"}
PREVF = {1: [0.006, 0.003, 0], 2: [0.019, 0.013, 0.008]}
EXLATE = [(0.40, 0.010)]
HALF = 0.5  # 比較用の列: 遅れの補正(stdelay)をこの倍率にした想定
DIRS = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]
SLOTNAME = {"s1": "初日1走目", "s2": "初日2走目", "s3": "2日目", "s4": "3日目以降"}


def jload(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def before_of(day, code, no, cache={}):
    p = os.path.join(DATA, "before", day, f"{code}_{no:02d}.json")
    b = jload(p)
    if b:
        return b
    if day not in cache:
        g = os.path.join(DATA, "before_past", day[:4], f"{day}.json.gz")
        try:
            with gzip.open(g, "rt", encoding="utf-8") as f:
                cache[day] = json.load(f)
        except OSError:
            cache[day] = {}
    return cache[day].get(f"{code}_{no:02d}")


def main():
    if len(sys.argv) > 1:
        y, m = map(int, sys.argv[1].split("-"))
    else:
        t = dt.date.today().replace(day=1) - dt.timedelta(days=1)
        y, m = t.year, t.month
    first = dt.date(y, m, 1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    sd = jload(os.path.join(DATA, "model", "stdelay.json"))
    fd = (jload(os.path.join(DATA, "stats", "fdelta.json")) or {}).get("delta") or {}
    bins = lambda x, th: sum(1 for t in th if x > t)  # noqa: E731

    # 選手ごとの記録(そのレースの前まで)
    cn = collections.defaultdict(lambda: [0.0, 0])   # (登番, コース) F持ちでないときのST
    an = collections.defaultdict(lambda: [0.0, 0])   # 登番 全コース(F持ちでないとき)
    cf = collections.defaultdict(lambda: [0.0, 0])   # (登番, コース, f1/f2) F持ち中のST
    fcnt = collections.defaultdict(collections.Counter)  # 登番 -> 期 -> F数
    lastv, today_n = {}, collections.Counter()
    info = {}
    rows = []
    for date, code, race in sorted(stats.load_races(), key=lambda t: (t[0], t[1], t[2]["no"])):
        if date > last:
            break
        ents = list(race["entries"].values()) if isinstance(race["entries"], dict) else race["entries"]
        target = first <= date and code in VENUES
        day = date.strftime("%Y%m%d")
        if target and day not in info:
            info[day] = (jload(os.path.join(DATA, "races", day[:4], f"{day}.json")) or {}).get("venues", {})
        v = info.get(day, {}).get(code) if target else None
        bi = before_of(day, code, race["no"]) if v else None
        for e in ents:
            r, c = e.get("regno"), e.get("course")
            if not r or not c:
                continue
            st_raw, status = e.get("st") or "", e.get("status")
            stv = stats.st_value(st_raw)
            per = stats.f_period(date)
            fc = fcnt[r][per]
            if v and v.get("day"):
                ve = next((x for x in v["races"].get(str(race["no"]), {}).get("entries", []) if x.get("regno") == r), {})
                slot = ("s2" if today_n[(r, day, code)] else "s1") if v["day"] == 1 else "s3" if v["day"] == 2 else "s4"
                prev = (per[0], 1) if per[1] == 2 else (per[0] - 1, 2)
                fp = fcnt[r][prev]
                mi = m_ = date.month
                mi = m_ - 11 if m_ >= 11 else m_ + 1 if m_ <= 4 else m_ - 5
                prevf = PREVF[min(fp, 2)][min(2, mi // 2)] if not fc and fp else 0
                k = "f2" if fc >= 2 else "f1"
                pred = None
                label = SLOTNAME[slot]
                if fc and slot == "s4" and cf[(r, c, k)][1] >= 5:
                    pred = cf[(r, c, k)][0] / cf[(r, c, k)][1]
                    label = "F持ち(実績)"
                elif slot != "s4" or fc or prevf:
                    b = cn[(r, c)]
                    a = an[r]
                    base = b[0] / b[1] if b[1] else (a[0] / a[1] if a[1] else None)
                    if base is not None:
                        fadd = (fd.get(k, {}).get(str(c)) or fd.get(k, {}).get("0") or 0) if fc else 0
                        delay = 0
                        mm = (sd or {}).get("slots", {}).get(slot)
                        if mm:
                            w = (bi or {}).get("weather") or {}
                            lv = lastv.get((r, code))
                            gap = (date - lv).days if lv else 9999
                            wd = w.get("wind_dir")
                            dn = (DIRS[((wd - 1) % 16) // 2] if w.get("wind") else "無風") if isinstance(wd, int) else (wd or "-")
                            avg = a[0] / a[1] if a[1] else None
                            val = {"v": code, "帯": str(bins(avg, sd["bins"]["帯"])) if avg is not None else None,
                                   "cls": ve.get("class"), "local": "1" if ve.get("branch") == LOCALB[code] else "0",
                                   "間": str(bins(gap if slot == "s1" else 1, sd["bins"]["間"])),
                                   "風": str(bins(w["wind"] if w.get("wind") is not None else 2, sd["bins"]["風"])),
                                   "波": str(bins(w["wave"] if w.get("wave") is not None else 2, sd["bins"]["波"])),
                                   "天候": w.get("sky") or "-", "dir": f"{code}_{dn}", "fk": str(min(fc, 2))}
                            delay = sum(mm.get(col, {}).get(val[col], 0) for col in sd["cols"])
                        exadd = 0
                        if slot in ("s1", "s2"):
                            s = next((x.get("st") for x in (bi or {}).get("start") or [] if x.get("boat") == e.get("boat")), None)
                            x = stats.st_value(s or "")
                            exadd = next((a_ for t, a_ in EXLATE if x is not None and x >= t), 0)
                            if exadd:
                                label += "・展示遅"
                        if fc:
                            label = "F持ち" if slot == "s4" else label + "・F持ち"
                        elif prevf:
                            label = "前期F" if slot == "s4" else label + "・前期F"
                        pred = base + fadd + prevf + delay + exadd
                        # 比較用: 初日/2日目の遅れの補正を半分にした想定(遅れの予想は当たりが弱く、控えめの方が良い可能性)
                        rows.append((label, slot, code, pred, base, stv, status, pred - delay * (1 - HALF)))
                if pred is not None and label == "F持ち(実績)":
                    rows.append((label, slot, code, pred, pred, stv, status, pred))
            # 記録を進める
            if stv is not None:
                if fc:
                    kk = "f2" if fc >= 2 else "f1"
                    cf[(r, c, kk)][0] += stv
                    cf[(r, c, kk)][1] += 1
                else:
                    cn[(r, c)][0] += stv
                    cn[(r, c)][1] += 1
                    an[r][0] += stv
                    an[r][1] += 1
            if st_raw.startswith("F") or (status or "").startswith("F"):
                fcnt[r][per] += 1
            today_n[(r, day, code)] += 1
        for e in ents:
            if e.get("regno"):
                lastv[(e["regno"], code)] = date

    ok = [x for x in rows if x[5] is not None and not x[6]]
    fl = sum(1 for x in rows if (x[6] or "").startswith("F"))
    print(f"## 想定STと実際のST {y}年{m}月(5場)\n")
    print(f"対象 {len(rows)}走(うち実際のSTがあるもの {len(ok)}走、本番F {fl}走)。単位は1/1000秒。ずれは＋なら実際が想定より遅い。\n")
    print("| 区分 | 走数 | 想定の平均誤差 | ずれ | 補正0.5倍の誤差 | ずれ | ふだんの平均だけの誤差 | ずれ |")
    print("|---|---|---|---|---|---|---|---|")

    def line(name, xs):
        if not xs:
            return
        e1 = sum(abs(x[5] - x[3]) for x in xs) / len(xs) * 1000
        b1 = sum(x[5] - x[3] for x in xs) / len(xs) * 1000
        e0 = sum(abs(x[5] - x[4]) for x in xs) / len(xs) * 1000
        b0 = sum(x[5] - x[4] for x in xs) / len(xs) * 1000
        eh = sum(abs(x[5] - x[7]) for x in xs) / len(xs) * 1000
        bh = sum(x[5] - x[7] for x in xs) / len(xs) * 1000
        same = all(x[0] == "F持ち(実績)" for x in xs)  # 実績そのものが想定なので比べる元がない
        d = lambda v, f: "-" if same else format(v, f)  # noqa: E731
        print(f"| {name} | {len(xs)} | {e1:.1f} | {b1:+.1f} | {d(eh, '.1f')} | {d(bh, '+.1f')} | {d(e0, '.1f')} | {d(b0, '+.1f')} |")
    line("全体", ok)
    for s in ("s1", "s2", "s3"):
        line(SLOTNAME[s], [x for x in ok if x[1] == s and "F持ち" not in x[0] and "前期F" not in x[0]])
    line("F持ち(実績5本以上)", [x for x in ok if x[0] == "F持ち(実績)"])
    line("F持ち(平均+遅れ)", [x for x in ok if "F持ち" in x[0] and x[0] != "F持ち(実績)"])
    line("前期F", [x for x in ok if "前期F" in x[0]])
    line("初日の展示遅(.40以上)", [x for x in ok if "展示遅" in x[0]])
    print("\n| 場 | 走数 | 想定の平均誤差 | ずれ | 補正0.5倍の誤差 | ずれ |")
    print("|---|---|---|---|---|---|")
    for code in VENUES:
        xs = [x for x in ok if x[2] == code]
        if xs:
            print(f"| {code} | {len(xs)} | {sum(abs(x[5] - x[3]) for x in xs) / len(xs) * 1000:.1f} | {sum(x[5] - x[3] for x in xs) / len(xs) * 1000:+.1f} | {sum(abs(x[5] - x[7]) for x in xs) / len(xs) * 1000:.1f} | {sum(x[5] - x[7] for x in xs) / len(xs) * 1000:+.1f} |")


if __name__ == "__main__":
    main()
