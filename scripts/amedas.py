#!/usr/bin/env python3
"""場の近くのアメダス(気象庁)の10分ごとの風を取る。場の風向きと比べるための元データ。

保存先: data/amedas/<場>/<年>/<YYYYMMDD>.json  [[時分, 平均風速, 風向, 最大瞬間風速, 風向], ...](8時〜22時)
開催日だけ取る。取得済みの日は飛ばす(今日・昨日は取り直す)。

  python scripts/amedas.py --start 20231110 --end 20261114
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
# 場: (地点名, 種類 a=アメダス s=気象台, 府県番号, 地点番号)
STATIONS = {"07": ("蒲郡", "a", 51, "1344"), "08": ("セントレア", "a", 51, "1555"), "11": ("大津", "a", 60, "0586"),
            "12": ("大阪", "s", 62, "47772"), "19": ("下関", "s", 81, "47762")}
URL = "https://www.data.jma.go.jp/obd/stats/etrn/view/10min_{k}1.php?prec_no={p}&block_no={b}&year={y}&month={m}&day={d}&view="
DIRS = ("北", "南", "東", "西", "静穏")
DBG = [0]


def parse(html):
    out = []
    for tr in re.findall(r'<tr class="mtx"[^>]*>(.*?)</tr>', html, re.S):
        tds = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not tds or not re.match(r"\d\d:\d\d", tds[0]):
            continue
        hm = tds[0]
        if not ("08:00" <= hm <= "22:00"):
            continue
        di = next((i for i, x in enumerate(tds) if x.startswith(DIRS)), None)
        if di is None or di < 2:
            continue
        def f(x):
            try:
                return float(re.sub(r"[)\]\s]", "", x))
            except ValueError:
                return None
        gi = di + 2 if di + 2 < len(tds) and tds[di + 2].startswith(DIRS) else None
        out.append([hm, f(tds[di - 1]), tds[di], f(tds[di + 1]) if gi else None, tds[gi] if gi else None])
    return out


def path(code, d):
    return os.path.join(DATA, "amedas", code, f"{d:%Y}", f"{d:%Y%m%d}.json")


def fetch(item):
    code, d = item
    _, k, p, b = STATIONS[code]
    r = subprocess.run(["curl", "-sSL", "-4", "--max-time", "40", "-A", "Mozilla/5.0",
                        URL.format(k=k, p=p, b=b, y=d.year, m=d.month, d=d.day)], capture_output=True)
    if r.returncode != 0:
        if DBG[0] < 3:
            DBG[0] += 1
            print("取得失敗", code, d, r.returncode, r.stderr[:200], flush=True)
        return item, None
    html = r.stdout.decode("utf-8", errors="replace")
    rows = parse(html)
    if not rows and DBG[0] < 3:
        DBG[0] += 1
        print("読み取れず", code, d, len(html), re.sub(r"\s+", " ", html[:300]), flush=True)
    return item, rows


def push(msg):
    def run(*a):
        return subprocess.run(list(a), capture_output=True, text=True)
    run("git", "add", os.path.join(DATA, "amedas"))
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-q", "-m", msg)
    for _ in range(4):
        run("git", "pull", "--rebase", "-q")
        if run("git", "push", "-q").returncode == 0:
            return
        time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--max-minutes", type=float, default=320)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    start = dt.datetime.strptime(a.start, "%Y%m%d").date()
    end = dt.datetime.strptime(a.end, "%Y%m%d").date()
    recent = (dt.datetime.utcnow() + dt.timedelta(hours=9)).date() - dt.timedelta(days=1)
    todo = []
    for p in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
        d = dt.datetime.strptime(os.path.basename(p)[:8], "%Y%m%d").date()
        if not (start <= d <= end):
            continue
        with open(p, encoding="utf-8") as f:
            venues = json.load(f).get("venues", {})
        for code in STATIONS:
            if venues.get(code, {}).get("races") and (d >= recent or not os.path.exists(path(code, d))):
                todo.append((code, d))
    print(f"取得する日 {len(todo)}件", flush=True)
    done = fail = 0

    def work(item):
        if time.time() - t0 > a.max_minutes * 60:
            return item, False
        time.sleep(0.3)
        return fetch(item)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for (code, d), rows in ex.map(work, todo):
            if rows is False:
                continue
            if not rows:
                fail += 1
                continue
            os.makedirs(os.path.dirname(path(code, d)), exist_ok=True)
            with open(path(code, d), "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
            done += 1
            if done % 300 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
                push(f"アメダスの風 {done}日")
    push(f"アメダスの風 {done}日")
    left = len(todo) - done - fail
    print(f"アメダス {done}日取得、失敗 {fail}日、残り {left}日")


if __name__ == "__main__":
    main()
