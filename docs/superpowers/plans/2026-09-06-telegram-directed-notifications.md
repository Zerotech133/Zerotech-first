# Telegram 安全绑定与定向通知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用平台统一机器人、一次性 deep-link 验证绑定、用户/实例通知偏好和数据库 outbox，将成交与报告从全员广播切换为按用户策略实例定向推送。

**Architecture:** 迁移 0023 增加 challenge、preference、event、delivery 表与 chat_id 唯一约束。交易事务仅创建 outbox，独立 worker claim/send/retry；`TELEGRAM_NOTIFICATION_MODE=broadcast|shadow|directed` 控制渐进切换，directed 必须依赖 owned execution。

> **迁移号（Phase A 占 0016-0020、Phase B 占 0021-0022）**：Phase C 的 notification schema = **0023**（down_revision=0022）。下文出现的 `0018` 为旧编号，以 0023 为准。**安全铁律**：`telegram_notification_mode` 默认 **broadcast**（现状广播不变）；`directed` 必须要求 `multi_user_execution_mode=enforced`（启动校验拒绝该组合的反向）；shadow 只记录路由判定不发送。绑定开启需 bot token + username + webhook secret + `https://` 公网 URL（本机/159 HTTP、113 自签名都不满足正式 webhook，绑定保持关闭不影响其余功能）。

**Tech Stack:** FastAPI webhook、SQLAlchemy/PostgreSQL `FOR UPDATE SKIP LOCKED`、httpx、APScheduler、pytest。

---

## 文件结构

**Create:**
- `migrations/versions/0023_telegram_binding_outbox.py`
- `src/zhice_os_v2/application/services/telegram_binding_service.py`
- `src/zhice_os_v2/application/services/notification_preference_service.py`
- `src/zhice_os_v2/application/services/notification_outbox_service.py`
- `src/zhice_os_v2/application/services/notification_template_service.py`
- `src/zhice_os_v2/application/services/notification_worker.py`
- `src/zhice_os_v2/infrastructure/repositories/notification_repository.py`
- `src/zhice_os_v2/api/routers/integrations_telegram.py`
- `scripts/configure_telegram_webhook.py`
- `tests/test_telegram_binding.py`
- `tests/test_notification_outbox.py`
- `tests/test_telegram_directed.py`
- `tests/test_user_reports.py`（**执行时修正**：用户级报告收件人测试已等价落位于 `tests/test_telegram_directed.py` 的 report_recipients 用例；USER_REPORT 事件（用户维度日报 builder+接线）**顺延 Phase D**，Phase C 仅保留 enum+模板预留）

**Modify:**
- `src/zhice_os_v2/config/settings.py`
- `src/zhice_os_v2/infrastructure/db/models.py`
- `src/zhice_os_v2/infrastructure/repositories/user_repository.py`
- `src/zhice_os_v2/application/services/telegram_notifier.py`
- `src/zhice_os_v2/application/services/workflow_service.py`
- `src/zhice_os_v2/scheduler.py`, `scheduler_jobs.py`
- `src/zhice_os_v2/api/routers/auth.py`, `reports.py`
- `src/zhice_os_v2/api/main.py`
- `.env.example`, `.env.prod.example`, `docker-compose.prod.yml`

### Task 1: Telegram 配置与模式组合验证

- [ ] **Step 1: 写配置 RED 测试**

```python
def test_directed_mode_requires_enforced_execution():
    with pytest.raises(ValueError, match="directed notifications require enforced ownership"):
        AppSettings(multi_user_execution_mode="legacy", telegram_notification_mode="directed")


def test_binding_config_requires_bot_username_and_webhook_secret():
    with pytest.raises(ValueError, match="telegram binding config"):
        AppSettings(telegram_binding_enabled=True, telegram_bot_token="token")
```

- [ ] **Step 2: RED then implementation**

```bash
uv run pytest -q tests/test_telegram_binding.py -k config
```

Add settings：

```python
telegram_bot_username: str = ""
telegram_webhook_secret: str = ""
telegram_webhook_public_url: str = ""
telegram_binding_enabled: bool = False
telegram_notification_mode: Literal["broadcast", "shadow", "directed"] = "broadcast"
telegram_challenge_ttl_seconds: int = 600
notification_worker_batch_size: int = 50
notification_retry_max: int = 6
```

