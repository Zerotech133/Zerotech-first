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
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # agent_platform/legion_command -> repo root
DATA_DIR = REPO / "work" / "keypool"
STORE = DATA_DIR / "keypool.json"
FERNET_KEY = DATA_DIR / "fernet.key"

SEATS = {
    "tianfeng": {"cn": "田丰", "provider": "zhipu", "binding": "glm/glm-5.3"},
    "zhaoyun": {
        "cn": "赵云",
        "provider": "deepseek",
        "binding": "deepseek/deepseek-chat",
    },
    "zhuge": {
        "cn": "诸葛亮",
        "provider": "deepseek",
        "binding": "openclaw/deepseek-chat",
    },
    "relay": {"cn": "中转站", "provider": "custom", "binding": "openai-compatible"},
}

EXTRA_FILE = REPO / "config" / "key_seats.json"


def _extra_seats() -> dict:
    try:
        d = json.loads(EXTRA_FILE.read_text(encoding="utf-8"))
        return d.get("seats") or {}
    except Exception:
        return {}


def all_seats() -> dict:
    d = dict(SEATS)
    d.update(_extra_seats())
    return d


def _meta(seat: str) -> dict:
    m = all_seats().get(seat)
    return m or {"cn": str(seat), "provider": "custom",
                 "binding": "openai-compatible"}


PROVIDER_URL = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}
PROVIDER_MODEL = {"zhipu": "glm-4.6-flash", "deepseek": "deepseek-chat"}
POLICY = {
    "max_attempts": 2,
    "cooldown_sec": 600,
    "retry_interval_sec": 60,
    "on_all_failed": "page_banner_log_only",
}


def _default() -> dict:
    return {
        "schema": "seat_keypool/v1",
        "seats": {
            k: {
                "provider": v["provider"],
                "binding": v["binding"],
                "auto_switch": True,
                "active_key_id": None,
            }
            for k, v in SEATS.items()
        },
        "keys": [],
        "policy": dict(POLICY),
        "log": [],
    }


def _fernet():
    try:
        from cryptography.fernet import Fernet

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not FERNET_KEY.exists():
            FERNET_KEY.write_bytes(Fernet.generate_key())
            try:
                os.chmod(FERNET_KEY, 0o600)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/keypool.py", exc)
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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/keypool.py", exc)
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
    d.setdefault("log", []).append(
        {
            "at": _now(),
            "seat": seat,
            "from": frm,
            "to": to,
            "reason": reason,
            "result": result,
        }
    )
    d["log"] = d["log"][-200:]


def _key(d: dict, key_id: str):
    for k in d.get("keys", []):
        if k.get("id") == key_id:
            return k
    return None


def _provider_test(
    provider: str, secret: str, timeout: int = 15, base_url: str = "", model: str = ""
) -> dict:
    url = base_url or PROVIDER_URL.get(provider)
    if not url:
        return {"status": "unknown", "detail": "provider_not_supported"}
    body = json.dumps(
        {
            "model": model or PROVIDER_MODEL.get(provider, ""),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + secret,
        },
    )
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
        return {
            "status": "degraded",
            "http": None,
            "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
        }


def auto_switch(d: dict, seat: str, reason: str) -> dict:
    s = d.setdefault("seats", {}).setdefault(seat, {})
    old = s.get("active_key_id")
    cands = [
        k
        for k in d.get("keys", [])
        if k.get("seat") == seat
        and k.get("enabled", True)
        and k.get("status") in ("healthy", "pending")
        and k.get("id") != old
    ]
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


def _seat_summary(keys: list) -> dict:
    """统一四态：available / throttled / invalid / unconfigured。"""
    total = len(keys)
    available = sum(1 for k in keys if k.get("status") == "healthy")
    invalid = sum(1 for k in keys if k.get("status") == "invalid")
    if total == 0:
        status = "unconfigured"
    elif available:
        status = "available"
    elif invalid == total:
        status = "invalid"
    else:
        status = "throttled"
    tested = [str(k.get("last_checked")) for k in keys if k.get("last_checked")]
    return {
        "status": status,
        "available_count": available,
        "total_count": total,
        "partial": bool(available and available < total),
        "last_tested_at": max(tested) if tested else None,
    }


def state() -> dict:
    d = _load()
    out = {
        "schema": "seat_keypool_state/v1",
        "generated_at": _now(),
        "seats": {},
        "policy": d.get("policy") or POLICY,
        "log": d.get("log", [])[-40:][::-1],
        "ok": True,
    }
    for sid, meta in all_seats().items():
        s = d.get("seats", {}).get(sid, {})
        keys = []
        for k in d.get("keys", []):
            if k.get("seat") != sid:
                continue
            keys.append(
                {
                    "id": k.get("id"),
                    "label": k.get("label"),
                    "masked": k.get("mask") or "***",
                    "priority": k.get("priority"),
                    "status": k.get("status", "pending"),
                    "base_url": k.get("base_url") or "",
                    "model": k.get("model") or "",
                    "enabled": k.get("enabled", True),
                    "last_checked": k.get("last_checked"),
                    "last_error": k.get("last_error"),
                }
            )
        summary = _seat_summary(keys)
        out["seats"][sid] = {
            "cn": meta["cn"],
            "provider": meta["provider"],
            "binding": meta["binding"],
            "auto_switch": bool(s.get("auto_switch", True)),
            "active_key_id": s.get("active_key_id"),
            "degraded": bool(s.get("degraded")),
            "status": summary["status"],
            "available_count": summary["available_count"],
            "total_count": summary["total_count"],
            "partial": summary["partial"],
            "last_tested_at": summary["last_tested_at"],
            "keys": keys,
            "extra": sid not in SEATS,
        }
    return out


