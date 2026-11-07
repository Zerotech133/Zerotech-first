# -*- coding: utf-8 -*-
"""批 6：测试侧回环 urlopen -> safe_urlopen（48 处 / 23 文件）。

背景
----
这些测试都是「自己起一个 127.0.0.1 服务，再去打它」，但用的是原生
urllib.request.urlopen。一旦环境里有 HTTP_PROXY/HTTPS_PROXY，urllib 会把
127.0.0.1 的请求交给代理（Windows 走注册表、Linux 走 no_proxy，默认都没有
对本机地址的豁免），表现为测试"莫名连不上自己刚起的服务"。

做法
----
1. 模块级插入 `from safe_urlopen import safe_urlopen`（各树就近取副本，
   副本内含 ProxyHandler({}) 回环豁免）。
   - tools/tests、tests/e2e 的树根不在 sys.path 上，额外插一行树根。
   - 插入点必须在各文件自己的 sys.path 调整**之后**，否则 import 直接失败。
2. `urllib.request.urlopen(` -> `safe_urlopen(`，断言每文件出现次数。
3. 替换后 `urllib` 相关 import 若已无引用，按 AST 判定删除（防 F401）。

本脚本只做机械改动 + 断言，不做语义判断；mock 约定逐个文件已核过：
这批文件没有一个 patch urlopen（portal/test_notification_decision.py 的
monkeypatch 目标是 `nd.urllib.request`，即被测模块内部调用，与本文件
自身的取数请求不冲突），所以换实现不会击穿任何桩。

用法
----
    python tools/research/patch_loopback_batch6.py --dry
    python tools/research/patch_loopback_batch6.py --apply
    python tools/research/patch_loopback_batch6.py --check
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

MARK = "from safe_urlopen import safe_urlopen"

_IMP_RE = re.compile(r"^[ \t]*import urllib\.(request|error|parse)[ \t]*$")

IMPORT_BLOCK = """
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402"""

# anchor 之后插入 block（block 首行为空行）
OPS: list[dict] = [
    # ── portal 根级：这些文件自己已把 portal/ 放进 sys.path ──────────
    {"file": "portal/test_legion_live_boot_p0.py",
     "after": 'sys.path.insert(0, str(ROOT / "portal"))', "block": IMPORT_BLOCK},
    {"file": "portal/test_legion_resolution_projection.py",
     "after": 'sys.path.insert(0, str(ROOT / "tools"))', "block": IMPORT_BLOCK},
    {"file": "portal/test_legion_room_api_live.py",
     "after": 'sys.path.insert(0, str(ROOT / "tools"))', "block": IMPORT_BLOCK},
    {"file": "portal/test_legion_room_list_end_to_end.py",
     "after": 'sys.path.insert(0, str(ROOT / "tools"))', "block": IMPORT_BLOCK},
    {"file": "portal/test_notification_decision.py",
     "after": "import push_admin_server as admin", "block": IMPORT_BLOCK},
    {"file": "portal/test_org_e2e.py",
     "after": "sys.path.insert(0, STATION)", "block": IMPORT_BLOCK},
    {"file": "portal/test_org_guard_p1.py",
     "after": "sys.path.insert(0, STATION)", "block": IMPORT_BLOCK},
    {"file": "portal/tests/test_d1_levels_ui.py",
     "after": "sys.path.insert(0, str(PORTAL))", "block": IMPORT_BLOCK},

    # ── quant_backtest：pytest 会把测试文件所在目录放进 sys.path[0] ──
    {"file": "quant_backtest/test_kg_events_e2e.py",
     "after": 'PORTAL = "http://127.0.0.1:8091"', "block": IMPORT_BLOCK},
    {"file": "quant_backtest/test_kg_events_integration.py",
     "after": 'PORTAL = "http://127.0.0.1:8091"', "block": IMPORT_BLOCK},
    {"file": "quant_backtest/test_kg_portal_e2e.py",
     "after": 'PORTAL = "http://127.0.0.1:8091"', "block": IMPORT_BLOCK},
    {"file": "quant_backtest/test_kg_portal_integration.py",
     "after": 'PORTAL = "http://127.0.0.1:8091"', "block": IMPORT_BLOCK},

    # ── agent_platform/tests：已插 WT/agent_platform ────────────────
    {"file": "agent_platform/tests/test_hq_acceptance.py",
     "after": 'sys.path.insert(0, str(WT / "agent_platform"))', "block": IMPORT_BLOCK},
    {"file": "agent_platform/tests/test_hq_agenda.py",
     "after": 'sys.path.insert(0, str(WT / "agent_platform"))', "block": IMPORT_BLOCK},
    {"file": "agent_platform/tests/test_hq_meeting.py",
     "after": 'sys.path.insert(0, str(WT / "agent_platform"))', "block": IMPORT_BLOCK},
    {"file": "agent_platform/tests/test_hq_p2.py",
     "after": 'sys.path.insert(0, str(WT / "agent_platform"))', "block": IMPORT_BLOCK},

    # ── tools/tests：树根 tools/ 不在 sys.path，需显式插 ────────────
    {"file": "tools/tests/test_card_review_page.py",
     "after": 'REPO = Path(__file__).resolve().parents[2]',
     "block": '\nsys.path.insert(0, str(REPO / "tools"))' + IMPORT_BLOCK},
    {"file": "tools/tests/test_export_conclusions.py",
     "after": 'REPO = Path(__file__).resolve().parents[2]',
     "block": '\nsys.path.insert(0, str(REPO / "tools"))' + IMPORT_BLOCK},
    {"file": "tools/tests/test_learning_dashboard.py",
     "after": 'REPO = Path(__file__).resolve().parents[2]',
     "block": '\nsys.path.insert(0, str(REPO / "tools"))' + IMPORT_BLOCK},
    {"file": "tools/tests/test_kb_qa_samples_v4.py",
     "after": 'sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))',
     "block": '\nsys.path.insert(0, str(ROOT / "tools"))' + IMPORT_BLOCK},
    {"file": "tools/tests/test_kb_search_frontend.py",
     "after": 'ROOT = Path(__file__).resolve().parents[2]',
     "block": '\nsys.path.insert(0, str(ROOT / "tools"))' + IMPORT_BLOCK},
    {"file": "tools/tests/test_screening_page.py",
     "after": 'ROOT = Path(__file__).resolve().parents[2]',
     "block": '\nsys.path.insert(0, str(ROOT / "tools"))' + IMPORT_BLOCK},

    # ── tests/e2e：树根也不在 path 上 ─────────────────────────────
    {"file": "tests/e2e/test_smoke_failure.py",
     "after": "REPO = pathlib.Path(__file__).resolve().parents[2]",
     "block": '\nsys.path.insert(0, str(REPO / "tools"))' + IMPORT_BLOCK},
]

OLD = "urllib.request.urlopen("
NEW = "safe_urlopen("


def detect_eol(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf and lf:
        raise SystemExit("混合行尾，拒绝自动改：crlf=%d bare_lf=%d" % (crlf, lf))
    return "\r\n" if crlf else "\n"


def urllib_attrs(tree: ast.AST) -> set[str]:
    """收集代码里实际用到的 urllib.<attr>（只看 Attribute 访问，不看 import 行）。"""
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "urllib":
            used.add(n.attr)
    return used


def drop_dead_urllib_imports(text: str, eol: str, log) -> str:
    """删除已无引用的 `import urllib.x`（含函数内局部 import），防 F401。

    两条保底，宁可留着也不制造 NameError：
      · 还有 `urllib.X` 被用（如 except 里的 urllib.error.URLError）时，
        只在"另有一条被用到的 urllib import 兜住 urllib 名字"的前提下才删；
      · 一条兜底都没有就不动。
    """
    lines = text.split(eol)
    used = urllib_attrs(ast.parse(text))
    idx_attrs = [(i, m.group(1)) for i, ln in enumerate(lines)
                 if (m := _IMP_RE.match(ln))]
    if not idx_attrs:
        return text

    if not used:                      # 完全不再引用 urllib：全删
        drop = {i for i, _ in idx_attrs}
    else:                             # 只删"没被用且有人兜底"的
        keepable = {a for _, a in idx_attrs if a in used}
        if not keepable:
            return text
        drop = {i for i, a in idx_attrs if a not in used}
    if not drop:
        return text

    for i in sorted(drop):
        log("    - 删除已无引用的 %s" % lines[i].strip())
    return eol.join(ln for i, ln in enumerate(lines) if i not in drop)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()

    root = pathlib.Path(__file__).resolve().parents[2]
    total_calls = 0
    changed = 0
    problems: list[str] = []

    def log(msg):
        print(msg)

    for op in OPS:
        path = root / op["file"]
        raw = path.read_bytes()
        eol = detect_eol(raw)
        text = raw.decode("utf-8")

        n_old = text.count(OLD)
        n_new = text.count(NEW)
        has_mark = MARK in text

        if args.check:
            if n_old or not has_mark:
                problems.append("%s: 未完成（old=%d mark=%s）" % (op["file"], n_old, has_mark))
            continue

        log("%s  (old=%d, 已插入=%s, eol=%r)" % (op["file"], n_old, has_mark, eol))
        body = text
        touched = False

        # 1) 插 import
        if not has_mark:
            anchor = op["after"].replace("\n", eol)
            n = body.count(anchor)
            if n != 1:
                raise SystemExit("%s: 锚点出现 %d 次（应为 1）：%r" % (op["file"], n, op["after"]))
            block = op["block"].replace("\n", eol)
            body = body.replace(anchor, anchor + block, 1)
            touched = True
            log("    + 插入 import")
        else:
            log("    = import 已存在，跳过")

        # 2) 换调用
        if n_old:
            body = body.replace(OLD, NEW)
            touched = True
            log("    * 替换 %d 处 urlopen -> safe_urlopen" % n_old)
            total_calls += n_old
        elif n_new:
            log("    = 调用已替换过（safe_urlopen %d 处）" % n_new)

        # 3) 清死 import
        if touched:
            before = body
            body = drop_dead_urllib_imports(body, eol, log)
            if body != before:
                pass

        if body == text:
            log("    . 无变化")
            continue

        try:
            ast.parse(body)
        except SyntaxError as exc:
            raise SystemExit("%s: 改后语法错误 %s" % (op["file"], exc))

        changed += 1
        if args.apply:
            path.write_bytes(body.encode("utf-8"))
            log("    > 已写入")
        else:
            log("    ~ dry-run，未写入")

    if args.check:
        if problems:
            for p in problems:
                log("[FAIL] " + p)
            return 1
        log("[OK] 23 个文件均已插入 import 且无残留 urllib.request.urlopen")
        return 0

    log("\n合计：%d 文件有变更，替换 %d 处调用" % (changed, total_calls))
    if not args.apply:
        log("（dry-run，磁盘未改；加 --apply 落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
