# -*- coding: utf-8 -*-
"""把 tools/tests/test_keepalive_budget.py 登记进 fast 门的 tools_key 冻结清单。

工单：2026-11-07 K-ORG-20261107-WATCHDOG（8095 补看护 + 看护预算）
tools_key 有 5 处冻结点，必须同批改净，否则 B1/A5STRICT/ORGGATE 三处守卫
会各自报「文件数不符」：

  1. tools/test_gate.ps1              'tools_key' = @( ... ) 数组（保 UTF-8 BOM + CRLF）
  2. tools/tests/test_test_gate_b1.py TOOLS_KEY 集合 + len(TOOLS_KEY)==19 + all_files==94 + 说明串
  3. tools/tests/test_test_gate_a5strict.py  ("tools_key", 19)
  4. tools/tests/test_test_gate_org_gate.py  ("tools_key", 19)

用法：
  python tools/research/patch_keepalivegate.py --dry     # 只报告将要改什么
  python tools/research/patch_keepalivegate.py --apply   # 写盘（幂等）
  python tools/research/patch_keepalivegate.py --check   # 断言已落盘
"""
import argparse
import sys

REPO = "C:/项目/老田的AI助手"
ENTRY = "tools/tests/test_keepalive_budget.py"
TAG = "看护预算契约（2026-11-07 K-ORG-20261107-WATCHDOG，+1）"

PS1 = REPO + "/tools/test_gate.ps1"
B1 = REPO + "/tools/tests/test_test_gate_b1.py"
A5 = REPO + "/tools/tests/test_test_gate_a5strict.py"
OG = REPO + "/tools/tests/test_test_gate_org_gate.py"


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw, raw.decode("utf-8-sig")


def _write(path, raw, text):
    bom = b"\xef\xbb\xbf" if raw[:3] == b"\xef\xbb\xbf" else b""
    with open(path, "wb") as f:
        f.write(bom + text.encode("utf-8"))


def _nl(text):
    return "\r\n" if "\r\n" in text else "\n"


def plan_ps1(text):
    """在 tools_key 数组尾部插一行。
    两个坑，都是 PowerShell 数组字面量的硬语法：
      a) 数组**最后一项不能带尾逗号**（`@(1,2,)` 报「',' 后面缺少表达式」），
         所以原文件最后一项原本无逗号，新插的那项也不许加；
      b) 插入点是原最后一项，必须顺手把它的逗号补上，否则两项会粘连。
    因此这里给两个候选锚：good（已修正的终态）与 bad（曾误加尾逗号的中间态，
    幂等修复用）。"""
    nl = _nl(text)
    good = ("        'tools/tests/test_scan_secrets_fp.py'," + nl
            + "        # ---- " + TAG + " ----" + nl
            + "        '" + ENTRY + "'" + nl
            + "    )")
    bad = ("        'tools/tests/test_scan_secrets_fp.py'," + nl
           + "        # ---- " + TAG + " ----" + nl
           + "        '" + ENTRY + "'," + nl
           + "    )")
    orig = "        'tools/tests/test_scan_secrets_fp.py'" + nl + "    )"
    return [(orig, good), (bad, good)]


def plan_b1(text):
    nl = _nl(text)
    edits = []
    edits.append((
        '    "tools/tests/test_scan_secrets_fp.py",' + nl + "}",
        '    "tools/tests/test_scan_secrets_fp.py",' + nl
        + "    # ---- " + TAG + " ----" + nl
        + '    "' + ENTRY + '",' + nl + "}",
    ))
    edits.append(("len(TOOLS_KEY) == 19", "len(TOOLS_KEY) == 20"))
    edits.append(("len(all_files) == 94", "len(all_files) == 95"))
    edits.append((
        "+凭据扫描回归(1) 应为 94 文件",
        "+凭据扫描回归(1)+看护预算契约(1) 应为 95 文件",
    ))
    return edits


def plan_count(text):
    return [('("tools_key", 19)', '("tools_key", 20)')]


TARGETS = [
    (PS1, [("ps1-array", plan_ps1)]),
    (B1, [("b1-key", plan_b1)]),
    (A5, [("a5-count", plan_count)]),
    (OG, [("og-count", plan_count)]),
]


def snapshot():
    out = {}
    for path in (PS1, B1, A5, OG):
        out[path] = _read(path)
    return out


def apply_one(path, raw, text, plan, mode):
    """返回 (逐条报告, 终态文本, 未达成的期望串)。
    判定口径是**终态**而非逐条命中：ps1 给了 good/bad 两个候选锚，必有一个
    是 MISS(0)（文件只可能处于其中一种形态），拿逐条命中当失败会误报。"""
    results = []
    expects = []
    for name, fn in plan:
        items = fn(text)
        if isinstance(items, tuple):
            items = [items]
        for old, new in items:
            n = text.count(old)
            ctx = (old.strip().splitlines() or [""])[0][:58]
            expects.append(new)
            if n == 1:
                text = text.replace(old, new)
                results.append((name, "REPLACED", ctx))
            elif n == 0 and new in text:
                results.append((name, "ALREADY", ctx))
            else:
                results.append((name, "MISS(%d)" % n, ctx))
    missing = [e for e in expects if e not in text]
    if mode == "apply" and text != raw.decode("utf-8-sig"):
        _write(path, raw, text)
    return results, text, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not (args.dry or args.apply or args.check):
        ap.print_help()
        return 2
    mode = "apply" if args.apply else "dry"
    bad = 0
    for path, plan in TARGETS:
        raw, text = _read(path)
        results, new_text, missing = apply_one(path, raw, text, plan, mode)
        short = path.split("/")[-1]
        for name, state, ctx in results:
            print("[%s] %-10s %-18s %s" % (short, name, state, ctx))
        if missing:
            bad += 1
            print("[%s] !! 终态未达成：%d 项期望串缺失" % (short, len(missing)))
    if args.check:
        # 终态断言：四处都必须已是新值
        _, p = _read(PS1)
        _, b = _read(B1)
        _, a = _read(A5)
        _, o = _read(OG)
        checks = {
            "ps1 含新条目": "'%s'" % ENTRY in p,
            "ps1 无重复": p.count("'%s'" % ENTRY) == 1,
            "ps1 无尾逗号": ("'%s'," % ENTRY) not in p,
            "b1 集合含新条目": '"%s",' % ENTRY in b,
            "b1 len==20": "len(TOOLS_KEY) == 20" in b,
            "b1 all==95": "len(all_files) == 95" in b,
            "a5 tools_key==20": '("tools_key", 20)' in a,
            "og tools_key==20": '("tools_key", 20)' in o,
            "ps1 保 BOM": open(PS1, "rb").read(3) == b"\xef\xbb\xbf",
            "ps1 全 CRLF": (lambda d: d.count(b"\n") == d.count(b"\r\n"))(open(PS1, "rb").read()),
        }
        ok = True
        for k, v in checks.items():
            print("  %-18s %s" % (k, "OK" if v else "FAIL"))
            ok = ok and v
        return 0 if ok else 1
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
