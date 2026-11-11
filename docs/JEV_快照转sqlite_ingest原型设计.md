# JEV 快照 → sqlite：ingest.py 原型设计（待批，未落代码）

> 时间：2026-11-10
> 上游口径：`docs/零界技能开发规范_v1.0.md`、`docs/零界技能规范_本仓差异注记.md`
> 配套骨架：`skills/zeroq-page-skill-template/`（已本地自测 15/15）
> 状态：**设计稿**。本轮未写生产代码、未动 8091 页面与 `/api/jev/graph`、未联网、未连生产服务器。

---

## 一、先纠一个容易走弯的地方

「JEV 快照 → sqlite」**不等于**把 `daily/` 与 `stk_mins/` 的 parquet 灌进 sqlite。
如果照字面搬原始行情，会立刻撞上三堵墙：4GB 量级、pandas/parquet 依赖、以及
`api.py` 的 stdlib only / 30 秒 / 8MB 上限。

规范里 `api.py` 是**只读查询层**，`ingest.py` 才是**取数与计算层**。
真正该落库的是 `jev_graph.build()` 已经算好的**图结果 JSON**：采集/计算留在 ingest 侧（可以用 pandas、
可以读 parquet、可以按需兜底），`api.py` 只做 `SELECT`。这与我们既有的「采集层与渲染层物理隔离」
口径完全一致，也正好绕开用户指出的卡点。

```
现状：  浏览器 --/api/jev/graph--> 8091 常驻 Python --pandas/parquet/xtcloud--> 数据仓
                                      （重计算在请求链路上，60s 缓存兜着）

原型：  ingest.py（离线/Agent run）--jev_graph.build(day)--> 图结果 JSON
                                   --原子写--> skill-data/*.db
        浏览器 --数据桥 ../../api/graph--> api.py（stdlib 只读）--SELECT--> 同一份 JSON
```

---

## 二、本机真实快照盘点（本轮只读实测）

| 类别 | 路径 | 规模 / 结构 |
| --- | --- | --- |
| 日线分片 | `quant_backtest/data/tushare/daily/d_<YYYYMMDD>.parquet` | 目录 940M，最新分片的交易日由文件名列目录取 max（~30ms） |
| 分钟分片 | `quant_backtest/data/tushare/stk_mins/*_<YYYYMM>.parquet` | 目录 3.4G，中证 800 × 近一年 |
| 盘中降级 | `quant_backtest/data/tushare/intraday_board/board_<day>.json` | 1 份 |
| 社交快照 | `portal/social_<YYYYMMDD>.json` | 顶层键：`day` / `codes` / `by_code` / `fetched_at` / `focus_board` / `guba` / `xq` / `errors` |
| 披露与资金 | `quant_backtest/data/tushare/_jev_snap/{disc,flow}_<day>.json` | 按日分片 |
| KG 快照 | `quant_backtest/data/tushare/_jev_kg/` | 48 份 |
| 概念快照 | `quant_backtest/data/tushare/_jev_concept/` | 1 份 |
| 新闻库 | `news_collector/news.db` | SQLite，`jev_graph` 用只读连接 |
| 审计库 | `agent_platform/data/jev_audit/jev_audit.db` | SQLite |

图结果由 `portal/jev_graph.py::build()` 汇总，`portal/api_jev_graph.py` 只做参数校验 + 60s 缓存。
响应含 `universe` / `minute_coverage` / `minute_read` / `boards` / `focus` / `fund` / `network` 等字段。

---

## 三、两档方案，推荐 A 起步

| | 方案 A：整图 JSON 单表（推荐） | 方案 B：结构化拆表 |
| --- | --- | --- |
| 库结构 | `graph_json(day, payload, built_at, source_sig, flags)` | `meta` / `boards` / `nodes` / `edges` / `events` / `social` / `coverage` 多表 |
| `api.py` | `SELECT payload WHERE day=?`，原样返回 | 按端点拼装 SQL，支持按板块/个股过滤 |
| 页面改造 | 仅换 URL 与取数函数，渲染逻辑基本不动 | 渲染层要跟着改字段映射 |
| 口径风险 | **最低**（JSON 原样搬运，不重算、不重排） | 高（每个字段都要与 8091 对齐，易漂移） |
| 工作量 | 小，适合原型 | 大，适合确定要长期做多日演化查询之后 |

**推荐 A**：原型阶段最该验证的是「链路能不能通、口径会不会丢」，不是查询能力。
表结构是技能私有契约，随时可重建；等确有跨日/结构化查询需求再演进到 B，不预先设计。

