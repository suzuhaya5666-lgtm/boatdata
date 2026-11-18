"""グランプリ公式サイトの獲得賞金ランキング(60位まで)を data/gp/ranking.json に保存する。

前回の順位も残しておき、画面で上がった/下がったを出せるようにする。
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
URL = "https://www.boatrace-grandprix.jp/2026/rtg/sp/ranking.php"
OUT = os.path.join(DATA, "gp", "ranking.json")
JST = timezone(timedelta(hours=9))


def get(url):
    r = subprocess.run(["curl", "-sS", "-4", "-L", "--max-time", "30", "-A", "Mozilla/5.0", url], capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


def text(s):
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", s)).replace("　", "")


def parse(html):
    m = re.search(r"<caption><span>(.*?)</span></caption>", html)
    asof = text(m.group(1)) if m else None
    rows = []
    for tr in re.findall(r'<tr data="(\d{4})"[^>]*>(.*?)</tr>', html, re.S):
        reg, body = tr
        rk = re.search(r'<span class="rank[^"]*">(\d+)</span>', body)
        nm = re.search(r'<span class="racer">(.*?)</span>', body)
        sb = re.search(r'<span class="shibu">(.*?)</span>', body)
        ky = re.search(r'<span class="kyu">(.*?)</span>', body)
        mo = re.search(r'<span class="money">([\d,]+)</span>', body)
        if not (rk and mo):
            continue
        name = re.sub(r"[\s　]+", " ", re.sub(r"<[^>]+>", "", nm.group(1))).strip() if nm else ""
        name = name.replace(" ", "")   # 1文字ずつ空白が入るので詰める(姓名の区切りは画面で選手一覧の名前を使う)
        rows.append({"rank": int(rk.group(1)), "regno": reg, "name": name,
                     "branch": text(sb.group(1)) if sb else "", "cls": text(ky.group(1)) if ky else "",
                     "money": int(mo.group(1).replace(",", ""))})
    return asof, rows[:60]


def main():
    html = get(URL)
    if not html:
        print("取得できませんでした")
        return
    asof, rows = parse(html)
    if len(rows) < 30:
        print("ランキングが読み取れませんでした", len(rows))
        return
    old = {}
    if os.path.exists(OUT):
        prev = json.load(open(OUT, encoding="utf-8"))
        if prev.get("asof") != asof:   # 集計日が変わった時だけ「前回」を入れ替える
            old = {r["regno"]: r["rank"] for r in prev.get("racers", [])}
            old_asof = prev.get("asof")
        else:
            old = prev.get("prev", {})
            old_asof = prev.get("prev_asof")
    else:
        old_asof = None
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = {"asof": asof, "fetched": datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "source": URL,
           "prev_asof": old_asof, "prev": old, "racers": rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(asof, len(rows), "人", rows[0]["name"], rows[0]["money"])


if __name__ == "__main__":
    main()
