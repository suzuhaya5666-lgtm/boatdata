#!/usr/bin/env python3
"""買い方ごとの過去データでの検証(収支タブの「買い方の比較」用)。

model.py と同じ材料で、出走表があるレースの古い6割で重みを決め、新しい4割(学習に使っていないレース)の
3連単120通りの確率を出す。そのうち最終オッズがあるレースで、pred.js / pnl.py と同じ選び方で買ったときの
的中率・回収率を場ごとに数える。1点100円。

  展示前 = 展示・オリジナル展示を使わない予想 / 展示後 = いつもの予想
  オッズは締切後の最終オッズ(実際は締切前のオッズで選ぶので、期待値で選ぶ買い方は少し良く見える)
  枠なりのレースだけ(model.py の学習と同じ)。画面の「予想の寄せ方」の補正表は使っていない

  python scripts/backtest.py   →  data/model/backtest.json
"""
import collections
import datetime as dt
import gzip
import itertools
import json
import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from collect import DATA  # noqa: E402

OUT = os.path.join(DATA, "model", "backtest.json")
STRATS = ["post_hon", "pre_hon", "post_ex1", "pre_ex1", "bal", "ex", "ana", "ana12", "ana12_f5", "ex3_10sw", "ex3_20"]
B = ["1", "2", "3", "4", "5", "6"]


