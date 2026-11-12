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
from stats import f_period, grade_cat, load_races, started  # noqa: E402
from collect import load_grades  # noqa: E402

M = 10           # 回数が少ないときに場平均へ寄せる強さ(10走分)
WARMUP_DAYS = 60  # 最初の2ヶ月はデータを貯めるだけ
OUT = os.path.join(DATA, "model", "weights.json")
FEATURES = ["exh", "nat_win", "loc_win", "motor_2", "class", "avg_st", "f_cnt", "lap", "turn", "straight", "setsu",
            "ex_st", "parts", "tilt", "motor_form", "local"]
FEATURE_NAMES = ["展示タイム", "全国勝率", "当地勝率", "モーター2連率", "級別", "平均ST", "F持ち", "一周", "まわり足", "直線", "節間成績",
                 "展示ST", "部品交換", "チルト", "モーターの調子", "地元"]
# 材料のまとまり(画面で一緒にオン・オフする単位)。オリジナル展示の3つは1つにまとめる
GROUPS = [[0, 11, 12, 13], [1], [2, 15], [3], [4], [5], [6], [7, 8, 9], [10, 14]]
GROUP_NAMES = ["展示(タイム・ST・部品・チルト)", "全国勝率", "当地勝率・地元", "モーター2連率", "級別", "平均ST", "F持ち", "オリジナル展示", "節間成績・モーターの調子"]
ORG = [7, 8, 9]  # オリジナル展示(場独自の計測、今は蒲郡のみ)。コースの平均を引いて、速いほど大きくして使う
FDELTA = {1: 0.018, 2: 0.061}  # F持ちで平均STが遅くなる量(F持ち時のSTが5本未満のときに使う)
SETSU_K = 6      # 節間成績: 走数が少ないときに0(平均)へ寄せる強さ。値 = 平均 × n/(n+6)
SETSU_CM = {}    # 場×コースの平均着順(予想がないレースの節間成績に使う。画面にも渡す)
ROWMETA = []     # 学習に使う各レース: (場, 日, R, {コース: 登番}, {コース: モーター番号})
LOCAL_BRANCH = {"07": "愛知", "08": "愛知", "11": "滋賀", "12": "大阪", "19": "山口"}  # 地元の支部
MOTOR_DAYS, MOTOR_K = 90, 12   # モーターの調子: 直近90日のその場での(着順 − 予想の着順)の平均を走数で寄せる
RESULTS = []     # 5場の全レースの着順: (場, 登番, 日, R, コース, 着順, モーター番号)


def setsu_value(srec, code, regno, day):
    """節間成績: その節の前日までの(着順 − 場×コース平均着順)の平均を、走数で0に寄せた値。小さいほど好調。
    節は「その場で続けて走った日」をさかのぼってまとめる。"""
    vals = []
    d = day - dt.timedelta(days=1)
    for _ in range(10):
        x = srec.get((code, regno, d))
        if not x:
            break
        vals += x
        d -= dt.timedelta(days=1)
    if not vals:
        return np.nan
    return float(np.mean(vals)) * len(vals) / (len(vals) + SETSU_K)


KINDS = ["予選", "準優", "優勝戦", "選抜・特選"]  # レースの種別(それ以外は補正なし)


def race_kind(name):
    name = name or ""
    if "優勝" in name and "準" not in name:
        return 3
    if "準優" in name:
        return 2
    if "予選" in name:
        return 1
    if any(x in name for x in ("選抜", "特選", "特賞", "ドリーム")):
        return 4
    return 0


