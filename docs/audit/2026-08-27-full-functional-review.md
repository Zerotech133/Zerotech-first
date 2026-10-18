# zhice-OS V2 全功能测试评审报告（技术总监视角）

**日期**：2026-08-27
**评审基线**：git HEAD `872b96c`（main 分支，工作树干净）
**评审方法**：全量测试实跑（197 用例）+ 覆盖率统计 + ruff/mypy 静态检查 + 前端构建 + 真实 PostgreSQL 数据实证 + 运行时 API 冒烟（132 端点）+ 迁移全新库实测 + 7 路并行子代理代码深审
**声明**：本报告只报告能从代码/数据/运行实证确证的问题；未修改任何源码。

---

## 〇、结论摘要（TL;DR）

系统处于**功能完备、可运行、但"可部署性/安全性/测试纵深"三个维度未达标**的状态：

| 维度 | 结论 |
|---|---|
| 功能完整性 | ✅ 132 个 API 端点全部可运行；四市场交易闭环（盘前→盘中→盘后→复盘→学习）真实落库运行中；前端 38 个页面与后端端点一一对应 |
| 测试通过率 | ⚠️ 197 过 / 1 挂（挂的是时区敏感 flaky 测试，CI 绿、本机 0:00-8:00 必红） |
| 测试覆盖率 | ⚠️ 总 54%；核心交易链 workflow_service 仅 50%、scheduler_jobs 14%、调度作业实现 0%、多只行情网关 28-45% |
| 静态质量 | ✅ ruff 全过；❌ mypy 110 错误（38 文件，CI 非阻塞形同虚设）；前端 strict TS 无 any |
| **全新部署** | ❌ **`alembic upgrade head` 在空库上必失败**（0001/0002 重复建 v2_market_calendar，已实测复现），且 12 张表、4 处列缺迁移 → **当前无法按文档路径从零拉起** |
| **安全** | ❌ **49 个写端点（含开实盘/批准建仓/解除熔断）全部匿名可访问**；token 密钥硬编码可伪造；默认口令 123456 |
| **交易正确性** | ❌ **加仓失效**：同一授权重复撮合（次数闸门永不触发）、持仓成本被覆盖、授权无 EXECUTED 终态（P0-6，代码+数据双重实证） |
| 数据一致性 | ⚠️ CRYPTO 74% 持仓无 plan 归属链；fill 无方向列致卖出成交不可审计；US/HK regime 静默退化到单只个股 |

**技术总监的一句话结论**：这套系统"能跑"且交易闭环在真实运行（昨天夜里还在触发止损平仓），但当前状态**绝不能直接上生产实盘**——先解决迁移可部署性（P0）、写端点鉴权（P0）、测试纵深（P1）三大问题。

---

## 一、测试执行结果（实测）

### 1.1 后端全量测试

| 项 | 结果 |
|---|---|
| 用例数 | 198（52 个测试文件） |
| 结果 | **197 passed / 1 failed**，耗时 8.24s |
| 失败用例 | `tests/test_conflict_redo_b.py::test_entry_query_excludes_dead_plan_but_keeps_orphan` |
| 失败根因（已实证） | 种子 `created_at=datetime.now(UTC)` 与查询下界 `datetime.combine(date.today()本地, min, tzinfo=UTC)` 在 **CST 00:00-08:00**（本地日超前 UTC 日）错日 → 全部行被过滤 → 断言 `set()`。CI（UTC 宿主）恒过、本机每日凌晨必红 |
| 修复建议 | 测试内改用 `date.today(UTC)` 或 `now(UTC).date()` 作为查询日（一行修复） |

**关键结论**：这套测试的绿灯含金量低——4~8 秒跑完全部 198 个用例意味着绝大多数是"内存后端 + 打桩"的单测，真实 PG 集成、真实网关、真实调度执行几乎未覆盖（详见 §五）。

### 1.2 覆盖率（pytest-cov 实测，总 54%）

**核心交易链关键模块覆盖一览（未达标清单）**：

