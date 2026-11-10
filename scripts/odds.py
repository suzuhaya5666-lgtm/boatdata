#!/usr/bin/env python3
"""公式サイトから締切時オッズ(2連単・2連複・3連単・3連複)を取る。

data/odds/<日付>/<場>_<R>.json = {"2t": {"1-2": 3.2, ...}, "2f": {"1=2": ...}, "3t": {...}, "3f": {...}}
使い方:
  python scripts/odds.py                       # 前日と今日の終わったレース
  python scripts/odds.py --start 20260701 --end 20261110 --max-minutes 330
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST  # noqa: E402

URL = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd}&hd={hd}"


def cells(tr):
    return [(a, re.sub(r"<[^>]+>", "", t).strip()) for a, t in re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S)]


def num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def tables(html):
    """ページ内の表(tbody)を順に返す。"""
    return re.findall(r'<tbody class="is-p3-0">(.*?)</tbody>', html, re.S)


def parse_two(tbody, ordered):
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S):
        cs = cells(tr)
        for col in range(6):
            if 2 * col + 1 >= len(cs):
                break
            second, odds = cs[2 * col][1], num(cs[2 * col + 1][1])
            if odds is None or not second.isdigit():
                continue
            a, b = col + 1, int(second)
            out[f"{a}-{b}" if ordered else "=".join(map(str, sorted((a, b))))] = odds
    return out


def parse_three(tbody, ordered):
    out = {}
    second = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S):
        cs = cells(tr)
        i = 0
        for col in range(6):
            if i < len(cs) and ("rowspan" in cs[i][0] or "is-fs14" in cs[i][0]):  # 2着の艇の欄
                second[col] = cs[i][1]
                i += 1
            if i + 1 >= len(cs):
                break
            third, odds = cs[i][1], num(cs[i + 1][1])
            i += 2
            if odds is None or not third.isdigit() or not str(second.get(col, "")).isdigit():
                continue
            a, b, c = col + 1, int(second[col]), int(third)
            out[f"{a}-{b}-{c}" if ordered else "=".join(map(str, sorted((a, b, c))))] = odds
    return out


def rng(s):
    """'1.3-1.8' → [1.3, 1.8]"""
    m = re.match(r"^([\d.]+)-([\d.]+)$", s.strip())
    return [float(m.group(1)), float(m.group(2))] if m else None


def parse_wide(tbody):
    """拡連複(2連複と同じ並び、値は幅)"""
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S):
        cs = cells(tr)
        for col in range(6):
            if 2 * col + 1 >= len(cs):
                break
            second, val = cs[2 * col][1], rng(cs[2 * col + 1][1])
            if val is None or not second.isdigit():
                continue
            out["=".join(map(str, sorted((col + 1, int(second)))))] = val
    return out


def parse_tf(html):
    """単勝・複勝: 表が2つ(単勝・複勝)、各行 [艇, 名前, オッズ]"""
    win, place = {}, {}
    for tb in re.findall(r"<tbody[^>]*>(.*?)</tbody>", html, re.S):
        cs = cells(tb)
        if len(cs) < 3 or not cs[0][1].isdigit():
            continue
        b, v = cs[0][1], cs[2][1]
        if "-" in v:
            r = rng(v)
            if r and r[1] > 0:
                place[b] = r
        else:
            x = num(v)
            if x:
                win[b] = x
    return win, place


def fetch(page, rno, jcd, hd):
    """curl で取る(Python の urllib だと GitHub の実行環境で毎回8秒ほど待たされるため)"""
    t = time.time()
    r = subprocess.run(["curl", "-sS", "-4", "--max-time", "20", "-A", "Mozilla/5.0 (personal data collection)",
                        URL.format(page=page, rno=rno, jcd=jcd, hd=hd)], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip())
    body = r.stdout.decode("utf-8", errors="replace")
    if VERBOSE:
        print(f"  {page} {jcd} {rno}R {time.time() - t:.1f}秒 {len(body)}バイト", flush=True)
    return body


VERBOSE = "--verbose" in sys.argv


def get_race_full(rno, jcd, hd, pool=None):
    """5ページ(2連単・2連複/3連単/3連複/単勝・複勝/拡連複)をまとめて取る。データがなければ None"""
    pages = ["odds2tf", "odds3t", "odds3f", "oddstf", "oddsk"]
    if pool:
        htmls = dict(zip(pages, pool.map(lambda pg: fetch(pg, rno, jcd, hd), pages)))
    else:
        htmls = {pg: fetch(pg, rno, jcd, hd) for pg in pages}
    if "データがありません" in htmls["odds2tf"] or not tables(htmls["odds2tf"]):
        return None
    t2 = tables(htmls["odds2tf"])
    win, place = parse_tf(htmls["oddstf"])
    tk = tables(htmls["oddsk"])
    return {"2t": parse_two(t2[0], True), "2f": parse_two(t2[1], False) if len(t2) > 1 else {},
            "3t": parse_three(tables(htmls["odds3t"])[0], True) if tables(htmls["odds3t"]) else {},
            "3f": parse_three(tables(htmls["odds3f"])[0], False) if tables(htmls["odds3f"]) else {},
            "1t": win, "1f": place, "k": parse_wide(tk[0]) if tk else {}}


def get_race(rno, jcd, hd):
    h2 = fetch("odds2tf", rno, jcd, hd)
    if "データがありません" in h2:
        return None
    t2 = tables(h2)
    h3t = fetch("odds3t", rno, jcd, hd)
    h3f = fetch("odds3f", rno, jcd, hd)
    out = {"2t": parse_two(t2[0], True) if t2 else {}, "2f": parse_two(t2[1], False) if len(t2) > 1 else {},
           "3t": parse_three(tables(h3t)[0], True) if tables(h3t) else {},
           "3f": parse_three(tables(h3f)[0], False) if tables(h3f) else {}}
    return out


def push(msg):
    def run(*a):
        return subprocess.run(list(a), capture_output=True, text=True)
    run("git", "add", os.path.join(DATA, "odds"))
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-m", msg)
    for _ in range(3):
        run("git", "pull", "--rebase", "-q")
        if run("git", "push", "-q").returncode == 0:
            return
        time.sleep(5)


def targets(start, end):
    """結果が出ていて、まだオッズを取っていないレース"""
    d = start
    while d <= end:
        p = os.path.join(DATA, "races", f"{d:%Y}", f"{d:%Y%m%d}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                day = json.load(f)
            for code, v in day["venues"].items():
                for no, race in sorted(v["races"].items(), key=lambda x: int(x[0])):
                    out = os.path.join(DATA, "odds", f"{d:%Y%m%d}", f"{code}_{int(no):02d}.json")
                    if race.get("payouts") and not os.path.exists(out):
                        yield d, code, int(no), out
        d += dt.timedelta(days=1)


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--max-minutes", type=float, default=60)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--workers", type=int, default=3, help="同時に取るレース数(公式サイトの応答が1ページ約8秒のため)")
    ap.add_argument("--push-every", type=int, default=0, help="この件数ごとに GitHub に保存(0なら保存しない)")
    a = ap.parse_args()
    today = dt.datetime.now(JST).date()
    start = dt.datetime.strptime(a.start, "%Y%m%d").date() if a.start.strip() else today - dt.timedelta(days=1)
    end = dt.datetime.strptime(a.end, "%Y%m%d").date() if a.end.strip() else today
    t0 = time.time()
    todo = list(targets(start, end))
    print(f"対象 {len(todo)}レース", flush=True)
    got = 0

    def work(item):
        d, code, no, out = item
        if time.time() - t0 > a.max_minutes * 60:
            return item, "時間切れ"
        try:
            o = get_race(no, code, f"{d:%Y%m%d}")
        except Exception as e:
            return item, f"失敗 {e}"
        if o is None:
            return item, "データなし"
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(o, f, separators=(",", ":"))
        return item, "ok"

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(work, it) for it in todo]):
            (d, code, no, _), status = fut.result()
            if status == "ok":
                got += 1
                if a.push_every and got % a.push_every == 0:
                    print(f"{got}件 ({d} まで)", flush=True)
                    push(f"オッズ {got}件")
            elif status != "時間切れ":
                print(f"{d} {code} {no}R: {status}", flush=True)
    left = len(todo) - got
    print(f"オッズ {got}件取得、残り {left}件" + ("(もう一度実行すると続きから取ります)" if left else ""))
    if a.push_every:
        push(f"オッズ {got}件")


if __name__ == "__main__":
    main()
