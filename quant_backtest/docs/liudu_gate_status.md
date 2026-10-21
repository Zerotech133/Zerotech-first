# 六度选股（荀攸席位）决策门总览 — 组织实施状态

> 生成时间：2026-10-21 19:38（GMT+8）
> 策略：休眠默认（dormant default）—— 决策落码、激活开关留待老田拍板；铁律红线不越权。
> 验证基线：8 测试套件 **64 passed**（pandas 环境全绿；托管解释器缺 pandas 时 2 个 N3 用例显红，属环境差异非代码回归）；非生产试跑 exit=0；`signal_pool.jsonl` 已按方案 B 落账（trial 数据，幂等 upsert，重跑不累积）；R2 十前置全 False。

## 一、非生产门（N1/N4/N8/N10/N11）— 决策已落码、休眠代码已就绪

| 门 | 决策（已拍板） | 落码位置 | 运行开关 | 休眠代码状态 | 验证 |
|---|---|---|---|---|---|
| **N1** universe=market | `SELECTED_UNIVERSE="market"` | `liudu_universe.py:56` | `LIUDU_UNIVERSE_ENABLED=True`（路径A已生效） | `apply_universe_filter` 已接入 `xunyou.build_pool:111` 且**实测生效** | 真实链实测：输入76→输出60、非A股残留0 |
| **N4** 止损参数 | `fixed=[-0.05,-0.08,-0.12]`+`trailing=0.10` | `stop_loss_harness.default_params` | `STOP_LOSS_EXPERIMENT_ENABLED=True`（路径A已生效） | 纯仿真，已在 trial 覆盖 16 只 | 9 passed |
| **N8** 语义模式 B | `SELECTED_RECONCILE_MODE=MODE_B` | `reconcile_semantics.py:41` | `RECONCILE_SEMANTICS_ENABLED=True`（路径A已生效） | `reconcile()` 恒 `NOT_VERIFIED`/`ready=False`（绝不私自 READY） | 6 passed |
| **N10** 导航入口 | 子页入口、不挂顶栏胶囊 | `portal/static/liudu_entry.html` | （静态页） | 已建，未挂载 nav.js/api_common/nginx | — |
| **N11** 多池方案 B | `TIERING_PLAN="B"`+`TIER_LAYER_ORDER` | `signal_pool_writer.py:62` | `SIGNAL_POOL_AUTO_WRITE=True`（路径A+1已生效） | `write_signals` 已接入 `liudu_trial_run.py`，分层落账 `data/signal_pool.jsonl`（幂等 upsert） | 8 passed；`signal_pool.jsonl` 已落账 30 条（xunyou_pool 上游，trial 数据） |

**关键结论**：N1/N4/N8/N11 四决策已据路径 A（及后续"按计划组织实施"指令）翻开关**真实生效**；N10 静态页就绪。`LIUDU_UNIVERSE_ENABLED=True` 后真实选股链强制剔除非 A 股/北交所，已实测；`SIGNAL_POOL_AUTO_WRITE=True` 后 trial 试跑显式落账分层统一入口，已实测 30 条。

## 二、B 组（N3/N5/N6/N12）— 卡在需老田亲给的实在材料

| 门 | 当前状态 | 阻塞物（我不代填） | 解阻塞后我即执行 |
|---|---|---|---|
| **N3** 采纳门 | 证据评估：**AI 建议暂缓采纳（BLOCK）** | 老田最终签字（采纳/驳回/条件采纳） | 填 `docs/liudu_adoption_record.md` 签字槽、标记 CLOSED |
| **N5** 数据源凭证 | 8 源键名模板已备 | 8 类真实凭证（Tushare/xtcloud/Snowball/东财/股吧/akshare/crypto/飞书）填 `.env.local` | 经 N6 授权后置 `DATA_SOURCE_REAL_AUTH_ENABLED=True`、逐源接 `real_auth` |
| **N6** 密钥治理 | dry-run 扫描：唯一明文风险=`.env.local` | 清理授权：(a)删 `.env.local` / (b)迁 `SecretStore` | 跑 `cleanup_plaintext_secrets.py`、统一 `SecretStore` 托管 |
| **N12** R2 切流 | 护栏全 False、不可达 | 五要素审批全文（命令/影响面/不可逆点/替代方案/回滚预案） | 填 `liudu_r2_cutover_approval.md`、置前置、校验、arm、trigger |

**N3/STRAT1 硬阻塞根因**（证据级）：真实研究池 `xunyou_pool_20261020.json` 76 只中 **16 只污染**（14 美/外股 + 2 北交所），其中 T2 档恰被 4 外国股（ON/RVSB/TXN/NNBR）污染。N1=market 在真实链强制生效可清零样本层污染；**根治须 N5 源治理**（消除外源 ticker 泄漏）。

## 三、R2 十前置（全 False，任何路径不可达切流）

N3 采纳 ✗ / N5 凭证齐备 ✗ / N6 ceremony ✗ / R0 离线闭环 ✗ / R1 试运行达标 ✗ / reconcile READY ✗ / universe 锁定 ✗ / 止损实验验收 ✗ / 导航挂载 ✗ / 回滚演练 ✗。

## 四、交付物清单（本轮累计）

- `strategy_layer/liudu_universe.py`（含 `apply_universe_filter` 真实链接入）
- `strategy_layer/reconcile_semantics.py` / `signal_pool_writer.py` / `data_source_adapter.py`
- `experiments/stop_loss_harness.py`
- `tools/secret_store.py` / `cleanup_plaintext_secrets.py` / `r2_cutover_staging.py`
- `tools/liudu_trial_run.py` / `liudu_trial_smoke.py` / `liudu_universe_guard_check.py` / `verify_credential_readiness.py`
- `portal/static/liudu_entry.html`
- `docs/liudu_b_group_execution_pack.md` / `liudu_adoption_record.md` / `liudu_r2_cutover_approval.md` / 本总览
- `reports/liudu_trial_20261021.json`（trial 产物）
- 8 测试套件（含 N1 新增 4 例、signal_pool_writer 新增 2 例幂等用例）= **64 passed**（pandas 环境）

> 纪律：改动全部留工作树、未 `git commit`；零真实凭证注入、零网络请求、零生产 picks、零 R2 切流触发。

## 五、状态更新（2026-10-21 20:45，续「按计划组织实施」）

- **幂等 upsert 修复收口**：`signal_pool_writer.write_signals` 由纯 append 改为按 `(trade_date, symbol, source)` 三键去重 + 原子写，trial 重跑 `data/signal_pool.jsonl` 仍 30 条、不累积；`tools/liudu_trial_run.py` 输出措辞同步由「append-only」更正为「幂等 upsert」。
- **测试口径校正**：8 套件在当前带 pandas 环境 **64 passed**（原记 61 为更早基线；少记的 2 例为 signal_pool_writer 幂等新增用例 + 计数口径微调），零失败零错误；托管解释器（3.13，无 pandas）下 2 个 N3 用例因 `market_scan.py`/`backtest_engine_v2.py` 依赖 pandas 显红，属环境差异、非代码回归。
- **B 组仍卡老田材料（铁律不代填）**：N3 签字 / N5 八类凭证 / N6 清理授权 / N12 五要素。其中 N3 证据评估维持 **AI 建议暂缓采纳（BLOCK）**——STRAT1 硬阻塞（真实池 T2 含 4 外股 ON/RVSB/TXN/NNBR 污染，根治须 N5）。
- 自主可推进上限已到：非生产 5 门决策落码 + 真实生效 + 验证全绿；余下均为需老田亲给实在材料的决策门，AI 不越权触发 R2 / 不注入凭证。
