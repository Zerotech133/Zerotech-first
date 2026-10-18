"""多用户 E2E 旅程（Phase D Task 11）：httpx 直打本机真实栈 API 8102。

默认被 pyproject addopts（-m 'not real_pg and not e2e'）整体 deselect，须显式运行：

    uv run pytest tests/e2e/multi_user_journey.py -m e2e -v

边界（与 tests/conftest.py 的内存后端守卫互不干扰）：
- **绝不 import zhice_os_v2**（conftest 顶层把 PERSISTENCE_BACKEND 钉成 memory、
  ADMIN_PASSWORD 置空，且 _guard_real_pg 会拦 get_session——本文件纯 httpx HTTP
  调用 + 唯一一处 psycopg2 只读 SELECT，均不经 zhice 代码路径）。
- 合成账号：随机后缀用户名（e2e_a_<hex>/e2e_b_<hex>），跑完**不清理**（dev 库
  测试号残留可接受；用户名前缀 e2e_ 便于事后人工识别）。
- admin 凭据：优先 E2E_ADMIN_PASSWORD 环境变量；缺省回落 123456（src config
  本地 dev bootstrap 默认值，非机密）。注意 conftest 把 ADMIN_PASSWORD 置空，
  故不读该键。
- 审计落库校验：psycopg2 只读 SELECT v2_access_audit（连接串取 E2E_DB_URL，
  缺省 zhice 本地 dev 库）；连不上/无驱动则该断言 skip（其余旅程不受影响）。

覆盖（对应计划 Task 11 Step 1 的 API 侧子集；模式相关 owned 成交/定向通知
已由后端 853 测试覆盖，本机 legacy+broadcast 模式不切）：
1. 公开注册 → 默认 USER + 注册即登录 token。
2. /my 全家空态一致性（strategies/accounts/strategy-instances/plans/positions/
   replays/approvals/notification-preferences）。
3. A/B 订阅同一公共策略 → 各自独立实例 + NEW_DEDICATED 独立账户，互不可见。
4. A 建私有策略 → B 目录不可见、B 直改/版本化 A 私有策略 404（不泄露存在性）。
5. A 申请发布 → request PENDING + 策略 publication_status=PENDING_REVIEW。
6. 用户级通知偏好读改回读（市场开关 toggle）。
7. 升级端点冒烟：target=当前版本 → 400 user_strategy.upgrade_same_version。
8. admin 监督面 /admin/* 可见 A/B 资源 + v2_access_audit 落库（LIST_GLOBAL）。
"""
from __future__ import annotations

import os
import random
import string
from collections.abc import Iterator

import httpx
import pytest

pytestmark = [pytest.mark.e2e]

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8102/api/v1").rstrip("/")
# 本地 dev bootstrap 默认口令（src/zhice_os_v2/config/settings.py admin_password
# 默认值）；有独立口令时经 E2E_ADMIN_PASSWORD 覆盖，绝不写入提交文件。
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD") or "123456"
E2E_DB_URL = os.environ.get(
    "E2E_DB_URL", "postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2"
)


def _suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


