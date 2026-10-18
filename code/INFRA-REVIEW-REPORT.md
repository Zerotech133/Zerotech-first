# zhice-OS V2 基础设施评审报告

评审范围：DB 层 / 迁移一致性 / 行情网关 / 实时推送 / 券商适配 / 密钥与配置。
方法：静态代码审查 + 脚本化执行对比（真实执行各迁移文件 upgrade() 提取建表/加列，再与 SQLAlchemy 模型元数据逐表逐列比对）+ git 追踪状态核验。
只报事实，未改任何代码。

---

## 0. 结论速览

| 级别 | 数量 | 主题 |
|---|---|---|
| P0 | 4 | ① 4 张表缺列 → 核心路径必然运行时 SQL 报错；② 12 张模型表无迁移无自愈 → 多个作业/API 首跑即崩；③ 提交进 git 的真实数据库口令；④ 全 API 默认无鉴权 + 硬编码 JWT 密钥 |
| P1 | 6 | 迁移链 fresh upgrade 必失败（v2_market_calendar 重复建表）；WS 匿名可连；Docker 0.0.0.0 无鉴权；默认 admin 口令；DB 口令默认值回退；alert 直插绕过自建 |
| P2 | 8 | 异常吞没致数据静默缺失；事件循环内同步 DB 调用；WS 无心跳/背压；运行时 DDL；token 走 URL query；N+1；错误路径会话泄漏；绕过连接池直连 psycopg2 |
| P3 | 8 | 死代码 / 重复协议 / 文档与实现不一致 / 明文 HTTP / 索引治理漂移等 |

---

## 1. DB 层

### 1.1 连接管理（get_session 是否关闭）

**总体：好。** 115 处 `get_session()` 中 114 处配套 `session.close()`（多数 try/finally）。`session.py:12-24` 用 lru_cache 缓存 engine（`pool_pre_ping=True`）与 sessionmaker，`expire_on_commit=False` 合理。仓库层统一 `with self._session_factory() as db`（如 `sqlalchemy_db.py:61,85,101...`），事务边界靠 Session 上下文管理器提交/回滚，符合规范。

例外（错误路径泄漏 + 静默失败）：

- [P2] `api/routers/market.py:23-67` — `_db_quotes()` 打开 session（:23），正常路径 :64 close，但 `except Exception: return []`（:66-67）**不 close** → 查询异常时连接泄漏回池（进程级累积），同时把真实错误吞成空列表。
- [P2] `infrastructure/market_data/kline.py:72-103`、`108-139` — `session.close()`（:84/:120）在查询之前，`except Exception: return None`（:102/:138）**不 close** → 同样泄漏；且所有异常被吞，K 线接口静默返回 None。
- [P3] `infrastructure/market_data/continuity.py:19-20`、`ingest.py:20-21`、`universe.py:14-15`、`gateways/us.py:222` — 每调用新建一条裸 psycopg2 连接（虽 finally close），绕过 `_engine()` 连接池；`us.py:222` 直连 `get_settings().db_main_url` 读 v2_instrument。

### 1.2 事务边界

- [P3] `sqlalchemy_db.py:207-229` + `256-257` — `save_candidate_pool` 对每个候选走"先 SELECT 再 INSERT/UPDATE"（N+1 写），同事务内可接受，但候选池规模大时放大为逐行往返；可用批量 upsert（如 `pg_insert...on_conflict`）替代。
- [P3] `save_position`（:471-504）只写 `trade_plan_id`，注释（:484-486）说明 avg_cost/pnl_pct/stop_loss_price 列**从不落库**——模型声明了 3 个列但代码无写入点（读侧 `_to_lot` :529-546 也不读），属于"列在模型里但无人消费"的隐性漂移。
- [P3] 三个 port 协议文件重叠：`protocols.py`（43 行，缺 list_strategies/delete 等方法）vs `ports.py`（60 行，全量 PersistencePort）——协议定义重复且已分叉，业务层实际 import 的端口不统一。

### 1.3 memory / SQL 后端行为差异

- [P3] `repositories/memory.py:92-96` `update_trade_plan_status` 用 `PlanStatus(status)` 枚举转换，而 SQL 版 `sqlalchemy_db.py:343` 直接赋字符串——若传入非枚举值，内存版抛异常、SQL 版静默接受，行为不一致。
- [P3] `memory.py` 全量存 dict 无持久化（docstring 自述"重启即丢"）；`settings.py:30` 默认已切 `persistence_backend="sql"`，但 `api/dependencies.py:44-45` 的 `get_market_data()` 仍是内存种子行情——API 部分路径（如 `market.py:452-463` 兜底）返回的是 seed 假数据而非真行情，存在"看起来是实时、实际是演示数据"的混淆面。

