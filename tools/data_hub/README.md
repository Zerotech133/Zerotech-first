# data_hub · 全网数据源接入与治理

> 2026-11-05 起用。解决两件事：**全网数据从哪取、出口怎么走**；**取来的东西对不对得上账**。  
> 一句话定位：这是"来源侧"的登记与体检，不是"数据侧"的仓库。

## 一、为什么要有这一层（实测驱动，不是设计洁癖）

国内源和海外源不能用同一条出网路径。2026-11-05 在本机做的对照实验：

| 源                     | 直连        | 走环境变量代理                |
| --------------------- | --------- | ---------------------- |
| `api.tushare.pro`     | **1.1s**  | 12.3s                  |
| `d.10jqka.com.cn`     | **0.96s** | ReadTimeout 6.7s       |
| `push2.eastmoney.com` | 连接重置      | ProxyError             |
| `api.binance.com`     | 不通        | 不通（环境代理），**本地 7890 通** |

`akshare` / `requests` 默认 `trust_env=True`，会继承宿主注入的 `http(s)_proxy`，  
把国内 A 股源也塞进代理——这是当前国内源"慢、偶发失败"的根因。

**结论：出口路径是数据质量的一部分，必须按源声明、实测择优、落盘记忆，不能靠全局环境变量。**

`net.py` 因此提供四条策略：

| 策略       | 行为                                                          | 适用          |
| -------- | ----------------------------------------------------------- | ----------- |
| `direct` | `trust_env=False`，清空 proxies                                | 国内 A 股源（默认） |
| `env`    | 跟随环境变量                                                      | 企业/沙箱统一出口   |
| `local`  | 强制 `127.0.0.1:7890`（可用 `DATA_HUB_LOCAL_PROXY` 覆盖）           | 海外源         |
| `auto`   | direct → env → local 实测择优，胜出路径写 `state/egress.json`（24h 有效） | 不确定的源       |

`auto` 只在首次试错，之后直接命中缓存；且它在单条路上**不重试**——三条路各重试 2 次  
会把单源耗时推到 60s（实测 gdelt 就是 60.8s），这是踩过的坑。

## 二、三层职责与边界

```
sources.yaml   来源登记：许可、频率、更新窗口、出口、探针、覆盖范围、状态
      │
   net.py      出口层：按源选路，不继承环境变量
      │
 probe.py      探活层：真发请求，产出可用性与延迟事实
      │
govern.py      对账层：源 ↔ catalog ↔ warehouse_registry 三方比对，出缺口工单
```

**不做的事（硬边界）**：

- 不写业务数据，不落 parquet，不改 `catalog.json`
- 不碰 `quant_backtest/warehouse_sync_runtime.py` 受控采集框架（那是另一套冻结契约）
- 不碰 `data_sources/cnopendata/`（408 条档案 + 6 张入湖表已自成体系，这里只登记其存在）

## 三、用法

```bash
cd C:/项目/老田的AI助手
PY="C:/Program Files/Python311/python.exe"

# 列源（可按域/状态筛）
$PY -m tools.data_hub sources
$PY -m tools.data_hub sources --domain macro
$PY -m tools.data_hub sources --status verified

# 校验登记表（改完 yaml 必跑，非零退出即不合格）
$PY -m tools.data_hub check

# 静态体检（不出网，毫秒级）
$PY -m tools.data_hub probe

# 真探活（出网，约 20-60s，产出 reports/data_hub/probe_<ts>.{jsonl,md}）
$PY -m tools.data_hub probe --live
$PY -m tools.data_hub probe --live --only tushare,eastmoney,ths

# 三方对账（只读，出缺口；--report 落文件）
$PY -m tools.data_hub govern --report

# 取数落地（东财 K 线 / 新浪快照 / 股吧帖子流）
$PY -m tools.data_hub collect --adapter guba --dataset 600519

# 社区情绪日表（帖子流 → 每票每日一行，见第七之二节）
$PY -m tools.data_hub sentiment
$PY -m tools.data_hub sentiment --codes 600519,301216 --dry-run
```

