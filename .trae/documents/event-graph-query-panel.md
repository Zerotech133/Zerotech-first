# 事件图谱查询面板（/kg/events）实施计划

## Context

K266 新闻事件已入图 14 条 `DOC_EVENT` 边（Security 之间，属性 `date/src_doc/ts_last/n/amount/rel/dir`），但门户缺少直观的查询面板。用户要求做"图谱查询面板"，核心诉求：
- UI 人性化、**支持下钻查看逻辑**：点公司 → 看事件 → 看原文
- 数据按市场分类独立展示（当前图内只有 A 股，按主板/创业板/科创板/北交所分，预留美股/港股/加密扩展位）
- 态势感知：连接状态、抓取时间、更新间隔、下次更新

已验证数据链路（无需再探索）：
- `DOC_EVENT.src_doc` 是**条级** doc_id（如 `d19b9f5a6fcfai07`），去末尾 `iNN` 后缀得**文档级** doc_id（`d19b9f5a6fcfa`）→ 14/14 命中 `doc_texts.jsonl` 原文 `text`
- `structure_shadow.jsonl`（4091 条）提供结构化摘要：`title_guess/direction_why/key_numbers/tags/events`
- 门户渲染模式：Handler 类服务端拼接 HTML（无 jinja2），`self._html(body)` + `self._KG_CSS` + `pill_nav(page_key)`；KG 路由集中注册 L21074-21112；导航菜单 L689-698；静态 G6 已本地托管 `/static/g6.min.js`

## 实施步骤

### 1. 新建 `quant_backtest/kg_events_portal.py`（纯查询模块，仿 kg_portal.py）

模块级懒加载 `KgClient(retry=1)`，`api(kind, arg, days)` 分发 + CLI 自测分支。API：

- `situ()` → `{ok, neo4j_up, doc_edges, shadow_mtime, texts_mtime, latest_event_ts, markets:{主板:n,…}, srcs:{…}}`
  - Neo4j 异常 → `{ok:False, error}` 快失败；字段级降级置 null
  - 影子文件 mtime 直读 `data/k266_docs/` 两个 jsonl
  - 每条目独立 try/except
- `graph(market, q, direction, rel, d0, d1, limit=200)` → `{nodes:[{id, data:{name, code, market}}], edges:[{id:"s>>t:"+rel, source, target, data:{rel, dir, n, date, amount, src_doc, ts_last}}]}`
  - Cypher 过滤：a.market / r.dir / r.rel / r.date 区间 / name 或 aliases CONTAINS q
  - G6 v5 数据格式（data 内嵌），仿 portal L19688
- `list(code, market, direction, rel, d0, d1)` → 事件行 `{subject:{name,code}, object:{…}, rel, dir, date, amount, n, src_doc, ts_last}`（点公司下钻与页面列表共用）
- `doc(doc_id)` → 原文：条级 id `re.sub(r'i\d+$','')` 取文档级 → 直读 doc_texts.jsonl（进程内 dict 缓存按 mtime 失效）取 `text/name/src/ts`；structure 字段按条级 id 精确查 structure_shadow.jsonl，miss 按文档级前缀兜底
- `search(q)` → Security 按 name/aliases/symbol/ts_code 模糊联想

### 2. 改动 `portal/portal_api.py`

- **导航**：L689-698「知识图谱」组加 `("📡 事件图谱", "/kg/events", "kgevents")`
- **PAGE_SECTION**：L731 前加 `"kgevents": "intel"`
- **路由**：L21112 `/kg/lexicon` 分支后加 `elif path == "/kg/events": self._kg_events_page(qs)`；随后加 `/kg/events/situ|graph|list|search|doc/<doc_id>` 的 JSON 分发分支（import 模式仿 L21175-21179）
- **新方法 `_kg_events_page(self, qs)`**（置于 `_kg_doc_page` 附近 L19972 一带），三段式服务端渲染：
  - ① **态势感知条**（5 格）：Neo4j 连接（绿/红）、入图事件边数、影子文件最后更新 mtime、最新事件时间（`max ts_last`）+ 距今时长、更新节奏（标注"手动批触发 k266_m3a_events.py"，>48h 无新事件黄条提示）
  - ② **市场 Tab**：全部 / 主板 / 创业板 / 科创板 / 北交所（按 `Security.market` 过滤）；美股 / 港股 / 加密置灰"未接入"（扩展位），GET `?market=` 回填
  - ③ **查询区 + 图谱 + 列表 + 弹层**：公司搜索（联想）、方向 pill（利好/中性/利空）、rel 下拉、日期区间；`<div id=evg>` 图容器（内联 `const INIT=...` + G6 初始化）、`<div id=evlist>` 事件列表（服务端首渲 + fetch 刷新）、`<div id=modal>` 原文弹层
  - 用 `body.replace("{css}", self._KG_CSS).replace("{nav}", pill_nav("kgevents"))` 后 `self._html(body)`；引 `/static/g6.min.js`

### 3. G6 交互（仿 portal L19659-19716 现有 v5 用法）

- 节点配色按 market：主板绿 / 创业板蓝 / 科创紫 / 北交橙 / 未分类灰；边按 dir：利好绿 / 利空红 / 中性灰，线宽 `4 + 2·log2(n)`
- `node:click` → fetch `list?c=code` 填充事件列表，附跳转链接 `图谱全景 /kg?c=…` 与 `K线研判 /bt/stock?c=…`
- `edge:click` → 从边 id `s>>t:rel` 取 src_doc → fetch `doc/<doc_id>` 弹层渲染原文（`white-space:pre-wrap`）+ 结构化字段（title_guess / direction_why / key_numbers / tags）

### 4. 验证

1. CLI：`python quant_backtest/kg_events_portal.py situ`（及 graph/list/doc/search 各分支）
2. 起 portal 后 curl 各端点：`/kg/events`（200 含 INIT）、`/kg/events/situ`、`/kg/events/graph?market=主板&dir=利好`、`/kg/events/list?c=<code>`、`/kg/events/doc/<doc_id>`（命中原文）、`/kg/events/search?q=奥普`
3. 浏览器：导航菜单出现"事件图谱"、态势条 5 格状态正确、市场 Tab 过滤生效、点边弹原文、点节点下钻事件列表并可跳 `图谱全景`/`K线研判`、切板块后图配色一致

## 复用清单

- `KgClient`（quant_backtest/kg_client.py）：Neo4j 会话
- `kg_portal.py`：纯查询模块 + `api()` 分发模式
- portal 内：`self._html`、`self._KG_CSS`、`pill_nav`、`_PILL/_PILL_ON`、tokens.css、`/static/g6.min.js`、G6 v5 事件绑定示例（L19659-19716）
- `k266_m3a_events.py`：src_doc 入图口径（docs[0] 条级 doc_id，L142）
