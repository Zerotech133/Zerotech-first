# -*- coding: utf-8 -*-
"""Tushare MCP token 轮换收口：安全输入 → 加密入库 → 渲染 → 审计。

用法（交互，token 不回显、不落 shell 历史）：
    python tools/secrets/rotate_token.py                 # 交互输入 + 校验 + 入库 + 渲染
    python tools/secrets/rotate_token.py --no-validate    # 跳过联网校验（离线）
    python tools/secrets/rotate_token.py --apply-config   # 同时把 .mcp.json 切到新 token
前置：你已在 https://tushare.pro/user/token 点「重新生成」拿到新 token。
边界：不重启任何服务；不改 jev-gate（那是 jev-eval/rotate_tushare_token.ps1 的职责）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

HERE = Path(__file__).resolve()
SECRETS_DIR = HERE.parent
REPO = HERE.parents[2]
VAULT = SECRETS_DIR / "vault.py"
RENDER = SECRETS_DIR / "render_mcp.py"
PREFLIGHT = SECRETS_DIR / "mcp_preflight.py"
VAULT_HOME = Path(os.environ.get("SECVAULT_HOME") or r"C:\项目\_vault")
STAGE_DIR = VAULT_HOME / "secrets" / "mcp"
STAGE = STAGE_DIR / "tushare_mcp_token"
CIPHER_REL = "mcp/tushare_mcp_token"
AUDIT = STAGE_DIR / "rotation_audit.jsonl"
API = "https://api.tushare.pro"


def token_shape_ok(tok: str) -> tuple[bool, str]:
    t = (tok or "").strip()
    if len(t) < 32:
        return False, "长度不足（Tushare token 通常 64 位十六进制）"
    if len(t) > 128:
        return False, "长度异常"
    return True, "ok"


def validate_online(token: str, timeout: int = 20) -> tuple[bool, str]:
    """调 trade_cal（低积分）验证；不打印 token。"""
    import urllib.request
    body = json.dumps({"api_name": "trade_cal", "token": token,
                       "params": {"exchange": "SSE"}, "fields": "cal_date"}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return False, "网络失败: " + type(e).__name__
    if int(d.get("code") or -1) != 0:
        return False, "接口拒绝: " + str(d.get("msg") or "")[:80]
    return True, "ok(rows=%d)" % len(((d.get("data") or {}).get("items") or []))


def _run(argv: list, timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(argv, capture_output=True, timeout=timeout, env=env)


def encrypt_into_vault(token: str) -> dict:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    STAGE.write_text(token, encoding="utf-8")
    try:
        p = _run([sys.executable, str(VAULT), "--home", str(VAULT_HOME),
                  "encrypt", "--no-skip-unchanged", "--recursive", str(STAGE)])
        if p.returncode != 0:
            return {"ok": False, "step": "encrypt", "detail": (p.stderr or p.stdout or b"").decode("utf-8", "replace")[:200]}
        # 解密回读校验
        d = _run([sys.executable, str(VAULT), "--home", str(VAULT_HOME), "decrypt", CIPHER_REL])
        if d.returncode != 0:
            return {"ok": False, "step": "decrypt", "detail": (d.stderr or b"").decode("utf-8", "replace")[:200]}
        got = d.stdout.rstrip(b"\r\n")
        want = token.encode("utf-8")
        if got != want:
            return {"ok": False, "step": "verify", "detail": "回读不一致"}
        return {"ok": True, "sha256_prefix": hashlib.sha256(want).hexdigest()[:16], "bytes": len(want)}
    finally:
        # 安全清理暂存明文：覆写后删除
        try:
            if STAGE.exists():
                n = STAGE.stat().st_size
                STAGE.write_bytes(b"\x00" * n)
                STAGE.unlink()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/rotate_token.py", exc)


def audit(record: dict) -> None:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    rec.update(record)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tushare MCP token 轮换（安全输入→保险库→渲染）")
    ap.add_argument("--no-validate", action="store_true", help="跳过 Tushare 在线校验")
    ap.add_argument("--apply-config", action="store_true", help="渲染后同时切换 .mcp.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    print("== Tushare MCP token 轮换 ==")
    print("前置：已在 https://tushare.pro/user/token 点『重新生成』（旧 token 会失效）。")
    tok = getpass.getpass("粘贴新 token（不回显，回车取消）: ").strip()
    if not tok:
        print("取消：未输入")
        return 1
    ok, why = token_shape_ok(tok)
    if not ok:
        print("FAIL: 形状校验:", why)
        return 2
    if a.dry_run:
        print("DRY-RUN: 形状校验通过，长度=%d；未校验/未入库" % len(tok))
        return 0
    if not a.no_validate:
        ok, why = validate_online(tok)
        if not ok:
            print("FAIL: 在线校验:", why, "（若网络不可用可加 --no-validate）")
            return 2
        print("OK: 在线校验通过", why)

    r = encrypt_into_vault(tok)
    if not r["ok"]:
        print("FAIL:", r)
        audit({"action": "rotate", "ok": False, "detail": r})
        return 2
    print("OK: 已加密入库（sha256 前缀 %s, %d bytes）；暂存明文已覆写删除" % (r["sha256_prefix"], r["bytes"]))

    p = _run([sys.executable, str(RENDER)])
    if p.returncode != 0:
        print("WARN: render_mcp 失败，请手动跑:", RENDER)
    else:
        print("OK: .mcp.local.json 已重渲染")

    if a.apply_config:
        q = _run([sys.executable, str(PREFLIGHT), "--apply"])
        print("apply-config rc=", q.returncode, "(0=OK)")

    p2 = _run([sys.executable, str(PREFLIGHT), "--check"])
    print("preflight:", "需 --apply（MCP 客户端重启前）" if p2.returncode == 1 else ("OK" if p2.returncode == 0 else "异常"))
    audit({"action": "rotate", "ok": True, "sha256_prefix": r["sha256_prefix"],
           "bytes": r["bytes"], "validate": (not a.no_validate), "apply_config": bool(a.apply_config)})
    print("下一步：① MCP 客户端重启前跑 `python tools/secrets/mcp_preflight.py --apply`；")
    print("       ② jev-gate 侧另跑 `powershell -File C:\\aios\\jev-eval\\rotate_tushare_token.ps1`（同一 Tushare 账号）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
