# -*- coding: utf-8 -*-
"""沙箱出口白名单策略校验/决策器（P0⑧ 增强 · WO-SANDBOX-QUOTA-20261027）。

只读工具：校验 `agent_platform/rules/sandbox_network_policy.json`，并对 host/port 做
默认为 deny 的决策。**不部署代理、不改网络**（启用=另立 L2）。

用法：
  python -X utf8 agent_platform/tools/network_policy_check.py --check
  python -X utf8 agent_platform/tools/network_policy_check.py --host pypi.org --port 443
退出码：0 通过/allowed；3 denied；2 输入/配置错误。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
POLICY = REPO / "agent_platform" / "rules" / "sandbox_network_policy.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(json.dumps({"ok": False, "error": "policy_read_failed: %s" % exc},
                                    ensure_ascii=False))
    if not isinstance(data, dict):
        raise SystemExit(json.dumps({"ok": False, "error": "policy_not_object"}, ensure_ascii=False))
    return data


def validate(policy: dict) -> list[str]:
    problems = []
    if policy.get("schema") != "sandbox_network_policy/v1":
        problems.append("bad_schema")
    if policy.get("default") != "deny":
        problems.append("default_must_be_deny")
    mode = policy.get("mode")
    if mode not in ("proxy", "none"):
        problems.append("mode_must_be_proxy_or_none")
    if mode == "proxy" and not policy.get("proxy"):
        problems.append("proxy_required_in_proxy_mode")
    allow = policy.get("allow")
    if not isinstance(allow, list) or not allow:
        problems.append("allow_must_be_nonempty_list")
        return problems
    hosts = set()
    for item in allow:
        if not isinstance(item, dict):
            problems.append("allow_item_not_object")
            continue
        host = str(item.get("host") or "")
        port = item.get("port")
        if not host:
            problems.append("host_missing")
            continue
        if any(ch in host for ch in ("*", "/", ":")):
            problems.append("host_not_exact: %s" % host)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            problems.append("port_invalid: %r" % (port,))
        key = (host, port)
        if key in hosts:
            problems.append("duplicate: %s:%s" % key)
        hosts.add(key)
    return problems


def decide(host: str, port: int, policy: dict | None = None) -> dict:
    pol = policy or _read(POLICY)
    for item in pol.get("allow") or []:
        if item.get("host") == host and int(item.get("port") or 0) == int(port):
            return {"ok": True, "allowed": True, "host": host, "port": port,
                    "reason": item.get("reason", ""), "mode": pol.get("mode")}
    return {"ok": True, "allowed": False, "host": host, "port": port,
            "reason": "default_deny", "mode": pol.get("mode")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="沙箱出口白名单策略校验/决策")
    ap.add_argument("--policy", default=str(POLICY))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    path = Path(args.policy)
    if not path.is_file():
        print(json.dumps({"ok": False, "error": "policy_missing", "path": str(path)},
                         ensure_ascii=False))
        return 2
    pol = _read(path)
    if args.check:
        problems = validate(pol)
        out = {"ok": not problems, "problems": problems, "path": str(path),
               "default": pol.get("default"), "mode": pol.get("mode"),
               "allow_n": len(pol.get("allow") or [])}
    elif args.host:
        if not (1 <= int(args.port or 0) <= 65535):
            print(json.dumps({"ok": False, "error": "bad_port"}, ensure_ascii=False))
            return 2
        out = decide(args.host, args.port, pol)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out.get("allowed") else 3
    else:
        ap.error("需要 --check 或 --host/--port")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
