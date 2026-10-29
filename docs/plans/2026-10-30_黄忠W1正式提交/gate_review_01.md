# 七检解读 01：全绿（2026-10-30 现场实跑）

> 命令：`C:\项目\py312\python.exe -X utf8 agent_platform\tools\quality_gate.py --json`
> 执行目录：`C:\项目\老田的AI助手\.worktrees\WO-HZ-W1FORMAL-20261030`
> 执行时间：2026-10-30 01:04–01:05（七项串行，总耗时约 128s）· 环境：`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`
> 原始输出：`C:\Temp\hz_w1_qg_live2.json`（交付副本 `C:\项目\deliverables\2026-10-30_黄忠W1正式提交_证据\quality_gate_20261030T010501.json`）
> 落盘状态：`agent_platform/data/quality_gate/status.json`（worktree 内，data 目录不入库）；席位回执 `QA1/QA2/SEC1/SEC2` 均 `patrol_verdict ... quality_gate ok=True checks=7`

## 一、原始输出（逐项真实值，2026-10-30T01:05:01+0800）

```
compileall    ok=True  rc=0  elapsed_ms=947     # 语法/字节码编译
ports         ok=True  rc=0  elapsed_ms=2648    # 端口注册自检（注册 56 / 离线 19 / 未登记 0 / 冲突 0）
secrets       ok=True  rc=0  elapsed_ms=4253    # 明文凭据扫描（基线豁免生效）
names         ok=True  rc=0  elapsed_ms=132     # 一名一席卡（cards=63 / in_headcount=62 / virtual=1 / hr_names=61 / violations=[]）
capabilities  ok=True  rc=0  elapsed_ms=5638    # 能力自检（total=14 / ready=14 / stale=false / drift=[]）
evals         ok=True  rc=0  elapsed_ms=7437    # 行为评估 smoke 子集
tests         ok=True  rc=0  elapsed_ms=71611   # 测试基线（155 passed, 1 skipped in 70.65s）
overall       ok=True  checks=7
```

## 二、逐项判读

| 检查项 | 结果 | 判读 / 证据 |
|---|---|---|
| compileall | ok=True rc=0 947ms | `python -m compileall -q agent_platform tools` 全通过；语法/字节码层无失败文件 |
| ports | ok=True rc=0 2648ms | 注册 56 个、在册离线 19 个（8081/8104 等仅提示不判负）、**未登记 0、冲突 0**；本轮不涉及端口变更 |
| secrets | ok=True rc=0 4253ms | 显式扫描 `agent_platform tools ops docs`；二进制/超大文件按规则跳过，明文命中 0；本单为 docs-only，未引入凭据 |
| names | ok=True rc=0 132ms | `check_name_uniqueness --json`：cards=63、in_headcount=62、virtual=1、hr_names=61、`violations=[]`、`warnings=[]`；编制口径一致 |
| capabilities | ok=True rc=0 5638ms | `capability_self_audit --json`：total=14、ready=14、stale=false、drift=[]、source=skills_registry；技能注册表与真实工具无漂移 |
| evals | ok=True rc=0 7437ms | `run_evals --smoke --json`：smoke 子集全过（含 gov.trace-writable、gov.approval-kinds、gov.approval-signer-required 等） |
| tests | ok=True rc=0 71611ms | 核心套件 **155 passed, 1 skipped / 0 failed**（71s 内）；排除 live-state 组，无新增失败 |

## 三、结论与放行口径

- 7/7 全绿，`ok=True`，放行；`>=7` 项 `ok=true` 且无 rc 非 0，阈值 `失败项 <= 0`。
- 重点复核：`ports` 看「未登记监听/冲突」必须为 0（实测 0/0）；`tests` 看失败数 `<=0`（实测 0，155 passed）。
- 任一项 `ok=false` 即不得放行；不得只看 overall，须逐项留证（本文件即为逐项证据）。
- 现场踩坑记录：不设 `PYTHONUTF8=1` 时，本轮首跑 `capabilities` 因子进程 `gbk` 解码线程异常（`UnicodeDecodeError`）失败（`rc=1`）；设置 UTF-8 环境变量后复跑 7 项全绿。属会话编码环境问题，非代码缺陷；后续七检一律带 `PYTHONUTF8=1`。

## 四、交叉印证

- 历史全绿基线：`agent_platform/data/quality_gate/history.jsonl` 最近一次全绿条目 `2026-10-27T14:12:45+0800 ok=True failed=`（2026-10-28 08:38 条目为 `tests` 失败、2026-10-29 08:37 条目为 `capabilities,evals,tests` 失败，均早于本日实跑）。
- 本单实测时间晚于上述条目，且 `tests` 数 155 passed 与 `quality_gate` 核心套件清单一致。
