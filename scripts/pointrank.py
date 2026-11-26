#!/usr/bin/env python3
"""公式サイトの「得点率一覧」(予選の得点・減点・備考)を取って保存する。

  python scripts/pointrank.py --start 20231101 --end 20261126 [--semi-only]

data/races の番組を見て、予選の日と準優勝戦の日(=予選の最終成績)の場を取る。--semi-only は準優勝戦の日だけ。
data/pointrank/YYYY/YYYYMMDD.json に {場コード: [{rank, regno, name, cls, rate, marks, pts, minus, note}]} を書く。
marks は節間成績の着順(全角→半角、太字=ドリーム・特選などは [ ] で囲む)。rank・rate は「-」なら null。
自前の得点率の計算(index.html の seriesPoints)の答え合わせと、減点(不良航法など。成績ファイルにない)を知るため。
"""
import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
URL = "https://www.boatrace.jp/owpc/pc/race/pointrank?hd={d}&jcd={c}"


def fetch(url):
    for i in range(3):
        r = subprocess.run(["curl", "-sSL", "-4", "--max-time", "40", "-A", "Mozilla/5.0 (personal data collection)", url], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", errors="replace")
        time.sleep(3 * (i + 1))
    return None


def cell(s):
    s = re.sub(r'<span class="is-fBold">(.*?)</span>', r"[\1]", s, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return unicodedata.normalize("NFKC", s).strip()


def parse(h):
    rows = []
    for tr in re.findall(r"<tr class=\"is-p10-0\">(.*?)</tr>", h, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 9:
            continue
        m = re.search(r"toban=(\d{4})", tds[1])
        if not m:
            continue
        num = lambda x: float(x) if re.fullmatch(r"-?\d+(\.\d+)?", x) else None
        rows.append({
            "rank": int(cell(tds[0])) if cell(tds[0]).isdigit() else None,
            "regno": m.group(1), "name": re.sub(r"\s+", " ", cell(tds[2])), "cls": cell(tds[3]),
            "rate": num(cell(tds[4])), "marks": re.sub(r"\s+$", "", cell(tds[5])),
            "pts": num(cell(tds[6])), "minus": num(cell(tds[7])), "note": cell(tds[-1]),
        })
    return rows


def targets(start, end, semi_only):
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
        k = os.path.basename(f)[:8]
        if not start <= k <= end:
            continue
        with open(f, encoding="utf-8") as fp:
            d = json.load(fp)
        for code, v in d.get("venues", {}).items():
            names = [r.get("name") or "" for r in v.get("races", {}).values()]
            semi = any("準優勝戦" in n for n in names)
            prelim = not any("優勝戦" in n for n in names)
            if semi or (prelim and not semi_only):
                out.append((k, code))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--semi-only", action="store_true")
    ap.add_argument("--redo", action="store_true", help="保存済みも取り直す")
    ap.add_argument("--max-minutes", type=float, default=100)
    a = ap.parse_args()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
    start = a.start or (dt.datetime.strptime(today, "%Y%m%d") - dt.timedelta(days=2)).strftime("%Y%m%d")
    end = a.end or today
    t0, n = time.time(), 0
    cur = {}
    for k, code in targets(start, end, a.semi_only):
        if time.time() - t0 > a.max_minutes * 60:
            print("時間切れ", k)
            break
        p = os.path.join(DATA, "pointrank", k[:4], f"{k}.json")
        if p not in cur:
            for q in list(cur):
                save(q, cur.pop(q))
            cur[p] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        if code in cur[p] and not a.redo and k < today:
            continue
        h = fetch(URL.format(d=k, c=code))
        time.sleep(1)
        rows = parse(h) if h else []
        n += 1
        if not rows and n <= 3:
            print("  解析できず", len(h or ""), (h or "")[:200].replace("\n", " "), (h or "").count("is-p10-0"), repr((h or "")[(h or "").find("is-p10-0") - 20:][:300]))
        if rows:
            cur[p][code] = rows
        print(k, code, len(rows), flush=True)
    for q in list(cur):
        save(q, cur.pop(q))
    print("取得", n)


def save(p, d):
    if not d:
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
