"""オリジナル展示のレース内偏差値の基準と、偏差値ごとの着率表を作る(今は蒲郡のみ)。

偏差値の出し方(画面と同じ):
  1. 計測(展示・一周・まわり足・直線)から、そのコースの平均を引く(内側ほど速く測られる分を除く)
  2. レースの6艇の平均との差を、全体のばらつき(sd)で割り、速いほど高く 50+10×… にする
  3. 合計偏差値 = 4つの偏差値の平均を、レース内でもう一度偏差値にしたもの
保存先: data/model/hensa.json
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from collect import DATA  # noqa: E402
from model import org_of  # noqa: E402
from stats import load_races, started  # noqa: E402

BINS = [40, 45, 50, 55, 60, 65]
LABELS = ["40未満", "40〜45", "45〜50", "50〜55", "55〜60", "60〜65", "65以上"]
KEYS = ["exh", "lap", "turn", "straight"]
NAMES = ["展示タイム", "一周", "まわり足", "直線"]


def main():
    O, POS = [], []
    for d, code, r in load_races():
        if code != "07":
            continue
        ents = [e for e in r["entries"].values() if started(e)]
        if len(ents) != 6 or any(e["course"] != e["boat"] for e in ents):
            continue
        org = org_of(d, code, r["no"])
        if len(org) < 6:
            continue
        x = np.full((6, 4), np.nan)
        pos = np.zeros(6, int)
        for e in ents:
            o = org.get(str(e["boat"])) or {}
            x[e["course"] - 1] = [o.get(k) or np.nan for k in KEYS]
            p = e.get("place")
            pos[e["course"] - 1] = p if p in (1, 2, 3) else 0
        if np.isnan(x).any():
            continue
        O.append(x)
        POS.append(pos)
    O, POS = np.array(O), np.array(POS)
    cm = O.mean(0)                                   # (コース, 計測)
    D = (O - cm[None]) - (O - cm[None]).mean(1, keepdims=True)
    sd = D.std(axis=(0, 1))                          # 計測ごと
    T = 50 - 10 * D / sd
    TS = T.mean(2)
    TS = TS - TS.mean(1, keepdims=True)
    sd_sum = float(TS.std())
    TS = 50 + 10 * TS / sd_sum

    def table(t):
        rows = []
        b = np.digitize(t, BINS)                     # 0..6
        for bi in range(len(LABELS)):
            row = []
            for c in range(6):
                m = b[:, c] == bi
                p = POS[m, c]
                n = int(m.sum())
                row.append({"n": n, "r1": round(float(np.mean(p == 1)), 4) if n else None,
                            "r2": round(float(np.mean((p >= 1) & (p <= 2))), 4) if n else None,
                            "r3": round(float(np.mean(p >= 1)), 4) if n else None})
            rows.append(row)
        return rows
    avg = [{"r1": round(float(np.mean(POS[:, c] == 1)), 4), "r2": round(float(np.mean((POS[:, c] >= 1) & (POS[:, c] <= 2))), 4),
            "r3": round(float(np.mean(POS[:, c] >= 1)), 4)} for c in range(6)]
    out = {"07": {"races": int(len(O)), "keys": KEYS, "names": NAMES,
                  "cm": np.round(cm, 4).tolist(), "sd": np.round(sd, 4).tolist(), "sd_sum": round(sd_sum, 4),
                  "bins": BINS, "labels": LABELS, "avg": avg,
                  "tables": {"sum": table(TS), **{k: table(T[:, :, i]) for i, k in enumerate(KEYS)}}}}
    with open(os.path.join(DATA, "model", "hensa.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"偏差値の基準: 蒲郡 {len(O)}レース")


if __name__ == "__main__":
    main()
