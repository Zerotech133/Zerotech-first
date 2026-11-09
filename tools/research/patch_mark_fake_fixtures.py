# -*- coding: utf-8 -*-
"""给 verify_entropy_tests_have_teeth.py 的两行夹具变量补 `# fake` 行内标记。

原因：该脚本里的 tok / cand 是**故意构造的负例夹具**，形似真实 token；
pre-commit 的 HIGH_ENTROPY 会对它们报 warn。scan_secrets 的 TEST_LINE_MARKERS
（fake|dummy|fixture|假密钥…）正是为这种情况准备的按行豁免。

按**行号**改，不做字符串匹配——本机输出通道会把数字串串味（1234567890 与
1234567890 打印结果相同），据打印内容做字面匹配会改错行。
"""
import sys
from pathlib import Path

P = Path(__file__).resolve().parent / "verify_entropy_tests_have_teeth.py"
TARGETS = (46, 50)  # 1-based：tok 定义行、cand 边界夹具行
MARK = "  # fake 夹具（非真实凭据）"


def main() -> int:
    raw = P.read_bytes()
    lines = raw.split(b"\r\n") if b"\r\n" in raw else raw.split(b"\n")
    sep = b"\r\n" if b"\r\n" in raw else b"\n"
    for ln in TARGETS:
        idx = ln - 1
        assert 0 <= idx < len(lines), "行号越界"
        cur = lines[idx]
        assert MARK.encode("utf-8") not in cur, "第 %d 行已标记过" % ln
        # 只允许在「代码行」上加，避免误把注释/空行标成夹具
        assert cur.strip() and not cur.strip().startswith(b"#"), "第 %d 行不是代码行" % ln
        lines[idx] = cur + MARK.encode("utf-8")
    out = sep.join(lines)
    P.write_bytes(out)
    v = P.read_text(encoding="utf-8")
    for ln in TARGETS:
        assert v.splitlines()[ln - 1].endswith(MARK), "第 %d 行未生效" % ln
    print("OK：已为 %d 行补 fake 标记" % len(TARGETS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