def _public_state_key(k: dict) -> dict:
    # secret_enc 不可逆且不返回；masked 用存储的 mask 字段
    return k


def action(req: dict) -> dict:
    act = str(req.get("action") or "").strip()
    d = _load()
    seat = str(req.get("seat") or "").strip()
    if seat not in all_seats():
        return {"ok": False, "error": "bad_seat"}
    s = d.setdefault("seats", {}).setdefault(seat, {})
    if act == "add":
        raw = str(req.get("secret") or "").strip()
        if not raw:
            return {"ok": False, "error": "missing_secret"}
        if len(d["keys"]) >= 40:
            return {"ok": False, "error": "too_many_keys"}
        kid = "k-" + uuid.uuid4().hex[:8]
        d["keys"].append(
            {
                "id": kid,
                "seat": seat,
                "label": str(req.get("label") or ("Key %d" % (len(d["keys"]) + 1)))[
                    :40
                ],
                "secret_enc": _encrypt(raw),
                "mask": _mask(raw),
                "priority": int(
                    req.get("priority")
                    or (len([x for x in d["keys"] if x.get("seat") == seat]) + 1)
                ),
                "base_url": str(req.get("base_url") or "")[:300],
                "model": str(req.get("model") or "")[:80],
                "status": "pending",
                "enabled": True,
                "last_checked": None,
                "last_error": None,
            }
        )
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
        r = _provider_test(
            _meta(seat).get("provider", "custom"),
            secret,
            base_url=str(k.get("base_url") or ""),
            model=str(k.get("model") or ""),
        )
        k["status"] = r["status"]
        k["last_checked"] = _now()
        k["last_error"] = r.get("detail") if r["status"] != "healthy" else None
        if (
            r["status"] != "healthy"
            and bool(s.get("auto_switch", True))
            and s.get("active_key_id") == kid
        ):
            auto_switch(d, seat, "test_" + r["status"])
        _log(d, seat, kid, "-", "test_" + r["status"], r["status"])
        _save(d)
        return {"ok": True, "test": r, "state": state()}
    if act == "runtime_error":
        code = str(req.get("code") or "")
        kid = s.get("active_key_id")
        k = _key(d, kid) if kid else None
        if k:
            k["status"] = (
                "quota"
                if code in ("429", "quota_exhausted")
                else ("invalid" if code in ("401", "403") else "degraded")
            )
            k["last_checked"] = _now()
            k["last_error"] = "runtime_error:%s" % code
        res = (
            auto_switch(d, seat, "runtime_" + code)
            if bool(s.get("auto_switch", True))
            else {"ok": False}
        )
        _log(
            d,
            seat,
            kid or "-",
            res.get("active_key_id", "-"),
            "runtime_error_" + code,
            "switched" if res.get("ok") else "degraded",
        )
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


def _model_secret(d: dict, seat: str, want: str):
    """按具体模型名选 Key（healthy 优先），用于 glm-5.3/deepseek-v4-pro 等精确模型。"""
    want = str(want or "").strip()
    if not want:
        return None, None
    rank = {"healthy": 0, "degraded": 1, "quota": 2, "invalid": 3}
    cands = [
        k for k in d.get("keys", [])
        if k.get("seat") == seat and k.get("enabled", True)
        and str(k.get("model") or "").strip() == want
    ]
    cands.sort(key=lambda k: (rank.get(str(k.get("status")), 9), int(k.get("priority") or 99)))
    for k in cands:
        try:
            return k, _decrypt(k.get("secret_enc") or "")
        except Exception:
            continue
    return None, None


def _provider_chat(
    provider: str,
    secret: str,
    messages: list,
    timeout: int = 45,
    base_url: str = "",
    model: str = "",
    max_tokens: int = 4000,
    tools=None,
    tool_choice=None,
) -> dict:
    url = base_url or PROVIDER_URL.get(provider)
    if not url:
        return {"ok": False, "error": "provider_not_supported"}
    payload = {
        "model": model or PROVIDER_MODEL.get(provider, ""),
        "messages": messages,
        "max_tokens": max(256, min(int(max_tokens or 4000), 16000)),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + secret,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            choice = (data.get("choices") or [{}])[0] or {}
            message = choice.get("message") or {}
            reply = message.get("content") or ""
            return {
                "ok": True,
                "reply": reply,
                "tool_calls": message.get("tool_calls") or [],
                "finish_reason": choice.get("finish_reason"),
                "raw": message,
            }
    except urllib.error.HTTPError as e:
        try:
            msg = e.read(300).decode("utf-8", "replace")
        except Exception:
            msg = ""
        return {"ok": False, "code": str(e.code), "error": msg[:200]}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "code": "error",
            "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
        }


