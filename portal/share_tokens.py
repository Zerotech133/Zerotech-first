# -*- coding: utf-8 -*-
"""分享令牌（K-PUSH-002 A1）——推送卡片深链的溯源 token：签发/校验/访问账。

定位：商业化追溯（哪张卡带来的流量）+ 为付费分享预留（有效期/租户绑定）。
密钥与 commercial.json 的 apikey 分离（职责不同：apikey=对外 API 鉴权，
share_secret=链接签发），存 portal/work/share_secret.json（gitignored，
缺失自动生成 32B hex——首次签发即生效，轮换只改该文件，旧 token 全失效）。

格式：st.<b64url(payload_json)>.<hmac_sha256(secret, payload_b64)[:16 hex]>
payload：{"jti","code","kind","tenant","iat","exp"}（jti=随机 8 hex，
    同一 payload 每次签发都不同 → 可按 jti 区分单次分享）

账本：portal/work/share_tokens.jsonl（issue/visit 两类事件，
    O_APPEND + 单次 os.write 跨进程原子追加，同 push_flow 判例）

分层落账（K-STOCK-411 判例：公网网关静态直出、不执行 Python）：
    内网链路 server_gate(:3000) → GET /s/<token> 校验+visit 落账+302 进板；
    公网静态站（WorkBuddy 托管）→ 服务端不了账，?st= 由 picks.html
    前端 beacon 尝试 /s/<token>（公网 404 静默失败，内网同源命中）——
    降级可接受：公网访问量后续经 nginx/边缘方案收口再补。
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_PATH = os.path.join(HERE, "work", "share_secret.json")
FLOW_PATH = os.path.join(HERE, "work", "share_tokens.jsonl")
_lock = threading.Lock()


def _load_secret():
    """读取（缺失则生成）HMAC 密钥。失败返回空串→签发/校验双双降级。"""
    try:
        with open(SECRET_PATH, encoding="utf-8") as f:
            s = (json.load(f).get("secret") or "").strip()
            if s:
                return s
    except Exception:
        pass
    try:
        s = secrets.token_hex(32)
        os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
        with open(SECRET_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"secret": s, "created": time.strftime("%Y-%m-%d")},
                f,
                ensure_ascii=False,
            )
        return s
    except Exception:
        return ""


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def gen(code="", kind="card", ttl_hours=72, tenant="default"):
    """签发一枚分享 token；密钥不可用时返回空串（调用方降级为不带 st 链接）。"""
    secret = _load_secret()
    if not secret:
        return ""
    payload = {
        "jti": secrets.token_hex(4),
        "code": str(code or "")[:16],
        "kind": str(kind or "card")[:24],
        "tenant": str(tenant or "default")[:40],
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_hours * 3600,
    }
    body = _b64u(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()[:16]
    _record("issue", payload)
    return "st.%s.%s" % (body, sig)


def verify(token):
    """校验签名与有效期 → payload dict；任何异常/过期/坏签名返回 None。"""
    try:
        _, body, sig = str(token).split(".")
        if len(sig) != 16:
            return None
        secret = _load_secret()
        if not secret:
            return None
        want = hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).hexdigest()[:16]
        if not hmac.compare_digest(want, sig):
            return None
        payload = json.loads(_b64u_dec(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None


def visit(token):
    """访问收口：校验通过 → visit 落账并返回 payload；否则 None（不落账）。"""
    payload = verify(token)
    if payload is None:
        return None
    _record("visit", payload)
    return payload


def _record(evt, payload):
    """issue/visit 事件落账，fail-safe 吞异常（追溯账丢失可容忍）。"""
    try:
        from datetime import datetime

        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "evt": evt,
            "jti": payload.get("jti", ""),
            "code": payload.get("code", ""),
            "kind": payload.get("kind", ""),
            "tenant": payload.get("tenant", ""),
        }
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        with _lock:
            fd = os.open(FLOW_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except Exception:
        pass


def stats(days=7):
    """近 N 天：逐日签发/访问数 + 今日按 kind 的访问分布。"""
    import datetime as dt

    today = dt.date.today()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    by_day, today_by_kind = {}, {}
    try:
        with open(FLOW_PATH, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                d = r.get("date", "")
                if not (start <= d <= today.isoformat()):
                    continue
                slot = by_day.setdefault(d, {"issue": 0, "visit": 0})
                if r.get("evt") in ("issue", "visit"):
                    slot[r["evt"]] += 1
                if d == today.isoformat() and r.get("evt") == "visit":
                    k = r.get("kind", "?")
                    today_by_kind[k] = today_by_kind.get(k, 0) + 1
    except OSError:
        pass
    seq = [(today - dt.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    return {
        "by_day": [
            {
                "date": d,
                "issue": by_day.get(d, {}).get("issue", 0),
                "visit": by_day.get(d, {}).get("visit", 0),
            }
            for d in seq
        ],
        "today_visit_by_kind": today_by_kind,
    }


if __name__ == "__main__":
    tok = gen(code="605100.SH", kind="selftest")
    print("token:", tok)
    print("verify:", verify(tok))
    print("stats:", json.dumps(stats(1), ensure_ascii=False))
