# 方案 A 原型 · 契约速查、离线边界与自测

对应 `docs/JEV_快照转sqlite_ingest原型设计.md`；规范整理版 `docs/零界技能开发规范_v1.0.md`。
**本包只做本地自测**：未导入租户、未挂 Agent、未注册服务或计划任务。成图与数据桥全程离线；
另经批准做过**一次** 8091 只读 GET 用于判据 1 结构核对（见第七节），未改 8091 任何代码或配置。

## 一、契约对照

| 规范条目 | 本包实现 |
| --- | --- |
| 双轴推导 | `scripts/` 下有脚本 → `scripts_executables`；`page: html/index.html` 且文件在包内 → `pageEntry` 非空 |
| frontmatter | `name` / `description`（40–300 字符）/ `page` / `api` / `tags`；路径无 `..`、无绝对路径 |
| 页面自包含 | 内联 CSS + JS，无 CDN、无外链资源、无顶层导航/弹窗/表单 |
| 数据获取 | `fetch("../../api/graph", { credentials: "include" })` 命中数据桥 |
| api.py | stdlib only；stdin 收 `{path, method, query, body, companyId, skillId}`；stdout 出 JSON 或 404 信封；只读 `mode=ro, uri=True` |
| ingest.py | 可用 pandas；写 `~/.zerobound/instances/default/skill-data/<companyId>/<skillId>/jev.db` |
| 包目录只读 | 运行时数据只写公司隔离目录，不写包内 |
| 空数据 | 库不存在 / 无行返回 `empty: true` + `hint`，不抛异常、不现算 |

## 二、离线六保险（设计稿只列了两条）

设计稿 §4.4 假设 `fund=0` + `JEV_MINS_FALLBACK=off` 就够。**首版实测跑出上百次出站连接**
（被哨兵拦下，host 解析为 xtcloud 服务 IP；证据存档
`tmp/jev_ingest_blocked_firstrun.json`），逐个定位后补到六条：

| # | 措施 | 关掉的链路 |
| --- | --- | --- |
| 1 | `compute(..., fund=False)` | 资金流 + 研报/公告（8091 的 `fund=0` 档） |
| 2 | `JEV_MINS_FALLBACK=off` | xtcloud **单票**分钟远端兜底 `_mins_fetch()` |
| 3 | `JEV_MINS_WRITE=0` | 分钟层回写 parquet |
| 4 | 进程内 `jev_graph.build = partial(..., with_chain=False)` | 连板/席位/龙虎榜 xtcloud 三连请求（`compute()` 默认 `with_chain=True`） |
| 5 | 进程内 `G._warm_many = lambda ...: {}` | `build()` **批量**分钟兜底 `_xt().mins_many(workers=8)`；该路径**不检查 `JEV_MINS_FALLBACK`**，是隐藏最深的联网点 |
| 6 | 出站 socket 哨兵（非回环 connect 拒绝并计数） | 任何漏网的出站连接，计数写进 `source_sig.offline` |

第 4/5 条只在本进程内替换模块属性，**不写文件、不跨进程、不影响 8091 常驻服务**。
第 5 条选择置空「批量远端兜底」而不是 `with_minute=False`，是为了**保留 `_mins_local()` 本地分钟读取** ——
`with_minute=False` 会把本地快照一起丢掉，反而违背「只用本地已有快照」的口径。

`chain` 关闭后 `build()` 初始化值就是 `{"has": False, "note": ...}`。
**订正（8091 实测）**：`chain` 是**顶层键**，`focus` 下并没有 chain；线上 `with_chain`
默认开时顶层 `chain` 有 11 个子键，原型只有 `has` / `note`。判据 1 比的是顶层键集合，
chain 两边都存在故不判失败；内部子键不同属已知档位差异，不得当作口径漂移。

**判据口径**：离线与否以 `source_sig.offline.blocked_outbound == 0` 台账为准，
不以「设了哪些环境变量」为准。首版正是在只有前四条时靠这条台账抓出来的。

