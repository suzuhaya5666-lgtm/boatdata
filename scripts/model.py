#!/usr/bin/env python3
"""出目確率の重みを過去データから決める(月に1回作り直す)。

各レースについて、その日より前のデータだけで
  自艇: その選手がそのコースで走ったときの1〜3着率
  他艇から見た値: ほかの選手(コースc)のレースで、コースkの艇が1〜3着になった率
を出し(回数が少ないときは場平均に寄せる)、
「どの情報源をどれだけ混ぜると実際の着順に一番合うか」を着ごと・コースごとに求める。
さらに、その確率に 展示タイム・全国勝率・当地勝率・モーター2連率・級別・平均ST を
着ごとの係数で足し合わせる(条件付きロジット)。係数もここで決める。
結果は data/model/weights.json に保存し、画面側でその重みと係数を使って確率を出す。
"""
import collections
import datetime as dt
import json
import os
import re
import sys

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, VENUES  # noqa: E402
from stats import load_races, started  # noqa: E402

M = 10           # 回数が少ないときに場平均へ寄せる強さ(10走分)
WARMUP_DAYS = 60  # 最初の2ヶ月はデータを貯めるだけ
OUT = os.path.join(DATA, "model", "weights.json")
FEATURES = ["exh", "nat_win", "loc_win", "motor_2", "class", "avg_st"]
FEATURE_NAMES = ["展示タイム", "全国勝率", "当地勝率", "モーター2連率", "級別", "平均ST"]
CLS = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
_days = {}


def program_entries(day, code, no):
    key = day.isoformat()
    if key not in _days:
        p = os.path.join(DATA, "races", f"{day:%Y}", f"{day:%Y%m%d}.json")
        try:
            with open(p, encoding="utf-8") as f:
                _days[key] = json.load(f)
        except FileNotFoundError:
            _days[key] = {"venues": {}}
    return _days[key]["venues"].get(code, {}).get("races", {}).get(str(no), {}).get("entries", [])


def build():
    races = sorted(((d, c, r, [e for e in r["entries"].values() if started(e)]) for d, c, r in load_races()),
                   key=lambda x: x[0])
    first = races[0][0]
    selfc = collections.defaultdict(lambda: np.zeros((7, 4)))
    othc = collections.defaultdict(lambda: np.zeros((7, 7, 4)))
    venue = collections.defaultdict(lambda: np.zeros((7, 4)))
    stsum = collections.defaultdict(lambda: np.zeros(2))
    by_day = collections.defaultdict(list)
    for r in races:
        by_day[r[0]].append(r)
    X, N, P, Y, F = [], [], [], [], []
    for day in sorted(by_day):
        if day >= first + dt.timedelta(days=WARMUP_DAYS):
            for _, code, race, ents in by_day[day]:
                courses = {e["course"]: e for e in ents}
                if code not in VENUES or sorted(courses) != [1, 2, 3, 4, 5, 6]:
                    continue
                pl = {e.get("place"): e["course"] for e in ents}
                if not all(p in pl for p in (1, 2, 3)):
                    continue
                x = np.zeros((7, 7, 3))
                n = np.zeros((7, 7))
                for k in range(1, 7):
                    s = selfc[courses[k]["regno"]][k]
                    x[k, 0], n[k, 0] = s[1:4], s[0]
                    for c in range(1, 7):
                        if c != k:
                            o = othc[courses[c]["regno"]][c][k]
                            x[k, c], n[k, c] = o[1:4], o[0]
                v = venue[code]
                prior = np.zeros((7, 3))
                prior[1:] = (v[1:, 1:4] + 1) / (v[1:, :1] + 6)
                de = {e["boat"]: e for e in program_entries(day, code, race["no"])}
                f = np.full((7, len(FEATURES)), np.nan)
                for k in range(1, 7):
                    e = courses[k]
                    b = de.get(e["boat"], {})
                    st = stsum[e["regno"]]
                    f[k] = [e.get("exh") or np.nan, b.get("nat_win") or np.nan, b.get("loc_win") or np.nan,
                            b["motor_2"] if b.get("motor_2") is not None else np.nan,
                            CLS.get(b.get("class"), np.nan), st[0] / st[1] if st[1] else np.nan]
                X.append(x); N.append(n); P.append(prior); Y.append([pl[1], pl[2], pl[3]]); F.append(f)
        for _, code, race, ents in by_day[day]:
            for e in ents:
                c, p = e["course"], e.get("place")
                s = selfc[e["regno"]][c]
                if re.match(r"^\d?\.\d+$", e.get("st") or ""):
                    stsum[e["regno"]] += [float(e["st"]), 1]
                s[0] += 1
                if p in (1, 2, 3):
                    s[p] += 1
                if code in VENUES:
                    venue[code][c, 0] += 1
                    if p in (1, 2, 3):
                        venue[code][c, p] += 1
                for o in ents:
                    if o is not e:
                        oo = othc[e["regno"]][c][o["course"]]
                        oo[0] += 1
                        if o.get("place") in (1, 2, 3):
                            oo[o["place"]] += 1
    return np.array(X), np.array(N), np.array(P), np.array(Y), np.array(F), races[0][0], races[-1][0]


