#!/usr/bin/env python3
"""勝負駆けの補正(index.html の KAKE_ADJ)の成績を、補正なしと比べる。

  python scripts/kake_check.py [--since 2026-11-28] [--out report.md]

1. scripts/kake_rows.js で、予選最終日の各レースの勝負駆けの条件をサイトと同じ関数で出す
2. scripts/backtest.py と同じ作り方で、各レースの3連単の確率(補正なし)を出す(枠なりのレースだけ)
3. 補正ごとに、補正がかかったレースで 対数尤度・3連単上位N点の的中率と回収率(1点100円)・その艇の連対率 を比べる
--since 以降のレースだけの集計も出し、明らかに悪い(下の BAD の条件)ときは最後の行が「判定: 要相談」になる。
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def model_probs():
    import model as M
    src = open(os.path.join(ROOT, "scripts", "backtest.py"), encoding="utf-8").read()
    body = src.split("def probs():")[1].split("\n# ----")[0]
    body = body.replace("te[i:i + 4000]) for i in range(0, len(te), 4000)", "prog[i:i + 4000]) for i in range(0, len(prog), 4000)")
    body = body.replace("meta = [M.ROWMETA[i][:3] for i in te]", "meta = [M.ROWMETA[i][:3] for i in prog]")
    ns = {"M": M, "np": np, "warnings": warnings}
    exec("def probs():" + body, ns)
    out, meta = ns["probs"]()
    return out["post"], meta, np.array(M.PERMS)


def payouts():
    pay = {}
    for f in glob.glob(os.path.join(ROOT, "data", "races", "*", "*.json")):
        k = os.path.basename(f)[:8]
        d = f"{k[:4]}-{k[4:6]}-{k[6:]}"
        for c, v in json.load(open(f, encoding="utf-8"))["venues"].items():
            for r in v["races"].values():
                pay[(c, d, int(r["no"]))] = {x["combo"]: x["yen"] for x in (r.get("payouts") or {}).get("3連単", [])}
    return pay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-28", help="この日以降だけの集計も出す(補正を入れた日の翌日)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    tmp = "/tmp/kake_rows.json"
    subprocess.run(["node", os.path.join(ROOT, "scripts", "kake_rows.js"), tmp], check=True)
    K = json.load(open(tmp))
    post, meta, PA = model_probs()
    idx = {(c, d.strftime("%Y-%m-%d"), int(n)): i for i, (c, d, n) in enumerate(meta)}
    PIX = {tuple(int(v) for v in p): j for j, p in enumerate(PA)}
    pay = payouts()
    lines = [f"# 勝負駆けの補正の成績（{dt.date.today()}・{a.since}以降を「最近」として別に集計）", ""]
    bad = []
    for rule in K["rules"]:
        c, cats, ra, rb, gs = rule["c"], rule["cats"], rule["a"], rule["b"], rule.get("grades")
        st = collections.defaultdict(collections.Counter)
        for r in K["rows"]:
            conds = r["conds"]
            if conds.get(str(c)) not in cats or (gs and conds.get("g") not in gs):
                continue
            i = idx.get((r["code"], r["date"], r["no"]))
            if i is None or any(e.get("course") != e["boat"] for e in r["ents"]):
                continue
            fin = {e.get("place"): e["boat"] for e in r["ents"] if e.get("place")}
            if not all(k in fin for k in (1, 2, 3)):
                continue
            p = post[i].astype(float)
            q = p * np.where(PA[:, 0] == c, ra, 1) * np.where(PA[:, 1] == c, rb, 1)
            q /= q.sum()
            y = PIX[(fin[1], fin[2], fin[3])]
            yen = pay.get((r["code"], r["date"], r["no"]), {}).get(f"{fin[1]}-{fin[2]}-{fin[3]}", 0)
            ren = 1 if c in (fin[1], fin[2]) else 0
            for per in ("全期間", "最近") if r["date"] >= a.since else ("全期間",):
                for lab, x in (("なし", p), ("あり", q)):
                    s = st[(per, lab)]
                    s["n"] += 1
                    s["ll"] += float(np.log(x[y]))
                    s["ren"] += ren
                    s["pren"] += float(x[(PA[:, 0] == c) | (PA[:, 1] == c)].sum())
                    o = np.argsort(-x)
                    for N in (3, 5, 10):
                        h = y in o[:N]
                        s[f"h{N}"] += h
                        s[f"r{N}"] += yen if h else 0
        lines.append(f"## {rule['label']}（{c}コース・1着×{ra}・2着×{rb}）")
        for per in ("全期間", "最近"):
            n0, n1 = st[(per, "なし")], st[(per, "あり")]
            n = n0["n"]
            if not n:
                lines.append(f"- {per}: 該当レースなし")
                continue
            g = n1["ll"] - n0["ll"]
            roi = lambda s, N: s[f"r{N}"] / (100 * N * s["n"]) * 100
            lines.append(f"- {per}: {n}レース・その艇の連対 実際{n0['ren'] / n * 100:.1f}% / 予想 なし{n0['pren'] / n * 100:.1f}%→あり{n1['pren'] / n * 100:.1f}%・対数尤度の差 {g:+.2f}")
            lines.append("  - " + " / ".join(f"3連単上位{N}点 的中{n0[f'h{N}'] / n * 100:.1f}→{n1[f'h{N}'] / n * 100:.1f}% 回収{roi(n0, N):.0f}→{roi(n1, N):.0f}%" for N in (3, 5, 10)))
            # BAD: 最近20レース以上で、補正が確率の当たり具合をはっきり悪くしている(対数尤度で -2 より悪い)か、
            #      補正の向きと実際の連対が逆(下げたのに予想なしより多く連対・上げたのに少ない)で差が5pt以上
            if per == "最近" and n >= 20:
                down = ra * rb < 1
                gap = (n0["ren"] - n0["pren"]) / n * 100
                if g < -2 or (down and gap > 5) or (not down and gap < -5):
                    bad.append(f"{rule['label']}（{n}レース・対数尤度 {g:+.2f}・連対 実際−予想 {gap:+.1f}pt）")
        lines.append("")
    lines.append("判定: " + ("要相談 — " + "、".join(bad) if bad else "問題なし"))
    text = "\n".join(lines)
    print(text)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text + "\n")


if __name__ == "__main__":
    main()
