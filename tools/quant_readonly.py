# -*- coding: utf-8 -*-
"""P2 量化只读工具 v0：复用 tushare-http MCP（258 工具），只读、白名单、不落明文。

用法：
  python tools/quant_readonly.py --tool stock_basic --limit 3
  python tools/quant_readonly.py --tool daily --code 000001.SZ --start 20261020 --end 20261027
  python tools/quant_readonly.py --tool index_daily --code 000001.SH --limit 5 --json

约束：只调用读接口；token 从 SOPS 解密注入进程环境（不打印/不落盘）；失败 fail-visible。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "agent_platform"
VAULT_ENC = Path(r"C:\项目\_vault\secrets.enc\mcp\tushare_mcp_token.age")
TOKEN_ENV = "TUSHARE_MCP_TOKEN"
ALLOWED = ("stock_basic", "daily", "index_daily", "trade_cal", "daily_basic")
MAX_ROWS = 200


def _ensure_token() -> tuple[bool, str]:
    if os.environ.get(TOKEN_ENV):
        return True, ""
    if not VAULT_ENC.exists():
        return False, "vault_enc_missing"
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "tools" / "secrets" / "vault.py"),
             "decrypt", str(VAULT_ENC)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "vault_error:%s" % type(e).__name__
    tok = (r.stdout or "").strip()
    if r.returncode != 0 or not tok:
        return False, "vault_decrypt_failed"
    os.environ[TOKEN_ENV] = tok  # 仅进程内，不打印
    return True, ""


def _registry():
    if str(PLATFORM) not in sys.path:
        sys.path.insert(0, str(PLATFORM))
    from legion_mcp import registry  # noqa: E402
    return registry


def _call(tool: str, arguments: dict, timeout: float = 30.0) -> dict:
    ok, err = _ensure_token()
    if not ok:
        return {"ok": False, "error": err}
    if tool not in ALLOWED:
        return {"ok": False, "error": "tool_not_in_allowlist", "tool": tool,
                "allowed": list(ALLOWED)}
    return _registry().call("tushare-http", tool, arguments, timeout=timeout)


def _extract_rows(payload) -> list[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)][:MAX_ROWS]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "rows", "data", "result", "fields"):
        v = payload.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[:MAX_ROWS]
    # MCP content 包装：[{"type":"text","text":"{json}"}]
    content = payload.get("content")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                try:
                    obj = json.loads(c.get("text") or "")
                except ValueError:
                    continue
                rows = _extract_rows(obj)
                if rows:
                    return rows
    return []


def query(tool: str, code: str = "", start: str = "", end: str = "",
          limit: int = 20) -> dict:
    if tool not in ALLOWED:
        return {"ok": False, "error": "tool_not_in_allowlist", "allowed": list(ALLOWED)}
    limit = max(1, min(int(limit or 20), MAX_ROWS))
    args: dict = {"limit": limit}
    if code:
        args["ts_code"] = code
    if start:
        args["start_date"] = start
    if end:
        args["end_date"] = end
    rep = _call(tool, args)
    rows = _extract_rows(rep.get("payload"))
    if not rep.get("ok"):
        return {"ok": False, "error": rep.get("error") or "mcp_call_failed",
                "tool": tool, "detail": str(rep.get("error") or "")[:200]}
    return {"ok": True, "tool": tool, "arguments": args,
            "count": len(rows), "rows": rows, "read_only": True}


def to_markdown(result: dict) -> str:
    if not result.get("ok"):
        return "（查询失败：%s）" % result.get("error")
    rows = result.get("rows") or []
    if not rows:
        return "（无数据）"
    cols = list(rows[0].keys())[:8]
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "---|" * len(cols)]
    for r in rows[:20]:
        lines.append("| " + " | ".join(str(r.get(c, ""))[:40] for c in cols) + " |")
    lines.append("\n共 %d 行（只读；数据源 tushare-http MCP）" % result["count"])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量化只读查询（tushare MCP）")
    ap.add_argument("--tool", default="stock_basic", choices=ALLOWED)
    ap.add_argument("--code", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = query(args.tool, args.code, args.start, args.end, args.limit)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else to_markdown(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