## 三、跑法（Windows / Python 3.11）

`ingest.py` 必须带 pandas，用本机固定解释器；缺省 day 沿用 `jev_graph.resolve_day()`
（按 `daily/` 分片文件名取 max，不写死日历日期）。

```bash
cd /c/aios/skills/jev-graph-sqlite-prototype
ZB_COMPANY_ID=local-selftest \
ZB_SKILL_ID=jev-graph-sqlite-prototype \
  "C:/Program Files/Python311/python.exe" scripts/ingest.py

echo '{"path":"summary","companyId":"local-selftest","skillId":"jev-graph-sqlite-prototype"}' \
  | "C:/Program Files/Python311/python.exe" scripts/api.py
```

查审计台账（append-only，见第八节）：

```bash
echo '{"path":"audit","companyId":"local-selftest","skillId":"jev-graph-sqlite-prototype"}' \
  | "C:/Program Files/Python311/python.exe" scripts/api.py
```

指定选票日加 `--day <YYYYMMDD>`；`--timeout-s` 缺省 120 秒，超时或异常都不写库、旧库原样保留。

一键自检（含六条判据、回执落盘）：

```bash
cd /c/aios && "C:/Program Files/Python311/python.exe" tmp/verify_jev_ingest_prototype.py
```

## 四、验收判据（设计稿 §4.5）

| # | 判据 | 本轮实现口径 |
| --- | --- | --- |
| 1 | 与 `/api/jev/graph?day=<同一日>` 同结构 | **已补齐**：`tmp/verify_jev_8091_compare.py` 一次只读 GET（15/15）+ `tmp/probe_jev_8091_deepdiff.py` 离线深度复核。顶层键 17/17、boards 11/11、nodes 69/69、edges 39/39、六个主要子键集合全一致；内容 26 条差异 100% 归因两个离线开关。本地自检另保留 `compute()` 直出 → JSON → sqlite → api 的 SHA256 端到端链路（最新 32 PASS + 1 SKIP、FAIL=0，见第八节之二），两者互补。见第七节。 |
| 2 | 三旗标与 `minute_read` 台账逐字段相等 | ingest 回执 vs api 返回 `_bridge.flags` / payload `minute_read` 双向比对 |
| 3 | 重复跑两次仍只有一行、`built_at` 更新 | 同一 day `INSERT OR REPLACE`；`built_at` 用微秒精度；另加「同日两次 payload SHA256 一致」 |
| 4 | 断网/缺快照失败但旧库仍在 | 用参数级失败（非法 day）注入，验证 `rc != 0` 且 api 仍返回上一版数据 |
| 5 | api 单次 < 30s、输出 < 8MB、只读连接 | 自检实测耗时与字节数，源码核对 `mode=ro` |
| 6 | 空库 `empty: true`、未知端点 404 信封 | 用不存在的 companyId 造空库 |

## 四之二、source_sig 覆盖清单与追加验证

**参与成图的快照（全部进 `source_sig`，只 stat 不读内容；缺失记 `absent: True`，不补值）**：

| 类别 | 路径 |
| --- | --- |
| 日线分片 | `tushare/daily/d_<day>.parquet` |
| 披露 / 资金 | `tushare/_jev_snap/{disc,flow}_<day>.json` |
| 盘中降级 | `tushare/intraday_board/board_<day>.json` |
| **题材快照** | `tushare/_jev_concept/<day>.json` ← 首版漏算，已补 |
| 社交快照 | `portal/social_<day>.json` |
| 分钟分片 | `tushare/stk_mins/*_<YYYYMM>.parquet` |
| **基础表** | `tushare/stock_basic/*.parquet`（52 个分片，决定行业板块划分）← 首版漏算，已补 |
| 新闻库 / 审计库 | `news_collector/news.db`、`agent_platform/data/jev_audit/jev_audit.db` |

