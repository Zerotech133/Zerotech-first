# 可配置全市场扫描（分批 + 限流退避 + 历史回补）设计

- 日期：2026-09-13
- 分支：`feat/full-market-scan`（基于 main 9efb5b1）
- 生产：207.56.221.140（两市场 `SCHEDULER_MARKETS=US,CRYPTO`、legacy）。见 [[207-prod-ops]]。**未经明确授权不部署**。

## 1. 背景（重要更正）

排查发现"策略扫描不是全市场"的**真正瓶颈在取数，不在扫描**：

- **盘前 registry 链（调度器主链，真正下单）**：`run_pre_market_job → WorkflowService.run_pre_market → StrategyRegistryService.run → selector → data_loader.load_recent_bars`。**不读 `spec.universe`**，直接扫 `v2_bar_daily`⋈`v2_instrument` 该市场"所有有数据的标的"（近 90 天，无 top_n 截断）——**已是全市场扫描**。
- **candidate_service 链**（`spec.universe["codes"]` 写死清单，US 60）：API/回测/寻优触发的旁路，**不在调度器交易主链**。不改。
- **卡点 = 取数**：`run_fetch_daily_job(US)` 每日只给 `snapshot_universe(top_n=500)`（换手 ∪ 持仓 ∪ 候选）的美股拉新日线（Nasdaq 逐 code 会限流，故有意限量）。于是库里 5920 只 US 只有 ~557 只在更新、历史仅 ~5 个月 → 扫描器只能扫到这些。

**目标**：让**取数**覆盖全市场（分批 + 限流退避 + 分时扰平）并**回补 1 年历史**，使既有全市场扫描器实际覆盖全市场。扫描逻辑不改。

**范围/决策（用户拍板）**：扩宽取数到全市场 + 回补历史；US 用限流退避+分时扰平（保留 Nasdaq/yfinance 源，不换源）；回补 **1 年**（207 现跑 TREND/VIBE_EXTERNAL，**无缠论**，1 年足够）；全部硬编码值改为 env 可配。**缠论仅在 CN，207 不开 CN——给 US/CRYPTO 上缠论是独立后续任务，不在本范围**。

## 2. 架构与关键事实

- 现有取数 job `run_fetch_daily_job(market, limit=5)`（`scheduler_jobs.py`）：US/CRYPTO 宇宙=`snapshot_universe(top_n)`，CN/HK=`daily_universe`（全 ACTIVE）；`_chunked(codes,200)`，**批间无 sleep、网关无内建退避**。
- `daily_universe(market)`（`market_data/universe.py`）=`SELECT symbol FROM v2_instrument WHERE market=%s AND status='ACTIVE'`（全集，现成）。
- 落库 `ingest.upsert_daily`：唯一键 `(market,symbol,trade_date)`，`ON CONFLICT DO UPDATE`（**幂等**）；`backfill_pre_close(market)` 用 lag 反填非 CN 的 pre_close。
- 网关 `get_daily_bars_batch(ts_codes, limit, start, end)`（`gateways/{us,crypto}.py`）：US 逐 code(Nasdaq→yfinance 兜底)、CRYPTO 逐 code(Binance→Gate)。`continuity.backfill_daily_gaps` 已示范 per-symbol 带 start/end 拉取（limit_cap=400）。
- 跨进程状态 `v2_app_state`：经 `api/routers/app_state_store._load_state/_save_state`（key→JSON，DB+内存回退）。游标复用它，**无需迁移**。
- 配置 `settings.AppSettings`（Pydantic BaseSettings，`.env.local`）；`scheduler_markets` 是"逗号分隔市场开关"先例。当前 top_n/批大小/limit/频率全硬编码——本次提出为配置。

## 3. 配置项（settings.AppSettings 新增，env 可调，默认保持现状）

```python
fetch_daily_full_markets: str = ""      # 逗号分隔启用全市场慢抄的市场；空=不启用(保持 top_n)。生产设 "US,CRYPTO"
fetch_daily_batch_size: int = 200       # 慢抄/取数分批 size
fetch_daily_throttle_ms: int = 0        # 批间 sleep(ms)；慢抄建议 800
fetch_daily_sweep_slice: int = 500      # 每次慢抄 job 处理多少标的(游标切片)
fetch_backoff_max_retries: int = 5      # 限流退避最大重试
fetch_backoff_base_ms: int = 1000       # 退避基数(指数 base×2^n，封顶 ~30s)
backfill_daily_days: int = 365          # 历史回补深度(天)
```
解析 helper：`_full_markets() -> set[str]` = 拆 `fetch_daily_full_markets`（空→空集）。

## 4. 限流退避 helper

新增 `src/zhice_os_v2/infrastructure/market_data/throttle.py`：
```python
_RATE_KEYWORDS = ("429", "rate", "limit", "too many", "每分钟", "每天", "抱歉")

def is_rate_limited(exc: Exception) -> bool:
    return any(k in str(exc).lower() or k in str(exc) for k in _RATE_KEYWORDS)

def fetch_daily_chunk(gateway, chunk, *, limit, start=None, end=None,
                      max_retries, base_ms) -> dict:
    """调 get_daily_bars_batch；限流信号→指数退避重试；超次→抛/上层跳过。"""
    for attempt in range(max_retries + 1):
        try:
            return gateway.get_daily_bars_batch(chunk, limit=limit, start=start, end=end)
        except Exception as e:
            if attempt < max_retries and is_rate_limited(e):
                time.sleep(min(base_ms * (2 ** attempt), 30000) / 1000)
                continue
            raise
```
调用点批间 `time.sleep(throttle_ms/1000)`（throttle_ms>0 时）。单批彻底失败 → 记 error、continue，不中断整轮（沿用 minute job 容错）。