Validator rejects directed unless execution is enforced; binding enabled requires bot token, username, webhook secret and `telegram_webhook_public_url` beginning with `https://`. HTTP or self-signed-only deployments must keep binding disabled until a trusted public HTTPS endpoint exists.

- [ ] **Step 3: compose/env 透传 and GREEN**

Expose settings to API/scheduler without secret defaults. Run the config tests again; expect PASS.

### Task 2: 创建 0023 notification schema

- [ ] **Step 1: 写 metadata RED**

Assert ORM metadata contains challenge/preference/event/delivery tables and telegram_chat_id unique.

- [ ] **Step 2: 实现迁移**

Challenge: challenge_id, user_id FK, token_hash unique, expires_at, consumed_at, created_at.

Preference: user_id FK, channel, market_reports_enabled JSON, system_alerts_enabled, quiet_hours JSON, timezone, locale; unique `(user_id,channel)`.

Event: event_id unique, user_strategy_id/account_id nullable FK, user_id FK, event_type, payload JSON, occurred_at, created_at.

Delivery: delivery_id unique, event_id/user_id FK, channel, status (`PENDING/SENDING/RETRY/SENT/FAILED`), attempt_count, next_attempt_at, claimed_at, last_error_code, created_at, sent_at; unique `(event_id,user_id,channel)`, index `(status,next_attempt_at)`. Add `ck_v2_notification_delivery_status` with exactly those five values. A SENDING row whose `claimed_at` exceeds 5 minutes is eligible for recovery to RETRY.

Before chat unique index, query duplicates and abort with clear error; never silently choose one owner.

- [ ] **Step 3: 映射 ORM and GREEN**

```bash
uv run pytest -q tests/test_telegram_binding.py -k schema tests/test_notification_outbox.py -k schema
```

### Task 3: 一次性 deep-link challenge 与 webhook

- [ ] **Step 1: 写 challenge RED**

```python
def test_create_challenge_stores_hash_only(binding_service, users):
    result = binding_service.create_challenge(users.alice)
    row = binding_service.repo.latest_for_user(users.alice.user_id)
    assert result.url.startswith("https://t.me/test_bot?start=")
    assert result.token not in row.token_hash
    assert row.token_hash == hashlib.sha256(result.token.encode()).hexdigest()


def test_new_challenge_invalidates_old(binding_service, users):
    first = binding_service.create_challenge(users.alice)
    second = binding_service.create_challenge(users.alice)
    assert binding_service.consume(first.token, "123") == "expired_or_used"
    assert binding_service.consume(second.token, "123") == "bound"
```

- [ ] **Step 2: 实现 challenge lifecycle**

Use `secrets.token_urlsafe(32)`, SHA-256, 10-minute expiry; create and consume transactionally; new challenge invalidates old unconsumed rows.

- [ ] **Step 3: 写 webhook RED**

Cover wrong/missing secret header, malformed update, non-start text, expired/replayed token, same chat bound elsewhere and success.

- [ ] **Step 4: 实现 webhook**

Require `X-Telegram-Bot-Api-Secret-Token`, compare with `hmac.compare_digest`; parse `/start <token>` and chat.id; user_id comes only from challenge. Return 200 for ignorable Telegram updates and 403 for invalid secret.

- [ ] **Step 5: 实现 webhook 配置运维脚本**

`scripts/configure_telegram_webhook.py` reads settings, refuses non-HTTPS URL, calls Telegram `setWebhook` with `url` and `secret_token`, then calls `getWebhookInfo` and exits nonzero unless the configured URL matches and `last_error_message` is empty. CLI flags: `--apply` required for setWebhook; default mode only prints redacted current/desired status. Never print Bot Token.

- [ ] **Step 6: 替换 manual binding endpoints**

Add challenge/status/delete. Deprecate PATCH manual binding behind `TELEGRAM_MANUAL_BINDING_ENABLED=false`; remove after rollout.

- [ ] **Step 7: GREEN**

```bash
uv run pytest -q tests/test_telegram_binding.py tests/test_auth.py
```

### Task 4: 用户级与实例级通知偏好

- [ ] **Step 1: 写 RED**

