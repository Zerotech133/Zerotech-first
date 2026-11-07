# -*- coding: utf-8 -*-
"""证明第9轮新增的正例用例在改动前确实会红（不是空转测试）。

读 HEAD 版 scan_secrets.py → old 模块；工作区那份 → new 模块。
对同一 (候选, 行) 调 is_high_entropy_candidate：旧 True 且新 False 才算"测试有牙"。
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    src = subprocess.run(["git", "show", "HEAD:tools/scan_secrets.py"],
                         cwd=str(REPO), capture_output=True).stdout
    p = pathlib.Path(tempfile.mkdtemp()) / "old.py"
    p.write_bytes(src)
    old = load(p, "ss_old")
    new = load(REPO / "tools" / "scan_secrets.py", "ss_new")

    cand = "test_legion_live_boot_p0"
    cases = [
        "见 portal/%s.py 的 token 校验" % cand,
        "2. `portal/%s.py` 既有失败（与 token 无关）" % cand,
        "路径 tools\\%s.py 里的 token" % cand,
        "见 docs/%s.json 的 token 字段" % cand,
    ]
    bad = 0
    for c in cases:
        a, b = old.is_high_entropy_candidate(cand, c), new.is_high_entropy_candidate(cand, c)
        ok = (a is True and b is False)
        bad += 0 if ok else 1
        print("  old=%-5s new=%-5s %s | %s" % (a, b, "OK" if ok else "UNEXPECTED", c))

    # 反向：边界三条在 old 里也必须是 True（否则"边界仍须命中"是空转）
    tok = "Xk9mQ2pL7vR4wZ8bN3tY"
    bounds = [
        (cand, "见 %s 的 token" % cand),
        (cand, "见 portal/%s 的 token" % cand),
        ("abcdef1234567890abcd", "token abcdef1234567890abcd.py 待查"),
        (tok, "token: /%s.py 待查" % tok),
    ]
    print("\n反向（边界在 old/new 都应为 True）：")
    for c2, line in bounds:
        a, b = old.is_high_entropy_candidate(c2, line), new.is_high_entropy_candidate(c2, line)
        ok = (a is True and b is True)
        bad += 0 if ok else 1
        print("  old=%-5s new=%-5s %s | %s" % (a, b, "OK" if ok else "UNEXPECTED", line[:50]))

    print("\n结论：%s" % ("全部符合预期" if not bad else "%d 条不符合预期" % bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
