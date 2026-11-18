#!/usr/bin/env python3
"""保存した予想(data/pred)を結果と照らして、買い方ごとの収支を出す(収支タブの「集計用予想」用)。

  data/stats/pnl.json
    strategies: [[キー, 名前, 説明], ...]
    days: {"YYYY-MM-DD": {場コード: {"sum": {キー: [投資, 払戻, 的中レース数, 買ったレース数]},
                                     "races": {R: {"res": 3連単, "yen": 配当, "y2": 2連単の配当, "b": {キー: [投資, 払戻, [[買い目, 金額, 払戻], ...]]}}}}}}
  買い目は 1-2-3 なら3連単、1-2 なら2連単。
1点100円。結果は data/races の払戻、まだなければ data/live の速報(_res)を使う。
"""
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")

STRATS = [
    ("post_hon", "展示後 本命", "展示後の本命3点(3連単)"),
    ("pre_hon", "展示前 本命", "展示前の本命3点(3連単)"),
    ("post_ex1", "展示後 2連単", "展示後の2連単本命1点"),
    ("pre_ex1", "展示前 2連単", "展示前の2連単本命1点"),
    ("bal", "バランス", "展示後のバランス(最大5点)"),
    ("ex", "2連単期待値", "展示後の2連単期待値1点"),
    ("f2ex", "2連複期待値", "展示後の2連複期待値1点(本命寄りの出目確率で、確率上位3つの中の期待値が一番高い目、1.3以上の時だけ)"),
    ("f2x", "2連複1.5 切替", "2連複・本命寄りの確率上位3つで期待値1.5以上(最大2点、1点1000円)。2連単2点の合成オッズの方が高ければ2連単に切替"),
    ("f2x_m", "2連複1.5 切替(中間)", "2連複1.5切替と同じ買い方を、中間(サイト標準)の出目確率で。過去データで一番良かった形"),
    ("f3x", "3連複1.5 切替", "3連複・本命寄りの確率上位5つで期待値1.5以上(1点1000円、びわこ・住之江は除く)。3連複/3連単ボックス/1着率最低の頭切り4点を期待値で比べて切替"),
    ("ex_sw", "2連単期待値 流し切替", "2連単期待値1点。3連単4点流しの合成オッズの方が高ければ3連単に切替(1レース1000円を払戻がそろうように配分)"),
    ("ana", "穴", "展示後の穴(最大5点)"),
    ("ana12", "穴12点", "穴12点・期待値が最大の形"),
    ("ana12_f5", "穴12点(5倍↑)", "穴12点・1番人気5倍以上のレースだけ"),
    ("ex3_10sw", "1位固定 10%切替", "2連単1位固定・10%削り・2連単の方が高ければ2連単に切替"),
    ("ex3_20", "1位固定 20%", "2連単1位固定・20%削り(3連単だけ)"),
    ("bl2", "混ぜた確率 2連単1点", "予想0.18:オッズ0.90で混ぜた確率で、2連単上位3つの期待値が一番高い目(1.1以上)"),
    ("bl10", "混ぜた確率 1位固定10%切替", "混ぜた確率で1位固定10%切替、買う形の期待値1.1以上"),
    ("ex1_c", "2連単本命 混戦除き(自信)", "2連単本命1点。1位の目の確率25%以上で、オッズ3倍以上の時だけ"),
    ("ex1_g", "2連単本命 混戦除き(1着差)", "2連単本命1点。1着候補の1位と2位の差が60pt以上で、オッズ3倍以上の時だけ"),
    ("ex3_c", "1位固定10%切替 混戦除き", "1位固定10%切替。1位の目の確率25%以上で、2連単オッズ3倍以上の時だけ"),
    ("ex3_10sw_ev", "1位固定 10%切替 期待値1.3", "1位固定10%切替を、買う形の期待値が1.3以上の時だけ"),
    ("ex3_20_ev", "1位固定 20% 期待値1.3", "1位固定20%を、買う目の期待値(確率の合計×合成オッズ)が1.3以上の時だけ"),
]


def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def result(day, code, r, races_day):
    v = ((races_day or {}).get("venues") or {}).get(code) or {}
    x = (v.get("races") or {}).get(r) or {}
    if (x.get("payouts") or {}).get("3連単"):
        return x["payouts"]
    lv = load(os.path.join(DATA, "live", day, f"{code}_{int(r):02d}_res.json"))
    if lv and (lv.get("payouts") or {}).get("3連単"):
        return lv["payouts"]
    return None


def pay(po, typ, k):
    k = k.replace("=", "-")   # 連複は 1=2 で持っていて、払戻は 1-2
    return sum(e.get("yen") or 0 for e in po.get(typ, []) if e.get("combo") == k)


def keys(lst):
    return [z["k"] for z in (lst or []) if z and z.get("k")]


SW_YEN = 1000   # 流し切替の1レースの金額


def split_amt(odds, total=SW_YEN):
    """払戻がそろうように total 円を100円単位で配る(各100円以上)"""
    inv = [1 / o for o in odds]
    amt = [max(100, round(total * x / sum(inv) / 100) * 100) for x in inv]
    while sum(amt) > total and max(amt) > 100:
        amt[amt.index(max(amt))] -= 100
    return amt


