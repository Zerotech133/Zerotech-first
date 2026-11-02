# 并行开发 wave48 · 归档契约 v2 / 工具弃用 / lifecycle v2 · 回执

> 时间：2026-11-02 18:45–2026-11-03 00:25｜三条支线（2 条中途失败，Lead 收口）｜**门禁 PASS、31 tests、夜间任务全绿**

## 一、统一归档契约 v2（task-134，部分由 Lead 收口）
| 项 | 结果 |
|---|---|
| `learning_ops.py archive` | manifest 输出升级 **learning_archive_manifest.v1**（project_id/status/archived_at/owner_ai=DATA1/artifacts[{path,sha256,size}]/conclusions/forbidden/continue_entry），保留旧字段 |
| 旧归档适配 | 为学习PDF生成 `_archive/2026-11-02_学习PDF_量学/manifest.v2.json`（**43 个 artifacts**），不覆盖旧 manifest/ENTRY |
| gate 归档校验 | **优先 manifest.v2.json**（schema+sha256+工件存在性），无 v2 回退旧 manifest；工件路径兼容 仓库/平台 deliverables/绝对路径 |
| 验证 | `gate --stage all --strict` → **ok=true（2 项目/5 检查/0 失败）**；报告 `learning_gate_20261103.{json,md}` |

## 二、旧卡工具弃用（task-135，已完成）
| 项 | 结果 |
|---|---|
| 标记 | 9 个历史工具加 `DEPRECATED（2026-11-02 · v2 已统一）`，行为零改动：card_proposal v1–v4、card_precheck、card_precheck_v3、card_revision、card_revision_apply、card_revision_round2 |
| 统一路径 | `card_from_units.py`（生成）→ `card_precheck_laomu.py`（预审）→ `learning_release.py`（发布门） |
| 清单 | `docs/standards/2026-11-02_学习工具弃用清单_v1.md` |
| 测试 | `test_learning_deprecated_headers.py` **4 passed** |

## 三、注册表 lifecycle v2（task-136，部分由 Lead 收口）
| 项 | 结果 |
|---|---|
| registry | 每项目新增 `schema_version=learning_project.v1` + `lifecycle{stage,stage_updated_at,gate{ok,checked_at},history[]}`（旧字段保留） |
| 看板 | dashboard 聚合 lifecycle/gate，schema v2；构建时写回 registry `lifecycle.gate`（仅两字段） |
| 当前状态 | learnpdf=archived（gate ok）、laomu=distilling（gate ok，checked_at 00:20） |
| 测试 | dashboard 合约测试通过（5 文件合计 **31 passed**） |

## 四、夜间自动运行验证（18:37 → 00:03）
| 任务 | 最近运行 | 结果 |
|---|---|---|
| LearningIntake（每 30 分钟） | 00:03:15 | rc_pipeline=0 rc_gate=0 ✅ |
| LearningPipeline（20:00） | 20:00:01 | rc_pipeline=0 rc_gate=0 ✅ |
| LearningGovernance（02:00） | 18:37:55（手动） | 下次 02:00 |
| LearningStandardize（周一 10:00） | 18:38:01（手动） | 下次 11-09 10:00 |
| 队列问题 | 电池策略修复后**未再出现 Queued** | ✅ |

## 五、验收
| 项 | 结果 |
|---|---|
| 测试 | 5 组学习测试 **31 passed** |
| 门禁 | 语法 PASS / 导航 PASS（88 页）/ 路由 290 零 diff / 数据仓元数据 PASS |
| gate | `learning_gate_20261103.json`：ok=true，fail=0 |

## 六、产物清单
- 归档：`tools/kb_corpus/learning_ops.py`（archive v2 + adapt）、`learning_gate.py`（v2 优先 + 路径兼容）、`_archive/2026-11-02_学习PDF_量学/manifest.v2.json`
- 弃用：9 个工具头标记、`docs/standards/2026-11-02_学习工具弃用清单_v1.md`、`tools/tests/test_learning_deprecated_headers.py`
- lifecycle：`agent_platform/registry/learning_projects.json`、`tools/kb_corpus/learning_dashboard_build.py`、`portal/static/learning_projects_dashboard.json`
- 门禁报告：`research/reports/learning_gate_20261103.{json,md}`

## 七、说明（如实）
- task-134、task-136 的 teammate 在完成前失败（无收尾报告），但代码/测试已基本落盘；Lead 补齐了 manifest.v2 生成、工件路径解析、看板重建、gate 复跑与验证；
- 旧卡工具仅标弃用、不改逻辑；新发布唯一入口 = `learning_release.py`。
