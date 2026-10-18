# Paperclip 组织壳设计稿（M0）

- 日期：2026-10-18
- 路线：KK 路
- 状态：纸面设计 + 可执行配置稿（**不部署生产**）
- 上位依据：数字军团总定稿 V2（五部门架构）；Paperclip = 战略层公司
- 关联稿：`2026-10-18_田丰与诸葛亮双profile设计稿.md`、`2026-10-18_军团M0骨架施工单.md`

---

## 1. 定位与 M0 范围

Paperclip 是军团的**战略层公司**，M0 阶段只要求三件事在 Docker 容器内跑通最小 API：

| 能力 | M0 验收口径 |
|---|---|
| 预算硬停 | 预算账本可扣减，超阈值后执行接口直接拒绝 |
| 审批流 | 工单可走完 draft→submitted→approved/rejected→executing→done 状态机 |
| 审计 | 所有状态变化与预算变化均落 append-only 事件表 |

**明确不在 M0 范围**：对接周一主链、接入 Hermes 调度、真实付费动作、多币种/多预算主体。

## 2. 端口核实结论（2026-10-18 实测）

```
netstat -ano | findstr LISTENING   # 87xx 段
127.0.0.1:8787  LISTENING  PID 15976   # Hermes WebUI（占用）
127.0.0.1:8788  LISTENING  PID 38508   # （占用）
8790-8799       无任何监听             # 全空闲
```

**结论：Paperclip API 选 `127.0.0.1:8790`**，容器宿主侧固定绑定 8790，容器内部仍为 8000。若后续 8790 被占，顺延 8791（已核实空闲）。

## 3. 预算硬停

### 3.1 预算账本表结构（SQLite）

```sql
-- 预算科目表：一个科目 = 一个预算主体（M0 只建 default）
CREATE TABLE budget_account (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,          -- 'default'
  quota_cents   INTEGER NOT NULL,              -- 总预算，单位：分（避免浮点）
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 预算账本：append-only 的扣减/入账流水
CREATE TABLE budget_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id    INTEGER NOT NULL REFERENCES budget_account(id),
  delta_cents   INTEGER NOT NULL,              -- 负数=扣减，正数=入账
  reason        TEXT NOT NULL,                 -- 'wo:WO-2026-0001:llm-call' 等
  wo_id         TEXT,                          -- 关联工单号，可空
  balance_after INTEGER NOT NULL,              -- 扣减后余额（冗余落盘，便于硬停判断与对账）
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

要点：
- 金额一律用**分（cents）整数**，杜绝浮点误差。
- `balance_after` 冗余字段使得「当前余额」读取无需 SUM 全表，也天然构成对账链。

### 3.2 扣减点

M0 只有三个扣减点，全部收口在 Paperclip API 内：

| 扣减点 | 触发时机 | 扣减内容 |
|---|---|---|
| D1 工单进入 executing | 状态机 `approved→executing` | 预扣该工单预估成本（wo 需携带 `est_cost_cents`） |
| D2 工单执行中计量 | 每次外部调用回执 | 实际成本补扣/退还差额 |
| D3 工单终态结算 | `done/rejected` | 未预扣部分结算，退回多余预扣 |

### 3.3 硬停判断伪代码

```python
def try_spend(account_id: str, amount_cents: int, reason: str, wo_id: str | None) -> bool:
    """扣减唯一入口。硬停 = 余额不足以覆盖本次扣减时直接拒绝并抛出 BudgetHardStop。"""
    with sqlite_conn():                     # BEGIN IMMEDIATE，串行化扣减
        bal = current_balance(account_id)   # 读 ledger 最新一条的 balance_after，缺省=quota
        if bal < amount_cents:
            audit_append("budget.hard_stop", {
                "account_id": account_id,
                "requested_cents": amount_cents,
                "balance_cents": bal,
                "wo_id": wo_id,
            })
            raise BudgetHardStop(f"余额 {bal} 分 < 请求 {amount_cents} 分，工单 {wo_id} 硬停")
        ledger_append(account_id, -amount_cents, reason, wo_id, new_balance=bal - amount_cents)
        audit_append("budget.spend", {"amount_cents": amount_cents, "wo_id": wo_id})
        return True

# 工单状态机转 executing 前强制过闸：
def transition(wo, to_state):
    if to_state == "executing":
        try_spend(wo.account_id, wo.est_cost_cents, f"wo:{wo.id}:pre-hold", wo.id)
    ...
```

硬停语义：**拒绝即停**，不做部分扣减、不做赊账；硬停事件本身写入审计表供复盘。

> **【勘误回写 2026-10-18，MM 路施工发现 + m1-harden 016c434 固化】**上图伪代码中 `audit_append("budget.hard_stop", ...)` 与 `raise` 同处一个 `with sqlite_conn()` 事务内——异常抛出会回滚主事务，**硬停审计随回滚丢失**。实装修复：硬停事件改为**独立事务落盘**（meter 端点在捕获 `BudgetHardStop` 的 except 分支内调用 `audit_hard_stop` 独立写审计，余额分文不动、工单状态留原态）。设计稿留此勘误供 M1+ 复盘口径统一；以 paperclip/app.py 实装为准。

## 4. 审批流（工单状态机）

### 4.1 状态机

```
draft ──submit──> submitted ──approve──> approved ──execute──> executing ──finish──> done
                        │                                              │
                        └──reject──> rejected                          └──abort──> rejected
