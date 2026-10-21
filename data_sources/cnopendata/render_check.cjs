// 最小 DOM stub，真执行档案页的 render()，断言别名徽标真渲染出来。
// 目的：只 grep 字符串会放过「变量未声明必崩」这类缺陷，必须让代码真跑。
const fs = require("fs");
const path = process.argv[2] || "档案总览.html";
const html = fs.readFileSync(path, "utf8");
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const big = scripts.sort((a, b) => b.length - a.length)[0];

const store = {};
const mk = id => ({ id, value: "", innerHTML: "", textContent: "", addEventListener() {} });
global.document = {
  getElementById(id) { store[id] = store[id] || mk(id); return store[id]; },
  addEventListener() {},
};
global.window = global;

eval(big);

const rows = store["rows"].innerHTML;
const trCount = (rows.match(/<tr>/g) || []).length;
const badge = (rows.match(/别名 → /g) || []).length;
const freshBadge = (rows.match(/至今存疑 · 止期距今/g) || []).length;
const freqBadge = (rows.match(/频率口径待确认<\/span>/g) || []).length;

console.log("渲染 <tr> 行数 =", trCount);
console.log("别名徽标出现 =", badge);
console.log("新鲜度存疑徽标出现 =", freshBadge);
console.log("频率口径待确认徽标出现 =", freqBadge);
console.log("count 文案 =", JSON.stringify(store["count"].textContent));
console.log("cards 卡片数 =", (store["cards"].innerHTML.match(/class="card"/g) || []).length);

// 独立口径：拿 registry.json（真源）与页面渲染结果比对，
// 而不是用页面自带的 DATA 变量自证——那样等于自己证明自己。
const reg = JSON.parse(fs.readFileSync("registry.json", "utf8"));
const dataAlias = reg.datasets.filter(d => d.alias_of).length;
const dataStale = reg.datasets.filter(d => d.freshness_flag === "stale").length;
const dataFreq = reg.datasets.filter(d => d.freq_conflict === "频率低频但区间标实时").length;
const ok = trCount === reg.datasets.length && badge === dataAlias && dataAlias > 0
  && freshBadge === dataStale && freqBadge === dataFreq
  && store["count"].textContent === "显示 " + reg.datasets.length + " / " + reg.datasets.length + " 条";
console.log(`真源 registry：${reg.datasets.length} 条 / 别名 ${dataAlias} 条 / 至今存疑 ${dataStale} 条 / 频率口径待确认 ${dataFreq} 条`);
console.log(ok ? "PASS：渲染行数/别名徽标/存疑徽标与真源一致" : "FAIL：渲染结果与真源不一致");
process.exit(ok ? 0 : 1);