---

## 四、方案 A 契约（原型）

### 4.1 表

```sql
CREATE TABLE IF NOT EXISTS graph_json (
  day         TEXT PRIMARY KEY,          -- YYYYMMDD，选票日
  payload     TEXT NOT NULL,             -- jev_graph.build 的完整 JSON 字符串
  built_at    TEXT NOT NULL,             -- 观测/构建时刻，动态生成
  source_sig  TEXT NOT NULL,             -- 参与计算的快照文件 mtime+size 摘要
  flags       TEXT NOT NULL              -- 口径旗标，原样带入，不得清洗
);
CREATE INDEX IF NOT EXISTS idx_graph_built ON graph_json(built_at);
```

### 4.2 ingest.py（取数与计算层，可用 pandas）

- 入参 `--day <YYYYMMDD>`，缺省按 `daily/` 分片文件名取 max（沿用 `resolve_day` 口径）。
- 调 `api_jev_graph.compute(day, fund=..., kind=...)` 拿图结果；**不复制** `build()` 的计算逻辑，避免两套口径。
- `flags` 原样保存 P2.3 三旗标：`coverage_only=True` / `gate_eligible=False` / `calibrated=False`；
  社交覆盖率两档分母恒为 `snapshot.codes`，`posts > listed` 合法——**ingest 不得改写、不得补值的字段**。
- 幂等：`INSERT OR REPLACE` by day；同一天重跑覆盖，不追加历史版本。
- 原子写：先写 `<db>.tmp` → `os.replace`；任何异常保留上一版库，**不制造半库、不删旧数据**。
- 库路径：`~/.zerobound/instances/default/skill-data/<companyId>/<skillId>/jev.db`，
  靠 `ZB_COMPANY_ID` / `ZB_SKILL_ID` 与数据桥对齐（复用骨架的 `db_path()`）。

### 4.3 api.py（只读查询层，stdlib only）

| path | 行为 |
| --- | --- |
| `graph` | 查 `graph_json` 最新 day 或 `query.day` 指定日，返回 `payload` 解析后的对象 + `built_at` / `flags` |
| `summary` | 返回库内可用日列表、最近构建时刻、`empty` 标记 |
| `health` | 存活 + 库可读性 |
| 其它 | 404 信封 |

库不存在 / 无当日行 → `{"empty": true, "hint": "Agent 运行 ingest 后可查看"}`，**不现算、不联网**。

### 4.4 耗时与联网边界（重要）

- `api.py` 的 30 秒 / 8MB 是**每请求**上限，ingest 不在此限；但 ingest 必须有自己的超时与失败回滚。
- `jev_graph.build()` 默认会走资金流（~4s）与 xtcloud 远端分钟兜底（`JEV_MINS_BUDGET=12s`）——**会联网**。
  原型阶段强制 `fund=0` 且 `JEV_MINS_FALLBACK=off`，只用本地已有快照，保证「不联网」边界不失守；
  若后续要出完整图，需另行批准联网取数，并在 `source_sig` 里如实标注远端兜底命中情况。
- `minute_read` 三档台账（local / remote / fail）原样带进 payload，不得美化。

**2026-11-10 原型实测订正（重要，本节原假设不足）**：上面「强制 `fund=0` 且
`JEV_MINS_FALLBACK=off`」**不能**保证离线。首版实测仍产生上百次出站连接（被哨兵拦下，
host 解析为 xtcloud 服务 IP；证据 `tmp/jev_ingest_blocked_firstrun.json`），根因三条：

1. `compute()` 默认 `with_chain=True` → `jev_chain.load_chain()` 查三张 xtcloud 表；
2. `build()` 的**批量**分钟兜底 `_warm_many()` → `_xt().mins_many(workers=8)`，
   **不检查 `JEV_MINS_FALLBACK`**（该变量只管单票 `_mins_fetch()`）；
3. 单票兜底与批量兜底是两条独立路径，关一条不关另一条。

原型最终以六条措施收口，见 `skills/jev-graph-sqlite-prototype/references/README.md` 第二节。
离线判据改为以 `source_sig.offline.blocked_outbound == 0` 台账为准，
不以「设了哪些环境变量」为准。

### 4.5 验收判据（原型自检）

1. 本地跑 ingest 后，`api.py graph` 返回的对象与 `/api/jev/graph?day=<同一日>` **同结构**；
   逐项比对顶层键、`boards` 长度、`network.nodes/edges` 长度。