VCODES = sorted(VENUES)
VENUE_IDX = {c: i for i, c in enumerate(VCODES)}
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
    stN = collections.defaultdict(lambda: np.zeros(2))  # F持ちでないときのST
    stF = collections.defaultdict(lambda: np.zeros(2))  # F持ちのときのST
    fcnt = collections.Counter()                        # (選手, 期) -> F数
    grades = load_grades()
    by_day = collections.defaultdict(list)
    for r in races:
        by_day[r[0]].append(r)
    # 場×コースの平均着順(失格・転覆なども6着として数える)
    cmc = collections.defaultdict(lambda: [0.0, 0])
    for _, code, race, ents in races:
        if code in VENUES:
            for e in ents:
                a = cmc[(code, e["course"])]
                a[0] += e.get("place") or 6
                a[1] += 1
    SETSU_CM.clear()
    ROWMETA.clear()
    RESULTS.clear()
    for code in VENUES:
        SETSU_CM[code] = [round(cmc[(code, c)][0] / cmc[(code, c)][1], 4) if cmc[(code, c)][1] else 3.5 for c in range(7)]
    srec = collections.defaultdict(list)  # (場, 選手, 日) -> [着順 − コース平均]
    X, N, P, Y, F, H, V, S, KD = [], [], [], [], [], [], [], [], []
    KR, KY = [], []   # 決まり手率(1コース: 被差し・被まくり・被まくり差し、2〜6: 差し・まくり・まくり差し)と、実際の決まり手
    klost = collections.defaultdict(lambda: np.zeros(4))
    kwin = collections.defaultdict(lambda: np.zeros(4))
    glost = np.zeros(4)
    gwin = collections.defaultdict(lambda: np.zeros(4))
    for day in sorted(by_day):
        if day >= first + dt.timedelta(days=WARMUP_DAYS):
            for _, code, race, ents in by_day[day]:
                courses = {e["course"]: e for e in ents}
                if code not in VENUES or sorted(courses) != [1, 2, 3, 4, 5, 6]:
                    continue
                if any(e["course"] != e["boat"] for e in ents):  # 前付けなど枠なりでないレースは学習に使わない
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
                org = org_of(day, code, race["no"])
                f = np.full((7, len(FEATURES)), np.nan)
                for k in range(1, 7):
                    e = courses[k]
                    b = de.get(e["boat"], {})
                    fc = fcnt[(e["regno"], f_period(day))]
                    n_, f_ = stN[e["regno"]], stF[e["regno"]]
                    normal = n_[0] / n_[1] if n_[1] else np.nan
                    adj = normal if fc == 0 else (f_[0] / f_[1] if f_[1] >= 5 else normal + FDELTA[min(fc, 2)])
                    f[k] = [e.get("exh") or np.nan, b.get("nat_win") or np.nan, b.get("loc_win") or np.nan,
                            b["motor_2"] if b.get("motor_2") is not None else np.nan,
                            CLS.get(b.get("class"), np.nan), adj, fc] + \
                        [(org.get(str(e["boat"])) or {}).get(x) or np.nan for x in ("lap", "turn", "straight")] + \
                        [setsu_value(srec, code, e["regno"], day)] + before_feats(day, code, race["no"], e["boat"]) + [np.nan] + \
                        [1.0 if (b.get("branch") or e.get("branch")) == LOCAL_BRANCH.get(code) else 0.0]
                X.append(x); N.append(n); P.append(prior); Y.append([pl[1], pl[2], pl[3]]); F.append(f)
                ROWMETA.append((code, day, int(race["no"]), {k: courses[k]["regno"] for k in range(1, 7)},
                                {k: courses[k].get("motor_no") for k in range(1, 7)}))
                H.append(any(e.get("nat_win") is not None for e in de.values()))  # 出走表(勝率など)があるレースか
                V.append(VENUE_IDX[code])
                S.append(grade_cat(grades.get((code, day.isoformat()))) == "sgg1")
                KD.append(race_kind(race.get("name")))
                kr = np.zeros((7, 3))
                a = klost[courses[1]["regno"]]
                kr[1] = (a[1:] + KIM_M * glost[1:] / max(glost[0], 1)) / (a[0] + KIM_M)
                for k in range(2, 7):
                    b, g = kwin[(courses[k]["regno"], k)], gwin[k]
                    kr[k] = (b[1:] + KIM_M * g[1:] / max(g[0], 1)) / (b[0] + KIM_M)
                KR.append(kr)
                KY.append(kim_alt(pl[1], race.get("kimarite")))
        for _, code, race, ents in by_day[day]:
            for e in ents:
                c, p = e["course"], e.get("place")
                if code in SETSU_CM and 1 <= c <= 6:
                    srec[(code, e["regno"], day)].append((p or 6) - SETSU_CM[code][c])
                    RESULTS.append((code, e["regno"], day, int(race["no"]), c, p or 6, e.get("motor_no")))
                s = selfc[e["regno"]][c]
                if re.match(r"^\d?\.\d+$", e.get("st") or ""):
                    (stF if fcnt[(e["regno"], f_period(day))] else stN)[e["regno"]] += [float(e["st"]), 1]
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
        for _, code, race, ents in by_day[day]:  # 決まり手もその日が終わってから数える
            km = race.get("kimarite")
            winner = next((e for e in ents if e.get("place") == 1), None)
            for e in ents:
                if e["course"] == 1:
                    klost[e["regno"]][0] += 1
                    glost[0] += 1
                    if winner is not None and winner is not e and km in KM3:
                        klost[e["regno"]][1 + KM3.index(km)] += 1
                        glost[1 + KM3.index(km)] += 1
                elif 2 <= e["course"] <= 6:
                    kwin[(e["regno"], e["course"])][0] += 1
                    gwin[e["course"]][0] += 1
                    if e is winner and km in KM3:
                        kwin[(e["regno"], e["course"])][1 + KM3.index(km)] += 1
                        gwin[e["course"]][1 + KM3.index(km)] += 1
        for _, code, race, ents in by_day[day]:  # Fはその日のレースが終わってから数える
            for e in ents:
                if (e.get("st") or "").startswith("F") or (e.get("status") or "").startswith("F"):
                    fcnt[(e["regno"], f_period(day))] += 1
    return (np.array(X), np.array(N), np.array(P), np.array(Y), np.array(F), np.array(H), np.array(V), np.array(S, bool),
            np.array(KD), np.array(KR), np.array(KY), races[0][0], races[-1][0])


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


