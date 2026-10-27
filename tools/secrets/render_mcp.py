#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_mcp.py —— 从 ``.mcp.json.template`` + 密文 token 渲染 ``.mcp.local.json``。

工单 K-DATA-20261027-SECVAULT（L1）：``.mcp.json`` 里 tushareMcp.url 曾含明文
Tushare MCP token。本轮把 token 移出模板/配置文件，只保留占位符
``${TUSHARE_MCP_TOKEN}``；真实 token 仅存在于 ``secrets.enc`` 密文与运行时渲染出的
本地文件（gitignored、ACL 收紧）中。

用法::

    python tools/secrets/render_mcp.py            # 渲染两份 .mcp.local.json
    python tools/secrets/render_mcp.py --check    # 只校验（不写文件、不打印 token）

红线：任何输出都不打印 token 明文；只报路径/sha256/长度/masked。
"""

from __future__ import annotations

import subprocess
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault  # noqa: E402
import logging

PLACEHOLDER = "${TUSHARE_MCP_TOKEN}"
TOKEN_CIPHER_REL = "mcp/tushare_mcp_token.age"
TEMPLATE_REL = ".mcp.json.template"
PARENT_TEMPLATE_REL = "tools/secrets/templates/parent.mcp.json.template"


def resolve_main_repo(repo_root: Path) -> Path:
    """worktree 布局下，主仓根 = 本 worktree 的上级仓根。"""
    env = os.environ.get("SECVAULT_MAIN_REPO")
    if env:
        return Path(env).expanduser().resolve()
    repo_root = repo_root.resolve()
    if repo_root.parent.name == ".worktrees":
        return repo_root.parents[1]
    return repo_root


def _decrypt_token(home: Path, key_file: Path, token_cipher: Path | None) -> str:
    if token_cipher is None:
        token_cipher = home / vault.ENC_DIRNAME / TOKEN_CIPHER_REL
    token_cipher = Path(token_cipher)
    if not token_cipher.is_file():
        raise vault.VaultError(f"token 密文不存在：{token_cipher}")
    plain = vault.decrypt_bytes(home, key_file, token_cipher.read_bytes())[1]
    token = plain.decode("utf-8").strip()
    if not token:
        raise vault.VaultError("token 密文解密为空（fail-closed）")
    return token


def _render_template(template_text: str, token: str) -> str:
    if PLACEHOLDER not in template_text:
        raise vault.VaultError(f"模板缺少占位符 {PLACEHOLDER}（fail-closed）")
    return template_text.replace(PLACEHOLDER, token)


def _read_exact(path: Path) -> str:
    """按原样读取（不做 universal-newline 转换），保证渲染保留模板的 CRLF/LF 约定。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/render_mcp.py", exc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _count_occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    n = 0
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i < 0:
            return n
        n += 1
        start = i + len(needle)


def _git_ignored(path: Path) -> bool | None:
    """dest 若位于 git 仓库内，判断是否被 ignore；不在仓库返回 None。"""
    d = path.parent
    if not (d / ".git").exists():
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(d), "check-ignore", "-q", str(path.name)],
            capture_output=True,
            timeout=60,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def plan_targets(repo_root: Path) -> list[dict]:
    """渲染目标：① 本仓根 ② 主仓父目录（C:\\项目）。

    ``config`` 指向本轮收编的真实 ``.mcp.json``（主仓根 / 父目录），供 --check
    验证「明文 token 命中数 = 0」；``dest`` 是 gitignored 的本地渲染文件。
    """
    repo_root = repo_root.resolve()
    main_repo = resolve_main_repo(repo_root)
    parent_dir = Path(os.environ.get("SECVAULT_PARENT_DIR") or main_repo.parent).resolve()
    targets = [
        {
            "kind": "repo",
            "template": repo_root / TEMPLATE_REL,
            "dest": repo_root / ".mcp.local.json",
            "config": main_repo / ".mcp.json",
        },
        {
            "kind": "parent-dir",
            "template": repo_root / PARENT_TEMPLATE_REL,
            "dest": parent_dir / ".mcp.local.json",
            "config": parent_dir / ".mcp.json",
        },
    ]
    return targets


