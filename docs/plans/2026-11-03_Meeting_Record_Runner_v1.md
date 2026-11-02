# 会议纪要草稿器 Runbook v1（C2，dry-run）

- **状态**：`draft`（工具+测试已落地，待 A0/DSH 验收；生产落盘仍需 A0 确认，本单不实现 `--write`）
- **日期**：2026-11-03
- **依据**：`docs/plans/2026-11-03_HQ_Morning_Meeting_Runbook_v1.md`（晨会运行卡：会后纪要/决议/派单）、`docs/plans/2026-11-03_RND_Plan_v1.md`（C2）、`agent_platform/legion_command/hq_meeting.py`（schemas 2.0 生产纪要格式）、`agent_platform/command_hq/role_contract.py`（RoleOpinion v1）、`tools/hq_meeting_prep.py`（晨会草案）
- **性质**：**dry-run 纪要草稿器**——把「晨会草案 + 五角色 RoleOpinion」整理成 hq_meeting v2 格式草稿；**不写生产会议、不调模型、不自动派单、不启停服务**
- **工具**：`tools/hq_meeting_record.py`（新增）｜**测试**：`tools/tests/test_hq_meeting_record.py`（9 例全绿）

## 一、定位：会前 → 会中 → 会后 的"会后草稿"环节

```
hq_meeting_prep.py --json       五角色 RoleOpinion v1（会上产生/或授权代理）
        │                              │
        └──────────────┬───────────────┘
                       ▼
        hq_meeting_record.py（本工具，dry-run）
                       ▼
   hq_meeting v2 格式草稿：agenda/attendees/transcript/decisions(空)/orders(空)
                       ▼
   A0 会议确认（补 missing/unverified 意见、填 decisions/orders）
                       ▼
   /hq 晨会服务落盘（hq_meeting.save_meeting；本工具不写）
```

**与 `agent_platform/legion_command/hq_meeting.py` 的关系**：本工具只对齐其 **schema 2.0 字段**（agenda/attendees/transcript/decisions/orders/acceptance/status/source_verification），不调用其 `save_meeting`（生产写入），不接受服务端 meeting_id 生成逻辑。草稿 `meeting_id` 形如 `hq-<YYYYMMDD>-<HHMMSS>-draft`，**不匹配**生产 `hq-\d{8}-\d{6}` 格式（避免被生产加载逻辑误读）。

## 二、使用方式

```powershell
cd C:\项目\老田的AI助手

# 0) 先生成晨会草案（上一步工具）
python -X utf8 tools\hq_meeting_prep.py --json --out C:\Temp\agenda.json

# 1) 只带议程 → 无意见草稿（五角色全部显式 missing，不伪造）
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json

# 2) 带五角色意见（RoleOpinion v1 数组或 {"opinions":[...]}）
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --opinions C:\Temp\opinions.json

# 3) 人读 Markdown
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --opinions C:\Temp\opinions.json --markdown

# 4) 只写指定文件（.json = hq_meeting v2 草稿；.md = 人读版）
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --out C:\Temp\record.json

# 5) 生产写入：本单未实现（默认禁止）
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --write   # rc=2 production_write_disabled
```

- 输入 `--agenda`：`hq_meeting_prep.py --json` 输出（文件，或 `-` 读 stdin）；**必填**。
- 输入 `--opinions`：可选；RoleOpinion v1 的 JSON 数组，或 `{"opinions":[...]}`；文件或 `-`（只允许一个输入用 stdin）。
- 输出：默认 **JSON（hq_meeting v2 草稿）** 打印；`--json` 同默认；`--markdown` 人读；`--out` 只写指定文件。
- **退出码**：`0` 草稿成功（可含 missing/unverified）；`2` fail-visible（缺/坏议程、坏意见 JSON、非法意见、生产写入被拒、参数错误）。

## 三、输入/输出契约

### 3.1 输入

| 输入 | 契约 | 校验 |
|---|---|---|
| 议程 | schema `hq/meeting_prep/v1`（`ok=true`，`topics[]` 每项有 `topic_id`/`title`/`owner`/`roles_expected` 等） | 缺文件/坏 JSON/`ok=false`/无 topics → rc=2 |
| 意见 | RoleOpinion v1（`hq_role_opinion/v1`）：schema/topic_id/role/round/position/recommendation/evidence[]/risks/conditions/escalate/escalate_reason/created_at | 复用 `role_contract.validate_opinion/normalize_opinion`；非法 → 显式 `invalid`；同 (topic,role,round) 去重保留最新 |

### 3.2 五角色意见状态（不伪造）

| 状态 | 含义 |
|---|---|
| `verified` | 意见合法且**至少 1 条 evidence 带可复核 ref 且 `verified=true`** |
| `unverified` | 意见合法但无已验证证据（evidence 空或皆未 verified）→ 不冒充 verified |
| `invalid` | RoleOpinion 校验失败（字段/规则）→ 附 errors，**草稿 ok=false**（不能当成功） |
| `missing` | 该议题缺该角色意见（议程 roles_expected 中但未提供）→ 不补、不代写 |

### 3.3 输出（schema `hq_meeting_record/draft/v1`）

