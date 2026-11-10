#!/usr/bin/env python3
"""出目確率の重みを過去データから決める(月に1回作り直す)。

各レースについて、その日より前のデータだけで
  自艇: その選手がそのコースで走ったときの1〜3着率
  他艇から見た値: ほかの選手(コースc)のレースで、コースkの艇が1〜3着になった率
を出し(回数が少ないときは場平均に寄せる)、
「どの情報源をどれだけ混ぜると実際の着順に一番合うか」を着ごと・コースごとに求める。
結果は data/model/weights.json に保存し、画面側でその重みを使って確率を出す。
"""
import collections
import datetime as dt
import json
import os
import sys

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DATA, JST, VENUES  # noqa: E402
from stats import load_races, started  # noqa: E402

M = 10           # 回数が少ないときに場平均へ寄せる強さ(10走分)
WARMUP_DAYS = 60  # 最初の2ヶ月はデータを貯めるだけ
OUT = os.path.join(DATA, "model", "weights.json")


def build():
    races = sorted(((d, c, r, [e for e in r["entries"].values() if started(e)]) for d, c, r in load_races()),
                   key=lambda x: x[0])
    first = races[0][0]
    selfc = collections.defaultdict(lambda: np.zeros((7, 4)))
    othc = collections.defaultdict(lambda: np.zeros((7, 7, 4)))
    venue = collections.defaultdict(lambda: np.zeros((7, 4)))
    by_day = collections.defaultdict(list)
    for r in races:
        by_day[r[0]].append(r)
    X, N, P, Y = [], [], [], []
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
                X.append(x); N.append(n); P.append(prior); Y.append([pl[1], pl[2], pl[3]])
        for _, code, race, ents in by_day[day]:
            for e in ents:
                c, p = e["course"], e.get("place")
                s = selfc[e["regno"]][c]
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
    return np.array(X), np.array(N), np.array(P), np.array(Y), races[0][0], races[-1][0]


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


def main():
    force = "--force" in sys.argv
    today = dt.datetime.now(JST).date()
    if os.path.exists(OUT) and not force:
        with open(OUT, encoding="utf-8") as f:
            if json.load(f).get("month") == f"{today:%Y-%m}":
                print("今月の重みは作成済み")
                return
    X, N, P, Y, first, last = build()
    W, losses = fit(X, N, P, Y)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"month": f"{today:%Y-%m}", "m": M, "races": int(len(Y)),
                   "from": first.isoformat(), "to": last.isoformat(), "loss": losses,
                   "W": np.round(W, 4).tolist()}, f, ensure_ascii=False)
    print(f"重みを作成: {len(Y)}レースで学習 logloss {losses}")


if __name__ == "__main__":
    main()
