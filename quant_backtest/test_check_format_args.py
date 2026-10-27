# -*- coding: utf-8 -*-
"""check_format_args 单元测试（W9 门禁，K-STOCK-393）。

背景：premarket.build_doc_md "7 占位符对 6 参数"潜伏 bug 连崩 3 天。
本测试钉死校验器的三组判例：真阳性必报、%% 文案场景不误报、
动态值/IfExp 等不可静态判定场景跳过。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import check_format_args as cfa  # noqa: E402


def _run(src):
    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, "sample.py")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(src)
        return cfa.check_file(fp)


def test_true_positive_mismatch():
    """占位符多、参数少 → 必报（premarket 连崩 3 天同款）。"""
    bad = _run('x = "%s %s %s：%s（%s）%s" % (a, b, c, d, e)\n')
    assert len(bad) == 1 and bad[0][1] == 6 and bad[0][2] == 5, bad


def test_double_percent_literal_no_false_positive():
    """'90%% avg' 字面百分号 + 后续单词 → 不得误报（26 误报实证）。"""
    ok = _run(
        'print("win=90%% avg=+18.88%%): diff win %s" % w)\n'
        'print("n=%d win=%.0f%% avg=%+.2f%%" % (n, w, a))\n'
    )
    assert ok == [], ok


def test_star_width_eats_arg():
    """%.*f → 1 占位符 + 1 个 * 参数 = 2 参。"""
    ok = _run('s = "%.*f" % (nd, x)\n')
    assert ok == [], ok
    bad = _run('s = "%.*f" % (nd,)\n')
    assert len(bad) == 1 and bad[0][1] == 2 and bad[0][2] == 1, bad


def test_dynamic_and_ifexp_skipped():
    """变量格式串 / dict 参数 / IfExp 元组 → 不可静态判定，跳过。"""
    ok = _run(
        "s = fmt % (a, b)\n"  # 变量格式串
        't = "%s %s" % d\n'  # dict 映射
        'u = "%s %s %s" % (x if c else (p, q, r))\n'
    )  # IfExp 定长元组
    assert ok == [], ok


def test_repo_self_clean():
    """工具+仓库核心目录自扫零命中（门禁自证）。"""
    base = os.path.dirname(HERE)
    roots = [
        os.path.join(base, d)
        for d in (
            "quant_backtest",
            "portal",
            "news_collector",
            "jev-eval",
            "agent_platform",
        )
    ]
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert cfa.scan(roots) == []
