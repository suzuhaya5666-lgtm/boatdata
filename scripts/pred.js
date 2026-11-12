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
const NAMES = ["ZERO", "CLS_NUM", "demeQ", "demeAdjust", "featGroups", "raceKind", "biasFor", "demeTri", "ARE", "areOf"];
const src = NAMES.map(pick).join("\n");
var state = {};
eval(src.replace(/^const /gm, "var "));

const day = process.argv[2];
const codes = process.argv.slice(3).length ? process.argv.slice(3) : ["07"];
const date = `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}`;
const dayData = J(`data/races/${day.slice(0, 4)}/${day}.json`);
if (!dayData) process.exit(0);
const model = J("data/model/weights.json"), vs = J("data/stats/venues.json");
const nowD = new Date(Date.now() + 9 * 3600 * 1000).toISOString();
const now = nowD.slice(11, 16), today = nowD.slice(0, 10);
const SPLIT = 12;

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
  const a = areOf({ "3連単": Object.fromEntries(tri.map((t) => [t.k, t.p])) }, code);
  const fav = hasOdds ? Math.min(...Object.values(o3).filter((x) => x > 0)) : null;
  return {
    time: now, exh: !!bi?.start?.length, odds: od?.time || null, order: order.slice(1).join(""), used: adj.used,
    nige: r4(win[String(order[1])] || 0), win: Object.fromEntries(Object.entries(win).map(([b, p]) => [b, r4(p)])),
    top10: r4(a?.s ?? 0), are: a?.label || null, fav,
    split: fav != null && fav >= SPLIT, combo: fav != null && fav >= SPLIT && ["07", "19"].includes(code) && a && a.lvl >= 2,
    hon: tri.slice(0, 3).map(one),
    top10list: tri.slice(0, 10).map(one),
    ex1: exs.slice(0, 3).map(([k, p]) => ({ k, p: r4(p), o: o2[k] || null })),
    // オッズがあるときだけ(最終オッズでの検証と同じ選び方)
    bal: hasOdds ? tri.slice(0, 10).filter((t) => o3[t.k] && t.p * o3[t.k] >= 1).sort((x, y) => y.p * o3[y.k] - x.p * o3[x.k]).slice(0, 5).map(one) : null,
    ex: hasOdds ? exs.slice(0, 3).filter(([k, p]) => o2[k] && p * o2[k] >= 1.2).sort((x, y) => y[1] * o2[y[0]] - x[1] * o2[x[0]]).slice(0, 1).map(([k, p]) => ({ k, p: r4(p), o: o2[k] })) : null,
    ana: hasOdds ? tri.filter((t) => (o3[t.k] || 0) >= 100 && t.p * o3[t.k] >= 1.5).sort((x, y) => y.p * o3[y.k] - x.p * o3[x.k]).slice(0, 5).map(one) : null,
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
    const closed = date < today || (date === today && race.deadline && now > race.deadline);
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
