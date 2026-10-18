# 国内双渠道通知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为普通用户增加 WxPusher 微信扫码绑定和定向通知，保留 Telegram 独立投递，并把管理员运维告警限定到企业微信群机器人。

**Architecture:** 在现有 transactional outbox 上增加渠道无关的 binding/challenge、统一 transport 契约和按渠道 preference；一个业务 event 可产生 Telegram 与 WxPusher 两条独立 delivery。WxPusher 参数二维码和回调严格按官方协议实现，worker 在发送前读取当前 active binding；企业微信群机器人只接收白名单化运维告警，不接收普通用户业务正文。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic/PostgreSQL、httpx、APScheduler、pytest；React 19、TypeScript 6、Ant Design 6、Vitest/Testing Library、Playwright。

---

## 0. 已锁定的实现契约

以下决策已由设计确认或由 2026-09-17 官方协议核验锁定，执行时不要重新发明第二套语义：

1. 新迁移为 `0033`，`down_revision = "0032"`；不修改历史迁移。
2. 物理表使用仓库统一前缀：`v2_user_notification_binding`、`v2_notification_binding_challenge`。
3. 旧 `v2_user.telegram_chat_id` 第一阶段保留；0033 回填到新 binding 表，所有新读路径以 binding 表为权威，兼容写路径同步旧列。
4. 渠道值只有 `telegram`、`wxpusher`；绑定状态只有 `active`、`revoked`。
5. 渠道级四类开关为：
   - `signal_enabled`
   - `fill_enabled`（覆盖 `fill`、`stop_loss`、`take_profit`）
   - `morning_report_enabled`
   - `evening_report_enabled`
6. 原 `market_reports_enabled` 继续作为报告市场 opt-in；报告投递条件是“市场已订阅”与“晨/晚类型已开启”的交集。
7. 实例级 9 类 preference 保持不变，并与渠道级 preference 取交集；不把两套偏好合并成一套。
8. 用户偏好 API 使用查询参数选择渠道：`GET/PATCH /api/v1/my/notification-preferences?channel=wxpusher`；缺省 `channel=telegram` 保持兼容。
9. WxPusher 参数二维码：
   - `POST https://wxpusher.zjiecode.com/api/fun/create/qrcode`
   - JSON：`{"appToken": ..., "extraCode": <challenge>, "validTime": 600}`
   - 成功需 `success is true` 且顶层 `code == 1000`
   - 前端直接展示 `data.url` 图片；`data.code` 是扫码查询键，不是二维码图片。
10. WxPusher 回调事件为 `app_subscribe` / `app_unsubscribe`；公开协议没有 HMAC 签名。来源校验组合为：不可猜测路径密钥 + `data.appId` + 常量时间比较 `data.appKey` + 一次性 challenge。
11. WxPusher 发送：`POST /api/send/message`，单用户也传 `"uids": [uid]`；成功同时要求顶层 `code == 1000` 和对应 `data[].code == 1000`。
12. 不猜测永久 UID 错误码。只有 `app_unsubscribe`，或已在真实测试应用中固化为 fixture 的目标级永久失败，才可 revoke binding；网络错误、5xx、非 1000 未分类业务错误只做有界重试/终态失败，不自动解绑。
13. WxPusher、Telegram 独立投递；不做失败自动转发。
14. VIEWER 仅放行本人 binding/preference 自助端点，其他写请求继续 403。
15. 报告 payload 增加机器字段 `report_kind: "morning" | "evening"`；event_id 纳入 report kind，不能从中文标题推断。
16. 生产开关分离：配置凭据不等于启用投递；`WXPUSHER_DELIVERY_ENABLED=false` 默认关闭。

官方协议依据：

- <https://wxpusher.zjiecode.com/docs/>
- <https://wxpusher.zjiecode.com/docs/api-reference.html>
- <https://github.com/wxpusher/wxpusher-docs>

## 1. 文件结构与职责

### 新建

- `migrations/versions/0033_domestic_notification_channels.py` — 新表、偏好列、Telegram 回填与回滚。
- `src/zhice_os_v2/domain/notification/contracts.py` — channel、binding 状态、RenderedMessage、SendResult、transport/binding client Protocol。
- `src/zhice_os_v2/infrastructure/repositories/notification_binding_repository.py` — binding/challenge 的事务性持久化。
- `src/zhice_os_v2/infrastructure/notifications/telegram_transport.py` — 现有 Telegram sender 的统一契约适配器。
- `src/zhice_os_v2/infrastructure/notifications/wxpusher_transport.py` — WxPusher 单 UID 发送与双层结果解析。
- `src/zhice_os_v2/infrastructure/notifications/wxpusher_binding_client.py` — 参数二维码创建。
- `src/zhice_os_v2/infrastructure/notifications/wxpusher_webhook.py` — 回调来源校验和事件解析。
- `src/zhice_os_v2/application/services/notification_binding_service.py` — channel-neutral challenge/list/revoke/test 编排。
- `src/zhice_os_v2/application/services/notification_health_service.py` — 有界失败/积压统计与脱敏运维告警。
- `src/zhice_os_v2/api/routers/my_notification_bindings.py` — 本人 binding API。
- `src/zhice_os_v2/api/routers/integrations_wxpusher.py` — WxPusher 回调入口。
- `tests/test_notification_contracts.py`
- `tests/test_migration_0033.py`
- `tests/test_notification_binding_repository.py`
- `tests/test_notification_preferences_channels.py`
- `tests/test_notification_transports.py`
- `tests/test_wxpusher_adapter_contract.py`
- `tests/test_notification_bindings_api.py`
- `tests/test_notification_worker_channels.py`
- `tests/test_notification_outbox_channels.py`
- `tests/test_notification_routing_channels.py`
- `tests/test_notification_ops_alerts.py`
- `frontend/src/components/WxPusherBind.tsx`
- `frontend/src/components/NotificationChannelSettings.tsx`
- `frontend/src/test/notificationChannelsApi.test.ts`
- `frontend/src/test/WxPusherBind.test.tsx`
- `frontend/src/test/UserNotificationPreferencesForm.test.tsx`
- `frontend/src/test/ProfileNotifications.test.tsx`
- `frontend/e2e/profile-notifications.spec.ts`
- `tests/test_notification_real_pg.py` — 绑定并发、条件撤销和多渠道 worker 的专用 PostgreSQL 门禁。
- `docs/runbooks/domestic-notifications-rollout.md`

### 修改

- `src/zhice_os_v2/infrastructure/db/models.py:272-409` — binding/challenge ORM、preference 四类字段。
- `src/zhice_os_v2/domain/notification/preference.py:17-35` — 渠道常量和四类开关。
- `src/zhice_os_v2/config/settings.py:84-99,168-199` — WxPusher 凭据、开关与 fail-fast 校验。
- `src/zhice_os_v2/infrastructure/repositories/telegram_binding_repository.py:43-159` — Telegram challenge 迁移到通用仓储。
- `src/zhice_os_v2/infrastructure/repositories/notification_preference_repository.py:23-151` — 四类开关与 caller-session 支持。
- `src/zhice_os_v2/application/services/telegram_binding_service.py:36-93` — 通用 challenge 兼容。
- `src/zhice_os_v2/application/services/notification_preference_service.py:62-182` — channel API、binding 门、报告类型。
- `src/zhice_os_v2/application/services/notification_template_service.py:159-300` — `RenderedMessage` 与渠道格式。
- `src/zhice_os_v2/application/services/notification_worker.py:78-502` — 快照携带 channel、transport 分流、条件 revoke。
- `src/zhice_os_v2/application/services/notification_outbox_service.py:72-397` — 多渠道 eligibility 与逐渠道 SAVEPOINT。
- `src/zhice_os_v2/application/services/notification_routing.py:90-330` — 微信-only 收件人、晨晚报幂等键。
- `src/zhice_os_v2/application/services/workflow_service.py:1928-1982` — 保留同事务 outbox，删除业务消息直发企业微信路径。
- `src/zhice_os_v2/application/services/alert_service.py:31-158` — 运维告警白名单化。
- `src/zhice_os_v2/api/routers/auth.py:114-220` — 旧 Telegram 响应脱敏、兼容绑定。
- `src/zhice_os_v2/api/routers/my_notification_preferences.py:23-65` — channel query 与 VIEWER 自助。
- `src/zhice_os_v2/api/dependencies.py:109-280` — 新仓储、客户端、服务装配。
- `src/zhice_os_v2/api/main.py:129-254` — 精确写白名单、webhook 豁免与 router 注册；保留当前未提交的 CORS 改动。
- `src/zhice_os_v2/scheduler_jobs.py:176-205,1227-1270` — worker 门、report_kind、健康告警。
- `src/zhice_os_v2/scheduler.py:570-616` — 调度注册回归。
- `.env.example`、`.env.prod.example`、`docker-compose.prod.yml` — API/scheduler 配置透传。
- `nginx/nginx.conf:34-40` — WxPusher webhook 路径关闭 access log，避免路径密钥泄露。
- `frontend/src/services/notificationsApi.ts:11-97` — 渠道 DTO/API。
- `frontend/src/components/UserNotificationPreferencesForm.tsx:23-169` — 四事件双渠道矩阵。
- `frontend/src/pages/ProfilePage.tsx:90-94` — 微信优先、Telegram 保留、状态联动。
- `frontend/src/i18n/locales/{zh,en}/pages.json`、`errors.json` — 双语文案和错误码。
- `.github/workflows/ci.yml:8-24` — 增加前端 test/build 门禁。

## 2. 依赖顺序

```text
Task 1 → Task 2 → Task 3 → Task 4 → Task 5
              └────────→ Task 6 → Task 7 → Task 8
Task 5 + Task 6 ───────→ Task 9 → Task 10 → Task 11 → Task 12
Task 8 + Task 5 ───────→ Task 13 → Task 14 → Task 15
Task 12 + Task 15 ─────→ Task 16
```

---

### Task 1: 通知渠道契约、脱敏与配置门

**Files:**
- Create: `src/zhice_os_v2/domain/notification/contracts.py`
- Create: `tests/test_notification_contracts.py`
- Modify: `src/zhice_os_v2/domain/notification/preference.py:17-35`
- Modify: `src/zhice_os_v2/config/settings.py:84-99,168-199`
- Test: `tests/test_settings_prod_validation.py`

- [ ] **Step 1: 写契约与配置 RED 测试**

