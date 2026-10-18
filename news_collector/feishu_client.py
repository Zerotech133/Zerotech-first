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

OPEN = "https://open.feishu.cn/open-apis"

_TAT_CACHE = {}                # (app_id, app_secret) -> (expire_ts, tat)
_TAT_LOCK = threading.Lock()
_TAT_TTL = 7200 - 600          # 提前 600s 过期
# 2026-10-16 审计：token 失效/即将失效类业务码 → 清缓存换新重发一次
_TAT_INVALID_CODES = {99991661, 99991663, 99991664, 99991668}


def load_app_creds():
    """读 ~/.lark-cli/config.json 静态凭证（apps[0]）。

    20261016换机：lark-cli 1.0.5+ 明文 secret 外置（config.json 里
    appSecret 为 {"source","id"} 引用 dict）——非 str 即静态凭证不可用，
    返回 (None, None) 让调用方走 lark-cli 回退；防止 dict 进 get_tat
    缓存键导致 unhashable type: 'dict'。"""
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
        if apps:
            aid, sec = apps[0].get("appId"), apps[0].get("appSecret")
            if aid and isinstance(sec, str):
                return aid, sec
    except Exception:
        pass  # 豁免（C2批2）：凭证缺失→(None,None)，调用方以 no app creds 呈现
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
    body = json.dumps({"app_id": app_id,
                       "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        OPEN + "/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read().decode("utf-8")).get(
                "tenant_access_token")
    except Exception as e:
        # 2026-10-16 审计：token 获取失败原全静默（调用方只见
        # "no tenant token" 无从定位）——stderr 补原因（网络/代理/凭证）
        print("[feishu_client] get_tat FAIL %s" % str(e)[:150],
              file=sys.stderr)
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
    except Exception:
        pass


# ── K-PUSH-003 推送中台闸门（hub 统一入口客户端侧）─────────────────
# 语义：所有 OpenAPI 消息先过 8097 /api/send 闸门（静默时段/配额/统一
# 流水 kind）；hub 不可达/超时/拒绝 → (False, 原因)，绝不回落直发。
HUB_SEND_URL = "http://127.0.0.1:8097/api/send"

# K-PUSH-012 供钥：PUSH_HUB_KEY env 优先，portal/work/push_secrets.json
# 兜底（gitignore 不入库；60s 缓存降读盘频次）。各跑批计划任务宿主环境
# 通常无 PUSH_* 变量，文件兜底保证生产者能过 8097 鉴权闸（供钥链闭环）。
HUB_SECRETS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "portal", "work", "push_secrets.json")
_HUB_KEY_CACHE = {"ts": 0.0, "key": ""}


def _hub_key():
    k = (os.environ.get("PUSH_HUB_KEY") or "").strip()
    if k:
        return k
    if time.time() - _HUB_KEY_CACHE["ts"] > 60.0:
        try:
            with open(HUB_SECRETS_FILE, encoding="utf-8") as f:
                _HUB_KEY_CACHE["key"] = str(
                    json.load(f).get("push_hub_key") or "").strip()
        except (OSError, ValueError):
            _HUB_KEY_CACHE["key"] = ""
        _HUB_KEY_CACHE["ts"] = time.time()
    return _HUB_KEY_CACHE["key"]


def _hub_send(content, msg_type, receive_id, receive_id_type,
              kind=None, critical=False, timeout=8):
    """→ (ok, why, message_id) 闸门结果；hub 不可达也返回失败，不直发。
    K-ORG-201 W38：hub 响应含 message_id（闸内直发回执）时透出，缺则 None。"""
    try:
        body = json.dumps({"content": content, "msg_type": msg_type,
                           "receive_id": receive_id,
                           "receive_id_type": receive_id_type or "open_id",
                           "kind": kind, "critical": critical}
                          ).encode("utf-8")
        key = _hub_key()
        if not key:
            return False, "hub authorization not configured", None
        rq = urllib.request.Request(
            HUB_SEND_URL, data=body,
            headers={"Content-Type": "application/json", "X-Push-Hub-Key": key})
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("ok"):
            mid = resp.get("message_id")
            return True, str(resp.get("why") or "hub ok"), (
                str(mid) if mid else None)
        return False, str(resp.get("error") or "hub deny"), None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "hub authorization denied", None
        return False, "hub HTTP %d" % e.code, None
    except Exception:
        return False, "hub unavailable or invalid response", None


def send_msg(content, msg_type, receive_id=None, receive_id_type="open_id",
             app_id=None, app_secret=None, kind=None, critical=False,
             via_hub=True):
    """发消息/卡片 → (ok, why)。why 文本兼容 send_feishu 判例
    （no app creds / openapi code=N / openapi err ...）。

    K-PUSH-003：via_hub=True 时先过 8097 闸门（鉴权/静默/配额/显式 kind
    流水）；8097 闸内转发自身调用必须传 via_hub=False（防环）。
    K-PUSH-012：hub 不可达/拒绝时 fail-closed 不回落直发（闸门唯一通路）。
    critical=True 为预警类（豁免静默，风险提示不可吞）。
    K-ORG-201 W38：内部委托 send_msg_tracked 丢掉第三元 message_id，
    在役调用方签名与返回完全零感知。"""
    ok, why, _mid = send_msg_tracked(content, msg_type, receive_id,
                                     receive_id_type, app_id, app_secret,
                                     kind, critical, via_hub)
    return ok, why


