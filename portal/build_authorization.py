# -*- coding: utf-8 -*-
"""授权名单主表构建（P0-2，2026-10-26）。

输入（现存多套名单）：
  portal/work/feishu_secrets/owners.json        人的 open_id 白名单
  portal/work/feishu_targets_by_app.json        各 app 的 owner/群 chat_id
  quant_backtest/receivers.json                 接收人（个人/群）
输出：
  portal/work/authorization.json                schema zb.authorization.v1（主表）
规则：主表是唯一可编辑事实源；owners/receivers/targets_by_app 保留只读兼容。
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "work", "authorization.json")


def _json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def build():
    owners = _json(os.path.join(HERE, "work", "feishu_secrets", "owners.json"), {})
    by_app = _json(os.path.join(HERE, "work", "feishu_targets_by_app.json"), {})
    recv = _json(os.path.join(ROOT, "quant_backtest", "receivers.json"), {}).get(
        "receivers", []
    )

    apps = by_app.get("apps") or {}
    people = {}
    groups = {}

    def person(name, **kw):
        p = people.setdefault(
            name,
            {
                "name": name,
                "aliases": [],
                "roles": [],
                "open_ids": {},
                "union_id": "",
                "note": "",
            },
        )
        for k, v in kw.items():
            if k == "open_ids":
                p["open_ids"].update(v)
            elif v:
                p[k] = v

    person(
        "田峰",
        aliases=["老田", "田丰"],
        roles=["owner", "admin"],
        union_id=owners.get("union_id", ""),
        note="本人；owner/admin；allow_any_p2p=%s" % owners.get("allow_any_p2p"),
    )
    for app_name, cfg in apps.items():
        oid = cfg.get("owner_open_id")
        if oid:
            people["田峰"]["open_ids"][app_name] = oid
        for gname, cid in (cfg.get("groups") or {}).items():
            g = groups.setdefault(
                gname,
                {"name": gname, "aliases": [], "chat_ids": {}, "roles": [], "note": ""},
            )
            g["chat_ids"][app_name] = cid
    for oid in owners.get("open_ids") or []:
        people["田峰"]["open_ids"].setdefault("legacy", oid)

    for r in recv:
        nm = r.get("name")
        if not nm:
            continue
        if r.get("type") == "chat_id":
            g = groups.setdefault(
                nm,
                {
                    "name": nm,
                    "aliases": [],
                    "chat_ids": {},
                    "roles": [],
                    "note": r.get("note") or "",
                },
            )
            g["chat_ids"].setdefault("receivers", r.get("id"))
        else:
            target = "田峰" if nm in ("个人", "本人", "老田", "田丰") else nm
            p = people.setdefault(
                target,
                {
                    "name": target,
                    "aliases": [],
                    "roles": ["member"],
                    "open_ids": {},
                    "union_id": "",
                    "note": "",
                },
            )
            p["open_ids"].setdefault("receivers", r.get("id"))
            if nm != target and nm not in p["aliases"]:
                p["aliases"].append(nm)
            if r.get("note"):
                p["note"] = r.get("note")

    d = {
        "schema": "zb.authorization.v1",
        "version": 1,
        "updated": "2026-10-26",
        "note": "授权名单主表（唯一可编辑事实源）。owners/receivers/targets_by_app 为只读兼容输入，不得再手改新增；新增人/群只改本表。",
        "sources": {
            "people": "work/feishu_secrets/owners.json",
            "by_app": "work/feishu_targets_by_app.json",
            "receivers": "quant_backtest/receivers.json",
        },
        "people": sorted(people.values(), key=lambda x: x["name"]),
        "groups": sorted(groups.values(), key=lambda x: x["name"]),
        "roles": {
            "owner": {
                "desc": "本人/决策审批",
                "permissions": ["decide", "execute", "push_critical", "admin"],
            },
            "admin": {
                "desc": "管理面（8097 console）",
                "permissions": ["state_read", "send_test", "config_write"],
            },
            "member": {"desc": "普通接收人", "permissions": ["receive"]},
        },
        "permissions_default": {"member": ["receive"]},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)
    return d


if __name__ == "__main__":
    d = build()
    print(
        "authorization: people=%d groups=%d -> %s"
        % (len(d["people"]), len(d["groups"]), OUT)
    )