---

## 2. 迁移一致性（重点）

方法：真实执行 6 个迁移文件（0001/0002/0003/0005/0006/0007）的 upgrade() 记录建表/加列，与 `Base.metadata`（models.py 39 个模型）逐表逐列比对。

### 2.1 链完整性

- 链：`0001 → 0002 → 0003 → 0005 → 0006 → 0007`，head=0007，无悬空 down_revision。**未断裂。**
- [P3] `0005_fundamental.py:4` docstring 写 `Revises: 0004`，实际 `down_revision = "0003"`（:13）——注释过期；`0004_unique_constraints.py` 源文件已被删除（仅剩 `migrations/versions/__pycache__/0004_unique_constraints.cpython-312.pyc`），其内容（uq_v2_bar_daily_symbol_date / uq_v2_bar_minute_symbol_time / uq_v2_market_calendar_market_date）被 `ingest.py` 运行时索引取代（见 2.4）。
- [P1] **fresh `alembic upgrade head` 必失败**：`0001_initial.py:165-174` 已建 `v2_market_calendar`，`0002_market_calendar.py:19-28` **再次 create_table 同名表** → 0002 报 `relation "v2_market_calendar" already exists`（且两次 id 类型不一致：0001 用 BigInteger、0002 用 Integer）。现有库能跑多半是 0001 时代先建过表/手工跳过所致，任何干净部署都会卡死。

### 2.2 缺列对比结果表（模型有、迁移没有 → 运行时 SQL 报错）

| 表 | 模型列（models.py 行） | 迁移状态 | 运行时影响（证据） |
|---|---|---|---|
| v2_strategy | `description` String(512)（:34） | 0001 未建 | **strategy 全部 CRUD 崩**：`sqlalchemy_db.py:70,80`（INSERT/UPDATE）、:95,:110（实体查询自动带该列）；`api/routers/strategy.py:155,209,225` 读写 description |
| v2_bar_daily | `pre_close` Numeric（:165） | 0001 未建 | **日线同步作业必崩**：`scheduler_jobs.py:245-262` INSERT 含 pre_close；`ingest.py:40,56,60` 批量 upsert 含 pre_close；`api/routers/positions.py:186-188`、`system_ext.py:106-109`、`reports.py:187`、`pnl_calendar_service.py:252` 查询 pre_close → 500 |
| v2_fill_record | `realized_pnl`（:377）、`sell_reason`（:378） | 0007 未加 | **平仓闭环必崩**：`application/services/workflow_service.py:1396-1397` 写入；`api/routers/strategy_detail.py:193-197` 读取（实体查询先崩，getattr 兜底无效）；`scripts/backfill_fill_pnl.py:35`、`backfill_sell_reason.py:34` 直 UPDATE 假定列存在 |
| v2_learning_case | `status`（:435，default="REVIEWED"） | 0007 未建 | **学习闭环保存必崩**：`sqlalchemy_db.py:605-616` INSERT 时默认值生效 → 列包含在 INSERT 中 → UndefinedColumnError |

### 2.3 缺表对比结果表（模型有、迁移完全没有 → UndefinedTableError）

| 表（模型） | 运行时自愈？ | 首次触达即崩的路径 |
|---|---|---|
| v2_alert（:39） | 部分：仓库层 `sqlalchemy_db.py:33-38` 懒建；但 `alert_service.py:69-76` **直插绕过**该自建 → 告警静默丢失 | `application/services/alert_service.py:69` |
| v2_latest_snapshot（:201） | 是：`scheduler_jobs.py:387` `__table__.create(checkfirst=True)`，但仅当快照作业先跑；API 先于作业查询即崩 | `api/routers/positions.py:68-77`、`market.py:81-88`、`risk_service.py:71-73` |
| v2_app_state（:499） | 无 | `api/routers/app_state_store.py:25,47,66`（KV 存储，首 GET/PUT 即崩） |
| v2_adj_factor（:509） | 无 | `adj_factor_service.py:60,79`；`scheduler_jobs.py` run_sync_adj_factor_job |
| v2_earnings_forecast（:525） | 无 | `scheduler_jobs.py:749`（每日 18:20 增量作业）；`event_strategies.py:80-91`（选股） |
| v2_holder_trade（:546） | 无 | `scheduler_jobs.py:762`；`event_strategies.py:112-121` |
| v2_top_list（:567） | 无 | `scheduler_jobs.py:777`；`event_strategies.py:142-151` |
| v2_dividend（:589） | 无 | `scheduler_jobs.py:790` |
| v2_market_environment（:610） | 无 | `regime_service.py:95-125`（市场环境判定） |
| v2_promotion_decision（:630） | 无 | `governance.py:54-55`、`promotion_service.py:167,191` |
| v2_gray_release（:655） | 无 | `promotion_service.py:272-303` |
| v2_backtest_run（:673） | 无 | `walk_forward_service.py:60`、`promotion_service.py:48-55` |

