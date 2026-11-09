# -*- coding: utf-8 -*-
"""全仓 urlopen × 回环地址 静态扫描器。

判定逻辑（三件事，缺一不可）：
  1) 文件里有 urlopen 系调用（urlopen / safe_urlopen / auth_urlopen / _open ...）
  2) 该调用的目标 URL 能静态追溯到回环字面量（127.0.0.1 / localhost / ::1 / 0.0.0.0）
  3) 文件/调用所在模块没有代理旁路机制（ProxyHandler({}) / NO_PROXY / proxy_bypass / set_proxy）

输出：JSON + markdown 分层清单。

用法：python scan_loopback_urlopen.py <repo_root> [--out <dir>]
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", ".worktrees", "node_modules", "__pycache__", ".venv", "venv",
    "site-packages", ".pytest_cache", ".mypy_cache", "dist", "build",
}
SKIP_PATH_PARTS = ("/data/archive/", "\\data\\archive\\")

LOOPBACK_RE = re.compile(
    r"127\.0\.0\.1|localhost|\[?::1\]?|0\.0\.0\.0", re.IGNORECASE
)
# 只把 0.0.0.0 当"可疑"，不当回环（绑定地址不等于客户端目标）
LOOPBACK_STRICT_RE = re.compile(r"127\.0\.0\.1|localhost|\[::1\]", re.IGNORECASE)

BYPASS_TOKENS = (
    "ProxyHandler({})", "ProxyHandler()", "proxy_bypass", "no_proxy", "NO_PROXY",
    "_NO_PROXY_OPENER", "NO_PROXY_OPENER", "build_opener(urllib.request.ProxyHandler({})",
)
# 用 set_proxy / proxies={} 这类"显式清空代理"也算旁路
BYPASS_RE = re.compile(
    r"ProxyHandler\(\s*\{\s*\}\s*\)|proxy_bypass|no_proxy|NO_PROXY|"
    r"proxies\s*=\s*\{\s*\}|set_proxy\s*\(\s*None\s*\)",
    re.IGNORECASE,
)


# 已知内建回环旁路的包装器（实现在各自 safe_urlopen.py：ProxyHandler({})）
# auth_urlopen（legion_command/hermes_auth.py）2026-11-07 复核后加入：它的 5 个
# return 分支全部落到 safe_urlopen（enabled 关 / 非 hermes 域 / 取 cookie 失败 /
# 正常带 cookie / 401-403 重取），自己不碰 urlopen，属真旁路。
# 收录门槛：**每个分支都必须以 safe_urlopen 收口**，只加认证头而直连 urlopen 的
# 包装器不能进这张表（那只是把代理问题藏起来）。
SAFE_WRAPPERS = frozenset({"safe_urlopen", "auth_urlopen"})


def is_urlopen_call(node: ast.Call) -> tuple[bool, str]:
    f = node.func
    name = ""
    if isinstance(f, ast.Name):
        name = f.id
    elif isinstance(f, ast.Attribute):
        name = f.attr
    if "urlopen" in name.lower() or name in ("_open", "http_open", "auth_urlopen"):
        return True, name
    return False, name


MODULE_SCOPE = "<module>"


class Collector:
    """收集「作用域名 -> 变量 -> 源码片段」，供表达式溯源。

    作用域感知（2026-11-07 修）：原实现按变量名平铺收集，结果 `_get_price` 里的
    `req` 被 `_hub_call` 里的 `req` 覆盖 —— 打外网 hq.sinajs.cn 的那行被误判成
    回环 VICTIM。现在按限定名（`func.nested`）记录，解析时只沿**自身 + 词法外层
    + 模块级**查找，兄弟函数之间不再串名。
    """

    def __init__(self, src: str, tree: ast.AST):
        self.src = src
        self.assigns: dict[str, list[str]] = {}                 # 兼容旧接口：全局并集
        self.scoped: dict[tuple[str, str], list[str]] = {}      # (作用域, 名字) -> 片段
        self.line_scope: dict[int, str] = {}                    # 行号 -> 最内层作用域
        self._walk(tree, MODULE_SCOPE)

    def _walk(self, node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == MODULE_SCOPE else scope + "." + child.name
            ln = getattr(child, "lineno", None)
            if ln is not None:
                self.line_scope[ln] = inner      # 后写覆盖：更内层的作用域胜出
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                val = child.value
                if val is not None:
                    seg = ast.get_source_segment(self.src, val) or ""
                    targets = []
                    if isinstance(child, ast.Assign):
                        targets = [t.id for t in child.targets if isinstance(t, ast.Name)]
                    elif isinstance(child.target, ast.Name):
                        targets = [child.target.id]
                    for t in targets:
                        self.scoped.setdefault((inner, t), []).append(seg)
                        self.assigns.setdefault(t, []).append(seg)
            self._walk(child, inner)

    def scope_at(self, lineno: int) -> str:
        return self.line_scope.get(lineno, MODULE_SCOPE)

    def _chain(self, scope: str) -> list[str]:
        """由内到外的查找链：a.b.c -> [a.b.c, a.b, a, <module>]。"""
        if scope == MODULE_SCOPE:
            return [MODULE_SCOPE]
        parts = scope.split(".")
        out = [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
        out.append(MODULE_SCOPE)
        return out

    def _lookup(self, ident: str, scope: str) -> list[str]:
        found: list[str] = []
        for s in self._chain(scope):
            found.extend(self.scoped.get((s, ident), []))
            if found:
                break          # 内层命中就不再向外找，避免父层同名覆盖
        return found

    def resolve(self, seg: str, depth: int = 4, seen: set | None = None,
                scope: str = MODULE_SCOPE) -> str:
        """把表达式片段里的标识符 BFS 展开成源码集合（有界，线性）。

        用 visited 去重 + 深度上限 + 片段数上限，避免指数级爆炸
        （自己踩过：递归版在 assign 多的大文件上会直接自爆）。
        """
        visited: set[str] = set(seen or ())
        queue: list[tuple[str, int]] = [(seg, depth)]
        parts: list[str] = []
        budget = 400
        while queue and budget > 0:
            cur, d = queue.pop(0)
            parts.append(cur)
            budget -= 1
            if d <= 0:
                continue
            for ident in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", cur)):
                if ident in visited:
                    continue
                visited.add(ident)
                for v in self._lookup(ident, scope)[:3]:
                    queue.append((v, d - 1))
        return "\n".join(parts)


def scan_file(p: Path, root: Path) -> list[dict]:
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if "urlopen" not in src and "_open(" not in src:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    col = Collector(src, tree)
    file_has_bypass = bool(BYPASS_RE.search(src))
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        ok, cname = is_urlopen_call(n)
        if not ok:
            continue
        arg_seg = ""
        if n.args:
            arg_seg = ast.get_source_segment(src, n.args[0]) or ""
        elif n.keywords:
            for kw in n.keywords:
                if kw.arg in ("url", "req", "request", "fullurl"):
                    arg_seg = ast.get_source_segment(src, kw.value) or ""
                    break
        expanded = col.resolve(arg_seg, scope=col.scope_at(n.lineno)) if arg_seg else ""
        strict = bool(LOOPBACK_STRICT_RE.search(expanded))
        loose = bool(LOOPBACK_RE.search(expanded))
        hits.append({
            "file": str(p.relative_to(root)).replace("\\", "/"),
            "line": n.lineno,
            "call": cname,
            "arg": arg_seg[:160],
            "expanded_hit": (LOOPBACK_STRICT_RE.search(expanded) or LOOPBACK_RE.search(expanded)).group(0) if loose else "",
            "loopback_strict": strict,
            "loopback_loose": loose,
            "file_has_bypass": file_has_bypass,
            "arg_inline_bypass": bool(BYPASS_RE.search(arg_seg or "")),
        })
    return hits


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    outdir = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--out" else root / "deliverables"
    hits: list[dict] = []
    nfiles = 0
    # 只扫 git 跟踪文件：仓库里有 28 万个未跟踪 .py（venv/依赖树），
    # 全盘 os.walk + AST 会把进程打死（实测 SIGTERM）。"全仓"按跟踪文件界定。
    import subprocess
    listed = subprocess.run(
        ["git", "ls-files", "-z", "*.py"], cwd=str(root),
        capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    for rel in listed:
        if not rel:
            continue
        if any(s.strip("/") in rel.replace("\\", "/") for s in SKIP_PATH_PARTS):
            continue
        p = root / rel
        if not p.is_file():
            continue
        nfiles += 1
        hits.extend(scan_file(p, root))

    for h in hits:
        # 已知内建回环旁路的包装器：调用它们即等于已有旁路（实现在各自 safe_urlopen.py）。
        # 不认这一条，修好共享入口后所有引用文件仍会被报成 VICTIM —— 扫描结论会失真。
        wrapper = h.get("call") in SAFE_WRAPPERS
        if h["loopback_strict"] and not (h["file_has_bypass"] or h["arg_inline_bypass"] or wrapper):
            h["verdict"] = "VICTIM"          # 确认受害：回环 + 无旁路
        elif h["loopback_loose"] and not (h["file_has_bypass"] or h["arg_inline_bypass"] or wrapper):
            h["verdict"] = "SUSPECT"         # 0.0.0.0 或间接，可疑
        elif h["loopback_strict"] or h["loopback_loose"]:
            h["verdict"] = "GUARDED"         # 回环但已有旁路
        else:
            h["verdict"] = "EXTERNAL"        # 外网目标，走代理是本意

    summary = {
        "root": str(root),
        "py_files_scanned": nfiles,
        "files_with_urlopen": len({h["file"] for h in hits}),
        "calls_total": len(hits),
        "by_verdict": {},
        "victim_files": sorted({h["file"] for h in hits if h["verdict"] == "VICTIM"}),
        "suspect_files": sorted({h["file"] for h in hits if h["verdict"] == "SUSPECT"}),
    }
    for v in ("VICTIM", "SUSPECT", "GUARDED", "EXTERNAL"):
        summary["by_verdict"][v] = sum(1 for h in hits if h["verdict"] == v)

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "loopback_urlopen_scan.json").write_text(
        json.dumps({"summary": summary, "hits": hits}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n== VICTIM (确认受害) ==")
    seen = set()
    for h in hits:
        if h["verdict"] == "VICTIM" and h["file"] not in seen:
            seen.add(h["file"])
            print("  %-70s L%-5d %s(%s)" % (h["file"], h["line"], h["call"], (h["arg"] or "").replace("\n", " ")[:70]))
    print("\n== SUSPECT ==")
    seen = set()
    for h in hits:
        if h["verdict"] == "SUSPECT" and h["file"] not in seen:
            seen.add(h["file"])
            print("  %-70s L%-5d %s(%s)" % (h["file"], h["line"], h["call"], (h["arg"] or "").replace("\n", " ")[:70]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
