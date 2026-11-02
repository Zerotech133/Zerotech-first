# HQ Directive / LegionReport 只读契约 v1

- **日期**：2026-11-03｜**任务单**：task-33（C3 Directive/LegionReport 只读对象与契约）
- **性质**：只读模型/契约；**不派单、不写生产任务/会议、不改运行时、不新增真源**
- **实现**：`tools/hq_directives.py`、`tools/hq_reports.py`（CLI `--json` 机器可读）
- **测试**：`tools/tests/test_hq_directives.py`、`tools/tests/test_hq_reports.py`
- **上游**：`docs/plans/2026-11-03_RND_Plan_v1.md` C3「议题→五角色→派单→军团→回报→验收」

## 一、设计原则

1. **复用真源，不新造**：
   - 会议纪要 v2：`agent_platform/data/meetings/hq/hq-*.json`（decisions / orders / transcript / attendees）；
   - 会前议程：`portal/data/council_new_topics.json`（**只读**，不写 portal）；
   - 五角色职责/身份：`agent_platform/command_hq/roles.py`（`ROLE_SPECS`，只读导入）；
   - 任务板：`.worktrees/tasks.json`（devtask 维护，只读）。
2. **不伪造**：`opinions` 只收会议 transcript 中五席（A0/AI1/ZHAOYUN/HUANGZHONG/GUANYU）的**原样发言**；
   缺席角色写进 `roles_missing`，模型调用失败的原样保留并标 `model_call_failed=true`；**绝不补写意见**。
3. **fail-visible**：无会议/议题真源 → `ok=false, error=no_source_data, rc=2`；任务板缺失/坏 JSON →
   `ok=false, error=no_tasks_data, rc=2`。坏的单文件跳过并在 `warnings` 列出，不静默假绿。
4. **只读**：两个对象所有层级 `read_only=true`；工具不写任何文件、不调用派单/任务接口。
5. **无数据不报绿**：LegionReport 空 scope 一律 `amber`（`no_tasks_in_scope`），没有证据不许 green。

## 二、对象 1：Directive（`schema = hq/directives/v1`）

### 2.1 JSON 形状

```json
{
  "schema": "hq/directives/v1",
  "ok": true, "read_only": true, "generated_at": "2026-11-03T06:45:00+0800",
  "sources": {"meetings_dir": "agent_platform/data/meetings/hq", "meetings": 8,
              "council_topics": 9, "council_scheduled": 3,
              "tasks_file": ".worktrees/tasks.json", "tasks_available": true,
              "roles_source": "command_hq.roles"},
  "warnings": [], "summary": {"total": 12, "by_kind": {"decision": 2, "order": 1, "topic": 9},
                               "by_status": {"open": 11, "dispatched": 1},
                               "by_legion": {}, "with_opinions": 3, "with_task_link": 0},
  "directives": [
    {"id": "DIR-MEET-hq-20261028-233044-O1", "kind": "order",
     "topic": "三席失联（赵云/关羽/黄忠）…", "source_role": "GUANYU",
     "legion": "hq-audit", "owner": "GUANYU", "task_id": "", "status": "dispatched",
     "due": "", "evidence": ["meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#order:1",
                            "dispatch:inbox:lg-186f6e96d817"],
     "opinions": [{"role": "TIANFENG", "seat": "A0", "text": "…", "src": "live",
                    "evidence": ["meeting:morning-20261028"], "model_call_failed": false}],
     "created_at": "2026-10-28T23:30:44", "meeting_id": "hq-20261028-233044",
     "roles_involved": [{"role": "TIANFENG", "seat": "A0", "name": "田丰", "duty": "目标拆解…"}],
     "roles_present": ["TIANFENG"], "roles_missing": ["ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU"],
     "opinions_scope": "meeting", "task_ref": {"source": "none", "task_id": ""},
     "read_only": true}
  ]
}
```

### 2.2 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 确定性 id：`DIR-MEET-<meeting_id>-D<n>`（决议）/`-O<n>`（订单）/`DIR-COUNCIL-<sha1(text+created)[:8]>`（议题） |
| `kind` | str | `decision`｜`order`｜`topic` |
| `topic` | str | 议题/决议/订单正文（原样截断 2000 字） |
| `source_role` | str | 来源角色/席位：owner 命中五席则转角色（A0→TIANFENG…），否则为席位/发起人（如 `ACAD1`、`LAOTIAN`） |
| `legion` | str | 军团归属（见 §2.3 映射）；未映射=`unmapped`（不猜） |
| `owner` | str | 负责人/执行席位（原样） |
| `task_id` | str | **仅显式**任务链接（`task_id`/`dispatch.data.task_id|ticket_id`）；无链接=`""` |
| `status` | str | 状态机见 §2.4 |
| `due` | str | 截止（原样，可能为空） |
| `evidence` | str[] | 可复核引用：`meeting:<rel>#decision|order:<id>` / `council:<rel>#topic:<i>` / `dispatch:inbox:<id>` / `evidence:<上游>` / `task:<id>` |
| `opinions` | obj[] | 会议 transcript 五席原样发言（role/seat/text/src/evidence/model_call_failed）；无则 `[]` |
| `created_at` / `meeting_id` | str | 来源时间与会议 id（议题为空） |
| `roles_involved` | obj[] | 涉及角色职责（role/seat/name/duty，来自 `command_hq.roles`） |
| `roles_present` / `roles_missing` | str[] | 五角色出席/缺席（由 owner 角色 + 真实 opinions 计算；missing 不伪造意见） |
| `opinions_scope` | str | `meeting`（会议级发言挂到该会议每条 Directive，同一口径）｜`none` |
| `task_ref` | obj | `{source, task_id, task_exists?}`；`none`/`unresolved` 明确未链接 |
| `read_only` | bool | 恒 true |

