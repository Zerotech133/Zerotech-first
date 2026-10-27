# -*- coding: utf-8 -*-
"""test_healthcheck_all：聚合逻辑测试（不真跑五个工具）。

两种注入方式：
  1) monkeypatch healthcheck_all.run_tool —— 假 CompletedProcess，测映射/汇总/透传；
  2) tmp_path 造假脚本 + monkeypatch TOOLS_DIR —— 真走 subprocess 全链路。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import healthcheck_all  # noqa: E402

FAKE_REPO_ARGS = ["--repo-root", "R", "--data-root", "D"]


# ---------------------------------------------------------------------------
# 1) monkeypatch run_tool：纯聚合逻辑
# ---------------------------------------------------------------------------

def _fake_run_tool(codes: dict):
    """按脚本名返回假退出码；记录每次调用 (工具名, 完整命令)。"""
    calls = []

    def fake(cmd, cwd, timeout_s):
        name = Path(cmd[1]).stem
        calls.append((name, tuple(cmd)))
        return subprocess.CompletedProcess(
            cmd, codes[name], stdout="OUT:%s\n" % name, stderr="ERR:%s\n" % name)

    fake.calls = calls
    return fake


def _run(monkeypatch, fake, *argv):
    monkeypatch.setattr(healthcheck_all, "run_tool", fake)
    return healthcheck_all.main(list(argv))


def test_all_pass_aggregates_to_zero(monkeypatch):
    """a/b/c 全 0=PASS、d/e 恒 0=中性 → 总退出码 0。"""
    fake = _fake_run_tool({"check_trigger_chain": 0, "check_observation_point": 0,
                           "verify_strategy_ledger": 0, "list_rejects": 0,
                           "scan_secrets": 0})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS)
    assert rc == 0
    assert [n for n, _ in fake.calls] == [s["name"] for s in healthcheck_all.TOOL_SPECS]


def test_any_fail_aggregates_to_two(monkeypatch, capsys):
    """任一工具 2=FAIL → 总退出码 2。"""
    fake = _fake_run_tool({"check_trigger_chain": 0, "check_observation_point": 2,
                           "verify_strategy_ledger": 0, "list_rejects": 0,
                           "scan_secrets": 0})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS)
    assert rc == 2
    table = capsys.readouterr().out
    assert "check_observation_point" in table and "FAIL" in table


def test_verify_ledger_exit1_is_fail(monkeypatch):
    """verify_strategy_ledger 1=存在 FAIL（注意其语义与 a/b 的 1=中性不同）。"""
    fake = _fake_run_tool({"check_trigger_chain": 0, "check_observation_point": 0,
                           "verify_strategy_ledger": 1, "list_rejects": 0,
                           "scan_secrets": 0})
    assert _run(monkeypatch, fake, *FAKE_REPO_ARGS) == 2


def test_all_neutral_aggregates_to_one(monkeypatch):
    """a/b 全 UNKNOWN(1) + d 恒 0 浏览 → 全中性 → 总退出码 1。"""
    fake = _fake_run_tool({"check_trigger_chain": 1, "check_observation_point": 1,
                           "list_rejects": 0})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS,
              "--only", "check_trigger_chain", "check_observation_point", "list_rejects")
    assert rc == 1
    assert [n for n, _ in fake.calls] == ["check_trigger_chain",
                                          "check_observation_point", "list_rejects"]


def test_only_runs_subset_in_order(monkeypatch):
    """--only 只跑指定子集且保持 T-30min 时序顺序。"""
    fake = _fake_run_tool({"scan_secrets": 0, "check_trigger_chain": 0})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS,
              "--only", "scan_secrets", "check_trigger_chain")
    assert rc == 0  # check_trigger_chain PASS → 0
    assert [n for n, _ in fake.calls] == ["check_trigger_chain", "scan_secrets"]  # 时序优先


def test_crash_exit_code_counts_as_fail(monkeypatch, capsys):
    """非正常退出码（如 99）按 FAIL 计并显示 stderr 尾部。"""
    fake = _fake_run_tool({"check_trigger_chain": 99})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS, "--only", "check_trigger_chain")
    assert rc == 2
    err = capsys.readouterr().err
    assert "ERR:check_trigger_chain" in err  # stderr 尾部透出


def test_crash_negative_signal_counts_as_fail(monkeypatch):
    """负数退出码（信号崩溃）也按 FAIL 计。"""
    fake = _fake_run_tool({"scan_secrets": -3})
    rc = _run(monkeypatch, fake, *FAKE_REPO_ARGS, "--only", "scan_secrets")
    assert rc == 2


def test_stdout_passthrough_and_quiet(monkeypatch, capsys):
    """默认透传工具 stdout；--quiet 只出总表。"""
    fake = _fake_run_tool({"check_trigger_chain": 0})
    _run(monkeypatch, fake, *FAKE_REPO_ARGS, "--only", "check_trigger_chain")
    assert "OUT:check_trigger_chain" in capsys.readouterr().out

    _run(monkeypatch, fake, *FAKE_REPO_ARGS, "--only", "check_trigger_chain", "--quiet")
    out = capsys.readouterr().out
    assert "OUT:check_trigger_chain" not in out
    assert "聚合总表" in out


def test_build_cmd_explicit_args():
    """每工具显式参数纪律：拼出的命令含索引卡 CLI 形态。"""
    tools_dir = Path("T")
    repo, data = Path("R"), Path("D")
    by_name = {s["name"]: healthcheck_all.build_cmd(s, tools_dir, repo, data)
               for s in healthcheck_all.TOOL_SPECS}
    assert by_name["check_trigger_chain"][2:] == ["--repo-root", str(repo)]
    assert by_name["check_observation_point"][2:] == ["--data-root", str(data)]
    assert by_name["verify_strategy_ledger"][2:] == ["--data-root", str(data)]
    assert by_name["scan_secrets"][2:] == ["--git-status"]
    assert by_name["list_rejects"][2:] == []
    for spec in healthcheck_all.TOOL_SPECS:
        assert by_name[spec["name"]][1] == str(tools_dir / spec["script"])


# ---------------------------------------------------------------------------
# 2) tmp_path 假脚本：真走 subprocess 全链路
# ---------------------------------------------------------------------------

FAKE_SCRIPT_TMPL = """import sys
print("FAKE_OUT")
sys.exit(%d)
"""


def test_fake_script_end_to_end(tmp_path, monkeypatch, capsys):
    """tmp_path 造假脚本注入：真 subprocess 跑通聚合、映射与 stdout 透传。"""
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    (fake_tools / "check_trigger_chain.py").write_text(
        FAKE_SCRIPT_TMPL % 0, encoding="utf-8")
    monkeypatch.setattr(healthcheck_all, "TOOLS_DIR", fake_tools)
    argv = ["--repo-root", str(tmp_path), "--only", "check_trigger_chain"]
    assert healthcheck_all.main(list(argv)) == 0
    out = capsys.readouterr().out
    assert "FAKE_OUT" in out
    assert "聚合总表" in out

    (fake_tools / "check_trigger_chain.py").write_text(
        FAKE_SCRIPT_TMPL % 2, encoding="utf-8")
    assert healthcheck_all.main(list(argv)) == 2
