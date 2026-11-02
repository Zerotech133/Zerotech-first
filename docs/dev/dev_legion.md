# 研发军团作战屏 v1 · 使用与数据口径

- **页面**：`/dev_legion.html`（模块验证可先直接打开 `portal/dev_legion.html`；live 8734 需 LS-11 重启后生效）
- **API**：`GET /api/dev/legion`（只读）
- **契约**：`docs/dev/legion_screen_contract.json`（frozen v1.0）
- **ADR**：`docs/adr/ADR-020-dev-legion-screen-contract.md`
- **冒烟**：`python -X utf8 tools/dev_legion_smoke.py`

## 一、数据源
| 数据 | 真源 | 缺失时 |
|---|---|---|
| 组织/席位/组/汇报线 | `agent_platform/registry/org.json` | `seats=[]` + errors |
| 在制任务 | `.worktrees/tasks.json` | `work_in_progress=[]` + errors |
| C6 指标 | `agent_platform/data/dev/daily_metrics.json` | `metrics` 空值 + errors |
| AI/量化/风控卡 | 同上（派生） | 空列表 |

> 接口恒 HTTP 200，错误放 `errors[]`，不 500。

## 二、页面板块
1. 组织架构：部门/主官/副主官 + G1/G2 + 11 席卡（姓名/岗位/工程域/组/档位/模型/汇报线）；
2. AI 席位卡：模型/档位/评估状态；
3. 量化席位卡：模型/策略/注册状态（未接如实标注）；
4. 回测/风控卡：Go-No-Go/判据冻结（未接如实标注）；
5. 在制任务：owner→工单列表；
6. C6 指标：近排单/归属率/验证覆盖/E3/前置 p50；
7. 红线状态：可判定项 ✅/❌，不可判定标"人工"。

## 三、验收
```powershell
python -X utf8 -m pytest agent_platform/tests/test_dev_legion_api.py `
  agent_platform/tests/test_dev_legion_metrics.py `
  agent_platform/tests/test_dev_legion_ai_card.py `
  agent_platform/tests/test_dev_legion_quant_card.py `
  agent_platform/tests/test_dev_legion_risk_card.py `
  agent_platform/tests/test_dev_legion_ui.py `
  agent_platform/tests/test_legion_contract.py -q
python -X utf8 tools/dev_legion_smoke.py
```

## 四、边界
- 只读：无写接口、不读凭据、不调生产；
- 不新增端口；接现有 8734 门户；
- 页面样式只引 `tokens.css`，不自绘色值；
- 字段只增不改；变更走 ADR 修订。
