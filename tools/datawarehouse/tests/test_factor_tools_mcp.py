# -*- coding: utf-8 -*-
"""factor-tools 只读 MCP 测试：stdio JSON-RPC 协议 + 真实产物 + 失败路径（fail-visible）。

覆盖（对照《平台线只读MCP建设排期 v1》§二/§三 与 task-8 验收）：
  1) --selftest 可跑且 ok=true；
  2) initialize / tools/list / ping / 未知方法；
  3) 四个工具的真实研究产物读数（factor_daily_latest / factor_mining_latest /
     factor_reports / factor_script_catalog）；
  4) 指定因子 IC 查询命中；不存在因子 → isError；
  5) 产物缺失、JSON 损坏、字段类型不符 → isError，不静默返回空数据；
  6) 记录只读取（dry_run/meta/readonly）与路径白名单（仅固定目录 + 固定 glob）。

测试只读仓库文件；fixture 一律写 pytest 的 tmp_path，不落仓库。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "agent_platform" / "tools" / "factor_tools_mcp.py"
TOOL_NAMES = {"factor_daily_latest", "factor_mining_latest",
              "factor_reports", "factor_script_catalog"}


# ---------------------------------------------------------------- helpers
def _env(extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra or {})
    return env


def _run(msgs, env_extra=None, argv=None):
    """起一次 MCP 子进程：msgs=None 表示 argv 模式（--selftest）。"""
    inp = None
    if msgs is not None:
        inp = "".join(json.dumps(msg, ensure_ascii=False) + "\n" for msg in msgs)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *(argv or [])],
                          input=inp, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180,
                          env=_env(env_extra), cwd=str(REPO))
    assert proc.returncode == 0, "MCP 进程退出码 %s: %s" % (proc.returncode, proc.stderr[-500:])
    return proc


def _batch(calls, env_extra=None):
    """一次子进程内连续调用多个工具，返回 [(is_error, payload_or_text), ...]。"""
    msgs = [{"jsonrpc": "2.0", "id": index + 1, "method": "tools/call",
             "params": {"name": name, "arguments": args or {}}}
            for index, (name, args) in enumerate(calls)]
    responses = [json.loads(line) for line in _run(msgs, env_extra).stdout.splitlines() if line.strip()]
    assert len(responses) == len(calls), responses
    result = []
    for response in responses:
        body = response["result"]
        text = body["content"][0]["text"]
        result.append((True, text) if body.get("isError") else (False, json.loads(text)))
    return result


def _call(name, args=None, env_extra=None):
    return _batch([(name, args)], env_extra)[0]


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _factor_daily(tag="20261102", factors=2, alerts=1, proposals=1):
    rows = [{"factor": "alpha_%d" % index, "ic_mean12": 0.01 * (index + 1),
             "t": 1.5, "ic_pos_rate": 60.0, "last3_mean": 0.02, "prev3_mean": 0.01,
             "sign_flip": False, "months": 12, "type": "known", "verdict": "四关 PASS（LS）"}
            for index in range(factors)]
    return {
        "generated_at": "2026-11-02T01:00:00+08:00",
        "latest_date": "20261031",
        "window": "20251101 .. 20261031",
        "months": 12,
        "owner": "FACTOR1/蒋琬",
        "factors": rows,
        "decay_alerts": [{"factor": "alpha_0", "verdict": "四关 FAIL"}] * alerts,
        "proposals": [{"id": "P%d" % index} for index in range(proposals)],
        "note": "测试夹具（只读用例）",
    }


def _mining(tag="20261102", ranked=5):
    return {
        "meta": {"generated_at": "2026-11-02T02:00:00+08:00", "start": "20190101",
                 "window": "20190131 .. 20261031", "months": 93,
                 "universe": "非ST/上市≥250日", "caveats": ["c1"], "note": "test"},
        "ranked": [{"feature": "feat_%d" % index, "ic_mean": -0.09 + index * 0.01,
                    "ic_std": 0.2, "t_stat": -2.68, "ic_pos_rate": 40.0,
                    "ls_mean": -0.01, "ls_ann_approx_pct": -14.2, "months": 45}
                   for index in range(ranked)],
        "monthly": [{"date": "20190228"}],
    }


@pytest.fixture()
def research_state():
    """真实仓的研究产物是否在位（缺产物的工作区跳过真实读数用例，不假绿）。"""
    reports = REPO / "research" / "reports"
    factor_files = sorted(reports.glob("factor_daily_*.json")) if reports.is_dir() else []
    mining_files = sorted(reports.glob("strategy_mining_*.json")) if reports.is_dir() else []
    return {"factor_daily": factor_files, "strategy_mining": mining_files}


# ---------------------------------------------------------------- 1. 协议 / selftest
def test_selftest_ok():
    proc = _run(None, argv=["--selftest"])
    report = json.loads(proc.stdout)
    assert report["server"] == "factor-tools"
    assert report["tools"] == len(TOOL_NAMES)
    assert report["ok"] is True
    assert {check["tool"] for check in report["checks"]} == TOOL_NAMES
    assert all(check["ok"] for check in report["checks"])


def test_stdio_initialize_tools_list_ping_and_unknown_method():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "no/such/method", "params": {}},
    ]
    lines = [json.loads(line) for line in _run(msgs).stdout.splitlines() if line.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "factor-tools"
    assert lines[0]["result"]["protocolVersion"] == "2024-11-05"
    assert {tool["name"] for tool in lines[1]["result"]["tools"]} == TOOL_NAMES
    assert lines[2]["result"] == {}
    assert lines[3]["error"]["code"] == -32601


# ---------------------------------------------------------------- 2. 真实产物读数
def test_factor_daily_latest_real(research_state):
    if not research_state["factor_daily"]:
        pytest.skip("research/reports 下无 factor_daily_*.json（工作区无研究产物）")
    is_error, body = _call("factor_daily_latest")
    assert is_error is False
    # 只读标记 + 来源元数据
    assert body["dry_run"] is True
    assert body["meta"]["readonly"] is True
    assert body["meta"]["name"].startswith("factor_daily_") and body["meta"]["name"].endswith(".json")
    assert body["meta"]["bytes"] > 0
    # 摘要字段：日期 / 因子数 / 告警 / 提案
    assert body["latest_date"]
    assert isinstance(body["factor_count"], int) and body["factor_count"] >= 1
    assert isinstance(body["alert_count"], int) and isinstance(body["proposal_count"], int)
    assert len(body["factors"]) == min(body["factor_count"], 50)
    assert isinstance(body["verdict_counts"], dict)
    row = body["factors"][0]
    assert "factor" in row and ("ic_mean12" in row or "ic_mean" in row)


def test_factor_query_ic_hit_and_missing_factor_error(research_state):
    if not research_state["factor_daily"]:
        pytest.skip("research/reports 下无 factor_daily_*.json（工作区无研究产物）")
    is_error, body = _call("factor_daily_latest")
    assert is_error is False
    name = body["factors"][0]["factor"]
    is_error, hit = _call("factor_daily_latest", {"factor": name})
    assert is_error is False
    assert hit["matched"]["count"] >= 1
    assert any(str(row.get("factor")) == name for row in hit["matched"]["factors"])
    assert "ic_mean12" in hit["matched"]["factors"][0] or "t" in hit["matched"]["factors"][0]
    # 不存在因子 → fail-visible（isError），不返回空 matched
    is_error, text = _call("factor_daily_latest", {"factor": "definitely_no_such_factor_xyz"})
    assert is_error is True
    assert "未找到因子" in text


def test_factor_mining_latest_top_n(research_state):
    if not research_state["strategy_mining"]:
        pytest.skip("research/reports 下无 strategy_mining_*.json（工作区无挖掘产物）")
    is_error, body = _call("factor_mining_latest", {"top_n": 3})
    assert is_error is False
    assert body["meta"]["name"].startswith("strategy_mining_")
    assert body["ranked_total"] >= 1
    assert len(body["ranked"]) == min(3, body["ranked_total"])
    assert body["ranked"][0]["feature"]
    assert body["ranked_truncated"] is (body["ranked_total"] > 3)


def test_factor_reports_and_script_catalog_real():
    is_error, reports = _call("factor_reports")
    assert is_error is False
    assert reports["dry_run"] is True
    assert set(reports["categories"]) == {"factor_daily", "strategy_mining", "neutral_effect"}
    assert reports["total_files"] >= 1
    for category in reports["categories"].values():
        assert isinstance(category["present"], bool)
        assert category["files"] == category["json"] + category["md"]

    is_error, catalog = _call("factor_script_catalog")
    assert is_error is False
    names = {row["script"] for row in catalog["scripts"]}
    assert {"factor_daily.py", "strategy_miner.py"} <= names
    assert catalog["missing_core"] == []
    assert all(row["bytes"] > 0 for row in catalog["scripts"])
    miner = next(row for row in catalog["scripts"] if row["script"] == "strategy_miner.py")
    assert miner["description"]  # docstring 首行可解析
    is_error, filtered = _call("factor_script_catalog", {"q": "miner"})
    assert is_error is False
    assert {row["script"] for row in filtered["scripts"]} == {"strategy_miner.py"}


# ---------------------------------------------------------------- 3. 失败路径（fail-visible）
def test_missing_artifacts_is_error_not_empty_success(tmp_path):
    empty = tmp_path / "reports"
    empty.mkdir()
    for tool in ("factor_daily_latest", "factor_mining_latest"):
        is_error, text = _call(tool, env_extra={"FACTOR_MCP_REPORTS": str(empty)})
        assert is_error is True, tool
        assert "未找到" in text, text
    # 目录存在但三类产物都空 → 清单工具成功但 present=false + warnings（可见，不假绿）
    is_error, body = _call("factor_reports", env_extra={"FACTOR_MCP_REPORTS": str(empty)})
    assert is_error is False
    assert body["total_files"] == 0
    assert all(not category["present"] for category in body["categories"].values())
    assert len(body["warnings"]) == 3
    # 目录本身不存在 → 三个工具全部 isError
    missing = tmp_path / "no_such_dir"
    for tool in ("factor_daily_latest", "factor_reports", "factor_script_catalog"):
        env_extra = {"FACTOR_MCP_REPORTS": str(missing)}
        if tool == "factor_script_catalog":
            env_extra = {"FACTOR_MCP_SCRIPTS": str(missing)}
        is_error, text = _call(tool, env_extra=env_extra)
        assert is_error is True, tool
        assert "不存在" in text, text


def test_latest_picks_newest_tag_and_top_n(tmp_path):
    reports = tmp_path / "reports"
    _write(reports / "factor_daily_20260101.json", _factor_daily(tag="20260101", factors=1))
    _write(reports / "factor_daily_20261102.json", _factor_daily(factors=2, alerts=1, proposals=1))
    _write(reports / "strategy_mining_20261102.json", _mining(ranked=5))
    _write(reports / "neutral_effect_20261102.json", {"meta": {"generated_at": "x"}})
    env_extra = {"FACTOR_MCP_REPORTS": str(reports)}

    is_error, body = _call("factor_daily_latest", env_extra=env_extra)
    assert is_error is False
    assert body["meta"]["name"] == "factor_daily_20261102.json"  # 按文件名日期标签取最新
    assert body["report_tag"] == "20261102"
    assert body["latest_date"] == "20261031"
    assert (body["factor_count"], body["alert_count"], body["proposal_count"]) == (2, 1, 1)

    is_error, mining = _call("factor_mining_latest", {"top_n": 2}, env_extra=env_extra)
    assert is_error is False
    assert mining["ranked_total"] == 5 and len(mining["ranked"]) == 2
    assert mining["ranked_truncated"] is True
    assert mining["ranked"][0]["feature"] == "feat_0"

    is_error, listing = _call("factor_reports", env_extra=env_extra)
    assert is_error is False
    assert listing["categories"]["factor_daily"]["files"] == 2
    assert listing["categories"]["neutral_effect"]["files"] == 1
    assert listing["warnings"] == []


def test_broken_json_and_bad_shape_are_errors(tmp_path):
    reports = tmp_path / "reports"
    _write(reports / "factor_daily_20261102.json", "{ this is not json ")
    is_error, text = _call("factor_daily_latest", env_extra={"FACTOR_MCP_REPORTS": str(reports)})
    assert is_error is True and "JSON 解析失败" in text

    _write(reports / "factor_daily_20261102.json", {"factors": "oops"})
    is_error, text = _call("factor_daily_latest", env_extra={"FACTOR_MCP_REPORTS": str(reports)})
    assert is_error is True and "不是数组" in text

    _write(reports / "factor_daily_20261102.json", [1, 2, 3])
    is_error, text = _call("factor_daily_latest", env_extra={"FACTOR_MCP_REPORTS": str(reports)})
    assert is_error is True and "不是 JSON object" in text


def test_argument_validation_and_unknown_tool(tmp_path):
    reports = tmp_path / "reports"
    _write(reports / "strategy_mining_20261102.json", _mining())
    env_extra = {"FACTOR_MCP_REPORTS": str(reports)}
    for args in ({"top_n": 0}, {"top_n": 51}, {"top_n": "abc"}):
        is_error, text = _call("factor_mining_latest", args, env_extra=env_extra)
        assert is_error is True, args
        assert "top_n" in text
    is_error, text = _call("factor_reports", {"kind": "bogus"}, env_extra=env_extra)
    assert is_error is True and "kind" in text
    is_error, text = _call("no_such_tool")
    assert is_error is True and "unknown tool" in text


def test_script_catalog_missing_core_is_visible(tmp_path):
    scripts = tmp_path / "scripts"
    _write(scripts / "factor_demo.py", '"""演示因子脚本：仅用于测试。"""\nX = 1\n')
    _write(scripts / "strategy_miner.py", "# no docstring\nY = 2\n")
    is_error, body = _call("factor_script_catalog", env_extra={"FACTOR_MCP_SCRIPTS": str(scripts)})
    assert is_error is False
    rows = {row["script"]: row for row in body["scripts"]}
    assert set(rows) == {"factor_demo.py", "strategy_miner.py"}
    assert rows["factor_demo.py"]["description"] == "演示因子脚本：仅用于测试。"
    assert rows["factor_demo.py"]["category"] == "factor"
    assert rows["strategy_miner.py"]["category"] == "mining"
    assert rows["strategy_miner.py"]["description"] is None
    assert body["missing_core"]  # 缺核心脚本必须可见
    assert body["warnings"] and "缺少核心脚本" in body["warnings"][0]
