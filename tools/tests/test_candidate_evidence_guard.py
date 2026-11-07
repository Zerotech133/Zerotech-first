# -*- coding: utf-8 -*-
"""candidate_evidence_guard：W3 候选证据包只读校验器测试（全离线 mock）。

覆盖：完整候选 pass / 各强制字段缺失 fail / raw ref 复核失败（sha 不符/文件缺/selector 缺）/
PIT 等级 / OOS 阈值 / L4 established artifact_ref / MD+YAML 输入 / 多输入混合 /
CLI 退出码 0-1-2 / --json/--markdown/--out / 只读不落盘。
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "candidate_evidence_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("candidate_evidence_guard_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()
_MISSING = object()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _base_env(tmp_path: Path) -> dict:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events = data_dir / "events.jsonl"
    events.write_text('{"id":"e1","impact":"positive"}\n{"id":"e2"}\n', encoding="utf-8")
    sha = _sha256(events)
    receipt_rel = "agent_platform/data/academy/freeze_receipts/FR-TEST-1.json"
    receipt = tmp_path / receipt_rel
    _write_json(receipt, {
        "schema": "academy_freeze_receipt/v1",
        "status": "final",
        "data_version": "tdx-parquet@2026-10-28; files=2; sha256=" + sha,
        "sample_in": "2023-07-31~2025-06-30",
        "sample_out": "2025-07-01~2026-10-16",
        "cost_model": "cost_bps=3; slippage_bps=1; t_plus_one=true",
    })
    artifact_rel = "agent_platform/data/academy/artifacts/RA-TEST-1.json"
    _write_json(tmp_path / artifact_rel, {
        "schema": "research_artifact/v1",
        "artifact_id": "RA-TEST-1",
        "data_sha": sha,
        "as_of": "2026-10-31T10:00:00+08:00",
    })
    return {"tmp": tmp_path, "sha": sha, "events": events,
            "receipt_rel": receipt_rel, "artifact_rel": artifact_rel,
            "artifact_root": str((tmp_path / "agent_platform/data/academy/artifacts"))}


def _raw_ref(env: dict) -> dict:
    return {
        "schema": "evidence_ref/v2",
        "kind": "event_record",
        "source": "news_event",
        "uri": "raw://event_record/data/events.jsonl",
        "as_of": "2026-10-31T10:00:00+08:00",
        "hash": env["sha"][:16],
        "freshness_s": 0,
        "raw_uri": "data/events.jsonl",
        "raw_sha256": env["sha"],
        "raw_row_range": [],
        "raw_selector": "line:0",
        "raw_bytes": env["events"].stat().st_size,
        "raw_verified": False,
    }


def _candidate(env: dict, **overrides) -> dict:
    candidate = {
        "schema": "research_artifact/v2",
        "candidate": {"candidate_id": "cand-test-600519",
                      "symbol": "600519", "name": "测试标的"},
        "hypothesis": "突破前20日高点且量能>1.5倍均量的信号，在样本外扣成本后存在正期望（可证伪）",
        "data": {
            "data_version": "tdx-parquet@2026-10-28; files=2; sha256=" + env["sha"],
            "sha256": env["sha"],
            "freeze_receipt": env["receipt_rel"],
            "frozen_at": "2026-10-31T10:00:00+08:00",
            "sample_out": "2025-07-01~2026-10-16; rows=314",
            "pit_verdict": "B：窗口过滤与抓取时点可核验（无字节快照，不能标 A）",
        },
        "evidence_refs": [_raw_ref(env)],
        "backtest": {"window_rows": 777, "signals_out_sample": 5,
                     "executable_trades_out_sample": 12, "win_rate": 0.6,
                     "avg_ret": 0.01, "cum_ret": 0.12},
        "falsification_conditions": [
            {"id": "F1", "condition": "OOS 可执行交易 <10", "observed": 12,
             "triggered": False},
        ],
        "l4": {"status": "unresolved", "verdict": "散手票", "source": "xsec_profile/v2"},
        "verdict": "No-Go",
    }
    for key, value in overrides.items():
        parts = key.split(".")
        cursor = candidate
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        last = parts[-1]
        if value is _MISSING:
            cursor.pop(last, None)
        else:
            cursor[last] = value
    return candidate


def _validate(env: dict, candidate, **kwargs):
    path = _write_json(env["tmp"] / "candidate.json", candidate)
    return MOD.validate_report(
        [str(path)], repo_root_path=env["tmp"], raw_root=env["tmp"],
        artifact_root=env["artifact_root"], **kwargs)


def _check(report: dict, check_id: str) -> dict:
    candidate = report["candidates"][0]
    for item in candidate["checks"]:
        if item["id"] == check_id:
            return item
    raise AssertionError("check 不存在：%s；已有=%s"
                         % (check_id, [c["id"] for c in candidate["checks"]]))


# ---------------------------------------------------------------------------
# 完整候选 / pass
# ---------------------------------------------------------------------------
def test_complete_candidate_passes(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env))
    assert report["verdict"] == "pass", report["gaps"]
    assert report["summary"] == {"total": 1, "pass": 1, "fail": 0, "unknown": 0}
    assert report["engine"]["ok"] is True
    assert _check(report, "evidence_refs")["evidence"]["verified"] == 1


def test_candidate_wrapper_list_passes(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, {"candidates": [_candidate(env)]})
    assert report["verdict"] == "pass"
    assert report["summary"]["total"] == 1


def test_raw_ref_row_range_and_selector_ok(tmp_path):
    env = _base_env(tmp_path)
    ref = _raw_ref(env)
    ref["raw_selector"] = "line:1"
    report = _validate(env, _candidate(env, evidence_refs=[ref]))
    assert report["verdict"] == "pass"


# ---------------------------------------------------------------------------
# 字段缺失 / 非法 → fail
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("override,check_id", [
    ({"candidate.candidate_id": _MISSING}, "candidate_id"),
    ({"candidate.symbol": _MISSING}, "symbols"),
    ({"candidate.symbol": "600519.SH,600519"}, "symbols"),
    ({"data.data_version": _MISSING}, "data_version"),
    ({"data.data_version": "tdx-parquet@2026-10-28", "data.sha256": _MISSING},
     "data_version"),
    ({"data.freeze_receipt": "no/such/receipt.json"}, "freeze_receipt"),
    ({"data.frozen_at": _MISSING}, "as_of"),
    ({"data.frozen_at": "not-a-time"}, "as_of"),
    ({"data.pit_verdict": "C：不可核验"}, "pit"),
    ({"data.pit_verdict": _MISSING}, "pit"),
    ({"evidence_refs": _MISSING}, "evidence_refs"),
    ({"backtest": _MISSING}, "backtest"),
    ({"data.sample_out": _MISSING}, "backtest"),
    ({"falsification_conditions": _MISSING}, "invalidation"),
    ({"falsification_conditions": []}, "invalidation"),
    ({"l4": _MISSING}, "l4"),
    ({"l4": {"status": "weird"}}, "l4"),
    ({"hypothesis": _MISSING}, "hypothesis"),
    ({"verdict": _MISSING}, "decision"),
])
def test_field_violations_fail(tmp_path, override, check_id):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env, **override))
    assert report["verdict"] == "fail"
    assert _check(report, check_id)["status"] == "fail", report["gaps"]


def test_pit_a_passes(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env, **{"data.pit_verdict": "A（字节快照+sha 可复核）"}))
    assert _check(report, "pit")["status"] == "pass"


def test_freeze_receipt_sha_mismatch_fails(tmp_path):
    env = _base_env(tmp_path)
    _write_json(env["tmp"] / env["receipt_rel"], {
        "schema": "academy_freeze_receipt/v1", "status": "final",
        "data_version": "tdx@2026-10-28; sha256=" + "a" * 64,
    })
    report = _validate(env, _candidate(env))
    assert report["verdict"] == "fail"
    assert _check(report, "freeze_receipt")["status"] == "fail"
    assert "不一致" in _check(report, "freeze_receipt")["detail"]


def test_freeze_receipt_not_final_fails(tmp_path):
    env = _base_env(tmp_path)
    _write_json(env["tmp"] / env["receipt_rel"], {
        "schema": "academy_freeze_receipt/v1", "status": "pending",
        "data_version": "tdx@2026-10-28; sha256=" + env["sha"],
    })
    report = _validate(env, _candidate(env))
    assert report["verdict"] == "fail"
    assert _check(report, "freeze_receipt")["status"] == "fail"


# ---------------------------------------------------------------------------
# raw ref 复核
# ---------------------------------------------------------------------------
def _set_ref(env, **ref_over):
    ref = _raw_ref(env)
    ref.update(ref_over)
    return [ref]


def test_summary_only_refs_fail(tmp_path):
    env = _base_env(tmp_path)
    summary_ref = {"type": "kline_raw", "uri": "quant_backtest/data/x.parquet",
                   "as_of": "2026-10-16T17:05:06", "hash_note": "摘要自证"}
    report = _validate(env, _candidate(env, evidence_refs=[summary_ref]))
    assert report["verdict"] == "fail"
    item = _check(report, "evidence_refs")
    assert item["status"] == "fail"
    assert "raw" in item["detail"]


def test_raw_ref_sha_mismatch_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, evidence_refs=_set_ref(env, raw_sha256="b" * 64)))
    assert report["verdict"] == "fail"
    item = _check(report, "evidence_refs")
    assert item["status"] == "fail"
    assert any(f["reason"] == "sha256_mismatch" for f in item["evidence"]["failures"])


def test_raw_ref_file_missing_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, evidence_refs=_set_ref(env, raw_uri="data/missing.jsonl")))
    assert report["verdict"] == "fail"
    assert _check(report, "evidence_refs")["status"] == "fail"


def test_raw_ref_selector_missing_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, evidence_refs=_set_ref(env, raw_selector="line:99")))
    assert report["verdict"] == "fail"
    assert any(f["reason"] == "selector_not_found"
               for f in _check(report, "evidence_refs")["evidence"]["failures"])


def test_raw_ref_mixed_one_bad_fails(tmp_path):
    env = _base_env(tmp_path)
    good = _raw_ref(env)
    bad = dict(good, raw_sha256="c" * 64)
    report = _validate(env, _candidate(env, evidence_refs=[good, bad]))
    assert report["verdict"] == "fail"
    assert _check(report, "evidence_refs")["status"] == "fail"


# ---------------------------------------------------------------------------
# backtest / OOS
# ---------------------------------------------------------------------------
def test_oos_trades_below_threshold_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, **{"backtest.executable_trades_out_sample": 3}))
    assert report["verdict"] == "fail"
    item = _check(report, "backtest")
    assert item["status"] == "fail"
    assert "3 <" in item["detail"]


def test_oos_threshold_configurable(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, **{"backtest.executable_trades_out_sample": 3}), min_oos_trades=3)
    assert report["verdict"] == "pass"
    assert report["policy"]["min_oos_trades"] == 3


def test_oos_missing_return_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env, backtest={
        "window_rows": 777, "executable_trades_out_sample": 12}))
    assert report["verdict"] == "fail"
    assert _check(report, "backtest")["status"] == "fail"


def test_invalidation_triggered_still_passes_field(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env, falsification_conditions=[
        {"id": "F1", "condition": "样本不足", "observed": 3, "triggered": True},
    ]))
    assert report["verdict"] == "pass"
    assert "已触发" in _check(report, "invalidation")["detail"]


# ---------------------------------------------------------------------------
# L4
# ---------------------------------------------------------------------------
def test_l4_established_with_valid_artifact_passes(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, l4={"status": "established", "artifact_ref": "RA-TEST-1.json",
                 "verdict": "定性成立"}))
    assert report["verdict"] == "pass", report["gaps"]
    assert _check(report, "l4")["status"] == "pass"


def test_l4_established_without_artifact_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env, l4={"status": "established"}))
    assert report["verdict"] == "fail"
    assert _check(report, "l4")["status"] == "fail"


def test_l4_established_invalid_artifact_fails(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(
        env, l4={"status": "established", "artifact_ref": "nope.json"}))
    assert report["verdict"] == "fail"
    item = _check(report, "l4")
    assert item["status"] == "fail"
    assert "artifact_ref 无效" in item["detail"]


def test_l4_unresolved_allowed(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env))
    assert _check(report, "l4")["status"] == "pass"


# ---------------------------------------------------------------------------
# 输入格式 / unknown
# ---------------------------------------------------------------------------
def test_bad_json_unknown(tmp_path):
    env = _base_env(tmp_path)
    path = env["tmp"] / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    report = MOD.validate_report([str(path)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["verdict"] == "unknown"
    assert report["candidates"][0]["verdict"] == "unknown"


def test_missing_input_file_unknown(tmp_path):
    env = _base_env(tmp_path)
    report = MOD.validate_report([str(env["tmp"] / "nope.json")],
                                 repo_root_path=env["tmp"], raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["verdict"] == "unknown"
    assert "不存在" in report["inputs"][0]["error"]


def test_md_with_json_block_passes(tmp_path):
    env = _base_env(tmp_path)
    body = json.dumps(_candidate(env), ensure_ascii=False, indent=1)
    path = _write_text(env["tmp"] / "cand.md",
                       "# 候选证据包\n\n```json\n" + body + "\n```\n")
    report = MOD.validate_report([str(path)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["inputs"][0]["mode"] == "md_json_block"
    assert report["verdict"] == "pass"


def test_md_without_block_unknown(tmp_path):
    env = _base_env(tmp_path)
    path = _write_text(env["tmp"] / "cand.md", "# 纯文本候选\n\n没有机器块\n")
    report = MOD.validate_report([str(path)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["verdict"] == "unknown"
    assert "无 ```json" in report["inputs"][0]["error"]


def test_yaml_input_passes(tmp_path):
    yaml = pytest.importorskip("yaml")
    env = _base_env(tmp_path)
    path = _write_text(env["tmp"] / "cand.yaml",
                       yaml.safe_dump(_candidate(env), allow_unicode=True,
                                      sort_keys=False))
    report = MOD.validate_report([str(path)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["inputs"][0]["mode"] == "yaml"
    assert report["verdict"] == "pass"


def test_multiple_inputs_mixed_overall_fail(tmp_path):
    env = _base_env(tmp_path)
    good = _write_json(env["tmp"] / "good.json", _candidate(env))
    bad_cand = _candidate(env)
    bad_cand.pop("hypothesis")
    bad = _write_json(env["tmp"] / "bad.json", bad_cand)
    report = MOD.validate_report([str(good), str(bad)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    assert report["verdict"] == "fail"
    assert report["summary"] == {"total": 2, "pass": 1, "fail": 1, "unknown": 0}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli_inputs(env):
    good = _write_json(env["tmp"] / "good.json", _candidate(env))
    bad_cand = _candidate(env)
    bad_cand.pop("hypothesis")
    bad = _write_json(env["tmp"] / "bad.json", bad_cand)
    return str(good), str(bad)


def _cli_common(env):
    return ["--repo-root", str(env["tmp"]), "--raw-root", str(env["tmp"]),
            "--artifact-root", env["artifact_root"]]


def test_cli_pass_exit0_json(tmp_path, capsys):
    env = _base_env(tmp_path)
    good, _bad = _cli_inputs(env)
    code = MOD.main(["--input", good, *_cli_common(env), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    assert payload["schema"] == "candidate_evidence_guard/v1"


def test_cli_fail_exit1(tmp_path, capsys):
    env = _base_env(tmp_path)
    _good, bad = _cli_inputs(env)
    code = MOD.main(["--input", bad, *_cli_common(env), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "fail"


def test_cli_unknown_exit2(tmp_path, capsys):
    env = _base_env(tmp_path)
    code = MOD.main(["--input", str(env["tmp"] / "no.json"), *_cli_common(env),
                     "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "unknown"


def test_cli_markdown_default(tmp_path, capsys):
    env = _base_env(tmp_path)
    good, _bad = _cli_inputs(env)
    code = MOD.main(["--input", good, *_cli_common(env)])
    assert code == 0
    out = capsys.readouterr().out
    assert "# 候选证据包只读校验" in out
    assert "候选汇总" in out


def test_cli_out_writes_report_files(tmp_path, capsys):
    env = _base_env(tmp_path)
    good, _bad = _cli_inputs(env)
    out_dir = env["tmp"] / "deliverables"
    code = MOD.main(["--input", good, *_cli_common(env), "--out", str(out_dir),
                     "--date", "2026-10-31"])
    assert code == 0
    md = out_dir / "2026-10-31_候选证据包校验_v1.md"
    js = out_dir / "2026-10-31_候选证据包校验_v1.json"
    assert md.is_file() and js.is_file()
    assert json.loads(js.read_text(encoding="utf-8"))["verdict"] == "pass"


def test_cli_no_out_writes_nothing(tmp_path, capsys):
    env = _base_env(tmp_path)
    good, _bad = _cli_inputs(env)
    before = sorted(p.name for p in env["tmp"].iterdir())
    code = MOD.main(["--input", good, *_cli_common(env), "--json"])
    assert code == 0
    capsys.readouterr()
    after = sorted(p.name for p in env["tmp"].iterdir())
    assert before == after


def test_cli_invalid_date_exit2(tmp_path, capsys):
    env = _base_env(tmp_path)
    good, _bad = _cli_inputs(env)
    code = MOD.main(["--input", good, *_cli_common(env), "--date", "20261031"])
    assert code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_readonly_validate_does_not_write(tmp_path):
    env = _base_env(tmp_path)
    candidate = _write_json(env["tmp"] / "candidate.json", _candidate(env))
    before = sorted(str(p) for p in env["tmp"].rglob("*") if p.is_file())
    report = MOD.validate_report([str(candidate)], repo_root_path=env["tmp"],
                                 raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    after = sorted(str(p) for p in env["tmp"].rglob("*") if p.is_file())
    assert report["verdict"] == "pass"
    assert before == after


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def test_render_contains_required_sections(tmp_path):
    env = _base_env(tmp_path)
    report = _validate(env, _candidate(env))
    md = MOD.render_markdown(report)
    for token in ("# 候选证据包只读校验", "候选汇总", "强制字段口径",
                  "未决口径", "说明", "校验项"):
        assert token in md
    assert "PASS" in md


def test_render_unknown_input(tmp_path):
    env = _base_env(tmp_path)
    report = MOD.validate_report([str(env["tmp"] / "nope.json")],
                                 repo_root_path=env["tmp"], raw_root=env["tmp"],
                                 artifact_root=env["artifact_root"])
    md = MOD.render_markdown(report)
    assert "UNKNOWN" in md
    assert "不存在" in md


def test_md_cell_escapes_pipe():
    assert MOD._md_cell("a|b\nc") == "a\\|b c"
    assert MOD._md_cell(None) == "-"
