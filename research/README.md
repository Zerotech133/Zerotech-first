# research/ — 研究台账与四关判定（2026-10-28 建立）

> 目的：把「想法」变成可追踪、可复现、可证伪的编号假设。所有研究先过四关，再谈回测收益。

## 一、四关判定（任何假设立项前必须逐条回答）

| # | 关 | 问题 | 判定标准 | 不过怎么办 |
|---|---|---|---|---|
| 1 | **时点可知**（PIT） | 所有输入在决策时刻是否已经知道？ | 特征只能取自 `t-1` 收盘后已发布数据；公告按"披露日+1交易日"生效；无未来函数 | 重设计特征，或用点时快照 |
| 2 | **样本外**（OOS） | 是否在没调参的数据上验证？ | 时间切分（如 2026 上半年开发/下半年验证）+ 滚动；样本外收益不低于样本内 50% | 缩短参数、减少自由度 |
| 3 | **成本后**（Cost） | 扣掉佣金/滑点/涨跌停/停牌后还赚吗？ | 按双边 0.1%–0.2% + 冲击成本；换手率高的策略要求更高边际 | 降频、加流动性过滤 |
| 4 | **容量/衰减**（Capacity） | 资金大了会不会失效？信号多久衰减？ | 按成交额占比估算容量；检验 1/3/5 日衰减曲线 | 只做小容量或放弃 |

**四关全过才允许进入"回测/影子"阶段；任何一关不过，台账状态标 `rejected` 并写清原因。**

## 二、台账（hypotheses.db）

表 `hypotheses` 字段：`id`（H1…）、`title`、`question`、`data_sources`、`method`、`gates`（JSON：pit/oos/cost/capacity 四问答案）、`status`、`owner`、`conclusion`、`evidence_path`、`created_at`、`updated_at`。

状态机：`idea → in_progress → backtested → validated / rejected → promoted`（进 playbook/影子）。

## 三、用法

```powershell
# 初始化 + 播种 10 个候选假设
C:\项目\py312\python.exe tools/research/hypotheses.py init --seed

# 列表 / 新增 / 更新
C:\项目\py312\python.exe tools/research/hypotheses.py list
C:\项目\py312\python.exe tools/research/hypotheses.py list --status in_progress
C:\项目\py312\python.exe tools/research/hypotheses.py add --id H11 --title "..." --question "..." --data "..." --method "..."
C:\项目\py312\python.exe tools/research/hypotheses.py update H1 --status backtested --conclusion "..." --evidence research/reports/H1_xxx.md

# H1：公告事件 CAR
C:\项目\py312\python.exe tools/research/event_study.py --days 90 --json
# H6：JEV 决策归因
C:\项目\py312\python.exe tools/research/jev_attribution.py --json
```

## 四、报告规范

- 每份报告落 `research/reports/<H编号>_<题目>_<日期>.md` + 同名 `.json`（原始统计）；
- 报告必须包含：数据窗口与 catalog 版本、样本量、方法、结果表（含 n/均值/胜率/t 值）、**局限**、结论（支持/不支持/证据不足）；
- 任何进入 `validated` 的假设，`evidence_path` 必须指向报告文件。

## 五、当前种子假设（10 条）

H1 公告事件 CAR、H2 PEAD、H3 龙虎榜席位、H4 资金流背离、H5 舆情 lead-lag、H6 JEV 决策归因、H7 财务因子分层、H8 行业轮动、H9 跨市场映射、H10 数据质量对回测影响。

> 第一批执行：**H1（数据→信号）+ H6（决策→改进）**，见 2026-10-28 的报告。


## 六、统计修正纪律（2026-10-30 判例）

> 四关之外的第 0 关：**统计口径是否正确**。

- 低频信号 × 多日持有 → 重叠，普通 t 虚高；必须用非重叠抽样或 block bootstrap；
- 两种修正都通过（非重叠均值 t≥2 且 bootstrap p<0.05）才允许进四关；
- 工具：`tools/research/stat_utils.py`；
- 判例：H22 前视（已修）、H23 重叠（修正后 rejected）。
