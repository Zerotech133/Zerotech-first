# -*- coding: utf-8 -*-
"""工单 §八.2：修正 test_gate.ps1 头注释「tools 套件供夜间/手动全量」的不实描述。

事实：夜间 -Suite all = portal/kstock/agent/quant/ide，**不含 tools**；
tools 只能手工 -Suite tools 触发。故改为「仅手工触发 + 关键单文件登记进 tools_key」。

按字节替换，保 UTF-8 BOM 与 CRLF。
"""
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[2] / "tools" / "test_gate.ps1"
OLD = "（tools 套件供夜间/手动全量，不进 fast）"
NEW = "（tools 套件仅手工触发、不进 fast；关键单文件请登记进 tools_key）"


def main() -> int:
    raw = P.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM 丢了"
    old_b, new_b = OLD.encode("utf-8"), NEW.encode("utf-8")
    if raw.count(new_b) == 1 and raw.count(old_b) == 0:
        print("已应用过，无需重复。")
        return 0
    n = raw.count(old_b)
    assert n == 1, "锚点不唯一：count=%d" % n
    out = raw.replace(old_b, new_b, 1)
    assert new_b in out and old_b not in out
    P.write_bytes(out)
    raw2 = P.read_bytes()
    assert raw2.startswith(b"\xef\xbb\xbf"), "写后 BOM 丢失"
    assert raw2.count(b"\r\n") == raw2.count(b"\n"), "写后出现裸 LF"
    print("OK：BOM 在，CRLF 全一致，行数 %d → %d" % (raw.count(b"\n"), raw2.count(b"\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
