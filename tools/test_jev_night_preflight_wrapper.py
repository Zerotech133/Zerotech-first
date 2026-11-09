# -*- coding: utf-8 -*-
"""2026-11-07 复盘执行项②：preflight wrapper env「入口×开关」矩阵校验回归。

背景（BUG-3 复盘结论）：wrappers_ok() 原先只查 night/backfill 两个启动器，
feed 启动器漏配 JEV_PREFILTER 时静默失效（公告粗筛永不生效）。
本文件锁死三入口全量校验行为。
"""
import jev_night_preflight as jp

WRAPPER_FILES = (
    ("jev-eval", "start_jev_night.ps1"),
    ("jev-eval", "jev_backfill_task.ps1"),
    ("news_collector", "start_jev_feed.ps1"),
)


def _mk_repo(tmp_path, with_env=True, skip=()):
    for sub, name in WRAPPER_FILES:
        if name in skip:
            continue
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        body = "$env:JEV_PREFILTER = 'safe'\n" if with_env else "$log = 'x'\n"
        (d / name).write_text(body, encoding="utf-8")


def test_wrappers_ok_all_three(tmp_path, monkeypatch):
    _mk_repo(tmp_path)
    monkeypatch.setattr(jp, "REPO", tmp_path)
    ok, detail = jp.wrappers_ok()
    assert ok is True
    assert "feed" in detail  # 明细必须覆盖 feed 入口


def test_wrappers_missing_file(tmp_path, monkeypatch):
    _mk_repo(tmp_path, skip=("start_jev_feed.ps1",))
    monkeypatch.setattr(jp, "REPO", tmp_path)
    ok, detail = jp.wrappers_ok()
    assert ok is False
    assert "缺 wrapper" in detail and "start_jev_feed.ps1" in detail


def test_wrappers_missing_env(tmp_path, monkeypatch):
    _mk_repo(tmp_path, with_env=False)
    monkeypatch.setattr(jp, "REPO", tmp_path)
    ok, detail = jp.wrappers_ok()
    assert ok is False
    assert "缺 JEV_PREFILTER" in detail


def test_real_repo_feed_wrapper_has_prefilter():
    """生产仓库实查：feed 启动器必须含 JEV_PREFILTER（BUG-3 回归钉子）。"""
    import pathlib

    feed = pathlib.Path(jp.REPO) / "news_collector" / "start_jev_feed.ps1"
    assert feed.exists(), "feed 启动器缺失"
    assert "JEV_PREFILTER" in feed.read_text(encoding="utf-8", errors="replace")