def fit(X, N, P, Y):
    E = (X + M * P[:, :, None, :]) / (N[..., None] + M)
    W_all = np.zeros((3, 7, 7))
    losses = []
    idx = np.arange(len(Y))
    for j in range(3):
        excl = np.zeros((len(Y), 7))
        for jj in range(j):
            excl[idx, Y[:, jj]] = 1

        def unpack(theta):
            W = np.zeros((7, 7))
            W[1:, :] = np.exp(theta.reshape(6, 7))
            for k in range(1, 7):
                W[k, k] = 0
            return W / W.sum(1, keepdims=True).clip(1e-12)

        def loss(theta):
            q = np.einsum("rks,ks->rk", E[:, :, :, j], unpack(theta))
            q[:, 0] = 0
            q = np.clip(q, 1e-9, None) * (1 - excl)
            q[:, 0] = 0
            q = q / q.sum(1, keepdims=True)
            return -np.log(q[idx, Y[:, j]]).mean()

        r = minimize(loss, np.zeros(42), method="L-BFGS-B")
        W_all[j] = unpack(r.x)
        losses.append(round(float(r.fun), 4))
    return W_all, losses


def standardize(F):  # noqa: E302
    """レース内の平均との差を、全体のばらつきで割る。欠けている値はレース平均(=0)。"""
    Z = F[:, 1:, :].copy()
    mean = np.nanmean(Z, axis=1, keepdims=True)
    Z = np.where(np.isnan(Z), mean, Z)
    Z = Z - Z.mean(axis=1, keepdims=True)
    sd = Z.reshape(-1, Z.shape[2]).std(0)
    sd = np.where(sd == 0, 1, sd)
    Z = np.nan_to_num(Z / sd)
    return np.concatenate([np.zeros((len(F), 1, Z.shape[2])), Z], axis=1), sd


def base_lq(X, N, P, W):
    E = (X + M * P[:, :, None, :]) / (N[..., None] + M)
    q = np.einsum("rksj,jks->rkj", E, W)
    q[:, 0, :] = 0
    q = np.clip(q, 1e-9, None)
    q[:, 0, :] = 0
    return np.log(q / q.sum(1, keepdims=True) + 1e-12)


def fit_logit(LQ, Z, Y, feats, use_cbias, rows=None):
    """選んだ材料(feats)とコース補正の有無で、着ごとの係数を決める。"""
    if rows is not None:
        LQ, Z, Y = LQ[rows], Z[rows], Y[rows]
    idx = np.arange(len(Y))
    nf = len(feats)
    Zs = Z[:, :, feats]
    coefs, cbias = [], []
    for j in range(3):
        excl = np.zeros((len(Y), 7))
        excl[:, 0] = 1
        for jj in range(j):
            excl[idx, Y[:, jj]] = 1

        def loss(t):
            s = t[0] * LQ[:, :, j] + (Zs @ t[1:1 + nf] if nf else 0)
            if use_cbias:  # コースの補正(全場共通): 内外の差が弱めに出るのを直す
                s = s + np.concatenate([[0], t[1 + nf:]])[None, :]
            s = np.where(excl == 1, -1e9, s)
            s = s - s.max(1, keepdims=True)
            reg = 0.001 * (t[1 + nf:] ** 2).sum() if use_cbias else 0
            return -(s[idx, Y[:, j]] - np.log(np.exp(s).sum(1))).mean() + reg
        x0 = np.zeros(1 + nf + (6 if use_cbias else 0))
        x0[0] = 1
        t = minimize(loss, x0, method="L-BFGS-B").x
        full = [0.0] * len(FEATURES)
        for i, f in enumerate(feats):
            full[f] = round(float(t[1 + i]), 4)
        coefs.append([round(float(t[0]), 4)] + full)
        cbias.append([0] + (np.round(t[1 + nf:], 4).tolist() if use_cbias else [0] * 6))
    return coefs, cbias