退出码：`0` 正常；`1` 校验/体检有问题；`2` 参数或加载错误。

## 四、判定口径（写死，避免各判各的）

| 结论            | 含义                | 处置                      |
| ------------- | ----------------- | ----------------------- |
| `ok`          | 2xx/3xx 且 ≤3000ms | 可用                      |
| `slow`        | 2xx/3xx 但 >3000ms | 可用但需限速/错峰               |
| `guarded`     | 4xx，站点可达，缺头/参数/鉴权 | **不是故障**，补 header 或 key |
| `unreachable` | 连接层失败（超时/DNS/重置）  | 先怀疑出口，再怀疑源              |
| `skipped`     | 无 HTTP 入口（私有协议）   | 走客户端探针                  |

登记状态只有四值：`verified` / `reachable_guarded` / `unreachable` / `planned`。  
**`unreachable` 必须写 `note` 说明是"出口限制"还是"源消失"**，否则静态体检报错——  
这条规则来自实测：binance 一开始被判死，实际只是本机出口不通，换本地代理就 200。

## 五、首次实测结论（2026-11-05 07:35）

30 个登记源，真探活结果：**ok 22 / slow 2 / guarded 3 / unreachable 2 / skipped 1**。

可用清单（ok/slow）：sse、szse、bse、eastmoney（K线域）、ths、sina、tencent、  
tushare、cninfo、cnopendata、guba、pbc、customs、worldbank、stooq、sec_edgar、  
cffex、shfe、chinamoney、jisilu、hkex(6.6s)、okx(5.3s)、binance(经本地代理)。

受阻清单与原因：

| 源             | 结论     | 真实原因                                          |
| ------------- | ------ | --------------------------------------------- |
| nbs 国家统计局     | 403    | 接口需 `time` 参数 + Cookie，站点本身可达                 |
| xueqiu 雪球     | 400    | 需先取 `xq_a_token`（项目内 `xueqiu_sync.py` 已实现登录态） |
| yahoo_finance | 403    | 需 crumb + cookie 握手                           |
| fred          | 超时/400 | 需免费 api_key；出口不稳                              |
| gdelt         | 超时     | 本机出口限制，非源消失                                   |

三项探针级发现，已固化进 yaml：

- 新浪 `hq.sinajs.cn`、深交所 `szse.cn` **必须带 Referer**，不带一律 403；带上前者 0.67s 200
- 东财实时域 `push2.eastmoney.com` 直连被 RESET、走代理 ProxyError；K线域 `push2his` 正常
- **只看状态码会漏判**：stooq 返回 796B "This site requires JavaScript" 挑战页，
  HTTP 状态是 200，最初被误判为 ok。已加 `probe.contains` 判据（响应体必须包含指定串），
  缺失即降级 guarded。tushare / eastmoney / ths / sse / sina 均已配判据并复测通过。

## 六、治理对账（2026-11-05 07:48 实测）

| 项 | 数量 | 说明 |
|---|---|---|
| 目录表 | 257 | `quant_backtest/data/catalog.json` |
| 物理表 | 305 | `warehouse_registry.json` 登记 |
| 无日期表 | 24 | 静态表，不参与时效判定 |
| **时效欠账** | **94 张** | 超频率 SLA |
| 　A 停产 | 0 | 4 张已写 lifecycle deprecated，自动退出统计 |
| 　B 断更 | 1 | `tdx_sector_min` 滞后 42 天 |
| 　C 滞后 | 93 | SLA 至 36 天 |
| 生命周期跳过 | 19 | planned/deprecated/archived，按契约不计欠账 |
| 未登记物理表 | 48 | `news_*`、`paper_*` 有数据但目录看不见 |
| 有表无源 | 2 | `us_legacy`(legacy)、`v2_bar_daily`(pg) |
| 有源无表 | 0 | cnopendata 6 张表已正确挂上 |

