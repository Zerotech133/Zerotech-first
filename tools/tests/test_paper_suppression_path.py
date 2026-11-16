"""抑制令路径解析回归测试（2026-11-16 D-P1）。

背景：计划任务 PaperTrade 用 C:\\aios\\quant_backtest（软链）启动，
而 DELIVERABLES 之前用 abspath 算，不解析软链 ⇒ 落到 C:\\deliverables（不存在）
⇒ load_suppression 把「文件不存在」当成「当日无抑制」，抑制令静默失效。

三条断言：
  T1 软链入口与实体入口必须解析到同一交付目录（realpath 生效）
  T2 交付目录不存在时必须抛错，不能返回 None（失败关闭不被绕过）
  T3 目录在但当天无抑制令文件时返回 None（非抑制日合法，不能被 T2 误伤）

T2/T3 是一对：只做 T2 会让每个非抑制日都中断，只做 T3 就回到原来的缺陷。
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "quant_backtest", "paper_trade.py")
LINK = r"C:\aios\quant_backtest\paper_trade.py"
REAL_DIR = os.path.normcase(os.path.join(os.path.dirname(ROOT), "deliverables"))


def _load(tag, path, deliver=None):
    """独立加载一份 paper_trade，可覆盖 ZB_DELIVERABLES。返回模块。"""
    old = os.environ.get("ZB_DELIVERABLES")
    if deliver is not None:
        os.environ["ZB_DELIVERABLES"] = deliver
    else:
        os.environ.pop("ZB_DELIVERABLES", None)
    name = "_pt_%s_%d" % (tag, len(sys.modules))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
        return m
    finally:
        if old is None:
            os.environ.pop("ZB_DELIVERABLES", None)
        else:
            os.environ["ZB_DELIVERABLES"] = old


def test_t1_symlink_and_real_resolve_same_dir():
    """T1：软链入口与实体入口的交付目录必须一致。"""
    m_real = _load("real", TARGET)
    assert os.path.normcase(m_real.DELIVERABLES) == REAL_DIR, (
        "实体入口交付目录不对", m_real.DELIVERABLES)

    if not os.path.isfile(LINK):
        pytest.skip("C:\\aios 软链不存在，跳过软链入口用例")
    m_link = _load("link", LINK)
    assert os.path.normcase(m_link.DELIVERABLES) == os.path.normcase(m_real.DELIVERABLES), (
        "软链入口与实体入口交付目录不一致",
        m_link.DELIVERABLES, m_real.DELIVERABLES)


def test_t2_missing_dir_raises(tmp_path):
    """T2：交付目录不存在 → 抛 RuntimeError，绝不返回 None。"""
    bogus = str(tmp_path / "no_such_dir")
    assert not os.path.isdir(bogus)
    m = _load("bogus", TARGET, deliver=bogus)
    with pytest.raises(RuntimeError) as ei:
        m.load_suppression("20261116")
    assert "目录不存在" in str(ei.value), ei.value


def test_t3_dir_exists_no_file_returns_none(tmp_path):
    """T3：目录在、当日无抑制令 → 返回 None（非抑制日合法）。"""
    m = _load("emptydir", TARGET, deliver=str(tmp_path))
    assert m.load_suppression("20260101") is None


def test_t4_coding_point_of_sample_day():
    """码点复核：测试里用到的日期字面量没被输出通道串味。"""
    day = "20261116"
    assert [ord(c) for c in day] == [50, 48, 50, 54, 49, 49, 49, 54], day
