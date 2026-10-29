# 历史代码分级登记表 v1（legacy_registry，2026-10-30 · 关羽）

> P0-2 交付：按《项目深度复盘与历史代码数据治理方案 v1》§4 分级模型，对 L1 冻结 / L2 归档 / L3 回收候选做**首版登记**。
> 原则：**不搞大规模迁移**；新代码只进 L0；旧代码原地冻结 + 标注；L3 删除必须引用链实证 + 回收站 + L2 审批（一次一议）。
> 证据工具：`tools/legacy_scan.py`（引用链扫描）；行数/规模为本日实测（命令见 §七）。

## 一、分级定义（沿用治理方案 §4.1）

| 级别 | 定义 | 动作 |
|---|---|---|
| **L0 活跃主线** | 被服务/产线/测试引用 | 正常维护；新代码守 portal 拆分守则 |
| **L1 冻结** | 仍被引用但不再演进（或拆分等待中） | 只修 bug；禁止扩展；登记 owner；优先拆分方案 |
| **L2 归档** | 无引用但保留证据 | 原地只读 + README 标注（或移 `archive/`）；不删除 |
| **L3 回收候选** | 无引用、无证据价值 | **L2 审批 + 快照 + 回收站**；一次一议 |

## 二、L0 活跃主线（范围）

`portal/`（api_kg 拆分后新模块）、`agent_platform/` 主线、`k_stock/` 产线、`quant_backtest/` 产线、`tools/` 治理工具链——均为活跃维护，不入本表逐项登记。

## 三、L1 冻结登记（≥2,000 行巨型文件，本日实测 15 个）

| 路径 | 行数 | 域 | 冻结原因 | 处置 |
|---|---:|---|---|---|
| `tools/gen_health_card.py` | 7,201 | 治理/工具 | 超大生成器，需评估是否含内嵌数据/可外置模板 | 只修 bug；Q4 评估拆分或模板化 |
| `portal/api_backtest.py` | 6,394 | 门户/回测 | 拆分方案未立项（api_kg 手法可复用） | 只修 bug；P1 出拆分方案 |
| `portal/portal_api.py` | 5,058 | 门户入口 | 行数预算 5,200（P4 拒收闸待启） | 只改不扩；新 handler 一律新文件 |
| `quant_backtest/kg_portal.py` | 3,350 | 图谱/量化 | 与 zerobound-kg 边界待收口 | 只修 bug；边界判例已立（外部子系统） |
| `quant_backtest/premarket.py` | 2,828 | 量化盘前 | 产线关键路径，拆风险高 | 只修 bug；变更走测试门 |
| `portal/api_misc.py` | 2,798 | 门户杂项 | 待并入归属模块 | 只修 bug |
| `quant_backtest/execution_engine.py` | 2,706 | 量化执行 | 与 paper 执行链耦合 | 只修 bug；改动须回测验证 |
| `portal/council_ops.py` | 2,439 | 军团/议事 | 页面+逻辑混合 | 只修 bug；P2 页面/逻辑分离 |
| `portal/api_chanlun_pages.py` | 2,403 | 缠论页面 | 页面 Handler 集中 | 只修 bug；随 portal 拆分收口 |
| `agent_platform/legion_command/serve.py` | 2,402 | 军团网关 | HTTP 端点集中 | 只修 bug；新端点新文件 |
| `portal/push_admin_server.py` | 2,391 | 推送管理 | 独立小服务 | 只修 bug |
| `portal/api_portfolio.py` | 2,383 | 持仓页面 | 页面 Handler 集中 | 只修 bug |
| `portal/kg/kg_pages_misc.py` | 2,310 | 图谱页面 | api_kg 拆分产物（后续批次继续拆） | L1，拆分期 |
| `tools/devtask.py` | 2,189 | 治理/工单 | 合并链路核心，行数增长（质量闸/锁/计时均在内） | 只修 bug；P2 拆 gates 子模块 |
| `quant_backtest/rps_resonance.py` | 2,109 | 量化策略 | 策略实现，改动需回测 | 只修 bug |

> 拆分进度：`portal/api_kg.py` 已 5,913 → 368 行（本次不列为 L1，回归 L0）；`api_kg` 过渡模块 `portal/kg/*` 待契约收尾后归位。

## 四、L2 归档登记（引用链实证，2026-10-30）

| 目录 | 文件数 | 大小 | 外部引用 | 处置 |
|---|---:|---:|---:|---|
| `quant_backtest/archive/` | 89 | 0.2MB | **0** | L2：只读保留 + README 标注；派生物无证据价值者转 L3（需审批） |
| `docs/audit/_archive/` | 2 | 0.0MB | 3（仅文档/索引引用） | L2：只读保留（审计证据） |
| `automation_runs/_archive/` | 1 | 0.4MB | **0** | L2 观察→L3 候选（保留 1 季度） |
| `automation_runs/ollama_guard/_archive/` | 1 | 0.0MB | **0** | L2 观察→L3 候选 |
| `news_collector/outbox/archive/` | 3 | 0.0MB | **0** | L2 观察→L3 候选 |
| `backup/` | 7 | 0.1MB | **0** | 需先确认用途（疑似旧备份脚本/样本）→ 登记 L2 待核 |
| `quant_backtest/data/archive/` | 30 | 63.5MB | **0** | **数据资产**：归数据治理（B 系列），不按代码 L3 处置；删除须数据线 L2 审批 |

## 五、L3 回收候选（首版）

| 候选 | 依据 | 前置条件 |
|---|---|---|
| `quant_backtest/archive/` 内 0 引用派生物 | `legacy_scan` 0 引用；非文档证据 | 逐项快照（catalog/manifest）→ L2 审批 → 回收站 |
| `automation_runs/_archive/`、`ollama_guard/_archive/`、`news_collector/outbox/archive` | 0 引用、非证据 | 保留观察至 2026-11-30；若仍 0 引用 → L2 审批后回收站 |
| `backup/`（用途待核） | 0 引用 | 先查创建者/用途（git log）再定 |

> **不得**仅凭本表直接删除：每个候选仍需逐项快照 + L2 审批（一次一议，回收站可逆）。

## 六、流程与 KPI

- **登记率**：L1/L2 目录 100% 入本表（本版覆盖 15 + 7 项）；新归档目录 30 天内登记；
- **月度归档评审**（A0 主持，30 分钟）：处理 L2→L3、L1→拆分/冻结续期；
- **每次 L3 处置**：`legacy_scan` 0 引用复证 → 快照 → 回收站 → 判例留档；
- **KPI（季度）**：无 owner 的 L1 = 0；L2 目录 100% 有 README/说明；活跃入口季度降 10%（配合 `entrypoints_v1.tsv`）。

## 七、复现命令

```powershell
# 引用链扫描（0 引用 = L3 候选；仅文档引用 = L2 候选）
python tools\legacy_scan.py --root . --path quant_backtest/archive --path docs/audit/_archive ^
  --path automation_runs/_archive --path automation_runs/ollama_guard/_archive ^
  --path news_collector/outbox/archive --path backup --path quant_backtest/data/archive

# 巨型文件行数
python -c "from pathlib import Path;[print(sum(1 for _ in open(p,encoding='utf-8',errors='replace')),p) for p in Path('.').rglob('*.py')]"
```

## 八、变更记录

- 2026-10-30 首版（关羽）：工具 `tools/legacy_scan.py` 交付；L1 15 项 / L2 7 目录 / L3 候选 5 组登记。