> 反向（迁移有、模型无）：**无**。所有迁移列都能在模型中找到；无孤儿表。

### 2.4 运行时 DDL 治理漂移

- [P2] `ingest.py:48-51, 84-87` — 每次 `upsert_daily`/`upsert_minute` 调用都先执行 `CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_bar_daily_mst ON v2_bar_daily (market,symbol,trade_date)`（及 minute 版）——**唯一性约束不在迁移里，而在每次写入路径上运行时建**，既有每次调用 DDL 开销，也意味着删除重建数据库后唯一性依赖该代码路径先执行。
- 索引命名漂移：被删除的 0004 建 `uq_v2_bar_daily_symbol_date`，ingest.py 建 `uq_v2_bar_daily_mst`（列序也不同：symbol_date vs market_symbol_trade_date）——同一约束两个名字/两种定义，历史库与新库行为不一致。

### 2.5 类型漂移

- [P3] 0001 全表 id 用 `sa.BigInteger`，0002/0003/0005/0006 用 `sa.Integer`，0007 又用 `sa.BigInteger`；模型里部分表用 `BigInteger().with_variant(Integer,"sqlite")`（如 `models.py:206,266,442`）部分裸 `BigInteger`（如 :299,322）。PostgreSQL 下均可运行，但 SQLite 测试路径与 PG 路径行为不完全一致，且 `v2_market_calendar` 的 id 类型在 0001(BigInteger) 与 0002(Integer) 间冲突。

---

## 3. 行情网关

### 3.1 超时 / 重试 / 限流

- [P3] `datacenter.py:78-105` — 仅对 6001 限流码指数退避（`time.sleep(2**attempt)`，:91）和超时重试；HTTP 5xx 走 `raise_for_status()` → `except httpx.HTTPError`（:101-102）**立即抛错不重试**。退避无抖动。
- [P3] `eastmoney.py:131-140`、`306-314` — `except Exception` 全吞 + 固定退避 `self._interval*(i+1)*2`（0.24s/0.72s/1.44s/2.4s），对节流/5xx 一视同仁重试，但**没有识别 HTTP 429 并放大退避**。
- [P2] 全网关 `except Exception: return []` / `return None` 吞没（`us.py:76,137,158,185`；`hk.py:107,136,157,185`；`crypto.py:57,85,112,144,177`；`eastmoney.py:138`）——网络故障静默转空数据，上层无法区分"停牌/无数据"与"源故障"，连续性巡检会误判为缺口反复回填。

### 3.2 连接生命周期

- [P3] `registry.py:11-25` 每次调用 `get_data_gateway(market)` 返回**新实例**（注释自述"有状态，用完 close"）；调用方必须记得 close。核验结果：`scheduler_jobs.py:515-531, 539-554, 564-581` 与 `continuity.py:114-129` 均正确 `finally: g.close()`——**无泄漏**。但 `us.py:104-123` Finnhub 每次 `httpx.get`（非复用 client）；`hk.py:75` 与 `eastmoney.py` 用裸 `requests.get`/`requests.Session`。

### 3.3 密钥 / 传输

- [P2] `us.py:110` — Finnhub token 作为 **URL query 参数**传递（`params={"symbol":..., "token": self._token}`），会进代理/访问日志。
- [P3] `hk.py:75` — `http://qt.gtimg.cn/q=...` **明文 HTTP**（非 HTTPS），行情数据与 UA 明文传输。

### 3.4 数据源降级链（合理性）

