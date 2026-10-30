# -*- coding: utf-8 -*-
"""飞书 OpenAPI 统一客户端（K-STOCK-228 P1 出口收敛唯一底座）。

收敛判例（本模块后，全仓直连 open.feishu.cn 仅允许出现在）：
  1. 本文件（token / 消息 / 卡片图片家族）；
  2. p_g_robot.py（量化问答 bot 独立 app 凭证，多 bot 矩阵正例）；
  3. weak_track_archive.py 的 drive 文件家族（含 scope 守卫，token 走本模块）；
  4. 各测试文件的 mock。
事件订阅（feishu_bot_ws / p_g_robot）不在此层。

- 纯 urllib 零三方依赖；跨目录用 sys.path 挂 news_collector 后 import；
- TAT 进程内缓存（有效期 ~7200s，余量 600s），threading.Lock 保护，
  按 (app_id, app_secret) 分键——多 bot 并存互不污染；
- 凭证缺省读 ~/.lark-cli/config.json 第一项（send_feishu 判例），
  可显式传 (app_id, app_secret) 供独立 app 接入。
"""

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

OPEN = "https://open.feishu.cn/open-apis"

# W3 清债（2026-10-24）：OpenAPI 直发可重试错误集合与可注入的 HTTP 打开器（单测用）
_HTTP_OPEN = safe_urlopen
_RETRY_HTTP_STATUS = {429, 500, 502, 503, 504}
# 频控(99991400) 与 token 失效类(9999166x)：退避重试；token 类先强制刷新
_RETRY_BIZ_CODES = {99991400, 99991661, 99991663, 99991664, 99991668}
_TOKEN_BIZ_CODES = {99991661, 99991663, 99991664, 99991668}


def _biz_code_from_body(raw):
    try:
        return int(json.loads(raw).get("code"))
    except (ValueError, TypeError, AttributeError):
        return None


def _brief(raw, n=200):
    return str(raw or "").replace("\n", " ")[:n]


_TAT_CACHE = {}  # (app_id, app_secret) -> (expire_ts, tat)
_TAT_LOCK = threading.Lock()
_TAT_TTL = 7200 - 600  # 提前 600s 过期
# 2026-10-16 审计：token 失效/即将失效类业务码 → 清缓存换新重发一次
_TAT_INVALID_CODES = {99991661, 99991663, 99991664, 99991668}


def _canonical_app_id():
    """期望使用的 app_id：env FEISHU_APP_ID 优先，其次 work/feishu_secrets/canonical.json。

    2026-10-24 W3 根因：~/.lark-cli/config.json 里可能注册多个 app（实测两个），
    apps[0] 顺序漂移会让 token 应用域与 push_targets 的 open_id/chat_id 域错配
    → 间歇 HTTP 400（code=230001 invalid receive_id / 99992356 invalid open_chat_id）。
    """
    v = (os.environ.get("FEISHU_APP_ID") or "").strip()
    if v:
        return v
    kf = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "portal",
        "work",
        "feishu_secrets",
        "canonical.json",
    )
    try:
        with open(kf, encoding="utf-8") as f:
            return str(json.load(f).get("app_id") or "").strip()
    except (OSError, ValueError):
        return ""


_WARNED_APPS = set()