def bets(pre, post, po, live=None):
    """買い方ごとの [投資, 払戻]。買わなかった買い方は入れない"""
    out = {}

    def put(name, items):
        """items: [(買い目, 金額)]。券種は買い目の長さで決まる"""
        rows = []
        for k, amt in items:
            typ = ("2連複" if len(k.split("=")) == 2 else "3連複") if "=" in k else ("3連単" if len(k.split("-")) == 3 else "2連単")
            rows.append([k, amt, pay(po, typ, k) * amt // 100])
        out[name] = [sum(r[1] for r in rows), sum(r[2] for r in rows), rows]

    def tri(name, ks):
        if ks:
            put(name, [(k, 100) for k in ks])

    if pre:
        tri("pre_hon", keys(pre.get("hon")))
        ex1 = keys(pre.get("ex1"))
        if ex1:
            put("pre_ex1", [(ex1[0], 100)])
    if post:
        tri("post_hon", keys(post.get("hon")))
        ex1 = keys(post.get("ex1"))
        if ex1:
            put("post_ex1", [(ex1[0], 100)])
        tri("bal", keys(post.get("bal")))
        f2 = keys(post.get("f2ex"))
        if f2:
            put("f2ex", [(f2[0], 100)])
        for nm in ("bl2", "bl10"):
            if (post.get(nm) or {}).get("buy"):
                put(nm, [(b[0], b[1]) for b in post[nm]["buy"]])
        for nm in ("f2x", "f2x_m", "f3x"):
            its = [(b[0], b[1]) for z in post.get(nm) or [] for b in z.get("buy") or []]
            if its:
                put(nm, its)
        ex = keys(post.get("ex"))
        if ex:
            put("ex", [(k, 100) for k in ex])
            # 流し切替: 決めた時点の 2連単オッズ と 3連単4点(k-x)の合成オッズ を比べる
            k = ex[0]
            e1 = next((z for z in post.get("ex1") or [] if z and z.get("k") == k), None)
            o3 = (live or {}).get("3t") or {}
            ks3 = [f"{k}-{x}" for x in range(1, 7) if str(x) not in k.split("-")]
            if e1 and e1.get("syn") and e1.get("o") and e1["syn"] > e1["o"] and all(o3.get(z) for z in ks3):
                put("ex_sw", list(zip(ks3, split_amt([o3[z] for z in ks3]))))
            else:
                put("ex_sw", [(k, SW_YEN)])
        tri("ana", keys(post.get("ana")))
        a = post.get("ana12")
        if a and a.get("best") in a:
            tri("ana12", a[a["best"]]["k"])
            if (post.get("fav") or 0) >= 5 and "ana12" in out:
                out["ana12_f5"] = list(out["ana12"])
        e = post.get("ex3")
        if e:
            c10 = keys(e.get("cut10"))
            if c10 and e.get("o2") and e.get("cut10_syn"):
                n = len(c10)
                if e["o2"] > e["cut10_syn"]:
                    put("ex3_10sw", [(e["k"], 100 * n)])
                else:
                    tri("ex3_10sw", c10)
            tri("ex3_20", keys(e.get("cut20")))
            # 混戦を除く: 1位の目の確率25%以上 or 1着候補の差60pt以上、どちらもオッズ3倍以上
            w = sorted((post.get("win") or {}).values(), reverse=True)
            gap = w[0] - w[1] if len(w) >= 2 else 0
            if (e.get("o2") or 0) >= 3:
                if (e.get("p") or 0) >= 0.25:
                    put("ex1_c", [(e["k"], 100)])
                    if "ex3_10sw" in out:
                        out["ex3_c"] = out["ex3_10sw"]
                if gap >= 0.6:
                    put("ex1_g", [(e["k"], 100)])
            # 期待値つき: 2連単なら 確率×オッズ、3連単なら 確率の合計×合成オッズ
            psum = lambda n: sum(z.get("p") or 0 for z in e.get(n) or [])
            if c10 and e.get("o2") and e.get("cut10_syn"):
                sw = e["o2"] > e["cut10_syn"]
                ev = (e.get("p") or 0) * e["o2"] if sw else psum("cut10") * e["cut10_syn"]
                if ev >= 1.3:
                    if sw:
                        put("ex3_10sw_ev", [(e["k"], 100 * len(c10))])
                    else:
                        tri("ex3_10sw_ev", c10)
            if e.get("cut20_syn") and psum("cut20") * e["cut20_syn"] >= 1.3:
                tri("ex3_20_ev", keys(e.get("cut20")))
    return out


def main():
    days = {}
    for d in sorted(os.listdir(os.path.join(DATA, "pred")) if os.path.isdir(os.path.join(DATA, "pred")) else []):
        races_day = load(os.path.join(DATA, "races", d[:4], f"{d}.json"))
        date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        for p in sorted(glob.glob(os.path.join(DATA, "pred", d, "*.json"))):
            code = os.path.basename(p)[:2]
            pr = load(p)
            if not pr:
                continue
            summ, rs = {}, {}
            for r in sorted(pr.get("races", {}), key=int):
                po = result(d, code, r, races_day)
                if not po:
                    continue
                x = pr["races"][r]
                b = bets(x.get("pre"), x.get("post"), po, load(os.path.join(DATA, "live", d, f"{code}_{int(r):02d}.json")))
                if not b:
                    continue
                t3 = po["3連単"][0]
                t2 = (po.get("2連単") or [{}])[0]
                rs[r] = {"res": t3.get("combo"), "yen": t3.get("yen"), "y2": t2.get("yen"), "b": b}
                for k, (inv, ret, _) in b.items():
                    s = summ.setdefault(k, [0, 0, 0, 0])
                    s[0] += inv
                    s[1] += ret
                    s[2] += 1 if ret > 0 else 0
                    s[3] += 1
            if rs:
                days.setdefault(date, {})[code] = {"sum": summ, "races": rs}
    idx = load(os.path.join(DATA, "index.json")) or {}
    out = {"strategies": [list(s) for s in STRATS], "venues": idx.get("venues", {}), "days": days}
    os.makedirs(os.path.join(DATA, "stats"), exist_ok=True)
    with open(os.path.join(DATA, "stats", "pnl.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("pnl:", len(days), "日")


if __name__ == "__main__":
    main()
