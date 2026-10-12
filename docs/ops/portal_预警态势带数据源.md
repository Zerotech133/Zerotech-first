# 预警态势带 · 数据源说明（K59 建立，K60 大屏 v4 更新，2026-10-04）

大屏 `/screen`（8080，K44 静态版）顶部的超级导航卡带。**每张卡 = 一个数据源 + 一个下钻入口**。
渲染链：上游任务落盘 → `build_alerts()` / `build_daily_pulse()` 读文件锚 → `/api/screen`（60s 轮询，`SCREEN_TTL=60` 缓存）→ 前端 `renderThreat()`。
零重量级调用：不碰 schtasks、不扫 parquet，全部读 json/jsonl 快照。

> **K60 布局注记**：大屏 v4「态势指挥屏」为三段式——态势带之下依次为 ①选股罗盘迷你卡行（六卡，`renderCompass`）②主战场（左「当日买卖点」主表前 20 + 右「今日预警速览」「缠论尾15」竖叠）③资产与健康（组合总览/持仓速览/每日更新流水）。本节三张下钻表即位于主战场段；态势带六卡数据源不受改版影响。

## 一、六卡数据源总表

| 卡片 | value 口径 | Δ | 数据文件 | 生产任务（时刻） | 点击下钻 |
|---|---|---|---|---|---|
| **盘前倾向**（大卡） | `verdict` 截「（」前（偏多/偏空/中性） | — | `reports/premarket_latest.json` | 量化盘前作战计划 **14:15**（AI盘前分析 14:30 二次消费） | `/premarket` 六层详情 |
| 缠论信号 | 最新检出日 `买 N · 卖 M`（type 尾字计数） | 当日买数 − 前检出日买数 | `quant_backtest/data/chanlun_pool.jsonl` | ChanlunScan **05:50** | `/bt-chanlun-pool` 预警池 |
| 九维警报池 | `n_alert` 只（综合分 ≥ `alert_th`(75) 入池） | — | `quant_backtest/data/nine_dim_scores.json` | NineDimScan **06:40** | `/bt-9dim` |
| 弱于板块在案 | 活跃案数：`weak_track_*.jsonl` 排除 `.closed_` 且末行可解析 | — | `reports/weak_track_*.jsonl` | 预警监听周期跟踪（06:00 起） | `/bt-weak` 建案→跟踪→结案 |
| 共振做多 | `n_hit≥2 且 state=long 且 n_long≥2` 计数（与共振页同口径） | — | `cross_poll.build_cross()` 内置 60s 缓存 | AI量化RPS共振 **23:35** | `/bt-cross` |
| 数据链健康 | 八任务锚 `ok N / 8`（exit 码/json ts/尾行时间） | — | 各任务日志 + 快照（见 K57） | 夜链 23:30 → 晨链 07:10 | `/bt-data-health` 数据仓 |

## 二、盘前六度色条（six 字段）

`premarket_latest.json` 的 l1–l6 六层评分，取值与着色：

| 格 | 来源层 | 分值 | 上游构成 |
|---|---|---|---|
| 外围 | l1 | `vals["外围"]` | 隔夜美/港/亚太指数 + A50 |
| 政策 | l2 | `vals["政策"]` | 宏观数据 + 财经大事记 + 新闻情绪 |
| 流动性 | l3 | `vals["流动性"]` | 两融 + 北向 + Shibor |
| 板块 | l4 | `vals["板块"]` | 美股→A股板块映射联动 |
| 情绪 | l5 | `vals["情绪"]` | 涨跌家数/涨停梯队 + cn_env + VIX |
| 公告研报 | l6 | `v=null, n=公告+研报总数`（显示 ×N） | ann_stats/rpt_stats |

- 着色：**>0 红（偏多）、<0 绿（偏空）、0/缺 灰**；`overall` = 六层综合分（卡内不显，详情页见）。
- 大字 verdict：偏多=红 / 偏空=绿 / 中性=灰（中式红涨绿跌）。
- 假期「公告研报 ×0」为 T+1 披露节奏，开盘日自动有数。

## 三、行级超级导航（表格行 onclick）

| 表 | 行点击目标 | 说明 |
|---|---|---|
| 今日预警速览 | 该路 `href` | 后端 build_alerts 五路自带 |
| 缠论池最新 15 票 | `/bt-linkage?code=<code>` | 该股三线联动图（洋红+缠论结构） |
| 当日买卖点 | `/bt-linkage?code=<code>` | 同上；⚡=1卖 或 tbe≥80 强预警 |

## 四、新鲜度与节奏

- 大屏轮询 60s；态势带数据随 `/api/screen` 刷新（缓存 TTL 60s）。
- 上游刷新节奏：缠论 05:50 → 九维 06:40 → 盘前 **14:15**（盘中数据次日更新）→ 共振 23:35。
- 顶栏「数据锚 N/M 新鲜」徽章 = 15 表 source_fresh 统计（`data_freshness_bg(1800)` 缓存热同步，启动后 ~1min 预热）。

## 五、排障速查

- 某卡消失 = 该路 json 缺失/异常（build_alerts 单项 try/except 缺项不拖垮）→ 查上表数据文件是否生成、任务是否跑（exit= 码）。
- 六度全灰/×0 → premarket_latest.json 未更新（14:15 任务失败或节假日）。
- 卡片不可点 → 前端 renderThreat 未渲染（查浏览器控制台 JS 错误）。
- 验证命令：`curl http://127.0.0.1:8091/api/screen | python -m json.tool` 看 `alerts[].six` / `pulse`。