`_theme_mod().strength()` 在 `build()` 里总会调用，题材快照**文件缺席同样参与决策**
（决定 `theme.has=false`），所以缺席也必须记账。`_jev_kg/` 的 48 份快照属于
`jev_kg.py` / `api_jev_kg.py` 另一个接口，`build()` 不读，故**不纳入**。

追加验证（命令与回执）：

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 原型自检（含 source_sig 覆盖、缺席记账） | `python tmp/verify_jev_ingest_prototype.py` | 32 PASS + 1 SKIP / FAIL=0；判据 3 因上游变动标 SKIP，见第八节之二 |
| 跨日累积 | `python tmp/verify_jev_multiday.py` | 14/14，两行并存、旧行未被覆盖、历史日可读 |
| 同日重跑可复现性 | `python tmp/probe_jev_reproducibility.py` | `REPRODUCIBLE`，两次 `source_sig` 与 payload 全一致、零变更文件 |
| 8091 一次只读比对 | `python tmp/verify_jev_8091_compare.py` | 15/15，结构同构成立、零写副作用 |
| 8091 离线深度复核 | `python tmp/probe_jev_8091_deepdiff.py` | 结构 11/12、归因闭合 `true`、零数值漂移 |
| 台账 append-only | `python tmp/verify_jev_audit_ledger.py` | 27/27，追加不覆盖、历史行逐字段不变 |
| 台账改动后判据 1 复验 | `python tmp/verify_jev_8091_after_change.py` | 10/10，payload SHA256 与首轮逐字节一致、零新请求 |

回执：`tmp/jev_ingest_prototype_selftest.md/.json`、`tmp/jev_multiday_selftest.md/.json`、
`tmp/jev_reproducibility.md/.json`、`tmp/jev_8091_compare.md/.json`、
`tmp/jev_8091_deepdiff.md/.json`、`tmp/jev_audit_ledger_selftest.md/.json`、
`tmp/jev_8091_after_change.md/.json`。

**口径陷阱（实测踩过，必须记住）**：`payloadBytes` 是 **UTF-8 字节数**
（`len(text.encode("utf-8"))`），而 SQL `length(payload)` 返回的是**字符数**。
本例 `52390` 字节 vs `47230` 字符，差 `5160` 全来自中文多字节。曾据此把同一次数据
误判成「同日漂移」，复核后证伪。判体积一律用字节数，不要用 SQL `length()`。

**审计缺口已收口**：新增 `ingest_audit` append-only 台账（见第八节）。
同一 day 的 `graph_json` 仍 `INSERT OR REPLACE`，但每次 ingest 的输入摘要逐次留痕，
历史行按原 seq 全量搬回、逐字段不可变。台账启用前的历次 ingest 无审计行、
不可回溯，按不造假数据铁律未伪造回填。

## 五、与 8091 线上结果的已知差异（不得隐瞒）

1. `fund=False` → `fund.has=false`、`focus.flow`/`disclosures` 为空；结构键仍在。
2. `with_chain=False` → **顶层** `chain.has=false`，梯队/席位/龙虎榜为空；
   `network.nodes` 中 `list_net` / `nums` / `relay_style` / `top_buyer` 为 `None`
   （线上有值时为 float/int/str）。`chain` **不是** `focus` 的子键。
3. `JEV_MINS_FALLBACK=off` → `minute_read.fallback` 与 `minute_coverage.fallback` 为 `off`；
   本次 8091 实测 `remote=0` / `fail=60`，即线上兜底虽开但未命中任何一票。
4. 8091 有 60s 请求级缓存；本原型每次 ingest 重新成图，以 `built_at` 与 `source_sig` 记账。
5. 线上 `api_jev_graph.handle_get()._json` 会做 NaN/Inf → null 清洗；本原型 `san()` 与之逐行等价，
   这是**序列化层既有行为**，不是对业务口径字段的改写。