### 降噪过程（115 → 94 是修口径，不是掩盖问题）

第一次跑出 115 张，其中不少是误报，逐条核过后做了三处修正：

1. `shibor_lpr`、`irm_qa_*_probe` 等 19 张**早已**在 `data_lifecycle.yaml` 标了
   deprecated，但 govern 没读 lifecycle → 重复报警。现已接入 `lifecycle.skipped()`。
2. Q 类 SLA 100 天不符合财报披露节律（季末到入库本就 3-4 个月），改 150 天，
   港股财务四表从"欠账"回归正常。
3. 部分低频表在 catalog 里被标成 `D`（日更），加了 `freshness_sla_overrides` 按表覆盖。

### 真实问题：日更批次覆盖不足，不是采集停摆

先看一个容易得出的错误结论——"xtcloud 采集挂了"：

- catalog 显示 64 张 xtcloud 表数据止于 10-29/10-30
- 但 `data/tdx` 目录里 11-02/11-03/11-04 分别写入了 21/30/25 个 parquet

**所以采集在跑，只是每日批次只覆盖约 30 张表**，其余 64 张表不在日更清单内。
`batch_profile` 这张表回答的是"哪些表没被最近批次覆盖"，**不能读成"采集挂了"**。
（这条口径 2026-11-05 实证修正过：初版注释写成"该批次没跑"，是错的。）

### A 类：4 张上游已停产，已走 deprecate

`ts_slb_sec`（止于 2024-09-30，766 天）、`ts_kpl_concept`（2024-11-17，718 天）、
`ts_tdx_index`（2025-04-17，567 天）、`ts_slb_len`（2025-07-25，468 天）。
处理方式是写 `agent_platform/catalogs/data_lifecycle.yaml` 标 `deprecated` + 写明上游停更证据，
**不是"补采"**——补也补不回来，只会一直报错。

### 已知局限（别当成没问题）

- SLA 用**自然日**，周末/节假日会放大滞后天数。精确做法是按交易日计缺口，
  `quant_backtest/cn_trading_days.py` 已具备能力，但 govern 尚未接入。
- `freshness` 取自 catalog，而 catalog 由 `build_catalog.py` 重建；catalog 不重建，
  govern 看到的就是旧快照。对账前若刚采集过，应先生成 catalog。


## 七、collect：登记 → 取数 → 校验 → 落地（最小闭环）

```bash
# 东财日 K（secid: 1.=沪 0.=深）
$PY -m tools.data_hub collect --adapter eastmoney --dataset 1.600000 --limit 30

# 新浪实时快照（必须带 Referer，适配器已内置）
$PY -m tools.data_hub collect --adapter sina --dataset "sh600000,sz000001"

# 只取数校验，不落盘
$PY -m tools.data_hub collect --adapter eastmoney --dataset 1.600000 --dry-run
```

落盘位置：`data_hub_landing/<adapter>/<dataset>/part_NNNN.parquet` + `_manifest.json`。
**故意不写 `quant_backtest/data/`**：那是受控采集框架的地盘，进 catalog 有主从问题。
这里只证明"登记即取数"这条路通，正式入仓仍需走受控框架。

三道闸门（不过就拒绝落盘，宁可不写也不留脏数据）：

1. `check_frame`：空表、日期列不可解析、日期重复、缺主键列、全空行 → `rejected`
2. 增量语义：`since` 有值且上游无新行 → `no_new`（不算失败，退出码 0）
3. 清单留痕：每片 parquet 记 rows / sha256_16 / 取数 URL / 出口 / 列名 / 时间

水位存在 `state/watermarks.json`，下次不带 `--since` 时自动用它做增量。
实测（2026-11-05）：首次东财拉 30 行写入；第二次带水位只回 1 行（新增当日），符合预期。

