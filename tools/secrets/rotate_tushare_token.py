# -*- coding: utf-8 -*-
"""R1 Tushare MCP token 轮换引导（A3 E1-E9 · 2026-10-27）

安全设计（与 tools/secrets/vault.py 同一红线）：
  · 只从 getpass 读新 token（不回显、不进 shell 历史、不落日志/聊天）；
  · 输出只打印 sha256 前 8 位/长度指纹，绝不打印 token 明文；
  · 不碰外部平台（重置 token 由老田在 tushare 控制台完成）；
  · 失败即停，明确回滚指引。

用法：
  C:\\项目\\py312\\python.exe tools/secrets/rotate_tushare_token.py --dry-run
  C:\\项目\\py312\\python.exe tools/secrets/rotate_tushare_token.py

流程（成功路径）：
  1) 交互输入两次新 token；
  2) 写入 <SECVAULT_HOME>/mcp/tushare_mcp_token；
  3) vault.py encrypt --recursive；vault.py verify --json；
  4) render_mcp.py；render_mcp.py --check（0 明文命中）；
  5) 打印指纹与下一步验收提示。
"""
from __future__ import annotations
from typing import NoReturn

import argparse
import getpass
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]


def _default_vault() -> Path:
    """定位 SECVAULT_HOME：优先自身/祖先下已存在的 _vault/secrets
    （主仓与 worktree 都能找到 C:\\项目\\_vault\\secrets）。"""
    for anc in [REPO] + list(REPO.parents):
        cand = anc / "_vault" / "secrets"
        if cand.is_dir():
            return cand.resolve()
    # 兜底：主仓在工作区下一级（C:\项目\老田的AI助手），worktree 再下一级
    return (REPO.parent / "_vault" / "secrets").resolve()


DEFAULT_VAULT = _default_vault()
TARGET_REL = Path("mcp") / "tushare_mcp_token"
TOOLS = REPO / "tools" / "secrets"
VAULT_PY = TOOLS / "vault.py"
RENDER_PY = TOOLS / "render_mcp.py"


