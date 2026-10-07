# 数据仓健康盘点（Data Warehouse Health）实施方案

## Context

金融策略集市（盘前/共振/缠论/九维）全部跑在 `quant_backtest/data/` 之上，但数据底座当前不透明：
- A 股 K 线 5592 只（SH/SZ/BJ 混排平铺），最后更新 2026-09-26，9/27 增量缺失未被发现
- 美股/港股/加密 K 线目录不存在，盘前 L1 的恒指、JEV 的美股/港股判断没有本地数据验证
- 新闻 news.db 2040 条（9/25~9/27），无公告/调研/研报数据源
- 策略产物（缠论池/九维/回测）时间戳分散，无法一眼看出「哪些数据新鲜、哪些过期」

目标：新建 `/bt-data-health` 数据仓健康页 + 新增 `data_health.py` 扫描器，让数据底座「可观测、可下钻、可追责」。

## 推荐方案

### 1. 新建 `quant_backtest/data_health.py`（数据仓扫描器）

纯函数 `scan_data_health() -> dict`，一次性扫描并返回结构化健康报告：

```python
{
  "generated_at": "2026-09-27 10:00:00",
  "kline": {
    "cn": {"count": 5592, "sh": 2332, "sz": 2912, "bj": 348,
           "latest_file": "2026-09-26", "size_mb": 257.0,
           "avg_rows": 300, "missing_fields": ["turnover", "market_cap", "pe"]},
    "us": {"count": 0, "exists": false},
    "hk": {"count": 0, "exists": false},
    "crypto": {"count": 0, "exists": false}
  },
  "news": {
    "total": 2040, "date_range": "2026-09-25 ~ 2026-09-27",
    "sources": {"sina": 1303, "eastmoney": 430, "wallstreetcn": 307},
    "jev_judged": 44, "jev_pending": 1996,
    "has_announcement": false, "has_research": false, "has_report": false
  },
  "strategy_artifacts": [
    {"name": "缠论预警池", "file": "chanlun_pool.jsonl", "mtime": "2026-09-26 22:26", "age_hours": 12},
    {"name": "九维评分", "file": "nine_dim_scores.json", "mtime": "2026-09-27 06:40", "age_hours": 3},
    ...
  ],
  "gaps": [
    "美股 K 线缺失：盘前 L1 恒指/纳指/标普无本地验证",
    "港股 K 线缺失：JEV 港股判断无数据支撑",
    "A 股 K 线缺换手率/市值/PE 字段",
    "新闻无公告/调研/研报源",
    "news.db stocks 关联字段疑似空转（待确认）"
  ]
}
```

**关键设计**：
- 目录探测用 `pathlib.Path.rglob("*.parquet")`，按 `_SH/_SZ/_BJ` 后缀分类计数
- 单文件抽样读 1 只 parquet 验证行数和字段（用 `pyarrow.parquet`）
- news.db 用 `sqlite3` 读表结构 + `MIN(ts)/MAX(ts)` + `GROUP BY source`
- 策略产物清单硬编码已知文件名（chanlun_pool.jsonl / nine_dim_scores.json / rps_latest.json 等），读 `os.path.getmtime`
- gaps 列表由代码硬编码已知缺口（基于本次盘点），后续新缺口手动追加

### 2. 门户新增 `/bt-data-health` 页面

- 路由注册进 `FIN_NAV_GROUPS` 的「跟踪」组
- 页面结构：
  - **顶部 KPI 卡**：A股数量 / 美股(0) / 港股(0) / 加密(0) / 新闻条数 / 最后更新时间
  - **数据覆盖矩阵**：市场 × 数据类型（K线/新闻/公告/调研）的 ✓/✗ 表格
  - **缺口清单**：红色高亮，每条带影响说明
  - **策略产物时间戳表**：文件名 + 最后更新 + 距今天数，超过 24h 的标黄
  - **下钻**：A股 K 线 → `/bt/m/cn` 市场页；新闻 → `/bt-jev`；策略产物 → 对应工作台
- 样式复用 `_COCKPIT_CSS` + `.sec` 折叠区块

### 3. /fin 首页 C 区「工具与横切」加入口

- 在工具区加一行：`📊 数据仓健康 → /bt-data-health`

### 4. 单元测试 `test_ui_drilldown.py` 新增 `TestDataHealth`

- `test_scan_returns_structure`：mock 目录结构，验证返回 dict 的 key 完整性
- `test_kline_count_and_market_split`：mock parquet 文件名，验证 SH/SZ/BJ 分类计数
- `test_news_db_stats`：mock sqlite3，验证 total/sources/date_range
- `test_gaps_list_populated`：验证 gaps 非空且包含已知缺口关键词
- `test_page_renders_and_drills`：mock scan 结果，验证页面渲染 + 下钻链接存在

## 关键文件

| 文件 | 动作 |
|---|---|
| `quant_backtest/data_health.py` | 新建：扫描器 |
| `portal/portal_api.py` | 新增 `_bt_data_health_page()` + 路由注册 + /fin C 区入口 |
| `quant_backtest/test_ui_drilldown.py` | 新增 `TestDataHealth` 测试类 |

## 验证方式

1. `python -m unittest test_ui_drilldown -v` 全过
2. 重启门户，访问 `http://127.0.0.1:8091/bt-data-health`
3. 确认页面显示：
   - A股 5592 只（SH 2332 / SZ 2912 / BJ 348）
   - 美股/港股/加密 0 只，红色高亮
   - 新闻 2040 条，来源分布
   - 缺口清单至少 3 条（美股/港股/加密缺失）
4. 点击 A股 K 线下钻到 `/bt/m/cn`，确认市场页正常

## 后续扩展（本方案不做，仅预留）

- 增量更新检测：对比 parquet 最新 trade_date 与今天日期，自动标记「过期」
- 公告/调研/研报源接入：在 gaps 清单中标记「待接入」，接入后自动移除
- 数据质量评分：基于完整性/时效性/字段丰富度打分，低于阈值告警
