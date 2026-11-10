#!/usr/bin/env python3
"""公式サイトの月間スケジュールから、場・日ごとのグレードを取る。

data/grades/YYYYMM.json = {場コード: {日付: {"grade": "SG|G1|G2|G3|一般", "type": 種別, "title": 開催名}}}
過去の月は一度取ったら取り直さない。今月と来月は毎回取り直す。
"""
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST  # noqa: E402

URL = "https://www.boatrace.jp/owpc/pc/race/monthlyschedule?ym={ym}"
# 月間スケジュールの色分け → (グレード, 種別)
# オールレディースは G3、ヴィーナス・ルーキー・マスターズは一般戦として扱う
CLASSES = {
    "SG": ("SG", ""), "G1": ("G1", ""), "G2": ("G2", ""), "G3": ("G3", ""), "Ippan": ("一般", ""),
    "Lady": ("G3", "レディース"), "Venus": ("一般", "ヴィーナス"), "Rookie": ("一般", "ルーキー"),
    "Takumi": ("一般", "マスターズ"),
}


def parse(html, year, month):
    """表の列は見出しの日付(月の1日より前の月曜から始まる)に対応する。"""
    head = re.search(r"<thead>(.*?)</thead>", html, re.S)
    if not head:
        return {}
    days = [int(x) for x in re.findall(r"<th[^>]*>\s*(\d{1,2})\s*<br>", head.group(1))]
    if not days:
        return {}
    first = dt.date(year, month, 1)
    start = first if days[0] == 1 else (first - dt.timedelta(days=1)).replace(day=days[0])
    out = {}
    for tb in re.findall(r"<tbody>(.*?)</tbody>", html, re.S):
        m = re.search(r"jcd=(\d{2})", tb)
        if not m:
            continue
        code = m.group(1)
        col = 0
        for attrs, body in re.findall(r"<td([^>]*)>(.*?)</td>", tb, re.S):
            span = re.search(r'colspan\s*=\s*"?(\d+)', attrs)
            n = int(span.group(1)) if span else 1
            gm = re.search(r"is-gradeColor(\w+)", attrs)
            if gm:
                grade, kind = CLASSES.get(gm.group(1), ("一般", gm.group(1)))
                title = unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", "", body)).strip()
                for i in range(col, col + n):
                    date = start + dt.timedelta(days=i)
                    if date.month == month:  # 前後の月の分はその月のページで取る
                        out.setdefault(code, {})[date.isoformat()] = {"grade": grade, "type": kind, "title": title}
            col += n
    return out


def months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    today = dt.datetime.now(JST).date()
    # --since YYYYMM で、それより前の月も取る(過去分のまとめ取り用)
    if "--since" in sys.argv:
        ym = sys.argv[sys.argv.index("--since") + 1]
        start = dt.date(int(ym[:4]), int(ym[4:6]), 1)
    else:
        start = dt.date(today.year - 1, today.month, 1)
    nxt = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    os.makedirs(os.path.join(DATA, "grades"), exist_ok=True)
    for y, m in months(start, nxt):
        path = os.path.join(DATA, "grades", f"{y}{m:02d}.json")
        recent = (y, m) >= (today.year, today.month)
        if os.path.exists(path) and not recent:
            continue
        req = urllib.request.Request(URL.format(ym=f"{y}{m:02d}"),
                                     headers={"User-Agent": "Mozilla/5.0 (personal data collection)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"{y}/{m}: 取得失敗 {e}")
            continue
        time.sleep(1)
        data = parse(html, y, m)
        if not data:
            print(f"{y}/{m}: 読み取れず")
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"{y}/{m}: {sum(len(v) for v in data.values())}場日")


if __name__ == "__main__":
    main()
