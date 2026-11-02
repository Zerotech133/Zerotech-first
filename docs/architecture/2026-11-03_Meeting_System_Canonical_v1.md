# 会议系统主系统与入口实现 v1

- 日期：2026-11-03｜触发：A0“读取项目文件内所有会议系统，根据那个系统来实现会议”
- 结论：**指挥部会议唯一主系统 = HQ Meeting v2（8796 war room + hq_meeting.py + meeting.html）**；8798 议会厅已收编只读，不复活。

## 一、全量盘点

| 系统 | 位置 | 角色 | 判定 |
|---|---|---|---|
| **HQ Meeting v2** | `agent_platform/legion_command/hq_meeting.py`、`serve.py`、`static/meeting.html` | 五席指挥部会议：morning/operation/acceptance；agenda/transcript/decisions/orders/acceptance；save/dispatch/stats | **主系统** |
| 会前议程 | `hq_agenda.py` + `portal/data/council_*` | 8798 议会厅收编只读；议题/留言/预约 | 主系统的会前段 |
| 会议看板 | `hq_stats.py` | 闭环率/跑偏/回放 | 主系统的会后段 |
| 8798 议会厅 | `portal/council_ops.py`、`portal/os_council.html` | 旧交互桥 | 只读归档，不启动 |
| 数字军团 tri-party | `legion_tri_party_meeting`、`portal/api_legion_ops_v2.py` | 军团三方会 | 军团专项，不是指挥部会议 |
| D1 投研会 | `agent_platform/d1_meeting.py`、`portal/api_d1_meeting.py` | 投研 create/vote/finalize | 投研专项，不替代指挥部 |
| k_stock paper meeting | `k_stock/paper/meeting.py` 等 | 模拟盘会议/账本 | 策略域内部 |
| 外部会议纪要 | `lark-*` skills | 飞书会议/妙记 | 外部能力，不是本系统主数据 |

依据：`docs/plans/2026-10-28_指挥部会议系统结合方案_v1.md` 已定“一个入口、两种会场、三段闭环”，并明确 8798 收编、五席上桌、schema v2。

## 二、主系统实现（本次接线）

### 2.1 唯一入口

- `http://127.0.0.1:8734/meeting`（需登录）
- `/hq` 页头新增「作战会议室 →」
- 未登录 302 `/login?next=/meeting`

### 2.2 路由

```nginx
location = /meeting { auth_request; proxy_pass 8796/static/meeting.html; }
location ^~ /api/hq/meeting/ { auth_request; proxy_pass 8796;
  /api/hq/meeting/agenda         -> /api/hq/agenda
  /api/hq/meeting/agenda/message -> /api/hq/agenda/message
}
```

- 避免与 `/hq` 页（8091 `/api/hq/agenda`）冲突；会议页改用 `/api/hq/meeting/agenda`。
- 其余 `/api/hq/meeting/save|dispatch|stats|list|acceptance/evidence` 直通 8796。

### 2.3 页面已具备

`meeting.html` 已有：
- 五席固定桌：A0 田丰、AI1 诸葛亮、ZHAOYUN 赵云、HUANGZHONG 黄忠、GUANYU 关羽；
- 三种模板：晨会 15min / 作战会 30-45min / 验收会；
- 会前议题（8798 收编只读）、验收证据、会议看板；
- 保存指挥部纪要（schema 2.0）、决议→任务申请（不自动执行、不代批 L2）。

## 三、使用流程

1. 登录 8734 → `/meeting`；
2. 选“晨会/作战会/验收会”，五席自动上桌；
3. 会前议题/证据 → 逐席发言（真实模型通道）；
4. 填决议 + owner + due；
5. 保存纪要 → `/api/hq/meeting/save` 落 `agent_platform/data/meetings/hq/`；
6. 派单 → 生成任务申请（待田丰派工，不执行）；
7. `/hq` 议程/军团汇报消费结果。

## 四、边界

- 纪要为“服务端记录客户端声明”，真实模型回执需后续证据校验；
- 不自动执行、不代签、不代批 L2；
- 8798 不复活；D1/军团/paper meeting 保持各自专项；
- `/meeting` 不做免登录预览（含写入/决议）。
