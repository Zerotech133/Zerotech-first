# -*- coding: utf-8 -*-
"""O3-P2 对账：扩展 manifest ↔ 运行配置（servers.json / tool entry）只读差异。

用法：
  python -X utf8 tools/extension_manifest_audit.py
  python -X utf8 tools/extension_manifest_audit.py --json
  python -X utf8 tools/extension_manifest_audit.py --strict   # 有 mismatch → rc=1
规则见 docs/specs/2026-10-27_扩展契约_v1.md §七。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(REPO, "extensions")
SERVERS = os.path.join(REPO, "agent_platform", "legion_mcp", "servers.json")


def load_extensions(ext_dir=EXT_DIR):
    out = {}
    for p in sorted(glob.glob(os.path.join(ext_dir, "*", "manifest.json"))):
        try:
            with io.open(p, "r", encoding="utf-8") as f:
                out[os.path.basename(os.path.dirname(p))] = json.load(f)
        except (OSError, ValueError):
            out[os.path.basename(os.path.dirname(p))] = {}
    return out


def _norm_allow(v):
    if v == "*" or v is None:
        return "*"
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    return str(v)


def compare_mcp(manifest, server):
    """比较一个 manifest 与其 runtime.mcp_server 对应的 server 条目。"""
    findings = []
    if str(manifest.get("risk") or "") != str(server.get("risk") or ""):
        findings.append(("risk", manifest.get("risk"), server.get("risk")))
    m_t = str(manifest.get("transport") or "stdio").lower()
    s_t = str(server.get("transport") or "stdio").lower()
    if m_t != s_t:
        findings.append(("transport", m_t, s_t))
    if _norm_allow(manifest.get("allow_tools")) != _norm_allow(server.get("allow_tools")):
        findings.append(("allow_tools", manifest.get("allow_tools"), server.get("allow_tools")))
    if m_t == "http":
        if str(manifest.get("token_env") or "") != str(server.get("token_env") or ""):
            findings.append(("token_env", manifest.get("token_env"), server.get("token_env")))
        if str(manifest.get("url") or "") != str(server.get("url") or ""):
            findings.append(("url", manifest.get("url"), server.get("url")))
    return findings


def audit(ext_dir=EXT_DIR, servers_path=SERVERS):
    exts = load_extensions(ext_dir)
    try:
        with io.open(servers_path, "r", encoding="utf-8") as f:
            servers = (json.load(f) or {}).get("servers") or {}
    except (OSError, ValueError):
        servers = {}
    mapped = {}
    mismatches = []
    for name, m in exts.items():
        rt = (m or {}).get("runtime") or {}
        srv = rt.get("mcp_server")
        if not srv:
            continue
        mapped[srv] = name
        if srv not in servers:
            mismatches.append({"extension": name, "server": srv, "field": "missing_server"})
            continue
        for field, mval, sval in compare_mcp(m, servers[srv]):
            mismatches.append({"extension": name, "server": srv, "field": field,
                               "manifest": mval, "runtime": sval})
    unmigrated = [s for s in servers if s not in mapped]
    return {
        "extensions": len(exts),
        "server_entries": len(servers),
        "mapped": len(mapped),
        "mismatches": mismatches,
        "unmigrated_servers": unmigrated,
        "ok": not mismatches,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="扩展 manifest ↔ 运行配置只读对账")
    ap.add_argument("--ext-dir", default=EXT_DIR)
    ap.add_argument("--servers", default=SERVERS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有 mismatch → rc=1（未迁移仅 warn）")
    args = ap.parse_args(argv)
    res = audit(args.ext_dir, args.servers)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("extension/runtime audit: ext=%d servers=%d mapped=%d mismatches=%d unmigrated=%d" % (
            res["extensions"], res["server_entries"], res["mapped"],
            len(res["mismatches"]), len(res["unmigrated_servers"])))
        for mm in res["mismatches"]:
            print("  MISMATCH " + json.dumps(mm, ensure_ascii=False))
        for s in res["unmigrated_servers"]:
            print("  WARN unmigrated server: %s（可补 extensions/<name>/manifest.json）" % s)
    if args.strict and res["mismatches"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
