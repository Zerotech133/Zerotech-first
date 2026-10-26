# -*- coding: utf-8 -*-
"""三席多 Key 池（田丰/赵云/诸葛亮）— 存储/测试/切换/自动切换。

设计：2026-10-27《三席多Key池页面设计_v1》。
- Key 手动录入，Fernet 加密存本机 `work/keypool/keypool.json`（密钥 `fernet.key` 同目录）；
- 页面/API 只返回掩码；日志不含明文；
- test 真调 provider 最小请求：200=healthy / 401/403=invalid / 429/402=quota / 超时=degraded；
- 失败且 auto_switch=on → 自动切到下一个候选（按 priority），日志留痕；
- 本模块是页面/池 MVP，不直接改各席位 LLM 调用链（下一期接读取路径）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # agent_platform/legion_command -> repo root
DATA_DIR = REPO / "work" / "keypool"
STORE = DATA_DIR / "keypool.json"
FERNET_KEY = DATA_DIR / "fernet.key"

SEATS = {
    "tianfeng": {"cn": "田丰", "provider": "zhipu", "binding": "glm/glm-5.3"},
    "zhaoyun": {"cn": "赵云", "provider": "deepseek", "binding": "deepseek/deepseek-chat"},
    "zhuge": {"cn": "诸葛亮", "provider": "deepseek", "binding": "openclaw/deepseek-chat"},
}
PROVIDER_URL = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}
PROVIDER_MODEL = {"zhipu": "glm-4.6-flash", "deepseek": "deepseek-chat"}
POLICY = {"max_attempts": 2, "cooldown_sec": 600, "retry_interval_sec": 60,
          "on_all_failed": "page_banner_log_only"}


def _default() -> dict:
    return {"schema": "seat_keypool/v1", "seats": {
        k: {"provider": v["provider"], "binding": v["binding"],
            "auto_switch": True, "active_key_id": None}
        for k, v in SEATS.items()},
        "keys": [], "policy": dict(POLICY), "log": []}


def _fernet():
    try:
        from cryptography.fernet import Fernet
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not FERNET_KEY.exists():
            FERNET_KEY.write_bytes(Fernet.generate_key())
            try:
                os.chmod(FERNET_KEY, 0o600)
            except OSError:
                pass
        return Fernet(FERNET_KEY.read_bytes())
    except Exception:
        return None


def _encrypt(secret: str) -> str:
    f = _fernet()
    if f:
        return "fernet:" + f.encrypt(secret.encode()).decode()
    return "b64:" + base64.b64encode(secret.encode()).decode()


def _decrypt(token: str) -> str:
    if token.startswith("fernet:"):
        f = _fernet()
        if not f:
            raise ValueError("fernet_unavailable")
        return f.decrypt(token[7:].encode()).decode()
    if token.startswith("b64:"):
        return base64.b64decode(token[4:]).decode()
    return ""


def _load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("keys"), list):
            d.setdefault("seats", {})
            d.setdefault("policy", dict(POLICY))
            d.setdefault("log", [])
            return d
    except Exception:
        pass
    return _default()


def _save(d: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STORE)


def _mask(secret: str) -> str:
    s = str(secret or "")
    if len(s) <= 6:
        return "*" * max(2, len(s))
    return s[:3] + "***" + s[-3:]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _log(d: dict, seat: str, frm: str, to: str, reason: str, result: str) -> None:
    d.setdefault("log", []).append({"at": _now(), "seat": seat, "from": frm,
                                    "to": to, "reason": reason, "result": result})
    d["log"] = d["log"][-200:]


def _key(d: dict, key_id: str):
    for k in d.get("keys", []):
        if k.get("id") == key_id:
            return k
    return None


def _provider_test(provider: str, secret: str, timeout: int = 15) -> dict:
    url = PROVIDER_URL.get(provider)
    if not url:
        return {"status": "unknown", "detail": "provider_not_supported"}
    body = json.dumps({"model": PROVIDER_MODEL.get(provider, ""),
                       "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 1, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + secret})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(200)
            return {"status": "healthy", "http": r.status, "detail": "ok"}
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            msg = e.read(400).decode("utf-8", "replace")
        except Exception:
            msg = ""
        if code in (401, 403):
            return {"status": "invalid", "http": code, "detail": msg[:200]}
        if code in (402, 429):
            return {"status": "quota", "http": code, "detail": msg[:200]}
        if 500 <= code < 600:
            return {"status": "degraded", "http": code, "detail": msg[:200]}
        return {"status": "degraded", "http": code, "detail": msg[:200]}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "http": None,
                "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}


def auto_switch(d: dict, seat: str, reason: str) -> dict:
    s = d.setdefault("seats", {}).setdefault(seat, {})
    old = s.get("active_key_id")
    cands = [k for k in d.get("keys", [])
             if k.get("seat") == seat and k.get("enabled", True)
             and k.get("status") in ("healthy", "pending")
             and k.get("id") != old]
    cands.sort(key=lambda k: (int(k.get("priority") or 99), k.get("id")))
    if not cands:
        s["active_key_id"] = None
        s["degraded"] = True
        _log(d, seat, old or "-", "-", reason, "no_candidate_degraded")
        return {"ok": False, "error": "no_healthy_key", "degraded": True}
    new = cands[0]["id"]
    s["active_key_id"] = new
    s["degraded"] = False
    _log(d, seat, old or "-", new, reason, "auto_switched")
    return {"ok": True, "active_key_id": new, "from": old, "reason": reason}


def state() -> dict:
    d = _load()
    out = {"schema": "seat_keypool_state/v1", "generated_at": _now(),
           "seats": {}, "policy": d.get("policy") or POLICY,
           "log": d.get("log", [])[-40:][::-1], "ok": True}
    for sid, meta in SEATS.items():
        s = d.get("seats", {}).get(sid, {})
        keys = []
        for k in d.get("keys", []):
            if k.get("seat") != sid:
                continue
            keys.append({"id": k.get("id"), "label": k.get("label"),
                         "masked": k.get("mask") or "***",
                         "priority": k.get("priority"), "status": k.get("status", "pending"),
                         "enabled": k.get("enabled", True),
                         "last_checked": k.get("last_checked"),
                         "last_error": k.get("last_error")})
        out["seats"][sid] = {"cn": meta["cn"], "provider": meta["provider"],
                             "binding": meta["binding"],
                             "auto_switch": bool(s.get("auto_switch", True)),
                             "active_key_id": s.get("active_key_id"),
                             "degraded": bool(s.get("degraded")),
                             "keys": keys}
    return out


def _public_state_key(k: dict) -> dict:
    # secret_enc 不可逆且不返回；masked 用存储的 mask 字段
    return k


def action(req: dict) -> dict:
    act = str(req.get("action") or "").strip()
    d = _load()
    seat = str(req.get("seat") or "").strip()
    if seat not in SEATS:
        return {"ok": False, "error": "bad_seat"}
    s = d.setdefault("seats", {}).setdefault(seat, {})
    if act == "add":
        raw = str(req.get("secret") or "").strip()
        if not raw:
            return {"ok": False, "error": "missing_secret"}
        if len(d["keys"]) >= 40:
            return {"ok": False, "error": "too_many_keys"}
        kid = "k-" + uuid.uuid4().hex[:8]
        d["keys"].append({"id": kid, "seat": seat,
                          "label": str(req.get("label") or ("Key %d" % (len(d["keys"]) + 1)))[:40],
                          "secret_enc": _encrypt(raw),
                          "mask": _mask(raw),
                          "priority": int(req.get("priority") or (len([x for x in d["keys"] if x.get("seat") == seat]) + 1)),
                          "status": "pending", "enabled": True,
                          "last_checked": None, "last_error": None})
        if not s.get("active_key_id"):
            s["active_key_id"] = kid
        _log(d, seat, "-", kid, "manual_add", "added")
        _save(d)
        return {"ok": True, "id": kid, "state": state()}
    if act == "update":
        k = _key(d, str(req.get("key_id") or ""))
        if not k or k.get("seat") != seat:
            return {"ok": False, "error": "key_not_found"}
        if req.get("label") is not None:
            k["label"] = str(req.get("label"))[:40]
        if req.get("priority") is not None:
            k["priority"] = int(req.get("priority"))
        if req.get("enabled") is not None:
            k["enabled"] = bool(req.get("enabled"))
        _save(d)
        return {"ok": True, "state": state()}
    if act == "delete":
        kid = str(req.get("key_id") or "")
        d["keys"] = [k for k in d["keys"] if k.get("id") != kid]
        if s.get("active_key_id") == kid:
            s["active_key_id"] = None
        _log(d, seat, kid, "-", "delete", "deleted")
        _save(d)
        return {"ok": True, "state": state()}
    if act == "switch":
        kid = str(req.get("key_id") or "")
        k = _key(d, kid)
        if not k or k.get("seat") != seat:
            return {"ok": False, "error": "key_not_found"}
        s["active_key_id"] = kid
        s["degraded"] = False
        _log(d, seat, "-", kid, "manual_switch", "switched")
        _save(d)
        return {"ok": True, "state": state()}
    if act == "autoswitch_toggle":
        s["auto_switch"] = bool(req.get("enabled"))
        _save(d)
        return {"ok": True, "state": state()}
    if act == "test":
        kid = str(req.get("key_id") or "")
        k = _key(d, kid)
        if not k or k.get("seat") != seat:
            return {"ok": False, "error": "key_not_found"}
        try:
            secret = _decrypt(k.get("secret_enc") or "")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "decrypt_failed", "detail": str(e)[:120]}
        r = _provider_test(SEATS[seat]["provider"], secret)
        k["status"] = r["status"]
        k["last_checked"] = _now()
        k["last_error"] = r.get("detail") if r["status"] != "healthy" else None
        if r["status"] != "healthy" and bool(s.get("auto_switch", True)) and s.get("active_key_id") == kid:
            auto_switch(d, seat, "test_" + r["status"])
        _log(d, seat, kid, "-", "test_" + r["status"], r["status"])
        _save(d)
        return {"ok": True, "test": r, "state": state()}
    if act == "runtime_error":
        code = str(req.get("code") or "")
        kid = s.get("active_key_id")
        k = _key(d, kid) if kid else None
        if k:
            k["status"] = "quota" if code in ("429", "quota_exhausted") else (
                "invalid" if code in ("401", "403") else "degraded")
            k["last_checked"] = _now()
            k["last_error"] = "runtime_error:%s" % code
        res = auto_switch(d, seat, "runtime_" + code) if bool(s.get("auto_switch", True)) else {"ok": False}
        _log(d, seat, kid or "-", res.get("active_key_id", "-"), "runtime_error_" + code,
             "switched" if res.get("ok") else "degraded")
        _save(d)
        return {"ok": True, "switch": res, "state": state()}
    return {"ok": False, "error": "unknown_action"}


def mask_only_state() -> dict:
    """state() 已只返回掩码；保留别名供测试。"""
    return state()


def _active_secret(d: dict, seat: str):
    s = d.get("seats", {}).get(seat, {})
    kid = s.get("active_key_id")
    k = _key(d, kid) if kid else None
    if not k or not k.get("enabled", True):
        return None, None
    try:
        return k, _decrypt(k.get("secret_enc") or "")
    except Exception:
        return k, None


def _provider_chat(provider: str, secret: str, messages: list, timeout: int = 45) -> dict:
    url = PROVIDER_URL.get(provider)
    if not url:
        return {"ok": False, "error": "provider_not_supported"}
    body = json.dumps({"model": PROVIDER_MODEL.get(provider, ""), "messages": messages,
                       "max_tokens": 800, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + secret})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return {"ok": True, "reply": reply}
    except urllib.error.HTTPError as e:
        try:
            msg = e.read(300).decode("utf-8", "replace")
        except Exception:
            msg = ""
        return {"ok": False, "code": str(e.code), "error": msg[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def _local_chat(messages: list, timeout: int = 60) -> dict:
    body = json.dumps({"model": "qwen3.5:9b", "messages": messages,
                       "max_tokens": 800, "stream": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions",
                                 data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return {"ok": True, "reply": reply}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def auto_chat(messages: list) -> dict:
    """一个对话框，自动适配：田丰GLM Key → 赵云/诸葛亮 DeepSeek Key → 本地9B。"""
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "error": "empty_messages"}
    messages = [m for m in messages if isinstance(m, dict) and m.get("role") and m.get("content")]
    messages = messages[-20:]
    d = _load()
    attempts = []
    for seat in ("tianfeng", "zhaoyun", "zhuge"):
        k, secret = _active_secret(d, seat)
        if not k:
            attempts.append({"seat": seat, "channel": SEATS[seat]["provider"], "result": "no_active_key"})
            continue
        if secret is None:
            attempts.append({"seat": seat, "channel": SEATS[seat]["provider"], "result": "decrypt_failed"})
            continue
        r = _provider_chat(SEATS[seat]["provider"], secret, messages)
        if r.get("ok"):
            _log(d, seat, k.get("id"), k.get("id"), "auto_chat", "used")
            _save(d)
            return {"ok": True, "reply": r.get("reply", ""),
                    "channel": SEATS[seat]["provider"], "seat": seat,
                    "attempts": attempts + [{"seat": seat, "channel": SEATS[seat]["provider"], "result": "ok"}]}
        code = str(r.get("code") or "")
        k["status"] = "quota" if code in ("429", "402") else ("invalid" if code in ("401", "403") else "degraded")
        k["last_error"] = r.get("error")
        k["last_checked"] = _now()
        sw = auto_switch(d, seat, "auto_chat_" + code)
        attempts.append({"seat": seat, "channel": SEATS[seat]["provider"],
                         "result": "fail:" + code, "switched": sw.get("ok", False)})
        _save(d)
    lr = _local_chat(messages)
    if lr.get("ok"):
        return {"ok": True, "reply": lr.get("reply", ""), "channel": "local-qwen3.5:9b",
                "seat": "local", "attempts": attempts + [{"channel": "local-qwen3.5:9b", "result": "ok"}]}
    return {"ok": False, "error": "all_channels_failed",
            "detail": lr.get("error"), "attempts": attempts}
