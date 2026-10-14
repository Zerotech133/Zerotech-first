# 20261014 cross 净向分排序切换（W5 · cross-netdev）

执行人：cross-netdev ｜ 产线解释器：`C:/Program Files/Python311/python.exe`
涉及文件：`quant_backtest/cross_poll.py`、`quant_backtest/config.json`、
新增 `quant_backtest/test_cross_netsort.py`。

## 〇、落点勘误（与任务前提的差异，必读）

任务书说排序在 market_scan.py，**经核实不符**：market_scan.py 是缠论
全市场扫描器（chanlun 源上游），无 cross 排序/买入观望分级逻辑——W3-4
韧性改造（20261014_cross_resilience_changes.md）已核实同一结论并零改动。
cross TopN 排序唯一落点是 **cross_poll.py build_cross**。因此：

- market_scan.py 本次**零改动**，未做 `market_scan.py.bak_20261014_net`
  备份（对未改动文件留备份名会误导回退）；
- 实际改前备份：`cross_poll.py.bak_20261014_net`、
  `config.json.bak_20261014_net`（均为改前原样字节拷贝）。

## 一、公式（对齐 cross_grid_backtest W3-4 网格）

```
net_score = Σ(源方向分 × 源权重 × 源方向符号) / Σ(可用源权重)
源方向分：long=+1 / short=-1 / neutral=0（未命中的可用源贡献 0）
源权重：  qpzm=1.0  sig4=1.0  chanlun=1.0  9dim=0.5
方向符号：qpzm/sig4/chanlun=+1；9dim=-1（反指，方向取反）
归一化：  分母=权重已配置且 src_state=ok 的源权重之和 → 分数口径恒 [-1,1]，
          降级日与全源日可比；分母为 0（全源缺失）→ 0.0
```

- 9dim 反指处理（任务要求检查项）：**改前聚合未做任何反指/降权**——
  `_src_9dim` 把 score≥80 一律记 long，原样计入 n_long。本版在净向分中
  显式处理：方向取反 × 权重 0.5 → 命中贡献 **-0.5**。口径说明：W3-4
  网格中 down0.5 为 +0.5、reverse 为 -1.0，W5 批准的 -0.5 为两者折中的
  保守口径（方向体现反指、幅度减半防单源噪声），网格结论"净向分与 raw
  历史无差异"覆盖此量级扰动。
- **n_hit / n_long 原始计数不动**（9dim 仍计一次 hit/long），k=3 门槛与
  买入/观望分级阈值不受影响——排序键只改序，不放水。
- 单源缺失（src_state 非 ok）：该源不进分子、权重剔出分母（归一化），
  与韧性降级语义（degraded_k3 / blocked）共存。

## 二、开关与输出标注

- `config.json` cross 节最小增量：`"sort_key": "net"`；
- 缺省（config 无此键）= **net**（W5 直上）；设 `"raw"` 一键回退旧长票
  计数排序（-n_hit,-n_long,code），且不产出 net_score 字段（零残留）；
  非法值按缺省 net 处理不炸；
- 产物 `build_cross()` 新增顶层字段 `sort_key`（"net"/"raw"），net 模式
  下 stocks[] 每票带 `net_score` 字段（round 4 位）；CLI `__main__` 汇总
  新增一行：`排序键: sort_key=net（净向分 net=Σ(dir×w)/Σw（9dim 反向×0.5））`
  ——下游与推送卡可据此识别口径。

## 三、验证

1. **单测** `test_cross_netsort.py`（合成数据，三路径 14 用例）：
   - 净向分计算：四源齐整基线值逐票对表（含 9dim -0.5 反向、冲突票 0）、
     单源缺失归一化（den 2.5）、全源缺失除零保护、与 raw 排序头名翻转；
   - 开关：raw 回退旧行为且无 net_score 字段 / 缺省 net / 非法值兜底；
   - 韧性共存：单源 error 降级继续出票、blocked 产空、k 门槛与原始计数
     不动、source_health 互不干扰。
   结果：`pytest test_cross_netsort.py test_cross_resilience.py -q` →
   **24 passed**；`test_ui_drilldown.py + test_sqlite_migration.py` 回归
   通过（115 passed，与改前一致）。
2. **离线对照**（2026-10-13 生产产物只读重放，四源全 ok，过 k=3 门槛
   候选 19 只，TopN=10）：

   | 指标 | net vs raw |
   |---|---|
   | TopN 集合 | 8/10 相同；2 只轮换（net 换入 超颖电子/松发股份，换出 闽东电力/红豆股份） |
   | 位次差异 | 4/10 条目顺序不同 |
   | 头部 5 只 | 完全一致（光智科技/义翘神州/先导基电/甬金股份/博汇科技） |

   差异幅度小、符合 W3-4 网格"两种排序键历史表现无差异"结论；差异来源
   是当日候选均为 3 源命中、raw 侧 n_long 平票靠字典序，net 侧按方向
   一致度区分（mixed 票的 short 票被净向分压后）。

## 四、回退方法

```bash
cd /e/老田的AI助手/quant_backtest
cp cross_poll.py.bak_20261014_net cross_poll.py
cp config.json.bak_20261014_net config.json
rm test_cross_netsort.py   # 可选：新单测一并移除
```

仅 config 回退（保留代码）也可：把 cross.sort_key 改为 "raw" 即旧行为。

## 五、纪律与遗留

- 不推飞书、未注册计划任务、未动 positions.json 与生产数据；验证全部
  只读（build_cross 读产物聚合，无写路径）。
- 遗留：①今日候选全为 3 源命中，net 的区分力主要体现在 mixed 票压后，
  4 源全命中票（net 0.714 vs 0.857）的区分样本待生产观察；②9dim -0.5
  折中口径与网格 down0.5(+0.5)/reverse(-1.0) 不同，若后续网格扩样本
  （9dim 锚变密）建议三口径重验；③"买入/观望分级"若指 todays_picks
  T1/T2/T3 管线，其消费 topn 顺序自动跟随新排序，阈值未动。