# ---------------------------------------------------------------- 予想決まり手
KM3 = ("差し", "まくり", "まくり差し")
KIM_M = 40
KIM_TY = ["逃げ", "差し", "まくり", "まくり差し"]
KIM_ALT = [(1, "逃げ")] + [(k, t) for k in range(2, 7) for t in KM3] + [(0, "その他")]
KIM_FEATS = [("exh", -1), ("avg_st", -1), ("lap", 1), ("turn", 1), ("straight", 1)]  # 標準化した値に掛ける向き(速いほど+)


def kim_alt(win_course, km):
    for a, (k, t) in enumerate(KIM_ALT):
        if k == win_course and t == km:
            return a
    return len(KIM_ALT) - 1


def kim_design(Z, KR, lr_mean=None):
    """選択肢(勝つコース×決まり手)ごとの説明変数。画面(index.html)でも同じ並びで計算する。
    列: 材料ごとに [攻める艇(決まり手4種)、1コース(差し・まくり・まくり差し)] の7列、最後に決まり手率2列。"""
    R, NA = len(Z), len(KIM_ALT)
    cols = []
    for f, sgn in KIM_FEATS:
        z = sgn * Z[:, :, FEATURES.index(f)]
        for t in KIM_TY:
            a = np.zeros((R, NA))
            for ai, (k, tt) in enumerate(KIM_ALT):
                if tt == t and k >= 1:
                    a[:, ai] = z[:, k]
            cols.append(a)
        for t in KM3:
            b = np.zeros((R, NA))
            for ai, (k, tt) in enumerate(KIM_ALT):
                if tt == t and k >= 2:
                    b[:, ai] = z[:, 1]
            cols.append(b)
    a = np.zeros((R, NA))
    b = np.zeros((R, NA))
    for ai, (k, tt) in enumerate(KIM_ALT):
        if k >= 2:
            ti = KM3.index(tt)
            a[:, ai] = np.log(KR[:, k, ti] + 1e-3)
            b[:, ai] = np.log(KR[:, 1, ti] + 1e-3)
        elif k == 1:
            b[:, ai] = np.log(np.clip(1 - KR[:, 1, :].sum(1), 0.05, 1))
    if lr_mean is None:
        lr_mean = [a.mean(0).tolist(), b.mean(0).tolist()]
    cols += [a - np.array(lr_mean[0]), b - np.array(lr_mean[1])]
    return np.stack(cols, 2), lr_mean


def fit_kim(X, y, l2=0.003):
    NA, p, n = X.shape[1], X.shape[2], len(y)

    def f(t):
        s = np.concatenate([[0], t[:NA - 1]])[None, :] + X @ t[NA - 1:]
        s = s - s.max(1, keepdims=True)
        lse = np.log(np.exp(s).sum(1))
        G = np.exp(s - lse[:, None])
        G[np.arange(n), y] -= 1
        G /= n
        return (-(s[np.arange(n), y] - lse).mean() + l2 * (t[NA - 1:] ** 2).sum(),
                np.concatenate([G.sum(0)[1:], np.einsum("ra,rap->p", G, X) + 2 * l2 * t[NA - 1:]]))
    t = minimize(f, np.zeros(NA - 1 + p), jac=True, method="L-BFGS-B").x
    return np.concatenate([[0], t[:NA - 1]]), t[NA - 1:]