```

### 4.2 工单表

```sql
CREATE TABLE work_order (
  id              TEXT PRIMARY KEY,            -- 'WO-2026-0001'
  title           TEXT NOT NULL,
  payload         TEXT NOT NULL DEFAULT '{}',  -- JSON：执行参数
  est_cost_cents  INTEGER NOT NULL DEFAULT 0,
  state           TEXT NOT NULL DEFAULT 'draft',
    -- draft | submitted | approved | rejected | executing | done
  account_id      TEXT NOT NULL DEFAULT 'default',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 4.3 谁可以转谁（M0 权限矩阵）

M0 角色极简：`operator`（田丰侧执行体）、`approver`（Paperclip/老板侧）、`system`（容器内定时/回调）。

| 转移 | 动作 | 允许角色 | 备注 |
|---|---|---|---|
| draft → submitted | submit | operator | 必须填 est_cost_cents |
| submitted → approved | approve | approver | approve 时校验预算余额 ≥ est_cost_cents，不足直接拒 |
| submitted → rejected | reject | approver | 必须带 reason，落审计 |
| approved → executing | execute | system / operator | 触发扣减点 D1（硬停闸） |
| executing → done | finish | system / operator | 结算扣减点 D3 |
| executing → rejected | abort | approver / system | 中止，退预扣 |

实现为一张静态转移表，非法转移返回 409：

```python
TRANSITIONS = {
    ("draft","submitted"): {"operator"},
    ("submitted","approved"): {"approver"},
    ("submitted","rejected"): {"approver"},
    ("approved","executing"): {"system","operator"},
    ("executing","done"): {"system","operator"},
    ("executing","rejected"): {"approver","system"},
}
```

M0 鉴权用静态 token（`PAPERCLIP_ROLE_TOKEN_*` 环境变量注入），不做用户体系。

## 5. 审计（append-only 事件表）

```sql
CREATE TABLE audit_event (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 单调递增，天然顺序号
  ts          TEXT NOT NULL DEFAULT (datetime('now')),
  actor       TEXT NOT NULL,                      -- operator/approver/system
  event_type  TEXT NOT NULL,                      -- 'wo.state_change' | 'budget.spend' | 'budget.hard_stop' | ...
  wo_id       TEXT,
  payload     TEXT NOT NULL DEFAULT '{}',         -- JSON 快照（含 from_state/to_state / delta_cents 等）
  prev_hash   TEXT NOT NULL,                      -- 前一条事件的 hash，构成哈希链
  hash        TEXT NOT NULL                       -- sha256(seq|ts|actor|event_type|wo_id|payload|prev_hash)
);
-- 防篡改：表只允许 INSERT（API 层收口，不留 UPDATE/DELETE 端点）；
-- 校验任务：重放哈希链比对 hash，断链即告警。
```

哈希链 M0 就落，成本低且让「append-only」从纪律变成可验证机制。

## 6. 容器与最小 API 骨架

### 6.1 目录布局（仅设计，不落生产）

```
paperclip/
  app/
    main.py          # FastAPI 入口：/healthz /wo /wo/{id}/transition /budget/{account} /audit/tail
    db.py            # SQLite 连接与建表（/data/paperclip.db）
    budget.py        # try_spend / 硬停
    workflow.py      # TRANSITIONS 转移表
    audit.py         # audit_append / 哈希链校验
  requirements.txt   # fastapi uvicorn
  Dockerfile         # python:3.12-slim，非 root 运行
  docker-compose.yml
```

### 6.2 API 面（M0 最小集）

| 方法/路径 | 说明 |
|---|---|
| GET /healthz | 建表自检 + 余额快照 |
| POST /wo | 建 draft 工单 |
| POST /wo/{id}/transition | body: {to, actor, reason?}；执行状态机 + 扣减闸 |
| GET /budget/{account} | 当前余额 |
| POST /budget/{account}/topup | approver 入账 |
| GET /audit/tail?n=50 | 最近事件 |

### 6.3 docker-compose.yml（配置稿）

```yaml
# Paperclip M0 配置稿 —— 2026-10-18，仅纸面，不部署
services:
  paperclip-api:
    build: ./paperclip
    container_name: paperclip-api
    ports:
      - "127.0.0.1:8790:8000"        # 仅本机回环，8790 段 2026-10-18 netstat 核实空闲
    environment:
      - PAPERCLIP_DB_PATH=/data/paperclip.db
      - PAPERCLIP_ROLE_TOKEN_OPERATOR=${PAPERCLIP_ROLE_TOKEN_OPERATOR}
      - PAPERCLIP_ROLE_TOKEN_APPROVER=${PAPERCLIP_ROLE_TOKEN_APPROVER}
      # GPT 6.0 等上游凭据一律环境变量注入，本文件不落任何密钥
      - OPENAI_GPT6_KEY=${OPENAI_GPT6_KEY}
    volumes:
      - paperclip-data:/data
    restart: unless-stopped

volumes:
  paperclip-data:
```

要点：
- 宿主侧 `127.0.0.1:8790`，**不暴露公网**。
- token 与 `OPENAI_GPT6_KEY` 全部经 `${...}` 从环境注入，compose 文件零密钥落盘。
- 数据卷 `paperclip-data` 挂 `/data`，账本与审计随容器生命周期持久化；M0 不接主仓 `data/`（红线）。

## 7. 开放问题

1. 预算主体与 Kanban/执行仓的命名是否共用一套 wo 编号（待与 JJ 路对表）。
2. `est_cost_cents` 的定价口径（LLM token 计价表）M0 用静态估算还是留 0，需老板拍板。
3. approve 权限在 M0 是否先由老板本人 token 人工行使，还是先挂军师府。
4. OpenClaw 网关最终形态（详见双 profile 设计稿「待对表」项）可能影响 Paperclip 与执行链的对接点。