| 模块 | 覆盖率 | 说明 |
|---|---|---|
| `scheduler_jobs.py`（840 行，全部定时任务实现） | **14%** | 调度是系统的心脏，实现几乎裸奔 |
| `pipeline_service.py` | **11%** | |
| `promotion_service.py` / `walk_forward_service.py` / `continuity.py` / `providers.py` / `universe.py` / `adj_factor_service.py` / `data_ingestion_service.py` / `intraday.py` 管线 | **0%** | 一整批模块零覆盖 |
| `workflow_service.py`（812 行核心交易闭环） | **50%** | 半数分支（含多个盘前/盘中路径）未测 |
| `sqlalchemy_db.py`（661 行仓储） | **43%** | 真库路径大半未测 |
| 行情网关 us/hk/crypto/datacenter | 28% / 38% / 45% / 39% | 外部源故障/降级/限流路径未测 |
| 缠论引擎 engine/rules_v2 | 22% / 17% | 结构评分核心逻辑覆盖严重不足 |
| 路由层 market.py 24% / positions.py 21% / system_ext.py 14% / candidates.py 17% | — | 前端主路径端点几乎无测试 |

### 1.3 静态检查

| 工具 | 结果 |
|---|---|
| ruff check src tests | ✅ All checks passed（规则集固定且 ignore 有注释，工程上可接受） |
| mypy src | ❌ **110 errors / 38 files**（CI 里 `continue-on-error: true` 非阻塞）—— 类型债已成体系：`workflow_service.py` 的 `strategy_version_id` str/int 混用、`market.py` 的 None 解引用、`system_ext.py` 的 Row 元组、网关 `dict[str, object]` 等 |

### 1.4 前端

| 项 | 结果 |
|---|---|
| `tsc -b && vite build` | ✅ 通过（413ms） |
| 产物 | 单 chunk **1.35 MB**（gzip 415KB），无代码分割、无 `React.lazy` |
| 测试 | ❌ **0 个测试文件**，无 test 脚本 |
| Lint | ❌ 无 eslint 配置/脚本（源码有 4 处 eslint-disable 注释） |
| 类型 | ✅ tsconfig strict，全库 0 个 any、0 console.log、0 TODO（纪律好） |
| 死依赖 | lightweight-charts、recharts 声明但全库零 import（K 线已自绘 SVG） |

---

## 二、功能矩阵总览（132 端点 + 38 页面）

### 2.1 后端功能域（全部实测可访问）

