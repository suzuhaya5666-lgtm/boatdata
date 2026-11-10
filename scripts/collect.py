#!/usr/bin/env python3
"""ボートレース公式の番組表(B)・競走成績(K)ファイルを取得して JSON にまとめる。

使い方:
  python scripts/collect.py                       # 直近3日分
  python scripts/collect.py --start 20250101 --end 20250131
  python scripts/collect.py --start 20250101 --end 20250131 --reparse
      (保存済みの元テキストから解析だけやり直す。取得はしない)
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request

# 対象のレース場(場コード: 名前)
VENUES = {"07": "蒲郡", "08": "常滑", "11": "びわこ", "12": "住之江", "19": "下関"}

BASE = "https://www1.mbrace.or.jp/od2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
JST = dt.timezone(dt.timedelta(hours=9))

BETS = ("単勝", "複勝", "2連単", "2連複", "拡連複", "3連単", "3連複")


# ---------------------------------------------------------------- 取得
def download(kind, d):
    """kind は 'K'(成績) か 'B'(番組表)。ファイルが無ければ None。"""
    url = f"{BASE}/{kind}/{d:%Y%m}/{kind.lower()}{d:%y%m%d}.lzh"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal data collection)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if b"-lh" not in body[:10]:  # LZH でない(未公開のときの案内ページなど)
        return None
    with tempfile.TemporaryDirectory() as tmp:
        arc = os.path.join(tmp, "a.lzh")
        out = os.path.join(tmp, "out")
        os.mkdir(out)
        with open(arc, "wb") as f:
            f.write(body)
        ok = False
        for cmd in (["7z", "x", "-y", f"-o{out}", arc], ["lha", f"-xw={out}", arc]):
            if shutil.which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True)
                if r.returncode == 0 and os.listdir(out):
                    ok = True
                    break
        if not ok:
            raise RuntimeError("解凍できませんでした: " + url)
        files = [p for p in glob.glob(out + "/**/*", recursive=True) if os.path.isfile(p)]
        with open(files[0], "rb") as f:
            raw = f.read()
    return raw.decode("cp932", errors="replace")


def sections(text, kind, all_venues=False):
    """場ごとの区切り(07KBGN〜07KEND など)を取り出す。通常は対象場だけ。"""
    out = {}
    cur = None
    for line in text.splitlines():
        m = re.match(rf"^(\d{{2}}){kind}BGN", line)
        if m:
            cur = m.group(1) if (all_venues or m.group(1) in VENUES) else None
            if cur:
                out[cur] = []
            continue
        if re.match(rf"^\d{{2}}{kind}END", line):
            cur = None
            continue
        if cur:
            out[cur].append(line)
    return out


def raw_path(kind, d):
    return os.path.join(DATA, "raw", kind, f"{d:%Y}", f"{kind.lower()}{d:%y%m%d}.txt")


def raw_all_path(d):
    """成績は選手別成績のため全24場分を圧縮して保存する。"""
    return os.path.join(DATA, "raw", "K", f"{d:%Y}", f"k{d:%y%m%d}.all.txt.gz")


def keep(secs, kind):
    kept = []
    for code, lines in secs.items():
        kept.append(f"{code}{kind}BGN")
        kept.extend(lines)
        kept.append(f"{code}{kind}END")
    return "\n".join(kept) + "\n"


def load_raw(kind, d, reparse):
    """保存済みの元テキストがあれば使い、無ければ取得して保存する。"""
    if kind == "K":
        pa = raw_all_path(d)
        # 成績ファイルは場が終わるごとに中身が増えるので、直近2日分はいつも取り直す
        recent = d >= dt.datetime.now(JST).date() - dt.timedelta(days=1)
        if os.path.exists(pa) and (reparse or not recent):
            with gzip.open(pa, "rt", encoding="utf-8") as f:
                return f.read(), False
    p = raw_path(kind, d)
    if reparse or (kind == "B" and os.path.exists(p)):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read(), False
        return None, False
    text = download(kind, d)
    time.sleep(1)
    if text is None:
        return None, False
    if kind == "K":
        os.makedirs(os.path.dirname(raw_all_path(d)), exist_ok=True)
        with gzip.open(raw_all_path(d), "wt", encoding="utf-8") as f:
            f.write(keep(sections(text, "K", all_venues=True), "K"))
        if os.path.exists(p):
            os.remove(p)  # 5場だけの古い保存は不要
        with gzip.open(raw_all_path(d), "rt", encoding="utf-8") as f:
            return f.read(), True
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = keep(sections(text, kind), kind)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return body, True


# ---------------------------------------------------------------- 解析
def num(s, cast=float):
    try:
        return cast(s)
    except (TypeError, ValueError):
        return None


def nk(s):
    return unicodedata.normalize("NFKC", s)


def clean_name(s):
    return re.sub(r"[\s\u3000]+", " ", s).strip()


def k_name(s):
    """成績ファイルの選手名(1字ずつ空白入り、姓と名の間は2字分)を「姓 名」にする。"""
    s = s.strip(" \u3000")
    parts = s.split("\u3000\u3000", 1)
    parts = [re.sub(r"[\s\u3000]+", "", x) for x in parts]
    return " ".join(x for x in parts if x)


def load_grades():
    """data/grades の月ファイルから {(場コード, 'YYYY-MM-DD'): {"grade", "type", "title"}} を作る。"""
    g = {}
    for p in sorted(glob.glob(os.path.join(DATA, "grades", "*.json"))):
        with open(p, encoding="utf-8") as f:
            for code, days in json.load(f).items():
                for date, info in days.items():
                    g[(code, date)] = info
    return g


GRADES = {}
NAMES = {}  # 登番 -> 正式な氏名(番組表は4文字で切れるため、成績ファイルの名前を使う)


def load_names():
    for p in sorted(glob.glob(os.path.join(DATA, "raw", "K", "*", "k*.all.txt.gz"))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^ *[0-9A-Z]{1,2} +[1-6] +(\d{4}) +(.*?) +\d+ +\d+ ", line)
                if m:
                    NAMES[m.group(1)] = k_name(m.group(2))


RACE_HEAD = re.compile(r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d+)m\s*(.*)$")


def parse_meta(lines):
    """番組表の見出しから開催名と何日目かを取る。"""
    meta = {}
    after = False
    for line in lines:
        n = nk(line).strip()
        if RACE_HEAD.match(nk(line)):
            break
        dm = re.search(r"第\s*(\d+)\s*日", n)
        if dm and "day" not in meta:
            meta["day"] = int(dm.group(1))
        if "番組表" in n:
            after = True
            continue
        if after and n and "title" not in meta:
            meta["title"] = clean_name(n)
    return meta


def parse_program(lines):
    races = {}
    cur = None
    for line in lines:
        n = nk(line)
        m = RACE_HEAD.match(n)
        if m:
            cur = {"no": int(m.group(1)), "name": clean_name(m.group(2)),
                   "distance": int(m.group(3)), "entries": {}}
            dm = re.search(r"締切予定\s*(\d{1,2}):(\d{2})", m.group(4))
            if dm:
                cur["deadline"] = f"{int(dm.group(1)):02d}:{dm.group(2)}"
            races[cur["no"]] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"^([1-6]) (\d{4})(\D+?)(\d{2})(\D{2})(\d{2})([AB][12])(.*)$", line)
        if not m:
            continue
        t = m.group(8).split()
        e = {
            "boat": int(m.group(1)), "regno": m.group(2), "name": clean_name(m.group(3)),
            "age": int(m.group(4)), "branch": clean_name(m.group(5)),
            "weight": int(m.group(6)), "class": m.group(7),
            "nat_win": num(t[0]) if len(t) > 0 else None,
            "nat_2": num(t[1]) if len(t) > 1 else None,
            "loc_win": num(t[2]) if len(t) > 2 else None,
            "loc_2": num(t[3]) if len(t) > 3 else None,
            "motor_no": num(t[4], int) if len(t) > 4 else None,
            "motor_2": num(t[5]) if len(t) > 5 else None,
            "boat_no": num(t[6], int) if len(t) > 6 else None,
            "boat_2": num(t[7]) if len(t) > 7 else None,
            "series": " ".join(t[8:]) if len(t) > 8 else "",
        }
        cur["entries"][e["boat"]] = e
    return races


def parse_results(lines):
    races = {}
    cur = None
    bet = None
    for line in lines:
        n = nk(line)
        m = RACE_HEAD.match(n)
        if m:
            cur = {"no": int(m.group(1)), "name": clean_name(m.group(2)),
                   "distance": int(m.group(3)), "entries": {}, "payouts": {}}
            wm = re.search(r"^(\S+)\s+風\s+(\S+)\s+(\d+)m\s+波\s+(\d+)cm", m.group(4).strip())
            if wm:
                cur["weather"] = {"sky": wm.group(1), "wind_dir": wm.group(2),
                                  "wind": int(wm.group(3)), "wave": int(wm.group(4))}
            races[cur["no"]] = cur
            bet = None
            continue
        if cur is None:
            continue
        if "レースタイム" in n:
            tok = n.split()
            if tok and tok[-1] != "レースタイム":
                cur["kimarite"] = tok[-1]
            continue
        # 選手行: 着 艇 登番 名前 モーター ボート 展示 進入 ST タイム
        m = re.match(r"^ *([0-9A-Z]{1,2}) +([1-6]) +(\d{4}) +(.*)$", line)
        if m:
            rest = re.match(r"^(.*?) +(\d+) +(\d+) +(.*)$", m.group(4))
            pos = m.group(1)
            e = {"boat": int(m.group(2)), "regno": m.group(3),
                 "place": int(pos) if pos.isdigit() else None, "status": None if pos.isdigit() else pos}
            if rest:
                t = rest.group(4).split()
                e.update({"name": k_name(rest.group(1)), "motor_no": int(rest.group(2)),
                          "boat_no": int(rest.group(3)),
                          "exh": num(t[0]) if len(t) > 0 else None,
                          "course": num(t[1], int) if len(t) > 1 else None,
                          "st": t[2] if len(t) > 2 else None,
                          "time": t[3] if len(t) > 3 and re.search(r"\d", t[3]) else None})
            cur["entries"][e["boat"]] = e
            continue
        # 払戻金
        pm = re.match(r"^\s*(" + "|".join(BETS) + r")\s+(.*)$", n)
        if pm:
            bet, body = pm.group(1), pm.group(2)
        elif bet and re.match(r"^\s+\d(-\d)+\s+[\d,]+", n):
            body = n
        else:
            if n.strip() == "":
                bet = None
            continue
        for combo, yen, pop in re.findall(r"(\d(?:-\d){0,2})\s+([\d,]+)(?:\s+人気\s+(\d+))?", body):
            cur["payouts"].setdefault(bet, []).append(
                {"combo": combo, "yen": int(yen.replace(",", "")), "pop": num(pop, int)})
    return races


# ---------------------------------------------------------------- まとめ
def day_path(d):
    return os.path.join(DATA, "races", f"{d:%Y}", f"{d:%Y%m%d}.json")


def merge(day, code, prog, res, meta=None):
    v = day["venues"].setdefault(code, {"name": VENUES[code], "races": {}})
    if meta:
        v.update(meta)
    for no in sorted(set(prog) | set(res)):
        p, r = prog.get(no, {}), res.get(no, {})
        race = v["races"].get(str(no), {"no": no})
        for k in ("name", "distance", "deadline"):
            if p.get(k) is not None:
                race[k] = p[k]
        for k in ("weather", "kimarite", "payouts"):
            if r.get(k):
                race[k] = r[k]
        if not race.get("name") and r.get("name"):
            race["name"], race["distance"] = r["name"], r["distance"]
        ents = {e["boat"]: e for e in race.get("entries", [])}
        for b in range(1, 7):
            e = ents.get(b, {"boat": b})
            if b in p.get("entries", {}):
                e.update(p["entries"][b])
            if b in r.get("entries", {}):
                rr = dict(r["entries"][b])
                for k in ("motor_no", "boat_no"):
                    if e.get(k) is not None:
                        rr.pop(k, None)
                e.update(rr)
            if e.get("regno") in NAMES:
                e["name"] = NAMES[e["regno"]]
            if len(e) > 1:
                ents[b] = e
        race["entries"] = [ents[b] for b in sorted(ents)]
        v["races"][str(no)] = race


def process(d, reparse):
    p = day_path(d)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            day = json.load(f)
    else:
        day = {"date": f"{d:%Y-%m-%d}", "venues": {}}
    kt, k_new = load_raw("K", d, reparse)
    bt, b_new = load_raw("B", d, reparse)
    if kt is None and bt is None:
        return None
    ks = sections(kt, "K") if kt else {}
    bs = sections(bt, "B") if bt else {}
    for code in VENUES:
        prog = parse_program(bs[code]) if code in bs else {}
        res = parse_results(ks[code]) if code in ks else {}
        if prog or res:
            meta = parse_meta(bs[code]) if code in bs else {}
            if any(re.search(r"(?<!準)優勝戦", r.get("name", "")) for r in prog.values()):
                meta["final"] = True
            g = GRADES.get((code, f"{d:%Y-%m-%d}"))
            if g:
                meta["grade"], meta["gtype"] = g["grade"], g["type"]
            merge(day, code, prog, res, meta)
    if not day["venues"]:
        return None
    day["has_results"] = any(
        any(r.get("payouts") for r in v["races"].values()) for v in day["venues"].values())
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, separators=(",", ":"))
    summary = ", ".join(
        f"{v['name']}{len(v['races'])}R" + ("" if any(r.get("payouts") for r in v["races"].values()) else "(結果なし)")
        for v in day["venues"].values())
    print(f"{d:%Y-%m-%d}: {summary}")
    return day


def write_index():
    days = []
    for p in sorted(glob.glob(os.path.join(DATA, "races", "*", "*.json"))):
        with open(p, encoding="utf-8") as f:
            day = json.load(f)
        days.append({"date": day["date"], "venues": sorted(day["venues"]),
                     "results": day.get("has_results", False)})
    idx = {"updated": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
           "venues": VENUES, "days": days}
    with open(os.path.join(DATA, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=100)
    a = ap.parse_args()
    t0 = time.time()
    today = dt.datetime.now(JST).date()
    start = dt.datetime.strptime(a.start, "%Y%m%d").date() if a.start.strip() else today - dt.timedelta(days=2)
    end = dt.datetime.strptime(a.end, "%Y%m%d").date() if a.end.strip() else today
    if end < start:
        start, end = end, start
    load_names()
    GRADES.update(load_grades())
    d = start
    while d <= end:
        if time.time() - t0 > a.max_minutes * 60:
            print(f"時間切れのため {d:%Y-%m-%d} の手前で終了。もう一度実行すると続きから取ります。")
            break
        try:
            process(d, a.reparse)
        except Exception as e:  # 1日失敗しても続ける
            print(f"{d:%Y-%m-%d}: 失敗 {e}", file=sys.stderr)
        d += dt.timedelta(days=1)
    write_index()


if __name__ == "__main__":
    main()