def load_app_creds():
    """读飞书应用静态凭证（**确定化选择**，不再盲取 apps[0]）。

    优先级：① env FEISHU_APP_ID/FEISHU_APP_SECRET 直供；
            ② env FEISHU_APP_ID 或 canonical.json 指定 app_id → 在 config 中精确匹配；
            ③ 都没有 → 退回 apps[0]，但打一次响亮告警（可观测，不静默）。
    20261016换机：lark-cli 1.0.5+ 明文 secret 可能外置（appSecret 为 dict 引用）
    ——非 str 即静态凭证不可用，返回 (None, None) 让调用方走 lark-cli 回退。
    """
    env_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    env_sec = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if env_id and env_sec:
        return env_id, env_sec
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
    except Exception:  # noqa: BLE001
        return None, None
    if not apps:
        return None, None
    want = env_id or _canonical_app_id()
    if want:
        for app in apps:
            if app.get("appId") == want:
                sec = app.get("appSecret")
                if isinstance(sec, str) and sec:
                    return want, sec
                print(
                    "[feishu_client] app %s secret 非明文（外置引用），"
                    "静态凭证不可用" % want,
                    file=sys.stderr,
                )
                return None, None
        print(
            "[feishu_client] 指定 app_id %s 不在 ~/.lark-cli/config.json"
            "（现有: %s）" % (want, ",".join(str(a.get("appId")) for a in apps)),
            file=sys.stderr,
        )
        return None, None
    aid, sec = apps[0].get("appId"), apps[0].get("appSecret")
    if aid and aid not in _WARNED_APPS:
        _WARNED_APPS.add(aid)
        print(
            "[feishu_client] WARN 未配置 FEISHU_APP_ID/canonical.json，"
            "退回 apps[0]=%s（config 共 %d 个 app，顺序漂移会导致 400）"
            % (aid, len(apps)),
            file=sys.stderr,
        )
    if aid and isinstance(sec, str):
        return aid, sec
    return None, None


def get_tat(app_id=None, app_secret=None, *, use_cache=True):
    """tenant_access_token（进程内缓存）。失败返回 None。

    use_cache=False 强制刷新（scope 授权后立即重试场景）。
    """
    if not app_id or not app_secret or not isinstance(app_secret, str):
        app_id, app_secret = load_app_creds()
        if not app_id or not app_secret:
            return None
    key = (app_id, app_secret)
    with _TAT_LOCK:
        c = _TAT_CACHE.get(key)
        if use_cache and c and c[0] > time.time():
            return c[1]
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        OPEN + "/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with safe_urlopen(req, timeout=15) as r:
            tok = json.loads(r.read().decode("utf-8")).get("tenant_access_token")
    except Exception as e:
        # 2026-10-16 审计：token 获取失败原全静默（调用方只见
        # "no tenant token" 无从定位）——stderr 补原因（网络/代理/凭证）
        print("[feishu_client] get_tat FAIL %s" % str(e)[:150], file=sys.stderr)
        return None
    if not tok:
        return None
    with _TAT_LOCK:
        _TAT_CACHE[key] = (time.time() + _TAT_TTL, tok)
    return tok