def send_msg_tracked(content, msg_type, receive_id=None,
                     receive_id_type="open_id", app_id=None, app_secret=None,
                     kind=None, critical=False, via_hub=True,
                     flow_record=True):
    """W38 送达证据版发消息 → (ok, why, message_id or None)。参数同 send_msg。

    message_id 口径：直发=飞书 im/v1/messages 响应 data.message_id；
    经 hub=hub /api/send 响应顶层 message_id（hub 侧闸内直发回执透传）。
    flow_record=False 供 8097 闸内直发自管流水（evidence 证据账）时抑止
    内部 K-PUSH-001 埋点，防同一发送双行双计。"""
    if via_hub:
        return _hub_send(content, msg_type, receive_id, receive_id_type,
                         kind, critical)
    if not app_id or not app_secret:
        app_id, app_secret = load_app_creds()
        if not app_id or not app_secret:
            return False, "no app creds", None
    if not receive_id:
        return False, "no receive_id", None
    tok = get_tat(app_id, app_secret)
    if not tok:
        return False, "no tenant token", None
    body = json.dumps({"receive_id": receive_id, "msg_type": msg_type,
                       "content": content}).encode("utf-8")

    def _post(_tok):
        rq = urllib.request.Request(
            OPEN + "/im/v1/messages?receive_id_type=" + receive_id_type,
            data=body, headers={"Content-Type": "application/json",
                                "Authorization": "Bearer " + _tok})
        with urllib.request.urlopen(rq, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fr(ok):
        if flow_record:
            _flow_record(ok, receive_id, kind=kind)

    def _mid_of(resp):
        mid = (resp.get("data") or {}).get("message_id") \
            if resp.get("code") == 0 else None
        return str(mid) if mid else None

    try:
        resp = _post(tok)
        # 2026-10-16 审计：token 失效类业务码 → 清缓存强制换新重发一次
        # （进程内 TAT 最长 6600s，权限变更后原只能干等缓存过期）
        if resp.get("code") in _TAT_INVALID_CODES:
            with _TAT_LOCK:
                _TAT_CACHE.pop((app_id, app_secret), None)
            tok = get_tat(app_id, app_secret, use_cache=False)
            if tok:
                resp = _post(tok)
        ok = resp.get("code") == 0
        _fr(ok)  # K-PUSH-001 埋点
        return ok, ("openapi code=%s %s" % (
            resp.get("code"), str(resp.get("msg"))[:120])), _mid_of(resp)
    except urllib.error.HTTPError as e:
        # K-PUSH-004 判例：HTTP 4xx/5xx 须读响应体拿飞书原始 code/msg
        # （hub 侧 400 只见"Bad Request"无从定位——08-17 卡片 400 排障实证）
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        detail = raw[:250]
        # K-ORG-199：HTTP 4xx 包裹的 TAT 失效码同样自愈——2026-10-19
        # 试发排障实证：hub 进程持坏 TAT，飞书 400 包 99991663 走到本
        # 分支原只读详情不刷新，只能干等 TTL（最长 6600s）自然过期。
        # 与上方 200 分支同口径：清缓存强刷重发一次，不递归。
        try:
            ecode = json.loads(raw).get("code")
        except Exception:
            ecode = None
        if ecode in _TAT_INVALID_CODES:
            with _TAT_LOCK:
                _TAT_CACHE.pop((app_id, app_secret), None)
            tok2 = get_tat(app_id, app_secret, use_cache=False)
            if tok2:
                try:
                    resp = _post(tok2)
                    ok = resp.get("code") == 0
                    _fr(ok)  # K-PUSH-001
                    return ok, ("openapi code=%s %s" % (
                        resp.get("code"), str(resp.get("msg"))[:120])), \
                        _mid_of(resp)
                except Exception as e2:
                    _fr(False)
                    return False, ("openapi err " + str(e2))[:150], None
        _fr(False)  # K-PUSH-001 埋点
        return False, ("openapi err %s %s" % (e, detail))[:400], None
    except Exception as e:
        _fr(False)  # K-PUSH-001 埋点
        return False, ("openapi err " + str(e))[:150], None


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
            OPEN + "/im/v1/messages/" + urllib.parse.quote(str(message_id),
                                                           safe=""),
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(rq, timeout=5) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp.get("code") == 0
    except Exception:
        return False


def upload_image(raw, filename="img.png", image_type="message",
                 app_id=None, app_secret=None):
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
    parts = [b"--" + b,
             b'Content-Disposition: form-data; name="image_type"',
             b"", image_type.encode("utf-8"),
             b"--" + b,
             b'Content-Disposition: form-data; name="image"; filename="'
             + fname + b'"',
             b"Content-Type: image/png",
             b""]
    data = b"\r\n".join(parts) + b"\r\n" + raw + b"\r\n--" + b + b"--\r\n"
    req = urllib.request.Request(
        OPEN + "/im/v1/images", data=data,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("code") == 0:
            return (resp.get("data") or {}).get("image_key")
        return None
    except Exception:
        return None
