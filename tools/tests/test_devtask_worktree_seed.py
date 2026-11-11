# -*- coding: utf-8 -*-
"""2026-11-07 WO-GUANYU-DATASEED-20261106：devtask new 后 worktree 数据种子。

覆盖：复制成功 / 不覆盖已有文件 / 源缺失只 warn 不抛 / 种子清单覆盖 B4 缺口。
"""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _fresh_devtask():
    """显式按 worktree 文件加载 devtask（不依赖 sys.path 顺序/其他测试缓存）。

    合并跑时其他测试/插件可能已缓存主仓的 devtask（无本单新 API），
    直接 import 会拿错版本；spec 指定文件路径可避免假绿/假红。
    """
    spec = importlib.util.spec_from_file_location("devtask_worktree_seed", TOOLS / "devtask.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert Path(mod.__file__).resolve() == (TOOLS / "devtask.py").resolve()
    return mod


def _make_src(tmp_path):
    devtask = _fresh_devtask()
    src = tmp_path / "main"
    for rel in devtask.WT_SEED_FILES:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(("SRC:" + rel).encode("utf-8"))
    return src


def test_seed_copies_missing_files(tmp_path):
    devtask = _fresh_devtask()
    src = _make_src(tmp_path)
    wt = tmp_path / "worktree"
    result = devtask._seed_worktree_data(str(wt), str(src))
    assert [st for _, st in result] == ["copied"] * len(devtask.WT_SEED_FILES)
    for rel in devtask.WT_SEED_FILES:
        assert (wt / rel).read_bytes() == ("SRC:" + rel).encode("utf-8")


def test_seed_does_not_overwrite_existing(tmp_path):
    devtask = _fresh_devtask()
    src = _make_src(tmp_path)
    wt = tmp_path / "worktree"
    first = devtask.WT_SEED_FILES[0]
    target = wt / first
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"KEEP")
    result = dict(devtask._seed_worktree_data(str(wt), str(src)))
    assert result[first] == "exists"
    assert target.read_bytes() == b"KEEP"
    assert result[devtask.WT_SEED_FILES[1]] == "copied"


def test_seed_missing_source_warns_but_no_raise(tmp_path):
    devtask = _fresh_devtask()
    wt = tmp_path / "worktree"
    empty_src = tmp_path / "empty-main"
    empty_src.mkdir()
    result = devtask._seed_worktree_data(str(wt), str(empty_src))
    assert [st for _, st in result] == ["missing-source"] * len(devtask.WT_SEED_FILES)
    assert not wt.exists() or not any(wt.rglob("*"))


def test_seed_files_constant_covers_b4_gap():
    devtask = _fresh_devtask()
    assert {Path(p) for p in devtask.WT_SEED_FILES} == {
        Path("quant_backtest/data/catalog.json"),
        Path("quant_backtest/data/warehouse_registry.json"),
        Path("quant_backtest/data/xueqiu/xueqiu_info.db"),
    }
