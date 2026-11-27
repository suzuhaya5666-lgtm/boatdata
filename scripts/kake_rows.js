// 予選最終日の各レースで、勝負駆けの条件(何着以内なら18位以内。0=1着でも届かない)をサイトと同じ関数で出す。scripts/kake_check.py から使う
//   node scripts/kake_rows.js 出力.json
const fs = require("fs"), path = require("path");
const ROOT = path.join(__dirname, "..");
const OUT = process.argv[2] || "/tmp/kake_rows.json";
const MEMO = {};
const J0 = (p) => { if (p in MEMO) return MEMO[p]; let x = null; try { x = JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8")); } catch (e) {} return (MEMO[p] = x); };
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
function pick(name) {
  let i = html.indexOf(`\nfunction ${name}(`);
  if (i < 0) i = html.indexOf(`\nasync function ${name}(`);
  if (i < 0) i = html.indexOf(`\nconst ${name} =`);
  if (i < 0) throw new Error(name);
  const start = i + 1, first = html.slice(start, html.indexOf("\n", start)).trimEnd();
  if (!/[{\[(]$/.test(first)) return first;
  const e = html.indexOf("\n}", start);
  return html.slice(start, html.indexOf("\n", e + 1));
}
const NAMES = ["KAKE_ADJ", "PTS_TBL", "ptsKind", "wCmp", "addDay", "dayPath", "isPrelimDay", "prelimRaces", "raceOutcome", "seriesPoints", "borderOthers", "statusText", "isPrelimFinal", "kakeConds"];
var state = {}, cache = {}, store = { get: () => null };
eval(NAMES.map(pick).join("\n").replace(/^const /gm, "var ").replace('for (let k = 6; k >= 1; k--) if ((a.pts + P[k - 1]) / (a.n + 1) >= b - 1e-9) { out[e.boat] = k; break; }', 'out[e.boat] = 0; for (let k = 6; k >= 1; k--) if ((a.pts + P[k - 1]) / (a.n + 1) >= b - 1e-9) { out[e.boat] = k; break; } out["r" + e.boat] = (a.rate == null ? 99 : sp.list.filter((x) => x !== a && !x.excl && x.n && x.rate > a.rate + 1e-9).length + 1);'));
var CUT = null; // {date, no}: この日のこのレース以降は結果を消す
const strip = (x, d) => {
  if (!x || !CUT || d !== CUT.date) return x;
  const y = JSON.parse(JSON.stringify(x));
  for (const v of Object.values(y.venues)) for (const r of Object.values(v.races)) if (Number(r.no) >= CUT.no) { r.entries.forEach((e) => { e.place = null; e.status = null; }); delete r.payouts; }
  return y;
};
const DAYS = {};
var cached = async (p) => J0(p), dayFile = async (d) => strip(J0(dayPath(d)), d), liveRes = async () => null;
var nowJST = () => ({ date: "2099-01-01", time: "00:00" });
(async () => {
  const files = fs.readdirSync(ROOT + "/data/races").flatMap((y) => fs.readdirSync(`${ROOT}/data/races/${y}`).map((f) => `${y}/${f}`)).sort();
  const out = [];
  for (const f of files) {
    const date = `${f.slice(5, 9)}-${f.slice(9, 11)}-${f.slice(11, 13)}`;
    const nd = J0(dayPath(addDay(date, 1)));
    if (!nd) continue;
    const d = J0(dayPath(date));
    for (const [code, v] of Object.entries(d.venues)) {
      const nv = nd.venues[code];
      if (!nv || nv.title !== v.title) continue;
      const semi = Object.values(nv.races).filter((r) => /準優勝戦/.test(r.name || ""));
      if (semi.length !== 3) continue;
      // 節の初日からそろっているか
      let ok = true; for (let k = 1; k < (v.day || 1); k++) { const x = J0(dayPath(addDay(date, -k)))?.venues?.[code]; if (!x || x.title !== v.title) ok = false; }
      if (!ok) continue;
      const S = new Set(semi.flatMap((r) => r.entries.map((e) => e.regno)));
      for (const r of Object.values(v.races).sort((a, b) => a.no - b.no)) {
        CUT = { date, no: Number(r.no) }; for (const k in cache) delete cache[k];
        const conds = await kakeConds(date, code, r);
        if (!Object.keys(conds).length) continue;
        out.push({ date, code, no: Number(r.no), name: r.name, conds,
          ents: r.entries.map((e) => ({ boat: e.boat, course: e.course, place: e.place, status: e.status, regno: e.regno, semi: S.has(e.regno) })) });
      }
    }
    CUT = null;
  }
  fs.writeFileSync(OUT, JSON.stringify({ rules: KAKE_ADJ, rows: out }));
  console.log("races", out.length, "racers", out.reduce((s, o) => s + Object.keys(o.conds).length, 0));
})();
