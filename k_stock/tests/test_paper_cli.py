# -*- coding: utf-8 -*-
"""paper.cli._newest_run 回归测试（WO-QT-20261020-049）。

核心缺陷（-046 取证实锤）：原实现按文件名字典序取尾，poc_pf_*/poc_adapter_*
前缀首字母恒大于日期戳开头，旧产物会压过新产物被当"最新"——默认 exec 落到
全陈旧跳过。修复后委托 trainee._newest_ai_run（mtime_ns + 文件名确定性破平，
排除面含 pf_/adapter_ 两族），本文件锁死该口径不回退。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

import cli as paper_cli        # noqa: E402
import trainee                 # noqa: E402
from engine import Reject      # noqa: E402


def _mk_ai_dir(tmp_path: Path, names: list[str]) -> Path:
    """造决策产物目录：每个文件落盘并给错开的 mtime（按列表序递增 1s）。"""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for i, name in enumerate(names):
        p = d / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, ns=(1_700_000_000_000_000_000 + i * 1_000_000_000,) * 2)
    return d


def test_newest_run_beats_lexicographic_trap(tmp_path, monkeypatch):
    """字典序陷阱回归：pf_/adapter_ 产物字典序 > 日期戳，但 mtime 更旧——
    修复前 sorted()[-1] 选中 poc_pf_002_20261015（10-15 旧产物）；
    修复后必须选 mtime 最新的 AI 产物 poc_20261020_211855。"""
    d = _mk_ai_dir(tmp_path, [
        "poc_20261008_151924.json",
        "poc_adapter_chanlun_20261013.json",
        "poc_pf_002_20261015.json",
        "poc_20261020_211855.json",          # 列表最后落盘 → mtime 最新
    ])
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    assert paper_cli._newest_run().name == "poc_20261020_211855.json"


def test_newest_run_excludes_non_ai_channels(tmp_path, monkeypatch):
    """他源通道排除面（qd/qpzm/qpzmus/qpzmhk/pf/adapter）：即便 mtime 最新
    也不得入选——07:05 automation 不吃他源信号（策略串味防护，K-STOCK-414）。"""
    d = _mk_ai_dir(tmp_path, [
        "poc_20261020_211855.json",
        "poc_qd_20261021_090000.json",       # 双刀通道，mtime 更新
        "poc_qpzm_20261021_090100.json",
        "poc_qpzmus_20261021_090200.json",
        "poc_qpzmhk_20261021_090300.json",
        "poc_pf_003_20261021_090400.json",
        "poc_adapter_x_20261021_090500.json",  # 全场 mtime 最新，仍须排除
    ])
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    assert paper_cli._newest_run().name == "poc_20261020_211855.json"


def test_newest_run_mtime_tie_breaks_deterministically(tmp_path, monkeypatch):
    """mtime 并列时按文件名破平（日期戳升序=新旧序），与 trainee 侧 -017 同款：
    glob 返回序不确定，max 首个会间歇选旧——本用例反复跑必须恒选 b。"""
    d = tmp_path / "data"
    d.mkdir()
    for name in ("poc_20260101_a.json", "poc_20260102_b.json"):
        p = d / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, ns=(1_700_000_000_000_000_000,) * 2)   # 同一刻落盘
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    got = {paper_cli._newest_run().name for _ in range(5)}
    assert got == {"poc_20260102_b.json"}, f"破平不确定: {got}"


def test_newest_run_empty_dir_rejects(tmp_path, monkeypatch):
    """目录无任何 AI 产物 → Reject（closed on failure，不静默返回 None）。"""
    d = tmp_path / "data"
    d.mkdir()
    (d / "poc_pf_009_20261015.json").write_text("{}", encoding="utf-8")  # 仅他源
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    with pytest.raises(Reject):
        paper_cli._newest_run()