| 字段 | 说明 |
|---|---|
| `ok/dry_run/read_only` | 成功（invalid=0）；`dry_run=true`、`read_only=true` |
| `agenda_source` | 议程 schema/date/topics/selection 摘要 |
| `opinions_source/opinions_count` | 意见来源与输入条数 |
| `meeting` | **hq_meeting v2 草稿**：`schema_version=2.0`、`meeting_id=*-draft`、`type=morning`、`agenda[]`（item/owner/minutes/topic_id/due）、`attendees[]`（真实 SEATS）、`transcript[]`（只含真实意见：position+recommendation+证据 refs+verified）、`decisions=[]`、`orders=[]`、`acceptance=[]`、`status=draft`、`source_verification=dry_run_draft_not_server_recorded`、`note` |
| `role_opinion_status[]` | 每议题×角色：status/position/recommendation/verified_evidence/errors（missing 也列出） |
| `summary` | topics/roles_expected/verified/unverified/invalid/missing/transcript/decisions/orders（decisions/orders 恒 0：A0 会上填） |
| `warnings/next` | 降级/去重告警；下一步提示（A0 确认 → /hq 落盘） |

## 四、示例（2026-11-03 真实议程）

```text
# 只带议程（无意见）——真实数据 3 议题
$ python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --json
ok=true dry_run=true meeting_id=hq-20261103-071012-draft status=draft
source_verification=dry_run_draft_not_server_recorded
topics=3 verified=0 unverified=0 invalid=0 missing=15 transcript=0 decisions=0 orders=0
attendees=A0,AI1,ZHAOYUN,HUANGZHONG,GUANYU
→ 五角色 15 格全部 missing（如实标注，不伪造意见）

# 带第 1 议题的 5 条 verified 意见
ok=true topics=3 verified=5 unverified=0 missing=10 transcript=5
→ HQ-20261103-01 五角色 verified（各 1 条已验证证据）；其余 2 议题仍 missing
```

Markdown 版含：基本信息 / 议程表 / 五角色意见状态表（15 格）/ Transcript（只含真实意见）/ 决议与派单（空，A0 会上填）/ 边界。

## 五、验收与测试

```powershell
python -X utf8 -m py_compile tools\hq_meeting_record.py tools\tests\test_hq_meeting_record.py   # rc=0
python -X utf8 -m pytest tools\tests\test_hq_meeting_record.py -q                               # 9 passed
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --json                   # rc=0
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --markdown              # rc=0
python -X utf8 tools\hq_meeting_record.py --agenda C:\Temp\agenda.json --out C:\Temp\record.json # rc=0，仅写指定文件
```

测试 9 例覆盖：完整五角色草稿、缺角色 missing 不伪造、无证据 unverified、坏意见 JSON fail-visible、非法意见 invalid（rc=2）、JSON/Markdown、`--out` 临时文件、生产目录/`--write` 拦截、议程缺失/坏/失败、与 `hq_meeting_prep` 真实输出集成。

**实测（2026-11-03 07:1x）**：真实议程 3 议题 → 无意见草稿 rc=0（missing=15，transcript=0）；补第 1 议题 5 条 verified 意见 → rc=0（verified=5，missing=10，transcript=5）；9 tests passed。

## 六、边界与未做项

**已做（本单）**：dry-run 草稿生成（JSON/Markdown）、RoleOpinion 校验与四态标注、`--out` 只写指定文件、生产目录/`--write` 拦截、fail-visible。

**未做（明确边界）**：
1. **生产落盘 `--write` 未实现**——写 `agent_platform/data/meetings/hq/` 须 A0 确认后另单/由 `/hq` 晨会服务（hq_meeting.save_meeting）执行；
2. 不自动生成 decisions/orders（决议与派单由 A0 会上给出；本工具不代签、不派单）；
3. 不调模型、不联网、不自动代写五角色意见（missing/unverified 如实标注）；
4. 不推送飞书、不改任务/服务、不 git；
5. 历史会议 JSON（10-28/10-29）部分中文存储期乱码，草稿如实承载上游文本（不编造/清洗），上游修复另立数据治理单。

## 七、下一步（C2 运行）

1. 每日：`hq_meeting_prep` 出草案 → 晨会上五角色给 RoleOpinion（可先手工/授权代理） → 本工具生成纪要草稿 → A0 确认补 decisions/orders → `/hq` 落盘（生产服务）。
2. C2 验收：连续 5 个工作日纪要 + 25 条 RoleOpinion + ≥5 Directive；本工具保证"草稿-意见-状态"可机读可复核。
3. 后续可接（另单）：A0 确认后的一键落盘（`--write` 生产实现，需 L2/接口授权）；从 `/hq` API 拉取真实 RoleOpinion；飞书纪要推送。

## 八、相关文件

| 文件 | 作用 |
|---|---|
| `tools/hq_meeting_record.py` | 本工具（dry-run 纪要草稿，~470 行） |
| `tools/tests/test_hq_meeting_record.py` | 9 例测试 |
| `agent_platform/legion_command/hq_meeting.py` | 生产纪要 schema 2.0 与 save_meeting（只读参考，不调用） |
| `agent_platform/command_hq/role_contract.py` | RoleOpinion v1 校验/规范化/去重（复用） |
| `tools/hq_meeting_prep.py` | 晨会草案（本工具的 agenda 输入） |
| `docs/plans/2026-11-03_HQ_Morning_Meeting_Runbook_v1.md` | 晨会运行卡 |
| `docs/plans/2026-11-03_RND_Plan_v1.md` | C2 工作包/P0 出口门 |

## 九、版本

- v1（2026-11-03）：dry-run 草稿器 + 9 例测试 + 文档；真实议程冒烟通过；生产写入仍未实现（A0 确认后另单）。