def load(p):
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------- 予想(確率)
def probs():
    X, N, P, Y, F, H, V, S, KD, KR, KY, first, last = M.build()
    W, _ = M.fit(X, N, P, Y)
    prog = np.where(H)[0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        F, _ = M.org_transform(F, V)
        Zp, _ = M.standardize(F[prog])
    Z = np.zeros((len(Y), 7, len(M.FEATURES)))
    Z[prog] = Zp
    LQ = M.base_lq(X, N, P, W)
    SI, MI = M.FEATURES.index("setsu"), M.FEATURES.index("motor_form")
    FI = [M.FEATURES.index("form"), M.FEATURES.index("form_young")]
    feats0 = [i for i in range(len(M.FEATURES)) if i not in (SI, MI, *FI, *M.GROUPS[M.SLIT_GROUP])]
    c0, b0 = M.fit_logit(LQ, Z, Y, V, S, KD, feats0, True, rows=prog)
    E = {}
    for st in range(0, len(prog), 4000):
        rr = prog[st:st + 4000]
        mg = M.predict_marg(LQ, Z, Y, V, S, KD, c0, b0, rr)
        ex_place = mg[:, :, 0] + 2 * mg[:, :, 1] + 3 * mg[:, :, 2] + 5 * (1 - mg.sum(2))
        for r_, i in enumerate(rr):
            code, day, no = M.ROWMETA[i][:3]
            for k in range(1, 7):
                E[(code, day, no, k)] = float(ex_place[r_, k])
    F[:, :, SI] = M.setsu_by_expect(E)
    F[:, :, MI] = M.motor_by_expect(E)
    F[:, :, FI] = M.form_by_expect(E)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        Zp, _ = M.standardize(F[prog])
    Z = np.zeros((len(Y), 7, len(M.FEATURES)))
    Z[prog] = Zp
    cut = int(len(prog) * 0.6)
    tr, te = prog[:cut], prog[cut:]
    W_tr, _ = M.fit(X[:prog[cut]], N[:prog[cut]], P[:prog[cut]], Y[:prog[cut]])
    LQ_tr = M.base_lq(X, N, P, W_tr)
    ng = len(M.GROUPS)
    post_mask = (1 << (ng + 1)) - 1
    for g in M.DEFAULT_OFF:
        post_mask &= ~(1 << g)
    pre_mask = post_mask
    for name in ("展示(タイム・ST・部品・チルト)", "オリジナル展示"):
        pre_mask &= ~(1 << M.GROUP_NAMES.index(name))
    out = {}
    for tag, mask in (("post", post_mask), ("pre", pre_mask)):
        feats = [i for g in range(ng) if mask >> g & 1 for i in M.GROUPS[g]]
        c, b = M.fit_logit(LQ_tr, Z, Y, V, S, KD, feats, bool(mask >> ng & 1), rows=tr)
        out[tag] = np.concatenate([M.predict_perm(LQ_tr, Z, V, S, KD, c, b, te[i:i + 4000]) for i in range(0, len(te), 4000)])
    meta = [M.ROWMETA[i][:3] for i in te]
    return out, meta


# ---------------------------------------------------------------- 買い方(pred.js と同じ選び方)
def comb(a, n):
    return [list(x) for x in itertools.combinations(a, n)]


def perm(h, s2, s3):
    return [f"{a}-{b}-{c}" for a in h for b in s2 for c in s3 if len({a, b, c}) == 3]


def shapes():
    sh = {"A": [], "B": [], "C": [], "D": [], "E": []}
    for h in B:
        o = [x for x in B if x != h]
        for s in comb(o, 4):
            sh["A"].append(perm([h], s, s))
        for s in comb(o, 3):
            sh["B"].append(perm([h], s, o))
    for hh in comb(B, 2):
        o = [x for x in B if x not in hh]
        for c in o:
            for ex in o:
                if ex != c:
                    sh["C"].append(perm(hh, hh + [c], [x for x in B if x != ex]))
        for s in comb(o, 2):
            g = sorted(hh + s)
            sh["D"].append(perm(hh, g, g))
    t3 = comb(B, 3)
    for i in range(len(t3)):
        for j in range(i + 1, len(t3)):
            sh["E"].append(perm(t3[i], t3[i], t3[i]) + perm(t3[j], t3[j], t3[j]))
    return sh


SHAPES = shapes()


def ana12(P, o3):
    best, bv = None, -1
    for cands in SHAPES.values():
        for ks in cands:
            v = sum(P.get(k, 0) * (o3.get(k) or 0) for k in ks)
            if v > bv:
                bv, best = v, ks
    return best


def choose(tri, o3, o2, fav):
    """tri: [(買い目, 確率)] 確率の高い順。返り値 {買い方: [(買い目, 金額)]}"""
    P = dict(tri)
    ex = collections.defaultdict(float)
    for k, p in tri:
        ex[k[:3]] += p
    exs = sorted(ex.items(), key=lambda x: -x[1])
    out = {"hon": [(k, 100) for k, _ in tri[:3]], "ex1": [(exs[0][0], 100)]}
    bal = [(k, p) for k, p in tri[:10] if o3.get(k) and p * o3[k] >= 1]
    bal.sort(key=lambda x: -x[1] * o3[x[0]])
    if bal:
        out["bal"] = [(k, 100) for k, _ in bal[:5]]
    e = [(k, p) for k, p in exs[:3] if o2.get(k) and p * o2[k] >= 1.2]
    e.sort(key=lambda x: -x[1] * o2[x[0]])
    if e:
        out["ex"] = [(e[0][0], 100)]
    an = [(k, p) for k, p in tri if (o3.get(k) or 0) >= 100 and p * o3[k] >= 1.5]
    an.sort(key=lambda x: -x[1] * o3[x[0]])
    if an:
        out["ana"] = [(k, 100) for k, _ in an[:5]]
    a12 = ana12(P, o3)
    out["ana12"] = [(k, 100) for k in a12]
    if fav >= 5:
        out["ana12_f5"] = list(out["ana12"])
    # 2連単1位固定
    top, pt = exs[0]
    rest = sorted([(k, p) for k, p in tri if k.startswith(top + "-")], key=lambda x: -x[1])

    def cut_to(x):
        c, cut = list(rest), 0.0
        while len(c) > 1 and cut + c[-1][1] / pt <= x + 1e-12:
            cut += c[-1][1] / pt
            c.pop()
        return c
    c10, c20 = cut_to(0.10), cut_to(0.20)
    out["ex3_20"] = [(k, 100) for k, _ in c20]
    if all((o3.get(k) or 0) > 0 for k, _ in c10) and o2.get(top):
        syn = round(100 / sum(1 / o3[k] for k, _ in c10)) / 100
        out["ex3_10sw"] = [(top, 100 * len(c10))] if o2[top] > syn else [(k, 100) for k, _ in c10]
    return out


def pay(po, k):
    typ = "3連単" if len(k) == 5 else "2連単"
    return sum(e.get("yen") or 0 for e in po.get(typ, []) if e.get("combo") == k)


def main():
    pr, meta = probs()
    keys = [f"{a}-{b}-{c}" for a, b, c in M.PERMS]  # 枠なりなのでコース=艇番
    acc = collections.defaultdict(lambda: [0, 0, 0, 0])  # (場, 買い方) -> [投資, 払戻, 的中, レース]
    days_used = set()
    day_cache = {}
    for i, (code, day, no) in enumerate(meta):
        ds = day.strftime("%Y%m%d")
        od = load(os.path.join(DATA, "odds", ds, f"{code}_{no:02d}.json"))
        o3, o2 = (od or {}).get("3t") or {}, (od or {}).get("2t") or {}
        if len([v for v in o3.values() if v]) < 60:
            continue
        if ds not in day_cache:
            day_cache = {ds: load(os.path.join(DATA, "races", ds[:4], f"{ds}.json"))}
        race = (((day_cache[ds] or {}).get("venues") or {}).get(code) or {}).get("races", {}).get(str(no)) or {}
        po = race.get("payouts") or {}
        if not po.get("3連単"):
            continue
        o3 = {k: (v[0] if isinstance(v, list) else v) for k, v in o3.items() if v}
        o2 = {k: (v[0] if isinstance(v, list) else v) for k, v in o2.items() if v}
        fav = min(o3.values())
        days_used.add(day)
        for tag in ("post", "pre"):
            p = pr[tag][i]
            order = np.argsort(-p)
            tri = [(keys[j], float(p[j])) for j in order]
            ch = choose(tri, o3, o2, fav)
            names = {"hon": f"{tag}_hon", "ex1": f"{tag}_ex1"}
            for k, items in ch.items():
                name = names.get(k, k)
                if tag == "pre" and name not in ("pre_hon", "pre_ex1"):
                    continue
                inv = sum(a for _, a in items)
                ret = sum(pay(po, kk) * a // 100 for kk, a in items)
                for c in (code, "all"):
                    a = acc[(c, name)]
                    a[0] += inv
                    a[1] += ret
                    a[2] += 1 if ret > 0 else 0
                    a[3] += 1
    res = {}
    for (c, name), (inv, ret, hit, n) in acc.items():
        res.setdefault(c, {})[name] = [inv, ret, hit, n]
    nr = collections.Counter(code for code, day, no in meta)
    out = {"made": dt.date.today().isoformat(),
           "from": min(days_used).isoformat() if days_used else None,
           "to": max(days_used).isoformat() if days_used else None,
           "races": {c: v.get("post_hon", [0, 0, 0, 0])[3] for c, v in res.items()},
           "note": "学習に使っていない新しい4割のレースのうち、最終オッズがあるレース。オッズは最終オッズ。枠なりのレースだけ。1点100円",
           "venues": res}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    for c in sorted(res):
        print(c, {k: f"{v[2] / v[3] * 100:.1f}% {v[1] / v[0] * 100:.1f}% ({v[3]})" for k, v in sorted(res[c].items())})


if __name__ == "__main__":
    main()
