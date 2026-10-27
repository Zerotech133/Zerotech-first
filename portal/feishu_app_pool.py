# -*- coding: utf-8 -*-
"""角色 → 飞书应用凭据池（W2 前置，2026-10-24）。

背景：
  · 分域后每个机器人一个独立飞书 app（registry.app_ref 指向 secrets 文件 <role>.json）；
  · 出向 8097 的默认 sender 仍用 canonical app（W3 分域后按 domain/role 选择）；
  · 发送前必须校验「目标 id 所属 app == 本次使用的 app」（否则 230001 invalid receive_id）。

本模块只做**确定化选择**与**校验**，不发消息、不读网络：
  load_role_app(role)       角色凭据（缺失 → AppPoolError，closed failure）
  app_for_role(role)        带缓存的同上
  canonical_app_id()        canonical/sender 默认 app（env → canonical.json）
  resolve_sender_app(domain, role)  按域/角色选 sender app
  validate_target_app(target, app_id)  纯函数：目标域与 sender app 一致性
  status_report()           各角色凭据就绪情况（管理台/巡检用）
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SECRET_DIR = os.path.join(HERE, "work", "feishu_secrets")
CANONICAL_FILE = os.path.join(SECRET_DIR, "canonical.json")
_CACHE = {}


class AppPoolError(RuntimeError):
    """角色凭据缺失/配置错误（显式失败，不静默降级到别的 app）。"""


def role_secret_path(role, base_dir=None):
    return os.path.join(base_dir or SECRET_DIR, role + ".json")


def canonical_app_id(base_dir=None):
    """sender 默认 app：env FEISHU_APP_ID → canonical.json → ""。"""
    v = (os.environ.get("FEISHU_APP_ID") or "").strip()
    if v:
        return v
    path = os.path.join(base_dir, "canonical.json") if base_dir else CANONICAL_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("app_id") or "").strip()
    except (OSError, ValueError):
        return ""


def load_role_app(role, base_dir=None):
    """→ (app_id, app_secret)；缺文件/缺字段抛 AppPoolError。"""
    path = role_secret_path(role, base_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise AppPoolError("角色 %s 凭据缺失：%s（%s）" % (role, path, e))
    except ValueError as e:
        raise AppPoolError("角色 %s 凭据不是合法 JSON：%s" % (role, e))
    aid = str(data.get("app_id") or "").strip()
    sec = str(data.get("app_secret") or "").strip()
    if not aid or not sec:
        raise AppPoolError("角色 %s 凭据缺 app_id/app_secret：%s" % (role, path))
    return aid, sec


def app_for_role(role, base_dir=None):
    """带进程内缓存；凭据文件 mtime 变化时自动失效。"""
    path = role_secret_path(role, base_dir)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise AppPoolError("角色 %s 凭据缺失：%s" % (role, path))
    key = (role, path)
    cached = _CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    val = load_role_app(role, base_dir)
    _CACHE[key] = (mtime, val)
    return val


def resolve_sender_app(domain, role="", base_dir=None):
    """出向 sender 选择：有独立角色凭据 → 用角色 app；否则按域回退 canonical。

    owner/ops 域允许回退 canonical（过渡期）；customer 域必须独立 app。
    """
    if role:
        try:
            return app_for_role(role, base_dir)[0]
        except AppPoolError:
            pass
    canonical = canonical_app_id(base_dir)
    if canonical:
        return canonical
    if (domain or "").strip() == "customer":
        raise AppPoolError("customer 域必须有独立 app 凭据（canonical 不可用于客户域）")
    raise AppPoolError("无可用 sender app：既无角色凭据也无 canonical")


def validate_target_app(target, app_id):
    """纯函数：目标登记的 app 与发送 app 是否一致 → (ok, reason)。

    target 形如 push_targets 条目 {name,id,type,app?}；无 app 字段 = 未登记域
    （过渡期放行，但要 reason 说明）。
    """
    want = str((target or {}).get("app") or "").strip()
    if not want:
        return True, "target 未登记 app（过渡期放行）"
    if not app_id:
        return False, "发送方 app 未知"
    if want != app_id:
        return False, "app mismatch：target=%s sender=%s" % (want, app_id)
    return True, "ok"


def status_report(base_dir=None):
    """各角色凭据就绪情况（管理台/巡检用，不发网络）。"""
    try:
        import feishu_registry as fr

        roles, _ = fr.load_registry(
            os.path.join(HERE, "data", "feishu", "registry.json")
        )
    except Exception:  # noqa: BLE001
        roles = {}
    rows = []
    for rid, meta in roles.items():
        try:
            aid, _ = load_role_app(rid, base_dir)
            ready = True
        except AppPoolError:
            aid, ready = meta.get("app_id_current") or "", False
        rows.append(
            {
                "role": rid,
                "display": meta.get("display"),
                "status": meta.get("status"),
                "app_id": aid,
                "secret_ready": ready,
            }
        )
    return {"canonical_app": canonical_app_id(base_dir), "roles": rows}


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(status_report(), ensure_ascii=False, indent=1))