def kim_models(Z, KR, KY, V, prog):
    """場ごとに予想決まり手の係数を決める(平均だけの分も)。"""
    out = {"alt": [[k, t] for k, t in KIM_ALT], "feats": KIM_FEATS, "venues": {}}
    for vi, c in enumerate(VCODES):
        rows = prog[V[prog] == vi]
        if len(rows) < 300:
            continue
        X, lr_mean = kim_design(Z[rows], KR[rows])
        y = KY[rows]
        a, b = fit_kim(X, y)
        avg = np.bincount(y, minlength=len(KIM_ALT)) / len(y)
        out["venues"][c] = {"a": np.round(a, 4).tolist(), "b": np.round(b, 4).tolist(), "lr_mean": np.round(lr_mean, 4).tolist(),
                            "avg": np.round(avg, 4).tolist(), "races": int(len(rows))}
    return out


_org_cache = {}


def org_of(day, code, no):
    """オリジナル展示 {艇番: {lap, turn, straight}}。過去分(org_past)か、直前情報に保存した分。"""
    import gzip
    if code != "07":
        return {}
    if day not in _org_cache:
        p = os.path.join(DATA, "org_past", f"{day:%Y}", f"{day:%Y%m%d}.json.gz")
        d = {}
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                d = json.load(f)
        _org_cache[day] = d
    key = f"{code}_{int(no):02d}"
    o = _org_cache[day].get(key)
    if o is None:
        p = os.path.join(DATA, "before", f"{day:%Y%m%d}", f"{key}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                o = json.load(f).get("org")
    return o or {}


_bp_cache = {}


def st_num(st):
    """展示STの文字列 → 数値。Fはマイナス、出遅れなどは None。"""
    if not st or not any(ch.isdigit() for ch in st):
        return None
    try:
        v = float(st.replace("F", "").replace("L", ""))
    except ValueError:
        return None
    return -v if st.startswith("F") else v


def before_feats(day, code, no, boat):
    """直前情報から [展示ST, 部品交換(0/1), チルト]。過去分(before_past)か、保存した直前情報。"""
    import gzip
    if day not in _bp_cache:
        p = os.path.join(DATA, "before_past", f"{day:%Y}", f"{day:%Y%m%d}.json.gz")
        d = {}
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                d = json.load(f)
        _bp_cache[day] = d
    key = f"{code}_{int(no):02d}"
    b = _bp_cache[day].get(key)
    if b is None:
        p = os.path.join(DATA, "before", f"{day:%Y%m%d}", f"{key}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                b = json.load(f)
    if not b:
        return [np.nan, np.nan, np.nan]
    st = next((x.get("st") for x in (b.get("start") or []) if x.get("boat") == boat), None)
    be = (b.get("entries") or {}).get(str(boat)) or {}
    v = st_num(st)
    t = be.get("tilt")
    return [np.nan if v is None else v, (1.0 if be.get("parts") else 0.0) if be else np.nan, np.nan if t is None else float(t)]


def motor_by_expect(E):
    """モーターの調子: その場の直近 MOTOR_DAYS 日の(着順 − 予想の着順)の平均を走数で0に寄せる。小さいほど好調。"""
    mrec = collections.defaultdict(list)
    for code, rg, day, no, c, pl, mno in RESULTS:
        if mno is None:
            continue
        e = E.get((code, day, no, c))
        mrec[(code, mno)].append((day, pl - (e if e is not None else SETSU_CM[code][c])))
    for v in mrec.values():
        v.sort(key=lambda x: x[0])
    out = np.full((len(ROWMETA), 7), np.nan)
    import bisect
    for i, (code, day, no, regs, motors) in enumerate(ROWMETA):
        for k in range(1, 7):
            lst = mrec.get((code, motors.get(k)))
            if not lst:
                continue
            ds = [d for d, _ in lst]
            a, b = bisect.bisect_left(ds, day - dt.timedelta(days=MOTOR_DAYS)), bisect.bisect_left(ds, day)
            xs = [x for _, x in lst[a:b]]
            if xs:
                out[i, k] = float(np.mean(xs)) * len(xs) / (len(xs) + MOTOR_K)
    return out


def org_transform(F, V):
    """オリジナル展示: 場・コースごとの平均を引いて符号を逆に(速いほど大きく)。平均は画面でも使う。"""
    cm = {}
    for vi, c in enumerate(VCODES):
        rows = V == vi
        m = np.nanmean(F[rows][:, :, ORG], axis=0) if rows.any() else np.full((7, len(ORG)), np.nan)
        if np.isnan(m[1:]).all():
            continue
        cm[c] = [[round(float(m[k, i]), 4) if not np.isnan(m[k, i]) else 0 for k in range(7)] for i in range(len(ORG))]
        for i, fi in enumerate(ORG):
            F[rows, :, fi] = -(F[rows, :, fi] - np.array(cm[c][i])[None, :])
    return F, cm


def standardize(F):  # noqa: E302
    """レース内の平均との差を、全体のばらつきで割る。欠けている値はレース平均(=0)。"""
    Z = F[:, 1:, :].copy()
    mean = np.nanmean(Z, axis=1, keepdims=True)
    Z = np.where(np.isnan(Z), mean, Z)
    Z = np.nan_to_num(Z)  # レース全体で値がない材料(オリジナル展示のない場など)は0
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


def fit_logit(LQ, Z, Y, V, S, KD, feats, use_cbias, rows=None):
    """選んだ材料(feats)とコース補正の有無で、着ごとの係数を決める。
    コース補正は 場ごと(場×コース) と SG・G1のとき(全場共通) の2つ。"""
    if rows is not None:
        LQ, Z, Y, V, S, KD = LQ[rows], Z[rows], Y[rows], V[rows], S[rows], KD[rows]
    nv = len(VCODES)
    nk = len(KINDS)
    Kone = np.eye(nk + 1)[KD][:, 1:]  # (レース, 種別) 0=補正なし
    sg = S.astype(float)[:, None]
    idx = np.arange(len(Y))
    nf = len(feats)
    Zs = Z[:, :, feats]
    coefs, cbias = [], []
    for j in range(3):
        excl = np.zeros((len(Y), 7))
        excl[:, 0] = 1
        for jj in range(j):
            excl[idx, Y[:, jj]] = 1

        def biases(t):
            vb = np.concatenate([np.zeros((nv, 1)), t[1 + nf:1 + nf + nv * 6].reshape(nv, 6)], axis=1)
            sb = np.concatenate([[0], t[1 + nf + nv * 6:1 + nf + nv * 6 + 6]])
            kb = np.concatenate([np.zeros((nk, 1)), t[1 + nf + nv * 6 + 6:].reshape(nk, 6)], axis=1)
            return vb, sb, kb

        onehot = np.zeros((len(Y), 7))
        onehot[idx, Y[:, j]] = 1
        Vone = np.eye(nv)[V]  # (レース, 場)

        def loss(t):
            s = t[0] * LQ[:, :, j] + (Zs @ t[1:1 + nf] if nf else 0)
            if use_cbias:  # 場ごとのコース補正 + SG・G1のときのコース補正
                vb, sb, kb = biases(t)
                s = s + vb[V] + sg * sb[None, :] + Kone @ kb
            s = np.where(excl == 1, -1e9, s)
            s = s - s.max(1, keepdims=True)
            lse = np.log(np.exp(s).sum(1))
            val = -(s[idx, Y[:, j]] - lse).mean()
            # 勾配(計算を速くするため式で出す)
            G = (np.exp(s - lse[:, None]) - onehot) / len(Y)
            g = [np.sum(G * np.where(excl == 1, 0, LQ[:, :, j]))]
            if nf:
                g.append(np.einsum("rk,rkf->f", G, Zs))
            if use_cbias:
                g.append((Vone.T @ G)[:, 1:].ravel())
                g.append((sg * G).sum(0)[1:])
                g.append((Kone.T @ G)[:, 1:].ravel())
                val += 0.001 * (t[1 + nf:] ** 2).sum()
            grad = np.concatenate([np.atleast_1d(x) for x in g])
            if use_cbias:
                grad[1 + nf:] += 0.002 * t[1 + nf:]
            return val, grad
        x0 = np.zeros(1 + nf + ((nv + 1 + nk) * 6 if use_cbias else 0))
        x0[0] = 1
        t = minimize(loss, x0, jac=True, method="L-BFGS-B").x
        full = [0.0] * len(FEATURES)
        for i, f in enumerate(feats):
            full[f] = round(float(t[1 + i]), 4)
        coefs.append([round(float(t[0]), 4)] + full)
        if use_cbias:
            vb, sb, kb = biases(t)
            cbias.append({"venues": {c: np.round(vb[i], 4).tolist() for i, c in enumerate(VCODES)},
                          "sgg1": np.round(sb, 4).tolist(),
                          "kinds": {k: np.round(kb[i], 4).tolist() for i, k in enumerate(KINDS)}})
        else:
            cbias.append({"venues": {c: [0] * 7 for c in VCODES}, "sgg1": [0] * 7, "kinds": {k: [0] * 7 for k in KINDS}})
    return coefs, cbias


PERMS = np.array([(a, b, c) for a in range(1, 7) for b in range(1, 7) for c in range(1, 7)
                  if a != b and b != c and a != c])


def evaluate(LQ, Z, Y, V, S, KD, coefs, cbias, rows):
    """検証用: 指定したレースで、3連単上位10点・2連単上位3点・単勝の的中率。"""
    LQ, Z, Y, V, S, KD = LQ[rows], Z[rows], Y[rows], V[rows], S[rows], KD[rows]
    n = len(Y)
    ex = []
    for j in range(3):
        c = np.array(coefs[j])
        vb = np.array([cbias[j]["venues"][cc] for cc in VCODES])
        kb = np.array([[0] * 7] + [cbias[j]["kinds"][k] for k in KINDS])
        s = c[0] * LQ[:, :, j] + Z @ c[1:] + vb[V] + S.astype(float)[:, None] * np.array(cbias[j]["sgg1"])[None, :] + kb[KD]
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


def predict_marg(LQ, Z, Y, V, S, KD, coefs, cbias, rows):
    """各コースの1〜3着の確率(3連単120通りから足し合わせ)。"""
    LQ, Z, V, S, KD = LQ[rows], Z[rows], V[rows], S[rows], KD[rows]
    ex = []
    for j in range(3):
        c = np.array(coefs[j])
        vb = np.array([cbias[j]["venues"][cc] for cc in VCODES])
        kb = np.array([[0] * 7] + [cbias[j]["kinds"][k] for k in KINDS])
        s = c[0] * LQ[:, :, j] + Z @ c[1:] + vb[V] + S.astype(float)[:, None] * np.array(cbias[j]["sgg1"])[None, :] + kb[KD]
        s[:, 0] = -1e9
        ex.append(np.exp(s - s.max(1, keepdims=True)))
    s1, s2, s3 = ex
    a, b, cc = PERMS[:, 0], PERMS[:, 1], PERMS[:, 2]
    p = (s1[:, a] / s1.sum(1, keepdims=True)) * (s2[:, b] / (s2.sum(1, keepdims=True) - s2[:, a])) * \
        (s3[:, cc] / (s3.sum(1, keepdims=True) - s3[:, a] - s3[:, b]))
    p = p / p.sum(1, keepdims=True)
    m = np.zeros((len(rows), 7, 3))
    for j, col in enumerate((a, b, cc)):
        for k in range(1, 7):
            m[:, k, j] = p[:, col == k].sum(1)
    return m


def setsu_by_expect(E):
    """節間成績(予想との差): 前日までの(着順 − 予想の着順)の平均を走数で0に寄せる。
    予想の着順 = 1×1着率 + 2×2着率 + 3×3着率 + 5×(4着以下)。予想がないレース(前付けなど)はコース平均との差。"""
    srec = collections.defaultdict(list)
    for code, rg, day, no, c, pl, _ in RESULTS:
        e = E.get((code, day, no, c))
        srec[(code, rg, day)].append(pl - (e if e is not None else SETSU_CM[code][c]))
    out = np.full((len(ROWMETA), 7), np.nan)
    for i, (code, day, no, regs, _) in enumerate(ROWMETA):
        for k in range(1, 7):
            out[i, k] = setsu_value(srec, code, regs[k], day)
    return out


def main():
    force = "--force" in sys.argv
    today = dt.datetime.now(JST).date()
    if os.path.exists(OUT) and not force:
        with open(OUT, encoding="utf-8") as f:
            if json.load(f).get("month") == f"{today:%Y-%m}":
                print("今月の重みは作成済み")
                return
    X, N, P, Y, F, H, V, S, KD, KR, KY, first, last = build()
    W, losses = fit(X, N, P, Y)  # コース別成績の重みは全期間で
    prog = np.where(H)[0]        # 材料の係数は、出走表があるレースだけで決める
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        F, org_cm = org_transform(F, V)
        Zp, sd = standardize(F[prog])
    Z = np.zeros((len(Y), 7, len(FEATURES)))
    Z[prog] = Zp
    LQ = base_lq(X, N, P, W)
    # 節間成績は「予想との差」で測る: いったん節間成績なしで予想を作り、その予想の着順と実際の差から作り直す
    SI, MI = FEATURES.index("setsu"), FEATURES.index("motor_form")
    feats0 = [i for i in range(len(FEATURES)) if i not in (SI, MI)]
    c0, b0 = fit_logit(LQ, Z, Y, V, S, KD, feats0, True, rows=prog)
    E = {}
    for st in range(0, len(prog), 4000):
        rr = prog[st:st + 4000]
        mg = predict_marg(LQ, Z, Y, V, S, KD, c0, b0, rr)
        ex_place = mg[:, :, 0] + 2 * mg[:, :, 1] + 3 * mg[:, :, 2] + 5 * (1 - mg.sum(2))
        for r_, i in enumerate(rr):
            code, day, no = ROWMETA[i][:3]
            for k in range(1, 7):
                E[(code, day, no, k)] = float(ex_place[r_, k])
    F[:, :, SI] = setsu_by_expect(E)
    F[:, :, MI] = motor_by_expect(E)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        Zp, sd = standardize(F[prog])
    Z = np.zeros((len(Y), 7, len(FEATURES)))
    Z[prog] = Zp
    print(f"節間成績(予想との差)を作り直し: 予想 {len(E) // 6}レース分")
    # 検証用: 出走表があるレースの古い6割で決めて、新しい4割で確かめる
    cut = int(len(prog) * 0.6)
    tr, te = prog[:cut], prog[cut:]
    W_tr, _ = fit(X[:prog[cut]], N[:prog[cut]], P[:prog[cut]], Y[:prog[cut]])
    LQ_tr = base_lq(X, N, P, W_tr)
    combos = {}
    ng = len(GROUPS)
    for mask in range(1 << (ng + 1)):
        feats = [i for g in range(ng) if mask >> g & 1 for i in GROUPS[g]]
        use_cb = bool(mask >> ng & 1)
        c_tr, b_tr = fit_logit(LQ_tr, Z, Y, V, S, KD, feats, use_cb, rows=tr)
        ev = evaluate(LQ_tr, Z, Y, V, S, KD, c_tr, b_tr, te)
        c_all, b_all = fit_logit(LQ, Z, Y, V, S, KD, feats, use_cb, rows=prog)
        combos[str(mask)] = {"coef": c_all, "cbias": b_all, "eval": ev}
        print(f"組み合わせ {mask:3d}: {ev}")
    full = combos[str((1 << (ng + 1)) - 1)]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"month": f"{today:%Y-%m}", "m": M, "races": int(len(Y)),
                   "from": first.isoformat(), "to": last.isoformat(), "loss": losses,
                   "W": np.round(W, 4).tolist(),
                   "features": FEATURES, "feature_names": FEATURE_NAMES, "sd": np.round(sd, 4).tolist(),
                   "groups": GROUPS, "group_names": GROUP_NAMES, "org_cm": org_cm,
                   "setsu_cm": SETSU_CM, "setsu_k": SETSU_K, "setsu_mode": "expect", "motor_days": MOTOR_DAYS, "motor_k": MOTOR_K, "local_branch": LOCAL_BRANCH,
                   "setsu_base_mask": (1 << (len(GROUPS) + 1)) - 1 - (1 << len(GROUPS) - 1),
                   "coef": full["coef"], "cbias": full["cbias"], "eval_races": int(len(te)), "bias_version": 2,
                   "combos": combos}, f, ensure_ascii=False, separators=(",", ":"))
    kim = kim_models(Z, KR, KY, V, prog)
    with open(os.path.join(os.path.dirname(OUT), "kimarite.json"), "w", encoding="utf-8") as f:
        json.dump(kim, f, ensure_ascii=False, separators=(",", ":"))
    print("予想決まり手: " + ", ".join(f"{c} {v['races']}レース" for c, v in kim["venues"].items()))
    print(f"重みを作成: {len(Y)}レース(材料の係数は {len(prog)}レース)、組み合わせ {len(combos)}通り")


if __name__ == "__main__":
    main()
