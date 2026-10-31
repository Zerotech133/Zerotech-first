# lifecycle 四态（archived）schema 提案 v1（2026-10-30 · 关羽）
> STATUS: superseded（见 PLAN_REGISTER_ALL_20261031）



> 目的：把治理方案 §5.3 的四态（active / planned / deprecated / **archived**）补齐为**代码可执行**语义，
> 为「deprecated 满 90 天 → archived」迁移提供消费方一致行为。**真源仍由数据线（DATA1）维护；本提案只做代码就绪 + 规则定义，YAML 登记变更需数据线会签。**

## 一、现状缺口

| 层 | 现状 |
|---|---|
| `data_lifecycle.yaml`（真源） | 注释只写 planned / deprecated / active（**无 archived**） |
| `tools/datawarehouse/lifecycle.py` | `status_for` 只认 planned/deprecated/active，未知值归一 active；`SKIP_STATUSES` 不含 archived |
| 消费方 | metadata_consistency / registry_rebuild / freshness_report / manifest_repair 的 skip 集合 = planned/deprecated（archived 会被当 active → 误报新鲜度/资格欠账） |
| 审计报告 | `lifecycle_coverage.py` 已按四态统计（含 archived），但代码消费方未对齐 → **口径不一致** |

## 二、archived 语义定义（提案）

| 状态 | 含义 | registry 资格 | freshness | metadata store 存在性 | 下游使用 |
|---|---|---|---|---|---|
| `active` | 正常在用（默认，无需登记） | 正常检查 | 正常检查 | 必须存在 | 允许 |
| `planned` | 规划中未实现 | 不计 unknown 欠账 | 跳过 | 可缺失 | 禁止 |
| `deprecated` | 已废弃（保留数据/兼容读） | 不计 unknown 欠账 | 跳过 | 可缺失 | 只读/迁移中 |
| **`archived`** | 已归档（原文件移 archive/ 或只留快照；**不再采集**） | 不计 unknown 欠账 | 跳过 | 可缺失 | 仅审计/回溯 |

- 与 deprecated 的唯一差别：**archived 表示物理迁移已完成**（YAML 中应带 `archived_path`/`archived_at` 建议字段，可选），deprecated 表示"尚未迁移"。
- 迁移链：`active → deprecated →（≥90 天且影响面报告通过）→ archived`；archived 不可自动回到 active（需数据线人工恢复 + 重新采集中断说明）。

## 三、本批代码就绪（P72 已实现，待数据线会签后生效）

| 文件 | 变更 |
|---|---|
| `tools/datawarehouse/lifecycle.py` | `VALID_STATUSES` 四态；`SKIP_STATUSES` 含 archived；`status_for/skipped` 语义更新 |
| `tools/datawarehouse/metadata_consistency.py` | `SKIP_LIFECYCLE` 含 archived（store 缺失豁免、placeholder 归属校验） |
| `tools/datawarehouse/registry_rebuild.py` | archived 表标记 qualification_status=archived、不计 unknown 欠账 |
| `tools/datawarehouse/freshness_report.py` | 经 `lifecycle.skipped()` 自动跳过（注释更新） |
| `tools/datawarehouse/manifest_repair.py` | 经 `lifecycle.skipped()` 自动跳过（注释更新） |
| `quant_backtest/build_catalog.py` | 注释更新（catalog 写 lifecycle 状态为透传） |
| `tools/datawarehouse/tests/test_lifecycle_archived.py` | 新增 3 测试（archived 有效/skip、未知值归一、all_overrides） |

## 四、需要数据线会签的动作（不在本提案自动执行）

1. **真源 schema 注释**更新：`agent_platform/catalogs/data_lifecycle.yaml` 头部「合法值」补 `archived` 与迁移规则；
2. **首个 archived 登记**：对首批验证对象（建议先 1 张低风险 deprecated 表）登记 `lifecycle: archived`，验证链路；
3. **重生成与对账**：`CATALOG_VERSION_OVERRIDE` 递增 → `build_catalog` → `registry_rebuild --apply` → `metadata_consistency --check`；
4. **月度审计**：`lifecycle_coverage.py --json` 纳入月报（分布中 archived 计数）。

## 五、影响面与风险

- 代码侧 fail-open：YAML 无 archived 条目时行为与现状完全一致（零影响）；
- 若数据线登记 archived 但未跑 registry 重建 → registry 可能短暂按旧状态检查（建议同一变更窗口执行 §四.3）；
- 回滚：代码回滚 = revert 本单（无数据写入）；YAML 回滚 = 改回 deprecated/active。

## 六、验证

```powershell
python -m pytest tools/datawarehouse/tests/test_lifecycle_archived.py -q
python tools\datawarehouse\lifecycle_coverage.py --json   # archived 计数出现于分布
python tools\datawarehouse\metadata_consistency.py --json  # 仍 PASS（当前无 archived 条目）
```

## 七、会签栏

| 角色 | 意见 | 签字/日期 |
|---|---|---|
| 数据线（DATA1） | 待签 | |
| 治理（关羽） | 提案发起 | 2026-10-30 |