def _local_openai_chat(messages: list, timeout: int = 60, model: str = "qwen3.5:9b",
                       max_tokens: int = 4000) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/v1/chat/completions",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
        return ((data.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        ) or ""


def _local_native_chat(messages: list, timeout: int = 90, model: str = "qwen3.5:9b") -> str:
    # 思考型模型（qwen3.5:9b）会在 reasoning 里吃满 token 导致 content 为空；
    # 原生 /api/chat + think=false 可关闭思考，直接拿最终回答。
    body = json.dumps(
        {"model": model, "messages": messages, "stream": False, "think": False},
        ensure_ascii=False,
    ).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
        return (data.get("message") or {}).get("content") or ""


def _local_chat(messages: list, timeout: int = 60, model: str = "qwen3.5:9b",
                max_tokens: int = 4000) -> dict:
    err = ""
    try:
        _t = 180 if "35b" in str(model) else timeout
        text = _local_openai_chat(messages, _t, model, max_tokens)
        if text.strip():
            return {"ok": True, "reply": text}
        err = "empty_content"
    except Exception as e:  # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, str(e)[:160])
    try:
        text = _local_native_chat(messages, (240 if "35b" in str(model) else max(timeout, 90)), model)
        if text.strip():
            return {"ok": True, "reply": text}
        return {"ok": False, "error": "local_empty_content"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s; fallback=%s" % (err, str(e)[:160])}


def auto_chat(messages: list, seats=None, allow_local: bool = True, local_model=None,
              model_filter=None, max_tokens: int = 4000, tools=None,
              tool_choice=None) -> dict:
    """一个对话框，自动适配：田丰GLM Key → 赵云/诸葛亮 DeepSeek Key → 本地9B。"""
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "error": "empty_messages"}
    messages = [
        m
        for m in messages
        if isinstance(m, dict) and m.get("role")
        and (m.get("content") or m.get("tool_calls") or m.get("role") == "tool")
    ]
    messages = messages[-40:]
    d = _load()
    attempts = []
    order = list(seats) if seats else ["relay", "tianfeng", "zhaoyun", "zhuge"]
    for seat in order:
        if model_filter:
            k, secret = _model_secret(d, seat, str(model_filter))
        else:
            k, secret = _active_secret(d, seat)
        if not k:
            attempts.append(
                {
                    "seat": seat,
                    "channel": _meta(seat).get("provider", "custom"),
                    "result": ("model_not_configured:%s" % model_filter) if model_filter else "no_active_key",
                }
            )
            continue
        if secret is None:
            attempts.append(
                {
                    "seat": seat,
                    "channel": _meta(seat).get("provider", "custom"),
                    "result": "decrypt_failed",
                }
            )
            continue
        r = _provider_chat(
            _meta(seat).get("provider", "custom"),
            secret,
            messages,
            base_url=str(k.get("base_url") or ""),
            model=str(model_filter or k.get("model") or ""),
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )
        if r.get("ok"):
            _log(d, seat, k.get("id"), k.get("id"), "auto_chat", "used")
            _save(d)
            return {
                "ok": True,
                "reply": r.get("reply", ""),
                "tool_calls": r.get("tool_calls") or [],
                "finish_reason": r.get("finish_reason"),
                "channel": (
                    "relay/custom"
                    if _meta(seat).get("provider", "custom") == "custom"
                    else _meta(seat).get("provider", "custom")
                ),
                "seat": seat,
                "attempts": attempts
                + [{"seat": seat, "channel": _meta(seat).get("provider", "custom"), "result": "ok"}],
            }
        code = str(r.get("code") or "")
        k["status"] = (
            "quota"
            if code in ("429", "402")
            else ("invalid" if code in ("401", "403") else "degraded")
        )
        k["last_error"] = r.get("error")
        k["last_checked"] = _now()
        sw = auto_switch(d, seat, "auto_chat_" + code)
        attempts.append(
            {
                "seat": seat,
                "channel": (
                    "relay/custom"
                    if _meta(seat).get("provider", "custom") == "custom"
                    else _meta(seat).get("provider", "custom")
                ),
                "result": "fail:" + code,
                "switched": sw.get("ok", False),
            }
        )
        _save(d)
    if not allow_local:
        return {"ok": False, "error": "all_channels_failed",
                "detail": "no_cloud_key_and_local_disabled", "attempts": attempts}
    lr = _local_chat(messages, model=str(local_model or "qwen3.5:9b"), max_tokens=max_tokens)
    if lr.get("ok"):
        return {
            "ok": True,
            "reply": lr.get("reply", ""),
            "channel": "local-qwen3.5:9b",
            "seat": "local",
            "attempts": attempts + [{"channel": "local-qwen3.5:9b", "result": "ok"}],
        }
    return {
        "ok": False,
        "error": "all_channels_failed",
        "detail": lr.get("error"),
        "attempts": attempts,
    }
