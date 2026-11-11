---
name: jev-graph-sqlite-prototype
description: JEV 板块联动图快照转 SQLite 的离线原型技能。ingest.py 复用门户已有的图计算函数生成整图 JSON 并原子写入公司隔离库，api.py 以标准库只读查询该库供页面取数；自带板块联动网查看器与 append-only 审计台账。成图全程不联网、不导入租户；已另做一次 8091 只读比对。产物恒为未校准快照，不得进入判定闸。
page: html/index.html
api: scripts/api.py
tags:
  - 原型
  - JEV
  - 数据桥
---

# JEV 图快照 → SQLite（方案 A 原型）

对应设计稿 `docs/JEV_快照转sqlite_ingest原型设计.md` 的**方案 A：整图 JSON 单表**。
对应规范 `docs/零界技能开发规范_v1.0.md`：安全轴 `scripts_executables`，展示轴 `pageEntry = html/index.html`。

## 为什么不是把行情灌进库

`api.py` 限定 stdlib only / 30 秒 / 8MB，而 `/api/jev/graph` 背后是 pandas / parquet / xtcloud。
按字面搬 4GB 原始数据等于把卡点原样复制一遍。本原型落库的是
**`jev_graph.build()` 已经算好的图结果 JSON**：重计算留在 ingest 侧，`api.py` 只做 `SELECT`。
这与「采集层与渲染层物理隔离」的既有口径一致。

```
ingest.py（离线 / 可用 pandas）--api_jev_graph.compute(day, fund=False)--> 整图 JSON
                                --原子写--> skill-data/<companyId>/<skillId>/jev.db
页面 --同源数据桥 ../../api/graph--> api.py（stdlib 只读 mode=ro）--SELECT--> 同一份 JSON
```

## 目录

```
SKILL.md              本文件，唯一入口
scripts/ingest.py     取数与计算：离线成图 + 原子写库（可用 pandas / parquet）
scripts/api.py        数据桥：stdin JSON -> stdout JSON，stdlib only、只读 sqlite
html/index.html       JEV 板块联动网查看器，自包含无 CDN；只走数据桥、不重算
references/README.md  契约、离线边界、验收判据与运行命令
```

## 运行环境与重要差异

- **`ingest.py` 不是 stdlib only**：它要 import `portal/api_jev_graph` 与 `jev_graph`，
  必须用本机 `C:\Program Files\Python311\python.exe`（3.11.9，带 pandas）。
  默认 `python` 是 3.13 且无 pandas，会造出假红。
- **`api.py` 是 stdlib only**，可用任意 3.x 跑，不碰 pandas。
- 上下文环境变量：`ZB_COMPANY_ID` / `ZB_SKILL_ID`，两个脚本用同一套派生规则，
  落到 `~/.zerobound/instances/default/skill-data/<companyId>/<skillId>/jev.db`。
- 运行时数据**不写进技能包目录**；包目录视为只读代码。

## 表与数据桥端点

```sql
graph_json(day TEXT PRIMARY KEY, payload TEXT, built_at TEXT,
           source_sig TEXT, flags TEXT)                 -- 按 day 幂等覆盖，一日一行
ingest_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, day, built_at,
             payload_sha, source_sig_sha, n_files, blocked_outbound,
             offline, flags)                            -- 只 INSERT，永不覆盖
```

| path | 返回 |
| --- | --- |
| `graph` | payload 原样展开 + `_bridge`（day / builtAt / flags / sourceSig 摘要） |
| `summary` | `empty` / `count` / `days` / `latestBuiltAt` |
| `health` | `ok` / `dbPresent` |
| `audit` | 台账行（seq / day / builtAt / payloadSha / sourceSigSha / nFiles / blockedOutbound / offline / flags），支持 `query.day` 过滤 |
| 其它 | `statusCode` 为 404 的信封 |

`graph_json` 按 day 做 `INSERT OR REPLACE`，同日重跑会覆盖该行 `source_sig`；
`ingest_audit` 因此**每次 ingest 追加一行**，把 `payload_sha` / `source_sig_sha` /
`offline` / `flags` 逐次留痕，解决「同一份图是由哪些输入算出来的」无法回溯的缺口。
原子写重建临时库时，旧台账行按**原 seq** 全量搬回；台账读取失败直接抛错、不写库。