### 2.3 owner/席位 → 军团映射（`OWNER_LEGION`）

| owner/席位 | 军团 |
|---|---|
| ACAD1 | acad1-legion |
| DATA1 | data-warehouse |
| D1、LUSU1 | lusu-d1-flow |
| STRAT1、ST1、STRAT2、BACK1 | quant-strategy-line |
| HUANGZHONG | huangzhong-acceptance |
| A0、AI1、ARCH1、DSH、DSH-Lead、LAOTIAN | hq-command |
| ZHAOYUN | hq-governance |
| GUANYU | hq-audit |
| 其他 | unmapped（如实显示） |

> 映射为 **HQ 汇报口径** 的静态表（v1 不解析组合 registry，避免引入写域外依赖）；后续可改由组合账本生成。

### 2.4 状态机（v1 只读推导，不持久化）

```
topic:                    open
decision:   draft ──(meeting 结束)──> open ──(显式 task 完成，未来)──> closed
order:      pending ──(dispatch 存在)──> dispatched ──(显式 task doing/review，未来)──> in_progress
                                        └────────(显式 task done，未来)────────> closed
```

| 对象 | v1 规则 |
|---|---|
| topic | 恒 `open` |
| decision | 会议 `status=draft` → `draft`；否则 `open`（task_id 显式且任务 done → 未来版本 `closed`） |
| order | 有 `dispatch`（ok 非 false）→ `dispatched`；否则 `pending` |

**允许值**：`draft｜open｜pending｜dispatched｜in_progress｜closed`。v1 实际只会产生前三类（`in_progress/closed` 留给 C3 闭环后续版本）。

### 2.5 任务链接（task_id）

- 只认显式字段：`decision.task_id` / `order.task_id` / `order.dispatch.data.task_id|ticket_id`；
- 若 tasks.json 中**不存在**该 id：清空 `task_id`、`task_ref.source="unresolved"` 并写 warning（不假装链接成功）；
- 真实数据现状：会议订单的 dispatch 是平台 inbox 申请（`lg-…`），**任务板无 meeting-id 回填** → `with_task_link=0` 属真实现状，未硬造；
  P2 需 devtask 建单时回填 `meeting_id`/`order_id` 才能自动链接（见 §六 未做项）。

## 三、对象 2：LegionReport（`schema = hq/legion_reports/v1`）

### 3.1 JSON 形状

```json
{
  "schema": "hq/legion_reports/v1", "ok": true, "read_only": true,
  "generated_at": "2026-11-03T06:45:00+0800",
  "sources": {"tasks_file": ".worktrees/tasks.json", "tasks": 2047,
              "directives_source": "on", "legions": 9, "owners": 85},
  "warnings": [],
  "legions": [
    {"scope": {"legion": "acad1-legion"},
     "tasks_total": 20, "tasks_done": 19, "tasks_active": 0, "tasks_parked": 0,
     "tasks_aborted": 1, "tasks_other": 0, "aborted_ratio": 0.05,
     "progress": 95.0, "status": "amber", "status_reason": "aborted>0",
     "eta": null, "directives_open": 0,
     "blockers_count": 1,
     "blockers": [{"task_id": "WO-…", "owner": "ACAD1", "status": "aborted",
                   "reason": "…", "created": "2026-11-01 05:34:17", "desc": "…"}],
     "evidence": ["source:.worktrees/tasks.json", "tasks_total=20",
                  "done=19 aborted=1 active=0 parked=0", "directives_source=on open=0"]}
  ],
  "owners": [{"scope": {"owner": "ACAD1"}, "legion": "acad1-legion", "…": "同上字段"}],
  "summary": {"legions": 9, "status_counts": {"green": 2, "amber": 5, "red": 2},
              "tasks_total": 2047, "tasks_aborted": 134, "owners": 85}
}
```

