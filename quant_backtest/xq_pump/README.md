# xq_pump —— 雪球增量抽水（免授权、逐步增量）

把 `quant_backtest/` 下散落的 `xq_*` / `xueqiu_*` 抽水爬虫**收口成一个干净的增量抽取包**。

定位：**只抓数据、逐步增量、不碰治理层**。剥离了原体系的动态代理池（`xq_proxy`）、
浏览器登录/`cookie` 收割（`xq_login`/`xq_browser`/`xq_cookie_patch`）、请求闸
（`xq_request_gate`）、以及 `intel_collector` 的 RightsGate/XQ_LIVE_ARMED 授权闸。
仅保留「能拉到数据的最小集」：`xueqiu_common` 的 token 注入 + 2s 抖动限速 + 403 熔断冷却 + 健壮 GET，
行情/K 线走 `pysnowball`（非 HTTP）。

## 5 类抽取（均已落地 + 单测覆盖）

| 类型 (`--type`) | 抽什么 | 端点 / 来源 | 游标 / 增量机制 |
|---|---|---|---|
| `posts` | 公开帖/舆情（类目时间线） | `/v4/statuses/public_timeline_by_category.json` | `max_id`+`next_max_id` 翻页；`xq_status.status_id` seen 去重；`exhausted` 标记到底 |
| `hot` | 热门榜 | `/statuses/hot/listV2.json` | `since_id`/`max_id` 游标 + seen 去重；落 `xq_hot_post` |
| `quotes` | 行情快照 | `pysnowball.quotec` | 按 `(symbol, ts)` 覆盖写；每次追加「当前时点」截面，可回溯 |
| `kline` | K 线 | `pysnowball.kline` | 按 `(symbol, date)` 覆盖写；拉最近 N 根，新日期追加 |
| `timeline` | 用户时间线 | `/statuses/user_timeline.json` | `last_id` 游标 + seen 去重（同落 `xq_status`，`source='user_timeline:<id>'`）|

`all` = 依次跑 `posts` → `hot` → `quotes` → `kline`（timeline 需 `--user-id`，不纳入 all）。

## 用法

从 `quant_backtest/` 下执行：

```bash
# 看帮助
python -m xq_pump.cli --help

# 只验证配置/接线，不实际抓（默认非 live，用 FakeSession）
python -m xq_pump.cli run --type posts --dry

# 真实抓取公开帖（需网络 + 雪球 cookie）
export XQ_COOKIE="你的_xq_a_token_或完整cookie"
python -m xq_pump.cli run --type posts --category 102 --max-new 200 --live

# 行情 + K线
python -m xq_pump.cli run --type quotes --symbols SH600519,SZ000001 --live
python -m xq_pump.cli run --type kline  --symbols SH600519 --period day --count 240 --live

# 全量增量（按数据根逐步累积）
python -m xq_pump.cli run --type all --data-root ./xq_pump/data --live
```

参数：`--type`(必填) / `--category`(逗号分隔，默认 102,104,105,111) / `--symbols` /
`--user-id` / `--period` / `--count`(K线条数或每页条数) / `--max-new` / `--max-pages` /
`--data-root` / `--db` / `--cursor-root` / `--cookie` / `--proxy` / `--live` / `--dry`。

## 数据落点（可配置，无硬编码路径）

- **库**：默认优先复用 `quant_backtest/xueqiu_info.db`（若已存在，保持历史数据连续性）；
  否则新建到 `<data-root>/xq_pump.db`。可用 `--db` 显式指定。
- **断点**：`<data-root>/cursors/<type>_cursor.json`，原子写（`.tmp`+`os.replace`）。
  断点维度：`posts` 按类目、`hot` 按 `all`、`timeline` 按 `user_id`。
- 下次运行自动从断点续翻，**只拉断点之后的新数据**。

## 包结构

```
xq_pump/
  core.py          # XqSession 抽象；LiveSession(真实)/FakeSession(测试)；常量；ms_to_dt
  cursor.py        # CursorStore：增量断点读写（每 type 一文件，原子写）
  store.py         # Store：SQLite 幂等 upsert + seen 集；表结构对齐 xueqiu_common
  extractors/
    _parse.py      # 响应归一化（兼容 {data:"{...}"} 嵌套 与 扁平两种结构）
    posts.py hot.py quotes.py kline.py timeline.py
  cli.py           # python -m xq_pump.cli run ...
  tests/           # 17 测试：cursor/store/posts/extractors（FakeSession 纯逻辑）
```

## 与旧体系的关系

- **复用**：`xueqiu_common` 的 token 三级注入、`jitter_sleep` 限速、`xq_get` 健壮 GET、`XqIpBlocked`
  熔断透传、`norm_symbol` 归一。
- **剥离**：代理池 / 浏览器登录 / cookie 收割 / 请求闸 / RightsGate 授权 —— 用户已裁定「不需要授权」。
- **未改动**：`quant_backtest/` 下既有 `xq_*` 脚本原样保留，本包是独立的整理产物，互不污染。

## 红线与未验证项（不冒充完成）

- **真实网络未验证**：沙箱无外网，`--live` 路径（含 `xueqiu_common`/`pysnowball` 真实调用）
  仅经语法 + 接线冒烟，未跑通真实抓取。首次真实运行可能遇到匿名被 WAF 拦（需 `--cookie`）。
- **pysnowball 行情/K线**：依赖用户侧已装 `pysnowball`；`quotes`/`kline` 的解析按
  `pysnowball` 返回 `{data:{items/column,item}}` 结构，真实字段名若不同需微调 `_parse`。
- **未提交 git**：本次整理未做版本提交（如需要请告知）。
- **与 intel_collector 关系**：本包是「采集合成/落库」层，不接入 `intel_collector` 的治理
  （RightsGate/SLA/PII/反馈环）。如需把抽水结果喂入治理平台，后续再接适配器。
```
