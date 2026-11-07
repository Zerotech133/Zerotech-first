#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""K-ORG-20261107-SCANFPGATE：把 tools/tests/test_scan_secrets_fp.py 挂进 fast 门。

为什么要进 fast（而不是留在手动的 -Suite tools）：
  tools/scan_secrets.py 是**每次 commit 都会跑**的 pre-commit 凭据闸门，也是
  devtask merge 侧 _secret_scan_gate（硬门）的引擎。但 `-Suite tools` 全仓零自动
  调用者（夜间 all = portal/kstock/agent/quant/ide，不含 tools），本测试此前是孤儿。
  扫描器退化的方向是「少报」——假阴性不报错，只会让 merge 硬门变纸门，属静默失效。
  成本：42 条全 tmp_path 合成夹具，0.62s，无仓库依赖、无网络。

5 处冻结点（沿用 GATESAFEURL 那次的教训，全仓 grep 过一遍，无第二份）：
  · tools/test_gate.ps1        tools_key 数组 +1（保 UTF-8 BOM + CRLF）
  · test_test_gate_b1.py       TOOLS_KEY 集合 +1
  · test_test_gate_b1.py       len(TOOLS_KEY) 18 → 19
  · test_test_gate_b1.py       len(all_files) 93 → 94
  · test_test_gate_a5strict.py ("tools_key", 18) → 19
  · test_test_gate_org_gate.py 同上（姊妹文件，必须同批）

用法：
  python tools/research/patch_scanfpgate.py --dry     # 只报告，不写
  python tools/research/patch_scanfpgate.py --apply   # 落盘
  python tools/research/patch_scanfpgate.py --check   # 复核已落盘（未落盘则退出码 1）

约定（沿用 patch_loopback_batch5/6、patch_gatesafeurl）：
  · 每处替换前断言旧串 count == 1，替换后断言新串存在；
  · 幂等：先认「新串已在且旧串已无」→ 视为已应用，不重复插入；
  · 保留原 BOM 与行尾（ps1 必须 UTF-8 BOM + CRLF，有 BOM 断言卡着）；
  · 改 python 文件后 ast.parse 校验语法。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PS1 = "tools/test_gate.ps1"
B1 = "tools/tests/test_test_gate_b1.py"
A5 = "tools/tests/test_test_gate_a5strict.py"
ORG = "tools/tests/test_test_gate_org_gate.py"

E = "\r\n"
TARGET = "tools/tests/test_scan_secrets_fp.py"

# ---- hunk 1：ps1 tools_key 数组追加一行 ----
PS1_OLD = "        'tools/tests/test_safe_urlopen.py'" + E + "    )"
PS1_NEW = (
    "        'tools/tests/test_safe_urlopen.py'," + E
    + "        # ---- 凭据扫描器误报回归（2026-11-07 K-ORG-20261107-SCANFPGATE，+1）----" + E
    + "        # scan_secrets.py 是每次 commit 都跑的 pre-commit 闸门，也是 devtask merge" + E
    + "        # 硬门的引擎；但它自己的检测逻辑此前零自动覆盖（-Suite tools 无人自动调）。" + E
    + "        # 退化方向是「少报」，假阴性不报错、只会让硬门变纸门。42 条全合成夹具，0.6s。" + E
    + "        'tools/tests/test_scan_secrets_fp.py'" + E
    + "    )"
)

# ---- hunk 2：b1 的 TOOLS_KEY 集合追加一条 ----
B1_SET_OLD = (
    "    # ---- 回环豁免副本一致性（2026-11-07 K-ORG-20261107-GATESAFEURL，+1）----" + E
    + '    "tools/tests/test_safe_urlopen.py",' + E
    + "}"
)
B1_SET_NEW = (
    "    # ---- 回环豁免副本一致性（2026-11-07 K-ORG-20261107-GATESAFEURL，+1）----" + E
    + '    "tools/tests/test_safe_urlopen.py",' + E
    + "    # ---- 凭据扫描器误报回归（2026-11-07 K-ORG-20261107-SCANFPGATE，+1）----" + E
    + '    "tools/tests/test_scan_secrets_fp.py",' + E
    + "}"
)

# ---- hunk 3：b1 数量断言 18 → 19 ----
B1_CNT_OLD = "assert len(AGENT_KEY) == 52 and len(QUANT_KEY) == 23 and len(TOOLS_KEY) == 18"
B1_CNT_NEW = "assert len(AGENT_KEY) == 52 and len(QUANT_KEY) == 23 and len(TOOLS_KEY) == 19"