```python
# tests/test_notification_contracts.py
from zhice_os_v2.config.settings import AppSettings
from zhice_os_v2.domain.notification.contracts import (
    NotificationChannel,
    SendResult,
    mask_recipient,
)


def test_mask_recipient_never_exposes_short_or_full_identifier():
    assert mask_recipient("1234") == "****"
    assert mask_recipient("UID_abcdef123456") == "UID_***3456"
    assert "abcdef" not in mask_recipient("UID_abcdef123456")


def test_send_result_separates_retry_from_recipient_invalidation():
    result = SendResult(
        ok=False,
        error_code="wxpusher_target_failed",
        retryable=False,
        recipient_invalid=True,
    )
    assert result.retryable is False
    assert result.recipient_invalid is True


def test_wxpusher_is_disabled_by_default():
    settings = AppSettings()
    assert settings.wxpusher_delivery_enabled is False
    assert settings.wxpusher_binding_enabled is False
    assert NotificationChannel.WXPUSHER.value == "wxpusher"


def test_enabling_wxpusher_binding_requires_all_credentials():
    try:
        AppSettings(wxpusher_binding_enabled=True)
    except ValueError as exc:
        text = str(exc)
        assert "WXPUSHER_APP_TOKEN" in text
        assert "WXPUSHER_APP_ID" in text
        assert "WXPUSHER_APP_KEY" in text
        assert "WXPUSHER_WEBHOOK_PATH_KEY" in text
    else:
        raise AssertionError("incomplete WxPusher binding config must fail")


def test_wxpusher_delivery_requires_directed_enforced_mode():
    try:
        AppSettings(
            wxpusher_delivery_enabled=True,
            wxpusher_app_token="AT_test",
            telegram_notification_mode="broadcast",
            multi_user_execution_mode="legacy",
        )
    except ValueError as exc:
        assert "directed notifications require enforced ownership" in str(exc)
    else:
        raise AssertionError("unsafe delivery mode must fail")
```

- [ ] **Step 2: 运行 RED 测试**

Run:

```bash
uv run pytest -q tests/test_notification_contracts.py tests/test_settings_prod_validation.py
```

Expected: FAIL，提示 `notification.contracts` 不存在或 WxPusher settings 字段不存在。

- [ ] **Step 3: 添加最小领域契约**

```python
# src/zhice_os_v2/domain/notification/contracts.py
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"
    WXPUSHER = "wxpusher"


class BindingStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class RenderedMessage:
    summary: str
    text: str
    html: str
    url: str | None = None


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None = None
    error_code: str | None = None
    retryable: bool = False
    recipient_invalid: bool = False
    retry_after: int | None = None


class NotificationTransport(Protocol):
    def send(self, recipient_id: str, message: RenderedMessage) -> SendResult: ...


def mask_recipient(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    if value.startswith("UID_"):
        return f"UID_***{value[-4:]}"
    return f"{'*' * (len(value) - 4)}{value[-4:]}"
```

同时在 `preference.py` 导出：

```python
NOTIFICATION_CHANNEL_TELEGRAM = "telegram"
NOTIFICATION_CHANNEL_WXPUSHER = "wxpusher"
SUPPORTED_NOTIFICATION_CHANNELS = frozenset(
    {NOTIFICATION_CHANNEL_TELEGRAM, NOTIFICATION_CHANNEL_WXPUSHER}
)
```

- [ ] **Step 4: 添加默认关闭的配置和组合校验**

```python
# AppSettings 字段
wxpusher_app_token: str = ""
wxpusher_app_id: int = 0
wxpusher_app_key: str = ""
wxpusher_webhook_path_key: str = ""
wxpusher_binding_enabled: bool = False
wxpusher_delivery_enabled: bool = False
wxpusher_challenge_ttl_seconds: int = 600
wxpusher_timeout_seconds: float = 8.0
```

校验逻辑必须等价于：

```python
if self.wxpusher_binding_enabled:
    missing = []
    if not self.wxpusher_app_token:
        missing.append("WXPUSHER_APP_TOKEN")
    if self.wxpusher_app_id <= 0:
        missing.append("WXPUSHER_APP_ID")
    if not self.wxpusher_app_key:
        missing.append("WXPUSHER_APP_KEY")
    if len(self.wxpusher_webhook_path_key) < 32:
        missing.append("WXPUSHER_WEBHOOK_PATH_KEY（至少 32 字符）")
    if missing:
        raise ValueError("wxpusher binding config 不完整，缺少: " + ", ".join(missing))

if self.wxpusher_delivery_enabled:
    if not self.wxpusher_app_token:
        raise ValueError("WXPUSHER_DELIVERY_ENABLED requires WXPUSHER_APP_TOKEN")
    if (
        self.telegram_notification_mode != "directed"
        or self.multi_user_execution_mode != "enforced"
    ):
        raise ValueError("directed notifications require enforced ownership")
```

- [ ] **Step 5: 跑 GREEN 和静态检查**

```bash
uv run pytest -q tests/test_notification_contracts.py tests/test_settings_prod_validation.py tests/test_telegram_binding.py -k "config or setting or contract"
uv run ruff check src/zhice_os_v2/domain/notification src/zhice_os_v2/config/settings.py tests/test_notification_contracts.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/domain/notification/contracts.py \
  src/zhice_os_v2/domain/notification/preference.py \
  src/zhice_os_v2/config/settings.py tests/test_notification_contracts.py \
  tests/test_settings_prod_validation.py
git commit -m "feat(notification): define channel transport contracts"
```

---

### Task 2: 0033 数据模型、迁移与 Telegram 回填

**Files:**
- Create: `migrations/versions/0033_domestic_notification_channels.py`
- Create: `tests/test_migration_0033.py`
- Modify: `src/zhice_os_v2/infrastructure/db/models.py:272-409`
- Modify: `tests/test_notification_outbox.py`

- [ ] **Step 1: 写 schema、回填、冲突和 downgrade RED 测试**

```python
# tests/test_migration_0033.py

def test_revision_chain(module):
    assert module.revision == "0033"
    assert module.down_revision == "0032"


def test_upgrade_creates_binding_and_generic_challenge(connection, upgrade_0033):
    tables = set(inspect(connection).get_table_names())
    assert "v2_user_notification_binding" in tables
    assert "v2_notification_binding_challenge" in tables


def test_upgrade_backfills_existing_telegram_binding(connection, seed_0032, upgrade_0033):
    row = connection.execute(
        text(
            "SELECT user_id, channel, recipient_id, status "
            "FROM v2_user_notification_binding WHERE user_id='usr-alice'"
        )
    ).mappings().one()
    assert row == {
        "user_id": "usr-alice",
        "channel": "telegram",
        "recipient_id": "12345678",
        "status": "active",
    }


def test_upgrade_aborts_on_duplicate_telegram_recipient(connection, seed_duplicate_0032):
    with pytest.raises(Exception):
        run_upgrade(connection, "0033")


def test_downgrade_removes_only_0033_objects(connection, upgrade_0033, downgrade_0033):
    tables = set(inspect(connection).get_table_names())
    assert "v2_user_notification_binding" not in tables
    assert "v2_notification_delivery" in tables
```

