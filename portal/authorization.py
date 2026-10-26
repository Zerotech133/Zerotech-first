# -*- coding: utf-8 -*-
"""授权名单主表读取/查询（P0-2，2026-10-26）。

主表：portal/work/authorization.json（zb.authorization.v1）
用途：出口/入向/决策/H5 统一查"这个人/这个群在不在名单、有什么权限"。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "work", "authorization.json")
_CACHE = {"mtime": None, "data": None}


def load(force=False):
    try:
        mtime = os.path.getmtime(PATH)
    except OSError:
        return {"schema": "zb.authorization.v1", "people": [], "groups": [],
                "roles": {}}
    if not force and _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {"schema": "zb.authorization.v1", "people": [], "groups": [],
             "roles": {}}
    _CACHE.update({"mtime": mtime, "data": d})
    return d


def people():
    return load().get("people") or []


def groups():
    return load().get("groups") or []


def person(name_or_alias):
    for p in people():
        if name_or_alias == p.get("name") or name_or_alias in (p.get("aliases") or []):
            return p
    return None


def group(name_or_alias):
    for g in groups():
        if name_or_alias == g.get("name") or name_or_alias in (g.get("aliases") or []):
            return g
    return None


def is_allowed_open_id(open_id):
    oid = str(open_id or "")
    if not oid:
        return False
    for p in people():
        if oid in (p.get("open_ids") or {}).values():
            return True
    return False


def chat_id_for(name, app=None):
    g = group(name)
    if not g:
        return ""
    ids = g.get("chat_ids") or {}
    if app and ids.get(app):
        return ids[app]
    return next(iter(ids.values()), "")


def open_id_for(name, app=None):
    p = person(name)
    if not p:
        return ""
    ids = p.get("open_ids") or {}
    if app and ids.get(app):
        return ids[app]
    return next(iter(ids.values()), "")


def permissions_for(name):
    p = person(name) or {}
    roles = p.get("roles") or []
    perms = set()
    for r in roles:
        perms.update((load().get("roles") or {}).get(r, {}).get("permissions") or [])
    return sorted(perms)


def summary():
    return {"people": len(people()), "groups": len(groups()),
            "owners": sum(1 for p in people() if "owner" in (p.get("roles") or []))}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = summary()
    if a.json:
        print(json.dumps({"summary": s, "people": people(), "groups": groups()},
                         ensure_ascii=False, indent=1))
    else:
        print("授权主表：人=%d 群=%d owner=%d"
              % (s["people"], s["groups"], s["owners"]))
    errs = []
    for p in people():
        if not p.get("open_ids"):
            errs.append("人 %s 没有任何 open_id" % p.get("name"))
    for g in groups():
        if not g.get("chat_ids"):
            errs.append("群 %s 没有 chat_id" % g.get("name"))
    if errs:
        print("校验失败：")
        for e in errs:
            print("  ✗", e)
        return 1
    print("校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
