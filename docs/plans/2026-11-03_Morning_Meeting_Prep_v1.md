# 五角色晨会议程准备 v1（C2）

- **状态**：`draft`（工具+测试已落地，待 A0/DSH 验收；C2 连续 5 个工作日验收从 11-03 起算）
- **日期**：2026-11-03
- **依据**：`docs/plans/2026-11-03_HQ_Morning_Meeting_Runbook_v1.md`（五角色晨会运行卡）、`docs/plans/2026-11-03_RND_Plan_v1.md`（C2 工作包与 P0 出口门）、`agent_platform/command_hq/roles.py`（五角色真身份）、`tools/hq_directives.py`（Directive 只读模型）、`tools/hq_reports.py`（LegionReport 红/黄/绿）、`tools/hq_agenda.py`（议题分类/议程聚合）
- **性质**：**只读会前准备**——把「今日晨会草案」一键生成（Markdown/JSON）；**不写生产会议、不自动派单、不调模型、不启停服务**；决议仍由 A0 人工拍板、纪要仍由 `/hq` 晨会流程落盘
- **工具**：`tools/hq_meeting_prep.py`（新增）｜**测试**：`tools/tests/test_hq_meeting_prep.py`（8 例全绿）

## 一、背景与定位

C2 要求「五角色晨会恢复」：连续 5 个工作日、5 份纪要、25 条 RoleOpinion、≥5 条 Directive。会前准备（选题/角色提示/证据要求/期望产出）过去靠田丰手工从 `/hq` 翻页完成，效率低且容易漏项。本工具把这一步变成**确定性草案**：

```
Directive（会议 decisions/orders + 会前议题） + LegionReport（红/黄/绿） + 任务板
        ↓ 选题规则（未派单 > 红/黄军团 > 到期；最多 3 项）
晨会草案（date + 3 topics + 每 topic 的 role_prompts/evidence_needed/desired_output/due/agenda_items）
        ↓ A0 主持确认（人工）
/hq 晨会落纪要（hq_meeting，不在本工具写） → Directive/task 派单
```

## 二、使用方式

```powershell
cd C:\项目\老田的AI助手

# 1) 默认：Markdown 草案（打印，不写盘）
python -X utf8 tools\hq_meeting_prep.py

# 2) 机器可读 JSON（stdout 纯 JSON，便于 /hq 或脚本消费）
python -X utf8 tools\hq_meeting_prep.py --json

# 3) 显式 Markdown / 指定上限 / 指定基准日
python -X utf8 tools\hq_meeting_prep.py --markdown
python -X utf8 tools\hq_meeting_prep.py --max-topics 2 --date 2026-11-03

# 4) 只写指定文件（.json / .md；不写生产目录）
python -X utf8 tools\hq_meeting_prep.py --out C:\Temp\hq_prep.json
python -X utf8 tools\hq_meeting_prep.py --out C:\Temp\hq_prep.md --markdown

# 5) 调试/测试：跳过角色提示生成（roles_expected 仍保留）
python -X utf8 tools\hq_meeting_prep.py --json --no-prompts
```

- **只读数据源**：`agent_platform/data/meetings/hq/hq-*.json`（纪要 decisions/orders）、`portal/data/council_new_topics.json`（会前议题）、`.worktrees/tasks.json`（任务板/链接校验）、`compositions` 与 `agent_platform/registry`（军团/席位真身份，经 hq_directives/hq_reports/hq_roles 复用）。
- **退出码**：`0` 成功；`2` fail-visible（无真源数据 / 无候选议题 / 参数或环境错误）。无数据时**不生成空草案**。
- 可选参数：`--meetings-dir/--council-dir/--tasks-file`（测试/演练指向临时目录）；`--no-prompts`（快速调试）。

## 三、输出字段（schema `hq/meeting_prep/v1`）

