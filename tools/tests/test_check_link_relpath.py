# -*- coding: utf-8 -*-
"""退层路径守卫自身的回归测试。

守四件事：
  T1 真缺陷必须被检出（造一处退层落到别名之外，bad>0、rc=1）
  T2 正常写法不许误报（别名与实体 realpath 相同 → bad=0、rc=0）
  T3 别名入口缺失时必须 WARN（否则等于「只验了一半还说 PASS」）
  T4 ★ 判定必须用 realpath，不能用字符串前缀
     —— 2026-11-16 第一版实现用 startswith 判「是否在项目内」，
        把 C:\\aios（同目录别名）误报成 112 处缺陷。本条守住这个错。

T4 的构造方式：让别名路径与实体路径**字符串不同、realpath 相同**
（直接让 --alias 指向 root 本身，只是路径拼法不同）。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "audit"))

import check_link_relpath as G  # noqa: E402

BAD_SRC = '''# -*- coding: utf-8 -*-
import os
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "..", "deliverables"))
'''


def _mkproj(tmp, with_bad=True):
    """造一个最小项目：<tmp>/proj/quant_backtest/holder.py"""
    proj = os.path.join(tmp, "proj")
    qb = os.path.join(proj, "quant_backtest")
    os.makedirs(qb, exist_ok=True)
    if with_bad:
        open(os.path.join(qb, "holder.py"), "w", encoding="utf-8").write(BAD_SRC)
    return proj


def _mk_other_tree(tmp, name="deeper_tree"):
    """造一棵**深度不同**的树，模拟别名根与实体根层级不一致。

    ★ 必须比 proj 深一层：若两棵树同层，各退两层会回到同一个父目录，
      字符串安全相同，构造不出缺陷（第一版测试就栽在这里，误判为「扫描器没检出」）。
      真实场景正是如此：C:\\aios 退两层 → C:\\，实体退两层 → C:\\项目。
    """
    return os.path.join(tmp, name, "alias_root")


def test_t1_real_defect_detected():
    """T1：别名根深度不同 → 退层落点不同 → 必须报 bad=1。"""
    tmp = tempfile.mkdtemp(prefix="lnk_t1_")
    try:
        proj = _mkproj(tmp, True)
        elsewhere = _mk_other_tree(tmp)
        os.makedirs(elsewhere, exist_ok=True)
        findings, stats = G.scan(proj, elsewhere)
        assert stats["n_hits"] == 1, stats
        bad = [f for f in findings if not f["same_realpath"]]
        assert len(bad) == 1, ("应检出 1 处真缺陷", findings)
        # 判据是「落点不同」，不是「落点不存在」：算到另一个存在的目录同样危险，
        # 甚至更危险（会往错的地方写）。exists_via_alias 只是辅助判断后果。
        assert os.path.normcase(bad[0]["real"]) != os.path.normcase(bad[0]["alias"]), bad[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_t2_benign_not_reported():
    """T2：别名与实体同一目录 → realpath 相同 → bad=0（不许误报）。"""
    tmp = tempfile.mkdtemp(prefix="lnk_t2_")
    try:
        proj = _mkproj(tmp, True)
        findings, stats = G.scan(proj, proj)     # 别名 = 自身
        assert stats["n_bad"] == 0, ("不许误报", findings)
        assert all(f["same_realpath"] for f in findings), findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_t3_alias_missing_warns(tmp=None):
    """T3：别名入口不存在时必须标记 alias_ok=False（结果不完整要能看出来）。"""
    tmp = tempfile.mkdtemp(prefix="lnk_t3_")
    try:
        proj = _mkproj(tmp, False)
        _, stats = G.scan(proj, os.path.join(tmp, "no_such_alias"))
        assert stats["alias_ok"] is False, stats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_t4_judge_by_realpath_not_prefix():
    """T4：判定必须用 realpath，不能用字符串前缀。

    这正是 11-16 第一版实现踩的坑：C:\\aios 与 C:\\项目\\老田的AI助手
    字符串不同却是同一目录，用 startswith 判定会把 112 处正常写法全报成缺陷。

    直接测判定函数本身——它才是口径所在：
      · 真实 junction（C:\\aios 与项目根）必须判为同一位置
      · 而字符串前缀判定会得出相反结论（证明两种方法结论不同）
      · 两棵不同深度的临时树必须判为不同位置
    """
    real_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    alias = r"C:\aios"
    if os.path.isdir(alias):
        assert G.same_realpath(alias, real_root) is True, (
            "C:\\aios 与项目根必须判为同一位置", alias, real_root)
        prefix_says_inside = os.path.normcase(alias).startswith(
            os.path.normcase(real_root) + os.sep)
        assert prefix_says_inside is False, (
            "字符串前缀应判为「不在项目内」—— 两种方法结论相反，"
            "所以前缀判定不可用")

    tmp = tempfile.mkdtemp(prefix="lnk_t4_")
    try:
        proj = _mkproj(tmp, True)
        elsewhere = _mk_other_tree(tmp)
        os.makedirs(elsewhere, exist_ok=True)
        assert G.same_realpath(proj, elsewhere) is False, (
            "两棵不同深度的树不许判成同一位置", proj, elsewhere)
        findings, stats = G.scan(proj, elsewhere)
        assert stats["n_diff_string"] == 1, ("应进入候选", stats)
        assert stats["n_bad"] == 1, ("realpath 不同应报缺陷", findings)
        assert findings[0]["same_realpath"] is False, findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_t5_exit_code_and_functional_line(capsys=None):
    """T5：有缺陷时 rc=1、无缺陷时 rc=0；且必须打印 LNK: 功能行。"""
    import contextlib
    import io
    tmp = tempfile.mkdtemp(prefix="lnk_t5_")
    try:
        proj = _mkproj(tmp, True)
        elsewhere = _mk_other_tree(tmp)
        os.makedirs(elsewhere, exist_ok=True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_bad = G.main(["--root", proj, "--alias", elsewhere, "--quiet"])
        out = buf.getvalue()
        assert rc_bad == 1, ("有缺陷应 rc=1", rc_bad)
        assert "LNK:" in out and "bad=1" in out, out

        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc_ok = G.main(["--root", proj, "--alias", proj, "--quiet"])
        out2 = buf2.getvalue()
        assert rc_ok == 0, ("无缺陷应 rc=0", rc_ok)
        assert "LNK:" in out2 and "bad=0" in out2, out2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_t6_guard_self_root_uses_realpath():
    """T6：守卫自己从别名入口启动时，DEFAULT_ROOT 必须仍是实体路径。

    否则它会自己踩它要防的坑：root 算成 C:\\aios 后与 ALIAS_ROOT 相同，
    每个常量都判「两入口一致」⇒ 永远 PASS。检查器自身也要站在实体路径上。
    """
    alias_script = r"C:\aios\tools\audit\check_link_relpath.py"
    if not os.path.isfile(alias_script):
        print("  (C:\\aios 不存在，跳过别名加载用例)")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("_g_from_alias", alias_script)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    real_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    assert os.path.normcase(m.DEFAULT_ROOT) == os.path.normcase(real_root), (
        "从别名入口加载后 DEFAULT_ROOT 必须是实体根", m.DEFAULT_ROOT, real_root)
    assert "aios" not in m.DEFAULT_ROOT.lower(), (
        "DEFAULT_ROOT 不许保留别名写法", m.DEFAULT_ROOT)


if __name__ == "__main__":
    fns = [test_t1_real_defect_detected, test_t2_benign_not_reported,
           test_t3_alias_missing_warns, test_t4_judge_by_realpath_not_prefix,
           test_t5_exit_code_and_functional_line,
           test_t6_guard_self_root_uses_realpath]
    n_ok = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
            n_ok += 1
        except AssertionError as e:
            print("FAIL %s  %s" % (fn.__name__, e))
    print("%d/%d passed" % (n_ok, len(fns)))
    sys.exit(0 if n_ok == len(fns) else 1)
