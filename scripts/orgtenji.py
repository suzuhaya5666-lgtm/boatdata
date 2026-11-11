"""各場のオリジナル展示(一周・まわり足・直線など)を取る。

今のところ蒲郡のみ(公式サイトの「オリジナル展示」ページ。過去の日付も取れる)。
  直前情報と一緒に取る:  before.py から fetch(code, day, rno) を呼ぶ
  過去分のまとめ取り:    python scripts/orgtenji.py --start 20231110 --end 20261109 --workers 3
    保存先: data/org_past/YYYY/YYYYMMDD.json.gz  ({"07_01": {"1": {"lap":.., "turn":.., "straight":..}, ...}})
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from collect import DATA  # noqa: E402

GAMAGORI = "https://www.gamagori-kyotei.com/asp/gamagori/kyogi/kyogihtml/time/time{hd}07{rno:02d}.htm"
VENUES = {"07": "蒲郡"}   # オリジナル展示が取れる場
LABELS = {"lap": "一周", "turn": "まわり足", "straight": "直線"}
OUT = os.path.join(DATA, "org_past")


def get(url):
    r = subprocess.run(["curl", "-sS", "-4", "--max-time", "30", "-A", "Mozilla/5.0 (personal data collection)", url],
                       capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


def num(s):
    s = re.sub(r"<[^>]+>", "", s).strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_gamagori(html):
    out = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        m = re.search(r'<td class="waku w(\d)">', tr)
        if not m:
            continue
        sc = re.findall(r'<td class="score[^"]*">(.*?)</td>', tr, re.S)
        if len(sc) < 4:
            continue
        reg = re.search(r'<div class="number">(\d{4})', tr)
        lap, turn, straight = num(sc[1]), num(sc[2]), num(sc[3])
        if lap is None and turn is None and straight is None:
            continue
        out[m.group(1)] = {"regno": reg.group(1) if reg else None, "exh": num(sc[0]),
                           "lap": lap, "turn": turn, "straight": straight}
    return out


def fetch(code, day, rno):
    """オリジナル展示を返す。取れない場・まだ出ていないときは None。"""
    if code != "07":
        return None
    html = get(GAMAGORI.format(hd=f"{day:%Y%m%d}", rno=rno))
    if not html:
        return None
    o = parse_gamagori(html)
    return o or None


# ---------------------------------------------------------------- 過去分のまとめ取り
def day_file(d):
    return os.path.join(OUT, f"{d:%Y}", f"{d:%Y%m%d}.json.gz")


def load_day(d):
    p = day_file(d)
    if not os.path.exists(p):
        return {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_day(d, data):
    os.makedirs(os.path.dirname(day_file(d)), exist_ok=True)
    with gzip.open(day_file(d), "wt", encoding="utf-8") as f:
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
    todo = []
    for p in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
        d = dt.datetime.strptime(os.path.basename(p)[:8], "%Y%m%d").date()
        if not (start <= d <= end):
            continue
        with open(p, encoding="utf-8") as f:
            day = json.load(f)
        have = load_day(d)
        for code in VENUES:
            for no in day["venues"].get(code, {}).get("races", {}):
                key = f"{code}_{int(no):02d}"
                if key not in have:
                    todo.append((d, code, int(no), key))
    print(f"取得するレース {len(todo)}件", flush=True)

    def work(item):
        if time.time() - t0 > a.max_minutes * 60:
            return item, False
        d, code, no, key = item
        return item, fetch(code, d, no) or {}   # 取れなかったレースも空で記録(取り直さない)

    pending, done = {}, 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for (d, code, no, key), o in ex.map(work, todo):
            if o is False:
                continue
            pending.setdefault(d, {})[key] = o
            done += 1
            if done % 300 == 0:
                for dd, add in pending.items():
                    data = load_day(dd); data.update(add); save_day(dd, data)
                pending = {}
                push(f"過去のオリジナル展示 {done}件")
                print(f"  {done}/{len(todo)}件", flush=True)
    for dd, add in pending.items():
        data = load_day(dd); data.update(add); save_day(dd, data)
    push(f"過去のオリジナル展示 {done}件")
    print(f"オリジナル展示 {done}件取得、残り {len(todo) - done}件")


if __name__ == "__main__":
    main()
