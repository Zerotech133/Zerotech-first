# -*- coding: utf-8 -*-
"""AI 快速接入台：一条命令给新 AI 生成虚拟 Key + 配置片段 + 注册表。

用法：
  python -X utf8 tools/ai_onboard.py add <name> [--alias auto] [--allowed auto strong] [--owner tianfeng]
  python -X utf8 tools/ai_onboard.py list [--json]
  python -X utf8 tools/ai_onboard.py disable <name>
  python -X utf8 tools/ai_onboard.py snippet <name>   # 只显示模板（token 只在 add 时可见）

设计：所有 AI 只连 8796 网关；真 Key 只在 Key 池；注册表落 config/ai_registry.json。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "agent_platform"))
from legion_command import seat_router as R  # noqa: E402

REG = REPO / "config" / "ai_registry.json"
BASE_URL = "http://127.0.0.1:8796/v1"


def _load(path: Path | None = None) -> list:
    p = Path(path) if path else REG
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(rows: list, path: Path | None = None) -> None:
    p = Path(path) if path else REG
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    import os
    os.replace(tmp, p)


def _snippet(name: str, alias: str, token: str = "<token-只在创建时显示>") -> str:
    return """# {name} 接入配置
base_url = {base}
api_key  = {token}
model    = {alias}

# OpenAI 兼容请求：
# POST {base}/chat/completions
# Authorization: Bearer {token}
# {{"model":"{alias}","messages":[{{"role":"user","content":"你好"}}],"stream":false}}
""".format(name=name, base=BASE_URL, token=token, alias=alias)


def add(name: str, alias: str = "auto", allowed=None,
        owner: str = "", path: Path | None = None) -> dict:
    name = str(name or "").strip()
    if not name or len(name) > 64:
        raise ValueError("name 必填且 ≤64 字符")
    rows = _load(path)
    active = [r for r in rows if r.get("name") == name and r.get("status") == "active"]
    if active:
        raise ValueError("AI 已存在且启用：%s（先 disable 再重建）" % name)
    vk = R.create_vk(owner or name, alias, allowed)
    rec = {"schema": "ai_registry/v1", "name": name, "owner": owner or name,
           "alias": alias, "allowed": vk.get("allowed"), "vk_id": vk["id"],
           "masked": vk.get("masked"), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "status": "active"}
    rows.append(rec)
    _save(rows, path)
    return {"ok": True, "record": rec, "token": vk["token"],
            "config": {"base_url": BASE_URL, "api_key": vk["token"], "model": alias},
            "snippet": _snippet(name, alias, vk["token"])}


def list_agents(path: Path | None = None) -> list:
    return _load(path)


def disable(name: str, path: Path | None = None) -> dict:
    rows = _load(path)
    for r in rows:
        if r.get("name") == name and r.get("status") == "active":
            R.disable_vk(r["vk_id"])
            r["status"] = "disabled"
            r["disabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            _save(rows, path)
            return r
    raise ValueError("未找到启用中的 AI：%s" % name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 快速接入台（网关 + 虚拟Key + 注册表）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--alias", default="auto")
    a.add_argument("--allowed", nargs="*", default=None)
    a.add_argument("--owner", default="")
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    d = sub.add_parser("disable")
    d.add_argument("name")
    s = sub.add_parser("snippet")
    s.add_argument("name")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            res = add(args.name, args.alias, args.allowed, args.owner)
            print(res["snippet"])
            print(json.dumps({k: v for k, v in res.items() if k != "snippet"},
                             ensure_ascii=False, indent=1))
            return 0
        if args.cmd == "list":
            rows = list_agents()
            print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else
                  "\n".join("%-20s alias=%-7s vk=%s status=%s"
                            % (r["name"], r["alias"], r.get("masked"), r["status"])
                            for r in rows))
            return 0
        if args.cmd == "disable":
            print(json.dumps(disable(args.name), ensure_ascii=False, indent=1))
            return 0
        rows = [r for r in list_agents() if r.get("name") == args.name]
        if not rows:
            print("未找到：%s" % args.name)
            return 2
        print(_snippet(args.name, rows[0]["alias"]))
        return 0
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