- CN：DataCenter（主）→（无兜底，直接抛 DataCenterError）`datacenter.py:93-104`。合理但无二级源。
- US：日线 Nasdaq→yfinance（`us.py:254`）；快照 Finnhub→yfinance（:262）；分钟 东财→yfinance（:268-269）。链合理。
- HK：实现为 腾讯→东财→yfinance（`hk.py:233-237`），但类 docstring（:215-216）与 `__init__` 注释（:220）写"东方财富为主源"——**文档与实现顺序不一致**（[P3]）。
- CRYPTO：快照 Gate→Binance（`crypto.py:204-208`）；日线 Binance→Gate（:215）；分钟仅 Binance（:221）。Gate 快照无源 tick 时间戳，`snapshot_time=now()`（:165）——代码注释已说明 7×24 场景可接受。
- [P3] `providers.py`（TushareProvider/YFinanceProvider/BinanceProvider）**死代码**：全仓库仅 `data_ingestion_service.py:5` 引用 StaticProvider；其余三个 Provider 无人调用，且与 gateway 层重复实现了一套更粗的数据逻辑（`providers.py:49-241`），存在"两套行情实现、口径可能分叉"的隐患。

---

## 4. 实时推送（WebSocket）

- [P1] **WS 端点可匿名连**：`api/routers/market.py:444-469` `@router.websocket("/ws/market")` 无任何鉴权（无 token、无 X-Internal-Key 检查）；且 FastAPI 的 `@app.middleware("http")`（`main.py:100-106`）**不拦截 WebSocket**——即使配置了 `internal_api_key`，WS 依然裸奔。连接即触发 `_db_quotes(limit=100)` 的同步 DB 查询（:448）。
- [P2] `realtime/manager.py:9-29` — `list[WebSocket]` 无锁、无心跳/ping-pong、无每连接订阅模型；`broadcast`（:21-26）对慢客户端 `send_json` **无背压**——一个慢连接会阻塞整个事件循环的广播；断连仅靠发送异常被动清理。
- [P2] `main.py:62-71` — lifespan 后台任务每 5s 在**事件循环内同步执行** `_db_quotes(limit=100)`（含 100 标的 × 2 次查询的 N+1，见 `market.py:34-43`），期间阻塞所有异步处理（WS 推送、其他请求）。
- [P3] `market.py:465-469` — WS 接收循环仅捕获 `WebSocketDisconnect`；其他异常（如 send 失败）不触发 `manager.disconnect`，僵尸连接残留列表。

---

## 5. 券商适配

- [P1] `brokers/live.py:15-32` — **纯空壳占位**：`enabled=False` 恒拒单（:20-25）；置 enabled 后 `place_order`/`cancel_order` 直接 `raise NotImplementedError`（:26,:29）；`get_order_status` 恒 REJECTED（:31-32）。**未对接任何真实券商**——正面看是安全护栏（杜绝误下真单），负面看"实盘"能力为零，任何 LIVE 模式订单都会 REJECTED。`api/dependencies.py:89` 将其注册为 ExecutionMode.LIVE 的 broker。
- [P3] `brokers/paper.py:19-28` — 按内存行情现价即刻全额成交，无滑点/费用/部分成交；`get_order_status` 恒 FILLED（:33-34）。作为纸面回放可接受，但与 simulated 的语义（滑点/流动性/挂单）不一致。
- 正面：`brokers/simulated.py`（258 行）撮合引擎质量较好——分档滑点/流动性部分成交（:34-38, 213-226）、限价挂单与 `process_pending`（:86-100）、费用模型（:190-196）、幂等（:53-54）。仅 [P3] 小问题：部分成交后剩余量以同一 order_id 重新挂单（:168），`process_pending` 会为该 order_id 产生**多个**成交结果，调用方若按 order_id 去重会漏后续成交。

---

## 6. 密钥与配置（泄漏面清单）

### 6.1 已提交进 git 的真实凭据（最严重）

| 文件:行 | 内容 | 级别 |
|---|---|---|
| `scripts/import_v1_production.py:18-23` | **V1 生产库口令 `lmkj929292`**（CN/US/HK/CRYPTO 四库）+ V2 `zhice_pass`；该文件已被 git 追踪（`git ls-files` 确认） | **P0** |
| `scripts/backfill_fill_pnl.py:6-11` | `password=lmkj929292` / `zhice_pass` 直连串，git 追踪 | **P0** |
| `scripts/backfill_v1_replays.py:6-7` | 同上 | **P0** |
| `scripts/backfill_sell_reason.py:11` | `password=zhice_pass` | **P0** |