### 3.2 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `scope` | obj | `{"legion": name}` 或 `{"owner": name}` |
| `tasks_total/done/active/parked/aborted/other` | int | 计数；active = `doing+review`；other = 未知状态 |
| `aborted_ratio` | float | aborted/total（0~1，保留 4 位） |
| `progress` | float | `done/total*100`（保留 1 位；0 任务=0.0） |
| `status` | str | `green｜amber｜red`（规则见 §3.3） |
| `status_reason` | str | 机器可读原因：`all_done`/`active_tasks>0`/`aborted>0`/`aborted_ratio>=10%`/`no_tasks_in_scope` |
| `eta` | str\|null | 该 scope 未关闭 Directive 的 `due` 最大 ISO 日期（YYYY-MM-DD）；无则 null（不猜） |
| `directives_open` | int | 该 scope 未关闭 Directive 数 |
| `blockers_count/blockers` | int/obj[] | 阻塞：`status=aborted` 或 `parked` 或 desc/reason 含「阻塞/blocked」；按 created 倒序取前 N（`--limit-blockers`） |
| `evidence` | str[] | 数据源/计数/指令源可复核引用 |

### 3.3 status 规则（防假绿）

| 条件（按顺序） | status | reason |
|---|---|---|
| `total == 0` | **amber** | `no_tasks_in_scope`（无证据不许报绿） |
| `aborted/total ≥ 10%` | **red** | `aborted_ratio>=10%` |
| `active(doing+review) > 0` 或 `parked > 0` | **amber** | `active_tasks>0` |
| `aborted > 0`（占比 <10%） | **amber** | `aborted>0` |
| 其余（全 done 且占比 <10%） | **green** | `all_done` |

> 10% 阈值与状态规则为 v1 的 HQ 汇报口径（可配置化前先固化）；`unmapped` 军团如实单列，不并入其他军团。

## 四、CLI 契约

### 4.1 `tools/hq_directives.py`

```powershell
python -X utf8 tools/hq_directives.py --json [--kind decision,order,topic]
        [--status open,dispatched] [--legion acad1-legion] [--meeting hq-20261028-233044]
        [--limit 20] [--meetings-dir DIR] [--council-dir DIR] [--tasks-file PATH]
```

- `--json`：**stdout 纯 JSON**（人读信息走 stderr；fail-visible 也是合法 JSON + rc=2）；
- rc：`0` 成功；`2` 无真源/参数/环境错误（`ok=false` + `error` + `searched`）；
- 过滤在聚合后应用，`filtered` 字段回显过滤条件与条数。

### 4.2 `tools/hq_reports.py`

```powershell
python -X utf8 tools/hq_reports.py --json [--legion LIST] [--owner LIST]
        [--limit-blockers N] [--tasks-file PATH] [--meetings-dir DIR] [--council-dir DIR]
```

- rc：`0` 成功；`2` 无任务板/坏 JSON/环境错误；
- `--legion/--owner` 过滤对应数组；`owners` 默认全量（45+ owner；需要收敛由调用方过滤）。

## 五、未来 API 形状（只读；本任务不实现）

```
GET /api/hq/directives?kind=&status=&legion=&meeting=&limit=
GET /api/hq/reports?legion=&owner=&limit_blockers=
```

- 复用 `/api/hq/*` 现有鉴权（登录态/只读 token），**不新增写接口、不做自动派单**；
- 返回即本文件 §2.1/§3.1 对象（含 `schema/read_only/generated_at`），保持 CLI 与 API 同构；
- 页面（/hq）只消费，不在本任务改 portal。

## 六、未做项（明确边界）

1. **自动派单/自动建任务**：不做（C3 只读模型；派单仍走 hq_meeting 的申请流 + 人工/L2）。
2. **task_id 自动回填**：真实会议→任务板无 backref，本版仅显式字段；需 devtask/派单侧回填 `meeting_id/order_id`（P2）。
3. **真闭环状态推进**：`in_progress/closed` 的落库与推进不做（需任务事件订阅；本版只读推导）。
4. **意见按 Directive 归属**：transcript 是会议级，本版挂到同会议全部 Directive（`opinions_scope=meeting` 明示）；
   若需逐条议题归属，需会议纪要 v3 增加 `opinion.topic_id`。
5. **会议 agenda 转 Directive**：本版只收 decisions/orders + council topics；agenda 项留给 v2（避免重复计数）。
6. **门户/API 集成、RBAC、持久化快照**：未做（C1/U1 另单）。
7. **C3 真实议题闭环**：本模型已具备“议题→五角色意见→owner/军团→回报”对象；**一条真实闭环（派单→回执→验收）仍依赖运行时链路**，属 C3 后续执行项，本单不宣称闭环完成。

## 七、验收

- `python -X utf8 -m py_compile tools/hq_directives.py tools/hq_reports.py` → rc=0；
- `pytest tools/tests/test_hq_directives.py tools/tests/test_hq_reports.py -q` → 19 passed（真实数据 + 合成边界，≥6）；
- `hq_directives.py --json` / `hq_reports.py --json` 在真实数据上 rc=0 且 stdout 可 `json.loads`；
- 未改 `compositions/`、`registry.json`、`portal/`、服务；未 git。

## 八、回滚

删除/还原：`tools/hq_directives.py`、`tools/hq_reports.py`、两个测试文件、本文件即可；无数据/服务副作用（纯只读 CLI）。
