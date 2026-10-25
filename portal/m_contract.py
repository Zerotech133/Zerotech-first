# -*- coding: utf-8 -*-
"""移动端页面契约加载与校验 v1（2026-10-25）。

四份契约（portal/data/m/）：
  urls.json    路由/参数/禁用项
  cards.json   卡片↔页面契约（按钮数/标题长度/禁止嵌套）
  auth.json    三壳鉴权/令牌 TTL/敏感场景限制
  beacon.json  埋点事件与字段

用法：
  python m_contract.py --check
  python m_contract.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "m")
FILES = ("urls", "cards", "auth", "beacon")
SCHEMAS = {
    "urls": "zb.m.urls.v1",
    "cards": "zb.m.cards.v1",
    "auth": "zb.m.auth.v1",
    "beacon": "zb.m.beacon.v1",
}


class ContractError(ValueError):
    pass


def _path(name):
    return os.path.join(DATA, name + ".json")


def load(name, base_dir=None):
    p = os.path.join(base_dir, name + ".json") if base_dir else _path(name)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise ContractError("契约文件缺失/不可读：%s（%s）" % (p, e))
    except ValueError as e:
        raise ContractError("契约不是合法 JSON：%s（%s）" % (p, e))


def validate(contracts):
    """contracts: {urls, cards, auth, beacon} → (summary dict)。
    任何结构违规抛 ContractError（fail loud）。"""
    for name, schema in SCHEMAS.items():
        c = contracts.get(name)
        if not isinstance(c, dict):
            raise ContractError("缺少契约：%s" % name)
        if c.get("schema") != schema:
            raise ContractError("%s schema 应为 %s，实际 %r"
                                % (name, schema, c.get("schema")))

    urls, cards, auth, beacon = (contracts["urls"], contracts["cards"],
                                 contracts["auth"], contracts["beacon"])

    # 1) 路由：path 唯一、page 存在、scene 唯一
    routes = urls.get("routes") or []
    if not routes:
        raise ContractError("urls.routes 为空")
    paths, scenes = set(), set()
    for r in routes:
        for key in ("path", "page", "scene"):
            if not str(r.get(key) or "").strip():
                raise ContractError("urls 路由缺字段 %s：%r" % (key, r))
        if not r["path"].startswith("/m"):
            raise ContractError("路由必须以 /m 开头：%r" % r["path"])
        if r["path"] in paths:
            raise ContractError("路由重复：%s" % r["path"])
        if r["scene"] in scenes:
            raise ContractError("scene 重复：%s" % r["scene"])
        paths.add(r["path"])
        scenes.add(r["scene"])

    # 2) 卡片：按钮数上限、scene 已登记、禁止嵌套卡
    rules = cards.get("rules") or {}
    if int(rules.get("max_buttons", 99)) > 3:
        raise ContractError("cards.rules.max_buttons 不得超过 3")
    if not rules.get("forbid_nested_card"):
        raise ContractError("cards.rules.forbid_nested_card 必须为 true")
    for scene, spec in (cards.get("scenes") or {}).items():
        if scene not in scenes:
            raise ContractError("cards.scene %s 未在 urls 注册" % scene)
        btns = [k for k in ("primary", "secondary") if spec.get(k)]
        if len(btns) > int(rules.get("max_buttons", 2)):
            raise ContractError("cards.scene %s 按钮数超限" % scene)
        for bk in btns:
            btn = spec.get(bk) or {}
            p = str(btn.get("path") or "")
            if p and not p.startswith("/m"):
                raise ContractError("卡片按钮路径必须 /m 开头：%s.%s=%r"
                                    % (scene, bk, p))

    # 3) 鉴权：三壳齐全、TTL 数字、敏感场景已登记
    shells = auth.get("shells") or {}
    for key in ("card_link", "feishu_webapp", "share_link"):
        if key not in shells:
            raise ContractError("auth.shells 缺 %s" % key)
    token = auth.get("token") or {}
    if token.get("algo") != "HMAC-SHA256":
        raise ContractError("auth.token.algo 必须为 HMAC-SHA256")
    ttl = token.get("ttl_sec") or {}
    for k in ("page", "share", "sensitive"):
        if not isinstance(ttl.get(k), int) or ttl[k] <= 0:
            raise ContractError("auth.token.ttl_sec.%s 必须为正整数" % k)
    restrictions = auth.get("restrictions") or {}
    for s in restrictions.get("sensitive_scenes") or []:
        if s not in scenes:
            raise ContractError("敏感场景 %s 未在 urls 注册" % s)

    # 4) 埋点：endpoint 以 /api/m/ 开头、事件字段完整
    if not str(beacon.get("endpoint") or "").startswith("/api/m/"):
        raise ContractError("beacon.endpoint 必须以 /api/m/ 开头")
    if int(beacon.get("max_batch") or 0) <= 0:
        raise ContractError("beacon.max_batch 必须为正整数")
    for ev, spec in (beacon.get("events") or {}).items():
        if not spec.get("required"):
            raise ContractError("beacon.events.%s 缺 required" % ev)
        fields = set(spec.get("fields") or [])
        for f in spec["required"]:
            if f not in fields:
                raise ContractError("beacon.events.%s 必填字段 %s 不在 fields"
                                    % (ev, f))

    return {"routes": len(routes), "scenes": len(scenes),
            "cards": len(cards.get("scenes") or {}),
            "events": len(beacon.get("events") or {}),
            "shells": len(shells)}


def validate_all(base_dir=None):
    contracts = {n: load(n, base_dir) for n in FILES}
    return validate(contracts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        s = validate_all()
    except ContractError as e:
        print("CONTRACT FAIL: %s" % e)
        return 1
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print("契约校验通过：routes=%d scenes=%d cards=%d events=%d shells=%d"
              % (s["routes"], s["scenes"], s["cards"], s["events"],
                 s["shells"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
