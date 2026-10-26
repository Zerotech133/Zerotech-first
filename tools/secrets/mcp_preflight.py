# -*- coding: utf-8 -*-
"""MCP 配置预检/应用/回滚：解决「.mcp.json 只留占位符、重启前必须渲染」的运维缺口。

用法：
    python tools/secrets/mcp_preflight.py --check      # 只看状态（0=已解析 / 1=需渲染 / 2=异常）
    python tools/secrets/mcp_preflight.py --apply      # 渲染并把 .mcp.local.json 落到 .mcp.json（先备份到 _vault）
    python tools/secrets/mcp_preflight.py --revert     # 恢复占位符版 .mcp.json（安全默认）
都不打印 token。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SECRETS_DIR = HERE.parent


def _main_repo(start: Path) -> Path:
    """主工作树根（linked worktree 内也解析回主仓）。"""
    for cand in (start, *start.parents):
        g = cand / ".git"
        try:
            if g.is_dir():
                return cand
            if g.is_file():
                txt = g.read_text(encoding="utf-8", errors="replace").strip()
                if txt.lower().startswith("gitdir:"):
                    raw = txt.split(":", 1)[1].strip()
                    gd = Path(raw)
                    if not gd.is_absolute():
                        gd = (cand / gd).resolve()
                    try:
                        return gd.parents[2]
                    except IndexError:
                        return cand
                return cand
        except OSError:
            continue
    return start.parents[2]


REPO = _main_repo(HERE)
WORKSPACE = REPO.parent
VAULT_HOME = Path(os.environ.get("SECVAULT_HOME") or r"C:\项目\_vault")
ARCHIVE = VAULT_HOME / "secrets_archive"
PLACEHOLDER = "${TUSHARE_MCP_TOKEN}"
SERVERS = {"repo": REPO / ".mcp.json", "parent": WORKSPACE / ".mcp.json"}
TEMPLATES = {"repo": REPO / ".mcp.json.template",
             "parent": SECRETS_DIR / "templates" / "parent.mcp.json.template"}


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def inspect_config(path: Path) -> dict:
    """不打印 token；只返回结构状态。"""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {"path": str(path), "exists": path.exists(), "valid_json": False, "state": "missing_or_bad"}
    url = str(((data.get("mcpServers") or {}).get("tushareMcp") or {}).get("url") or "")
    placeholder = PLACEHOLDER in url
    token_in_url = "token=" in url and not placeholder
    return {"path": str(path), "exists": True, "valid_json": True,
            "placeholder": placeholder, "token_present": token_in_url,
            "state": "placeholder" if placeholder else ("resolved" if token_in_url else "unknown")}


def render_check() -> dict:
    """调用 render_mcp.py --check --json（权威校验：模板/密文/渲染一致）。"""
    p = subprocess.run([sys.executable, str(SECRETS_DIR / "render_mcp.py"), "--check", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        return {"rc": p.returncode, "result": json.loads(p.stdout or "{}")}
    except Exception:  # noqa: BLE001
        return {"rc": p.returncode, "result": None, "stderr": (p.stderr or "")[:300]}


def status() -> dict:
    cfgs = {k: inspect_config(v) for k, v in SERVERS.items()}
    rc = render_check()
    rendered = {}
    for k, cfg_path in SERVERS.items():
        local = cfg_path.parent / ".mcp.local.json"
        rendered[k] = {"local_exists": local.exists(), "local_bytes": local.stat().st_size if local.exists() else 0}
    needs = [k for k, v in cfgs.items() if v.get("state") == "placeholder"]
    bad = [k for k, v in cfgs.items() if not v.get("valid_json")]
    return {"tool": "mcp_preflight", "configs": cfgs, "render_check_rc": rc["rc"],
            "rendered": rendered, "needs_apply": needs, "bad": bad,
            "code": 2 if bad or rc["rc"] not in (0, 1) else (1 if needs else 0)}


def apply() -> int:
    """渲染 → 备份旧配置 → 写 .mcp.json（两处）→ 复检。"""
    p = subprocess.run([sys.executable, str(SECRETS_DIR / "render_mcp.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if p.returncode != 0:
        print("FAIL: render_mcp 失败 rc=", p.returncode)
        return 2
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    bak = ARCHIVE / ("mcp_preflight_" + stamp)
    bak.mkdir(parents=True, exist_ok=True)
    for k, cfg in SERVERS.items():
        local = cfg.parent / ".mcp.local.json"
        if not local.is_file():
            print("FAIL: 缺渲染件", local)
            return 2
        if cfg.exists():
            shutil.copy2(cfg, bak / (k + ".mcp.json.before"))
        data = json.loads(local.read_text(encoding="utf-8"))
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        chk = inspect_config(cfg)
        if chk.get("state") != "resolved":
            print("FAIL: 写入后状态异常", k, chk.get("state"))
            return 2
        print("OK: %s -> resolved（备份 %s）" % (cfg, bak.name))
    return 0


def revert() -> int:
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    bak = ARCHIVE / ("mcp_preflight_revert_" + stamp)
    bak.mkdir(parents=True, exist_ok=True)
    for k, cfg in SERVERS.items():
        tpl = TEMPLATES.get(k)
        if tpl and tpl.is_file():
            if cfg.exists():
                shutil.copy2(cfg, bak / (k + ".mcp.json.before"))
            shutil.copy2(tpl, cfg)
            print("OK: 回退占位符 ->", cfg)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MCP 配置预检/应用/回滚（不打印 token）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只检查（默认）")
    g.add_argument("--apply", action="store_true", help="渲染并写入 .mcp.json")
    g.add_argument("--revert", action="store_true", help="恢复占位符版")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.apply:
        rc = apply()
        return rc if rc != 0 else (0 if status()["code"] == 0 else 1)
    if a.revert:
        revert()
        return 0
    st = status()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        for k, v in st["configs"].items():
            print("%-6s %-9s placeholder=%s token_present=%s" % (k, v.get("state"), v.get("placeholder"), v.get("token_present")))
        print("render_check_rc=", st["render_check_rc"], "needs_apply=", st["needs_apply"])
    return st["code"]


if __name__ == "__main__":
    raise SystemExit(main())
