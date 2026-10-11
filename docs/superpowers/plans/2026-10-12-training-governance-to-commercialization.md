# 训练候选治理到商业化准备实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按“训练候选治理 → 只读 RAG 基线 → P1 可观测性 → 策略分层验收 → 平台整合 → 商业化准备”的顺序，建立可复核、默认阻断的研究门禁链。

**Architecture:** 复用现有训练候选审计、RAG 基线和 P1 可观测性报告，新增声明式策略验收矩阵与只读审计器。审计器只读取 manifest、证据快照和历史报告，生成结构化 verdict；任何缺证据、标准定义未认证、交易日日历未认证、P1 实际回执缺失时均保持 blocked。平台整合与商业化只在前置门禁形成可复核通过记录后进入设计评审，不改变金融执行开关。

**Tech Stack:** Python 3.11、JSON、pytest、PowerShell 只读检查。

**Spec:** `docs/audit/2026-10-12-P1可观测性审计与下一阶段.md` 与 `docs/plans/2026-10-11_开发计划总表与进度看板.md`。

## Global Constraints

- 训练、LoRA、金融训练、模拟成交、交易和收费上架保持关闭。
- 只读审计工具不得修改来源、许可证、权限快照、策略、账本或任务数据库。
- 标准定义和交易日日历未认证时，不得输出可上线或可交易结论。
- 所有证据必须含来源路径、哈希或可信回执；排程配置不得替代实际执行回执。
- Windows 路径与输出均使用 UTF-8；不写含中文路径的 `.ps1`/`.bat`。

## Review Focus

- 缺少样本 SHA256 或时间切分时，样本必须被拒绝并保留原因。
- 同一 `split_group` 跨 train/validation/test 时，矩阵必须拒绝时序泄漏。
- 缺行情、缺基准或缺成本参数时，策略 verdict 必须为 blocked。
- 只有排程配置、没有实际运行产物时，P1 前置必须为 blocked。
- 即使证据完整，标准/日历未认证时也不得产生 live、训练或收费资格。

### Task 1: 建立策略验收矩阵

**Files:**
- Create: `work/strategy-acceptance-v0.1/strategy-acceptance-matrix-v0.1.json`
- Create: `work/strategy-acceptance-v0.1/README.md`

**Interfaces:**
- Produces: 四策略（缠论、九维、weak、双刀）的最小样本、时间窗、基准、成本、缺数据拒绝、暂态结构证据要求。

- [x] 写入声明式矩阵，所有生产/金融开关为 `false`。
- [x] 为每个策略写出 `required_evidence`、`missing_data_policy`、`transient_structure_evidence` 和 `minimums`。
- [x] 在 README 记录矩阵版本、只读边界和当前 blocked 原因。

### Task 2: 实现只读验收审计器

**Files:**
- Create: `tools/audit_strategy_acceptance.py`
- Create: `tools/test_audit_strategy_acceptance.py`

**Interfaces:**
- `audit_matrix(matrix, evidence, p1_report=None) -> dict`
- CLI: `python tools/audit_strategy_acceptance.py --matrix <path> --output <path> [--evidence <path>] [--p1-report <path>]`

- [x] 先写失败测试，覆盖完整性、时间泄漏、缺证据、P1 缺回执和全局金融门禁。
- [x] 实现只读解析，输出每策略 `verdict`、`reasons`、`evidence_summary`。
- [x] 强制 `production_eligible=false`、`financial_execution_enabled=false`。
- [x] 输出稳定 JSON，便于日报和后续平台页面消费。

### Task 3: 运行审计并收口门禁报告

**Files:**
- Create: `work/strategy-acceptance-v0.1/strategy-acceptance-report-v0.1.json`
- Modify: `docs/audit/2026-10-12-P1可观测性审计与下一阶段.md`

- [x] 使用当前训练候选、RAG、P1 报告运行审计。
- [x] 记录四策略 blocked 的真实原因，不补造回执。
- [x] 记录下一批仅允许补交的证据类型。

### Task 4: 进入平台整合前置评审

**Files:**
- Create: `docs/plans/2026-10-12_平台整合与商业化前置门禁.md`

- [x] 仅登记导航、端口、worktree、独立评审门和回滚要求。
- [x] 明确平台整合与商业化仍为 pending，不触碰线上金融能力。

## Verification

- `python -m pytest tools/test_audit_strategy_acceptance.py -q`
- `python tools/audit_strategy_acceptance.py ...`
- `python tools/check_ports.py --strict`
- `python tools/_mixin_integrity.py --check`
- `python tools/unify_nav.py --check`