fixture 使用合成值 `usr-alice`、`12345678`，不得读取生产库。

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_migration_0033.py
```

Expected: FAIL，迁移模块不存在。

- [ ] **Step 3: 添加 ORM 模型和 preference 字段**

```python
class UserNotificationBindingModel(Base):
    __tablename__ = "v2_user_notification_binding"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name="ck_v2_notification_binding_status"),
        UniqueConstraint("user_id", "channel", name="uq_v2_notification_binding_user_channel"),
        UniqueConstraint("channel", "recipient_id", name="uq_v2_notification_binding_recipient"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    binding_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_user.user_id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    recipient_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationBindingChallengeModel(Base):
    __tablename__ = "v2_notification_binding_challenge"
    __table_args__ = (
        UniqueConstraint("channel", "provider_code", name="uq_v2_notification_challenge_provider_code"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    challenge_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_user.user_id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider_code: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

给 `UserNotificationPreferenceModel` 增加四个 `Boolean NOT NULL server_default true` 字段。新 binding 初始化后默认开四类；未绑定渠道不会创建 preference，也不会生成 delivery。

- [ ] **Step 4: 实现 0033 迁移**

迁移必须按此顺序：创建表 → 增加 preference 列 → 查询重复 Telegram recipient 并抛脱敏错误 → 回填非空 chat ID → 复制旧 challenge 到通用表（`channel='telegram'`，保留 hash/expiry/consumed）→ downgrade 删除回填数据之外的新结构。

回填必须兼容生产 PostgreSQL 和 migration harness 的 SQLite，不能把 PostgreSQL 专用 `md5()` 写进迁移 SQL。使用绑定连接查询后由 Python 生成稳定 ID，再 `bulk_insert`：

```python
from datetime import UTC, datetime
from hashlib import sha256

bind = op.get_bind()
rows = bind.execute(
    sa.text(
        "SELECT user_id, telegram_chat_id FROM v2_user "
        "WHERE telegram_chat_id IS NOT NULL"
    )
).mappings()
now = datetime.now(UTC)
values = []
for row in rows:
    recipient = str(row["telegram_chat_id"] or "").strip()
    if not recipient:
        continue
    digest = sha256(f"{row['user_id']}:telegram".encode()).hexdigest()[:24]
    values.append({
        "binding_id": f"nbind-{digest}",
        "user_id": row["user_id"],
        "channel": "telegram",
        "recipient_id": recipient,
        "status": "active",
        "bound_at": now,
        "revoked_at": None,
        "created_at": now,
        "updated_at": now,
    })
if values:
    op.bulk_insert(binding_table, values)
```

错误信息只显示重复数量，不回显 chat ID。SQLite 与 PostgreSQL 都必须运行该真实回填路径，而不是在测试中跳过。

- [ ] **Step 5: 跑迁移与模型 GREEN**

```bash
uv run pytest -q tests/test_migration_0033.py tests/test_notification_outbox.py -k "schema or constraint"
uv run alembic heads
```

Expected: PASS，`alembic heads` 只输出 `0033 (head)`。

- [ ] **Step 6: 提交**

```bash
git add migrations/versions/0033_domestic_notification_channels.py \
  src/zhice_os_v2/infrastructure/db/models.py \
  tests/test_migration_0033.py tests/test_notification_outbox.py
git commit -m "feat(notification): add channel binding schema"
```

---

### Task 3: Binding/challenge 原子仓储

**Files:**
- Create: `src/zhice_os_v2/infrastructure/repositories/notification_binding_repository.py`
- Create: `tests/test_notification_binding_repository.py`
- Modify: `src/zhice_os_v2/infrastructure/repositories/notification_preference_repository.py:23-151`

- [ ] **Step 1: 写哈希、旧 challenge 作废、并发、冲突、解绑 RED 测试**

```python

def test_create_challenge_stores_hash_only(repo, session, alice):
    token = "synthetic-high-entropy-token"
    row = repo.create_challenge(
        session,
        user_id=alice.user_id,
        channel="wxpusher",
        token=token,
        expires_at=NOW + timedelta(minutes=10),
    )
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash


def test_new_challenge_invalidates_previous(repo, session, alice):
    first = create(repo, session, alice, "token-one")
    second = create(repo, session, alice, "token-two")
    assert repo.consume_and_bind(session, token="token-one", channel="wxpusher", recipient_id="UID_A", now=NOW) == "expired_or_used"
    assert repo.consume_and_bind(session, token="token-two", channel="wxpusher", recipient_id="UID_A", now=NOW) == "bound"


def test_recipient_cannot_move_between_accounts(repo, session, alice, bob):
    bind(repo, session, alice, "UID_SHARED")
    with pytest.raises(ValueError, match="notification.binding_recipient_conflict"):
        bind(repo, session, bob, "UID_SHARED")


def test_unbind_invalidates_open_challenges(repo, session, alice):
    create(repo, session, alice, "token-before-unbind")
    repo.revoke(session, user_id=alice.user_id, channel="wxpusher", now=NOW)
    assert repo.consume_and_bind(
        session,
        token="token-before-unbind",
        channel="wxpusher",
        recipient_id="UID_A",
        now=NOW,
    ) == "expired_or_used"


def test_conditional_revoke_does_not_revoke_new_recipient(repo, session, alice):
    bind(repo, session, alice, "UID_OLD")
    create(repo, session, alice, "token-rebind")
    assert repo.consume_and_bind(
        session,
        token="token-rebind",
        channel="wxpusher",
        recipient_id="UID_NEW",
        now=NOW,
    ) == "bound"
    assert repo.revoke_if_current(session, alice.user_id, "wxpusher", "UID_OLD", NOW) is False
    assert repo.get_active(session, alice.user_id, "wxpusher").recipient_id == "UID_NEW"
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_binding_repository.py
```

Expected: FAIL，仓储不存在。

- [ ] **Step 3: 实现 caller-session 仓储**

公开方法签名固定为：

```python
class NotificationBindingRepository:
    def create_challenge(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        token: str,
        expires_at: datetime,
    ) -> NotificationBindingChallengeModel: ...

    def set_provider_code(
        self, session: Session, challenge_id: str, provider_code: str
    ) -> bool: ...

    def consume_and_bind(
        self,
        session: Session,
        *,
        token: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> Literal["bound", "already_bound", "expired_or_used"]: ...

    def bind_verified(
        self,
        session: Session,
        *,
        user_id: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> UserNotificationBindingModel: ...

    def get_active(
        self, session: Session, user_id: str, channel: str
    ) -> UserNotificationBindingModel | None: ...

    def list_for_user(
        self, session: Session, user_id: str
    ) -> list[UserNotificationBindingModel]: ...

    def revoke(
        self, session: Session, *, user_id: str, channel: str, now: datetime
    ) -> bool: ...

    def revoke_if_current(
        self,
        session: Session,
        user_id: str,
        channel: str,
        recipient_id: str,
        now: datetime,
    ) -> bool: ...
```

`create_challenge` 先 `SELECT v2_user ... FOR UPDATE` 锁用户行，再作废同用户同渠道旧 challenge，解决首次并发生成无旧行可锁的问题。`consume_and_bind` 仅用于 challenge 驱动的绑定；`bind_verified` 只允许已由 auth 路由或可信 provider reconciliation 确认身份的调用方使用，manual Telegram bind 通过它同步新表。仓储不自行 commit/rollback。

- [ ] **Step 4: 同事务初始化 preference**

给 `NotificationPreferenceRepository.create/update` 增加可选 `session: Session | None`；`consume_and_bind` 成功后由 service 在同一 session 内创建对应渠道默认 preference，不能跨两个独立 commit。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_notification_binding_repository.py tests/test_notification_outbox.py -k "preference or binding"
uv run ruff check src/zhice_os_v2/infrastructure/repositories tests/test_notification_binding_repository.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/infrastructure/repositories/notification_binding_repository.py \
  src/zhice_os_v2/infrastructure/repositories/notification_preference_repository.py \
  tests/test_notification_binding_repository.py
git commit -m "feat(notification): add atomic channel binding repository"
```

---

### Task 4: Telegram 新旧绑定兼容与身份响应脱敏

**Files:**
- Modify: `src/zhice_os_v2/infrastructure/repositories/telegram_binding_repository.py:43-159`
- Modify: `src/zhice_os_v2/application/services/telegram_binding_service.py:36-93`
- Modify: `src/zhice_os_v2/infrastructure/repositories/user_repository.py:51-90`
- Modify: `src/zhice_os_v2/application/services/auth_service.py:24-60`
- Modify: `src/zhice_os_v2/api/routers/auth.py:114-220`
- Modify: `src/zhice_os_v2/application/services/telegram_notifier.py:119-160`
- Create: `tests/test_telegram_binding_compat.py`

- [ ] **Step 1: 写兼容与隐私 RED 测试**

```python

def test_existing_chat_id_is_available_as_active_binding(repo, migrated_session):
    row = repo.get_active(migrated_session, "usr-alice", "telegram")
    assert row is not None
    assert row.recipient_id == "12345678"


def test_manual_bind_updates_new_binding_and_legacy_column(client, session, auth_headers):
    response = client.patch(
        "/api/v1/auth/me/telegram",
        headers=auth_headers,
        json={"chat_id": "12345678"},
    )
    assert response.status_code == 200
    assert response.json()["telegram_chat_id"] == "****5678"
    assert active_recipient(session, "usr-alice", "telegram") == "12345678"
    assert legacy_chat_id(session, "usr-alice") == "12345678"


def test_auth_responses_never_return_full_chat_id(client, bound_user_headers):
    for path in ("/api/v1/auth/me", "/api/v1/auth/login"):
        response = get_or_post_auth(client, path, bound_user_headers)
        assert "12345678" not in response.text


def test_revoked_binding_does_not_fall_back_to_legacy_column(worker_env):
    worker_env.revoke_binding("usr-alice", "telegram")
    assert worker_env.resolve_recipient("usr-alice", "telegram") is None
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_telegram_binding_compat.py tests/test_telegram_binding.py
```

Expected: FAIL，新 binding 未接入或 API 回显完整 chat ID。

- [ ] **Step 3: 切换 Telegram 写路径到统一 binding**

Telegram manual bind、deep-link consume、DELETE 必须在一个事务内。两种绑定入口明确分开：

```python
# deep-link：消费 challenge 并绑定
binding_repo.consume_and_bind(
    session,
    token=token,
    channel="telegram",
    recipient_id=chat_id,
    now=now,
)

# authenticated manual bind：无需伪造 challenge，使用受信写入口
binding_repo.bind_verified(
    session,
    user_id=current_user.user_id,
    channel="telegram",
    recipient_id=chat_id,
    now=now,
)
user.telegram_chat_id = chat_id
```

解绑同时把 binding 标记 `revoked`、作废 challenge、设置旧列为 `NULL`。读取时只查 binding；旧列只作为迁移兼容写镜像，不作为 revoked 后回退来源。

- [ ] **Step 4: 脱敏 auth 与日志**

所有 login/register/me/manual bind 响应中的 `telegram_chat_id` 改为 `mask_recipient` 结果或 `None`。`_redact_chat_id` 调用统一 `mask_recipient`，长度小于等于 4 时全掩码。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_telegram_binding_compat.py tests/test_telegram_binding.py \
  tests/test_telegram_notifier.py tests/test_b7_reports_telegram_errors.py \
  tests/test_user_management.py
```

Expected: PASS，既有用户不需要重新绑定。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/infrastructure/repositories/telegram_binding_repository.py \
  src/zhice_os_v2/application/services/telegram_binding_service.py \
  src/zhice_os_v2/infrastructure/repositories/user_repository.py \
  src/zhice_os_v2/application/services/auth_service.py \
  src/zhice_os_v2/api/routers/auth.py \
  src/zhice_os_v2/application/services/telegram_notifier.py \
  tests/test_telegram_binding_compat.py tests/test_telegram_binding.py
git commit -m "refactor(notification): unify Telegram bindings"
```

---

### Task 5: 四类渠道偏好与 VIEWER 本人自助权限

**Files:**
- Modify: `src/zhice_os_v2/domain/notification/preference.py:24-35`
- Modify: `src/zhice_os_v2/infrastructure/repositories/notification_preference_repository.py:23-151`
- Modify: `src/zhice_os_v2/application/services/notification_preference_service.py:62-182`
- Modify: `src/zhice_os_v2/api/routers/my_notification_preferences.py:23-65`
- Modify: `src/zhice_os_v2/api/main.py:153-200`
- Create: `tests/test_notification_preferences_channels.py`
- Modify: `tests/test_telegram_directed.py:389-535`
- Modify: `tests/test_rbac_viewer.py:38-130`

- [ ] **Step 1: 写双渠道、binding 门和 VIEWER RED 测试**

```python

def test_preferences_are_channel_scoped(pref_api, alice_headers, bound_both):
    wx = pref_api.patch(
        "wxpusher",
        {"signal_enabled": False, "fill_enabled": True},
        alice_headers,
    )
    tg = pref_api.get("telegram", alice_headers)
    assert wx["channel"] == "wxpusher"
    assert wx["signal_enabled"] is False
    assert tg["signal_enabled"] is True


def test_cannot_enable_unbound_channel(pref_api, alice_headers):
    response = pref_api.raw_patch(
        "wxpusher", {"signal_enabled": True}, alice_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "notification.binding_required"


def test_viewer_can_patch_only_own_notification_preferences(viewer_client, viewer_headers):
    own = viewer_client.patch(
        "/api/v1/my/notification-preferences?channel=wxpusher",
        headers=viewer_headers,
        json={"signal_enabled": False},
    )
    assert own.status_code == 200
    business = viewer_client.post(
        "/api/v1/my/accounts", headers=viewer_headers, json={"name": "forbidden"}
    )
    assert business.status_code == 403


def test_report_gate_requires_market_and_report_kind(pref_service, alice):
    enable_binding(alice, "wxpusher")
    pref_service.update(
        alice.user_id,
        body(market_reports_enabled={"US": True}, morning_report_enabled=False),
        channel="wxpusher",
    )
    assert pref_service.report_enabled(alice.user_id, "wxpusher", "US", "morning") is False
    assert pref_service.report_enabled(alice.user_id, "wxpusher", "US", "evening") is True
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_preferences_channels.py tests/test_rbac_viewer.py \
  tests/test_telegram_directed.py -k "preference or viewer"
```

Expected: FAIL，字段或 channel query 不存在，VIEWER 返回 403。

- [ ] **Step 3: 扩展 domain/body/serialization**

```python
@dataclass
class UserNotificationPreference:
    preference_id: str
    user_id: str
    channel: str = NOTIFICATION_CHANNEL_TELEGRAM
    market_reports_enabled: dict = field(default_factory=dict)
    signal_enabled: bool = True
    fill_enabled: bool = True
    morning_report_enabled: bool = True
    evening_report_enabled: bool = True
    system_alerts_enabled: bool = True  # 兼容旧数据，不作为企业微信配置
    quiet_hours: dict | None = None
    timezone: str = DEFAULT_NOTIFICATION_TIMEZONE
    locale: str = "zh"
```

`NotificationPreferenceBody` 增加四个 `StrictBool | None` 字段；API 增加：

```python
from fastapi import Query


def _channel(value: str = Query(default="telegram")) -> str:
    if value not in SUPPORTED_NOTIFICATION_CHANNELS:
        raise HTTPException(status_code=422, detail={"code": "notification.channel_invalid"})
    return value
```

GET/PATCH 都使用该 dependency，缺省 Telegram。

- [ ] **Step 4: 精确放行 VIEWER 自助路径**

router PATCH 角色改为 `(Role.VIEWER, Role.USER, Role.ADMIN)`；`_VIEWER_WRITE_ALLOW` 只加入：

```python
("PATCH", "/api/v1/my/notification-preferences"),
("POST", "/api/v1/auth/me/notification-bindings/"),
("DELETE", "/api/v1/auth/me/notification-bindings/"),
```

`_write_allowed` 对 binding 路径做前缀匹配，但不得放行其他 `/my/` 或 `/auth/users/` 写面。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_notification_preferences_channels.py tests/test_rbac_viewer.py \
  tests/test_telegram_directed.py
```

Expected: PASS；原 `test_api_patch_viewer_forbidden` 更新为“本人通知偏好允许，业务写仍禁止”。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/domain/notification/preference.py \
  src/zhice_os_v2/infrastructure/repositories/notification_preference_repository.py \
  src/zhice_os_v2/application/services/notification_preference_service.py \
  src/zhice_os_v2/api/routers/my_notification_preferences.py \
  src/zhice_os_v2/api/main.py tests/test_notification_preferences_channels.py \
  tests/test_telegram_directed.py tests/test_rbac_viewer.py
git commit -m "feat(notification): add channel preference matrix"
```

---

### Task 6: RenderedMessage、Telegram 适配器与 WxPusher transport

**Files:**
- Create: `src/zhice_os_v2/infrastructure/notifications/__init__.py`
- Create: `src/zhice_os_v2/infrastructure/notifications/telegram_transport.py`
- Create: `src/zhice_os_v2/infrastructure/notifications/wxpusher_transport.py`
- Modify: `src/zhice_os_v2/application/services/notification_template_service.py:159-300`
- Modify: `src/zhice_os_v2/application/services/telegram_notifier.py:24-116`
- Create: `tests/test_notification_transports.py`
- Modify: `tests/test_telegram_notifier.py`

- [ ] **Step 1: 写 transport 双层成功、限流和泄密 RED 测试**

```python

def test_wxpusher_requires_top_level_and_target_success(mock_http):
    mock_http.respond(
        200,
        {"success": True, "code": 1000, "data": [
            {"uid": "UID_A", "code": 1001, "status": "failed"}
        ]},
    )
    result = wx_transport(mock_http).send("UID_A", sample_message())
    assert result.ok is False
    assert result.error_code == "wxpusher_target_1001"
    assert result.retryable is True


def test_wxpusher_sends_single_uid_array(mock_http):
    mock_http.respond(
        200,
        {"success": True, "code": 1000, "data": [
            {"uid": "UID_A", "code": 1000, "status": "success"}
        ]},
    )
    assert wx_transport(mock_http).send("UID_A", sample_message()).ok is True
    assert mock_http.last_json["uids"] == ["UID_A"]
    assert "topicIds" not in mock_http.last_json


def test_http_200_business_error_is_not_success(mock_http):
    mock_http.respond(200, {"success": False, "code": 2002, "msg": "bad token"})
    result = wx_transport(mock_http).send("UID_A", sample_message())
    assert result.ok is False
    assert result.error_code == "wxpusher_api_2002"
    assert "AT_secret" not in repr(result)


def test_network_error_is_retryable_without_raw_exception(mock_http):
    mock_http.raise_timeout("https://.../AT_secret/UID_A")
    result = wx_transport(mock_http).send("UID_A", sample_message())
    assert result.retryable is True
    assert result.error_code == "network"
    assert "AT_secret" not in repr(result)


def test_template_escapes_dynamic_content(template_service):
    message = template_service.render_message(
        "fill", locale="zh", payload={"symbol": "A&B <突破>", "price": 10}
    )
    assert "A&amp;B &lt;突破&gt;" in message.html
    assert "A&B <突破>" in message.text
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_transports.py tests/test_telegram_notifier.py
```

Expected: FAIL，transport 或 `render_message` 不存在。

- [ ] **Step 3: 改造模板返回统一消息**

`NotificationTemplateService.render_message` 返回：

```python
return RenderedMessage(
    summary=summary[:100],
    text=plain_text,
    html=html_text,
    url=deep_link,
)
```

保留旧 `render()`，内部返回 `.html`，供尚未迁移的 Telegram 测试兼容。

- [ ] **Step 4: 实现 transport**

Telegram adapter 把 `TelegramSendResult` 映射成统一 `SendResult`。WxPusher 请求体固定为：

```python
payload = {
    "appToken": self._app_token,
    "summary": message.summary,
    "content": message.html,
    "contentType": 2,
    "uids": [recipient_id],
}
if message.url:
    payload["url"] = message.url
```

响应先检查 HTTP，再检查顶层 `success/code`，最后精确找当前 UID 的 `data[]`。未分类业务错误默认 `retryable=True`，由 retry_max 收敛；`recipient_invalid=False`。不基于未文档化数字码自动撤销。

- [ ] **Step 5: 跑 GREEN 与架构检查**

```bash
uv run pytest -q tests/test_notification_transports.py tests/test_telegram_notifier.py \
  tests/test_telegram_directed.py -k "template or transport or escape"
uv run pytest -q tests/test_architecture.py
uv run ruff check src/zhice_os_v2/infrastructure/notifications \
  src/zhice_os_v2/application/services/notification_template_service.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/infrastructure/notifications \
  src/zhice_os_v2/application/services/notification_template_service.py \
  src/zhice_os_v2/application/services/telegram_notifier.py \
  tests/test_notification_transports.py tests/test_telegram_notifier.py \
  tests/test_telegram_directed.py
git commit -m "feat(notification): add WxPusher transport"
```

---

### Task 7: WxPusher 参数二维码与 webhook 协议适配器

**Files:**
- Create: `src/zhice_os_v2/infrastructure/notifications/wxpusher_binding_client.py`
- Create: `src/zhice_os_v2/infrastructure/notifications/wxpusher_webhook.py`
- Create: `tests/test_wxpusher_adapter_contract.py`

- [ ] **Step 1: 用官方脱敏 fixture 写 RED 测试**

```python
SUBSCRIBE_EVENT = {
    "action": "app_subscribe",
    "data": {
        "appId": 123,
        "appKey": "AK_synthetic",
        "appName": "Test App",
        "source": "scan",
        "userName": "Synthetic User",
        "uid": "UID_synthetic",
        "extraCode": "synthetic-challenge",
    },
}


def test_create_qr_uses_official_fields(mock_http):
    mock_http.respond(200, {
        "success": True,
        "code": 1000,
        "data": {"code": "QRC_1", "shortUrl": "https://s", "url": "https://img"},
    })
    qr = client(mock_http).create_parameter_qr("synthetic-challenge", valid_time=600)
    assert mock_http.last_json == {
        "appToken": "AT_synthetic",
        "extraCode": "synthetic-challenge",
        "validTime": 600,
    }
    assert qr.provider_code == "QRC_1"
    assert qr.image_url == "https://img"


def test_scan_query_uses_provider_code_and_returns_uid(mock_http):
    mock_http.respond(200, {
        "success": True,
        "code": 1000,
        "data": {"uid": "UID_synthetic"},
    })
    assert client(mock_http).query_scanned_uid("QRC_1") == "UID_synthetic"
    assert mock_http.last_params == {"code": "QRC_1"}


def test_subscribe_parser_uses_uid_and_extra_code_only():
    event = parser().parse(SUBSCRIBE_EVENT)
    assert event.action == "subscribe"
    assert event.recipient_id == "UID_synthetic"
    assert event.challenge_token == "synthetic-challenge"


def test_webhook_verifier_checks_app_id_and_constant_time_app_key():
    assert verifier(app_id=123, app_key="AK_synthetic").verify(SUBSCRIBE_EVENT) is True
    wrong = copy.deepcopy(SUBSCRIBE_EVENT)
    wrong["data"]["appKey"] = "AK_wrong"
    assert verifier(app_id=123, app_key="AK_synthetic").verify(wrong) is False


def test_unsubscribe_has_no_challenge_token():
    event = parser().parse({
        "action": "app_unsubscribe",
        "data": {"appId": 123, "appKey": "AK_synthetic", "uid": "UID_synthetic"},
    })
    assert event.action == "unsubscribe"
    assert event.challenge_token is None
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_wxpusher_adapter_contract.py
```

Expected: FAIL，adapter 不存在。

- [ ] **Step 3: 实现二维码客户端**

```python
@dataclass(frozen=True)
class WxPusherQrCode:
    provider_code: str
    image_url: str
    short_url: str


class WxPusherBindingClient:
    def create_parameter_qr(self, extra_code: str, *, valid_time: int) -> WxPusherQrCode:
        response = self._client.post(
            "https://wxpusher.zjiecode.com/api/fun/create/qrcode",
            json={
                "appToken": self._app_token,
                "extraCode": extra_code,
                "validTime": valid_time,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("success") is not True or body.get("code") != 1000:
            raise WxPusherBindingError(f"wxpusher_qr_{body.get('code', 'invalid')}")
        data = body.get("data") or {}
        return WxPusherQrCode(
            provider_code=str(data["code"]),
            image_url=str(data["url"]),
            short_url=str(data["shortUrl"]),
        )

    def query_scanned_uid(self, provider_code: str) -> str | None:
        response = self._client.get(
            "https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid",
            params={"code": provider_code},
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("success") is not True or body.get("code") != 1000:
            raise WxPusherBindingError(f"wxpusher_scan_{body.get('code', 'invalid')}")
        data = body.get("data")
        if not data:
            return None
        return str(data.get("uid") if isinstance(data, dict) else data)
```

异常只保留稳定错误码，不拼接响应原文。

- [ ] **Step 4: 实现 verifier/parser**

`verify` 先检查 `appId`，再用 `hmac.compare_digest` 比较 `appKey`。parser 只接受两个 action；`userName` 不写 binding、不写日志。Pydantic 模型 `extra="ignore"`，但 `uid`、`extraCode` 类型和长度严格限制，避免默认 422 回显原 payload。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_wxpusher_adapter_contract.py tests/test_notification_contracts.py
uv run ruff check src/zhice_os_v2/infrastructure/notifications tests/test_wxpusher_adapter_contract.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/infrastructure/notifications/wxpusher_binding_client.py \
  src/zhice_os_v2/infrastructure/notifications/wxpusher_webhook.py \
  tests/test_wxpusher_adapter_contract.py
git commit -m "feat(notification): add WxPusher binding adapters"
```

---

### Task 8: WxPusher 绑定服务、本人 API 与回调

**Files:**
- Create: `src/zhice_os_v2/application/services/notification_binding_service.py`
- Create: `src/zhice_os_v2/api/routers/my_notification_bindings.py`
- Create: `src/zhice_os_v2/api/routers/integrations_wxpusher.py`
- Modify: `src/zhice_os_v2/api/dependencies.py:109-280`
- Modify: `src/zhice_os_v2/api/main.py:129-254`
- Modify: `nginx/nginx.conf:34-40`
- Create: `tests/test_notification_bindings_api.py`

- [ ] **Step 1: 写 challenge、API、回调、退订、VIEWER RED 测试**

```python

def test_create_challenge_returns_image_not_plain_token(api, viewer_headers, mock_qr):
    response = api.post(
        "/api/v1/auth/me/notification-bindings/wxpusher/challenge",
        headers=viewer_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["qr_image_url"] == "https://img.example/qr"
    assert body["expires_at"].endswith("+00:00")
    assert "token" not in body
    assert response.headers["cache-control"] == "no-store"


def test_subscribe_callback_binds_user_once(api, webhook_path, subscribe_event):
    first = api.post(webhook_path, json=subscribe_event)
    second = api.post(webhook_path, json=subscribe_event)
    assert first.status_code == 204
    assert second.status_code == 204
    assert count_active_binding("usr-viewer", "wxpusher") == 1


def test_wrong_path_key_or_app_key_fails_closed(api, subscribe_event):
    assert api.post("/api/v1/integrations/wxpusher/webhook/wrong", json=subscribe_event).status_code == 404
    subscribe_event["data"]["appKey"] = "AK_wrong"
    assert api.post(valid_webhook_path(), json=subscribe_event).status_code == 403


def test_unsubscribe_revokes_current_uid(api, webhook_path, unsubscribe_event):
    response = api.post(webhook_path, json=unsubscribe_event)
    assert response.status_code == 204
    assert binding_status("usr-viewer", "wxpusher") == "revoked"


def test_test_message_ignores_caller_supplied_identity_and_uses_current_user(
    api, viewer_headers, transport_spy
):
    response = api.post(
        "/api/v1/auth/me/notification-bindings/wxpusher/test",
        headers=viewer_headers,
        json={"user_id": "usr-other"},
    )
    assert response.status_code == 200
    assert transport_spy.recipients == ["UID_VIEWER"]
    assert "UID_OTHER" not in transport_spy.recipients


def test_delete_has_no_cross_account_identifier(api, viewer_headers):
    response = api.delete(
        "/api/v1/auth/me/notification-bindings/wxpusher?user_id=usr-other",
        headers=viewer_headers,
    )
    assert response.status_code == 200
    assert binding_status("usr-viewer", "wxpusher") == "revoked"
    assert binding_status("usr-other", "wxpusher") == "active"
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_bindings_api.py tests/test_rbac_viewer.py
```

Expected: FAIL，路由不存在。

- [ ] **Step 3: 实现 service 事务边界**

`create_wxpusher_challenge` 顺序固定：本地事务生成 challenge 并提交 → 外部创建二维码 → 短事务按 challenge_id 写 `provider_code`；外部失败时仅条件作废本次 challenge，不能跨 HTTP 持锁，也不能撤销随后创建的新 challenge。

轮询必须闭环而不是只轮询本地 binding：challenge API 响应不向前端暴露 `provider_code`，后端状态查询在发现本人有未消费的 WxPusher challenge 且尚未绑定时，使用服务端保存的 `provider_code` 调官方：

```http
GET https://wxpusher.zjiecode.com/api/fun/scan-qrcode-uid?code=QRC_xxx
```

若返回 UID，则走与 webhook 完全相同的 `consume_and_bind` 事务；若未扫码则保持 unbound。该查询由后端节流为每个 challenge 最快 3 秒一次，appToken/provider_code 不返回前端、不写日志。测试需覆盖“webhook 丢失但官方扫码查询返回 UID 后仍可绑定”和“重复轮询不重复初始化 preference”。

返回 DTO：

```python
@dataclass(frozen=True)
class BindingChallengeView:
    qr_image_url: str
    expires_at: datetime


@dataclass(frozen=True)
class BindingView:
    channel: str
    status: str
    recipient_masked: str | None
    bound_at: datetime | None
```

- [ ] **Step 4: 实现 API 与 webhook**

端点固定为：

```text
POST   /api/v1/auth/me/notification-bindings/wxpusher/challenge
GET    /api/v1/auth/me/notification-bindings
DELETE /api/v1/auth/me/notification-bindings/wxpusher
POST   /api/v1/auth/me/notification-bindings/wxpusher/test
POST   /api/v1/integrations/wxpusher/webhook/{path_key}
```

webhook route 设置 `include_in_schema=False`；先常量时间比较 path key，再 verifier，再 parser。成功/重复回调返回空 `204`；错误响应只返回 `{code}`，不回显请求体。

- [ ] **Step 5: 精确 middleware/nginx 保护**

`internal_key_auth` 只豁免 `/api/v1/integrations/wxpusher/webhook/` 前缀；数据库 VIEWER 写白名单只放本人 binding 路径。nginx 为该前缀单独 location，复用 API proxy，但 `access_log off;`，避免路径密钥落 access log。

- [ ] **Step 6: 跑 GREEN、安全回归与架构测试**

```bash
uv run pytest -q tests/test_notification_bindings_api.py tests/test_rbac_viewer.py \
  tests/test_telegram_binding.py tests/test_architecture.py
uv run ruff check src/zhice_os_v2/api src/zhice_os_v2/application/services/notification_binding_service.py
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/zhice_os_v2/application/services/notification_binding_service.py \
  src/zhice_os_v2/api/routers/my_notification_bindings.py \
  src/zhice_os_v2/api/routers/integrations_wxpusher.py \
  src/zhice_os_v2/api/dependencies.py src/zhice_os_v2/api/main.py \
  nginx/nginx.conf tests/test_notification_bindings_api.py tests/test_rbac_viewer.py
git commit -m "feat(notification): add WxPusher scan binding API"
```

---

### Task 9: Worker 多渠道快照、分流与条件撤销

**Files:**
- Modify: `src/zhice_os_v2/application/services/notification_worker.py:78-502`
- Create: `tests/test_notification_worker_channels.py`
- Modify: `tests/test_notification_worker.py`

- [ ] **Step 1: 写分流、解绑、重绑、暂停和跨渠道隔离 RED 测试**

```python

def test_worker_dispatches_by_claimed_channel(env):
    env.seed_delivery(channel="wxpusher")
    env.worker().run_once()
    assert env.wxpusher_calls == [("UID_A", "fill")]
    assert env.telegram_calls == []


def test_worker_reads_current_binding_after_claim(env):
    delivery = env.seed_delivery(channel="wxpusher")
    env.claim(delivery)
    env.replace_binding("UID_A", "UID_NEW")
    env.worker().process_claimed(delivery)
    assert env.wxpusher_calls[0][0] == "UID_NEW"


def test_revoked_binding_fails_without_network_call(env):
    env.seed_delivery(channel="wxpusher")
    env.revoke_binding("wxpusher")
    stats = env.worker().run_once()
    assert stats["failed"] == 1
    assert env.wxpusher_calls == []


def test_invalid_old_uid_does_not_revoke_new_binding(env):
    delivery = env.seed_delivery(channel="wxpusher")
    env.transport_result = SendResult(
        ok=False, error_code="uid_invalid_verified", recipient_invalid=True
    )
    env.replace_binding("UID_A", "UID_NEW")
    env.worker().run_once()
    assert env.active_binding("wxpusher") == "UID_NEW"


def test_disabled_channel_is_not_claimed(env):
    env.seed_delivery(channel="wxpusher")
    env.wxpusher_enabled = False
    stats = env.worker().run_once()
    assert stats["claimed"] == 0
    assert env.delivery_status() == "PENDING"
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_worker_channels.py tests/test_notification_worker.py
```

Expected: FAIL，`_Claimed` 无 channel，worker 固定 Telegram。

- [ ] **Step 3: 扩展快照与 transport registry**

```python
@dataclass(frozen=True)
class _Claimed:
    delivery_id: str
    event_id: str
    user_id: str
    channel: str
    event_type: str
    payload: dict
    attempt_count: int
    claim_epoch: datetime
```

构造函数接收：

```python
transports: Mapping[str, NotificationTransport] | None = None
binding_repository: NotificationBindingRepository | None = None
channel_enabled: Callable[[str], bool] | None = None
```

未知 channel 返回 `FAILED(unsupported_channel)`，绝不能默认走 Telegram。

- [ ] **Step 4: 保留并扩展并发状态机**

`_claim` join event 读取 event_type/payload，并在 WHERE 中排除暂停渠道。SKIP LOCKED、touch、epoch CAS、stale recovery 原样保留。`_process`：发送前查当前 active binding → render_message → 对应 transport → 将 `SendResult.retryable` 映射为 RETRY/FAILED。

若 `recipient_invalid=True`，finalize delivery 和 `revoke_if_current(user, channel, recipient_used)` 在同一数据库事务中完成；应用 token 错误不得 revoke 用户。

- [ ] **Step 5: 跑 GREEN 与原 worker 全回归**

```bash
uv run pytest -q tests/test_notification_worker_channels.py tests/test_notification_worker.py
uv run pytest -q tests/test_architecture.py
```

Expected: PASS，现有 claim/touch/CAS/stale 测试保持通过。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/application/services/notification_worker.py \
  tests/test_notification_worker_channels.py tests/test_notification_worker.py
git commit -m "feat(notification): dispatch worker by channel"
```

---

### Task 10: Outbox 多渠道路由与逐渠道 SAVEPOINT

**Files:**
- Modify: `src/zhice_os_v2/application/services/notification_outbox_service.py:72-397`
- Create: `tests/test_notification_outbox_channels.py`
- Modify: `tests/test_notification_outbox.py`
- Modify: `tests/test_notification_wiring.py`

- [ ] **Step 1: 写微信-only、双渠道、单渠道 SQL 失败 RED 测试**

```python

def test_wxpusher_only_user_gets_one_wxpusher_delivery(env):
    env.bind("wxpusher", "UID_A")
    env.enable("wxpusher", signal=True)
    env.enqueue_signal()
    assert env.delivery_channels() == ["wxpusher"]


def test_user_with_both_bindings_gets_two_independent_deliveries(env):
    env.bind("telegram", "12345678")
    env.bind("wxpusher", "UID_A")
    env.enqueue_fill()
    assert set(env.delivery_channels()) == {"telegram", "wxpusher"}


def test_one_channel_insert_failure_preserves_other_delivery_and_business_row(env):
    env.fail_delivery_insert_for("wxpusher")
    env.persist_fill_and_enqueue()
    env.commit()
    assert env.fill_exists() is True
    assert env.delivery_channels() == ["telegram"]


def test_binding_query_failure_is_fail_closed(env):
    env.binding_repository_raises()
    env.enqueue_signal()
    assert env.delivery_channels() == []
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_outbox_channels.py tests/test_notification_wiring.py \
  tests/test_notification_outbox.py
```

Expected: FAIL，outbox 固定 Telegram 或查询异常 fail-open。

- [ ] **Step 3: 把 eligibility 改为按渠道计算**

```python
@dataclass(frozen=True)
class ChannelRoute:
    channel: str
    eligible: bool
    reason: str


def _routes(self, session: Session, owner: RuntimeOwner, event_type: NotificationType, payload: dict) -> list[ChannelRoute]:
    routes = []
    for channel in ("telegram", "wxpusher"):
        binding = self._bindings.get_active(session, owner.user_id, channel)
        if binding is None:
            routes.append(ChannelRoute(channel, False, "no_binding"))
            continue
        routes.append(self._preference_route(owner, event_type, payload, channel))
    return routes
```

实例级事件先查 instance preference，再查 channel family preference；报告查市场 + report_kind。查询异常 fail-closed 并记录白名单 reason，不创建死 delivery。

- [ ] **Step 4: event 一次写入、delivery 每渠道独立 SAVEPOINT**

外层 SAVEPOINT 保 event；每个渠道 delivery insert 再用独立 nested SAVEPOINT。单渠道 SQL 失败只丢该 delivery，不抛出污染业务事务；记录 `notification_delivery_enqueue_failed` 的 channel 和稳定错误类别，不记录 payload/recipient。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_notification_outbox_channels.py tests/test_notification_outbox.py \
  tests/test_notification_wiring.py
```

Expected: PASS；caller session 不 commit、业务 fill 可提交、另一渠道 delivery 保留。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/application/services/notification_outbox_service.py \
  tests/test_notification_outbox_channels.py tests/test_notification_outbox.py \
  tests/test_notification_wiring.py
git commit -m "feat(notification): enqueue independent channel deliveries"
```

---

### Task 11: 业务接线、晨晚报幂等键与调度门

**Files:**
- Modify: `src/zhice_os_v2/application/services/notification_routing.py:90-330`
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:1928-1982`
- Modify: `src/zhice_os_v2/scheduler_jobs.py:176-205,1227-1270`
- Modify: `src/zhice_os_v2/scheduler.py:570-616`
- Create: `tests/test_notification_routing_channels.py`
- Modify: `tests/test_notification_wiring.py`
- Modify: `tests/test_billing_data_gating.py`
- Modify: `tests/test_scheduler_jobs_registration.py`

- [ ] **Step 1: 写报告/信号/owner RED 测试**

```python

def test_wxpusher_only_report_subscriber_is_not_filtered_out(env):
    env.enable_market_instance("usr-alice", "US")
    env.bind("usr-alice", "wxpusher", "UID_A")
    env.enable_report("usr-alice", "wxpusher", "US", kind="morning")
    env.route_market_report("US", report_kind="morning", report_date=date(2026, 9, 17))
    assert env.delivery_channels("usr-alice") == ["wxpusher"]


def test_morning_and_evening_have_distinct_idempotency_keys(env):
    morning = env.route_market_report("US", "morning", date(2026, 9, 17))
    evening = env.route_market_report("US", "evening", date(2026, 9, 17))
    assert morning.event_id != evening.event_id
    assert len(morning.event_id) <= 64
    assert len(evening.event_id) <= 64


def test_same_report_kind_rerun_is_idempotent(env):
    env.route_market_report("US", "morning", date(2026, 9, 17))
    env.route_market_report("US", "morning", date(2026, 9, 17))
    assert env.event_count() == 1


def test_missing_owner_never_broadcasts_on_either_channel(env):
    env.route_ownerless_fill()
    assert env.telegram_calls == []
    assert env.wxpusher_calls == []
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_routing_channels.py tests/test_notification_wiring.py \
  tests/test_billing_data_gating.py tests/test_scheduler_jobs_registration.py
```

Expected: FAIL，报告收件人默认 Telegram 或晨晚报 key 相同。

- [ ] **Step 3: 明确 report_kind 并修复 event_id**

```python
class ReportKind(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


def report_event_id(market: str, report_date: date, report_kind: ReportKind, user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return f"mreport-{market}-{report_date:%Y%m%d}-{report_kind.value}-{digest}"
```

payload 必须含 `report_kind`；scheduler 晨报传 `morning`，晚报传 `evening`。部署当天对旧 `mreport-{market}-{date}-{uid}` key 做存在检查：若旧 key 已存在，同一用户同日新 key 不补发，避免升级后重发。

- [ ] **Step 4: 收件人按渠道求用户并集**

`report_recipients` 分别算 Telegram/WxPusher，求用户并集后每用户只 enqueue 一个 event；outbox 再按该用户的 active bindings 和 preferences 生成 delivery。订阅信号仍只向有效订阅者，payload 红线继续禁止 qty/amount/cost/PnL。

- [ ] **Step 5: 调度注册门改为全局 directed + 任一渠道可消费**

worker 在 `shadow` 或 `directed` 维持现有注册语义；WxPusher `delivery_enabled=false` 只暂停该 channel claim，不停 Telegram。禁止通过切回 broadcast 恢复 Telegram，因为这会重新引入全员业务广播。

- [ ] **Step 6: 跑 GREEN**

```bash
uv run pytest -q tests/test_notification_routing_channels.py tests/test_notification_wiring.py \
  tests/test_billing_data_gating.py tests/test_scheduler_jobs_registration.py \
  tests/test_scheduler_core.py tests/test_mirror_sim_push_isolation.py
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/zhice_os_v2/application/services/notification_routing.py \
  src/zhice_os_v2/application/services/workflow_service.py \
  src/zhice_os_v2/scheduler_jobs.py src/zhice_os_v2/scheduler.py \
  tests/test_notification_routing_channels.py tests/test_notification_wiring.py \
  tests/test_billing_data_gating.py tests/test_scheduler_jobs_registration.py
git commit -m "feat(notification): route reports across channels"
```

---

### Task 12: 企业微信运维告警隔离与 rollout 配置

**Files:**
- Create: `src/zhice_os_v2/application/services/notification_health_service.py`
- Create: `tests/test_notification_ops_alerts.py`
- Modify: `src/zhice_os_v2/application/services/alert_service.py:31-158`
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:800-1400`
- Modify: `src/zhice_os_v2/scheduler_jobs.py:176-205`
- Modify: `.env.example`
- Modify: `.env.prod.example`
- Modify: `docker-compose.prod.yml:34-112`

- [ ] **Step 1: 写白名单、去敏、阈值与关闭门 RED 测试**

```python

def test_ops_alert_drops_user_business_fields(fake_webhook):
    send_notification_ops_alert(
        kind="channel_auth_failed",
        channel="wxpusher",
        counts={"failed": 7},
        forbidden_context={
            "uid": "UID_secret",
            "chat_id": "12345678",
            "symbol": "AAPL",
            "payload": "private fill body",
        },
    )
    body = fake_webhook.last_json["text"]["content"]
    assert "UID_secret" not in body
    assert "12345678" not in body
    assert "AAPL" not in body
    assert "private fill body" not in body
    assert "channel=wxpusher" in body
    assert "failed=7" in body


def test_business_risk_and_stop_loss_do_not_use_ops_webhook(fake_webhook, workflow):
    workflow.trigger_risk_rejection()
    workflow.trigger_stop_loss()
    assert fake_webhook.calls == []


def test_disabled_wxpusher_keeps_pending_deliveries(worker_env):
    worker_env.wxpusher_enabled = False
    worker_env.seed_delivery("wxpusher")
    worker_env.worker().run_once()
    assert worker_env.delivery_status() == "PENDING"
```

- [ ] **Step 2: 运行 RED 测试**

```bash
uv run pytest -q tests/test_notification_ops_alerts.py tests/test_alerts.py \
  tests/test_notification_worker_channels.py
```

Expected: FAIL，现有企业微信路径仍接受业务正文或缺健康服务。

- [ ] **Step 3: 实现有限字段健康告警**

```python
@dataclass(frozen=True)
class NotificationHealthSnapshot:
    channel: str
    pending: int
    retry: int
    failed_last_hour: int
    oldest_due_seconds: int


_ALLOWED_OPS_KINDS = frozenset({
    "channel_auth_failed",
    "delivery_backlog",
    "worker_stalled",
    "terminal_failure_rate",
})
```

告警正文只允许 kind/channel/counts/duration/worker job ID；不得接受任意 message/payload。相同 kind+channel 使用现有 300 秒频控。普通用户风险、止损、标的价格从企业微信调用点移除，改走用户 outbox 或数据库 AlertModel。

- [ ] **Step 4: 配置透传**

`.env` 样例增加无秘密默认：

```dotenv
WXPUSHER_APP_TOKEN=
WXPUSHER_APP_ID=0
WXPUSHER_APP_KEY=
WXPUSHER_WEBHOOK_PATH_KEY=
WXPUSHER_BINDING_ENABLED=false
WXPUSHER_DELIVERY_ENABLED=false
WXPUSHER_CHALLENGE_TTL_SECONDS=600
WXPUSHER_TIMEOUT_SECONDS=8
ALERT_WEBHOOK_URL=
```

API 与 scheduler 都透传相同变量。文档明确 path key 至少 32 个随机字符，不能提交真实值。

- [ ] **Step 5: 跑 GREEN**

```bash
uv run pytest -q tests/test_notification_ops_alerts.py tests/test_alerts.py \
  tests/test_sentinel_alert_resolve.py tests/test_notification_worker_channels.py \
  tests/test_scheduler_jobs_registration.py
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/zhice_os_v2/application/services/notification_health_service.py \
  src/zhice_os_v2/application/services/alert_service.py \
  src/zhice_os_v2/application/services/workflow_service.py \
  src/zhice_os_v2/scheduler_jobs.py .env.example .env.prod.example \
  docker-compose.prod.yml tests/test_notification_ops_alerts.py
git commit -m "feat(notification): isolate operations alerts"
```

---

### Task 13: 前端渠道 API 与 DTO

**Files:**
- Modify: `frontend/src/services/notificationsApi.ts:11-97`
- Create: `frontend/src/test/notificationChannelsApi.test.ts`
- Modify: `frontend/src/test/myApis.test.ts:405-465`

- [ ] **Step 1: 写 URL、body、channel 隔离 RED 测试**

```typescript
it('loads and patches preferences for an explicit channel', async () => {
  mockFetchJson(preference('wxpusher'));
  await getMyNotificationPreferences('wxpusher');
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining('/my/notification-preferences?channel=wxpusher'),
    expect.anything(),
  );

  mockFetchJson(preference('wxpusher'));
  await patchMyNotificationPreferences('wxpusher', { signal_enabled: false });
  expect(lastRequestJson()).toEqual({ signal_enabled: false });
});

it('uses the WxPusher binding endpoints without user_id', async () => {
  mockFetchJson({ qr_image_url: 'https://img', expires_at: '2026-09-17T03:00:00+00:00' });
  await createWxPusherChallenge();
  expect(lastRequestUrl()).toEndWith('/auth/me/notification-bindings/wxpusher/challenge');
  expect(lastRequestBody()).toBeUndefined();
});
```

- [ ] **Step 2: 运行 RED 测试**

```bash
npm --prefix frontend run test -- src/test/notificationChannelsApi.test.ts src/test/myApis.test.ts
```

Expected: FAIL，新 API 函数不存在。

- [ ] **Step 3: 添加准确 DTO/API**

```typescript
export type NotificationChannel = 'telegram' | 'wxpusher';
export type BindingStatus = 'active' | 'revoked';

export interface NotificationBinding {
  channel: NotificationChannel;
  status: BindingStatus;
  recipient_masked: string | null;
  bound_at: string | null;
}

export interface WxPusherChallenge {
  qr_image_url: string;
  expires_at: string;
}

export interface NotificationPreference {
  user_id: string;
  channel: NotificationChannel;
  market_reports_enabled: Record<string, boolean>;
  signal_enabled: boolean;
  fill_enabled: boolean;
  morning_report_enabled: boolean;
  evening_report_enabled: boolean;
  quiet_hours: QuietHours | null;
  timezone: string;
  locale: 'zh' | 'en';
}
```

API 函数：

```typescript
export const getNotificationBindings = () =>
  api<NotificationBinding[]>('/auth/me/notification-bindings');
export const createWxPusherChallenge = () =>
  api<WxPusherChallenge>('/auth/me/notification-bindings/wxpusher/challenge', { method: 'POST' });
export const unbindWxPusher = () =>
  api<{ ok: boolean }>('/auth/me/notification-bindings/wxpusher', { method: 'DELETE' });
export const testWxPusher = () =>
  api<{ ok: boolean; reason?: string }>('/auth/me/notification-bindings/wxpusher/test', { method: 'POST' });
```

- [ ] **Step 4: 跑 GREEN**

```bash
npm --prefix frontend run test -- src/test/notificationChannelsApi.test.ts src/test/myApis.test.ts
npm --prefix frontend run build
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/services/notificationsApi.ts \
  frontend/src/test/notificationChannelsApi.test.ts frontend/src/test/myApis.test.ts
git commit -m "feat(frontend): add notification channel client"
```

---

### Task 14: 微信扫码绑定卡与双语文案

**Files:**
- Create: `frontend/src/components/WxPusherBind.tsx`
- Create: `frontend/src/test/WxPusherBind.test.tsx`
- Modify: `frontend/src/i18n/locales/zh/pages.json`
- Modify: `frontend/src/i18n/locales/en/pages.json`
- Modify: `frontend/src/i18n/locales/zh/errors.json`
- Modify: `frontend/src/i18n/locales/en/errors.json`

- [ ] **Step 1: 写二维码、倒计时、轮询、失效、解绑 RED 测试**

```typescript
it('shows the server QR image and never renders a challenge token', async () => {
  mockUnboundThenChallenge({
    qr_image_url: 'https://img.example/qr.png',
    expires_at: new Date(Date.now() + 600_000).toISOString(),
  });
  render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
  await user.click(await screen.findByRole('button', { name: '扫码绑定微信' }));
  expect(await screen.findByRole('img', { name: '微信绑定二维码' })).toHaveAttribute(
    'src', 'https://img.example/qr.png',
  );
  expect(screen.queryByText(/synthetic-challenge/)).not.toBeInTheDocument();
});

it('polls while active and stops immediately after binding', async () => {
  vi.useFakeTimers();
  const changed = vi.fn();
  mockBindingSequence([unboundBinding(), activeBinding('UID_***3456')]);
  render(<WxPusherBind binding={null} onBindingChanged={changed} />);
  await startChallenge();
  await vi.advanceTimersByTimeAsync(3_000);
  expect(await screen.findByText(/UID_\*\*\*3456/)).toBeInTheDocument();
  const calls = bindingStatusCalls();
  await vi.advanceTimersByTimeAsync(6_000);
  expect(bindingStatusCalls()).toBe(calls);
});

it('clears expired QR state and can regenerate', async () => {
  vi.useFakeTimers();
  render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
  await startChallengeExpiringIn(1_000);
  await vi.advanceTimersByTimeAsync(1_100);
  expect(screen.queryByRole('img', { name: '微信绑定二维码' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '重新生成二维码' })).toBeEnabled();
});

it('requires confirmation before unbinding', async () => {
  renderBoundWxPusher();
  await user.click(screen.getByRole('button', { name: '解除绑定' }));
  expect(unbindWxPusher).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '确认' }));
  expect(unbindWxPusher).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: 运行 RED 测试**

```bash
npm --prefix frontend run test -- src/test/WxPusherBind.test.tsx
```

Expected: FAIL，组件不存在。

- [ ] **Step 3: 实现状态机组件**

组件 props 固定为：

```typescript
interface WxPusherBindProps {
  binding: NotificationBinding | null;
  onBindingChanged: () => Promise<void> | void;
}
```

状态包含 `loading/unbound/active/expired/bound/revoked/error`。使用 `<img src={qr_image_url}>`，不使用 Ant Design `QRCode` 二次编码。3 秒轮询 `getNotificationBindings()`；1 秒倒计时；到期、解绑、重生成和卸载都清理 timer 与二维码 URL。临时 URL 只保存在 React state，不写 localStorage/log。

- [ ] **Step 4: 补齐中英文文案**

在 `pages.json` 独立新增顶层 `wxpusher`，不复用或覆盖 `telegram`/`userNotif`：标题、扫码说明、二维码 alt、倒计时、测试、解绑、revoked、加载失败。`errors.json` 增加 `notification.binding_required`、`notification.binding_recipient_conflict`、`wxpusher.binding_disabled` 等后端稳定 code 的中英文文本。

- [ ] **Step 5: 跑 GREEN 与 build**

```bash
npm --prefix frontend run test -- src/test/WxPusherBind.test.tsx
npm --prefix frontend run build
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/WxPusherBind.tsx \
  frontend/src/test/WxPusherBind.test.tsx \
  frontend/src/i18n/locales/zh/pages.json frontend/src/i18n/locales/en/pages.json \
  frontend/src/i18n/locales/zh/errors.json frontend/src/i18n/locales/en/errors.json
git commit -m "feat(frontend): add WxPusher scan binding"
```

---

### Task 15: 双渠道偏好矩阵与 Profile 集成

**Files:**
- Create: `frontend/src/components/NotificationChannelSettings.tsx`
- Modify: `frontend/src/components/UserNotificationPreferencesForm.tsx:23-169`
- Modify: `frontend/src/pages/ProfilePage.tsx:90-94`
- Create: `frontend/src/test/UserNotificationPreferencesForm.test.tsx`
- Create: `frontend/src/test/ProfileNotifications.test.tsx`
- Modify: `frontend/src/test/TelegramManualBind.test.tsx`

- [ ] **Step 1: 写矩阵与 Profile RED 测试**

```typescript
it('renders four event rows and two independent channel columns', async () => {
  renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
  for (const row of ['策略信号', '成交提醒', '晨报', '晚报']) {
    expect(await screen.findByRole('row', { name: new RegExp(row) })).toBeInTheDocument();
  }
  expect(screen.getByRole('columnheader', { name: '微信' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Telegram' })).toBeInTheDocument();
});

it('disables an unbound channel without changing the other channel', async () => {
  renderPreferences({ telegram: activeTelegram(), wxpusher: null });
  expect(screen.getByRole('switch', { name: '微信 策略信号' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).toBeEnabled();
});

it('saves only the changed channel', async () => {
  renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
  await user.click(screen.getByRole('switch', { name: '微信 成交提醒' }));
  expect(patchMyNotificationPreferences).toHaveBeenCalledWith(
    'wxpusher',
    expect.objectContaining({ fill_enabled: false }),
  );
  expect(patchMyNotificationPreferences).not.toHaveBeenCalledWith(
    'telegram', expect.anything()
  );
});

it('shows WeChat first and keeps Telegram as an optional overseas channel', async () => {
  render(<ProfilePage />);
  const headings = await screen.findAllByRole('heading');
  expect(indexOfHeading(headings, '微信通知')).toBeLessThan(
    indexOfHeading(headings, 'Telegram（境外可选）'),
  );
});

it('refreshes the matrix immediately after binding changes', async () => {
  render(<ProfilePage />);
  expect(await screen.findByRole('switch', { name: '微信 策略信号' })).toBeDisabled();
  simulateWxPusherBound();
  expect(await screen.findByRole('switch', { name: '微信 策略信号' })).toBeEnabled();
});
```

- [ ] **Step 2: 运行 RED 测试**

```bash
npm --prefix frontend run test -- src/test/UserNotificationPreferencesForm.test.tsx \
  src/test/ProfileNotifications.test.tsx
```

Expected: FAIL，矩阵/容器不存在。

- [ ] **Step 3: 实现页面级状态容器**

`NotificationChannelSettings` 一次加载 bindings，按 channel 传给 `WxPusherBind`、Telegram 卡和偏好矩阵；绑定/解绑成功统一调用 `refreshBindings()`。不要让三个组件各自维护互不知晓的 binding 状态。

- [ ] **Step 4: 实现四行双列矩阵**

```typescript
const EVENT_ROWS = [
  ['signal_enabled', 'userNotif.signal'],
  ['fill_enabled', 'userNotif.fill'],
  ['morning_report_enabled', 'userNotif.morningReport'],
  ['evening_report_enabled', 'userNotif.eveningReport'],
] as const;
```

每个 switch 的 accessible name 为“渠道 + 事件”。未 active 的列全部 disabled 并显示“请先绑定”。静音、时区、语言作为渠道级配置保留；市场报告 opt-in 区继续存在，并明确同时约束晨/晚报。实例级 9 键表单不改。

- [ ] **Step 5: Profile 接线并保留 Telegram 部署模式**

`ProfilePage` 以 `<NotificationChannelSettings />` 替换直接渲染的 `TelegramManualBind` 与旧 preference card。Telegram 组件根据后端 status 的配置能力决定 manual/deep-link；本次不强制未启用 deep-link 的生产环境切换方式。微信卡排第一，Telegram 标注“境外可选”。

- [ ] **Step 6: 跑前端全回归和 build**

```bash
npm --prefix frontend run test -- src/test/UserNotificationPreferencesForm.test.tsx \
  src/test/ProfileNotifications.test.tsx src/test/WxPusherBind.test.tsx \
  src/test/TelegramDeepLinkBind.test.tsx src/test/TelegramManualBind.test.tsx \
  src/test/myApis.test.ts
npm --prefix frontend run test
npm --prefix frontend run build
```

Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/NotificationChannelSettings.tsx \
  frontend/src/components/UserNotificationPreferencesForm.tsx \
  frontend/src/pages/ProfilePage.tsx \
  frontend/src/test/UserNotificationPreferencesForm.test.tsx \
  frontend/src/test/ProfileNotifications.test.tsx \
  frontend/src/test/TelegramManualBind.test.tsx
git commit -m "feat(frontend): add dual-channel notification settings"
```

---

### Task 16: E2E、CI、部署 runbook 与最终验证

**Files:**
- Create: `frontend/e2e/profile-notifications.spec.ts`
- Create: `tests/test_notification_real_pg.py`
- Modify: `.github/workflows/ci.yml:8-24`
- Create: `docs/runbooks/domestic-notifications-rollout.md`
- Test: whole repository

- [ ] **Step 1: 写浏览器 E2E**

```typescript
// frontend/e2e/profile-notifications.spec.ts
test('VIEWER binds WxPusher and saves channel preferences independently', async ({ page }) => {
  await loginAs(page, 'viewer');
  await mockNotificationBindings(page, [unbound('wxpusher'), bound('telegram', '****5678')]);
  await page.goto('/profile');

  await expect(page.getByRole('heading', { name: '微信通知' })).toBeVisible();
  await page.getByRole('button', { name: '扫码绑定微信' }).click();
  await expect(page.getByRole('img', { name: '微信绑定二维码' })).toBeVisible();

  await switchBindingMockToBound(page, 'wxpusher', 'UID_***3456');
  await expect(page.getByText('UID_***3456')).toBeVisible();
  await page.getByRole('switch', { name: '微信 策略信号' }).click();
  await expectPreferencePatch(page, 'wxpusher', { signal_enabled: false });
  await expect(page.getByRole('switch', { name: 'Telegram 策略信号' })).toBeChecked();
});


test('English UI renders all channel controls without Chinese fallback', async ({ page }) => {
  await loginAs(page, 'viewer', { locale: 'en' });
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: 'WeChat notifications' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'Telegram' })).toBeVisible();
});
```

route mock 只验证 UI 状态机；真实微信送达放生产 canary，不能用 mock 冒充。

- [ ] **Step 2: 运行 E2E RED/GREEN**

```bash
npm --prefix frontend run e2e -- profile-notifications.spec.ts
```

Expected: 首次 RED 应证明页面尚未满足新旅程；随后在该测试文件内实现 `loginAs`、`mockNotificationBindings`、`switchBindingMockToBound` 和 `expectPreferencePatch` 四个 helper（均用 `page.route`/请求监听且只处理合成数据），最终 PASS；保存 trace/screenshot 仅在失败时。

- [ ] **Step 3: 给 CI 增加前端门禁**

在现有后端 job 之外增加：

```yaml
  frontend-test-build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm test
      - run: npm run build
```

Playwright 不放普通 PR 阻塞门，除非 CI 明确安装系统 Chrome并提供登录后端；本任务保留为部署前门禁。

- [ ] **Step 4: 写分阶段 rollout/runbook**

runbook 必须给出准确顺序：

1. 备份数据库并记录当前 `alembic_version`。
2. API/scheduler 同时部署能识别 WxPusher channel 的新版本；旧 worker 全部退出。
3. `alembic upgrade 0033`，核对 Telegram 回填计数和冲突为零。
4. 配置真实 `APP_TOKEN/APP_ID/APP_KEY/PATH_KEY`，保持 `WXPUSHER_DELIVERY_ENABLED=false`。
5. 在 WxPusher 后台配置 `https://<host>/api/v1/integrations/wxpusher/webhook/<path_key>`；确认 nginx 该路径 access log 关闭。
6. 管理员 canary 扫码、测试消息、退订回调、重绑。
7. 先开 binding，再对少量用户开放 UI；观察 PENDING/RETRY/FAILED。
8. 确认 `telegram_notification_mode=directed` 且 execution mode=`enforced` 后，再启用 WxPusher delivery。
9. 回滚仅关闭 `WXPUSHER_DELIVERY_ENABLED` 和 UI 入口；保留 binding/history，不切回 broadcast。

不得把真实 secret 写入 runbook。

- [ ] **Step 5: 运行后端定向与全量门禁**

```bash
uv run pytest -q tests/test_notification_contracts.py tests/test_migration_0033.py \
  tests/test_notification_binding_repository.py tests/test_notification_preferences_channels.py \
  tests/test_notification_transports.py tests/test_wxpusher_adapter_contract.py \
  tests/test_notification_bindings_api.py tests/test_notification_worker_channels.py \
  tests/test_notification_outbox_channels.py tests/test_notification_routing_channels.py \
  tests/test_notification_ops_alerts.py
uv run pytest -q
uv run ruff check src tests
uv run mypy src/zhice_os_v2
uv run alembic heads
```

Expected:

- 所有 pytest/ruff 通过；
- mypy 不新增错误（若仍有仓库既有债，保存与基线对比）；
- Alembic 仅一个 head：`0033`。

- [ ] **Step 6: 写并运行专用 PostgreSQL 并发门禁**

`tests/test_notification_real_pg.py` 必须带 `pytestmark = pytest.mark.real_pg`，并覆盖真实 PG 的关键语义：

```python
pytestmark = pytest.mark.real_pg


def test_concurrent_challenge_creation_leaves_only_newest_consumable(pg_env):
    first, second = pg_env.create_challenges_concurrently("usr-alice", "wxpusher")
    outcomes = {
        pg_env.consume(first, "UID_A"),
        pg_env.consume(second, "UID_A"),
    }
    assert outcomes == {"bound", "expired_or_used"}
    assert pg_env.open_challenge_count("usr-alice", "wxpusher") == 0


def test_two_workers_do_not_double_send_cross_channel_deliveries(pg_env):
    pg_env.seed_delivery("evt-tg", "telegram")
    pg_env.seed_delivery("evt-wx", "wxpusher")
    pg_env.run_workers_concurrently(2)
    assert pg_env.send_count("evt-tg") == 1
    assert pg_env.send_count("evt-wx") == 1


def test_old_uid_failure_cannot_revoke_rebound_uid(pg_env):
    pg_env.bind("usr-alice", "wxpusher", "UID_OLD")
    delivery = pg_env.claim_delivery("usr-alice", "wxpusher")
    pg_env.bind("usr-alice", "wxpusher", "UID_NEW")
    pg_env.finalize_recipient_invalid(delivery, recipient_used="UID_OLD")
    assert pg_env.active_recipient("usr-alice", "wxpusher") == "UID_NEW"
```

不要对共享开发库或生产库运行。设置专用 `DB_MAIN_URL` 后：

```bash
REAL_PG=1 uv run pytest -q -m real_pg \
  tests/test_notification_real_pg.py tests/test_notification_worker.py
```

Expected: 用户行锁、SKIP LOCKED、唯一约束、条件撤销和 epoch CAS 全部 PASS；fixture 最终清理本测试创建的 `test-notification-*` 行，无残留业务数据。

- [ ] **Step 7: 运行前端门禁与敏感信息扫描**

```bash
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run e2e -- profile-notifications.spec.ts

grep -R -nE 'AT_[A-Za-z0-9]{8,}|AK_[A-Za-z0-9]{8,}|UID_[A-Za-z0-9]{8,}' \
  src frontend/src tests .env.example .env.prod.example || true
grep -R -nE 'logger\..*(recipient_id|chat_id|appToken|app_key|extraCode|payload)' \
  src/zhice_os_v2 || true
git diff --check
```

Expected: 测试/build/E2E 通过；扫描仅命中明确的 synthetic fixture 或无命中，不出现真实凭据和敏感日志。

- [ ] **Step 8: 提交最终门禁和 runbook**

```bash
git add frontend/e2e/profile-notifications.spec.ts tests/test_notification_real_pg.py \
  .github/workflows/ci.yml docs/runbooks/domestic-notifications-rollout.md
git commit -m "test(notification): add dual-channel rollout gates"
```

---

## 3. 最终人工 canary（仅获得生产部署授权后执行）

这些步骤有外部副作用，实施代理不得仅凭本计划自动执行：

1. 在 `159.75.220.212` 只读确认 WxPusher、企业微信仍可达。
2. 创建 WxPusher 测试应用或使用获授权应用，配置 webhook。
3. 管理员账号生成二维码，微信扫码后确认自动绑定。
4. 发送测试消息，核对微信真实到达和 delivery=SENT。
5. 同时启用 Telegram/WxPusher，触发合成业务事件，确认生成两条独立 delivery。
6. 暂停 WxPusher，确认其 delivery 保持 PENDING，Telegram 继续发送。
7. 取消关注，确认 `app_unsubscribe` 仅 revoke 当前 UID，不影响 Telegram。
8. 触发合成运维故障，确认企业微信群只收到脱敏计数，不出现 UID、chat ID、标的、价格或业务正文。
9. 检查 nginx/API/scheduler 日志，无 path key、appToken、appKey、完整 UID、challenge。

## 4. 完成定义

只有同时满足以下条件才可宣布完成：

- 0033 升降级、回填、冲突中止在 SQLite migration harness 和专用 PostgreSQL 验证通过。
- Telegram 既有绑定和投递无需用户重绑，全部回归通过。
- WxPusher HTTP 200 业务失败不会误记 SENT。
- 微信-only、Telegram-only、双渠道用户均按偏好正确建 delivery。
- 单渠道 SQL/HTTP 失败不影响另一渠道，也不污染成交事务。
- 晨报/晚报同日可各投一次，同类重跑幂等。
- VIEWER 可管理本人通知，但其他写权限仍被拒绝。
- 前端中英文、倒计时、轮询清理、解绑确认、矩阵联动均通过。
- 企业微信不再接收普通用户业务正文。
- 默认部署仍关闭 WxPusher delivery；回滚不切回 broadcast。
- 所有测试、ruff、前端 build、`git diff --check` 通过；mypy 不新增错误。
