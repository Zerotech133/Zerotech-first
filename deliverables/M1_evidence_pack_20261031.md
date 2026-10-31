# M1 信任链证据包（M1 evidence pack）

- 生成时间：2026-10-31T13:08:53+08:00
- 仓库：`C:\项目\老田的AI助手\.worktrees\WO-LEGION-M1-EVIDENCE-20261031` · 主仓 `C:\项目\老田的AI助手`
- commit：`c951bffc1515`（task/WO-LEGION-M1-EVIDENCE-20261031）
- 主机：os=nt · python=3.11.9

**判定：FAIL**（必需判据 通过 2 / 失败 1 / 未知 0，共 3 项）

## 判据表

| # | 判据 | 必需 | 状态 | 来源 | 摘要 |
|---|---|---|---|---|---|
| 1 | M1 信任链 KPI（trust_chain_kpi） | 是 | FAIL | trust_chain_kpi.py | verdict=fail pass=8 fail=1 unknown=0（共 9 项） |
| 2 | W1 发布回滚演练（release_rollback_drill） | 是 | PASS | release_rollback_drill.py | ok=True 场景 5/5 · 检查失败 0 |
| 3 | W2 工作树/abort 不变量（worktree_invariant_check） | 是 | PASS | worktree_invariant_check.py | verdict=PASS 阻断违规=0 已知欠账=34 工单=1830 |
| 4 | M4 四方签字（只读派生） | 否 | FAIL | trust_chain_kpi.py | approve=0 dispatch_done=- missing=QA1,GUANYU,VERIFY1,A0/老田 |
| 5 | 最新 fast gate 日志 | 否 | PASS | test_gate_p130.log | result=pass age=0.7h · GATE PASS: 无新增失败 |
| 6 | legion_ide evidence 最新 artifact | 否 | PASS | evidence | patchset=200 run=271 最新=patchset-rollback-T-BOOT-741c2ea1.json |

## 服务版本 / 健康（只读派生）

| 项 | 值 |
|---|---|
| 8103 LDE 版本 | 20261031_092952_7e6dbe8a |
| 8103 commit | 7e6dbe8a95b73474fba2eb415dd16d8b3ea4bff2 |
| 8103 version all_match | True |
| 8103 health ok / rw_enabled | True / True |
| 8787 Hermes 会话门 | pass |
| 发布指针 / junction 版本 | 20261031_092952_7e6dbe8a / 20261031_092952_7e6dbe8a |
| 发布 drift | False |
| 最新 patchset evidence | C:\项目\_vault\legion_ide\evidence\patchset-rollback-T-BOOT-741c2ea1.json |
| patchset signature_verified | true |
| M4 四方签字（派生） | fail |

## 缺口清单（2）

| # | 判据 | 必需 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | M1-KPI | 是 | FAIL | verdict=fail pass=8 fail=1 unknown=0（共 9 项） |
| 2 | M4-SIGNOFF | 否 | FAIL | approve=0 dispatch_done=- missing=QA1,GUANYU,VERIFY1,A0/老田 |

## evidence 目录最新 artifact（只读清单）

- 目录：`C:\项目\_vault\legion_ide\evidence` · patchset=200 / run=271

| 文件 | 类型 | mtime | age(h) | 说明 |
|---|---|---|---|---|
| patchset-rollback-T-BOOT-741c2ea1.json | patchset | 2026-10-31T13:08:20+08:00 | 0.0 | task=T-BOOT ok=True signature=True |
| patchset-T-BOOT-eeb0e426.json | patchset | 2026-10-31T13:08:20+08:00 | 0.0 | task=T-BOOT ok=True signature=True |
| run-b664bfce6461.json | run | 2026-10-31T13:08:13+08:00 | 0.0 |  |
| run-c3e0f9ab72ca.json | run | 2026-10-31T13:08:13+08:00 | 0.0 |  |
| patchset-rollback-T-BOOT-af8bac9a.json | patchset | 2026-10-31T13:06:31+08:00 | 0.0 | task=T-BOOT ok=True signature=True |
| patchset-T-BOOT-2dd15a1e.json | patchset | 2026-10-31T13:06:30+08:00 | 0.0 | task=T-BOOT ok=True signature=True |
| run-89686233fe8f.json | run | 2026-10-31T13:06:24+08:00 | 0.0 |  |
| run-b44b4d3fc1be.json | run | 2026-10-31T13:06:24+08:00 | 0.0 |  |

## 输入源采集

| 源 | 模式 | 状态 | 耗时(ms) | 路径/说明 |
|---|---|---|---|---|
| kpi | subprocess | OK | 271 | C:\项目\老田的AI助手\tools\trust_chain_kpi.py |
| drill | subprocess | OK | 934 | C:\项目\老田的AI助手\tools\release_rollback_drill.py |
| invariant | subprocess | OK | 160 | C:\项目\老田的AI助手\tools\worktree_invariant_check.py |
| fast_gate | file | PASS | - | C:\Temp\test_gate_p130.log |

## 说明

- 只读聚合：不写 approval/任务板/tasks.json、不重启 8787/8103、不代签、不 promote artifact
- 失败项（工具缺失/超时/输出非 JSON/文件不可读）一律标 unknown 并保留错误原文，不伪造数值
- M4 状态由 KPI m4_signoff 检查派生（只读计数），签字须 owner 本人完成
- fast gate 日志与 evidence 清单为 advisory：不参与总判定，但出现在缺口清单
- worktree 内运行时账本/配置以主仓为准（自动解析主仓并传 --repo 给 KPI/不变量工具）

