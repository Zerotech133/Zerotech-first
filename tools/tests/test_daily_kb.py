# -*- coding: utf-8 -*-
"""知识库每日自动化编排器（daily_kb）回归测试。

覆盖：
  1) 步骤编排定义（learning_digest → panel_daily → kb_ingest_status，--with-qa 追加 kb_qa --retrieval-only）；
  2) mock runner 全成功：status schema（generated_at/steps rc/耗时/输出 mtime/latest/portal_refresh/errors）；
  3) 失败隔离：某步 rc=1 不中断后续、errors 记录、最终 rc 非 0，状态文件照样写；
  4) 超时隔离：TimeoutExpired → rc=124 且后续步骤继续；
  5) --check-only：schema/免责声明校验（合法 0、非法 1）；
  6) describe_file / 门户刷新清单。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "daily_kb.py"

_spec = importlib.util.spec_from_file_location("daily_kb_tool", SCRIPT)
dk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dk)  # type: ignore[union-attr]


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "ok", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_runner(fail: tuple[str, ...] = (), timeout: tuple[str, ...] = ()):
    """构造 mock runner：按脚本 stem 决定成功/失败/超时；返回 (runner, calls)。"""
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):  # noqa: ANN001 - 模拟 subprocess.run
        calls.append(list(cmd))
        script = next((c for c in cmd if str(c).endswith(".py")), "")
        stem = Path(script).stem
        if stem in timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 1)
        if stem in fail:
            return FakeProc(returncode=1, stdout="", stderr=f"boom: {stem}")
        return FakeProc(returncode=0, stdout=f"ok: {stem}", stderr="")

    return runner, calls


def run_mocked(tmp_path: Path, *, with_qa: bool = False, fail=(), timeout=()) -> tuple[dict, list]:
    runner, calls = make_runner(fail=fail, timeout=timeout)
    status = dk.run_all(
        with_qa=with_qa,
        status_path=tmp_path / "daily_kb_status.json",
        runner=runner,
        write=True,
    )
    return status, calls


# ---------------------------------------------------------------------------
# 1) 步骤编排定义
# ---------------------------------------------------------------------------
def test_build_steps_and_qa_optional():
    steps = dk.build_steps(with_qa=False)
    assert [s["name"] for s in steps] == ["learning_digest", "panel_daily", "kb_ingest_status"]
    for step in steps:
        assert Path(step["script"]).is_file(), f"步骤脚本不存在: {step['script']}"
    assert dk.build_steps(with_qa=False)[0]["args"] == ["run"]

    steps_qa = dk.build_steps(with_qa=True)
    assert [s["name"] for s in steps_qa] == [
        "learning_digest", "panel_daily", "kb_ingest_status", "kb_qa_retrieval",
    ]
    qa = steps_qa[-1]
    assert Path(qa["script"]).name == "kb_qa.py"
    assert qa["args"][0] == "ask"
    assert "--retrieval-only" in qa["args"]
    assert "--json" in qa["args"]
    # 步骤过滤
    selected = dk.select_steps(steps_qa, ["kb_ingest_status", "learning_digest"])
    assert [s["name"] for s in selected] == ["learning_digest", "kb_ingest_status"]
    try:
        dk.select_steps(steps_qa, ["nope"])
    except ValueError as exc:
        assert "未知步骤" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知步骤应抛 ValueError")


# ---------------------------------------------------------------------------
# 2) mock 全成功 → status schema / latest / portal_refresh
# ---------------------------------------------------------------------------
def test_mocked_run_status_schema(tmp_path):
    status, calls = run_mocked(tmp_path, with_qa=True)
    assert len(calls) == 4
    for cmd in calls:
        assert cmd[0] == dk.PYTHON and cmd[1] == "-X" and cmd[2] == "utf8"

    status_file = tmp_path / "daily_kb_status.json"
    assert status_file.is_file(), "状态台账必须写盘"
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert dk.validate_status(payload) == [], dk.validate_status(payload)

    assert payload["ok"] is True and payload["rc"] == 0
    assert payload["mode"] == "run" and payload["with_qa"] is True
    assert payload["generated_at"]
    steps = payload["steps"]
    assert [s["name"] for s in steps] == [s["name"] for s in dk.build_steps(with_qa=True)]
    for step in steps:
        assert step["rc"] == 0 and step["ok"] is True
        assert isinstance(step["elapsed_seconds"], (int, float))
        for out in step["outputs"]:
            assert out["path"] and "exists" in out and "mtime" in out, "输出需含 mtime 快照"
    assert any(s["outputs"] for s in steps if s["name"] == "learning_digest")
    assert set(payload["latest"]) >= {"learning_digest", "panel_daily", "kb_ingest_status",
                                      "panel_daily_status", "kb_qa_samples"}
    assert len(payload["portal_refresh"]) == 4
    for item in payload["portal_refresh"]:
        for key in ("path", "producer", "page", "section", "exists", "mtime"):
            assert key in item
    assert payload["errors"] == []
    assert "不构成投资建议" in payload["disclaimer"]


# ---------------------------------------------------------------------------
# 3) 失败隔离：中间步骤失败不中断后续，最终 rc 非 0
# ---------------------------------------------------------------------------
def test_failure_isolation_continues(tmp_path):
    status, calls = run_mocked(tmp_path, fail=("panel_daily",))
    names = [Path(next(c for c in cmd if str(c).endswith(".py"))).stem for cmd in calls]
    assert names == ["learning_digest", "panel_daily", "kb_ingest_status"], "失败后仍须执行后续步骤"

    assert status["ok"] is False and status["rc"] == 1
    assert status["errors"] and "panel_daily" in status["errors"][0]
    by_name = {s["name"]: s for s in status["steps"]}
    assert by_name["panel_daily"]["ok"] is False and by_name["panel_daily"]["rc"] == 1
    assert by_name["learning_digest"]["ok"] is True
    assert by_name["kb_ingest_status"]["ok"] is True
    # 状态文件照样写盘（供巡检）
    payload = json.loads((tmp_path / "daily_kb_status.json").read_text(encoding="utf-8"))
    assert payload["rc"] == 1 and payload["errors"]
    assert dk.validate_status(payload) == []


# ---------------------------------------------------------------------------
# 4) 超时隔离：rc=124 且后续继续
# ---------------------------------------------------------------------------
def test_timeout_recorded_and_isolated(tmp_path):
    status, calls = run_mocked(tmp_path, timeout=("learning_digest",))
    assert len(calls) == 3, "超时后仍须执行后续步骤"
    first = status["steps"][0]
    assert first["name"] == "learning_digest"
    assert first["rc"] == 124 and first["ok"] is False
    assert "超时" in (first["error"] or "")
    assert status["ok"] is False and status["rc"] == 1
    assert status["steps"][1]["ok"] is True and status["steps"][2]["ok"] is True


# ---------------------------------------------------------------------------
# 5) --check-only：schema/免责声明校验
# ---------------------------------------------------------------------------
def test_check_only_schema_and_disclaimer(tmp_path):
    run_mocked(tmp_path)  # 生成合法状态
    status_path = tmp_path / "daily_kb_status.json"
    report = dk.check_only(status_path)
    assert report["ok"] is True, report["errors"]
    assert report["steps_checked"] == 3

    # 缺免责声明
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    del payload["disclaimer"]
    bad = tmp_path / "bad_missing_disclaimer.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report2 = dk.check_only(bad)
    assert report2["ok"] is False
    assert any("disclaimer" in e for e in report2["errors"])

    # 免责声明缺少关键短语
    payload2 = json.loads(status_path.read_text(encoding="utf-8"))
    payload2["disclaimer"] = "仅供研究"
    bad2 = tmp_path / "bad_disclaimer.json"
    bad2.write_text(json.dumps(payload2, ensure_ascii=False), encoding="utf-8")
    assert any("不构成投资建议" in e for e in dk.check_only(bad2)["errors"])

    # 损坏 JSON / 文件缺失
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    assert dk.check_only(broken)["ok"] is False
    assert dk.check_only(tmp_path / "missing.json")["ok"] is False

    # CLI 退出码：合法 0、非法 1
    def cli(*args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )

    assert cli("--check-only", "--status", str(status_path)).returncode == 0
    assert cli("--check-only", "--status", str(bad2)).returncode == 1


# ---------------------------------------------------------------------------
# 6) describe_file / 门户刷新清单
# ---------------------------------------------------------------------------
def test_describe_file_and_portal_refresh():
    real = dk.describe_file(REPO / "portal" / "static" / "kb_learning_digest.json")
    assert real["exists"] is True and real["size"] > 0 and real["mtime_epoch"] > 0
    missing = dk.describe_file(REPO / "no_such_file_xyz.json")
    assert missing["exists"] is False and missing["size"] is None and missing["mtime"] is None

    refresh = dk.build_portal_refresh()
    assert len(refresh) == 4
    paths = {item["path"] for item in refresh}
    assert "portal/static/kb_learning_digest.json" in paths
    assert "portal/static/report_panel.json" in paths
    assert "portal/static/kb_ingest.json" in paths
    for item in refresh:
        assert item["page"] and item["section"] and item["producer"]
    # 三个主产物均已存在（此前流水线已生成）
    primary = [i for i in refresh if i["page"].startswith("/")]
    assert len(primary) == 3
    for item in primary:
        assert item["exists"] is True, f"主产物应存在: {item['path']}"