### 6.2 源码内硬编码密钥 / 口令默认值

| 文件:行 | 内容 | 级别 |
|---|---|---|
| `application/services/token_service.py:11` | JWT 签名密钥 `_SECRET_KEY = "zhice-os-v2-dev-secret"` 硬编码 → 任何读到源码者可直接伪造任意角色（含 ADMIN）token | **P0** |
| `config/settings.py:26` | `admin_password: str = "123456"` 默认登录口令（未设 ADMIN_PASSWORD 即生效；`auth_service.py:19-21` 校验） | **P1** |
| `config/settings.py:31` | `db_main_url` 默认值含 `zhice:zhice_pass` | **P2** |
| `alembic.ini:4` | `sqlalchemy.url = postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2` | **P2** |
| `scheduler_jobs.py:30` | `_V2_DB_PASSWORD = os.getenv("V2_DB_PASSWORD", "zhice_pass")` —— 注释（:22）自称"源码不再保留任何口令默认值"，同文件 8 行后就自相矛盾 | **P1** |
| `docker-compose.yml:6,17` | `POSTGRES_PASSWORD: zhice_pass`、`DB_MAIN_URL` 内嵌口令 | **P2** |

### 6.3 本地明文凭据（未提交，但以明文落盘）

- `.env.local`（gitignore 已忽略，`git check-ignore` 确认未入库）内含**真实有效**凭据：`V1_DB_PASSWORD=lmkj929292`、`V2_DB_PASSWORD=zhice_pass`、`DATACENTER_API_KEY=ak_E3UNvs3wH-sKWx3ulB1oLw`、`TUSHARE_TOKEN=173e4e0e...`、`FINNHUB_TOKEN=d9t7lq1r01qqd...`（.env.local:3-13）。引用方式：`settings.py:11-16` 由 pydantic 读 `.env.local`，`scheduler_jobs.py:25` 显式 `load_dotenv(".env.local")`。风险：本地文件即明文，若机器/备份泄露即全量泄露。[P1]

### 6.4 鉴权面（与密钥直接相关）

- [P0] **API 默认全裸**：`main.py:100-106` internal_key 中间件仅当 `settings.internal_api_key` 非空才生效，而默认值为空串（`settings.py:22`）、`.env.local` 与 docker-compose 均未设置 → **默认部署下该中间件是 no-op**；除 `auth.py`（仅 `/auth/me`、`/auth/users`）外，**其余全部路由（strategy/positions/trade/workflow/reports/governance 等）无任何鉴权依赖**（grep 确认 `require_roles|get_current_user|get_token_data` 只出现在 auth.py）。
- [P1] `Dockerfile` CMD：`uvicorn ... --host 0.0.0.0 --port 8002`，compose 暴露 8002 → 若部署在可达网络，整个交易 API 匿名可访问。
- [P3] `api/routers/auth.py:18-21` 登录接口无速率限制/失败锁定；`login(password: str = "")` 在 `admin_password` 为空时允许空口令登录（当前默认非空，但该分支存在）。
- [P3] `token_service.py:46-57` `decode_token` 不校验 header 的 alg 字段（固定按 HS256 + 硬编码密钥验签）；payload 无 `nbf`/`iss`。

---

## 7. 建议修复优先级（事实推论，非代码改动）

1. 先补迁移：`v2_strategy.description`、`v2_bar_daily.pre_close`、`v2_fill_record.realized_pnl/sell_reason`、`v2_learning_case.status`（4 列，P0 运行时错误）；再为 12 张缺表补 0008 迁移（一次性 create_table），并删除 0002 的重复 `v2_market_calendar` 建表或将其改为 IF NOT EXISTS 语义（P1 阻断 fresh 部署）。
2. 轮换所有已入库口令（`lmkj929292` 等）并 purge git 历史；JWT 密钥改环境变量注入。
3. 默认关闭 0.0.0.0 绑定或强制 internal_api_key；为 WS 端点补 token 鉴权（握手头）。
4. 消除网关/查询层的裸 `except Exception: return []`，至少区分"源故障"与"无数据"并打日志。
5. 统一连接管理：所有 psycopg2 直连改为走 `_engine()`；WS 广播加每连接队列/心跳。

---
*报告生成：基于仓库当前工作树（含未提交改动）；迁移对比为脚本实测结果（执行 upgrade() 提取 schema），非人工目测。*