def _fail(msg: str) -> "NoReturn":
    print("[rotate-tushare] FAIL: %s" % msg)
    print("[rollback] 旧 token 未删除前可重跑；若 vault/render 半途失败："
          "恢复 _vault/secrets/mcp/tushare_mcp_token 旧值（离线保险箱）→ "
          "vault.py encrypt --recursive → render_mcp.py → verify。")
    raise SystemExit(2)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print("[run] " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _vault_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = None
    try:
        import os
        env = os.environ.get("SECVAULT_HOME")
    except Exception:
        env = None
    return Path(env).expanduser().resolve() if env else DEFAULT_VAULT


def _find_bin_age() -> Path | None:
    for anc in [REPO] + list(REPO.parents):
        cand = anc / "bin" / "age.exe"
        if cand.is_file():
            return cand
    return None


def dry_run(vault_dir: Path) -> int:
    target = vault_dir / TARGET_REL
    print("[dry-run] repo       =", REPO)
    print("[dry-run] vault_dir  =", vault_dir, "(exists=%s)" % vault_dir.is_dir())
    print("[dry-run] target     =", target, "(exists=%s)" % target.is_file())
    if target.is_file():
        raw = target.read_text(encoding="utf-8", errors="replace").strip()
        print("[dry-run] old token len=%d sha8=%s (masked)"
              % (len(raw), hashlib.sha256(raw.encode()).hexdigest()[:8]))
    print("[dry-run] vault.py   =", VAULT_PY, "(exists=%s)" % VAULT_PY.is_file())
    print("[dry-run] render_mcp =", RENDER_PY, "(exists=%s)" % RENDER_PY.is_file())
    age = _find_bin_age()
    print("[dry-run] age binary =", age, "(exists=%s)" % (age is not None))
    print("[dry-run] steps: getpass x2 -> write target -> vault encrypt --recursive "
          "-> vault verify --json -> render_mcp -> render_mcp --check")
    if not VAULT_PY.is_file() or not RENDER_PY.is_file():
        print("[dry-run] WARN: tools/secrets 不完整，实跑会失败")
        return 1
    if age is None:
        print("[dry-run] WARN: 未找到 bin/age.exe；若在 worktree 运行，请改用主仓脚本或"
              " C:\\项目\\老田的AI助手\\tools\\secrets\\rotate_tushare_token.py")
    print("[dry-run] OK: 前置齐备，可执行正式轮换（需先在 tushare 控制台重置 token）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R1 Tushare MCP token 轮换引导（A3）")
    ap.add_argument("--dry-run", action="store_true", help="只检查前置，不读取/写入 token")
    ap.add_argument("--secrets-dir", default=None, help="覆盖 _vault/secrets 路径")
    ap.add_argument("--skip-render", action="store_true", help="只加密+校验，不重渲染 MCP 配置")
    args = ap.parse_args(argv)

    vault_dir = _vault_dir(args.secrets_dir)
    if args.dry_run:
        return dry_run(vault_dir)
    if not vault_dir.is_dir():
        _fail("vault 目录不存在: %s" % vault_dir)
    if not VAULT_PY.is_file() or not RENDER_PY.is_file():
        _fail("tools/secrets 工具缺失")

    token = getpass.getpass("new Tushare MCP token (不回显): ").strip()
    if not token:
        _fail("空输入")
    if len(token) < 20:
        _fail("token 长度异常（%d < 20），像不完整输入" % len(token))
    if any(ch.isspace() for ch in token):
        _fail("token 含空白字符，请重新从控制台复制")
    confirm = getpass.getpass("再次输入确认: ").strip()
    if token != confirm:
        _fail("两次输入不一致，已中止（未写入）")

    target = vault_dir / TARGET_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".bak-rotate")
        try:
            backup.write_text(target.read_text(encoding="utf-8", errors="replace"),
                              encoding="utf-8")
            print("[ok] 旧值快照:", backup, "(离线保留 24h；勿外发)")
        except OSError as exc:
            _fail("旧值快照失败: %s" % exc)
    target.write_text(token, encoding="utf-8")
    print("[ok] 新 token 已写入 %s (len=%d sha8=%s)"
          % (target, len(token), hashlib.sha256(token.encode()).hexdigest()[:8]))

    steps = [
        ([sys.executable, "-X", "utf8", str(VAULT_PY), "encrypt", str(vault_dir), "--recursive"], "encrypt"),
        ([sys.executable, "-X", "utf8", str(VAULT_PY), "verify", "--json"], "verify"),
    ]
    if not args.skip_render:
        steps += [
            ([sys.executable, "-X", "utf8", str(RENDER_PY)], "render"),
            ([sys.executable, "-X", "utf8", str(RENDER_PY), "--check"], "render-check"),
        ]

    for cmd, name in steps:
        r = _run(cmd)
        if name == "verify":
            try:
                data = json.loads((r.stdout or "").strip().splitlines()[-1])
            except ValueError:
                data = {}
            print("[%s] rc=%s ok=%s failures=%s file_count=%s"
                  % (name, r.returncode, data.get("ok"), data.get("failures"), data.get("file_count")))
        else:
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            print("[%s] rc=%s %s" % (name, r.returncode, tail[-1] if tail else ""))
        if r.returncode != 0:
            _fail("%s 失败（见上方输出）" % name)

    print("[done] R1 本地链路完成。下一步验收：")
    print("  1) 用新 token 跑一次只读 MCP 行情查询（失败则回滚旧值并复跑本脚本）")
    print("  2) 通过后再重启 MCP 客户端/相关服务（落盘≠生效）")
    print("  3) 在 ROTATION_FILLBACK_TEMPLATE.md 回填 完成时间 + len/sha8（不要写 token）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
