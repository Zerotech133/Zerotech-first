# -*- coding: utf-8 -*-
"""M6：tasks.json 接 validate_json（profile=tasks）——降级巡检增强。

覆盖三层：
1. 纯函数 check_tasks_doc 的分级——ERROR（结构损坏，写侧必拒）与
   WARN（内容不精确，降级放行）边界不可混（判例：把「推送域无 branch」
   判成 ERROR 属范畴错误，会让合法写入寸步难行）；
2. CLI 两态——默认 WARN 不影响退出码，--strict 才计入失败；
3. 写侧接线——save_tasks 增量校验（只判本次变更，存量 grandfather）、
   ERROR 拒写且不留半成品、load_tasks 对非 dict 条目显式告警不静默丢弃。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import validate_json as vj  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dw = _load("devtask_wiring_schema", "agent_platform/domains/devtask_wiring.py")


GOOD = {
    "K-T1": {
        "status": "doing",
        "owner": "DEV1",
        "branch": "task/K-T1",
        "created": "2026-10-17 10:00:00",
        "desc": "修复门户死结",
    },
}


def _write(root: Path, tasks: dict):
    d = Path(root) / ".worktrees"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ----------------------------------------------------------------------
# 1. 纯函数分级
# ----------------------------------------------------------------------
def test_good_doc_passes_clean():
    errors, warns = vj.check_tasks_doc(GOOD)
    assert errors == [] and warns == []


def test_top_level_not_dict_is_error():
    errors, _ = vj.check_tasks_doc([1, 2, 3])
    assert errors and "顶层必须是 dict" in errors[0]


def test_value_not_dict_is_error():
    errors, _ = vj.check_tasks_doc({"K-T1": "oops"})
    assert errors and "不是 dict" in errors[0]


def test_missing_status_is_error():
    errors, _ = vj.check_tasks_doc({"K-T1": {"created": "2026-10-17"}})
    assert errors and "缺 status" in " ".join(errors)


def test_illegal_status_is_error():
    errors, _ = vj.check_tasks_doc(
        {"K-T1": {"status": "bogus", "created": "2026-10-17"}}
    )
    assert errors and "不在枚举" in " ".join(errors)


def test_missing_created_is_error():
    errors, _ = vj.check_tasks_doc({"K-T1": {"status": "doing"}})
    assert errors and "缺 created" in " ".join(errors)


def test_shell_done_is_warn_not_error():
    """done 但 desc/branch/owner 全空——推送域无 branch 属正常，只告警。"""
    errors, warns = vj.check_tasks_doc(
        {"K-PUSH-009": {"status": "done", "created": "2026-10-17"}}
    )
    assert errors == []
    assert any("空壳完工单" in w for w in warns)


def test_cli_flag_as_tid_is_warn():
    errors, warns = vj.check_tasks_doc(
        {
            "--help-aborted-084818": {
                "status": "aborted",
                "created": "2026-10-14 08:44:28",
            }
        }
    )
    assert errors == []
    assert any("CLI 参数" in w for w in warns)


def test_handwritten_approximate_created_is_warn():
    """真源实况：K-ORG-156 created='2026-10-17 20:1x'（未记准分钟用 x 占位）。"""
    errors, warns = vj.check_tasks_doc(
        {"K-ORG-156": {"status": "done", "desc": "x", "created": "2026-10-17 20:1x"}}
    )
    assert errors == []
    assert any("不可解析" in w for w in warns)


def test_default_handwritten_created_variants_accepted():
    """三代时间形态都合法：仅日期 / HH:MM / HH:MM:SS。"""
    for ts in ("2026-10-17", "2026-10-17 20:10", "2026-10-17 20:10:30"):
        errors, warns = vj.check_tasks_doc(
            {"K-T1": {"status": "doing", "desc": "d", "created": ts}}
        )
        assert errors == [] and warns == [], ts


def test_merged_free_text_with_note_accepted():
    errors, warns = vj.check_tasks_doc(
        {
            "K-ORG-027a": {
                "status": "done",
                "desc": "d",
                "created": "2026-10-13 09:45",
                "merged": "2026-10-13 09:45 (主控代实施)",
            }
        }
    )
    assert errors == [] and warns == []


def test_empty_merged_is_warn():
    errors, warns = vj.check_tasks_doc(
        {
            "K-ORG-163": {
                "status": "review",
                "desc": "d",
                "created": "2026-10-17",
                "merged": "",
            }
        }
    )
    assert errors == []
    assert any("merged=" in w for w in warns)


# ----------------------------------------------------------------------
# 2. CLI 两态
# ----------------------------------------------------------------------
def test_cli_default_passes_with_warns(tmp_path, capsys):
    p = tmp_path / "tasks.json"
    p.write_text(
        json.dumps({"K-PUSH-009": {"status": "done", "created": "2026-10-17"}}),
        encoding="utf-8",
    )
    rc = vj.main(["--profile", "tasks", str(p)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "WARN" in out and "降级放行" in out


def test_cli_strict_fails_on_warn(tmp_path):
    p = tmp_path / "tasks.json"
    p.write_text(
        json.dumps({"K-PUSH-009": {"status": "done", "created": "2026-10-17"}}),
        encoding="utf-8",
    )
    assert vj.main(["--profile", "tasks", "--strict", str(p)]) == 1


def test_cli_error_fails_even_without_strict(tmp_path):
    p = tmp_path / "tasks.json"
    p.write_text(
        json.dumps({"K-T1": {"status": "bogus", "created": "2026-10-17"}}),
        encoding="utf-8",
    )
    assert vj.main(["--profile", "tasks", str(p)]) == 1


# ----------------------------------------------------------------------
# 3. 写侧接线
# ----------------------------------------------------------------------
def test_save_rejects_structural_error(tmp_path, capsys):
    """改坏 status → 拒写，且盘上文件保持原样（不留半成品）。"""
    root = tmp_path / "repo"
    _write(root, GOOD)
    before = (root / ".worktrees" / "tasks.json").read_text(encoding="utf-8")
    bad = {"K-T1": dict(GOOD["K-T1"], status="bogus")}
    with pytest.raises(dw.DevtaskWiringError) as exc:
        dw.save_tasks(root, bad)
    assert "写前校验失败" in str(exc.value)
    assert (root / ".worktrees" / "tasks.json").read_text(encoding="utf-8") == before


def test_save_allows_change_while_legacy_error_remains(tmp_path):
    """存量 grandfather：盘上已有 status 非法的老工单，不应阻断其他工单写入。"""
    root = tmp_path / "repo"
    legacy = dict(GOOD)
    legacy["K-OLD"] = {"status": "bogus", "created": "2026-10-01"}  # 存量瑕疵
    _write(root, legacy)
    new = dict(legacy)
    new["K-T1"] = dict(GOOD["K-T1"], status="review")  # 合法变更
    dw.save_tasks(root, new)  # 不应抛错
    back = json.loads((root / ".worktrees" / "tasks.json").read_text(encoding="utf-8"))
    assert back["K-T1"]["status"] == "review"
    assert back["K-OLD"]["status"] == "bogus"  # 存量原样保留


def test_save_allows_warn_level_change(tmp_path, capsys):
    root = tmp_path / "repo"
    _write(root, GOOD)
    warn_task = {"K-T9": {"status": "done", "created": "2026-10-17"}}
    dw.save_tasks(root, dict(GOOD, **warn_task))
    err = capsys.readouterr().err
    assert "空壳完工单" in err  # 告警可见
    back = json.loads((root / ".worktrees" / "tasks.json").read_text(encoding="utf-8"))
    assert back["K-T9"]["status"] == "done"  # 但仍写入


def test_load_tasks_warns_on_non_dict_entry(tmp_path, capsys):
    root = tmp_path / "repo"
    _write(root, dict(GOOD, BAD="not-a-dict"))
    got = dw.load_tasks(root)
    assert "BAD" not in got and "K-T1" in got
    assert "非 dict 工单已跳过" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 4. 周一巡检附加观察：必须零退出码影响（门 3 判据锁死 7 项）
# ----------------------------------------------------------------------
mc = _load("monday_checkpoint_schema", "agent_platform/monday_checkpoint.py")


def test_patrol_observe_does_not_touch_results(tmp_path, monkeypatch, capsys):
    """观察项若混进 RESULTS，门 3 的 7/7 判据就会被悄悄改口径。"""
    _write(
        tmp_path,
        dict(GOOD, **{"K-PUSH-009": {"status": "done", "created": "2026-10-17"}}),
    )
    monkeypatch.setattr(mc, "BASE", tmp_path / "agent_platform")
    (tmp_path / "agent_platform").mkdir()
    before = len(mc.RESULTS)
    mc._tasks_schema_observe()
    out = capsys.readouterr().out
    assert len(mc.RESULTS) == before, "附加观察不得写入 RESULTS"
    assert "WARN 1" in out and "不计入退出码" in out


def test_patrol_observe_survives_missing_source(tmp_path, monkeypatch):
    """真源缺失时观察项静默降级——巡检其余 7 项照跑，不能因观察项崩溃。"""
    monkeypatch.setattr(mc, "BASE", tmp_path / "agent_platform")
    (tmp_path / "agent_platform").mkdir()
    mc._tasks_schema_observe()  # 不抛异常即通过