```python
def test_market_report_recipients_require_enabled_instance_and_preference(preference_service, users):
    enable_market_instance(users.alice, "US")
    preference_service.set_market_report(users.alice, "US", True)
    preference_service.set_market_report(users.bob, "US", True)
    assert preference_service.report_recipients("US") == [users.alice.user_id]


def test_instance_fill_disabled(instance_service, alice_instance):
    instance_service.update_notifications(alice_instance.user_id, alice_instance.id, {"fill": False})
    assert instance_service.should_notify(alice_instance.id, "fill") is False
```

- [ ] **Step 2: 实现 strict Pydantic preference models**

Reject unknown notification keys, invalid markets and invalid quiet-hour ranges. Defaults are full closed-loop. Locale defaults from user.

- [ ] **Step 3: 实现 `/my/notification-preferences` and GREEN**

GET/PATCH current user only, no user_id input.

```bash
uv run pytest -q tests/test_telegram_directed.py -k preference
```

### Task 5: Transactional outbox

- [ ] **Step 1: 定义事件类型并写 idempotency RED**

Create the event enum in `notification_outbox_service.py`:

```python
class NotificationType(StrEnum):
    SIGNAL = "signal"
    PLAN = "plan"
    APPROVAL = "approval"
    RISK = "risk"
    ORDER = "order"
    FILL = "fill"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    RUNTIME_ERROR = "runtime_error"
    MARKET_REPORT = "market_report"
    USER_REPORT = "user_report"
```

Use enum values consistently in preferences, events, templates and tests. Then write the idempotency tests:

```python
def test_enqueue_is_idempotent(outbox, owner):
    first = outbox.enqueue(event_id="fill-1", owner=owner, event_type="fill", payload={"fill_id": "f1"})
    second = outbox.enqueue(event_id="fill-1", owner=owner, event_type="fill", payload={"fill_id": "f1"})
    assert first.delivery_id == second.delivery_id
    assert outbox.delivery_count("fill-1") == 1
```

Also test preference-disabled creates event but no delivery.

- [ ] **Step 2: 实现 caller-session API**

```python
def enqueue(
    self, session: Session, *, event_id: str, owner: RuntimeOwner,
    event_type: NotificationType, payload: dict, occurred_at: datetime,
) -> None: ...
```

Use the same transaction as fill/position/report. Insert event and delivery with on-conflict-do-nothing.

- [ ] **Step 3: shadow routing logs**

Log event_id/user_id/event_type/eligible/reason only. No chat_id or payload.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_notification_outbox.py
```

### Task 6: 定向模板和 transport

- [ ] **Step 1: 写 escaping/locale RED**

```python
def test_dynamic_html_is_escaped(template_service):
    text = template_service.render("fill", locale="zh", payload={"strategy": "A&B <突破>"})
    assert "A&amp;B &lt;突破&gt;" in text


def test_english_fill_template(template_service):
    assert "Bought" in template_service.render("fill", locale="en", payload=english_fill_payload())
```

- [ ] **Step 2: 将 notifier 降为 transport**

Define the exact result type and transport signature in `telegram_notifier.py`:

```python
@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    status_code: int | None = None
    error_code: str | None = None
    retry_after: int | None = None


def send_telegram(chat_id: str, text: str) -> TelegramSendResult:
    ...
```

Map HTTP 200 to `ok=True`; parse Telegram 429 `parameters.retry_after`; map HTTP/network errors to stable error codes without including response token/chat data. Do not lookup recipients or log full chat ID.

- [ ] **Step 3: 实现 zh/en templates and GREEN**

Support all approved event types and HTML-escape every dynamic string.

```bash
uv run pytest -q tests/test_telegram_directed.py -k "template or escape or locale" tests/test_telegram_notifier.py
```

### Task 7: Worker claim/retry

- [ ] **Step 1: 写 RED**

Test one claim, two-worker no double claim, success→SENT, 429→RETRY, network/5xx backoff, 401/403→FAILED, max attempt→FAILED, and stale SENDING (`claimed_at < now()-5min`) recovery→RETRY.

- [ ] **Step 2: 实现 SKIP LOCKED claim**

Select due PENDING/RETRY rows `FOR UPDATE SKIP LOCKED LIMIT batch`, mark SENDING, commit, send outside transaction, then compare-and-set result.

- [ ] **Step 3: 实现 retry formula**

```python
def next_retry(attempt: int, retry_after: int | None) -> timedelta:
    if retry_after is not None:
        return timedelta(seconds=min(retry_after, 3600))
    return timedelta(seconds=min(30 * 2 ** max(attempt - 1, 0), 3600))
