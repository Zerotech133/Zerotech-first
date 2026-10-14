# K-STOCK-371 · D-Day（10-16）开闸 runbook 快查卡 + 报告填空模板

> 用途：10-16 当天照此卡逐步执行，报告按模板填空即出。上游依据：319 操作卡 §4、361 冻结清单、363 预检、367 彩排（953s·delta 全 0.0）。
> 冻结基线：git tag `open1016-frozen-d2`（92f36b7）。**当天禁改 quant_backtest/ 回测链路任何文件。**

---

## 一、执行前预检（5 分钟，全过才起跑）

> ✅ **命令级预演已验证（K-STOCK-375·10-14）**：P1 返回 0、P2 分片可查、P3 describe 正常、P4 venv python 及 pandas/pyarrow import OK——四条命令可直接照抄执行。D-Day 当天 P2 判据 = 分片内最新 trade_date = 20261016。

| # | 预检项 | 命令 | PASS 判据 |
|---|---|---|---|
| P1 | 产物面干净（🔴 367 判例：残留产物会全 skip 出假结果） | `ls quant_backtest/data/bt2/bt2_open1016_*.json 2>/dev/null \| wc -l` | **= 0**（非 0 → 先归档移走再跑） |
| P2 | 数据进湖 | 查 `data/tdx/ts_daily/` 最新 trade_date | **= 20261016**（D-Day 当日收盘数据） |
| P3 | 冻结基线自证 | `git describe --tags` | `open1016-frozen-d2-*`（回测链路五文件与 tag 一致） |
| P4 | 解释器 | 统一用 `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe` | 🔴 裸 3.13.12 无 pandas |

## 二、执行（预期 16~25 分钟）

```bash
cd /e/老田的AI助手/quant_backtest
"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -X utf8 open1016_run.py > _open1016_dday.log 2>&1
```

- 输出落文件（铁律：长跑不走管道）；如需后台，用「输出落文件」方式，勿裸管道
- 阶段预期：守卫 PASS → 三年窗重扫约 6min（~13.6 只/s）→ 一年窗重扫约 2.5min → 七臂串行（y1 臂实测 ~56s/臂）
- **守卫退出码 2 = 数据未就位，不是脚本坏了** → 先补增量拉取再执行

## 三、验收判据（三层）

1. **进程层**：日志七臂全 `DONE`、无 `FAIL`；rc=0
2. **产物层**：`data/bt2/` 七个 `bt2_open1016_*.json` 全部当日时间戳；`_open1016_compare.md` 生成
3. **对照层**：delta 表对照 315 五臂总表——
   - 终选臂 `open1016_y3_final` 预期落在 **-4.96 / PF1.20 / 仓位 7.7%** 族（新数据允许小幅漂移）
   - **delta 非零 = 新交易日带来的正常产出**（彩排 d1=1013 时全 0.0 是确定性基准）；异常判据 = 漂移方向与幅度显著背离（如终选臂转盈、PF<1.0），背离则进当天报告「异常与处置」段并挂复盘

## 四、异常处置速查

| 症状 | 处置 |
|---|---|
| 守卫 rc=2 | 数据未进湖：跑增量拉取器（tdx_sync/adj_factor_sync，走 7890 代理）后重跑 |
| 中途 FAIL | 看日志定位臂名；单臂失败可用 `--only <arm>` 复跑该臂（幂等） |
| 长时间无输出 | 看 `_open1016_dday.log` 是否仍在 flush（market_scan 每 200 只一行）；300s 阈值内属正常 |
| 产物残留 | 全部移入 `data/bt2_rehearsal_*/` 类归档目录后重跑 |

## 五、当日报告填空模板

```markdown
# K-STOCK-37X · 10-16 开闸正式跑批报告

## 一、执行概要
- 起跑/结束时刻：__ / __（总耗时 __min，基线 16~25min）
- 数据底座：ts_daily / adj_factor 至 20261016；守卫 PASS
- 预检：P1~P4 全 PASS（异常记录：__）

## 二、七臂结果与 delta 对照（对照基准=315 五臂总表）

| 臂 | ret | PF | 笔数 | 仓位 | delta(ret) | 判读 |
|---|---|---|---|---|---|---|
| y3 off | | | | | | |
| y3 weak | | | | | | |
| y3 high | | | | | | |
| y3 high+score | | | | | | |
| y3_final（终选） | | | | | | |
| y1_base | | | | | | |
| y1_final | | | | | | |

- 终选臂族值判读：预期 -4.96/PF1.20 族 → 实测 __，判读：__
- delta 显著项解读（新交易日效应）：__

## 三、结论与拍板事项
- 开闸有效性结论：__
- 终选参数（gate high + min80 + top10 + lc + runup5 + hist_st）维持/调整建议：__
- 遗留与挂账：__

## 四、归档
- 报告本档 portal/docs/；跑批日志 `_open1016_dday.log`；产物保留 data/bt2/（勿删）
- git 提交号：__
```

## 六、当天纪律

- 只跑不修：任何引擎/脚本问题当天只记录不改动（冻结纪律）；确需修复 → 解 tag 重核 17 项再重打
- 报告当日出：数据 + delta 表 + 结论三段齐即提交，号段顺延（下一个可用号见 MEMORY）