## 5. 全市场慢抄任务

`scheduler_jobs.run_full_market_daily_sweep_job(market) -> dict`：
- `if market not in _full_markets(): return {"swept": 0}`（未启用 no-op）。仅 sql 后端。
- `codes = daily_universe(market)`（全 ACTIVE，按 symbol 排序稳定）。
- 游标：`app_state_store._load_state("daily_sweep_cursor") or {}` → `off = state.get(market, 0)`；`slice = codes[off: off+sweep_slice]`；处理后 `off2 = (off+len(slice)) % len(codes)`（到底回绕），`_save_state("daily_sweep_cursor", {**state, market: off2})`。
- 处理 slice：`_chunked(slice, batch_size)` → `fetch_daily_chunk(g, chunk, limit=5, max_retries, base_ms)` + `sleep(throttle_ms)` → `upsert_daily(rows, market)`；结束 `backfill_pre_close(market)`。
- `limit=5`（增量：慢抄保证全市场每日都有新收盘；历史深度由回补脚本一次性铺）。
- 返回 `{"swept": n, "written": w, "errors": e, "cursor": off2}`；有 error 或写入 logger.info。
- **注册**（`scheduler.py`）：对 `_full_markets()` 里的市场，盘后窗口 `IntervalTrigger(minutes=4)`（几小时扫完全市场，与其它 job 错峰），`max_instances=1, coalesce=True`。**保留** `run_fetch_daily_job`(top_n) 不动（热门/持仓/候选高频刷新，与慢抄互补，均幂等）。

## 6. 历史回补（1 年）

`src/zhice_os_v2/infrastructure/market_data/backfill.py` 的 `backfill_daily_history(market, *, days, slice_size, batch_size, throttle_ms, max_retries, base_ms, max_symbols=None) -> dict`：
- `codes = daily_universe(market)`；游标 `backfill_daily_cursor:{market}`（v2_app_state，可续跑）。
- 逐标的**先判覆盖**：查 `SELECT min(trade_date) FROM v2_bar_daily WHERE market=? AND symbol=?`；若 `min ≤ today-days` → 已够，跳过（计 skipped）。否则 `fetch_daily_chunk(g, [sym], limit=days+15, start=today-days, end=today)` → `upsert_daily`。
- 节流+退避+幂等；`max_symbols` 限本次处理量（脚本可多次跑到完）。结束 `backfill_pre_close(market)`。
- 返回 `{"processed", "written", "skipped", "errors", "cursor"}`。
- **交付 = 脚本** `scripts/backfill_daily_history.py`（argparse `--market --days --max-symbols --throttle-ms`），后台盘后节流跑；跳过已覆盖 → 可重入续跑至全覆盖。

## 7. 测试策略

- 全部**mock 网关，零真实外网**；sqlite/内存 + mock `get_daily_bars_batch`。
- 配置：`_full_markets()` 解析（空/逗号/空格）、默认值不改变现状。
- 退避 helper：限流异常→退避重试并最终成功；超 max_retries→上抛；非限流异常→立即上抛；`is_rate_limited` 关键词命中。
- 慢抄 job：未启用市场→no-op(swept 0)；游标推进；到底回绕；slice 切片正确；`upsert_daily` 被调；单批失败不中断。
- 回补：已覆盖(min≤today-days)→skip；未覆盖→按 [today-days,today] 取并 upsert；游标续跑；二次跑幂等（无重复写/coverage 稳定）；max_symbols 限量。
- universe 选择：慢抄用 `daily_universe`(全) vs 现 top_n 各行其道。
- 沿用 `test_scheduler_jobs_registration` 加"sweep job 在启用市场注册"断言。
- **无迁移**（游标用 v2_app_state）。

## 8. 门禁与部署

- 门禁：`pytest -q` + `ruff check src tests` + `mypy`(本特性文件无新增债) ；`alembic heads` 不变（无迁移）；前端不动。
- 207 部署：① build api 镜像(慢~7min，改前 `docker tag ...:rollback-*`)→停 api/scheduler→(无迁移)→`up -d`（**连带 nginx 避 502**）；② `.env` 加 `fetch_daily_full_markets=US,CRYPTO`、`fetch_daily_throttle_ms=800`；③ **后台跑一次回补** `python scripts/backfill_daily_history.py --market US --days 365`（再 CRYPTO，本就近全很快）；④ 慢抄 job 随 scheduler 自动跑。
- 验收：`v2_bar_daily` 各市场 distinct symbols 涨到接近 `v2_instrument` 全集、min(trade_date) 退到 ~1 年前、无限流告警；registry 盘前扫描候选数随之扩大。
- 回滚：`fetch_daily_full_markets=""` → 慢抄 no-op、回到 top_n 行为；代码 rollback tag；数据只增不删（幂等 upsert）无需回滚。

## 9. 单元划分（供 writing-plans）

- **单元 1（配置 + 退避 helper）**：settings 新字段 + `_full_markets()` + `throttle.py`（`is_rate_limited`/`fetch_daily_chunk`）+ 单测。
- **单元 2（全市场慢抄）**：`run_full_market_daily_sweep_job` + scheduler 注册 + 游标 + 单测。
- **单元 3（历史回补）**：`backfill.py` `backfill_daily_history` + `scripts/backfill_daily_history.py` + 单测。
- **单元 4（门禁总核 + 部署手册补记）**。

## 10. YAGNI / 不做

- 不改 registry 扫描逻辑（已全市场）、不改 candidate_service（旁路）、不换数据源、不给 US/CRYPTO 上缠论、不做分钟线全市场（只日线）、不加迁移。
