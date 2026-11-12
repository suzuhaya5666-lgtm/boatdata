// その日の5場の全レースについて、各艇の「予想の着順」を出す(節間成績=予想との差 に使う)。
//   node scripts/expect.js YYYYMMDD
// data/expect/YYYYMMDD.json に {"07_01": {"1": 2.31, ...}, ...} を書く。
// 予想は model.py の1段目と同じく「節間成績なし」の組み合わせ(weights.json の setsu_base_mask)。
// 進入は実際のコース(結果) → スタート展示 → 枠なり の順。
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..");
const J = (p) => { try { return JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8")); } catch (e) { return null; } };

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
var state = {};
eval(["ZERO", "CLS_NUM", "demeQ", "demeAdjust", "featGroups", "defaultMask", "raceKind", "biasFor", "demeTri"].map(pick).join("\n").replace(/^const /gm, "var "));

const day = process.argv[2];
const dayData = J(`data/races/${day.slice(0, 4)}/${day}.json`);
const model = J("data/model/weights.json"), vs = J("data/stats/venues.json");
if (!dayData || !model || !vs) process.exit(0);
state.day = dayData;
state.featMask = model.setsu_base_mask ?? null;
// 過去の直前情報(before_past、gzip)
const zlib = require("zlib");
let _bp;
const bpast = () => {
  if (_bp === undefined) {
    try { _bp = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(ROOT, `data/before_past/${day.slice(0, 4)}/${day}.json.gz`))).toString()); } catch (e) { _bp = null; }
  }
  return _bp;
};
const out = {};
for (const code of ["07", "08", "11", "12", "19"]) {
  const v = dayData.venues?.[code];
  if (!v || !vs.venues?.[code]) continue;
  for (const race of Object.values(v.races)) {
    if (!race.entries?.length) continue;
    const no = String(race.no).padStart(2, "0");
    const bi = J(`data/before/${day}/${code}_${no}.json`) || bpast()?.[`${code}_${no}`] || null;
    let order = [0, 1, 2, 3, 4, 5, 6];
    const byC = {};
    race.entries.forEach((e) => { if (e.course >= 1 && e.course <= 6) byC[e.course] = e.boat; });
    if (Object.keys(byC).length === 6) order = [0, 1, 2, 3, 4, 5, 6].map((c) => (c ? byC[c] : 0));
    else if (bi?.start?.length === 6) order = [0, ...bi.start.map((s) => s.boat)];
    const racers = Object.fromEntries(race.entries.map((e) => [e.boat, J(`data/stats/racers/${e.regno}.json`)]));
    const ents = Object.fromEntries(race.entries.map((e) => [e.boat, e]));
    try {
      const adj = demeAdjust(demeQ(order, racers, vs.venues[code].all, model), order, ents, racers, bi, model, { ...race, _v: code });
      const m = {};
      for (let k = 1; k <= 6; k++) m[k] = [0, 0, 0];
      demeTri(adj.q).forEach((t) => t.c.forEach((k, j) => { m[k][j] += t.p; }));
      const r = {};
      for (let k = 1; k <= 6; k++) {
        const [a, b, c] = m[k];
        r[order[k]] = Math.round((a + 2 * b + 3 * c + 5 * (1 - a - b - c)) * 1000) / 1000;
      }
      out[`${code}_${no}`] = r;
    } catch (e) { /* そのレースは飛ばす */ }
  }
}
fs.mkdirSync(path.join(ROOT, "data/expect"), { recursive: true });
fs.writeFileSync(path.join(ROOT, `data/expect/${day}.json`), JSON.stringify(out));
console.log(`予想の着順 ${day}: ${Object.keys(out).length}レース`);
