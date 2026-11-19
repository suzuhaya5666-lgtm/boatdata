// 蒲郡の毎レース予想を保存する(振り返り用)。
//   node scripts/pred.js YYYYMMDD [場コード...]
// data/pred/YYYYMMDD/<場>.json に、レースごとに
//   pre : 展示前(出走表だけ)の予想。最初に1回だけ作る
//   post: 展示後(直前情報・オッズ込み)の予想。締切まで取り直し、締切後は固定
// を残す。計算は画面(index.html)の出目確率と同じ関数を読み込んで使う。
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..");
const J = (p) => { try { return JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8")); } catch (e) { return null; } };

// index.html から必要な関数・定数だけ取り出す
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
function pick(name) {
  let i = html.indexOf(`\nfunction ${name}(`);
  if (i < 0) i = html.indexOf(`\nconst ${name} =`);
  if (i < 0) throw new Error(`index.html に ${name} がない`);
  const start = i + 1, first = html.slice(start, html.indexOf("\n", start)).trimEnd();
  if (!/[{\[(]$/.test(first)) return first;
  const e = html.indexOf("\n}", start);
  return html.slice(start, html.indexOf("\n", e + 1));
}
const NAMES = ["ZERO", "CLS_NUM", "demeQ", "demeAdjust", "featGroups", "defaultMask", "raceKind", "biasFor", "demeTri", "ARE", "WIND", "areWind", "areOf"];
const src = NAMES.map(pick).join("\n");
var state = {};
eval(src.replace(/^const /gm, "var "));

const day = process.argv[2];
const codes = process.argv.slice(3).length ? process.argv.slice(3) : ["07"];
const date = `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}`;
const dayData = J(`data/races/${day.slice(0, 4)}/${day}.json`);
state.date = date;
state.day = dayData;
if (!dayData) process.exit(0);
const model = J("data/model/weights.json"), vs = J("data/stats/venues.json");
globalThis.GCOURSE = J("data/model/gcourse.json");   // SG・G1でのコース別成績
const nowD = new Date(Date.now() + 9 * 3600 * 1000).toISOString();
const now = nowD.slice(11, 16), today = nowD.slice(0, 10);
const SPLIT = 12;

// 穴12点: 決まった形(12点)ごとに、期待値(確率×オッズの合計)が最大になる艇を選ぶ。
//   A 1-4-4: X-ABCD-ABCD / B 1-3-5: X-ABC-残り全部 / C 2-3-5: XY-XYZ-(1艇除く5艇)
//   D 2-4-4: XY-XYZW-XYZW / E 3艇ボックス×2 。best はその中で期待値が最大の形
function ana12(tri, o3) {
  const B = ["1", "2", "3", "4", "5", "6"];
  const P = Object.fromEntries(tri.map((t) => [t.k, t.p]));
  const comb = (a, n) => (n === 0 ? [[]] : a.flatMap((x, i) => comb(a.slice(i + 1), n - 1).map((r) => [x, ...r])));
  const perm = (h, s2, s3) => h.flatMap((a) => s2.flatMap((b) => s3.filter((c) => a !== b && b !== c && a !== c).map((c) => `${a}-${b}-${c}`)))
    .filter((k) => new Set(k.split("-")).size === 3);
  const sh = { A: [], B: [], C: [], D: [], E: [] };
  for (const h of B) {
    const o = B.filter((x) => x !== h);
    comb(o, 4).forEach((s) => sh.A.push({ f: `${h}-${s.join("")}-${s.join("")}`, k: perm([h], s, s) }));
    comb(o, 3).forEach((s) => sh.B.push({ f: `${h}-${s.join("")}-${o.join("")}`, k: perm([h], s, o) }));
  }
  for (const hh of comb(B, 2)) {
    const o = B.filter((x) => !hh.includes(x));
    for (const c of o) for (const ex of o) if (ex !== c) {
      const s3 = B.filter((x) => x !== ex);
      sh.C.push({ f: `${hh.join("")}-${[...hh, c].sort().join("")}-${s3.join("")}`, k: perm(hh, [...hh, c], s3) });
    }
    comb(o, 2).forEach((s) => { const g = [...hh, ...s].sort(); sh.D.push({ f: `${hh.join("")}-${g.join("")}-${g.join("")}`, k: perm(hh, g, g) }); });
  }
  const t3 = comb(B, 3);
  for (let i = 0; i < t3.length; i++) for (let j = i + 1; j < t3.length; j++)
    sh.E.push({ f: `BOX${t3[i].join("")}+BOX${t3[j].join("")}`, k: [...perm(t3[i], t3[i], t3[i]), ...perm(t3[j], t3[j], t3[j])] });
  const r4 = (x) => Math.round(x * 10000) / 10000;
  const out = {};
  let best = null;
  for (const [name, cands] of Object.entries(sh)) {
    let top = null, tv = -1;
    for (const c of cands) { const v = c.k.reduce((s, k) => s + (P[k] || 0) * (o3[k] || 0), 0); if (v > tv) { tv = v; top = c; } }
    out[name] = { f: top.f, k: top.k, ev: r4(tv / 12), p: r4(top.k.reduce((s, k) => s + (P[k] || 0), 0)) };
    if (!best || out[name].ev > out[best].ev) best = name;
  }
  out.best = best;
  return out;
}

// 2連単1位の目を頭・2着に固定した3連単。3着は確率の低い艇を切る
//   cut1: 1艇切(3点) / cut10・cut15・cut20: 3着になる確率の合計がその%を超えない所まで下から削る
//   o2: その2連単のオッズ。syn: 各買い目の3連単の合成オッズ(オッズがある時だけ)。
//   切替 = o2 > syn なら同じ金額で2連単1点、そうでなければ3連単
function ex3(tri, exs, o3, o2s) {
  const [top, pt] = exs[0];
  const rest = tri.filter((t) => t.k.startsWith(top + "-")).sort((a, b) => b.p - a.p);
  const r4 = (x) => Math.round(x * 10000) / 10000;
  const one = (t) => ({ k: t.k, p: r4(t.p), o: o3[t.k] || null });
  const cutTo = (x) => { const c = [...rest]; let cut = 0;
    while (c.length > 1 && cut + c[c.length - 1].p / pt <= x + 1e-12) { cut += c[c.length - 1].p / pt; c.pop(); }
    return c; };
  const syn = (ks) => (ks.every((t) => o3[t.k] > 0) ? Math.round(100 / ks.reduce((s, t) => s + 1 / o3[t.k], 0)) / 100 : null);
  const sets = { cut1: rest.slice(0, 3), cut10: cutTo(0.1), cut15: cutTo(0.15), cut20: cutTo(0.2) };
  const out = { k: top, p: r4(pt), o2: o2s[top] || null };
  for (const [n, ks] of Object.entries(sets)) { out[n] = ks.map(one); out[n + "_syn"] = syn(ks); }
  return out;
}

// 予想とオッズを混ぜた確率(過去データで一番当たった比率: 予想0.18・オッズ0.90)で期待値を出す
//  bl2 : 2連単 確率上位3つで期待値が一番高い目(1.1以上)の1点
//  bl10: 1位固定10%切替(2連単1位を固定、3連単の下10%を削り、2連単の方が高ければ2連単)。買う形の期待値1.1以上
const BL_A = 0.18, BL_B = 0.90, BL_EV = 1.1;
function blendBets(tri, o3, o2) {
  if (Object.keys(o3).length < 100) return null;
  const io = tri.map((t) => (o3[t.k] > 0 ? 1 / o3[t.k] : 0)), si = io.reduce((a, b) => a + b, 0);
  const lp = tri.map((t, i) => BL_A * Math.log(t.p + 1e-6) + BL_B * Math.log(io[i] / si + 1e-6));
  const mx = Math.max(...lp), ex = lp.map((x) => Math.exp(x - mx)), se = ex.reduce((a, b) => a + b, 0);
  const pb = {}; tri.forEach((t, i) => { pb[t.k] = ex[i] / se; });
  const out0 = { p3b: packP(pb) };
  const p2 = {}; for (const [k, p] of Object.entries(pb)) { const k2 = k.split("-").slice(0, 2).join("-"); p2[k2] = (p2[k2] || 0) + p; }
  const ord2 = Object.entries(p2).sort((a, b) => b[1] - a[1]);
  const r2 = (x) => Math.round(x * 100) / 100;
  const out = out0;
  const c2 = ord2.slice(0, 3).filter(([k, p]) => o2[k] > 0 && p * o2[k] >= BL_EV).sort((a, b) => b[1] * o2[b[0]] - a[1] * o2[a[0]]);
  if (c2.length) { const [k, p] = c2[0]; out.bl2 = { ev: r2(p * o2[k]), buy: [[k, 100, o2[k]]] }; }
  const [top, pt] = ord2[0];
  const c = Object.keys(pb).filter((k) => k.startsWith(top + "-")).sort((a, b) => pb[b] - pb[a]);
  let cut = 0;
  while (c.length > 1 && cut + pb[c[c.length - 1]] / pt <= 0.1 + 1e-12) { cut += pb[c[c.length - 1]] / pt; c.pop(); }
  if (c.every((k) => o3[k] > 0)) {
    const syn = 1 / c.reduce((a, k) => a + 1 / o3[k], 0);
    if (o2[top] > syn) { if (pt * o2[top] >= BL_EV) out.bl10 = { ev: r2(pt * o2[top]), buy: [[top, 100 * c.length, o2[top]]] }; }
    else { const ps = c.reduce((a, k) => a + pb[k], 0); if (ps * syn >= BL_EV) out.bl10 = { ev: r2(ps * syn), buy: c.map((k) => [k, 100, o3[k]]) }; }
  }
  return out;
}

const PERMS3 = [];
for (let a = 1; a <= 6; a++) for (let b = 1; b <= 6; b++) for (let c = 1; c <= 6; c++) if (a !== b && b !== c && a !== c) PERMS3.push(`${a}-${b}-${c}`);
const packP = (m) => PERMS3.map((k) => Math.round((m[k] || 0) * 10000));

const PER = 1000;   // 連複系(切替あり)の1点あたりの金額
function splitAmt(odds, total = PER) {
  const inv = odds.map((o) => 1 / o), sm = inv.reduce((a, b) => a + b, 0);
  const amt = inv.map((x) => Math.max(100, Math.round(total * x / sm / 100) * 100));
  while (amt.reduce((a, b) => a + b, 0) > total && Math.max(...amt) > 100) amt[amt.indexOf(Math.max(...amt))] -= 100;
  return amt;
}
const permsOf = (bs) => bs.length === 2 ? [[bs[0], bs[1]], [bs[1], bs[0]]]
  : bs.flatMap((a) => bs.filter((b) => b !== a).map((b) => [a, b, bs.find((c) => c !== a && c !== b)]));

// 連複系(本命寄りの出目確率)
//  f2ex: 2連複 確率上位3つで期待値が一番高い目(1.3以上)
//  f2x : 2連複 確率上位3つで期待値1.5以上(最大2点)。2連単2点の合成オッズの方が高ければ2連単に切替
//  f3x : 3連複 確率上位5つで期待値1.5以上(全部、びわこ・住之江は買わない)。3連複 / 3連単ボックス6点 / 1着率が一番低い艇の頭を切った4点 を期待値で比べて一番高い形
function f2of(adj, order, od, code, lean = 1) {
  const of2 = od?.["2f"] || {}, of3 = od?.["3f"] || {}, o2 = od?.["2t"] || {}, o3 = od?.["3t"] || {};
  const tri = demeTri(adj.q, model, lean).map((t) => ({ b: t.c.map((c) => order[c]), p: t.p }));
  const P3 = {}, pf = {}, pf3 = {}, win = {};
  for (const t of tri) {
    P3[t.b.join("-")] = t.p; win[t.b[0]] = (win[t.b[0]] || 0) + t.p;
    const k2 = t.b.slice(0, 2).sort((m, n) => m - n).join("="); pf[k2] = (pf[k2] || 0) + t.p;
    const k3 = [...t.b].sort((m, n) => m - n).join("="); pf3[k3] = (pf3[k3] || 0) + t.p;
  }
  const r4 = (x) => Math.round(x * 10000) / 10000, r2 = (x) => Math.round(x * 100) / 100;
  const tops = (pp, oo, n) => Object.entries(pp).sort((a, b) => b[1] - a[1]).slice(0, n)
    .map(([k, p]) => ({ k, p: r4(p), o: oo[k] || null, ev: oo[k] ? r2(p * oo[k]) : null }));
  const top = tops(pf, of2, 3);
  const best = top.filter((z) => z.ev != null).sort((a, b) => b.ev - a.ev)[0];
  const ok2 = Object.keys(of2).length >= 10, ok3 = Object.keys(of3).length >= 15 && Object.keys(o3).length > 60;
  const syn = (ks, oo) => ks.every((k) => oo[k] > 0) ? 1 / ks.reduce((a, k) => a + 1 / oo[k], 0) : null;
  const f2x = !ok2 ? null : top.filter((z) => z.ev != null && z.ev >= 1.5).sort((a, b) => b.ev - a.ev).slice(0, 2).map((z) => {
    const ks = permsOf(z.k.split("=").map(Number)).map((x) => x.join("-")), s = syn(ks, o2);
    if (s && s > z.o) { const a = splitAmt(ks.map((k) => o2[k])); return { ...z, syn: r2(s), buy: ks.map((k, i) => [k, a[i], o2[k]]) }; }
    return { ...z, syn: s ? r2(s) : null, buy: [[z.k, PER, z.o]] };
  });
  const f3x = !ok3 || ["11", "12"].includes(code) ? null : tops(pf3, of3, 5).filter((z) => z.ev != null && z.ev >= 1.5).map((z) => {
    const bs = z.k.split("=").map(Number), lo = bs.reduce((m, b) => ((win[b] || 0) < (win[m] || 0) ? b : m), bs[0]);
    const k6 = permsOf(bs).map((x) => x.join("-")), k4 = k6.filter((k) => Number(k[0]) !== lo);
    const c = [{ ev: z.ev, buy: [[z.k, PER, z.o]], f: "3連複" }];
    const s6 = syn(k6, o3), s4 = syn(k4, o3);
    if (s6) c.push({ ev: k6.reduce((a, k) => a + (P3[k] || 0), 0) * s6, ks: k6, f: "ボックス" });
    if (s4) c.push({ ev: k4.reduce((a, k) => a + (P3[k] || 0), 0) * s4, ks: k4, f: `${lo}頭切り` });
    const w = c.sort((a, b) => b.ev - a.ev)[0];
    if (w.ks) { const a = splitAmt(w.ks.map((k) => o3[k])); w.buy = w.ks.map((k, i) => [k, a[i], o3[k]]); }
    return { ...z, form: w.f, buy: w.buy };
  });
  return { f2: top, f2ex: ok2 && best && best.ev >= 1.3 ? [best] : null, f2x: f2x?.length ? f2x : null, f3x: f3x?.length ? f3x : null, p3h: packP(P3) };
}

function predict(code, race, bi, od) {
  const racers = Object.fromEntries(race.entries.map((e) => [e.boat, J(`data/stats/racers/${e.regno}.json`)]));
  const ents = Object.fromEntries(race.entries.map((e) => [e.boat, e]));
  let order = [0, 1, 2, 3, 4, 5, 6];
  if (bi?.start?.length === 6) order = [0, ...bi.start.map((s) => s.boat)];
  const adj = demeAdjust(demeQ(order, racers, vs.venues[code].all, model), order, ents, racers, bi, model, { ...race, _v: code });
  const tri = demeTri(adj.q).map((t) => ({ k: t.c.map((c) => order[c]).join("-"), p: t.p }));
  const ex = {}; tri.forEach((t) => { const k = t.k.split("-").slice(0, 2).join("-"); ex[k] = (ex[k] || 0) + t.p; });
  const exs = Object.entries(ex).sort((a, b) => b[1] - a[1]);
  const o3 = od?.["3t"] || {}, o2 = od?.["2t"] || {};
  const hasOdds = Object.keys(o3).length > 60;
  const r4 = (x) => Math.round(x * 10000) / 10000;
  const one = (t) => ({ k: t.k, p: r4(t.p), o: o3[t.k] || null });
  const win = {}; tri.forEach((t) => { const b = t.k[0]; win[b] = (win[b] || 0) + t.p; });
  const a = areOf({ "3連単": Object.fromEntries(tri.map((t) => [t.k, t.p])) }, code, bi?.weather);
  const fav = hasOdds ? Math.min(...Object.values(o3).filter((x) => x > 0)) : null;
  return {
    time: now, exh: !!bi?.start?.length, odds: od?.time || null, order: order.slice(1).join(""), used: adj.used,
    nige: r4(win[String(order[1])] || 0), win: Object.fromEntries(Object.entries(win).map(([b, p]) => [b, r4(p)])),
    top10: r4(a?.s ?? 0), are: a?.label || null, fav,
    split: fav != null && fav >= SPLIT, combo: fav != null && fav >= SPLIT && ["07", "19"].includes(code) && a && a.lvl >= 2,
    hon: tri.slice(0, 3).map(one),
    top10list: tri.slice(0, 10).map(one),
    p3: packP(Object.fromEntries(tri.map((t) => [t.k, t.p]))),   // 中間の出目確率(120通り×10000)
    // 2連単の確率上位3つ: オッズ・期待値と、同じ1着・2着の3連単4点流しの合成オッズ(高ければ3連単に切り替える)
    ex1: exs.slice(0, 3).map(([k, p]) => {
      const t3 = Object.entries(o3).filter(([kk, v]) => kk.startsWith(k + "-") && v > 0).map(([, v]) => v);
      const syn = t3.length === 4 ? Math.round(100 / t3.reduce((a, v) => a + 1 / v, 0)) / 100 : null;
      return { k, p: r4(p), o: o2[k] || null, ev: o2[k] ? Math.round(p * o2[k] * 100) / 100 : null, syn };
    }),
    // オッズがあるときだけ(最終オッズでの検証と同じ選び方)
    bal: hasOdds ? tri.slice(0, 10).filter((t) => o3[t.k] && t.p * o3[t.k] >= 1).sort((x, y) => y.p * o3[y.k] - x.p * o3[x.k]).slice(0, 5).map(one) : null,
    ex: hasOdds ? exs.slice(0, 3).filter(([k, p]) => o2[k] && p * o2[k] >= 1.2).sort((x, y) => y[1] * o2[y[0]] - x[1] * o2[x[0]]).slice(0, 1).map(([k, p]) => ({ k, p: r4(p), o: o2[k] })) : null,
    ana: hasOdds ? tri.filter((t) => (o3[t.k] || 0) >= 100 && t.p * o3[t.k] >= 1.5).sort((x, y) => y.p * o3[y.k] - x.p * o3[x.k]).slice(0, 5).map(one) : null,
    ana12: hasOdds ? ana12(tri, o3) : null,
    ex3: ex3(tri, exs, o3, o2),
    ...(blendBets(tri, o3, o2) || {}),
    // 2連複(本命寄りの出目確率): 確率上位3つと、その中で期待値が一番高い目(1.3以上なら買い)
    ...f2of(adj, order, od, code),
    // 同じ2連複1.5切替を中間の出目確率で(過去データで一番良かった形)
    f2x_m: f2of(adj, order, od, code, 0.5).f2x,
  };
}

for (const code of codes) {
  const v = dayData.venues?.[code];
  if (!v) continue;
  const file = `data/pred/${day}/${code}.json`;
  const out = J(file) || { date, venue: code, races: {} };
  let changed = false;
  for (const race of Object.values(v.races).sort((a, b) => a.no - b.no)) {
    const no = String(race.no).padStart(2, "0");
    const slot = out.races[race.no] = out.races[race.no] || {};
    const closed = date < today || (date === today && race.deadline && now >= race.deadline);  // 締切の時刻になったら固定(締切後のオッズは使わない)
    // 節間のまとめ(節間成績・節間平均の展示)。画面の withSeries と同じ
    const sm = J("data/model/series.json")?.[code], sr = J(`data/series/${day}/${code}.json`);
    const withS = (b) => (sr?.racers ? { ...(b || {}), series: sr, seriesModel: sm } : b);
    if (!slot.pre) { slot.pre = predict(code, race, withS(null), null); changed = true; }
    if (!closed || !slot.post) {
      const bi = J(`data/before/${day}/${code}_${no}.json`);
      if (bi?.start?.length === 6 && !closed) {
        slot.post = predict(code, race, withS(bi), J(`data/live/${day}/${code}_${no}.json`));
        changed = true;
      }
    }
  }
  if (changed) {
    fs.mkdirSync(path.join(ROOT, `data/pred/${day}`), { recursive: true });
    fs.writeFileSync(path.join(ROOT, file), JSON.stringify(out));
  }
  console.log(`予想 ${code} ${changed ? "保存" : "変更なし"}`);
}