| 字段 | 说明 |
|---|---|
| `ok/read_only/no_write` | 是否成功；`read_only=true`、`no_write=true`（硬边界自证） |
| `date/generated_at` | 议题日期（默认今天）/ 生成时点 |
| `sources` | meetings 数、council_topics 数、tasks 可用性、directives 数、reports 状态、roles 数 |
| `selection` | 选题规则、候选数、选中数、`red_legions/amber_legions/unresolved/due` 计数、`max_topics` |
| `roles` | 五角色真身份（role/name/seat/duty/never/model/prompt_exists） |
| `topics[]` | **每议题**：`topic_id`（`HQ-YYYYMMDD-NN`）/ `kind`（研发\|挖掘\|分析\|商业\|其他）/ `title` / `owner` / `legion` / `due` / `due_state`（overdue\|due_soon\|""）/ `roles_expected`（五角色）/ `role_prompts`（五角色意见征询提示）/ `evidence_needed[]` / `desired_output` / `agenda_items[]`（会上四步）/ `priority`（类别/分数/来源 Directive/状态）/ `source_refs[]` |
| `agenda_items[]` | 选中议题的一行议程（order/topic_id/kind/title/owner/due） |
| `warnings[]` | 降级/缺口（如 reports 不可用、roles 降级、任务链接未解析）——不静默 |
| `next` | 下一步提示（A0 确认 → /hq 落纪要 → Directive 派单） |

`role_prompts` 是**意见征询提示**（含角色职责/边界/证据要求/RoleOpinion 契约），不是角色意见；参会角色本人（或授权代理）在会上给真实意见。

## 四、选题规则（确定性）

1. **候选池**：`hq_directives.build_directives()` 的 Directive（kind=decision/order/topic）+ `hq_reports.build_reports()` 的 LegionReport（红/黄）。
2. **优先级**（任务单口径：未派单 Directive > 红/黄军团 > 到期事项）：
   - **未派单**：无 `task_id` 且 `status∈{open,draft,pending}` 且 kind 合法 → 最高；
   - **红/黄军团**：legion 报告 `status=red/amber` 的议题（`unmapped` 桶不算真军团，不参与该规则）；若红军团无对应 Directive，则用 LegionReport 的 blockers 生成一条军团议题（真数据，不编）；
   - **到期事项**：`due ≤ 今天+2 天`（含逾期），未关闭；逾期略高于临期。
   - 同优先级排序：到期越早越前 → 创建越新越前 → id；**最多 3 项**（`--max-topics` 可降为 1/2，最多 3）。
3. **去重**：按 Directive id 去重；每个红/黄军团最多一条军团议题。
4. **fail-visible**：Directive 源不可用 → `error=no_source_data`；有源但无候选 → `error=no_candidates`；均 rc=2。

## 五、输出示例（2026-11-03 真实数据，草案）

```text
# 五角色晨会草案 · 2026-11-03
- 性质：只读草案（未写生产会议/未派单/未调模型）
- 来源：meetings=8 议题=9 tasks=on directives=12 reports=ok
- 挑选规则：未派单 Directive > 红/黄军团 > 到期事项（今天+2 天）；候选 16 → 选中 3
- 红/黄军团：红军团 quant-strategy-line / 黄军团 acad1-legion,hq-audit,...

## 五角色（真实身份/模型绑定）
| 田丰（TIANFENG） | A0 | 目标拆解/DAG/派单 | glm/glm-5.3 |
| 诸葛亮（ZHUGE） | AI1 | 反向质询/pre-mortem | openai/gpt-6.0 |
| 赵云（ZHAOYUN） | ZHAOYUN | 独立证据复核/审计 | deepseek/deepseek-v4-pro |
| 黄忠（HUANGZHONG） | HUANGZHONG | 验收标准/抽样/终检 | glm/glm-5.3 |
| 关羽（GUANYU） | GUANYU | 代码审计/技术债/门禁 | deepseek/deepseek-flash |

### 1. [研发] <议题标题>
- topic_id：HQ-20261103-01｜owner：GUANYU｜legion：hq-audit
- due：2026-11-03｜优先级：未派单 / 红黄军团 / 到期
- 期望产出：方案 + 代码/工单 + 验收命令（含 rc 与证据包路径）
- 证据要求：meeting:...；五角色 RoleOpinion（每人 1 条，无证据不得 verified）；派单证据 Directive→task；legion_report blockers；验收命令+rc
- 议程项：①背景与证据 ②五角色意见 ③A0 决议（owner/due/acceptance）④派单/关闭
- 角色提示：五角色各 1 条（只读提示，不含角色意见）
```

JSON 同字段（`--json`），可直接被 `/hq` 页面或脚本消费；`--out` 落盘也只写指定文件。

## 六、验收与测试

