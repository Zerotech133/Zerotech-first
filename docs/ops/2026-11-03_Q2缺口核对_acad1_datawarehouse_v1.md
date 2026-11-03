# Q2 缺口核对：acad1-legion / data-warehouse v1

- 日期：2026-11-03 21:58｜依据：PLATFORM_FRAMEWORK_v1 §Q1–Q4
- Q2 判据：**Q1 已登记 + 引用可解析 + 健康探针 + SLO + 回滚步骤 + 至少 1 个测试**
- 结论：**两组合均未达 Q2；保持 lifecycle=draft / phase=shadow / validated=false。**

## 一、逐项

| 判据 | acad1-legion | data-warehouse |
|---|---|---|
| Q1 已登记 | ✓ registry + manifest 一致 | ✓ registry + manifest 一致 |
| 引用可解析 | ✓ resolver 126/126 | ✓ resolver 126/126 |
| 健康探针 | ✗ `health=unknown`，无 probe 字段/端点 | ✗ `health=unknown`，无 probe 字段/端点 |
| SLO | △ 有 daily_rc/pool_consistency/review_cards/human_decision；无指标告警闭环 | △ 有 datasets/registered/exposed 基线；无目标达成/告警闭环 |
| 回滚步骤 | ✗ manifest 无 rollback 字段/步骤 | ✗ manifest 无 rollback 字段/步骤 |
| 至少 1 测试 | ✓ `test_academy_wiring.py` 等 | ✓ datawarehouse 340 passed（主仓） |

## 二、缺口动作

### acad1-legion

1. **健康探针**：定义 `/api/acad1/pool` 或 8105 health 的只读探针（schema：ok/checked_at/evidence），落 manifest `status.health_probe`；
2. **回滚步骤**：manifest 增加 rollback（revert manifest / 停入口 / 恢复 PG 投影），并演练；
3. **SLO 告警**：把 pool=30 / freshness=1d / evidence≤26h 接到每日检查；
4. 11-08 前 13 张 review 卡冻结是另一条门，不影响 Q2 字段但影响 ACAD1 正式化。

### data-warehouse

1. **健康探针**：`/api/dw/stats@8091` 或 catalog/registry 只读探针，落 manifest；
2. **回滚步骤**：registry/manifest revert + 8091 reload/观测；
3. **SLO 告警**：登记率 84.4%、暴露 72/314 是基线不是达标；DWH-103 整改期 11-12 前补；
4. `/api/dw/*` 经 8734 仍 401（L2 待批），Q2 只能在 8091 内可用口径下评估。

## 三、结论

- **Q2 不成立**：健康探针、回滚步骤缺失；SLO 有字段但无告警闭环；
- 不把 Q1 影子账本写成 active/Q2；
- 11-16 复审前按上表补证，再走 lifecycle `draft → validated`（需 owner 证据 + ARCH1 抽检）。

## 四、建议 owner

| 项 | owner |
|---|---|
| 健康探针/回滚字段 | ACAD1 / DATA1 |
| SLO 告警接线 | ARCH1 |
| Q2 证据复核 | QA/ARCH1 |
| 11-16 定稿 | A0 |

## 五、健康探针工具（2026-11-03 22:18）

新增 `tools/q2_probe.py`（只读 GET）：

- acad1-legion：`/api/acad1/pool` 200、`8105/api/v1/health` 200；
- data-warehouse：`/api/dw/stats` 200、`/api/dw/search?q=__none__` 200（state=no_data 属于正确空结果）；
- 实测 summary：**2/2 composition ok**。

**但 Q2 仍未达成**：探针证据只在工具输出，manifest 还没有 `status.health_probe` 字段；回滚步骤也仍缺。需 owner 把探针端点/字段写进 manifest 后，才能从 Q1→Q2。


## 六、2026-11-03 22:46 更新：探针/SLO 已写进 manifest

- `acad1-legion` status 增加 `health_probe`（/api/acad1/pool + 8105 health，last_result=ok）与 `slo_alert`（ARCH1，freshness/stale/pool/data_date，last_result=ok）；
- `data-warehouse` status 增加 `health_probe`（/api/dw/stats + /api/dw/search，last_result=ok）与 `slo_alert`（注册率/暴露率；last_result=**alert**，84.4%/22.9%）；
- `composition.py validate --all --strict`：**6/6 PASS**；
- SLO 评估器 `tools/q2_slo_alert.py`：acad1 ok、data-warehouse 2 alerts；
- **lifecycle 仍 draft、validated=false，不写成 active/Q2**。

Q2 剩余缺口：**rollback 步骤**仍未写；validated 需 11-16 复审。 


## 七、2026-11-04 02:26：rollback 字段已补

- `acad1-legion` / `data-warehouse` 的 `status.rollback` 已写 4 步：revert manifest/registry → 摘入口保留审计 → 恢复只读投影/重载 8091 → q2_probe + composition validate；
- `last_drilled: pending`，**尚未演练**；
- `composition.py validate --all --strict`：**6/6 PASS**；
- lifecycle 仍 `draft`、`validated=false`，仍未写 active/Q2；
- Q2 剩：rollback 演练 + 11-16 复审后 validated。

schema 说明：rollback 必须放 `status.*`（`spec.additionalProperties=false`）。