2. `flags` 三旗标与 `minute_read` 台账逐字段相等，无字段被清洗或补默认值。
3. 重复跑 ingest 两次，库内仍只有一行（幂等），`built_at` 更新。
4. 断网/缺快照时 ingest 失败但**旧库仍在**，`api.py` 仍返回上一版数据。
5. `api.py` 单次调用 < 30s、输出 < 8MB、只读连接（`mode=ro`）。
6. 空库与未知端点分别返回 `empty: true` 与 404 信封。

---

## 五、明确不动清单

`8091` 门户页面、`/api/jev/graph` 路由、`portal/api_jev_graph.py`、`portal/jev_graph.py`、
所有 `tools/*_fetch.py` 采集器、P2.3 覆盖率护栏、v2 真相源、测试基线、计划任务。
原型只新增：一个技能包（或复用骨架包）+ 本地自测脚本 + 回执。

---

## 六、待批与前置

- **待批**：是否把方案 A 落成可运行的原型脚本（纯新增、本地跑、不接 8091、不联网）。
- **待老田提供**：零界终端 3100 源码路径 / Git 地址 / 部署主机 / 启动命令，之后才谈页面挂载与分享链接。
- 若只读连 10.205.0.118 核查 3100，见 `tmp/zeroq_3100_只读核查_五要素批准书.md`（**未执行**）。
- 页面改造排在本原型验收之后。**状态更新**：本地部分已完成，见文末「页面改造状态」。

**状态更新（2026-11-10 晚）**：方案 A 已获批准并落地为
`skills/jev-graph-sqlite-prototype/`，本地自测回执见
`tmp/jev_ingest_prototype_selftest.md`。本节「待批」第①项已完成；
②（零界终端位置）与 3100 只读核查仍未解决、仍未执行。

**§4.5 判据 1 状态更新（2026-11-10 23:20）**：原本因「不接 8091」降级的
「线上逐项结构核对」已获批准并补齐 —— 一次 `fund=0` 只读 GET
（`tmp/verify_jev_8091_compare.py` 15/15）+ 离线深度复核
（`tmp/probe_jev_8091_deepdiff.py`）。结构同构成立：顶层键 17/17、
boards 11/11、nodes 69/69、edges 39/39、六个主要子键集合全一致；
内容 26 条差异 100% 归因 `with_chain` / `JEV_MINS_FALLBACK` 两个离线开关，
零数值漂移；请求前后扫描 43121 个易写文件零变更。
局限：8091 默认 `fund=1` 完整档未比对，如需补要另批一次请求。
回执 `tmp/jev_8091_compare.md`、`tmp/jev_8091_deepdiff.md`。

**原型增补（2026-11-10 23:30）**：新增 `ingest_audit` append-only 台账与
`api.py audit` 只读端点，收口「同日 `INSERT OR REPLACE` 覆盖 `source_sig`、
历史输入不可回溯」的缺口（`tmp/verify_jev_audit_ledger.py` 27/27）。
改后自检 32 PASS + 1 SKIP / FAIL=0（判据 3 因上游 `stk_mins` 被并行补数改写而标 SKIP，归因见 `tmp/jev_upstream_churn_20261110.md`），判据 1 离线复验 10/10（payload 逐字节未变、零新请求）。
台账启用前的历史不可回溯，按不造假数据铁律未伪造回填。

**页面改造状态（方案 A 验收后）**：`skills/jev-graph-sqlite-prototype/html/index.html`
已由链路诊断页升级为自包含 JEV 板块联动网查看器，只走 `../../api/{graph,summary,audit}`
数据桥，`api.py` 一行未动；8091 原页因依赖 5 个外部静态资源不能整页搬入 CSP 沙箱，
故按「仅换取数地址」保留核心渲染、重写内联样式与布局。
静态自测 `tmp/verify_jev_page.py` 28/28（含 `node --check`、frontmatter 精确判定与 95 条字段路径可达），
运行时渲染 `tmp/verify_jev_page_runtime.py` 23/23（Node + DOM stub 真跑页面 JS，
SVG 节点 / 连线数与 payload 精确一致）。
发现并如实披露 `cross` 边端点不在 `network.nodes` 的数据形态（见
`tmp/jev_page_edge_gap.md`），不伪造节点、不补值。
**真正挂载到零界终端页面路由仍需 3100 源码 / Git / 主机 / 启动命令四选一，未执行。**
