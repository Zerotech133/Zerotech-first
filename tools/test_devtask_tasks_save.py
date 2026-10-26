# -*- coding: utf-8 -*-
"""devtask._save 并发安全单测（K-QUANT-20261026-DEVTASKSAVE）。"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE / "devtask.py"


def _load():
    spec = importlib.util.spec_from_file_location("devtask_save_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path):
    M = _load()
    M.TASKS = str(tmp_path / "tasks.json")
    M.WT_DIR = str(tmp_path)
    return M


def _read(M):
    return json.loads(Path(M.TASKS).read_text(encoding="utf-8"))


def test_stale_dict_does_not_revert_other_ticket(tmp_path):
    M = _setup(tmp_path)
    M._save({"A": {"status": "doing"}, "B": {"status": "doing"}},
            changed=("A", "B"))
    stale = M._tasks()                       # 进程 A 读到的旧快照
    # 进程 B 并发把 B 置 done（只写变更键）
    fresh = M._tasks()
    fresh["B"] = {"status": "done"}
    M._save(fresh, changed=("B",))
    # 进程 A 随后用旧快照只写 A 的变更 → 不应回滚 B
    stale["A"] = {"status": "done"}
    M._save(stale, changed=("A",))
    disk = _read(M)
    assert disk["A"]["status"] == "done"
    assert disk["B"]["status"] == "done", "并发写回滚了他单状态（回归）"


def test_changed_new_key_and_drop(tmp_path):
    M = _setup(tmp_path)
    M._save({"A": {"status": "doing"}, "B": {"status": "doing"}},
            changed=("A", "B"))
    tasks = M._tasks()
    tasks["C"] = {"status": "done"}
    M._save(tasks, changed=("C",))
    assert _read(M)["C"]["status"] == "done"

    tasks = M._tasks()
    tasks.pop("A", None)
    tasks["A-aborted-1"] = {"status": "aborted"}
    M._save(tasks, changed=("A-aborted-1",), drop_keys=("A",))
    disk = _read(M)
    assert "A" not in disk and disk["A-aborted-1"]["status"] == "aborted"
    assert disk["B"]["status"] == "doing"


def test_legacy_full_merge_still_works(tmp_path):
    M = _setup(tmp_path)
    M._save({"A": {"status": "doing"}}, changed=("A",))
    M._save({"B": {"status": "done"}})   # changed=None → 旧语义整包合并
    disk = _read(M)
    assert disk["A"]["status"] == "doing" and disk["B"]["status"] == "done"
