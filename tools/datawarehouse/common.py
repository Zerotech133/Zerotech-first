# -*- coding: utf-8 -*-
"""数据仓治理工具公共层（只读）。

设计纪律：
- 全部只读：不写数据仓、不触发任务、不连需要凭据的服务；
- 路径真源：REPO = 主仓根（worktree 内运行时也能解析到主仓），DATA = 唯一 live 数据根；
- 退出码约定：0=ok / 1=warn / 2=not_ready。
"""
from __future__ import annotations

import sys
import hashlib
import json
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent


def _git_common_repo() -> Path:
    """返回**主工作树**根（linked worktree 内也解析回主仓，与 tools/_repo_paths 同口径）。"""
    p = TOOLS_DIR
    for cand in (p, *p.parents):
        g = cand / ".git"
        try:
            if g.is_dir():
                return cand
            if g.is_file():
                txt = g.read_text(encoding="utf-8", errors="replace").strip()
                if txt.lower().startswith("gitdir:"):
                    raw = txt.split(":", 1)[1].strip()
                    gitdir = Path(raw)
                    if not gitdir.is_absolute():
                        gitdir = (cand / gitdir).resolve()
                    # <main>/.git/worktrees/<name> → parents[2] 即 <main>
                    try:
                        return gitdir.parents[2]
                    except IndexError:
                        return cand
                return cand
        except OSError:
            continue
    # 兜底：按目录层级找 agent_platform（主仓必有）
    for cand in (p, *p.parents):
        if (cand / "agent_platform").is_dir():
            return cand
    return p.parents[1]


REPO = _git_common_repo()
WORKSPACE = REPO.parent
DATA = REPO / "quant_backtest" / "data"
CATALOG = DATA / "catalog.json"
REGISTRY = DATA / "warehouse_registry.json"
MAIN_REPO = Path(r"C:\项目\老田的AI助手")

EXIT_OK, EXIT_WARN, EXIT_NOT_READY = 0, 1, 2


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def sha256_file(path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_print(text: str) -> None:
    """非 UTF-8 控制台（GBK/CP936）也要能输出：优先原样，失败则按 UTF-8 字节兜底。"""
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            print(text.encode("ascii", "replace").decode("ascii"))


def emit(payload: dict, as_json: bool = False, title: str = "") -> int:
    if as_json:
        _safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if title:
            _safe_print("== %s ==" % title)
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                _safe_print("%s: %s" % (k, json.dumps(v, ensure_ascii=False)[:600]))
            else:
                _safe_print("%s: %s" % (k, v))
    code = payload.get("code", EXIT_OK)
    return int(code)


def rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO.resolve())).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return str(path)