台账启用前的历次 ingest 无审计行、不可回溯，按不造假数据铁律**未伪造回填**。

`_bridge` 是独立命名空间，避免与 payload 原有键冲突；payload 一字不改。
`sourceSig.files` 含上千条本机绝对路径，只留在库里供审计，不下发给页面；
`audit` 端点同样只返回摘要 sha，不下发路径明细。
库不存在或无行时返回 `empty: true` + `hint`，**不现算、不联网、不补值**。

## 口径红线

- `flags` 恒为 `coverage_only=True` / `gate_eligible=False` / `calibrated=False`，不得进判定闸 A/B。
- 社交覆盖率两档分母恒为 `snapshot.codes`，`posts > listed` 合法；ingest 不改写、不补默认值。
- `minute_read` 三档台账（local / remote / fail）原样带入，不美化。
- 除与 8091 `_json` 同款的 NaN/Inf → null 序列化清洗外，payload 不重算、不重排。
- 台账 append-only：只 INSERT，禁止 UPDATE / DELETE；历史行逐字段不可变。
- 原子写 `<db>.tmp → os.replace`；任何异常保留上一版库，零删除、不制造半库。
- 「不联网」以 `source_sig.offline.blocked_outbound == 0` 台账为准，
  不以「设了哪些环境变量」为准；实测 `with_chain` 与 `_warm_many` 是两条独立联网路径。

## 页面渲染口径

- 主图用 `network.nodes` / `network.edges`。`cross` 边的端点是股票代码，常不在
  `network.nodes` 内（实测某日 39 条边中 24 条如此）。页面**不伪造节点、不补值**，
  标题栏如实披露「已绘 X · 端点缺失未绘 Y」；跨板块信息由「跨板块传播」面板
  用 `network.cross_summary` 呈现，与 8091 `drawCross()` 同口径。
  详见 `tmp/jev_page_edge_gap.md`。
- 边类型：`cross` / `lead` 实线，`silent` / `echo` 虚线；涨红跌绿（中国口径）。
- 三旗标与「不得进判定闸 A/B」常驻免责带；离线降级（chain / fund / fallback）如实标注。
- 页面只走 `../../api/{graph,summary,audit}`，无 CDN、无外链、无表单 / 弹窗 / 顶层导航。

## 8091 只读比对（判据 1 已补齐）

经批准做过**一次** `127.0.0.1:8091/api/jev/graph?day=<同日>&fund=0` 只读 GET，
带 `fund=0` 与原型档位对齐。结论：结构同构成立（顶层键集合、boards / nodes / edges
长度、六个主要子键集合全一致）；内容 26 条差异 100% 归因 `with_chain` 与
`JEV_MINS_FALLBACK` 两个离线开关，零数值漂移。请求前后扫描 43121 个易写文件，
零新增 / 删除 / 改动。回执 `tmp/jev_8091_compare.md`、`tmp/jev_8091_deepdiff.md`。
**局限**：8091 默认 `fund=1` 完整档未比对，如需补要另批一次请求。

## 本包状态

**仅本地自测，未导入租户、未挂 Agent、未注册服务或计划任务。** 成图与数据桥全程离线；
8091 只做过上述一次只读 GET，**未改 8091 任何代码、配置或服务状态**。
页面改造的**本地部分已完成**：`html/index.html` 已升级为自包含 JEV 板块联动网查看器
（选日 / KPI / 板块榜 / 联动网 SVG / 焦点板块 / 跨板块传播 / 分钟台账 / 社交覆盖 /
审计台账），只走同源数据桥，不重算、不联网、不补值。
静态自测 `tmp/verify_jev_page.py` **28/28**、运行时渲染
`tmp/verify_jev_page_runtime.py` **23/23**（Node + DOM stub 真跑页面 JS，
喂真实 `api.py` 响应，SVG 节点 / 连线数与 payload 精确一致）。
**真正挂载到零界终端页面路由仍需 3100 源码 / Git / 主机 / 启动命令四选一**；
本包仍未导入租户、未挂 Agent。
