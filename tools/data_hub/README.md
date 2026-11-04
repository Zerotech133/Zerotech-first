# data_hub · 全网数据源接入与治理

> 2026-11-05 起用。解决两件事：**全网数据从哪取、出口怎么走**；**取来的东西对不对得上账**。
> 一句话定位：这是"来源侧"的登记与体检，不是"数据侧"的仓库。

## 一、为什么要有这一层（实测驱动，不是设计洁癖）

国内源和海外源不能用同一条出网路径。2026-11-05 在本机做的对照实验：

| 源 | 直连 | 走环境变量代理 |
|---|---|---|
| `api.tushare.pro` | **1.1s** | 12.3s |
| `d.10jqka.com.cn` | **0.96s** | ReadTimeout 6.7s |
| `push2.eastmoney.com` | 连接重置 | ProxyError |
| `api.binance.com` | 不通 | 不通（环境代理），**本地 7890 通** |

`akshare` / `requests` 默认 `trust_env=True`，会继承宿主注入的 `http(s)_proxy`，
把国内 A 股源也塞进代理——这是当前国内源"慢、偶发失败"的根因。

**结论：出口路径是数据质量的一部分，必须按源声明、实测择优、落盘记忆，不能靠全局环境变量。**

`net.py` 因此提供四条策略：

| 策略 | 行为 | 适用 |
|---|---|---|
| `direct` | `trust_env=False`，清空 proxies | 国内 A 股源（默认） |
| `env` | 跟随环境变量 | 企业/沙箱统一出口 |
| `local` | 强制 `127.0.0.1:7890`（可用 `DATA_HUB_LOCAL_PROXY` 覆盖） | 海外源 |
| `auto` | direct → env → local 实测择优，胜出路径写 `state/egress.json`（24h 有效） | 不确定的源 |

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
```

退出码：`0` 正常；`1` 校验/体检有问题；`2` 参数或加载错误。

## 四、判定口径（写死，避免各判各的）

| 结论 | 含义 | 处置 |
|---|---|---|
| `ok` | 2xx/3xx 且 ≤3000ms | 可用 |
| `slow` | 2xx/3xx 但 >3000ms | 可用但需限速/错峰 |
| `guarded` | 4xx，站点可达，缺头/参数/鉴权 | **不是故障**，补 header 或 key |
| `unreachable` | 连接层失败（超时/DNS/重置） | 先怀疑出口，再怀疑源 |
| `skipped` | 无 HTTP 入口（私有协议） | 走客户端探针 |

登记状态只有四值：`verified` / `reachable_guarded` / `unreachable` / `planned`。
**`unreachable` 必须写 `note` 说明是"出口限制"还是"源消失"**，否则静态体检报错——
这条规则来自实测：binance 一开始被判死，实际只是本机出口不通，换本地代理就 200。

## 五、首次实测结论（2026-11-05 07:35）

30 个登记源，真探活结果：**ok 22 / slow 2 / guarded 3 / unreachable 2 / skipped 1**。

可用清单（ok/slow）：sse、szse、bse、eastmoney（K线域）、ths、sina、tencent、
tushare、cninfo、cnopendata、guba、pbc、customs、worldbank、stooq、sec_edgar、
cffex、shfe、chinamoney、jisilu、hkex(6.6s)、okx(5.3s)、binance(经本地代理)。

受阻清单与原因：

| 源 | 结论 | 真实原因 |
|---|---|---|
| nbs 国家统计局 | 403 | 接口需 `time` 参数 + Cookie，站点本身可达 |
| xueqiu 雪球 | 400 | 需先取 `xq_a_token`（项目内 `xueqiu_sync.py` 已实现登录态） |
| yahoo_finance | 403 | 需 crumb + cookie 握手 |
| fred | 超时/400 | 需免费 api_key；出口不稳 |
| gdelt | 超时 | 本机出口限制，非源消失 |

两项探针级发现，已固化进 yaml：

- 新浪 `hq.sinajs.cn`、深交所 `szse.cn` **必须带 Referer**，不带一律 403；带上前者 0.67s 200
- 东财实时域 `push2.eastmoney.com` 直连被 RESET、走代理 ProxyError；K线域 `push2his` 正常

## 六、治理对账（同一时点）

| 项 | 数量 | 说明 |
|---|---|---|
| 目录表 | 257 | `quant_backtest/data/catalog.json` |
| 物理表 | 305 | `warehouse_registry.json` 登记 |
| **时效欠账** | **115 张** | 超过频率 SLA（D 类 3 天、M 类 35 天） |
| 未登记物理表 | 48 张 | `news_*`、`paper_*` 等有数据但目录看不见 |
| 有表无源 | 2 张 | `us_legacy`(legacy)、`v2_bar_daily`(pg) 来源不可追溯 |
| 有源无表 | 0 | cnopendata 的 6 张表已正确挂上 |

时效欠账最狠的几张（数据早已不能用，但仍在目录里当资产）：

- `ts_slb_sec` 滞后 **766 天**
- `ts_kpl_concept` 滞后 **718 天**
- `ts_tdx_index` 滞后 **567 天**
- `ts_slb_len` 滞后 **468 天**
- 港股财务四表（`hk_income`/`hk_balancesheet`/`hk_cashflow`/`hk_fina_indicator`）滞后 128 天

这批是下一轮该处理的工单，处理方式二选一：**补采** 或 **从目录下线并标记 deprecated**。
不处理的话，任何"按目录挑表"的策略都会踩空。

## 七、加一个新源要改什么

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

## 八、状态文件

- `state/egress.json` — auto 策略实测记忆（源 → 胜出出口 + 延迟 + 时间戳），24h 过期
- `reports/data_hub/probe_<ts>.jsonl|md` — 每次探活的原始事实与汇总
- `reports/data_hub/govern_<ts>.json|md` — 每次对账的完整缺口清单

`state/` 与 `reports/` 都可随时删掉重建，不含业务数据。
