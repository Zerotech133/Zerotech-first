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
import threading
import time
import urllib.request

OPEN = "https://open.feishu.cn/open-apis"

_TAT_CACHE = {}                # (app_id, app_secret) -> (expire_ts, tat)
_TAT_LOCK = threading.Lock()
_TAT_TTL = 7200 - 600          # 提前 600s 过期


def load_app_creds():
    """读 ~/.lark-cli/config.json 静态凭证（apps[0]）。"""
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
        if apps:
            return apps[0].get("appId"), apps[0].get("appSecret")
    except Exception:
        pass  # 豁免（C2批2）：凭证缺失→(None,None)，调用方以 no app creds 呈现
    return None, None


def get_tat(app_id=None, app_secret=None, *, use_cache=True):
    """tenant_access_token（进程内缓存）。失败返回 None。

    use_cache=False 强制刷新（scope 授权后立即重试场景）。
    """
    if not app_id or not app_secret:
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
    except Exception:
        return None
    if not tok:
        return None
    with _TAT_LOCK:
        _TAT_CACHE[key] = (time.time() + _TAT_TTL, tok)
    return tok


def send_msg(content, msg_type, receive_id=None, receive_id_type="open_id",
             app_id=None, app_secret=None):
    """发消息/卡片 → (ok, why)。why 文本兼容 send_feishu 判例
    （no app creds / openapi code=N / openapi err ...）。"""
    if not app_id or not app_secret:
        app_id, app_secret = load_app_creds()
        if not app_id or not app_secret:
            return False, "no app creds"
    if not receive_id:
        return False, "no receive_id"
    tok = get_tat(app_id, app_secret)
    if not tok:
        return False, "no tenant token"
    body = json.dumps({"receive_id": receive_id, "msg_type": msg_type,
                       "content": content}).encode("utf-8")
    req = urllib.request.Request(
        OPEN + "/im/v1/messages?receive_id_type=" + receive_id_type,
        data=body, headers={"Content-Type": "application/json",
                            "Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp.get("code") == 0, ("openapi code=%s %s" % (
            resp.get("code"), str(resp.get("msg"))[:120]))
    except Exception as e:
        return False, ("openapi err " + str(e))[:150]


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
