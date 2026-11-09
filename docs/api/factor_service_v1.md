# 因子服务 API v1（P1）

实现：`portal/api_factor.py`（路由/校验/缓存）、`portal/factor_worker.py`（子进程计算）。
挂载：8091 门户 API，统一前缀 `/api/factor/`。
本版本只读目录 + 受限裸因子值计算；不写数据库、不联网、不做中性化/组合/评测。

## 端点

### GET /api/factor/health

返回服务、目录版本、因子数、数据新鲜度、缓存状态。

### GET /api/factor/catalog

查询参数：

- `family`：精确家族过滤，如 `Tushare-动量`
- `q`：名称/描述子串
- `limit`：默认 500，最大 500
- `offset`：默认 0

返回 `catalog_version`、`total`、`families`、`factors`（name/family/kind/direction/description/source/requires/status）。

### GET /api/factor/catalog/<name>

单因子元数据；不存在返回 404。

### POST /api/factor/compute

需要 `X-Portal-Token`（本机直连放行；反代/局域网必须带）。

请求：

```json
{
  "factors": ["return_5d", "b1_mom_21d"],
  "start": "20261101",
  "end": "20261106",
  "universe": "all",
  "codes": ["000001.SZ"],
  "limit": 50000,
  "format": "long"
}
```

约束：

- `factors` 1–20 个，不能重复、必须是已注册因子
- `start/end` 为 `YYYYMMDD`，区间 ≤366 天
- `codes` 可选，最多 300 个，格式 `^\d{6}\.(SH|SZ|BJ)$`
- `universe`：`tradable` / `all`
- `limit`：1–50000；超出截断并返回 `truncated=true`
- `format`：P1 仅 `long`

响应 `data`：

```json
{
  "catalog_version": "sha256...",
  "factor_lib_version": "1.2.0",
  "generated_at": "...",
  "data_freshness": {"daily": {"latest": "20261106"}},
  "request": {"factors": ["return_5d"], "start": "20261101", "end": "20261106"},
  "rows": [["20261102", "000001.SZ", "return_5d", 0.02123894]],
  "count": 1,
  "truncated": false,
  "elapsed_ms": 2087,
  "cache_hit": false
}
```

`rows` 为 `[trade_date, ts_code, factor_name, value]`。

## 缓存与隔离

- 缓存键：`sha256(catalog_version + latest_daily + normalized_request)`
- TTL 300s；最多 8 条；单条 ≤5000 行才缓存
- 计算在独立子进程执行，父进程超时 120s；并发上限 2，超限 429
- 子进程只读 `quant_backtest/data/tushare`，不写库、不联网

## 说明

- 因子值在请求时实时计算，不是持久化 factor value 表；历史大范围查询需自行分页/缩小范围。
- 当前返回原始因子值（未中性化）；中性化、IC/组合评测属于后续 P2。
- 该路由需 8091 进程重启/自然重启后生效；本文件不自动重启服务。
