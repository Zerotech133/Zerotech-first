#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X5 Tushare token 轮换 · 轮换后一键验收（P0-3，2026-10-30）。

只读校验（不打印 token 明文，只输出 masked sha8/长度）：
  1. vault verify --json → 全部 ok？
  2. render_mcp --check → rc 0？
  3. 解密 mcp/tushare_mcp_token → 长度/sha8（不回显）
  4. （可选）8796 MCP 端点 GET /api/zhaoyun/mcp → enabled/tools（服务未起则 skip）

用法：
  python tools/secrets/post_rotation_check.py            # 人类可读
  python tools/secrets/post_rotation_check.py --json
退出码：0=全部通过（skip 不算失败）；1=有失败。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = sys.executable
VAULT = HERE / "vault.py"
RENDER = HERE / "render_mcp.py"


def _run(cmd, timeout=120):
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def check_vault():
    rc, out, err = _run([PY, str(VAULT), "verify", "--json"])
    try:
        data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        data = {}
    return {"name": "vault_verify", "ok": rc == 0, "rc": rc,
            "detail": (data.get("summary") or data.get("ok") or out.strip()[:120]),
            "stderr": err.strip()[:160]}


def check_render():
    rc, out, err = _run([PY, str(RENDER), "--check"])
    return {"name": "render_mcp_check", "ok": rc == 0, "rc": rc,
            "detail": (out.strip() or err.strip())[:160]}


def check_token_masked():
    rc, out, err = _run([PY, str(VAULT), "decrypt", "mcp/tushare_mcp_token"])
    tok = (out or "").strip()
    if rc != 0 or not tok:
        return {"name": "token_in_vault", "ok": False, "rc": rc,
                "detail": "decrypt 失败或无值", "stderr": err.strip()[:160]}
    sha8 = hashlib.sha256(tok.encode()).hexdigest()[:8]
    return {"name": "token_in_vault", "ok": True, "len": len(tok), "sha8": sha8,
            "detail": "len=%d sha8=%s（masked）" % (len(tok), sha8)}


def check_mcp_http(timeout=3.0):
    url = "http://127.0.0.1:8796/api/zhaoyun/mcp"
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        items = data.get("servers") or data.get("items") or []
        enabled = [s for s in items if s.get("enabled") or s.get("name") == "tushare-http"]
        return {"name": "mcp_8796", "ok": True, "detail": "enabled=%d items=%d"
                % (len(enabled), len(items))}
    except Exception as exc:  # noqa: BLE001
        return {"name": "mcp_8796", "ok": None, "skipped": True,
                "detail": "8796 未起/不可达（skip）：%s" % str(exc)[:80]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="X5 Tushare token 轮换后验收（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-http", action="store_true", help="跳过 8796 检查")
    args = ap.parse_args(argv)

    results = [check_vault(), check_render(), check_token_masked()]
    if not args.no_http:
        results.append(check_mcp_http())
    failed = [r for r in results if r.get("ok") is False]
    payload = {"ok": not failed, "results": results}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = "✓" if r.get("ok") else ("-" if r.get("skipped") else "✗")
            print("  %s %-18s %s" % (mark, r["name"], r.get("detail", "")))
        print("结论：%s" % ("全部通过" if not failed else "存在失败项（见上）"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
