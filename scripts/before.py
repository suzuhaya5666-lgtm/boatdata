#!/usr/bin/env python3
"""公式サイトの直前情報(展示タイム・チルト・部品交換・スタート展示・水面気象)を取る。

締切の少し前から締切後までのレースだけを取りに行き、
展示がそろったら data/before/<日付>/<場>_<R>.json に保存する。
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, VENUES  # noqa: E402
import orgtenji  # noqa: E402

URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
# 締切の何分前から何分後まで取りに行くか(GitHub の定期実行は遅れることがあるので広めに)
BEFORE_MIN, AFTER_MIN = 45, 20
# オッズは締切20分前から締切まで、毎回取り直す
ODDS_BEFORE_MIN = 20


def fetch_live_odds(day, now):
    """締切20分前〜締切のレースのオッズを取り、data/live/<日付>/<場>_<R>.json に保存する。"""
    from concurrent.futures import ThreadPoolExecutor
    from odds import get_race_full
    targets = []
    for code, v in day["venues"].items():
        for no, race in v["races"].items():
            if not race.get("deadline"):
                continue
            hh, mm = map(int, race["deadline"].split(":"))
            dl = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dl - dt.timedelta(minutes=ODDS_BEFORE_MIN) <= now <= dl + dt.timedelta(minutes=2):
                targets.append((code, int(no)))
    if not targets:
        return 0
    outdir = os.path.join(DATA, "live", f"{now:%Y%m%d}")

    def one(t):
        code, no = t
        try:
            o = get_race_full(no, code, f"{now:%Y%m%d}")
        except Exception as e:
            return f"{VENUES[code]}{no}R オッズ失敗 {e}"
        if not o or not o["3t"]:
            return f"{VENUES[code]}{no}R オッズまだ"
        o["time"] = dt.datetime.now(JST).strftime("%H:%M")
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, f"{code}_{no:02d}.json"), "w", encoding="utf-8") as f:
            json.dump(o, f, separators=(",", ":"))
        return None

    with ThreadPoolExecutor(max_workers=3) as ex:
        for msg in ex.map(one, targets):
            if msg:
                print(msg)
    print(f"{now:%H:%M} オッズ {len(targets)}レース")
    return len(targets)


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


def fetch_once():
    """締切が近いレースの直前情報を取る。戻り値: (保存件数, 今日のまだ終わっていないレースがあるか)"""
    now = dt.datetime.now(JST)
    today = now.date()
    day_path = os.path.join(DATA, "races", f"{today:%Y}", f"{today:%Y%m%d}.json")
    if not os.path.exists(day_path):
        print("今日の出走表がまだありません")
        return 0, now.hour < 12  # 朝の出走表取り込み前なら待つ
    with open(day_path, encoding="utf-8") as f:
        day = json.load(f)
    outdir = os.path.join(DATA, "before", f"{today:%Y%m%d}")
    got = 0
    remaining = False
    for code, v in day["venues"].items():
        for no, race in sorted(v["races"].items(), key=lambda x: int(x[0])):
            if not race.get("deadline"):
                continue
            hh, mm = map(int, race["deadline"].split(":"))
            dl = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now <= dl + dt.timedelta(minutes=AFTER_MIN):
                remaining = True
            if not (dl - dt.timedelta(minutes=BEFORE_MIN) <= now <= dl + dt.timedelta(minutes=AFTER_MIN)):
                continue
            path = os.path.join(outdir, f"{code}_{int(no):02d}.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    if json.load(f).get("complete"):
                        continue
            url = URL.format(rno=int(no), jcd=code, hd=f"{today:%Y%m%d}")
            r = subprocess.run(["curl", "-sS", "-4", "--max-time", "20",
                                "-A", "Mozilla/5.0 (personal data collection)", url], capture_output=True)
            if r.returncode != 0:  # 1レース失敗しても続ける
                print(f"{VENUES[code]}{no}R: 取得失敗 {r.stderr.decode(errors='replace').strip()}")
                continue
            html = r.stdout.decode("utf-8", errors="replace")
            time.sleep(1)
            info = parse(html)
            if not any(e["exh"] is not None for e in info["entries"].values()):
                print(f"{VENUES[code]}{no}R: 展示前")
                continue
            if code in orgtenji.VENUES:  # 場独自のオリジナル展示(一周・まわり足・直線)
                org = orgtenji.fetch(code, today, int(no))
                if org:
                    info["org"] = org
            done = complete(info) and (code not in orgtenji.VENUES or "org" in info)
            info.update({"complete": done, "fetched": now.strftime("%H:%M")})
            os.makedirs(outdir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, separators=(",", ":"))
            got += 1
            print(f"{VENUES[code]}{no}R: 保存" + ("" if info["complete"] else "(途中)"))
    print(f"{now:%H:%M} 直前情報 {got}件")
    try:
        fetch_live_odds(day, now)
    except Exception as e:  # オッズが失敗しても直前情報は続ける
        print(f"オッズ失敗 {e}")
    return got, remaining


def push():
    """取れた分をすぐ GitHub に保存する(画面はリポジトリから直接読む)。"""
    def run(*a):
        return subprocess.run(list(a), capture_output=True, text=True)
    paths = [p for p in (os.path.join(DATA, "before"), os.path.join(DATA, "live")) if os.path.exists(p)]
    if not paths:
        return
    run("git", "add", *paths)
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-m", f"直前情報 {dt.datetime.now(JST):%Y-%m-%d %H:%M}")
    for _ in range(3):
        run("git", "pull", "--rebase", "-q")
        if run("git", "push", "-q").returncode == 0:
            return
        time.sleep(5)
    print("保存に失敗(次の回でまとめて保存します)")


def main():
    # --loop 分: 指定した時間まで5分ごとに取り続ける(GitHub の定期実行は遅れたり飛んだりするため)
    if "--loop" not in sys.argv:
        fetch_once()
        return
    limit = float(sys.argv[sys.argv.index("--loop") + 1])
    start = time.time()
    while True:
        t_cycle = time.time()
        try:
            _, remaining = fetch_once()
            push()
        except Exception as e:  # 1回失敗しても続ける
            print(f"失敗 {e}")
            remaining = True
        if not remaining:
            print("今日のレースは終わりました")
            break
        if time.time() - start > limit * 60:
            print("時間になったので終了(次の実行に引き継ぎ)")
            break
        time.sleep(max(30, 300 - (time.time() - t_cycle)))  # 5分ごと(取得にかかった時間を差し引く)



if __name__ == "__main__":
    main()