`stooq` 适配器保留但当前不可用：该站返回 796B "This site requires JavaScript"
挑战页且状态码是 200，需要浏览器 cookie 才能取，已在 sources.yaml 降级为
`reachable_guarded`。

## 七之二、sentiment：社区情绪日表（帖子流 → 每票每日一行）

```bash
# 按 watchlist.yaml 的名单跑（默认 2 页/票，约 160 帖余量）
$PY -m tools.data_hub sentiment

# 临时换名单 / 只看不写 / 只聚合已落盘数据
$PY -m tools.data_hub sentiment --codes 600519,301216
$PY -m tools.data_hub sentiment --dry-run
$PY -m tools.data_hub sentiment --no-fetch
```

产物是**单文件** `data_hub_landing/sentiment_daily/sentiment_daily.parquet`
（每次从全部已落盘帖子重算后覆盖写）。

**为什么单文件而不是追加分片**：每次跑都是全量重算，写成分片会让同一天出现
两行（09:45 的 46 帖 + 14:30 的 120 帖各占一行），下游必须先规整才能用——
这种"看起来有数据、其实得先去重"的表等于没做。单文件覆盖 + 原子替换
（`os.replace`）没有这个坑。

字段与口径：

| 字段 | 说明 |
|---|---|
| `code` / `trade_date` | 6 位代码 / **归属交易日** |
| `posts` / `authors` | 当日帖量 / 独立作者数 |
| `top_author_share` | 单作者最多发帖占当日比例，**>0.5 基本是刷屏/机器人** |
| `comments` / `clicks` / `heat` | 评论合计 / 点击合计 / 两者之和 |
| `bull` / `bear` / `neutral` | 标题关键词命中数（词表在 watchlist.yaml） |
| `bull_ratio` | `bull/(bull+bear)`，**无命中时为 null，不填 0.5 假装中性** |
| `cal` | `trading_day`（非交易日归到下一交易日）/ `natural`（日历不可用时降级） |

三条必须知道的口径：

1. **非交易日帖子归到下一个交易日**（周末发的算周一），这样才能和
   K 线/px_d 对齐。日历不可用时退回自然日并把 `cal` 标成 `natural`，
   不假装对齐。
2. **股吧的 `bullish_bearish` 字段是空的**（实测 85 帖：80 个 0、5 个 NaN），
   所以多空方向必须自己从标题算，不能假设上游给了标签。
3. **帖量受采集频次影响，跨日不可比**。日表里的 `posts` 是"我抓到了多少帖"，
   不是"当天真实发生多少帖"。当前口径下只有 08:20 一次采集 + 单票两页，
   **日内分时不可信、跨日比较要等固定频次攒够样本**。要拿它做因子，
   先按固定时点（如 11:35 / 15:35）跑两周再说。

雪球状态（2026-11-05 实测）：无 cookie 直连 400，带 token 403
`Seek IP Blacklisted`，**当前拿不到数据**，所以本模块只吃股吧。
xq_pump 的链路是好的，等 IP/ token 解封后按同一套字段接进来即可
（`xq_hot_post` 有 `title/time/comment_count`，映射成本很低）。

## 七之三、长尾兜底批（tdx / tushare）：漂移检测，不复制清单

批次化采集一定有长尾：表被分进 A/B/C/D 四批，批次表单里没有的表就永远
轮不到。2026-11-05 对账实证两处：xtcloud 侧 84 张滞后 5-7 天，tushare 侧
10 张停在 10-14~10-30——而 **TushareSync 任务夜夜 exit=0、当晚跑满 6695s**。

**核心原则：兜底批不维护第二份表清单**。复制一份表名维护，两份必然漂移
（原 owner 改批次，兜底批不知道）。改成"谁落后补谁"：

