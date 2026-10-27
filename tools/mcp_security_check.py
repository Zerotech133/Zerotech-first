# -*- coding: utf-8 -*-
"""MCP 白名单安全自查（只读）：对齐 MCP Security Best Practices + ADR/安全策略。

检查项（默认 config=agent_platform/legion_mcp/servers.json）：
  1. 每个 server 的 risk 必须为 read-only（写权限默认拒绝）；
  2. http server 必须有 token_env（token 只从环境注入），URL 禁止携带 token/key 查询参数；
  3. token_env 必须是环境变量名（大写），且配置里不得出现明文 token 形态字符串；
  4. allow_tools="*" 视为 scope 未最小化 → 警告（不阻断）；
  5. 未知 transport / 缺 url 视为错误（fail-closed）。

用法：
  python -X utf8 tools/mcp_security_check.py
  python -X utf8 tools/mcp_security_check.py --json
  python -X utf8 tools/mcp_security_check.py --config <path>
退出码：0=无 error（warn 不影响）；1=有 error；2=配置缺失/解析失败。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(REPO, "agent_platform", "legion_mcp", "servers.json")

SECRET_PARAM_RE = re.compile(r"(?i)(token|key|apikey|access_token|secret)=")
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def _finding(server, check, ok, detail, level=None):
    return {
        "server": server,
        "check": check,
        "ok": bool(ok),
        "level": level or ("info" if ok else "error"),
        "detail": detail,
    }


def check_servers(config):
    """纯函数：返回 findings 列表（便于单测）。"""
    findings = []
    if not isinstance(config, dict):
        return [_finding("-", "schema", False, "配置不是 dict")]
    if not config.get("schema"):
        findings.append(_finding("-", "schema", False, "缺少 schema 字段", "warn"))
    servers = config.get("servers")
    if not isinstance(servers, dict) or not servers:
        findings.append(_finding("-", "servers", False, "servers 为空/不是 dict"))
        return findings

    raw = json.dumps(config, ensure_ascii=False)
    if SECRET_VALUE_RE.search(raw):
        findings.append(_finding("-", "no-plaintext-secret", False,
                                 "配置中出现明文 token 形态字符串"))
    else:
        findings.append(_finding("-", "no-plaintext-secret", True, "无明文 token 形态"))

    for name, s in servers.items():
        if not isinstance(s, dict):
            findings.append(_finding(name, "entry", False, "server 配置不是 dict"))
            continue
        risk = str(s.get("risk") or "").lower()
        findings.append(_finding(name, "risk-read-only", risk == "read-only",
                                 "risk=%s" % (risk or "<missing>")))
        transport = str(s.get("transport") or "stdio").lower()
        if transport == "http":
            url = str(s.get("url") or "")
            if not url:
                findings.append(_finding(name, "http-url", False, "http server 缺 url"))
            else:
                findings.append(_finding(name, "http-url-no-token", not SECRET_PARAM_RE.search(url),
                                         "url 含 token/key 查询参数" if SECRET_PARAM_RE.search(url)
                                         else "url 无敏感查询参数"))
                if not url.lower().startswith("https://"):
                    findings.append(_finding(name, "https", False, "http url 非 https", "warn"))
            tok_env = str(s.get("token_env") or "")
            findings.append(_finding(name, "token-env", bool(tok_env),
                                     "token_env=%s" % (tok_env or "<missing>")))
            if tok_env and not ENV_NAME_RE.match(tok_env):
                findings.append(_finding(name, "token-env-name", False,
                                         "token_env 不是大写环境变量名：%r" % tok_env))
            if s.get("enabled") and not tok_env:
                findings.append(_finding(name, "enabled-http-needs-token", False,
                                         "enabled http server 必须配 token_env"))
        elif transport == "stdio":
            findings.append(_finding(name, "stdio-script", bool(s.get("script")),
                                     "stdio server 缺 script" if not s.get("script") else "stdio ok"))
        else:
            findings.append(_finding(name, "transport", False,
                                     "未知 transport=%r（fail-closed）" % transport))
        if str(s.get("allow_tools") or "") == "*":
            findings.append(_finding(name, "scope-minimized", True,
                                     'allow_tools="*"（scope 未最小化，建议白名单）', "warn"))
    return findings


def summarize(findings):
    errors = [f for f in findings if not f["ok"] and f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    return {"ok": not errors, "errors": len(errors), "warnings": len(warns),
            "total_checks": len(findings), "findings": findings}


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP 白名单安全自查（只读）")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.config):
        print("config 不存在：%s" % args.config, file=sys.stderr)
        return 2
    try:
        with io.open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        print("config 解析失败：%s" % e, file=sys.stderr)
        return 2

    result = summarize(check_servers(config))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("MCP security check: errors=%d warnings=%d checks=%d" % (
            result["errors"], result["warnings"], result["total_checks"]))
        for f in result["findings"]:
            mark = "OK  " if f["ok"] else ("WARN" if f["level"] == "warn" else "FAIL")
            print("  [%s] %-18s %-22s %s" % (mark, f["server"], f["check"], f["detail"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