## 六、下一步（不在本轮范围）

- 页面改造（**本地部分已完成，见第九节**）：`html/index.html` 已升级为自包含板块联动网查看器，仅换取数地址；静态 28/28 + 运行时 23/23。**真正挂载到终端页面路由仍需 3100 可用**。
- 出完整图：放开 `fund` / `with_chain` / 分钟兜底会联网，须另批，并在 `source_sig` 如实标注远端命中。
- 8091 默认 `fund=1` 完整档的结构核对未做，如需补要另批一次只读请求。
- 方案 B（拆 boards / nodes / edges 多表）：等确有跨日结构化查询需求再演进，不预先设计。
- 零界终端源码路径 / Git 地址 / 部署主机 / 启动命令四选一仍待老田提供；
  只读连 10.205.0.118 核查见 `tmp/zeroq_3100_只读核查_五要素批准书.md`（**未执行**）。

## 七、8091 只读比对（判据 1 已补齐，2026-11-10）

经批准做过**一次** `http://127.0.0.1:8091/api/jev/graph?day=20261110&fund=0`。
带 `fund=0` 与原型 `fund=False` 对齐，跳过资金流联网与 `_jev_snap` 快照写；
失败不重试、不绕过。

| 项 | 结果 |
| --- | --- |
| 一次比对 `tmp/verify_jev_8091_compare.py` | 15/15，HTTP 200 / 65164 B / 38.231 s |
| 只读实证 | 请求前后扫描 43121 个易写文件，新增 / 删除 / 改动均为 0 |
| 结构判据（离线独立重算） | 11/12；顶层键 17/17、boards 11/11、nodes 69/69、edges 39/39、六个主要子键集合全一致 |
| 唯一未过的扩展项 | `chain` 子键集合：线上 11 个 vs 原型 2 个，纯 `with_chain` 档位差异 |
| 内容差异 | 26 条 = `chain` 11 + `network` 13 + 两处 `fallback` 2；三类之外 0，数值差异 0 |

`network` 的 13 条全是 `type` 差异，原型侧一律 `NoneType`，字段为
`list_net` / `nums` / `relay_style` / `top_buyer` —— 均为连板/席位/龙虎榜派生字段。
**归因闭合 = true**：共同拥有的数据维度上，线上与原型数值完全一致，
未发现字段清洗、补默认值或口径漂移。

首轮回执把归因证据误写成 `focus.chain`（两边皆空），已由
`tmp/probe_jev_8091_deepdiff.py` 订正：`chain` 是顶层键。因只批了一次 GET，
**未重发请求**，以离线复核留痕。

证据原文：`tmp/jev_8091_online_response.json`（线上）、
`tmp/jev_8091_local_response.json`（原型）；
逐字段明细 `tmp/jev_8091_deepdiff_rows.json`。

**局限**：本次是 `fund=0` 档；8091 默认 `fund=1` 完整档未比对。线上单次请求
38.231 s，重计算仍在请求链路上 —— 这正是把计算挪到 ingest 侧的原始动机。

## 八、append-only 审计台账（历史输入可回溯）

```sql
ingest_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, day, built_at,
             payload_sha, source_sig_sha, n_files, blocked_outbound,
             offline, flags)
```

**只 INSERT、永不 UPDATE / DELETE**。`graph_json` 仍按 day 幂等覆盖（一日一行），
两者解耦。`api.py` 新增只读 `audit` 端点（支持 `query.day`），只返回摘要 sha，
不下发 `source_sig.files` 的本机绝对路径明细。

原子写每次重建临时库，旧台账行按**原 seq** 全量搬回再追加本次行；台账读取失败
直接抛错、不写库，**宁可本次失败也不丢审计历史**。失败注入（非法 day）
不追加台账行。`tmp/verify_jev_audit_ledger.py` 27/27 全绿（追加不覆盖、
历史行逐字段不变、seq 连续、三旗标与六保险档位原样记录）。

