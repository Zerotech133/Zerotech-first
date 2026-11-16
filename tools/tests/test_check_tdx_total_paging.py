# -*- coding: utf-8 -*-
"""`total` 分页守卫自身的回归测试。

守六件事：
  T1 正确写法不许报（offset 递增到空行 —— 这是唯一正确的终止条件）
  T2 页数算术必须报：ceil(total/page)、total//PAGE、range(0,total,PAGE)
  T3 `while offset < total` 只判 WARN 不判 BAD（有终止保护时只是多打请求）
  T4 文案失真必须报：字符串里同时有 total 与「下限」
  T5 作用域：没有 TDX 标记的文件不扫（否则全域误报，闸门会被关掉）
  T6 ★ 参考实现不许误报：`refresh_snapshot.py` 拿 total 当**初始上界**、
     再用 bisect 实测边界，之后所有页数都用实测值 —— 第一版闭包式污染分析
     把这条路也判成缺陷，这是本次修规则的直接原因（误报一次闸门就废）

外加 T7 变异检验：把缺陷改回正确写法，断言必须变 PASS。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "audit"))

import check_tdx_total_paging as G  # noqa: E402

MARK = "BASE = 'http://rookie.xtcloud-tech.com:48088'  # xtcloud\n"

GOOD_SRC = MARK + '''
PAGE = 5000
def pull(path):
    rows, offset = [], 0
    while True:
        d = _get(path, {"limit": PAGE, "offset": offset})
        got = d.get("rows") or []
        rows.extend(got)
        if len(got) < PAGE:
            break
        offset += PAGE
    return rows
'''

CEIL_SRC = MARK + '''
import math
PAGE = 5000
def pages_needed(d):
    total = d.get("total") or 0
    return math.ceil(total / PAGE)
'''

FLOORDIV_SRC = MARK + '''
PAGE = 5000
def pages_needed(d):
    total = d.get("total") or 0
    n = total // PAGE
    return n
'''

RANGE_SRC = MARK + '''
PAGE = 5000
def walk(d):
    total = d.get("total") or 0
    for off in range(0, total, PAGE):
        _get("/api/data/table/x", {"offset": off})
'''

WHILE_SRC = MARK + '''
def pull(d):
    total = d.get("total") or 0
    offset = 0
    while offset < total:
        d = _get("/api/data/table/x", {"offset": offset})
        chunk = d.get("rows") or []
        if not chunk:
            break
        offset += len(chunk)
'''

COPY_SRC = MARK + '''
NOTE = "接口的 total 是估算下限（48 张表受影响），非精确行数。"
'''

# 参考实现：total 只当初始 claim，真实边界靠探测，页数用实测边界
REF_SRC = MARK + '''
PAGE = 5000
def bisect_boundary(probe, claim_total):
    lo, hi = 0, claim_total * 2
    return hi if claim_total else 0

def plan_pages(boundary, *, page=PAGE):
    return -(-boundary // page) if boundary > 0 else 0

def main(d):
    claim = d.get("total") or 0
    boundary = bisect_boundary(lambda off: 100, claim)
    pages = plan_pages(boundary)
    offset = 0
    while offset < boundary:
        _get("/api/data/table/x", {"offset": offset, "limit": PAGE})
        offset += PAGE
    return pages
'''

NO_MARK_SRC = '''
PAGE = 5000
def pages_needed(d):
    total = d.get("total") or 0
    return total // PAGE
'''


def _scan(src):
    return G.scan_text(src)


def test_t1_good_not_flagged():
    bad, warn, err = _scan(GOOD_SRC)
    assert err is None
    assert bad == [], bad
    assert warn == [], warn


def test_t2_page_arithmetic_flagged():
    for name, src in (("ceil", CEIL_SRC), ("floordiv", FLOORDIV_SRC), ("range", RANGE_SRC)):
        bad, warn, err = _scan(src)
        assert err is None, (name, err)
        assert bad, "未检出：%s\n%s" % (name, src)
        kinds = {k for _, k, _ in bad}
        assert kinds <= {"arith", "range", "page_var"}, (name, kinds)


def test_t3_while_is_warn_not_bad():
    bad, warn, err = _scan(WHILE_SRC)
    assert err is None
    assert bad == [], bad
    assert warn and warn[0][1] == "while_bound"


def test_t4_copy_distortion_flagged():
    bad, warn, err = _scan(COPY_SRC)
    assert err is None
    assert any(k == "copy" for _, k, _ in bad), bad


def test_t6_reference_impl_not_flagged():
    """参考实现（实测边界）不许被判缺陷 —— 误报一次闸门就会被关掉。"""
    bad, warn, err = _scan(REF_SRC)
    assert err is None
    assert bad == [], bad
    assert warn == [], warn


def test_t5_scope_by_marker(tmp_path=None):
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        proj = os.path.join(tmp, "proj")
        qb = os.path.join(proj, "quant_backtest")
        os.makedirs(qb)
        open(os.path.join(qb, "no_mark.py"), "w", encoding="utf-8").write(NO_MARK_SRC)
        rc = G.main(["--root", proj, "--quiet"])
        # 没有 TDX 标记 ⇒ 一个都不扫 ⇒ 无 BAD
        assert rc == 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_t7_cli_rc_semantics():
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp()
    try:
        proj = os.path.join(tmp, "proj")
        qb = os.path.join(proj, "quant_backtest")
        os.makedirs(qb)
        f = os.path.join(qb, "bad.py")
        open(f, "w", encoding="utf-8").write(FLOORDIV_SRC)
        assert G.main(["--root", proj, "--quiet"]) == 1, "有缺陷必须 rc=1"
        # 变异：改回正确写法（用返回行数，不用 total）
        open(f, "w", encoding="utf-8").write(GOOD_SRC)
        assert G.main(["--root", proj, "--quiet"]) == 0, "修好后必须 rc=0（否则无区分力）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
