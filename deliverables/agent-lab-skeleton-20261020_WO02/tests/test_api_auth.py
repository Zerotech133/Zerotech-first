"""工位CC2（WO-S12-03）：汇报总线 HTTP 身份层鉴权测试。

守的是一条红线：**默认关闭、启用即强制（closed on failure）**。
`roles/message_bus.py` 的 `MessageBus(seat_credentials=...)` 用一个事实
（凭证表是否为 None）表达开关：

    未配置（None）→ 零校验，四条路由行为与加鉴权前逐字节一致；
    已配置（任意 dict，含 {}）→ /route /status /board 强制校验，失败 401。

覆盖范围（逐条对应用工单核验清单）：
- None 与 {} 的语义**必须真的相反**（不是注释写了就算）；
- 启用后受保护路由不存在「缺头放过」分支：缺头 / 席位名错 / token 错 /
  空串 / 前后空白 / header 大小写 / 路径归一化（尾斜杠、穿越、查询串）
  逐一验证，失败一律 401；
- /health 启用后仍免验（含尾斜杠），且返回结构不变；
- token 比较经 hmac.compare_digest（不是 ==）：用 spy 证明判定结果确实由它
  产出，且比较入参是编码后的 bytes（非 ASCII token 也能走通）；
- 401 与既有 `200 + {"ok": false}` 业务拒绝通道**不混用**：鉴权失败不进
  死信、不写账本，业务拒绝在有凭证时仍是 200+ok=false；
- 401 响应体只含 error_code + 固定文案，不含 token 明文/前缀/长度；
- 空 token 配置（""/'   '/空席位名）不得成为「空 token 即可通过」的绕过路径；
- 既有成功返回结构未被改变：/board 信封 {"contract_version","points"} 与行级
  键集、/route 的 200 响应键、/status 的 200 响应键、/health 的四字段，
  均与未启用时逐键（必要时逐值）相等。

约定（与 S10/S11 判例一致）
- 全部经 fastapi.testclient.TestClient 走 ASGI 进程内调用：不起真实端口、
  不 monkeypatch 全局 socket（Windows 上 asyncio 自管道也用 socketpair，
  打桩会误伤 TestClient 自身）；
- 请求数据、席位名、凭证全部为**模拟数据**（mock-* / 占位中文），
  不代表真实席位与真实口令；
- 账本与死信路径全部经 tmp_path 注入，绝不写骨架内真实 data/；
- MessageBus 不是 BaseAgent 子类，不触碰 prometheus 全局注册表，无需清 REGISTRY。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import roles.message_bus as message_bus
from roles.message_bus import (
    AUTH_ERR_DETAILS,
    AUTH_ERR_SEAT_MISSING,
    AUTH_ERR_SEAT_UNKNOWN,
    AUTH_ERR_TOKEN_INVALID,
    AUTH_ERR_TOKEN_MISSING,
    AUTH_EXEMPT_PATHS,
    AUTH_HEADER_SEAT,
    AUTH_HEADER_TOKEN,
    BOARD_CONTRACT_VERSION,
    STATUS_POINT_FIELDS,
    MessageBus,
    create_app,
)

# ---------------- 模拟数据（非真实席位/口令） ----------------

MOCK_SEAT = "tian_feng"          # 模拟席位名
MOCK_TOKEN = "sk-mock-8f3a1c9d"  # 模拟口令
MOCK_PEER_SEAT = "zhuge_liang"   # 模拟另一个席位（未登记到凭证表）
MOCK_SOURCE = "agent1"           # 模拟消息来源席位

# 未过期基准时间（模拟），用于 /board 行级 stale 判定
MOCK_TS = 1_750_000_000.0

ROUTE_OK_PAYLOAD: Dict[str, Any] = {
    "expected_target": "",
    "report": {"source": MOCK_SOURCE, "level": "platform",
               "subject": "模拟汇报", "body": "mock"},
}
STATUS_OK_PAYLOAD: Dict[str, Any] = {
    "agent": MOCK_SOURCE, "platform": "quant", "doing": "模拟回测",
    "progress": "50%", "artifact": "mock.csv", "ts": MOCK_TS,
}

# 受保护路由 → (HTTP 方法, 请求体)。/board 为 GET。
PROTECTED_ROUTES: Dict[str, tuple] = {
    "/route": ("post", ROUTE_OK_PAYLOAD),
    "/status": ("post", STATUS_OK_PAYLOAD),
    "/board": ("get", None),
}
PROTECTED_PATHS = sorted(PROTECTED_ROUTES)


# ---------------- 夹具与工具 ----------------

def make_bus(tmp_path: Path, credentials: Optional[Dict[str, str]] = None,
             tag: str = "") -> MessageBus:
    """构造总线：账本/死信全部落 tmp_path（tag 用于同用例内多实例隔离）。"""
    prefix = f"bus_{tag}_" if tag else "bus_"
    return MessageBus(
        ledger_path=tmp_path / f"{prefix}ledger.jsonl",
        dlq_path=tmp_path / f"{prefix}dlq.jsonl",
        seat_credentials=credentials,
    )


def client_for(bus: MessageBus) -> TestClient:
    """进程内 ASGI 客户端：不起端口、不发真实 HTTP。"""
    return TestClient(create_app(bus))


def mock_creds(seat: str = MOCK_SEAT, token: str = MOCK_TOKEN) -> Dict[str, str]:
    return {AUTH_HEADER_SEAT: seat, AUTH_HEADER_TOKEN: token}


def hit(client: TestClient, path: str,
        headers: Optional[Dict[str, str]] = None):
    """按路由表发起请求（headers=None 时完全不带身份头）。"""
    method, payload = PROTECTED_ROUTES[path]
    if method == "post":
        return client.post(path, json=payload, headers=headers)
    return client.get(path, headers=headers)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def open_bus(tmp_path: Path, tag: str = "open") -> MessageBus:
    """未配置凭证（None）的总线：鉴权关闭，并注册消息来源席位。"""
    bus = make_bus(tmp_path, None, tag=tag)
    bus.register(MOCK_SOURCE, "agent")
    return bus


def enabled_bus(tmp_path: Path, tag: str = "") -> MessageBus:
    """启用鉴权的总线：登记了 MOCK_SEAT，并注册了消息来源席位。"""
    bus = make_bus(tmp_path, {MOCK_SEAT: MOCK_TOKEN}, tag=tag)
    bus.register(MOCK_SOURCE, "agent")
    return bus


# ---------------- 一、未配置（None）：零校验，既有行为不变 ----------------

def test_none_credentials_means_auth_disabled(tmp_path):
    """None → auth_enabled is False（开关唯一事实来源是凭证表是否为 None）。"""
    bus = make_bus(tmp_path, None)
    assert bus.auth_enabled is False
    # 净化后的凭证表为空，但开关状态不依赖它
    assert bus._seat_credentials == {}


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_none_credentials_all_protected_routes_open(tmp_path, path):
    """未配置：三条受保护路由不带任何身份头均可正常访问（200）。"""
    resp = hit(client_for(open_bus(tmp_path)), path)
    assert resp.status_code == 200, f"{path} 在未配置鉴权时不应被拦：{resp.text}"


def test_none_credentials_health_still_works(tmp_path):
    """未配置：/health 结构不变（四字段，无鉴权相关字段）。"""
    body = client_for(open_bus(tmp_path)).get("/health").json()
    assert set(body) == {"ok", "service", "ledger", "dlq"}
    assert body["ok"] is True and body["service"] == "message_bus"


def test_none_credentials_board_envelope_exact_keys(tmp_path):
    """未配置：/board 信封逐键断言（contract_version/points，不多不少）。"""
    bus = open_bus(tmp_path)
    bus.collect_status_point({
        "agent": MOCK_SOURCE, "platform": "quant", "doing": "模拟回测",
        "progress": "50%", "artifact": "mock.csv", "ts": time.time(),
    })
    payload = client_for(bus).get("/board").json()

    assert set(payload) == {"contract_version", "points"}
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION == "v1"
    assert isinstance(payload["points"], list) and len(payload["points"]) == 1
    row = payload["points"][0]
    # 行级键集精确相等（既有六字段 + stale 增量，零增删）
    assert set(row) == set(STATUS_POINT_FIELDS) | {"stale"}
    assert row["agent"] == MOCK_SOURCE and row["doing"] == "模拟回测"
    assert row["stale"] is False and "stale_reason" not in row


def test_none_credentials_ignores_bogus_identity_headers(tmp_path):
    """未配置：即使带了错误的身份头也照旧放行（证明真的零校验，不是"碰巧通过"）。"""
    client = client_for(open_bus(tmp_path))
    bogus = mock_creds(seat="ghost_seat", token="whatever")
    for path in PROTECTED_PATHS:
        assert hit(client, path, headers=bogus).status_code == 200


# ---------------- 二、配置后：受保护路由强制校验，全部 401 ----------------

@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_enabled_missing_headers_rejected_401(tmp_path, path):
    """启用后：三条受保护路由缺头 → 401 + 缺席位头原因码（无"缺头放过"）。"""
    resp = hit(client_for(enabled_bus(tmp_path)), path)

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_SEAT_MISSING


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_enabled_unknown_seat_rejected_with_seat_unknown_code(tmp_path, path):
    """席位名未登记 → 401，原因码是 SEAT_UNKNOWN（**不是** TOKEN_INVALID）。

    先判存在性再比 token：审计才能把「冒充陌生席位」与「凭证填错」分开聚合。
    """
    client = client_for(enabled_bus(tmp_path))
    resp = hit(client, path, headers=mock_creds(seat=MOCK_PEER_SEAT))

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_SEAT_UNKNOWN
    assert resp.json()["error_code"] != AUTH_ERR_TOKEN_INVALID


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_enabled_registered_seat_without_token_header(tmp_path, path):
    """已登记席位但缺 token 头 → 401 + TOKEN_MISSING（与"token 错"可区分）。"""
    client = client_for(enabled_bus(tmp_path))
    resp = hit(client, path, headers={AUTH_HEADER_SEAT: MOCK_SEAT})

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_TOKEN_MISSING


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_enabled_wrong_token_rejected_with_token_invalid_code(tmp_path, path):
    """token 不一致 → 401 + TOKEN_INVALID（与缺头 TOKEN_MISSING 可区分）。"""
    client = client_for(enabled_bus(tmp_path))
    resp = hit(client, path, headers=mock_creds(token="sk-mock-wrong-000"))

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_TOKEN_INVALID


def test_enabled_empty_presented_token_is_missing_not_empty_pass(tmp_path):
    """已登记席位 + 空串 token 头 → TOKEN_MISSING（空串不等于"空 token 通过"）。"""
    client = client_for(enabled_bus(tmp_path))
    resp = hit(client, "/board", headers=mock_creds(token=""))

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_TOKEN_MISSING


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_enabled_correct_credentials_pass(tmp_path, path):
    """两个头都正确 → 200（启用只是加了闸门，不改变业务可用性）。"""
    resp = hit(client_for(enabled_bus(tmp_path)), path, headers=mock_creds())
    assert resp.status_code == 200, f"{path} 凭证正确却被拦：{resp.text}"


def test_auth_check_exception_is_fail_closed(tmp_path, monkeypatch):
    """校验过程本身抛异常时，请求不得被放行：fail-closed（500，而非 200）。"""
    bus = enabled_bus(tmp_path)

    def boom(*args: Any, **kwargs: Any) -> Optional[str]:
        raise RuntimeError("模拟：校验内部异常（不得回显细节）")

    monkeypatch.setattr(bus, "check_seat_credential", boom)
    client = TestClient(create_app(bus), raise_server_exceptions=False)

    resp = client.get("/board")
    assert resp.status_code == 500, "校验异常被当成放行，属于 fail-open"
    assert resp.text == "Internal Server Error"
    assert "模拟" not in resp.text


def test_route_added_after_factory_is_protected_by_default(tmp_path):
    """工厂之后新增的路由天然落在受保护侧（中间件方案的核心收益）。

    若改用逐路由 Depends，新路由漏写依赖就是一条 fail-open 的路——
    这里把"新路由默认受保护"钉成用例，防止后续波次改回依赖注入写法。
    """
    app = create_app(enabled_bus(tmp_path))

    @app.get("/new_read_route")
    def new_read_route() -> Dict[str, Any]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/new_read_route").status_code == 401
    assert client.get("/new_read_route",
                      headers=mock_creds()).status_code == 200


# ---------------- 三、None 与 {} 的语义必须相反 ----------------

def test_empty_dict_credentials_enabled_but_nothing_allowed(tmp_path):
    """{} 空表 = 已配置但无人可放行：受保护路由全部 401（不是"关闭"）。

    这是本工单最容易写错的一条：若把 `{}` 当成 falsy 一并关闭鉴权，
    "配错成空表"就会静默退化成无鉴权（fail-open）。
    """
    bus = make_bus(tmp_path, {})
    assert bus.auth_enabled is True and bus._seat_credentials == {}
    client = client_for(bus)

    for path in PROTECTED_PATHS:
        # 不带任何头
        assert hit(client, path).status_code == 401
        # 带看似合理的头也必须 401：无任何席位登记 => 一律未登记
        denied = hit(client, path, headers=mock_creds())
        assert denied.status_code == 401
        assert denied.json()["error_code"] == AUTH_ERR_SEAT_UNKNOWN


def test_none_vs_empty_dict_same_request_opposite_result(tmp_path):
    """同一请求（无身份头）：None → 200，{} → 401，直接对照两种语义。

    这证明开关确实是"凭证表是否为 None"这一个事实，而不是"表是否为空"。
    """
    open_bus_inst = open_bus(tmp_path)
    closed_bus = make_bus(tmp_path, {}, tag="closed")

    assert open_bus_inst.auth_enabled is False and closed_bus.auth_enabled is True
    for path in PROTECTED_PATHS:
        assert hit(client_for(open_bus_inst), path).status_code == 200
        assert hit(client_for(closed_bus), path).status_code == 401


# ---------------- 四、坏配置只会更严：空/空白 token 不得成为绕过路径 ----------------

@pytest.mark.parametrize("bad_config,reason", [
    ({MOCK_SEAT: ""}, "空串 token"),
    ({MOCK_SEAT: "   "}, "纯空白 token"),
    ({"": MOCK_TOKEN}, "空席位名"),
    ({"   ": MOCK_TOKEN}, "纯空白席位名"),
])
def test_bad_credential_entries_dropped_never_fail_open(tmp_path, bad_config, reason):
    """坏凭证条目被剔除（等价于未登记），鉴权仍启用 → 请求一律 401。

    空 token 一旦进表，"空 token 即可通过"就成了绕过路径（只需猜中席位名）。
    剔除后该席位变成"未登记"，失败即拒绝。
    """
    bus = make_bus(tmp_path, bad_config)
    assert bus.auth_enabled is True          # 仍是启用态，未退化成关闭
    assert bus._seat_credentials == {}       # 坏条目未进表
    client = client_for(bus)

    # 用"配置里那个席位名 + 空 token"尝试绕过：必须失败
    seat_name = next(iter(bad_config))
    for token in ("", "   ", MOCK_TOKEN):
        resp = hit(client, "/board", headers=mock_creds(seat=seat_name, token=token))
        assert resp.status_code == 401, f"{reason} 造成了绕过：token={token!r}"
    assert hit(client, "/status").status_code == 401


def test_bad_config_does_not_disable_other_seats(tmp_path):
    """单席位配坏只降级该席位，不拖垮整条总线（其余席位仍可正常鉴权）。"""
    bus = make_bus(tmp_path, {MOCK_SEAT: MOCK_TOKEN, "broken_seat": ""})
    client = client_for(bus)

    assert hit(client, "/board", headers=mock_creds()).status_code == 200
    assert hit(client, "/board",
               headers=mock_creds(seat="broken_seat", token="")).status_code == 401


# ---------------- 五、/health 免验，返回结构不变 ----------------

@pytest.mark.parametrize("path", ["/health", "/health/"])
def test_health_exempt_when_auth_enabled(tmp_path, path):
    """/health（含尾斜杠）启用后仍无 header 可访问，且结构与未启用时逐键相同。"""
    auth_bus = enabled_bus(tmp_path, tag="auth")
    open_bus_inst = open_bus(tmp_path)
    auth_client = client_for(auth_bus)

    resp = auth_client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"ok", "service", "ledger", "dlq"}
    assert body["ok"] is True and body["service"] == "message_bus"
    assert body["ledger"] == str(auth_bus.ledger_path)
    assert body["dlq"] == str(auth_bus.dlq_path)

    # 与未启用时的返回结构逐键一致（仅路径取值随实例不同）
    open_body = client_for(open_bus_inst).get("/health").json()
    assert set(body) == set(open_body)
    # 免验白名单只有 /health，没有别的路径被顺带放行
    assert AUTH_EXEMPT_PATHS == frozenset({"/health"})


# ---------------- 六、401 不得泄露 token，也不得混入业务拒绝通道 ----------------

def test_401_body_never_leaks_token(tmp_path):
    """401 响应体只含 error_code + 固定文案：无 token 明文/前缀/后缀/长度。

    结构上锁死：body 键集精确等于 {error_code, detail}，detail 必须**等于**
    AUTH_ERR_DETAILS 里的常量文案——既不能拼接凭证，也不能回显长度。
    """
    client = client_for(enabled_bus(tmp_path))
    cases = [
        (mock_creds(token="sk-mock-wrong-000"), AUTH_ERR_TOKEN_INVALID),
        (mock_creds(token=MOCK_TOKEN + "x"), AUTH_ERR_TOKEN_INVALID),
        ({AUTH_HEADER_SEAT: MOCK_SEAT}, AUTH_ERR_TOKEN_MISSING),
    ]

    for headers, expected_code in cases:
        resp = hit(client, "/board", headers=headers)
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/json")

        body = resp.json()
        # 响应体逐键逐值等于「原因码 + 固定文案」：这是结构级保证，
        # 比"逐个子串不含"更强——不存在拼接 token、前缀或长度的位置。
        assert body == {"error_code": expected_code,
                        "detail": AUTH_ERR_DETAILS[expected_code]}

        text = resp.text
        # 真 token 与其前后缀、以及实际提交的错误 token 一律不得出现
        for secret in (MOCK_TOKEN, MOCK_TOKEN[:4], MOCK_TOKEN[-4:],
                       "sk-mock-wrong-000"):
            assert secret not in text, f"401 响应体泄露了凭证片段: {secret!r}"


def test_auth_failure_writes_no_ledger_and_no_dlq(tmp_path):
    """鉴权失败是身份层失败：完全不进入业务审理，不写账本、不进死信。"""
    bus = enabled_bus(tmp_path)
    client = client_for(bus)

    # 用一条本会被死信的正常业务载荷：若鉴权失败误入业务层，dlq 就会被写
    for _ in range(3):
        assert client.post("/route", json={
            "expected_target": "principal_laotian",
            "report": {"source": MOCK_SOURCE, "level": "principal",
                       "subject": "模拟越级请示"},
        }).status_code == 401

    assert read_jsonl(bus.dlq_path) == []
    assert read_jsonl(bus.ledger_path) == []


def test_business_rejection_still_200_ok_false_with_credentials(tmp_path):
    """有凭证时既有业务拒绝通道原样：200 + ok=False + dlq=True（不是 401）。"""
    bus = enabled_bus(tmp_path)
    client = client_for(bus)

    resp = client.post("/route", json={
        "expected_target": "principal_laotian",
        "report": {"source": MOCK_SOURCE, "level": "principal",
                   "subject": "模拟越级请示", "body": "mock"},
    }, headers=mock_creds())

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and body["dlq"] is True
    assert "越级拦截" in body["reason"]
    assert len(read_jsonl(bus.dlq_path)) == 1
    assert read_jsonl(bus.ledger_path) == []

    # /status 的业务拒绝（缺 agent）同样是 200 + ok=False
    status = client.post("/status", json={"platform": "quant"},
                         headers=mock_creds())
    assert status.status_code == 200
    assert status.json() == {"ok": False, "reason": "状态点缺失 agent 字段，无法归集"}


def test_auth_precedes_request_body_validation(tmp_path):
    """鉴权在框架请求体校验之前：无头 + 无请求体 → 401，而非 422。"""
    client = client_for(enabled_bus(tmp_path))

    assert client.post("/route").status_code == 401
    assert client.post("/status", json=["not-an-object"]).status_code == 401
    # 带凭证才轮到框架校验（422），证明 401 不是把 422 吞掉后的假象
    assert client.post("/route", headers=mock_creds()).status_code == 422
    assert client.post("/status", json=["not-an-object"],
                       headers=mock_creds()).status_code == 422


# ---------------- 七、成功返回结构与未启用时逐键一致 ----------------

def test_authorized_payload_shape_identical_to_unauthenticated(tmp_path):
    """同一业务请求：未启用 vs 启用后成功响应**键集与取值一致**（结构零漂移）。"""
    open_bus_inst = open_bus(tmp_path)
    auth_bus = enabled_bus(tmp_path, tag="auth")
    for bus in (open_bus_inst, auth_bus):
        bus.collect_status_point({
            "agent": MOCK_SOURCE, "platform": "quant", "doing": "模拟回测",
            "progress": "50%", "artifact": "mock.csv", "ts": time.time(),
        })
    open_client, auth_client = (client_for(open_bus_inst),
                                client_for(auth_bus))

    # /route：键集一致，ok 一致（record_ts 是时间戳，只比键）
    open_route = open_client.post("/route", json=ROUTE_OK_PAYLOAD)
    auth_route = auth_client.post("/route", json=ROUTE_OK_PAYLOAD,
                                  headers=mock_creds())
    assert open_route.status_code == auth_route.status_code == 200
    assert set(open_route.json()) == set(auth_route.json()) == {
        "ok", "reason", "record_ts"}
    assert auth_route.json()["ok"] is True and auth_route.json()["reason"] == ""

    # /status：键集与取值全等
    open_status = open_client.post("/status", json=STATUS_OK_PAYLOAD).json()
    auth_status = auth_client.post("/status", json=STATUS_OK_PAYLOAD,
                                   headers=mock_creds()).json()
    assert open_status == auth_status == {"ok": True, "agent": MOCK_SOURCE}

    # /board：信封与行内容全等（同一 ts、同一 now 口径下 stale 同为 False）
    assert (open_client.get("/board").json()
            == auth_client.get("/board", headers=mock_creds()).json())


def test_board_row_keys_unchanged_when_authorized(tmp_path):
    """启用后 /board 行级键集仍精确等于「六字段 + stale」（契约零漂移）。"""
    bus = enabled_bus(tmp_path)
    bus.collect_status_point({
        "agent": MOCK_SOURCE, "platform": "quant", "doing": "模拟回测",
        "progress": "50%", "artifact": "mock.csv", "ts": time.time(),
    })
    payload = client_for(bus).get("/board", headers=mock_creds()).json()

    assert set(payload) == {"contract_version", "points"}
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION
    row = payload["points"][0]
    assert set(row) == set(STATUS_POINT_FIELDS) | {"stale"}
    assert "contract_version" not in row and "stale_reason" not in row


# ---------------- 八、比较方式：hmac.compare_digest，而非 == ----------------

class _CompareDigestSpy:
    """hmac.compare_digest 替身：记录入参，按预设返回。"""

    def __init__(self, result: bool = True) -> None:
        self.calls: List[tuple] = []
        self._result = result

    def __call__(self, left: Any, right: Any) -> bool:
        self.calls.append((left, right))
        return self._result


def test_token_comparison_uses_compare_digest_with_bytes(
        tmp_path, monkeypatch):
    """凭证比较走 hmac.compare_digest，且入参是 utf-8 bytes（非 ASCII 亦安全）。

    只替换 message_bus 命名空间里的 hmac 绑定，不动全局 hmac 模块。
    """
    spy = _CompareDigestSpy(result=True)
    monkeypatch.setattr(message_bus, "hmac",
                        SimpleNamespace(compare_digest=spy))

    bus = enabled_bus(tmp_path)
    resp = hit(client_for(bus), "/board", headers=mock_creds())

    assert resp.status_code == 200
    assert spy.calls == [(MOCK_TOKEN.encode("utf-8"),
                          MOCK_TOKEN.encode("utf-8"))]
    assert all(isinstance(a, bytes) and isinstance(b, bytes)
               for a, b in spy.calls)


def test_decision_is_delegated_to_compare_digest(tmp_path, monkeypatch):
    """compare_digest 返回 False 时，即使 token 字面相同也拒绝（证明确实不是 ==）。"""
    monkeypatch.setattr(message_bus, "hmac",
                        SimpleNamespace(compare_digest=_CompareDigestSpy(False)))

    bus = enabled_bus(tmp_path)
    resp = hit(client_for(bus), "/board", headers=mock_creds())

    assert resp.status_code == 401
    assert resp.json()["error_code"] == AUTH_ERR_TOKEN_INVALID


# ---------------- 九、header 大小写 / 空白 / 路径归一化边角 ----------------

def test_header_name_case_insensitive_but_value_exact(tmp_path):
    """头名大小写不敏感（HTTP 语义），头值逐字节比较（大小写敏感、不 strip）。"""
    client = client_for(enabled_bus(tmp_path))

    lower = {"x-seat-name": MOCK_SEAT, "x-seat-token": MOCK_TOKEN}
    assert client.get("/board", headers=lower).status_code == 200

    upper = client.get("/board", headers=mock_creds(token=MOCK_TOKEN.upper()))
    assert upper.status_code == 401
    assert upper.json()["error_code"] == AUTH_ERR_TOKEN_INVALID


@pytest.mark.parametrize("headers", [
    {AUTH_HEADER_SEAT: MOCK_SEAT, AUTH_HEADER_TOKEN: f" {MOCK_TOKEN}"},
    {AUTH_HEADER_SEAT: MOCK_SEAT, AUTH_HEADER_TOKEN: f"{MOCK_TOKEN} "},
    {AUTH_HEADER_SEAT: MOCK_SEAT, AUTH_HEADER_TOKEN: f" {MOCK_TOKEN} "},
    {AUTH_HEADER_SEAT: f" {MOCK_SEAT}", AUTH_HEADER_TOKEN: MOCK_TOKEN},
    {AUTH_HEADER_SEAT: f"{MOCK_SEAT} ", AUTH_HEADER_TOKEN: MOCK_TOKEN},
])
def test_surrounding_whitespace_not_stripped(tmp_path, headers):
    """前后空白必须失败，而不是被 strip 后通过。

    实测：TestClient/httpx 不对头值做 OWS 折叠（" tok " 原样到达 ASGI），
    实现侧也只是用 .strip() 判断"是否为空"、比较时用的是**原串**
    （_sanitize_seat_credentials 存原值，check_seat_credential 逐字节比），
    故这里断言的是"实现不归一化"的真实语义：带空白 → 401。
    """
    resp = hit(client_for(enabled_bus(tmp_path)), "/board", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error_code"] in {AUTH_ERR_TOKEN_INVALID,
                                        AUTH_ERR_SEAT_UNKNOWN}


def test_configured_token_with_spaces_matches_only_exact_bytes(tmp_path):
    """配置侧同样不 strip：配置 " tok " 时只有逐字节相同的请求才通过。

    这一条锁住"比较是对称的、无隐式归一化"——两侧都不 strip，
    就不会出现"配置带空格、请求不带也能过"的模糊地带。
    """
    bus = make_bus(tmp_path, {MOCK_SEAT: f" {MOCK_TOKEN} "})
    client = client_for(bus)

    assert client.get("/board", headers=mock_creds(
        token=f" {MOCK_TOKEN} ")).status_code == 200
    assert client.get("/board",
                      headers=mock_creds()).status_code == 401


@pytest.mark.parametrize("path", [
    "/board/",                 # 尾斜杠：归一化后仍受保护
    "//board",                 # 双斜杠
    "/./board",                # 点段
    "/health/../board",        # 借白名单前缀做穿越
    "/board?x=/health",        # 借查询串伪装
    "/HEALTH",                 # 路径大小写不折叠（/health 才免验）
    "/openapi.json",           # 接口清单也受保护（有意为之）
    "/docs",
])
def test_path_variants_never_bypass_auth(tmp_path, path):
    """各种路径花招都不得绕过：只有归一化后恰为 /health 才免验。"""
    resp = client_for(make_bus(tmp_path, {})).get(path)
    assert resp.status_code == 401, f"路径变体绕过鉴权: {path}"
    assert resp.json()["error_code"] == AUTH_ERR_SEAT_MISSING


@pytest.mark.parametrize("method,path", [
    ("head", "/board"), ("options", "/board"), ("put", "/board"),
    ("get", "/route"),
])
def test_other_methods_also_gated(tmp_path, method, path):
    """鉴权在中间件层，与方法无关：HEAD/OPTIONS/PUT/GET 一样 401。"""
    client = client_for(enabled_bus(tmp_path))
    resp = getattr(client, method)(path)
    assert resp.status_code == 401


# ---------------- 十、构造期快照：凭证表不可事后被改写 ----------------

def test_credentials_are_snapshotted_at_construction(tmp_path):
    """构造后修改传入的 dict 不影响已生效的凭证（无 TOCTOU 式的配置漂移）。"""
    provided = {MOCK_SEAT: MOCK_TOKEN}
    bus = make_bus(tmp_path, provided)
    bus.register(MOCK_SOURCE, "agent")

    provided[MOCK_SEAT] = "sk-mock-evil"     # 事后篡改
    provided["ghost"] = "sk-mock-ghost"      # 事后新增
    client = client_for(bus)

    assert client.get("/board", headers=mock_creds()).status_code == 200
    assert client.get("/board", headers=mock_creds(
        token="sk-mock-evil")).status_code == 401
    assert client.get("/board", headers=mock_creds(
        seat="ghost", token="sk-mock-ghost")).status_code == 401


# ---------------- 十一、校验方法直调：完整矩阵 + 非 ASCII ----------------

def test_check_seat_credential_matrix(tmp_path):
    """check_seat_credential 全分支矩阵（含非字符串输入，一律落到拒绝分支）。"""
    disabled = make_bus(tmp_path, None, tag="off")
    # 关闭态：恒放行（连空输入也放行，因为压根不校验）
    assert disabled.check_seat_credential("", "") is None
    assert disabled.check_seat_credential(MOCK_SEAT, "any") is None

    bus = make_bus(tmp_path, {MOCK_SEAT: MOCK_TOKEN}, tag="on")
    assert bus.check_seat_credential(MOCK_SEAT, MOCK_TOKEN) is None
    assert bus.check_seat_credential("", MOCK_TOKEN) == AUTH_ERR_SEAT_MISSING
    assert bus.check_seat_credential(MOCK_SEAT, "") == AUTH_ERR_TOKEN_MISSING
    assert bus.check_seat_credential(MOCK_PEER_SEAT, MOCK_TOKEN) == AUTH_ERR_SEAT_UNKNOWN
    assert bus.check_seat_credential(MOCK_SEAT, "sk-mock-wrong") == AUTH_ERR_TOKEN_INVALID
    # 非字符串输入不得抛错、不得通过
    assert bus.check_seat_credential(None, None) == AUTH_ERR_SEAT_MISSING


def test_check_seat_credential_supports_non_ascii_token(tmp_path):
    """非 ASCII 席位/凭证：encode('utf-8') 后比较可正常通过（bytes 比较路径）。

    说明：HTTP 头值受 latin-1 限制，中文凭证只能在总线内直调层面覆盖，
    故此处测方法层——中间件把 header 原样交给它，编码逻辑一致。
    """
    bus = make_bus(tmp_path, {"归口席": "密钥-模拟"})
    assert bus.check_seat_credential("归口席", "密钥-模拟") is None
    assert bus.check_seat_credential("归口席", "密钥-模拟x") == AUTH_ERR_TOKEN_INVALID
    assert bus.check_seat_credential("归口席-假", "密钥-模拟") == AUTH_ERR_SEAT_UNKNOWN


# ---------------- 十二、原因码是封闭集合 ----------------

def test_error_codes_are_closed_set_with_fixed_details():
    """四个原因码构成封闭集合；每个都有固定中文文案，且文案不含任何凭证。"""
    assert set(AUTH_ERR_DETAILS) == {
        AUTH_ERR_SEAT_MISSING, AUTH_ERR_SEAT_UNKNOWN,
        AUTH_ERR_TOKEN_MISSING, AUTH_ERR_TOKEN_INVALID,
    }
    for code, detail in AUTH_ERR_DETAILS.items():
        assert isinstance(detail, str) and detail
        assert MOCK_TOKEN not in detail
        assert "sk-" not in detail  # 文案里不得出现任何凭证形态的串
    # 头名常量是契约（下游要靠它构造请求）
    assert AUTH_HEADER_SEAT == "X-Seat-Name"
    assert AUTH_HEADER_TOKEN == "X-Seat-Token"