class UserCtx:
    """一个已注册（即已登录）用户的最小上下文。"""

    def __init__(self, client: httpx.Client, reg: dict) -> None:
        self.client = client
        self.token: str = reg["token"]
        self.user_id: str = reg["user_id"]
        self.username: str = reg["username"]
        self.role: str = reg["role"]

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _register(client: httpx.Client, username: str) -> UserCtx:
    resp = client.post(
        "/auth/register",
        json={
            "username": username,
            # RFC 保留域 example.com：email-validator 接受（不查 DNS），合成值可重复跑。
            "email": f"{username}@e2e.example.com",
            "password": "e2e-Pass-123456",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["token"], "注册即登录应返回 token"
    assert data["role"] == "USER", f"公开注册默认 USER，防提权；got {data['role']}"
    return UserCtx(client, data)


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
def users(client: httpx.Client) -> dict[str, UserCtx]:
    """每轮运行创建两个随机后缀 USER；残留即记录（不清理）。"""
    run = _suffix(6)
    a = _register(client, f"e2e_a_{run}{_suffix()}")
    b = _register(client, f"e2e_b_{run}{_suffix()}")
    return {"a": a, "b": b}


def _pick_public_strategy_version(client: httpx.Client, token_headers: dict) -> tuple[str, str]:
    """从公共目录取一个可运行版本的策略（ACTIVE/STAGING/PUBLISHED 版本可建实例）。"""
    resp = client.get("/strategies", headers=token_headers)
    assert resp.status_code == 200, resp.text
    for row in resp.json():
        versions = row.get("versions") or []
        runnable = [
            v for v in versions if v.get("status") in ("ACTIVE", "STAGING", "PUBLISHED")
        ]
        if runnable:
            return row["strategy_id"], runnable[-1]["strategy_version_id"]
    pytest.skip("公共目录无可运行版本的策略（目录为空）——订阅旅程无法执行")
    raise AssertionError("unreachable")


# ---------- 旅程 1：注册 + /auth/me ----------


def test_register_default_role_and_me(client: httpx.Client, users: dict[str, UserCtx]) -> None:
    a = users["a"]
    resp = client.get("/auth/me", headers=a.auth)
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["user_id"] == a.user_id
    assert me["role"] == "USER"
    assert me["email"] == f"{a.username}@e2e.example.com"


# ---------- 旅程 2：/my 全家空态一致性 ----------


def test_my_namespace_empty_state(client: httpx.Client, users: dict[str, UserCtx]) -> None:
    b = users["b"]
    for ep in (
        "/my/accounts",
        "/my/strategy-instances",
        "/my/plans",
        "/my/positions",
        "/my/replays",
        "/my/approvals",
    ):
        resp = client.get(ep, headers=b.auth)
        assert resp.status_code == 200, f"{ep}: {resp.status_code} {resp.text}"
        assert resp.json() == [], f"新用户 {ep} 应为空，got {resp.json()[:3]}"

    # strategies 目录 = 公共 + 本人私有：新用户只见公共，不得出现他人私有。
    resp = client.get("/my/strategies", headers=b.auth)
    assert resp.status_code == 200, resp.text
    assert all(
        r.get("visibility") in (None, "PUBLIC") or r.get("owner_user_id") == b.user_id
        for r in resp.json()
    ), "目录不得出现他人私有策略"

    # 通知偏好：幂等 get-or-create。
    resp = client.get("/my/notification-preferences", headers=b.auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == b.user_id


# ---------- 旅程 3：A/B 同一公共策略 → 独立实例 + 独立账户 ----------


def test_subscribe_same_public_strategy_isolated(
    client: httpx.Client, users: dict[str, UserCtx]
) -> None:
    a, b = users["a"], users["b"]
    _sid, version_id = _pick_public_strategy_version(client, a.auth)
    made: dict[str, dict] = {}
    for who in (a, b):
        resp = client.post(
            "/my/strategy-instances",
            headers=who.auth,
            json={
                "strategy_version_id": version_id,
                # 不传 account_id/account_mode → NEW_DEDICATED 独立子账户（向导默认）。
                "initial_capital": 10000,
                "execution_mode": "MANUAL",
            },
        )
        assert resp.status_code == 200, resp.text
        inst = resp.json()
        assert inst["enabled"] is False, "create 恒建 disabled（安全默认），enable 是独立事务"
        resp = client.patch(
            f"/my/strategy-instances/{inst['user_strategy_id']}/enable", headers=who.auth
        )
        assert resp.status_code == 200, resp.text
        made[who.user_id] = resp.json()
        assert made[who.user_id]["enabled"] is True

    ia, ib = made[a.user_id], made[b.user_id]
    assert ia["user_strategy_id"] != ib["user_strategy_id"]
    assert ia["account_id"] != ib["account_id"], "NEW_DEDICATED 应各建独立账户"

    # 各自列表只含本人实例；对方实例 id 不可见。
    for who, own, other in ((a, ia, ib), (b, ib, ia)):
        resp = client.get("/my/strategy-instances", headers=who.auth)
        assert resp.status_code == 200, resp.text
        ids = [r["user_strategy_id"] for r in resp.json()]
        assert own["user_strategy_id"] in ids
        assert other["user_strategy_id"] not in ids, "他人实例不得出现在本人列表"

    # 独立账户各归各人（旅程 2 已证空态，此处必为本次新建）。
    for who, own, other in ((a, ia, ib), (b, ib, ia)):
        resp = client.get("/my/accounts", headers=who.auth)
        assert resp.status_code == 200, resp.text
        acc_ids = [r["account_id"] for r in resp.json()]
        assert own["account_id"] in acc_ids
        assert other["account_id"] not in acc_ids, "他人账户不得出现在本人账户列表"

    # B 直改 A 实例 → 404（不泄露存在性）。
    resp = client.patch(
        f"/my/strategy-instances/{ia['user_strategy_id']}",
        headers=b.auth,
        json={"auto_execute": True},
    )
    assert resp.status_code == 404, f"B 改 A 实例应 404，got {resp.status_code}"

    # 记录给后续旅程（module 级同一批用户）。
    users["_instance_a"] = ia  # type: ignore[assignment]


# ---------- 旅程 4：A 私有策略 → B 不可见/404 ----------


def test_private_strategy_isolation(
    client: httpx.Client, users: dict[str, UserCtx]
) -> None:
    a, b = users["a"], users["b"]
    resp = client.post(
        "/my/strategies",
        headers=a.auth,
        json={"strategy_name": f"[E2E] 私有策略 {a.username}", "market": "CN"},
    )
    assert resp.status_code == 200, resp.text
    s = resp.json()
    assert s["visibility"] == "PRIVATE"
    assert s["owner_user_id"] == a.user_id
    sid = s["strategy_id"]

    # A 自己目录可见（PRIVATE 行）。
    resp = client.get("/my/strategies", headers=a.auth)
    rows_a = {r["strategy_id"]: r for r in resp.json()}
    assert sid in rows_a and rows_a[sid]["visibility"] == "PRIVATE"

    # B 目录不可见。
    resp = client.get("/my/strategies", headers=b.auth)
    assert sid not in {r["strategy_id"] for r in resp.json()}, "B 不得看到 A 私有策略"

    # B 直连 A 私有资源：改元信息 / 新建版本 / 申请发布 → 一律 404（不泄露存在性）。
    # （versions 须带合法 spec——端点先做 spec 校验（400）再做 owner 边界（404），
    # 用合法 spec 才能真正走到归属校验。）
    minimal_spec = {
        "spec_version": 1,
        "market": "CN",
        "universe": {"type": "manual", "symbols": ["600519.SH"]},
        "timeframe": "1d",
    }
    resp = client.put(f"/my/strategies/{sid}", headers=b.auth, json={"name": "hijack"})
    assert resp.status_code == 404, resp.text
    resp = client.post(
        f"/my/strategies/{sid}/versions", headers=b.auth, json={"spec": minimal_spec}
    )
    assert resp.status_code == 404, resp.text
    resp = client.post(f"/my/strategies/{sid}/publish", headers=b.auth)
    assert resp.status_code == 404, resp.text
    users["_private_sid"] = sid  # type: ignore[assignment]


# ---------- 旅程 5：A 申请发布 → PENDING ----------


def test_request_publication_pending(client: httpx.Client, users: dict[str, UserCtx]) -> None:
    a = users["a"]
    sid = users.get("_private_sid")
    if not sid:
        pytest.skip("前置旅程 4 未产出私有策略")
    resp = client.post(f"/my/strategies/{sid}/publish", headers=a.auth)
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert req["source_strategy_id"] == sid
    assert req["status"] == "PENDING"

    # 策略元数据翻到 PENDING_REVIEW（mine 页禁用再次申请按钮）。
    resp = client.get("/my/strategies", headers=a.auth)
    row = next(r for r in resp.json() if r["strategy_id"] == sid)
    assert row["publication_status"] == "PENDING_REVIEW"

    # 重复申请 → 409。
    resp = client.post(f"/my/strategies/{sid}/publish", headers=a.auth)
    assert resp.status_code == 409, resp.text


# ---------- 旅程 6：用户级通知偏好 toggle 回读 ----------


def test_notification_preference_roundtrip(
    client: httpx.Client, users: dict[str, UserCtx]
) -> None:
    a = users["a"]
    resp = client.get("/my/notification-preferences", headers=a.auth)
    assert resp.status_code == 200, resp.text
    before = resp.json()

    # 打开 CN 市场报告（全量集合提交，后端按键合并——与 Profile 表单同口径）。
    markets = dict(before.get("market_reports_enabled") or {})
    markets["CN"] = True
    resp = client.patch(
        "/my/notification-preferences",
        headers=a.auth,
        json={"market_reports_enabled": markets},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["market_reports_enabled"]["CN"] is True

    # 回读持久化（刷新回显语义）。
    resp = client.get("/my/notification-preferences", headers=a.auth)
    after = resp.json()
    assert after["market_reports_enabled"].get("CN") is True
    assert after["user_id"] == a.user_id

    # 复原到进入前状态，减少偏好面残留（账号本身仍保留）。
    resp = client.patch(
        "/my/notification-preferences",
        headers=a.auth,
        json={"market_reports_enabled": before.get("market_reports_enabled") or {}},
    )
    assert resp.status_code == 200, resp.text


# ---------- 旅程 7：升级端点冒烟（无升级目标 → 400） ----------


def test_upgrade_same_version_400(client: httpx.Client, users: dict[str, UserCtx]) -> None:
    a = users["a"]
    inst = users.get("_instance_a")
    if not inst:
        pytest.skip("前置旅程 3 未产出实例（公共目录为空）")
    resp = client.patch(
        f"/my/strategy-instances/{inst['user_strategy_id']}/upgrade",
        headers=a.auth,
        json={"target_version_id": inst["strategy_version_id"]},
    )
    assert resp.status_code == 400, resp.text
    assert "same_version" in str(resp.json().get("detail", "")), resp.text


# ---------- 旅程 8：admin 监督面 + 审计落库 ----------


def _admin_login(client: httpx.Client) -> UserCtx:
    resp = client.post(
        "/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, f"admin 登录失败（设 E2E_ADMIN_PASSWORD）：{resp.text}"
    data = resp.json()
    return UserCtx(client, {**data, "username": "admin"})


def test_admin_oversight_and_audit(client: httpx.Client, users: dict[str, UserCtx]) -> None:
    a, b = users["a"], users["b"]
    admin = _admin_login(client)
    assert admin.role == "ADMIN"

    inst = users.get("_instance_a")
    resp = client.get("/admin/strategy-instances", headers=admin.auth)
    assert resp.status_code == 200, resp.text
    if inst:
        all_ids = {r["user_strategy_id"] for r in resp.json()}
        assert inst["user_strategy_id"] in all_ids, "admin 全局面应可见用户实例"
    resp = client.get("/admin/accounts", headers=admin.auth)
    assert resp.status_code == 200, resp.text
    resp = client.get("/admin/strategies", headers=admin.auth)
    assert resp.status_code == 200, resp.text
    if "_private_sid" in users:
        assert users["_private_sid"] in {
            r["strategy_id"] for r in resp.json()
        }, "admin 全局面含所有用户私有策略"

    # USER 不得进监督面。
    resp = client.get("/admin/strategy-instances", headers=a.auth)
    assert resp.status_code == 403, f"USER 访问 /admin 应 403，got {resp.status_code}"
    resp = client.get("/admin/accounts", headers=b.auth)
    assert resp.status_code == 403, resp.text

    # 审计落库：只读 SELECT v2_access_audit（record_access fail-open，此处断言确实落库）。
    psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 不可用，审计落库断言跳过")
    try:
        conn = psycopg2.connect(E2E_DB_URL)
    except Exception as exc:  # noqa: BLE001 - 连不上本地 dev 库则降级 skip
        pytest.skip(f"连不上本地 PG（{exc}），审计落库断言跳过")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM v2_access_audit "
                "WHERE actor_user_id = %s AND action = 'LIST_GLOBAL'",
                (admin.user_id,),
            )
            count = cur.fetchone()[0]
    finally:
        conn.rollback()  # 只读，绝不留事务
        conn.close()
    assert count >= 3, f"三次 /admin 查看应各落一条 LIST_GLOBAL 审计，got {count}"