```powershell
# 1) 语法
python -X utf8 -m py_compile tools\hq_meeting_prep.py tools\tests\test_hq_meeting_prep.py   # rc=0

# 2) 单测（8 例：真实数据/无数据 fail-visible/排序+上限/无候选/JSON+Markdown/角色提示不伪造/--out/到期判断）
python -X utf8 -m pytest tools\tests\test_hq_meeting_prep.py -q                             # 8 passed, rc=0

# 3) CLI 冒烟
python -X utf8 tools\hq_meeting_prep.py --json        # rc=0，stdout 纯 JSON
python -X utf8 tools\hq_meeting_prep.py --markdown    # rc=0，Markdown 草案
python -X utf8 tools\hq_meeting_prep.py --out C:\Temp\hq_prep.json   # rc=0，仅写指定文件
```

**实测（2026-11-03 07:0x）**：真实数据 `--json` rc=0，`ok=true`，候选 16 → 选中 3（红军团 quant-strategy-line；黄军团 acad1-legion/hq-audit/hq-command/hq-governance/huangzhong-acceptance）；8 tests passed。

**C2 验收口径（连续 5 个工作日，11-03 起）**：每天 1 次本工具出草案 → A0 主持 15min 晨会 → `/hq` 落 1 份纪要 → 5 角色各出 1 条 RoleOpinion；≥5 条 Directive 中 ≥3 条有 task/order；未派单决议 0。本工具只承担「会前准备」环节。

## 七、边界（硬约束）

1. **不写生产会议**：不写 `agent_platform/data/meetings/hq/**`；纪要由 `/hq` 晨会服务记录（hq_meeting）。
2. **不自动派单**：只给 owner/due/证据要求/期望产出；派单由 A0/田丰确认后走 Directive/task 单。
3. **不调模型、不伪造意见**：`role_prompts` 只是提示文本，不含任何「角色已同意/已结论」；无证据不 verified。
4. **无数据 fail-visible**：无真源 → rc=2 + `ok=false`，不生成空草案、不以 rc=0 冒充有产出。
5. **只读上游**：所有数据源只读；`--out` 只写调用者指定文件；不启停服务、不 git。
6. **如实呈现上游数据缺陷**：历史会议 JSON（10-28/10-29）部分中文已乱码（存储期编码问题），草案会如实显示该文本并在证据中标注来源，**不在本工具内编造/清洗成"好看"的文本**；上游修复另单（数据治理）。

## 八、下一步

1. **11-03 起**：DSH/A0 每日 08:45 前跑一次本工具 → 以草案主持 15min 晨会 → `/hq` 落纪要（C2 记录开始）。
2. **C2 记录**：5 个工作日纪要 + 25 条 RoleOpinion + Directive 闭环（由 hq_directives/hq_reports 只读复核，不靠本工具自证）。
3. **后续可接（另单）**：把草案以「预填」形式接 `/hq` 晨会页（需 8091 接口/服务变更 → 走 L2/ARCH1 批）；定时预生成（计划任务属 L2 五要素）；历史会议乱码修复（数据治理单）。
4. **不做**：不自动发飞书、不自动建任务、不自动改会议状态。

## 九、相关文件

| 文件 | 作用 |
|---|---|
| `tools/hq_meeting_prep.py` | 本工具（只读草案生成，~560 行） |
| `tools/tests/test_hq_meeting_prep.py` | 8 例测试（真实数据/失败/排序/输出/提示/落盘/到期） |
| `tools/hq_directives.py` | Directive 只读模型（真源聚合/任务链接校验/意见不伪造） |
| `tools/hq_reports.py` | LegionReport（红/黄/绿、blockers、eta） |
| `tools/hq_agenda.py` | 议题分类 `_kind`（研发/挖掘/分析/商业） |
| `tools/hq_roles.py` / `agent_platform/command_hq/roles.py` | 五角色真身份/模型绑定/边界 |
| `docs/plans/2026-11-03_HQ_Morning_Meeting_Runbook_v1.md` | 晨会运行卡（会前/会中/会后、五角色固定输出） |
| `docs/plans/2026-11-03_RND_Plan_v1.md` | C2 工作包与 P0 出口门 |

## 十、版本

- v1（2026-11-03）：工具 + 8 例测试 + 文档；真实数据冒烟 rc=0；C2 连续 5 工作日验收开始。
