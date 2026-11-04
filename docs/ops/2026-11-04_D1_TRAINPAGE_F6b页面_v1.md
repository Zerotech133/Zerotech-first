# D1 训练面板 `/d1/training` 只读页（IFS F6b）交付记录 v1

- **工单**：WO-D1-TRAINPAGE-20261104（IFS F6b / D1-TRAINPAGE）
- **分支**：`task/WO-D1-TRAINPAGE-20261104`（基于 master，F5 training API 已合入）
- **日期**：2026-11-04　**owner**：D1；**性质**：只读页面 + 声明层登记（不接执行、不训练）
- **依据**：`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-02/05/11/12）、
  `docs/standards/2026-11-03_OUTCOME_TRAIN_契约_v1.md`、`compositions/README.md`、
  `docs/plans/2026-11-03_IFS-COMP_D1组合对接登记_v1.md`、F5 API（`portal/api_d1_training.py`，2026-11-04 已合入）

---

## 一、一句话结论

新增只读页 `/d1/training`（`portal/d1_training.html`），接 F5 只读接口
`GET /api/d1/training/episodes`；生产 `episodes.jsonl` 未生成时 **诚实空态**
（`data_unavailable=true`，不造样例/不显示假数据）；页面仅 1 个 GET 请求，**无 POST/写接口/训练触发**；
composition `page/d1/training` planned → **active**（真实页面落地）；route baseline 297 条零 diff（页面 tuple 路由不入扫描）。

---

## 二、交付文件（6 文件，纯 portal/声明/文档层）

| # | 文件 | 变更 |
|---|---|---|
| 1 | `portal/d1_training.html` | **新增**只读训练面板页：tokens.css + nav.js + shortscreen.js；只读状态卡（ok/data_unavailable/count/total/invalid_lines/auto_training）；episode 表格（metrics/组合/数据时点/只读标记）+ 行展开只读 JSON；诚实空态/坏行/接口失败三分支；仅 1 个 GET `fetch` |
| 2 | `portal/portal_api.py` | `do_GET` 增页面 dispatch：`elif path in ("/d1/training", "/d1_training.html")` → `_html_file(...d1_training.html)`（+4 行；tuple 形式与 `/d1/research` 同款） |
| 3 | `portal/d1_research.html` | 页头入口登记：新增「D1 作战台 / 🧪 训练面板」链接（不重写页面，仅 head 区加链接样式+入口） |
| 4 | `portal/tests/test_d1_training_page.py` | **新增 7 tests**：三件套/只读单 GET/无写接口/诚实空态关键词/dispatch/入口链接/route baseline 297 零漂移/composition active |
| 5 | `compositions/lusu-d1-flow/manifest.yaml` | `pages` 的 `/d1/training` → active（note 说明页面载体/诚实空态/入口）；`status.pages.active 2→3, planned []`；`status.training_page` → active；notes 补 F6b 事实；`slo.test_entry` 加页面测试命令 |
| 6 | `docs/ops/2026-11-04_D1_TRAINPAGE_F6b页面_v1.md` | 本文档（交付/证据/8734 可达性/回滚/接线项） |

**未改**：`portal/api_d1_training.py`、`portal/api_d1_*.py` 等 API/训练链、`agent_platform/training/*`、
`compositions/_schema/*`、`tools/*`、`portal/static/nav.js`、route baseline。

---

## 三、页面契约（只读、诚实）

### 3.1 数据接线（唯一请求）
- `GET /api/d1/training/episodes?limit=<10|20|50|100|200>`（`cache: no-store`）；
- 响应字段：`ok/api_schema/schema_version/readonly/paper_only/not_a_trade/auto_training/trading_permitted/generated_at/data_unavailable/count/total/invalid_lines/episodes[]/errors[]/source/note`；
- 无其他 fetch、无 POST、无 `XMLHttpRequest`、无训练触发。

### 3.2 三态展示（与 API 语义一一对应）
| API 状态 | 页面表现 |
|---|---|
| `data_unavailable=true`（episodes.jsonl 不存在） | 状态卡 `data_unavailable=true`；表隐藏；空态框标题「数据未生成（data_unavailable=true）」+ 接口 `note` 原文 + 数据源/契约说明 + **不造数/不显示假数据** 声明 |
| `data_unavailable=false, count=0`（文件存在但空） | 表隐藏；空态「无 episode 记录（data_unavailable=false / count=0）」 |
| `count>0` | 表格：episode_id/combo/数据时点/paper_trades/outcomes/coverage/hit_rate/avg_return/brier/只读；点击行展开只读 JSON |
| `invalid_lines>0` | 保留有效行，额外提示「存在 N 行坏数据（已逐行跳过并计数）」+ errors 前 10 条 |
| 请求失败/非 ok | 空态「接口加载失败」+ 路由接线说明，不重试写操作 |

### 3.3 只读标记（页面可见）
`readonly=true`、`paper_only=true`、`auto_training=false`、`trading_permitted=false`；
页脚明确「唯一 GET / 无 POST / 无写接口 / 行点击仅展开只读 JSON」。

---

## 四、测试与验证证据（本 worktree 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 页面回归 | `python -m pytest portal/tests/test_d1_training_page.py -q -p no:cacheprovider` | **7 passed，rc=0**（2026-11-04） |
| JS 语法 | 提取内联 `<script>` → `node --check` | rc=0 |
| 路由基线 | `python tools/portal_route_inventory.py --check` | ✅ ROUTE INVENTORY OK: **297 条与基线零 diff**（页面 tuple dispatch 不被扫描） |
| 组合校验 | `python tools/composition.py validate --all` | **rc=0 / errors=0**；warnings=7（含 lusu 4 条为 worktree 缺 gitignored 数据路径的既有环境告警，主仓文件存在时回落） |
| 组合计划 | `python tools/composition.py plan --strict --json` | rc=0 / errors=0 / findings=0 |
| 三态渲染（DOM 桩 node harness） | `node C:\Temp\d1_training_harness.js <payload> <mode>` | unavailable/available/error **三态 rc=0**：空态含「数据未生成+不造数」；有数据 2 行+2 详情；接口失败显「接口加载失败」 |
| 线上 API 只读副本 | `GET 8091 /api/d1/training/episodes` | 200；`data_unavailable=true, count=0, total=0, invalid_lines=0`（2026-11-04T02:42，真实生产 `episodes.jsonl` 未生成） |

> 渲染验证 payload 全部落在 `C:\Temp`（线上只读响应副本 + 验证用 mock 各 1 份），未写入仓库/生产数据。

---

## 五、8734 入口可达性说明（不重启服务，静态判定 + 只读探针）

链路：`8734`（portproxy，`netsh interface portproxy` 实测 8734→8080）→ nginx（登录门，`server: nginx/1.31.7`）→ `127.0.0.1:8091` portal_api。

- **未登录访问 8734 任意 `/d1/*`**：统一返回登录页（HTTP 200 / 7821B「登录 · 零界」），无法匿名验收页面内容——这是既有登录门行为，非本页缺失；
- **8091 直连现状（只读探针）**：`GET /d1/training` → **404**（页面 dispatch 为本单新增，常驻进程仍跑旧码）；`GET /api/d1/training/episodes` → 200（F5 API 已生效）；
- **生效条件**：页面 dispatch 需 **维护窗口重启 portal_api（8091）换血**后生效（红线「修复写盘≠生效」）；重启后 `8091 /d1/training` → 200，登录态下 `8734 /d1/training` 同源可达；
- 本单**未启停任何服务**；重启验证由 Lead 在维护窗口执行。

**接线项（Lead）**
1. 维护窗口重启 portal_api（8091）使页面 dispatch 生效，并按 `GET 8091 /d1/training` 200 + 登录态 8734 复核落证据；
2. 从 `compositions/_schema/planned_refs.yaml` 移除 `page/d1/training` planned 条目（本单写域不含 `_schema`）；移除后 `composition.py validate --all` 复跑；
3. 可选：Q2 探针 endpoints 增列页面 URL（`/d1/training`），在重启后纳入健康证据。

---

## 六、回滚（声明层，无运行时/数据变更）

1. `git revert <本单 commit>`（6 文件；页面/测试/manifest/文档/dispatch/入口链接一次性复原）；
2. 若仅需下架页面：删 `portal/d1_training.html` + 回退 portal_api 页面 dispatch + manifest `page/d1/training` 改回 planned；
3. 无数据写入、无服务变更、无计划任务 → 回滚不涉及数据/账本/服务。

---

## 七、边界与未做

- 不改 API/训练链、不写生产 `episodes.jsonl`、不触发训练、不接实盘；
- 不启停服务、不新增计划任务、不改 route baseline（页面 tuple 路由不入清单）、不改 `tools/*`；
- 未加入全站 `nav.js` 导航数据：D1 页面族（research / war-room / training）当前均不在全站 nav groups，
  本单入口采用「D1 研究页页头链接 + `/d1/training` 直达 + composition page 登记」，避免只给训练页单独加全站入口造成口径不一致；
- 生产 `episodes.jsonl` 生成属 F5 生产接线（Lead 决策），本页对缺失数据保持诚实空态。