def _flow_record(ok, target, channel="openapi", kind=None):
    """K-PUSH-001 底座埋点：真实 API 调用结果落 push_flow 追溯账。

    fail-safe：任何异常吞掉，绝不影响发送主链路；kind 显式传入优先
    （K-PUSH-003 hub 闸内直发），否则由主脚本名推断
    （push_flow.infer_kind，auto: 前缀不计费）。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import push_flow

        push_flow.record(kind or push_flow.infer_kind(), channel, target, 1, ok)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/feishu_client.py", exc)


# ── K-PUSH-003 推送中台闸门（hub 统一入口客户端侧）─────────────────
# 语义：所有 OpenAPI 消息先过 8097 /api/send 闸门（静默时段/配额/统一
# 流水 kind）；hub 不可达/超时/拒绝 → (False, 原因)，绝不回落直发。
HUB_SEND_URL = "http://127.0.0.1:8097/api/send"

# K-ORG-204：飞书 Agent 中台出口（App Webhook / 事件订阅）
# 架构拍板：所有推送最终收口到飞书 Agent 端点，由 Agent 侧负责分发给客户群
# （push_targets 群列表作为 route_targets 字段透传，不作直发目标）。
# 配置：PUSH_AGENT_WEBHOOK env 优先，portal/work/push_secrets.json 兜底
# （gitignore 不入库；60s 缓存降读盘频次）。
# DD-1 方案 B（2026-10-19 P0 修复）：占位 URL 从未接入真实地址，闸内直发
# 全量打占位符成死信——端点解析改为「空/缺键/占位符=未配置」，未配置时
# 由 _agent_webhook_send 按降级开关处置（默认 OpenAPI 直发 / 关闭则拒发），
# 占位符只允许出现在下方比较语境，代码路径中不可达。
AGENT_WEBHOOK_PLACEHOLDER = "https://agent-webhook.local/placeholder"
AGENT_WEBHOOK_SECRETS_KEY = "push_agent_webhook"
# DD-1 方案 B 降级开关：默认开（无端点 → OpenAPI 直发，why 带
# openapi_fallback: 前缀）；显式 0/false/off/no 关闭 → 拒发+死信（B3）。
AGENT_WEBHOOK_FALLBACK_ENV = "PUSH_AGENT_WEBHOOK_FALLBACK"
_AGENT_WEBHOOK_CACHE = {"ts": 0.0, "url": ""}


def agent_webhook_fallback_enabled():
    """DD-1 方案 B 降级开关：无可用端点时是否降级 OpenAPI 直发。

    默认开（不依赖外部输入、可逆；真实 webhook 到手后自然走 Agent 路径）；
    env PUSH_AGENT_WEBHOOK_FALLBACK 显式设 0/false/off/no 关闭——关闭后
    无端点即拒发并落死信（B3，reason 区别于网络失败，不静默）。"""
    v = (os.environ.get(AGENT_WEBHOOK_FALLBACK_ENV) or "").strip().lower()
    return v not in ("0", "false", "off", "no")


def _agent_webhook():
    """Agent webhook URL：env 优先，push_secrets.json 兜底（60s 缓存）。

    DD-1 方案 B：空值/缺键/或等于占位符一律视为**未配置**→ 返回 ""，
    绝不回落占位 URL（占位符只在本函数作比较，发送代码路径中不可达）。
    未配置的处置（降级/拒发）见 _agent_webhook_send。"""
    v = (os.environ.get("PUSH_AGENT_WEBHOOK") or "").strip()
    if v and v != AGENT_WEBHOOK_PLACEHOLDER:
        return v
    now = time.time()
    if now - _AGENT_WEBHOOK_CACHE["ts"] > 60.0:
        try:
            kf = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "portal",
                "work",
                "push_secrets.json",
            )
            with open(kf, encoding="utf-8") as f:
                _AGENT_WEBHOOK_CACHE["url"] = str(
                    json.load(f).get(AGENT_WEBHOOK_SECRETS_KEY) or ""
                ).strip()
        except (OSError, ValueError):
            _AGENT_WEBHOOK_CACHE["url"] = ""
        _AGENT_WEBHOOK_CACHE["ts"] = now
    cached = _AGENT_WEBHOOK_CACHE["url"]
    if cached and cached != AGENT_WEBHOOK_PLACEHOLDER:
        return cached
    return ""


def agent_webhook_endpoint():
    """Agent webhook 端点公开解析（供 8097 闸门判定 via/拒发，DD-1 方案 B）。
    → URL 或 ""（未配置：空/缺键/占位符）。"""
    return _agent_webhook()


# K-PUSH-012 供钥：PUSH_HUB_KEY env 优先，portal/work/push_secrets.json
# 兜底（gitignore 不入库；60s 缓存降读盘频次）。各跑批计划任务宿主环境
# 通常无 PUSH_* 变量，文件兜底保证生产者能过 8097 鉴权闸（供钥链闭环）。
HUB_SECRETS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "portal",
    "work",
    "push_secrets.json",
)
_HUB_KEY_CACHE = {"ts": 0.0, "key": ""}


def _hub_key():
    k = (os.environ.get("PUSH_HUB_KEY") or "").strip()
    if k:
        return k
    if time.time() - _HUB_KEY_CACHE["ts"] > 60.0:
        try:
            with open(HUB_SECRETS_FILE, encoding="utf-8") as f:
                _HUB_KEY_CACHE["key"] = str(
                    json.load(f).get("push_hub_key") or ""
                ).strip()
        except (OSError, ValueError):
            _HUB_KEY_CACHE["key"] = ""
        _HUB_KEY_CACHE["ts"] = time.time()
    return _HUB_KEY_CACHE["key"]


def _hub_send(
    content,
    msg_type,
    receive_id,
    receive_id_type,
    kind=None,
    critical=False,
    timeout=8,
    route_targets=None,
    domain=None,
    sender_role=None,
):
    """→ (ok, why, message_id) 闸门结果；hub 不可达也返回失败，不直发。
    K-ORG-201 W38：hub 响应含 message_id（闸内直发回执）时透出，缺则 None。
    K-ORG-204：route_targets 透传给 hub gate，hub 侧转发 Agent webhook。
    FC-A（2026-10-20）：domain（owner/ops/customer）一级路由字段透传给 hub，
    缺省 None 不发字段，存量调用零变化。
    W3（2026-10-24）：sender_role 透传给 hub，由 8097 按角色选发送 app
    （sender 统一：通知/指令/执行/问答各用自己的 app）。缺省不发送该字段。"""
    try:
        body_obj = {
            "content": content,
            "msg_type": msg_type,
            "receive_id": receive_id,
            "receive_id_type": receive_id_type or "open_id",
            "kind": kind,
            "critical": critical,
        }
        if route_targets is not None:
            body_obj["route_targets"] = route_targets
        if domain:
            body_obj["domain"] = domain
        if sender_role:
            body_obj["sender_role"] = sender_role
        body = json.dumps(body_obj).encode("utf-8")
        key = _hub_key()
        if not key:
            return False, "hub authorization not configured", None
        rq = urllib.request.Request(
            HUB_SEND_URL,
            data=body,
            headers={"Content-Type": "application/json", "X-Push-Hub-Key": key},
        )
        with safe_urlopen(rq, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("ok"):
            mid = resp.get("message_id")
            return True, str(resp.get("why") or "hub ok"), (str(mid) if mid else None)
        # B1：闸门响应键名统一为 why（error 兜底一版——老闸门只回 error
        # 时真因不应被 "hub deny" 吞掉）
        return False, str(resp.get("why") or resp.get("error") or "hub deny"), None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "hub authorization denied", None
        return False, "hub HTTP %d" % e.code, None
    except Exception:
        return False, "hub unavailable or invalid response", None


def send_msg(
    content,
    msg_type,
    receive_id=None,
    receive_id_type="open_id",
    app_id=None,
    app_secret=None,
    kind=None,
    critical=False,
    via_hub=True,
    sender_role=None,
):
    """发消息/卡片 → (ok, why)。why 文本兼容 send_feishu 判例
    （no app creds / openapi code=N / openapi err ...）。

    K-PUSH-003：via_hub=True 时先过 8097 闸门（鉴权/静默/配额/显式 kind
    流水）；8097 闸内转发自身调用必须传 via_hub=False（防环）。
    K-PUSH-012：hub 不可达/拒绝时 fail-closed 不回落直发（闸门唯一通路）。
    critical=True 为预警类（豁免静默，风险提示不可吞）。
    K-ORG-201 W38：内部委托 send_msg_tracked 丢掉第三元 message_id，
    在役调用方签名与返回完全零感知。
    W3：sender_role 交给 8097 按角色选发送 app（不填=闸门默认 canonical）。"""
    ok, why, _mid = send_msg_tracked(
        content,
        msg_type,
        receive_id,
        receive_id_type,
        app_id,
        app_secret,
        kind,
        critical,
        via_hub,
        False,
        None,
        None,
        sender_role,
    )
    return ok, why


def send_msg_tracked(
    content,
    msg_type,
    receive_id=None,
    receive_id_type="open_id",
    app_id=None,
    app_secret=None,
    kind=None,
    critical=False,
    via_hub=True,
    flow_record=True,
    route_targets=None,
    domain=None,
    sender_role=None,
):
    """W38 送达证据版发消息 → (ok, why, message_id or None)。参数同 send_msg。

    message_id 口径：经 hub=hub /api/send 响应顶层 message_id；
    Agent webhook=Agent 侧回执 message_id（无则 None）。
    flow_record=False 供 8097 闸内直发自管流水（evidence 证据账）时抑止
    内部 K-PUSH-001 埋点，防同一发送双行双计。

    K-ORG-204：via_hub=False（hub 闸内直发）改为推 Agent webhook
    （App Webhook / 事件订阅），废弃 OpenAPI 直发 chat_id 路径。
    route_targets 为 push_targets 群列表透传给 Agent 侧路由参考。"""
    if via_hub:
        return _hub_send(
            content,
            msg_type,
            receive_id,
            receive_id_type,
            kind,
            critical,
            route_targets=route_targets,
            domain=domain,
            sender_role=sender_role,
        )
    # K-ORG-204：via_hub=False 改为 Agent webhook（闸内直发时由 8097 指定 app）
    return _agent_webhook_send(
        content,
        msg_type,
        receive_id,
        receive_id_type,
        kind,
        critical,
        route_targets=route_targets,
        flow_record=flow_record,
        app_id=app_id,
        app_secret=app_secret,
    )


def _agent_webhook_send(
    content,
    msg_type,
    receive_id,
    receive_id_type,
    kind,
    critical,
    route_targets,
    flow_record,
    app_id=None,
    app_secret=None,
):
    """K-ORG-204：推飞书 Agent webhook（App Webhook / 事件订阅）。
    hub 闸内直发路径改道至此——不再直调 OpenAPI /im/v1/messages chat_id。
    route_targets=push_targets 群列表透传给 Agent 侧路由参考。
    message_id 从 Agent 回执取，无则 None。

    DD-1 方案 B（B2/B3）：端点未配置（空/缺键/占位符）→ 不发起任何网络
    请求——降级开关开（缺省）走 _openapi_direct_send（OpenAPI 直发，why
    带 openapi_fallback: 前缀）；开关关闭则拒发（why=no endpoint
    configured，由上层 8097 落死信，不静默）。"""
    webhook = _agent_webhook()
    if not webhook:
        if agent_webhook_fallback_enabled():
            return _openapi_direct_send(
                content,
                msg_type,
                receive_id,
                receive_id_type,
                kind,
                flow_record,
                app_id=app_id,
                app_secret=app_secret,
            )
        return (
            False,
            (
                "no endpoint configured: agent webhook unset (env "
                "PUSH_AGENT_WEBHOOK / push_secrets.json %s) and "
                "openapi fallback disabled" % AGENT_WEBHOOK_SECRETS_KEY
            ),
            None,
        )
    payload = {
        "content": content,
        "msg_type": msg_type,
        "receive_id": webhook,  # K-ORG-204：receive_id 语义改为 Agent webhook URL
        "route_targets": route_targets or [],
        "kind": kind,
        "critical": critical,
    }
    # 兼容旧调用：route_targets 为空但 receive_id 是 chat_id → 单群透传
    if not route_targets and receive_id and str(receive_id).startswith("oc_"):
        payload["route_targets"] = [
            {"name": "legacy", "id": receive_id, "type": receive_id_type}
        ]
    try:
        body = json.dumps(payload).encode("utf-8")
        rq = urllib.request.Request(
            webhook,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Push-From": "hub-gate-agent-dispatch",
            },
        )
        with safe_urlopen(rq, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        ok = (
            bool(resp.get("ok"))
            or resp.get("code") == 0
            or resp.get("status") in ("accepted", "queued", "received")
        )
        mid = None
        if isinstance(resp, dict):
            mid = (
                resp.get("message_id")
                or resp.get("mid")
                or (resp.get("data") or {}).get("message_id")
            )
        mid = str(mid) if mid else None
        if flow_record:
            _flow_record(ok, webhook, kind=kind)
        return (
            ok,
            str(
                resp.get("why")
                or resp.get("msg")
                or resp.get("error")
                or "agent webhook ok"
            ),
            mid,
        )
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/feishu_client.py", exc)
        if flow_record:
            _flow_record(False, webhook, kind=kind)
        return False, ("agent webhook HTTP %d %s" % (e.code, raw[:200]))[:300], None
    except Exception as e:
        if flow_record:
            _flow_record(False, webhook, kind=kind)
        return False, ("agent webhook err " + str(e))[:200], None


def _openapi_content(content, msg_type):
    """OpenAPI content 规范化：text 类非 JSON 文本包成 {"text": ...}；
    已是 JSON（卡片/富文本/带 text 键）原样透传。"""
    if msg_type == "text":
        try:
            v = json.loads(content)
            if isinstance(v, dict) and "text" in v:
                return content
        except (ValueError, TypeError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/feishu_client.py", exc)
        return json.dumps({"text": str(content)}, ensure_ascii=False)
    return content


def _target_app_guard(receive_id, receive_id_type="", targets=None):
    """收件人域登记校验（W3-D）：receive_id 在 push_targets 里登记了 app 时返回该 app_id。

    背景：**open_id 是 app 维度**的；用 A app 的 token 发 B app 域 open_id
    会得到 HTTP 400（230001 invalid receive_id）。登记表有 app 字段时提前拦截，
    把"神秘 400"变成可读的 mismatch 证据。

    2026-10-25 W3 窗口修正：**chat_id 为租户级共享**（实测两个不同 app 看到
    同一 oc_ 值且都能发送成功）→ 仅对 open_id 目标做严格域校验；chat_id
    目标放行（其登记 app 仅作备注）。旧条目无 app 字段 → None（不拦）。
    """
    if (
        str(receive_id or "").startswith("oc_")
        or (receive_id_type or "").lower() == "chat_id"
    ):
        return None
    try:
        if targets is None:
            import push_targets

            targets = push_targets.targets()
    except Exception:  # noqa: BLE001
        return None
    for t in targets or []:
        if str(t.get("id")) == str(receive_id):
            v = str(t.get("app") or "").strip()
            return v or None
    return None


def _openapi_direct_send(
    content,
    msg_type,
    receive_id,
    receive_id_type,
    kind,
    flow_record,
    app_id=None,
    app_secret=None,
):
    """DD-1 方案 B 降级路径：Agent webhook 不可用 → OpenAPI 直发
    （K-ORG-204 前的旧出口 POST /im/v1/messages，chat_id/open_id 直发）。
    why 统一带 openapi_fallback: 前缀（证据链可 grep）；message_id 取
    data.message_id，无则 None。P2.6 FE-C：落账行 channel 标记为
    "openapi-fallback"（最小可选标记，非降级路径行结构零变化），供
    8097 /api/public/ops fallback_24h 只读占比统计识别。"""
    if not receive_id:
        if flow_record:
            _flow_record(False, "", kind=kind, channel="openapi-fallback")
        return False, "openapi_fallback: missing receive_id", None
    tok = get_tat(app_id, app_secret) if app_id else get_tat()
    if not tok:
        if flow_record:
            _flow_record(False, receive_id, kind=kind, channel="openapi-fallback")
        return False, "openapi_fallback: no tenant token", None
    want_app = _target_app_guard(receive_id, receive_id_type)
    if want_app:
        used_app = app_id or load_app_creds()[0]
        if used_app and used_app != want_app:
            if flow_record:
                _flow_record(False, receive_id, kind=kind, channel="openapi-fallback")
            return (
                False,
                (
                    "openapi_fallback: target app mismatch "
                    "(target needs %s, token app %s)" % (want_app, used_app)
                )[:300],
                None,
            )
    body = json.dumps(
        {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": _openapi_content(content, msg_type),
        }
    ).encode("utf-8")
    url = (
        OPEN
        + "/im/v1/messages?receive_id_type="
        + urllib.parse.quote(receive_id_type or "open_id", safe="")
    )
    attempts = 3
    resp = None
    last_why = ""
    for attempt in range(1, attempts + 1):
        rq = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + tok,
            },
        )
        try:
            with _HTTP_OPEN(rq, timeout=15) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # W3 清债（2026-10-24）：原来只留 "HTTP Error 400" 丢弃响应体，
            # 无法区分频控/参数/鉴权错误——补读 body；可重试错误退避重试。
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/feishu_client.py", exc)
            code = _biz_code_from_body(raw)
            retryable = e.code in _RETRY_HTTP_STATUS or code in _RETRY_BIZ_CODES
            last_why = (
                "openapi_fallback: err HTTP %d %s" % (e.code, _brief(raw, 300))
            )[:400]
            if retryable and attempt < attempts:
                if code in _TOKEN_BIZ_CODES:
                    tok = get_tat(use_cache=False) or tok
                time.sleep(0.5 * attempt)
                continue
            if flow_record:
                _flow_record(False, receive_id, kind=kind, channel="openapi-fallback")
            return False, last_why, None
        except Exception as e:  # noqa: BLE001
            last_why = ("openapi_fallback: err " + str(e))[:200]
            if attempt < attempts:
                time.sleep(0.5 * attempt)
                continue
            if flow_record:
                _flow_record(False, receive_id, kind=kind, channel="openapi-fallback")
            return False, last_why, None
        biz = resp.get("code")
        if biz != 0 and biz in _RETRY_BIZ_CODES and attempt < attempts:
            if biz in _TOKEN_BIZ_CODES:
                tok = get_tat(use_cache=False) or tok
            last_why = "openapi_fallback: " + str(resp.get("msg"))[:120]
            time.sleep(0.5 * attempt)
            continue
        break
    if resp is None:
        return False, (last_why or "openapi_fallback: no response")[:400], None
    ok = resp.get("code") == 0
    mid = (resp.get("data") or {}).get("message_id") if ok else None
    if flow_record:
        _flow_record(ok, receive_id, kind=kind, channel="openapi-fallback")
    why = "openapi_fallback: " + (
        str(resp.get("msg"))[:120] if not ok else "openapi direct ok"
    )
    return ok, why, (str(mid) if mid else None)


def verify_message(message_id, app_id=None, app_secret=None):
    """W38 接收端核实：GET /im/v1/messages/{message_id} 反查消息真实存在。
    只读查询：code==0 → True；404/其他业务码/凭证缺失/任何异常 → False
    （不清 TAT 缓存、不重试、不抛）。"""
    if not message_id:
        return False
    if not app_id or not app_secret:
        app_id, app_secret = load_app_creds()
        if not app_id or not app_secret:
            return False
    tok = get_tat(app_id, app_secret)
    if not tok:
        return False
    try:
        rq = urllib.request.Request(
            OPEN + "/im/v1/messages/" + urllib.parse.quote(str(message_id), safe=""),
            headers={"Authorization": "Bearer " + tok},
        )
        with safe_urlopen(rq, timeout=5) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp.get("code") == 0
    except Exception:
        return False


def upload_image(
    raw, filename="img.png", image_type="message", app_id=None, app_secret=None
):
    """上传卡内图片 → image_key（失败 None）。multipart 判例保留。"""
    if not app_id or not app_secret:
        app_id, app_secret = load_app_creds()
        if not app_id or not app_secret:
            return None
    tok = get_tat(app_id, app_secret)
    if not tok:
        return None
    boundary = "----QPZMBoundary7MA4YWxkTrZu0gW"
    b = boundary.encode()
    fname = (filename or "img.png").encode("utf-8")
    parts = [
        b"--" + b,
        b'Content-Disposition: form-data; name="image_type"',
        b"",
        image_type.encode("utf-8"),
        b"--" + b,
        b'Content-Disposition: form-data; name="image"; filename="' + fname + b'"',
        b"Content-Type: image/png",
        b"",
    ]
    data = b"\r\n".join(parts) + b"\r\n" + raw + b"\r\n--" + b + b"--\r\n"
    req = urllib.request.Request(
        OPEN + "/im/v1/images",
        data=data,
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Authorization": "Bearer " + tok,
        },
    )
    try:
        with safe_urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("code") == 0:
            return (resp.get("data") or {}).get("image_key")
        return None
    except Exception:
        return None