```

- [ ] **Step 4: 注册 15 秒 scheduler job and GREEN**

```bash
uv run pytest -q tests/test_notification_outbox.py -m "not real_pg"
REAL_PG=1 uv run pytest -q -m real_pg tests/test_notification_outbox.py
```

### Task 8: 接入成交与报告并关闭广播

- [ ] **Step 1: 写 directed fill RED**

```python
def test_alice_fill_only_enqueues_alice_delivery(workflow, alice_instance, bob_instance):
    fill = workflow.record_fill_for(alice_instance)
    assert deliveries_for(fill.fill_id) == [alice_instance.user_id]
```

- [ ] **Step 2: `_record_fill` 同事务 enqueue**

Legacy keeps broadcast, shadow compares, directed creates exact owner delivery. Missing owner in directed creates `notification.owner_missing` system alert; never broadcast fallback.

- [ ] **Step 3: 报告 recipients**

Directed market reports query users with enabled market instance + report preference and deduplicate user IDs. User report builder only uses owner-scoped data.

- [ ] **Step 4: GREEN**

```bash
uv run pytest -q tests/test_telegram_directed.py tests/test_notification_wiring.py \
  tests/test_report_archive.py tests/test_reporting_daily_total_pnl.py
```

### Task 9: 0023 总验证

- [ ] **Step 1: migration/full tests/security scan**

```bash
uv run alembic upgrade head
uv run pytest -q
REAL_PG=1 uv run pytest -q -m real_pg tests/test_notification_outbox.py
uv run ruff check src tests
uv run mypy src/zhice_os_v2
uv run alembic heads
grep -R "api.telegram.org/bot[0-9]" -n src tests .env* || true
grep -R "logger.*chat_id\|logger.*token" -n src/zhice_os_v2 || true
git diff --check
```

Expected: single head 0023, tests/static checks pass, no hardcoded secrets or sensitive logs.

- [ ] **Step 2: commit with authorization**

```bash
git add migrations/versions/0023_telegram_binding_outbox.py src/zhice_os_v2 \
  tests .env.example .env.prod.example docker-compose.prod.yml
git commit -m "feat(telegram): route notifications by user strategy"
```

---

## 执行后归档（终审，2026-09-07）

Phase C 整体终审（4 必修 + 1 文档项）已修：① enqueue 事务中毒防护——
`NotificationOutboxService.enqueue` 调用方事务分支以 `begin_nested()`（SAVEPOINT）
包住全部 outbox 写，PG 语句级错不再把成交事务置 aborted（整笔成交丢失）；
② manual PATCH 撞 `uq_v2_user_telegram_chat_id` → `user_repository.update_telegram_chat_id`
捕 IntegrityError 抛领域错，PATCH `/auth/me/telegram` 返回 409
`{"code": "telegram.chat_conflict"}`（对齐 webhook 语义）；③ `internal_key_auth`
中间件豁免 `/api/v1/integrations/telegram/webhook`（自有 secret 鉴权）；④ worker
`not_configured` 直接终态 FAILED（不再空转 6 次退避）。

**顺延 Phase D / enforced 启用前清单（终审确认的缺口）**：

1. **directed/enforced 下开仓（BUY）无通知**：owned 订单链由
   `_persist_owned_order_chain` 自记四表、不经 `_record_fill`，故开仓成交不触发
   route_fill——目前仅平仓（SELL）有定向通知。→ 归 Phase D，且列入
   **enforced 模式启用前检查清单**（否则用户开仓静默）。
2. **人工审批 `execute_approved` 不走 `_record_fill`**：三模式（broadcast/shadow/
   directed）下该路径均无成交通知（承自广播时代的既有缺口，非本次引入）。
   → 同清单 + Phase D。
3. **challenge 已消费行与 SENT delivery/event 无保留清理策略**：表只增不删。
   → Phase D（按保留窗口归档/清理）。
4. **实例级通知偏好无用户 API 面**：领域/仓储/路由已就绪，`/my/…` 端点与前端
   设置 UI 未建（当前只能靠缺省全开）。→ Phase D UI。
5. **新 409 错误码 `telegram.chat_conflict` 未入前端 errors.json**：后端已按
   `{code}` 方案返回，前端中英文文案待补。→ Phase D。
