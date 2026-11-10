#!/usr/bin/env python3
"""公式サイトの直前情報(展示タイム・チルト・部品交換・スタート展示・水面気象)を取る。

締切の少し前から締切後までのレースだけを取りに行き、
展示がそろったら data/before/<日付>/<場>_<R>.json に保存する。
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, VENUES  # noqa: E402

URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
# 締切の何分前から何分後まで取りに行くか(GitHub の定期実行は遅れることがあるので広めに)
BEFORE_MIN, AFTER_MIN = 45, 20


def text(s):
    s = re.sub(r"<[^>]+>", " ", s).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse(html):
    out = {"entries": {}, "start": [], "weather": {}}
    for tb in re.findall(r'<tbody class="is-fs12[^"]*">(.*?)</tbody>', html, re.S):
        tds = [text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tb, re.S)]
        if len(tds) < 13 or not tds[0].isdigit():
            continue
        boat = int(tds[0])
        out["entries"][str(boat)] = {
            "boat": boat, "name": tds[2], "weight": tds[3].replace("kg", "") or None,
            "exh": num(tds[4]), "tilt": num(tds[5]), "propeller": tds[6] or None,
            "parts": tds[7].split() if tds[7] else [],
            "adjust": num(tds[12]),
        }
    m = re.search(r"スタート展示.*?<tbody[^>]*>(.*?)</tbody>", html, re.S)
    if m:
        for course, td in enumerate(re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S), start=1):
            t = text(td)
            bm = re.match(r"^([1-6])\s*([FL]?\.?\d*)", t)
            if bm:
                out["start"].append({"course": course, "boat": int(bm.group(1)), "st": bm.group(2) or None})
    body = text(html[html.find("水面気象情報"):][:3000]) if "水面気象情報" in html else ""
    w = out["weather"]
    for key, pat, cast in (("air", r"気温\s*([\d.]+)", float), ("wind", r"風速\s*(\d+)", int),
                           ("water", r"水温\s*([\d.]+)", float), ("wave", r"波高\s*(\d+)", int)):
        mm = re.search(pat, body)
        if mm:
            w[key] = cast(mm.group(1))
    sky = re.search(r"気温\s*[\d.]+℃\s*(\S+)", body)
    if sky:
        w["sky"] = sky.group(1)
    wd = re.search(r"is-wind(\d+)", html[html.find("水面気象情報"):] if "水面気象情報" in html else "")
    if wd:
        w["wind_dir"] = int(wd.group(1))  # 1〜16(北から時計回り)
    at = re.search(r"(\d+)R時点", body)
    if at:
        w["as_of"] = int(at.group(1))
    return out


def complete(info):
    ents = info["entries"].values()
    return len(info["entries"]) == 6 and all(e["exh"] is not None for e in ents if e.get("weight"))


def main():
    now = dt.datetime.now(JST)
    today = now.date()
    day_path = os.path.join(DATA, "races", f"{today:%Y}", f"{today:%Y%m%d}.json")
    if not os.path.exists(day_path):
        print("今日の出走表がまだありません")
        return
    with open(day_path, encoding="utf-8") as f:
        day = json.load(f)
    outdir = os.path.join(DATA, "before", f"{today:%Y%m%d}")
    got = 0
    for code, v in day["venues"].items():
        for no, race in sorted(v["races"].items(), key=lambda x: int(x[0])):
            if not race.get("deadline"):
                continue
            hh, mm = map(int, race["deadline"].split(":"))
            dl = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (dl - dt.timedelta(minutes=BEFORE_MIN) <= now <= dl + dt.timedelta(minutes=AFTER_MIN)):
                continue
            path = os.path.join(outdir, f"{code}_{int(no):02d}.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    if json.load(f).get("complete"):
                        continue
            url = URL.format(rno=int(no), jcd=code, hd=f"{today:%Y%m%d}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal data collection)"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors="replace")
            except Exception as e:  # 1レース失敗しても続ける
                print(f"{VENUES[code]}{no}R: 取得失敗 {e}")
                continue
            time.sleep(1)
            info = parse(html)
            if not any(e["exh"] is not None for e in info["entries"].values()):
                print(f"{VENUES[code]}{no}R: 展示前")
                continue
            info.update({"complete": complete(info), "fetched": now.strftime("%H:%M")})
            os.makedirs(outdir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, separators=(",", ":"))
            got += 1
            print(f"{VENUES[code]}{no}R: 保存" + ("" if info["complete"] else "(途中)"))
    print(f"直前情報 {got}件")


if __name__ == "__main__":
    main()