def cmd_check(home: Path, key_file: Path, repo_root: Path, token_cipher: Path | None) -> dict:
    token = _decrypt_token(home, key_file, token_cipher)
    result: dict = {
        "ok": True,
        "token": f"<masked len={len(token)}>",
        "checks": [],
    }
    for t in plan_targets(repo_root):
        item: dict = {"kind": t["kind"], "template": str(t["template"]), "dest": str(t["dest"])}
        problems: list[str] = []
        if not t["template"].is_file():
            problems.append("template-missing")
            tpl_text = None
        else:
            tpl_text = _read_exact(t["template"])
            if PLACEHOLDER not in tpl_text:
                problems.append("template-placeholder-missing")
        expected = _render_template(tpl_text, token) if tpl_text is not None else None
        if t["dest"].is_file() and expected is not None:
            got = _read_exact(t["dest"])
            item["rendered_matches"] = got == expected
            if got != expected:
                problems.append("rendered-mismatch")
        else:
            item["rendered_matches"] = None
            item["rendered"] = "absent(optional)"
        # 真实配置：token 明文命中数必须为 0
        if t["config"].is_file():
            cfg = _read_exact(t["config"])
            item["config_path"] = str(t["config"])
            item["config_token_hits"] = _count_occurrences(cfg, token)
            item["config_has_placeholder"] = PLACEHOLDER in cfg
            if item["config_token_hits"]:
                problems.append("plaintext-token-present")
        else:
            item["config_path"] = None
            item["config_token_hits"] = 0
        item["problems"] = problems
        item["ok"] = not problems
        result["checks"].append(item)
        if problems:
            result["ok"] = False
    return result


def cmd_render(
    home: Path, key_file: Path, repo_root: Path, token_cipher: Path | None, dry_run: bool
) -> dict:
    token = _decrypt_token(home, key_file, token_cipher)
    out: dict = {"ok": True, "token": f"<masked len={len(token)}>", "rendered": []}
    for t in plan_targets(repo_root):
        if not t["template"].is_file():
            raise vault.VaultError(f"模板不存在：{t['template']}")
        expected = _render_template(_read_exact(t["template"]), token)
        entry = {"kind": t["kind"], "dest": str(t["dest"])}
        if dry_run:
            entry["status"] = "dry-run"
        else:
            _atomic_write(t["dest"], expected)
            entry["status"] = "written"
            entry["sha256"] = _sha256_text(expected)
            entry["acl"] = vault.tighten_acl(t["dest"])
        out["rendered"].append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/secrets/render_mcp.py", exc)
    ap = argparse.ArgumentParser(prog="render_mcp.py", description="渲染 .mcp.local.json（不打印 token）")
    ap.add_argument("--check", action="store_true", help="只校验模板/密文/渲染一致性，不写文件")
    ap.add_argument("--dry-run", action="store_true", help="渲染但不写盘")
    ap.add_argument("--home", default=None, help="保险库根（默认 $SECVAULT_HOME 或 C:\\项目\\_vault）")
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--repo", default=None, help="仓库根（默认本脚本所在仓库）")
    ap.add_argument("--token-cipher", default=None, help="token 密文路径（默认 <home>/secrets.enc/mcp/tushare_mcp_token.age）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    home = vault.resolve_home(args.home)
    key_file = vault.resolve_key_file(home, args.key_file)
    repo_root = Path(args.repo).expanduser().resolve() if args.repo else vault.repo_root_default()
    token_cipher = Path(args.token_cipher).expanduser().resolve() if args.token_cipher else None
    try:
        if args.check:
            result = cmd_check(home, key_file, repo_root, token_cipher)
        else:
            result = cmd_render(home, key_file, repo_root, token_cipher, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[render_mcp] ok={result['ok']} token={result['token']}")
            if args.check:
                for c in result["checks"]:
                    print(
                        f"  [{c['kind']}] rendered={c.get('rendered_matches')} "
                        f"config={c.get('config_path')} plaintext_token_hits={c.get('config_token_hits')} "
                        f"placeholder_in_config={c.get('config_has_placeholder')} "
                        f"problems={c.get('problems')}"
                    )
            else:
                for r in result["rendered"]:
                    print(f"  [{r['kind']}] {r['status']} -> {r['dest']} sha256={r.get('sha256')}")
        return 0 if result["ok"] else 1
    except vault.VaultError as e:
        print(f"[render_mcp] FAIL-CLOSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
