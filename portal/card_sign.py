# -*- coding: utf-8 -*-
"""卡片回调安全：HMAC 签名 + request_id 幂等（W40 收口，2026-10-26）。

问题：飞书卡片按钮 value 可被伪造/重放（回调重试、旧卡再次点击）。
方案：
  1) 建卡时 sign(val)：加 rid（唯一请求号）+ _ts + _sig = HMAC(secret, canonical)。
  2) 回调时 verify(val)：签名/时间窗（默认 600s）；旧卡无签名 → unsigned（兼容窗口，可强制）。
  3) guard(val)：rid 24h 内只处理一次（work/card_requests.jsonl），重复点击幂等忽略。

密钥：work/card_sign_secret.txt（首次自动生成；不落代码/不入库）。
环境变量：CARD_SIGN_ENFORCE=1 时拒绝无签名旧卡（默认仅告警）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
SECRET_PATH = os.path.join(WORK, "card_sign_secret.txt")
LEDGER = os.path.join(WORK, "card_requests.jsonl")
_LOCK = threading.Lock()
_TTL_VERIFY = int(os.environ.get("CARD_SIGN_TTL", "600"))
_TTL_IDEM = int(os.environ.get("CARD_IDEM_TTL", "86400"))


def _secret():
    p = SECRET_PATH
    try:
        with open(p, encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/card_sign.py", exc)
    s = uuid.uuid4().hex + uuid.uuid4().hex
    try:
        os.makedirs(WORK, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/card_sign.py", exc)
    return s


def _canonical(value):
    v = {k: v for k, v in (value or {}).items() if k != "_sig"}
    return json.dumps(
        v, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sign(value):
    """→ 新 dict：含 rid / _ts / _sig（不改原对象）。"""
    v = dict(value or {})
    v.setdefault("rid", uuid.uuid4().hex)
    v["_ts"] = int(time.time())
    v["_sig"] = hmac.new(
        _secret().encode("utf-8"), _canonical(v), hashlib.sha256
    ).hexdigest()
    return v


def verify(value, ttl=None):
    """→ (ok, reason)。reason 取值 ok/signature/expired/unsigned/no_ts。"""
    if not isinstance(value, dict):
        return False, "unsigned"
    sig = value.get("_sig")
    ts = value.get("_ts")
    if not sig:
        return False, "unsigned"
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False, "no_ts"
    if abs(time.time() - ts) > (ttl or _TTL_VERIFY):
        return False, "expired"
    want = hmac.new(
        _secret().encode("utf-8"), _canonical(value), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(str(sig), want):
        return False, "signature"
    return True, "ok"


def _load_ledger():
    rows = []
    try:
        with open(LEDGER, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def guard(value):
    """幂等：同一 rid 在 TTL 内只允许一次 → (ok, reason)。

    reason: ok / duplicate / no_rid。
    """
    rid = (value or {}).get("rid")
    if not rid:
        return True, "no_rid"
    now = time.time()
    with _LOCK:
        rows = _load_ledger()
        for r in rows:
            if r.get("rid") != rid:
                continue
            try:
                if now - float(r.get("ts") or 0) < _TTL_IDEM:
                    return False, "duplicate"
            except (TypeError, ValueError):
                continue
        try:
            os.makedirs(WORK, exist_ok=True)
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps({"rid": rid, "ts": now}, ensure_ascii=False) + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/card_sign.py", exc)
    return True, "ok"


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        v = sign({"action_id": "demo", "x": 1})
        print("signed:", v)
        print("verify:", verify(v))
        print("tampered:", verify(dict(v, x=2)))
        print("guard1:", guard(v))
        print("guard2:", guard(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
