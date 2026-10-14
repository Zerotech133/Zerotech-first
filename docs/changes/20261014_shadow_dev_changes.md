# W3-5b 任务B：量子双刀产线「市况维度」影子观察注记（2026-10-14）

执行人：shadow-dev。性质：**注记式改动，不改信号行为**——信号判定、八重过滤、
Regime 分层、推送路径全部零触碰，仅新增观察字段与摘要行。

## 一、改动点清单

| 文件 | 改动 | 性质 |
|---|---|---|
| `portal/qd_scanner.py` | 新增 7 个函数：`_load_cfg` / `breadth_bucket` / `gate_would_block` / `regime_majority` / `market_summary_line` / `lake_breadth` / `annotate_breadth` / `apply_breadth_note`；`main()` 中在尾盘复核之后、cache 落盘之前插入一行 `apply_breadth_note(...)` 调用；cache 仅在注记生效时新增 `breadth_note`/`market_summary` 两个键；`[done]` 汇总行尾部追加市况摘要（开关关闭时输出与旧版逐字节一致） | 新增键/新增行 |
| `portal/qd_scanner_config.json` | 新建配置文件：`{"qd_breadth_note": true}`（qd_scanner 此前无配置机制，本文件即最小机制；缺失/坏 JSON/键为 false 均视为关闭） | 新建 |
| `portal/qd_view.py` | `render_workspace` 卡片区尾部按 `snap.get("market_summary")` 有则追加一行 dim 摘要（`.get()` 防御，旧缓存无此键不渲染，向后兼容） | 防御式新增 |
| `portal/test_qd_breadth_note.py` | 新建单测，8 条用例覆盖四条路径 | 新建 |
| `portal/qd_scanner.py.bak_20261014` | 改前完整备份（回退锚点） | 备份 |

宽度计算口径：`lake_breadth` 遍历湖 parquet（只读 `trade_date`+`close` 两列），
≥20 根 bar 计入，MA20 = 含锚日 20 根收盘均值，`close > MA20` 为站上；
锚日取湖末根众数（个别断更票自动剔除）。环境闸门参考口径与
`quant_backtest/qpzm_backtest.py::env_gate`、`cross_grid_backtest.py` 的
「全市场站上 MA20 占比 ≥40%」档一致。

## 二、字段格式示例（qd_scan_cache.json 新增部分）

顶层新增键（仅开关开启且湖宽度可用时写入）：

```json
{
  "breadth_note": {
    "breadth": 32.7,
    "bucket": "<40",
    "breadth_ref": "T-1",
    "ref_date": "20261013",
    "n_above": 1811,
    "n_total": 5545,
    "market_summary": "市况: breadth=32.7% [<40] regime=range",
    "note": "breadth_ref=T-1：14:40 时点当日湖未更新，用 T-1 锚宽度，与回测当日口径存在已知差异"
  },
  "market_summary": "市况: breadth=32.7% [<40] regime=range"
}
```

每个信号条目（signals_now / signals_recent / tail_signals）新增键：

```json
{
  "code": "600001.SH",
  "regime": "range",
  "breadth": 32.7,
  "breadth_bucket": "<40",
  "breadth_ref": "T-1",
  "gate_would_block": true
}
```

`gate_would_block` 语义：**只标注不拦截**——按 40% 档门控假设（参考
cross/qpzm 环境闸门）本单是否会被拦，供影子期攒「信号日宽度分档」
实盘数据验证门控假说，任何情况下不影响信号产出与推送。

汇总行示例（[done] 尾部追加）：

```
[done] 扫描 5545 只（跳过 51）：… 耗时 340s → qd_scan_cache.json · 市况: breadth=32.7% [<40] regime=range
```

下游兼容性：qd_view / api_misc / api_portfolio / portal_api 全部以 `.get()`
读缓存键，新增键缺失不炸；已用旧缓存（无新键）实测 qd_view 自校验 8/8 PASS。
`regime` 字段复用既有逐票值，摘要行 regime 取当日全市场 regime 分布多数派。

## 三、T-1 锚与回测口径的已知差异（留痕）

1. **锚日差一天**：14:40 盘中时点当日湖未更新（湖收盘后才写），宽度锚定
   湖末根 = T-1 日，`breadth_ref="T-1"` 标注；回测口径用信号当日宽度。
   差异方向：T-1 宽度对当日市况有一日滞后，临界日（宽度在 40% 附近）的
   gate_would_block 标注可能与当日口径相反。
2. **样本口径**：湖内有效票（≥20 根 bar 且末根=锚日）≈5545 只；回测口径
   的全市场样本随历史时点变化，两者分母不同。
3. **MA20 定义**：本实现 MA20 = 含锚日 20 根收盘简单均值，与
   qpzm_backtest.env_gate 一致；若引擎回测改用其他均线口径需同步。
4. **量纲无关**：宽度只用 close，不涉 amount，与 W1-2 量纲修复无交叉影响；
   `lake_amounts`/`merge_amount` 既有逻辑零改动，共存。

影子期数据分析时：凡 gate_would_block 临界样本（breadth∈[38,42]），建议
用收盘后湖数据重算当日宽度复核一遍再入档。

## 四、测试结果

`"C:/Program Files/Python311/python.exe" portal/test_qd_breadth_note.py`
→ **8/8 PASS**（0.49s），覆盖：
1. 宽度计算正确性（合成湖：MA20 判定、次新 <20 bar 剔除、断更票锚日剔除、T-1 锚）
2. 三档分桶（<40 / 40-50 / ≥50，含 40/50 边界归属与 None 降级）
3. gate_would_block 判定（39.9 拦 / 40.0 不拦 / None 不拦）
4. 开关关闭零改动（enabled=False → 信号 dict 逐键不变、返回 (None, "")）

回归：`test_qd_amount_fix.py` 15/15 PASS（输出键集不变校验含在内）；
`qd_view.py` 离线自校验 8/8 PASS；`py_compile` 通过。
真实湖只读冒烟：breadth=32.7% [<40]，ref_date=20261013，n=1811/5545。

## 五、回退方法

- **一键回退**：`portal/qd_scanner_config.json` 中 `qd_breadth_note` 改为
  `false`，或直接删除该文件 → 扫描输出与旧版逐字节一致（新键不写入、
  汇总行无摘要、宽度不计算、lake_breadth 不执行零开销）。
- **代码级回退**：`copy portal/qd_scanner.py.bak_20261014 portal/qd_scanner.py`，
  并删除 `portal/qd_scanner_config.json`、`portal/test_qd_breadth_note.py`；
  qd_view.py 的 msum 三行为防御式可选代码，可保留（旧缓存无键不渲染）。

## 六、纪律执行声明

- 改前已备份 `qd_scanner.py.bak_20261014`；diff 复核仅新增行 + 汇总行格式串追加
- 未改信号/过滤/推送行为（注记字段与摘要行除外）
- 测试全部走离线合成数据与只读湖路径，未触网推飞书
- 未注册计划任务，未跑全市场实时扫描（湖宽度冒烟为只读离线计算）
- 解释器仅用 `C:/Program Files/Python311/python.exe`
