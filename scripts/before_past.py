"""過去の直前情報(チルト・展示タイム・スタート展示・部品交換など)をまとめて取る。

保存先: data/before_past/YYYY/YYYYMMDD.json.gz  ({"07_01": 直前情報, ...})
取得済みのレースは飛ばすので、何度動かしても続きから取る。

  python scripts/before_past.py --start 20231110 --end 20261109 --max-minutes 330 --workers 3

ほかの場(チルトを跳ねた回数を数えるため): --codes 01,04,... を付けると、
全24場の成績ファイル(data/raw/K/*.all.txt.gz)からレースを拾い、data/before_other/ に保存する。
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from before import URL, parse  # noqa: E402
from collect import DATA, VENUES  # noqa: E402

OUT = os.path.join(DATA, "before_past")
OTHER = ["01", "04", "05", "06", "09", "10", "13", "14", "15", "16", "17", "18", "20", "21", "22", "23", "24"]  # 戸田・江戸川と5場以外


def day_file(d):
    return os.path.join(OUT, f"{d:%Y}", f"{d:%Y%m%d}.json.gz")


def load_day(d):
    p = day_file(d)
    if not os.path.exists(p):
        return {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_day(d, data):
    p = day_file(d)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def push(msg):
    def run(*a):
        return subprocess.run(list(a), capture_output=True, text=True)
    run("git", "add", OUT)
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    run("git", "commit", "-q", "-m", msg)
    for _ in range(4):
        run("git", "pull", "--rebase", "-q")
        if run("git", "push", "-q").returncode == 0:
            return
        time.sleep(5)
    print("保存に失敗(次の回でまとめて保存します)", flush=True)


def targets_k(start, end, codes):
    """成績ファイルに載っているレース(全24場)から、codes の場の分"""
    import re
    todo = []
    for p in sorted(glob.glob(os.path.join(DATA, "raw", "K", "*", "k*.all.txt.gz"))):
        d = dt.datetime.strptime("20" + os.path.basename(p)[1:7], "%Y%m%d").date()
        if not (start <= d <= end):
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            text = f.read()
        have = load_day(d)
        for code, body in re.findall(r"^(\d{2})KBGN\n(.*?)^\1KEND", text, re.S | re.M):
            if code not in codes:
                continue
            for no in sorted({int(x) for x in re.findall(r"^\s+(\d{1,2})R\s+\S.*?H\d{4}m", body, re.M)}):
                key = f"{code}_{no:02d}"
                if key not in have:
                    todo.append((d, code, no, key))
    return todo


def targets(start, end):
    todo = []
    for p in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
        d = dt.datetime.strptime(os.path.basename(p)[:8], "%Y%m%d").date()
        if not (start <= d <= end):
            continue
        with open(p, encoding="utf-8") as f:
            day = json.load(f)
        have = load_day(d)
        for code, v in day["venues"].items():
            if code not in VENUES:
                continue
            for no in sorted(v["races"], key=int):
                key = f"{code}_{int(no):02d}"
                if key not in have:
                    todo.append((d, code, int(no), key))
    return todo


def fetch(item):
    d, code, no, key = item
    url = URL.format(rno=no, jcd=code, hd=f"{d:%Y%m%d}")
    r = subprocess.run(["curl", "-sS", "-4", "--max-time", "30",
                        "-A", "Mozilla/5.0 (personal data collection)", url], capture_output=True)
    if r.returncode != 0:
        return item, None
    info = parse(r.stdout.decode("utf-8", errors="replace"))
    if not info["entries"]:
        return item, {"none": True}  # 中止など。取り直さないよう印だけ残す
    return item, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--max-minutes", type=float, default=330)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--push-every", type=int, default=300)
    ap.add_argument("--codes", default="", help="ほかの場(カンマ区切り、other なら戸田・江戸川と5場以外の17場)")
    a = ap.parse_args()
    global OUT
    codes = OTHER if a.codes == "other" else [c for c in a.codes.split(",") if c]
    if codes:
        OUT = os.path.join(DATA, "before_other")
    t0 = time.time()
    start = dt.datetime.strptime(a.start, "%Y%m%d").date()
    end = dt.datetime.strptime(a.end, "%Y%m%d").date()
    todo = targets_k(start, end, codes) if codes else targets(start, end)
    print(f"取得するレース {len(todo)}件", flush=True)
    pending = {}  # 日付 -> {key: info}
    lock = threading.Lock()
    done = fail = 0

    def flush(msg):
        with lock:
            items = list(pending.items())
            pending.clear()
        for d, add in items:
            data = load_day(d)
            data.update(add)
            save_day(d, data)
        push(msg)

    def work(item):
        if time.time() - t0 > a.max_minutes * 60:
            return None
        return fetch(item)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(work, todo):
            if res is None:
                continue
            (d, code, no, key), info = res
            if info is None:
                fail += 1
                continue
            with lock:
                pending.setdefault(d, {})[key] = info
            done += 1
            if done % a.push_every == 0:
                print(f"  {done}/{len(todo)}件 ({d})", flush=True)
                flush(f"過去の直前情報 {done}件")
    flush(f"過去の直前情報 {done}件")
    left = len(todo) - done
    print(f"直前情報 {done}件取得、失敗 {fail}件、残り {left}件" + ("(もう一度実行すると続きから取ります)" if left else ""))


if __name__ == "__main__":
    main()