PERMS = np.array([(a, b, c) for a in range(1, 7) for b in range(1, 7) for c in range(1, 7)
                  if a != b and b != c and a != c])


def evaluate(LQ, Z, Y, coefs, cbias, rows):
    """検証用: 指定したレースで、3連単上位10点・2連単上位3点・単勝の的中率。"""
    LQ, Z, Y = LQ[rows], Z[rows], Y[rows]
    n = len(Y)
    ex = []
    for j in range(3):
        c = np.array(coefs[j])
        s = c[0] * LQ[:, :, j] + Z @ c[1:] + np.array(cbias[j])[None, :]
        s[:, 0] = -1e9
        ex.append(np.exp(s - s.max(1, keepdims=True)))
    s1, s2, s3 = ex
    a, b, cc = PERMS[:, 0], PERMS[:, 1], PERMS[:, 2]
    p = (s1[:, a] / s1.sum(1, keepdims=True)) * (s2[:, b] / (s2.sum(1, keepdims=True) - s2[:, a])) * \
        (s3[:, cc] / (s3.sum(1, keepdims=True) - s3[:, a] - s3[:, b]))
    p = p / p.sum(1, keepdims=True)
    ai = np.array([np.where((PERMS == y).all(1))[0][0] for y in Y])
    pa = p[np.arange(n), ai]
    tri10 = float(np.mean((p > pa[:, None]).sum(1) < 10))
    keys = {}
    for i, (x, y_, _) in enumerate(PERMS):
        keys.setdefault((x, y_), []).append(i)
    kl = list(keys)
    em = np.stack([p[:, keys[k]].sum(1) for k in kl], 1)
    act = np.array([kl.index((y[0], y[1])) for y in Y])
    ex3 = float(np.mean((em > em[np.arange(n), act][:, None]).sum(1) < 3))
    win = np.zeros((n, 7))
    for i, (x, _, _) in enumerate(PERMS):
        win[:, x] += p[:, i]
    tan = float(np.mean(win.argmax(1) == Y[:, 0]))
    return {"tri10": round(tri10 * 100, 1), "ex3": round(ex3 * 100, 1), "tan": round(tan * 100, 1)}


def main():
    force = "--force" in sys.argv
    today = dt.datetime.now(JST).date()
    if os.path.exists(OUT) and not force:
        with open(OUT, encoding="utf-8") as f:
            if json.load(f).get("month") == f"{today:%Y-%m}":
                print("今月の重みは作成済み")
                return
    X, N, P, Y, F, first, last = build()
    W, losses = fit(X, N, P, Y)
    Z, sd = standardize(F)
    LQ = base_lq(X, N, P, W)
    # 検証用: 古い6割で決めて、新しい4割で確かめる
    cut = int(len(Y) * 0.6)
    tr, te = np.arange(cut), np.arange(cut, len(Y))
    W_tr, _ = fit(X[tr], N[tr], P[tr], Y[tr])
    LQ_tr = base_lq(X, N, P, W_tr)
    combos = {}
    nfeat = len(FEATURES)
    for mask in range(1 << (nfeat + 1)):
        feats = [i for i in range(nfeat) if mask >> i & 1]
        use_cb = bool(mask >> nfeat & 1)
        c_tr, b_tr = fit_logit(LQ_tr, Z, Y, feats, use_cb, rows=tr)
        ev = evaluate(LQ_tr, Z, Y, c_tr, b_tr, te)
        c_all, b_all = fit_logit(LQ, Z, Y, feats, use_cb)
        combos[str(mask)] = {"coef": c_all, "cbias": b_all, "eval": ev}
        print(f"組み合わせ {mask:3d}: {ev}")
    full = combos[str((1 << (nfeat + 1)) - 1)]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"month": f"{today:%Y-%m}", "m": M, "races": int(len(Y)),
                   "from": first.isoformat(), "to": last.isoformat(), "loss": losses,
                   "W": np.round(W, 4).tolist(),
                   "features": FEATURES, "feature_names": FEATURE_NAMES, "sd": np.round(sd, 4).tolist(),
                   "coef": full["coef"], "cbias": full["cbias"], "eval_races": int(len(te)),
                   "combos": combos}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"重みを作成: {len(Y)}レース、組み合わせ {len(combos)}通り")


if __name__ == "__main__":
    main()