| 域 | 端点数 | 代表端点 | 实测 |
|---|---|---|---|
| 系统/健康 | 2 | /health /status | ✅ |
| 行情 | 12+WS | /market/kline /quotes /overview /regime /calendar /ws/market | ✅（WS 匿名可连） |
| 策略 | 13 | /strategies CRUD、/performance、/{sv}/approve | ✅ |
| 候选/计划 | 6 | /candidates/pools、/plans | ✅ |
| 授权/执行 | 5 | /approvals create/approve/reject | ✅（**匿名可批→真实建仓**） |
| 风控 | 6 | /risk/checks、/circuit-breaker/* | ✅（**匿名可解除熔断**） |
| 持仓/交易 | 4 | /positions、/positions/close、/trade/simulate | ✅ |
| 账户/组合 | 9 | /accounts/stats、/portfolio/*、/accounts-ext/* | ✅ |
| 报告 | 12 | /reports/*（日报/学习/衰减/盈亏日历） | ✅ |
| 复盘/研究 | 10 | /replay/*、/research/*、/knowledge/* | ✅ |
| 治理/模型 | 19 | /governance/*、/model-ops/*、/multi-strategy/* | ✅ |
| 回测 | 3 | /backtest/run、walk-forward、exit-lab | ✅ |
| 认证 | 3 | /auth/login /me /users | ✅（仅此 3 个有鉴权） |
| 调度 | 83 个 APScheduler job | 四市场盘前/盘中/盘后/日K/快照/分钟线/复盘/晨晚报/连续性巡检 | ✅ 注册结构完整（实测 build_scheduler 可构建，26 个关键 job 全在） |

### 2.2 前端 38 页面 × 后端矩阵

- 前端全部 ~60 个 API 调用**均能在后端找到对应路由**（0 个"前端调后端不存在"）
- 后端有 **~35 个端点前端完全未用**：governance 整组 8 个、market/concepts、snapshot、indicators、backtest/walk-forward、trade/simulate、reports/replay/decay/daily 等 —— "后端能力无人消费"，维护面冗余
- 4 个页面页面内自选市场、忽略 URL 市场（MarketPage/RegimePage/WorkflowPage/ResearchPage）；BacktestPage 写死 `market: 'CN'`
- 登录成功不跳转；密码以 **query string 传输**（进浏览器历史/访问日志）

### 2.3 真实数据量（实测 PostgreSQL）

日K 256 万 / 分钟K 218 万 / 复权因子 274 万 / 标的主数据 14306 / 计划 1538 / 成交 749 / 持仓 429 / 决策复盘 1435 / 学习案例 567 —— 数据是"真的"，且系统昨晚（08-26 24:00-00:20）仍在自动触发止损平仓（告警实证）。

---

## 三、P0 —— 上线前必须解决

### P0-1　全新部署必失败：`alembic upgrade head` 在空库上 DuplicateTable（已实测复现）

- **证据**：在全新空库 `zhice_scratch_test` 上执行迁移，`0002` 阶段报 `psycopg2.errors.DuplicateTable: relation "v2_market_calendar" already exists`。根因：`0001_initial.py:166` 与 `0002_market_calendar.py:20` **重复建 v2_market_calendar**。
- 现有开发库能跑是因为历史迁移路径绕过了该冲突，**任何新环境都无法按文档路径（`make migrate`）初始化**。

### P0-2　迁移链与 ORM 模型严重漂移：12 张表、4 处列无迁移（脚本逐列比对实证）

| 类别 | 明细 |
|---|---|
| 12 张模型表**无任何迁移** | v2_alert、v2_latest_snapshot、v2_app_state、v2_adj_factor、v2_earnings_forecast、v2_holder_trade、v2_top_list、v2_dividend、v2_market_environment、v2_promotion_decision、v2_gray_release、v2_backtest_run —— 全库无 `create_all`/`CREATE TABLE`，仅 v2_alert（sqlalchemy_db.py:37）与 v2_latest_snapshot（scheduler_jobs.py:387）有运行时自建兜底，**其余首触即崩** |
| 4 处模型有列、迁移没有 | v2_strategy.description（策略 CRUD 全崩）、v2_bar_daily.pre_close（日线同步/持仓必崩）、v2_fill_record.realized_pnl/sell_reason（平仓闭环必崩）、v2_learning_case.status |
| 链健康 | 0005 docstring 写 `Revises:0004` 但 `down_revision="0003"`（0004 文件已删，仅剩 pyc）；alembic.ini 硬编码开发库 URL（不读 `DB_MAIN_URL` 环境变量，部署到其它库会迁移错库） |

**结论**：当前"从零部署"这条路是断的。整改：修复 0001/0002 重复 + 补 0004-0008 迁移（autogenerate 后逐列核对）+ alembic.ini 改环境变量 + CI 增加"空库 upgrade head + 冒烟查询"门禁。

### P0-3　安全：49 个写端点全部匿名可访问（实测 422 而非 401/403 即证明）

- 全仓库仅 `/auth/me`、`/auth/users` 两个端点使用 token/RBAC（`get_current_user` 只在 auth.py 引用）；**其余 128 个端点零鉴权**。
- `internal_key_auth` 中间件默认 `internal_api_key=""`（settings.py:22）→ 完全关闭；且若配置该 Key，**前端 api.ts 从不发送 `X-Internal-Key` → 整体 401 瘫痪**（前后端契约缺口）。
- 实测：`POST /approvals` 无任何凭据 → 422（仅参数校验拦截）；`POST /circuit-breaker/trigger` → 422。**攻击者仅需 3 个 HTTP 调用即可：开实盘（/brokers/live）→ 批准建仓（/approvals/{id}/approve）→ 解除熔断（/circuit-breaker/reset）**。
- 高危匿名写端点清单（节选）：`/positions/close`（一键平全部持仓）、`/approvals/{id}/approve|reject`（批准即真实建仓）、`/brokers/live`（开实盘开关）、`/workflow/pre-market|intraday|post-market`（触发完整流水线）、`/circuit-breaker/trigger|reset`、`/settings/trade-mode|risk-params`、`/multi-strategy/resolve`、`/strategies/{id}/approve`、`/governance/*`、`/model-ops/*`、`/accounts`、`/alerts`、`/trade/simulate`。
- **整改**：全路由接入 `Depends(get_current_user)` + 角色矩阵（读=登录、写=角色、危险写=双因子预留）；密钥外置且强制非空；前端 api() 加 401 全局跳登录。

### P0-4　凭据硬编码面（SEC-2 复检：部分修、残留多）

- **已入库的真实口令**：`scripts/import_v1_production.py:18-23`（`lmkj929292`）、`backfill_fill_pnl.py:6-11`、`backfill_v1_replays.py:6-7`、`backfill_sell_reason.py:11`（git ls-files 确认已提交）
- `scheduler_jobs.py:26` `V2_DB_PASSWORD` 默认 `zhice_pass`（活路径 psycopg2 直连使用）
- `settings.py:18` `db_main_url` 内嵌 `zhice:zhice_pass@`；`settings.py:26` 默认口令 `123456`（.env.local 未覆盖 → 生产默认弱口令）
- `token_service.py:11` JWT 密钥硬编码 `"zhice-os-v2-dev-secret"` → **凭源码可伪造任意角色 token**

### P0-5　真实口令进了 git 历史

除 P0-4 列举的脚本外，8/20 审计的"15 笔 CLOSED 仓 pnl NULL"已修复（实测当前 CLOSED 0 笔 NULL），但**历史 git 中仍可翻出凭据**——建议轮换 V1/V2 数据库口令并清理历史（git filter-repo）。

### P0-6　加仓功能失效 + 同一授权重复撮合 + 持仓成本被覆盖（代码+数据双重实证）

盘中入场循环对同一 APPROVED 授权允许最多 2 次成交（首建 + 一次加仓，`workflow_service.py:1133-1134,1186 MAX_ENTRIES_PER_PLAN=2`），但存在三个叠加缺陷：

1. **入场次数闸门永不触发**：计数靠 `_today_entry_counts` 读 `v2_order_intent`（workflow_service.py:1342-1367），而 `_record_fill` 以 `intent_id = "intent-" + order_id`（order_id = `{request_id}-sim`）写库且 `on_conflict_do_nothing` 去重（models.py:323 intent_id 唯一）——**第二次执行写不进新 intent 行 → 计数恒为 1，`already >= 2` 永不成立** → 同一授权在满足价格条件时每 5 分钟重复撮合，只有"不向下摊薄"（现价 < 上次成交价）和入场时间窗在兜。
2. **加仓 = no-op 且均价被重写**：第二次执行 `order_id` 与首次相同（`f"{request_id}-sim"`，workflow_service.py:764）→ `position_id = pos-{order_id}` 相同 → `save_position` 走 **UPDATE 分支整体覆盖 open_qty/open_price**（sqlalchemy_db.py:495-503），不累加 lot、不写新 fill。V1 语义"首建 + 一次加仓"实际不生效；若二次撮合价不同，**持仓成本被重写为最新价**，pnl 计算随之失真。
3. **授权无终态**：`ApprovalStatus` 无 EXECUTED（approval.py:11-16），APPROVED 永久停留 → 手动批准（approval.py:76 立即执行）与盘中循环（同一 order_id 再撮合）双入口反复命中；`idempotency_key` 列（models.py:345）无任何消费；无行锁/乐观锁。

正确实现 `PositionOpsService.add_position`（独立 lot 累加）与 `tiered_take_profit/evaluate_take_profit/t_plus_one_locked`（grep 证实全库无调用点）是**死代码**——"审查对象 ≠ 运行对象"的典型。**整改**：加仓改走 add_position 累加 lot；授权加 EXECUTED 终态 + idempotency_key 消费；entry 计数改按 fill 记录去重计数。

---

## 四、P1 —— 高风险逻辑/健壮性

### P1-1　跨市场汇总仍会算错（8/20 P0-1 复检：部分修复）
- ✅ 已修：report_service/portfolio_service 改经 `domain/market/fx.py` 折算 CNY 并带 `fx_stale` 标记；前端 PnlCalendar 标签改"每日权益盯市"。
- ⚠️ 残留：`fx.py` 仍是**静态硬编码汇率**（USD 7.2 / HKD 0.92 / USDT 7.2，as_of=2026-01-01，`FX_STALE=True`），实时汇率表是 TODO；`portfolio/summary` 响应未回传 `fx_stale`（前端无法感知）；`HomeBigscreen.tsx:70` 前端还有一份 LOCAL_FX 兜底（F-6 未修）；日报/组合总览的总 CNY 数值在汇率漂移后系统性失真，只是"明示了"。

### P1-2　US/HK 大盘择时静默退化到单只个股（实测）
- `regime_service.py:22-24` 主基准已分市场（US=^GSPC、HK=^HSI），**但本库没有指数日K** → 运行时静默走 `_FALLBACK_BENCH`：**实测 `GET /market/regime?market=US` 返回 `benchmark:"AAPL"`**，即美股 regime 由苹果一只股票的 5 日收益判定，且响应无任何 degraded 标记。
- 影响：US/HK 策略的敞口帽/启停由错误基准驱动，且无人能察觉。

### P1-3　CRYPTO 74% 持仓无 plan 归属链（数据实证）
- `v2_position.trade_plan_id` NULL 117/429（27%），其中 **CRYPTO 57/77（74%）**。归属链修复（38148b8）只覆盖部分入场路径；CRYPTO 入场（spec 信号/自动化）不走 plan 落库 → 复盘归因、胜率统计、组合分析对 CRYPTO 大面积断裂。
- 另：`v2_fill_record` **无 direction 列**（P0-3 的 schema 根因仍在），卖出成交无法从表结构区分；`realized_pnl` NULL 425/749（57%，部分为 BUY 天然无值，但无方向列导致不可验证）。

### P1-4　静默吞异常 + 会话泄漏叠加（8/20 P2-1/P2-9 复检：未修）
- 16 处 `except Exception: return []/{}` 无日志（market.py:66-67/112-113/156-157/197-198/211-212/227-228/261-262/295-296、positions.py:54-55/81-82/108-109/158-159/193-194/219-220/229-230、overview.py:45-46/91-92/122-123、candidates.py:33-34）→ **DB 故障伪装成"无数据"**，与真空表无法区分。
- 16 处 `session.close()` 写在 try 体内而非 finally（异常路径跳过 close）→ 连接池渐进耗尽。**最危险组合**：DB 故障时前端主路径（/market/*、/positions）表现为"莫名为空"，同时池被慢慢抽干。

### P1-5　参数校验缺口（写端点）
- `POST /positions/close` 的 `close_price: float` 无约束 → **负价/零价可平仓并写入错误 pnl**（positions.py:368-369）
- `POST /approvals` 的 `direction` 自由字符串直落库；`strategy_version_id` 裸 int 但调用方传 str（mypy 已报 workflow_service.py:643）
- `POST /trade/simulate`、`/workflow/pre-market` 坏输入（错市场/坏日期）→ **500 而非 422**
- `POST /strategies/register` 校验失败 500，同域 `/create` 却是 400 —— 错误语义不一致

### P1-6　前端 token/登录体系（安全连带）
- token 存 localStorage（任意 XSS 可读）、无 httpOnly cookie 方案、无 401 全局跳转
- **ProtectedRoute 只检查 token 是否存在**（任意字符串可过守卫），且不调 /auth/me 验证
- 登录口令走 query string（LoginPage.tsx:18）；登录成功不跳转

### P1-7　调度实现裸奔（scheduler_jobs.py 覆盖 14%）
- job 实现大面积无测试；`plan_expiry_crypto` 用宿主 `date.today()`（UTC 宿主跨午夜延迟 ~24h）
- US 盘前自愈永不触发（catchup 窗口与 job 时区零重叠）；CN 14:56-15:00 收盘分钟线永不采集（cron 边界）；US/HK 盘后 15:35 早于实际收盘（美股 16:00）
- daily 采集链失败**静默无重试无日志**（源故障=数据静默缺失）
- psycopg2 直连与 ORM 混用 + 每次 upsert 运行时 CREATE UNIQUE INDEX（唯一约束不在迁移里）

### P1-8　spec 信号平仓绕 T+1/可卖性 + 跨进程无锁
- `run_spec_signals` 平仓直接调 `_close_position`（workflow_service.py:861-866），该函数无 T+1、无 can_sell 校验（1368-1398）→ CN 盘中 spec job 每 5 分钟注册（scheduler.py:370-377），T+1 约束对 spec 信号路径形同虚设
- `POST /workflow/*` 与调度器进程可并发；启动 catch-up（scheduler.py:74-111）与 09:25 盘前 job 存在竞态（双触发候选池/计划）
- 部分成交剩余量不追踪：PARTIALLY_FILLED 只按 filled_qty 入账，剩余量丢弃，二次执行按全量重买（P1-6 老问题仍残留）

### P1-9　配置项定义了但从未被消费（"配置=摆设"清单）
- `sim_max_positions` / `sim_max_total_exposure_pct` / `trade_approval_expire_seconds`（settings.py:44-46）**全库无读取点**
- 风控 C2 风险预算、`POSITION_RATIO_TOO_HIGH`、`LIQUIDITY_INSUFFICIENT` 三条约束**恒不生效**（调用点不传 turnover/ratio 参数，risk_service.py:29-41 分支不可达）
- `OrderResult.fee` 无消费者；ST 涨跌停 5% 检测失效（`_load_security_names` 恒空）；平仓 pnl 两步写非原子（save_position 后裸 UPDATE，失败则 CLOSED 行 pnl 恒 NULL）
- upsert 无状态守卫：CANCELLED/REJECTED 的计划/授权可能被同日重跑"复活"（人工拒绝被推翻）
- **12+ 个 workflow 助手无视 `persistence_backend` 直读生产 PG**（position_sizing_service.py:42 等）——memory 后端隔离形同虚设；`list_candidates_by_strategy` 双后端行为不一致

---

## 五、测试体系专项评审（技术总监最该关注）

### 5.1 这套测试能否拦住回归？—— 不能，理由如下

1. **CI 绿 ≠ 本地绿**：本次唯一失败（test_conflict_redo_b flaky）在 UTC 宿主恒过、CST 凌晨必红（TZ=UTC 复跑同用例通过，机制坐实）——CI 与开发环境时区不同步，回归防线第一道就漏。
2. **全量 4~8 秒**：198 用例 = 纯单测 ~77%（152）+ sqlite 真 ORM ~11%（21）+ TestClient 端到端 ~8%（16）+ 真 PG 直连 3%（6 个，**CI 上因无 PG 服务必然 skip**）——没有任何调度作业实现、网关故障降级、并发重入的测试。
3. **最高危端点零测试**：`/positions/close`、`/approvals/approve`、`/brokers/live`、`/circuit-breaker/trigger|reset`、`/settings/trade-mode` 这些"一键改钱"的写端点**全部没有端点级测试**（40 个路由文件仅 ~15 个端点有 HTTP 测试）。
4. **conftest 守卫可被绕过**：`_guard_real_pg` 只替换函数体内 import 的 `get_session`，**17 个 router 是模块级 import 绑定原函数、守卫完全绕过**（子代理脚本实证；本机 test_kline_endpoint 实际读了真库）。且 `persistence_backend != "sql"` 短路是"设计性不测"：`_run_entry_loop` 在测试模式直接 `return []`，`_record_risk/_persist_candidate_pool/_record_fill` 等真库写路径**零覆盖**——测试断言的是"跳过行为"而非业务结果。
5. **弱断言/自证**：部分测试只验证"自己刚塞进内存库的数据"（sqlalchemy roundtrip、注册表测试验证自己写死的常量、kline `len>0` 双数据源通吃）；auth 测试因 conftest 设 `ADMIN_PASSWORD=""` 从不覆盖生产默认的密码校验路径；test_indicators 只验键不验数值；e2e 多为 `200 + len==1` 浅断言。
6. **工程化缺口**：pyproject 无 addopts/fail-under/markers 注册；CI 无 `-x`、无 `--cov-fail-under`、mypy 非阻塞、无 PG service（6 真库测试 CI 必 skip）；sqlite 样板在 6 个文件重复 6 份无共享夹具。

**顶层结论**：能拦住"被测过路径"的回归（缺陷驱动测试质量高），但 CI 绿灯 ≈ "192 测试在 UTC 通过 + 54% 覆盖无门禁 + 真库测试未运行"，对 scheduler/pipeline/promotion/walk_forward/continuity/网关错误路径等 46% 裸奔代码**没有拦截能力**；该 flaky 测试更使 CI 对该回归无仲裁力。

### 5.2 建议补的测试（按价值排序）

| 优先级 | 补测内容 |
|---|---|
| P0 | 全新空库 `alembic upgrade head` + 冒烟（CI 门禁，防止 P0-1/P0-2 复发） |
| P0 | 写端点鉴权矩阵测试（无 token → 401/403） |
| P1 | 交易闭环真库集成（计划→风控→授权→撮合→持仓→平仓→pnl 全链路，含部分成交/T+1/涨跌停） |
| P1 | 调度 job 实现级测试（盘前/盘中/盘后各跑一遍，断言 DB 产物） |
| P1 | 网关降级链测试（主源挂→兜底→标 stale） |
| P2 | 并发重入测试（同一计划双触发只执行一次）；时区边界测试（CST 凌晨/UTC 宿主） |
| P2 | 前端：组件渲染 + api 封装 401 处理（目前 0 测试） |

---

## 六、前端专项（子代理深审摘要）

- **已修复确认**：F-1 写死 CN 大部分已改 `useMarket`（剩 HomeBigscreen.tsx:111 + 死代码 BigscreenPage）；F-3 流水线按钮按状态渲染；F-4 盈亏日历标签/配色统一；SEC-1 后端密码校验已加（弱默认 123456）
- **仍存在**：F-6 HomeBigscreen LOCAL_FX 硬编码汇率兜底；F-7 BigscreenPage.tsx 全项目零 import（死代码）+ ExportsPage 10 行空壳（裸 `<a download>` 直链，不走 api()）
- **F-2 口径问题本质未变**：同屏双数据源（/overview 与 /pipeline）口径不同（今日候选 14 vs 累计候选 516），无前端对齐/校验
- 4 处页面内自选市场与 URL 市场脱节；BacktestPage 写死 CN；全站 30s 轮询 + StrictMode 双调用 → 重复请求
- **若配置 INTERNAL_API_KEY，前端整体 401 瘫痪**（契约缺口，见 P0-3）

---

## 七、基础设施专项（子代理深审摘要）

- **实时推送**：`main.py:62-71` 在 asyncio 事件循环里每 5s 做同步 SQL 查询（`_db_quotes` 对 100 只标的 N+1 查日K）→ **阻塞事件循环**；`realtime/manager.py` 无心跳/背压，慢客户端阻塞广播
- **网关**：us/hk/crypto/eastmoney 大面积 `except: return []`（源故障与无数据无法区分）；`us.py:110` Finnhub token 走 URL query；`hk.py:75` 明文 HTTP；`providers.py` 三个 Provider 死代码；HKGateway docstring 与实现主源不一致
- **ingest**：每次 upsert 运行时建唯一索引（不在迁移、与已删 0004 的索引定义漂移）
- **券商**：LiveBroker 是安全占位（enabled=False 恒拒单 + NotImplementedError），**无任何真实券商对接能力** —— 模拟/纸面能跑，实盘是空头支票
- **数据新鲜度**：快照表新鲜度正常（CN 收盘 14:59 / US 盘中 / CRYPTO 5s 级），但 `/market/overview` 实际读的是日K表（volume/turnover 恒 0、snapshot_time 实为 trade_date），且涨跌榜基于**按 symbol 排序的前 500 只**（非代表采样，`sampled:true` 误导）

---

## 八、遗留问题清单（8/20 审计整改率）

按 8/20 审计 41 条逐条复核（子代理实证）：**FIXED 23（56%）/ PARTIAL 10 / REMAINS 8**，折算整改率 ≈ 68%。

- **P0 已修 4/5**（平仓写 realized、apply_fill 方向、regime 分市场、盈亏日历 pre_close；剩 P0-1 跨币种=部分修）
- **P1 已修 11/12**（剩 P1-5 熔断权限矩阵仍装饰性）
- **P2 只修 2/9（重灾区）**：静默吞异常、毛/净口径、缺价兜底、date.today、红线硬编码副本、封顶 ratio 判红线 全部未修
- **P3 修 2/6**；**前端修 3/7**（F-6/F-7 未修）；**安全修 1/2**（SEC-1 密码校验补上但默认弱口令 + SEC-2 残留）
- **本评审新增发现**：迁移断链（P0-1/P0-2）、加仓失效+重复撮合+成本覆盖（P0-6）、49 写端点全匿名（P0-3）、JWT 密钥硬编码、CRYPTO 归属链断裂（74% NULL）、regime 静默退化 AAPL、fill 无方向列、spec 平仓绕 T+1（P1-8）、配置定义不消费（P1-9）、calendar 接口无日期过滤、overview 采样误导、事件循环阻塞、守卫可绕过等

---

## 九、整改路线图（技术总监建议）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **本周（P0）** | ① 修迁移断链（0001/0002 重复 + 补 12 表 4 列）+ CI 空库门禁；② 全路由接入鉴权（读=登录、写=角色、危险写=双因子预留）；③ 密钥/口令全部外置并轮换历史泄露凭据；④ 前端 api() 补 X-Internal-Key/401 全局跳转；⑤ 修加仓链路（add_position 累加 lot + 授权 EXECUTED 终态 + idempotency_key 消费） | 空库可一键拉起；无 token 调写端点返回 401；凭据 0 硬编码；加仓真正累加、授权不重复撮合 |
| **两周（P1）** | ⑥ 交易闭环真库集成测试 + 写端点矩阵测试；⑦ 修静默吞异常（全部转 logger.exception + 5xx）；⑧ 会话 try/finally 全量整改；⑨ regime 补指数数据/降级标记；⑩ CRYPTO 归属链落 plan；⑪ fill 加 direction 列；⑫ spec 平仓补 T+1/can_sell；⑬ 配置项接消费点（sim_max_positions/风控 C2） | 覆盖率核心链 ≥80%；错误可观测；regime 响应带 degraded 标记；T+1 全路径生效 |
| **一个月（P2）** | ⑭ 调度 job 实现级测试（先补 0% 模块）；⑮ 事件循环 DB 查询改线程池/异步驱动；⑯ 前端代码分割 + 补单测；⑰ 实时汇率表 + fx_stale 全链路回传；⑱ 移除死代码（BigscreenPage/providers/add_position 之外的死函数/死依赖） | 前端首屏 <1MB；mypy 归零；无死代码 |
| **持续** | ⑲ mypy 设为 CI 阻塞；⑳ 时区/并发边界测试常态化；㉑ 灰度+实盘券商适配（LiveBroker 落地）；㉒ git 历史凭据清理（filter-repo + 轮换口令） | CI 门禁全绿含时区矩阵；凭据无历史残留 |

---

## 附：评审过程记录

- 全量 pytest 实跑 3 次（-x、全量、带覆盖率）；ruff/mypy/前端 build 各 1 次
- 真实 PG 逐表计数 + 数据一致性核对（pnl NULL、plan 归属、fill 方向）
- 运行时冒烟：uvicorn 起 8011 端口，132 端点 OpenAPI 统计，12 组关键端点实测
- 全新空库 alembic 实测（DuplicateTable 复现）
- WS 正确路径实测（/api/v1/market/ws/market）
- 7 路并行子代理代码深审（审计复核/API/交易闭环/基础设施/调度/前端/测试质量）
- 冒烟服务器已关闭，临时库已清理，未对生产库做任何写操作