| | tdx（`quant_backtest/tdx_tail_sync.py`） | tushare（`quant_backtest/ts_tail_sync.py`） |
|---|---|---|
| 判据 | 最新分片 mtime 落后 >36h | **数据水位**（读日期列取 max）落后 |
| 为什么不同 | 分片按日命名，读数据贵 | 逐码/按日/按月混合，mtime 会被修复与空片污染 |
| 执行 | `tdx_sync.py --tables` | 按表配方（api + 粒度 + 落盘方式）直拉 |
| 幂等 | tdx_sync 自身幂等 | 已有分片跳过；0 行不落盘；part 去重追加 |

**为什么 tushare 不用 mtime**：`index_weight/000906_202611.parquet` 是 0 行空片，
mtime 是今晨 01:21（增量任务写的），文件名日期还是 11 月——三个信号都"新鲜"，
数据其实是 10-30。按 mtime 判会永远漏掉它。空片还会让 freshness 假绿，
`build_catalog` 的 K99/K100 已经踩过这个坑，兜底批沿用同一口径：**0 行不算水位**。

**日频 vs 季频必须分开判**：

- `daily`：有缺口交易日就补（行情/问答/持股/龙虎榜/北向）。
- `periodic`：滞后超 SLA(45d) 才补（业绩快报/预告）。

后者是实测逼出来的：`express` 2026Q3 全市场只有 **11 家**发快报
（ann_date 20261009~20261021，本地已齐全），Q2 只有 66 家。按日频判欠账，
兜底批会每天白拉 14 天、每天 0 行，然后把"上游没发"记成"采集漏了"。

```bash
cd quant_backtest
$PY ts_tail_sync.py --dry-run      # 只看水位与缺口
$PY ts_tail_sync.py                # 实跑（2026-11-05 首跑：8 表 / 25936 行 / 0 失败）
$PY ts_tail_sync.py --tables top_inst --max-backfill-days 60
```

跑完必须 `build_catalog.py` → `govern --report` 才可见：govern 读的是
catalog.freshness，不是磁盘 mtime（"补了数但对账没变"多半是忘了重建）。

配套的口径修正（不是刷绿，是改错判）：

- `express` / `forecast` → SLA 45d；`index_weight` → 40d（见 sources.yaml
  `freshness_sla_overrides`）。
- tushare `hk_basic` → `build_catalog._CATALOG_STATIC_V31` 加 static_no_date：
  date_col 是 `list_date`（上市日），不是数据水位，刷新也不会变新。

## 八、加一个新源要改什么

只改 `sources.yaml` 一条，不改代码：

```yaml
  - id: my_source
    name: 某某源
    domain: macro            # quote|finance|disclosure|flow|macro|derivatives|overseas|crypto|alt
    home: https://example.com
    license: public_page     # public_api|public_page|open_license|paid|unknown
    freq: D                  # RT|D|W|M|Q|A|EVENT
    update_window: "交易日 17:00"
    proxy_policy: direct
    status: planned
    adapter: http_json       # tushare|http_json|http_csv|http_html|file|sdk
    covers: [要点1, 要点2]
    linked_tables: []        # catalog 表名前缀；接了表才填
    probe: {url: "https://example.com/api", expect: 200}
    note: ""
```

然后 `check` → `probe --live --only my_source` → 按实测改 `status` 与 `note`。

**这条流水线就是"全网获取"的接入协议**：登记 → 探活 → 定性 → 挂表。  
每接一个源，成本是填 12 个字段，不是写一个 sync 脚本。

## 九、状态文件

- `state/egress.json` — auto 策略实测记忆（源 → 胜出出口 + 延迟 + 时间戳），24h 过期  
  |  |  |  
  | --- | --- |
- `reports/data_hub/probe_<ts>.jsonl|md` — 每次探活的原始事实与汇总
- `reports/data_hub/govern_<ts>.json|md` — 每次对账的完整缺口清单

`state/` 与 `reports/` 都可随时删掉重建，不含业务数据。