# ---- hunk 4：b1 总和 93 → 94 ----
B1_ALL_OLD = (
    'assert len(all_files) == 93, "B1(57)+B2(31)+黄忠聚合入口(1)+学习治理契约(2)+盘后分析(1)'
    '+回环豁免(1) 应为 93 文件"'
)
B1_ALL_NEW = (
    'assert len(all_files) == 94, "B1(57)+B2(31)+黄忠聚合入口(1)+学习治理契约(2)+盘后分析(1)'
    '+回环豁免(1)+凭据扫描回归(1) 应为 94 文件"'
)

# ---- hunk 5/6：a5strict 与 org_gate 的元组计数 18 → 19 ----
TUP_OLD = 'for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 18)):'
TUP_NEW = 'for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 19)):'

EDITS = [
    ("ps1 tools_key 追加 %s" % TARGET, PS1, PS1_OLD, PS1_NEW),
    ("b1 TOOLS_KEY 追加一条", B1, B1_SET_OLD, B1_SET_NEW),
    ("b1 len(TOOLS_KEY) 18→19", B1, B1_CNT_OLD, B1_CNT_NEW),
    ("b1 len(all_files) 93→94", B1, B1_ALL_OLD, B1_ALL_NEW),
    ("a5strict tools_key 18→19", A5, TUP_OLD, TUP_NEW),
    ("org_gate tools_key 18→19", ORG, TUP_OLD, TUP_NEW),
]

BOM = b"\xef\xbb\xbf"


def load(rel: str):
    p = REPO / rel
    raw = p.read_bytes()
    has_bom = raw.startswith(BOM)
    text = raw[3:].decode("utf-8") if has_bom else raw.decode("utf-8")
    return p, text, has_bom


def save(p: Path, text: str, has_bom: bool):
    data = text.encode("utf-8")
    if has_bom:
        data = BOM + data
    p.write_bytes(data)


def plan():
    """返回 [(label, rel, old, new, state)]，state ∈ todo / applied / missing。"""
    cache = {}
    out = []
    for label, rel, old, new in EDITS:
        if rel not in cache:
            cache[rel] = load(rel)[1]
        text = cache[rel]
        # 幂等判定必须在计数断言之前：重复跑时 old 已不存在
        if text.count(new) >= 1 and text.count(old) == 0:
            out.append((label, rel, old, new, "applied"))
        elif text.count(old) == 1:
            out.append((label, rel, old, new, "todo"))
        else:
            out.append((label, rel, old, new, "missing:%d" % text.count(old)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()

    rows = plan()
    bad = [r for r in rows if r[4].startswith("missing")]
    for label, rel, _old, _new, state in rows:
        print("[%-9s] %-42s %s" % (state, rel.split("/")[-1], label))

    if bad:
        print("\n断言失败：有 hunk 的旧串不唯一或已消失，未写入任何文件。")
        return 2

    if args.dry:
        todo = [r for r in rows if r[4] == "todo"]
        print("\n--dry：待改 %d 处，不动文件。" % len(todo))
        return 0

    if args.check:
        todo = [r for r in rows if r[4] == "todo"]
        if todo:
            print("\n--check 未通过：还有 %d 处未落盘。" % len(todo))
            return 1
        print("\n--check 通过：6 处均已落盘。")
        return 0

    # ---- apply ----
    by_file = {}
    for label, rel, old, new, state in rows:
        if state == "todo":
            by_file.setdefault(rel, []).append((label, old, new))
    for rel, items in by_file.items():
        p, text, has_bom = load(rel)
        for label, old, new in items:
            assert text.count(old) == 1, "%s：%s 旧串不唯一" % (rel, label)
            text = text.replace(old, new, 1)
            assert new in text, "%s：%s 替换后新串缺失" % (rel, label)
        if rel.endswith(".py"):
            ast.parse(text)  # 语法自证
        # a5strict / org_gate 只改计数、不出现目标文件名，故仅对登记清单的两份断言
        if rel in (PS1, B1):
            assert text.count(TARGET.split("/")[-1]) >= 1, "%s：目标文件未出现" % rel
        save(p, text, has_bom)
        print("\n已写入 %s（%d 个 hunk，BOM=%s）" % (rel, len(items), has_bom))

    after = plan()
    left = [r for r in after if r[4] != "applied"]
    if left:
        print("\n落盘后复核失败：%s" % left)
        return 3
    print("\n落盘后复核：6/6 全部 applied。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