**已知边界**：台账启用前的历次 ingest 无审计行、不可回溯，按不造假数据铁律
**未伪造回填**；可追溯性自本次启用起算。

## 八之二、上游变动与判据 3 修正（2026-11-10 23:34）

文档订正后重跑自检曾出现**一次真实红**：判据 3「同日重跑 payload 一致」失败。
台账 `ingest_audit` 直接给出根因 —— seq 1–4 的 `source_sig_sha` 与 payload 全一致
（`n_files=1159`），seq 5/6 变成 473 / 485 且 sig 不同、payload 不同。
外部只读核查：`stk_mins/*_202611.parquet` 当前 575 个，最近 600 秒改写 276 个，
最新 mtime 23:36:02 仍在持续，符合并行分钟补数逐票原子替换的形态。
按互斥协议未干预、未删、未协调停工。

修正：判据 3 从「任意两次必相同」改为**「相同输入必得相同输出」** ——
`source_sig.sha256` 相同才断言 payload 一致；输入变了标 **SKIP**，不假装 PASS，
逐次输入由台账留痕。`ALL_PASS` = `FAIL 0` 且 `PASS = 合计 - SKIP`。
补丁 `tmp/patch_selftest_j3.py`（9 锚点、逐一断言唯一、写后编译复核）。
重跑 `pass=32 / skip=1 / fail=0 / total=33`。同输入一致性另有
`tmp/probe_jev_reproducibility.py` 专门验证。

**这轮红恰好实证了台账的价值**：没有逐次输入留痕，只能靠猜。
回执 `tmp/jev_upstream_churn_20261110.md`。

## 九、页面改造（本地部分，2026-11-10）

`html/index.html` 从链路诊断页升级为 **JEV 板块联动网查看器**：选日下拉（`summary.days`）、
KPI、板块强度榜、联动网 SVG（分环确定性布局）、焦点板块、跨板块传播、分钟三档台账、
社交覆盖、口径来源、审计台账。全部内联 CSS/JS，无 CDN、无外链、无表单 / 弹窗 / 顶层导航；
只走 `../../api/{graph,summary,audit}`，**页面不重算、不联网、不补值**。
涨红跌绿按中国口径；三旗标与「不得进判定闸 A/B」常驻免责带。

8091 原页 893 行但依赖 5 个外部静态资源（主题 CSS / 导航 JS），按规范 CSP 自包含
要求**不能整页搬**，故按方案 A「仅换取数地址」保留核心渲染、重写样式与布局。

| 验证 | 脚本 | 结果 |
| --- | --- | --- |
| 静态契约 | `tmp/verify_jev_page.py` | **28/28**：CSP 自包含、零手写日历日期、`node --check` 真编译、三旗标、涨红跌绿、CONTRACT 95 条字段路径全可达 |
| 运行时渲染 | `tmp/verify_jev_page_runtime.py` + `tmp/jev_page_runtime_driver.js` | **23/23**：Node `vm` + DOM stub 真跑页面 JS，喂真实 `api.py` 响应；SVG 圆点 / 连线数、板块行数与 payload 精确一致；选日切换后仍渲染 |

**已知边缺口**：`cross` 边的端点是股票代码，常不在 `network.nodes` 内（实测 39 条边中
24 条）。页面不伪造节点、不补值，标题栏如实披露「已绘 X · 端点缺失未绘 Y」，
跨板块信息由 `cross_summary` 面板呈现。详见 `tmp/jev_page_edge_gap.md`。

**边界**：`api.py` 一行未动（方案 A 只换取数地址）；`portal/` 生产代码未改；
未导入租户、未挂 Agent、未请求 8091、未联网、未删文件。
`file://` 直开时无数据桥路由，fetch 失败属预期。
完整挂载与分享链接仍卡在零界终端 3100 的位置信息。
