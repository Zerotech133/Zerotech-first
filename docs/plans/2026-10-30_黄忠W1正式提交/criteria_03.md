# 验收判据 03：研究院角色属性一致性（WO-ACAD-ROLE-ATTR-20261029）

> 工单：WO-ACAD-ROLE-ATTR-20261029「复盘整改 A：仅修研究院角色属性一致性」
> 任务提交：ac45c66bd · 合并提交：37bbcfca3（merge task/WO-ACAD-ROLE-ATTR-20261029）· 2026-10-29 08:16 入 master
> 核对：`git cat-file -t 37bbcfca3` → commit；`git merge-base --is-ancestor 37bbcfca3 HEAD` → 退出码 0（master 祖先，2026-10-30 实测）

## 一、判据表（每条含证据路径/命令 + 通过线）

| # | 判据 | 证据路径/命令 | 通过线（阈值） |
|---|---|---|---|
| 1 | 研究院正式/协作双身份分离 | `agent_platform/registry/org.json` → `departments[dept=academy]`：`formal_agents` 为 7 席、`liaison_agents` 为 GUANYU/HUANGZHONG | `len(formal_agents) == 7`；`len(liaison_agents) == 2`；两协作席 `reports_to == "A0"` 且 `basis` 非空 |
| 2 | org 档案补 analysis_type/reports_to/parent_node_keys | 同文件 `agent_profiles`：ACAD1 → `reports_to=A0`、`parent_node_keys=[]`；STRAT1/BACK1/COMM1/INTEL1/AI1/PROD1 → `reports_to=ACAD1`、`parent_node_keys=["acad1_core"]` | 9 个 profile 全部 `analysis_type` 非空；7 正式席 `reports_to` 层级 `ok=7/7`；6 子席 parent 键 `ok=6/6` |
| 3 | team_academy 7 席对齐 | `agent_platform/registry/team_academy.json` → `agents` 恰 7 席（ACAD1, STRAT1, BACK1, COMM1, INTEL1, AI1, PROD1），子席 `member_kind="formal"`、`analysis_type` 非空 | 席位数 `== 7`；子席 `member_kind=formal` 命中 6/6；`analysis_type` 非空 6/6 |
| 4 | 只修属性、不扩能力 | 4 席 tools 统一含 `events`（前）：`org.json` → academy `agent_profiles[COMM1/INTEL1/AI1/PROD1].tools` 含 `"events"` | 4/4 席 `"events" in tools` 为真；未新增 registry 内不存在的能力项（diff 仅属性字段） |
| 5 | 属性一致性单测 | `C:\项目\py312\python.exe -X utf8 -m pytest -q -p no:cacheprovider -o addopts= agent_platform/tests/test_academy_role_attrs.py` | **4 passed / 0 failed**，rc=0（2026-10-30 实测：4 passed in 0.15s） |
| 6 | 现场数据核对命令 | `python -X utf8 -c` 读取 org.json/team_academy.json 打印 formal_agents/liaison_agents/reports_to/parent_node_keys/tools | 与判据 1–4 断言逐项一致（2026-10-30 实测输出：formal 7、liaison 2、ACAD1→A0、子席 parent=["acad1_core"]、HUANGZHONG academy_status=liaison） |

## 二、通过线汇总（可计数）

- 数据判据：`formal_agents=7`、`liaison_agents=2`、`reports_to` 断言 `ok=9/9`、`parent_node_keys` 断言 `ok=9/9`、`events` 工具保留 `ok=4/4`。
- 测试判据：`passed >= 4`、`failed <= 0`、`rc=0`。
- 阈值口径：任一项 `ok=0` 即退回；不得以「看起来合理」替代计数。

## 三、回归项（修复后不误伤）

| 回归项 | 命令 | 通过线 |
|---|---|---|
| 属性单测全过 | `pytest -q agent_platform/tests/test_academy_role_attrs.py` | 4 passed |
| 编制名称/席位唯一性 | `python -X utf8 tools/check_name_uniqueness.py --json` | `ok=true`、`violations=[]`（七检 names 项实测 cards=63 / in_headcount=62 / virtual=1） |
| 能力注册表无漂移 | `python -X utf8 agent_platform/tools/capability_self_audit.py --json` | `ok=true`、`drift=[]`、`total=14 ready=14`（七检 capabilities 项实测） |
| 会议/验收链路不误伤 | `pytest -q agent_platform/tests/test_hq_acceptance.py agent_platform/tests/test_academy_role_attrs.py tools/tests/test_task_board_audit.py` | 12 passed（2026-10-30 实测） |

## 四、不可逆点与边界

- 本单改动 `org.json` 与 `team_academy.json` 两个真源 + 1 个测试文件；回滚=`git revert ac45c66bd`（org.json 体量大，revert 冲突需人工确认，见回滚预案）。
- 边界：academy 之外部门未动；「不扩能力」只保证 4 席保留 events，不承诺能力面今后不扩展（后续批次自己的验收）。
- liaison 双身份（GUANYU/HUANGZHONG）`reports_to=A0` 属指挥部口径，`academy_status=liaison` 为展示字段，不改变其正式编制归属。
