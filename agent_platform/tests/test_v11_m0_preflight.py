# -*- coding: utf-8 -*-
"""v1.1 M0 批前只读预检器测试（task-43）。

覆盖：contract/live key diff、白名单缺失、adapter hash、ADR 编号冲突、
live 不可达降级、只读零写、JSON/摘要/退出码。
全部用 tmp 合成夹具，不碰真实 live/仓内状态。
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO / "tools" / "v11_m0_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("v11_m0_preflight_t43",
                                                  str(TOOL_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREFLIGHT = _load()
FROZEN_KEYS = ["generated_at", "department", "seats", "work_in_progress",
               "metrics", "redlines", "sources"]
EXTRA_KEYS = ["schema", "readonly", "ai_cards", "quant_cards", "risk_cards",
              "governance", "extensions", "errors", "ok"]
CONTRACT = {"schema": "legion_screen_contract/v1", "status": "frozen",
            "version": "1.0", "generated_at": "2026-10-28",
            "endpoint": "/api/dev/legion", "method": "GET", "readonly": True,
            "cache_ttl_s": 60,
            "response": {key: "x" for key in FROZEN_KEYS},
            "errors": {"fallback": "empty"},
            "frozen_at": "2026-10-28T18:00:00+08:00",
            "approved_by": "老田（代确认 ARCH1 契约；2026-10-28）"}
FIELDS = {"allowed": {"sources": ["agent_platform/registry/org.json"]},
          "banned": ["symbol", "name", "price", "target_price", "rationale",
                     "trace_id", "owner", "recommendation"],
          "k_anon": 5, "max_rows": 5000, "cache_ttl_s": 60,
          "log_no_raw_rows": True, "source_label": "as_of:2026-11-01;source:org"}
LIVE_PAYLOAD = {key: None for key in FROZEN_KEYS + EXTRA_KEYS}
LIVE_PAYLOAD.update({"schema": "legion_screen_contract/v1", "readonly": True,
                     "ok": True, "errors": [],
                     "extensions": ["v1.1_quant_real", "v1.1_risk_real"]})
ADAPTERS = {
    "portal/api_dev_legion.py": "print('api_dev_legion fixture')\n",
    "portal/dev_legion_quant_real.py": "print('quant_real fixture')\n",
    "portal/dev_legion_risk_real.py": "print('risk_real fixture')\n",
}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def _adr_text(number, append_v11=False):
    text = "# ADR-%03d test\n\n- status: Accepted\n" % number
    if number == 9 and append_v11:
        text += "\n## v1.1 optional_extensions\n\n- v1.1 quant/risk real 追加节\n"
    return text


def make_root(tmp_path, *, contract=None, fields=FIELDS, live=None,
              doc_text="# v1.1\n\n- 已生效（M0 冻结说明）\n",
              manifest=True, adr_numbers=range(1, 15), adr_append=True,
              duplicate_adr009=False, adapter_content=None):
    """生成一份合成仓库根；返回 (root, adapter_args)。"""
    root = Path(tmp_path) / "repo"
    contract = CONTRACT if contract is None else contract
    _write_json(root / "docs" / "dev" / "legion_screen_contract.json", contract)
    if fields is not None:
        _write_json(root / "docs" / "dev" / "dev_legion_v11_fields.json", fields)
    _write(root / "docs" / "dev" / "dev_legion_v11.md", doc_text)
    for number in adr_numbers:
        _write(root / "docs" / "adr" / ("ADR-%03d-test.md" % number),
               _adr_text(number, append_v11=adr_append))
    if duplicate_adr009:
        _write(root / "docs" / "adr" / "ADR-009-port-registry.md",
               _adr_text(9))
    if manifest:
        _write_json(root / "work" / "audit-20261101" / "v11_m0" / "manifest.json",
                    {"files": [], "owner": "DSH-Lead"})
    contents = adapter_content or ADAPTERS
    adapter_args = []
    for rel, text in contents.items():
        _write(root / rel, text)
        # Windows 写文件会把 \n 转 \r\n → 按实际文件字节算期望前缀
        prefix = hashlib.sha256((root / rel).read_bytes()).hexdigest()[:8]
        adapter_args.extend(["--adapter", "%s=%s" % (rel, prefix)])
    live = LIVE_PAYLOAD if live is None else live
    _write_json(Path(tmp_path) / "live_snapshot.json", live)
    return root, adapter_args


def _run(root, adapter_args, tmp_path, extra=()):
    snapshot = Path(tmp_path) / "live_snapshot.json"
    return PREFLIGHT.main(["--root", str(root), "--live-snapshot",
                           str(snapshot), "--json", *adapter_args, *extra])


def _report(capsys):
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# PASS / 关键 diff
# ---------------------------------------------------------------------------
def test_preflight_pass_when_ready(tmp_path, capsys):
    full_contract = json.loads(json.dumps(CONTRACT))
    full_contract["response"] = {key: "x" for key in FROZEN_KEYS + EXTRA_KEYS}
    root, adapter_args = make_root(tmp_path, contract=full_contract)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 0
    assert report["status"] == "PASS"
    assert report["exit_code"] == 0
    assert report["readonly"] is True and report["not_executing_m0"] is True
    assert report["suggested_steps"] == []
    assert {c["id"] for c in report["checks"]} == {
        "C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7"}


def test_key_drift_partial_and_suggest_s2(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path)  # 契约 7 key vs live 16 key
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    assert report["status"] == "PARTIAL"
    drift = [c for c in report["checks"] if c["id"] == "C3"][0]
    assert drift["status"] == "PARTIAL"
    assert drift["evidence"]["live_extra_keys"] == sorted(EXTRA_KEYS)
    assert drift["evidence"]["frozen_missing_in_live"] == []
    assert "S2" in report["suggested_steps"]
    assert report["next_step"] == "S2"


def test_frozen_key_regression_fails(tmp_path, capsys):
    contract = json.loads(json.dumps(CONTRACT))
    live = {key: None for key in EXTRA_KEYS + FROZEN_KEYS[:-1]}
    root, adapter_args = make_root(tmp_path, contract=contract, live=live)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 2
    assert report["status"] == "FAIL"
    drift = [c for c in report["checks"] if c["id"] == "C3"][0]
    assert drift["status"] == "FAIL"
    assert drift["evidence"]["frozen_missing_in_live"] == ["sources"]


def test_missing_fields_whitelist_partial_s1(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, fields=None)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    fields_check = [c for c in report["checks"] if c["id"] == "C4"][0]
    assert fields_check["status"] == "PARTIAL"
    assert any(f["code"] == "fields_missing"
               for f in fields_check["findings"])
    assert "S1" in report["suggested_steps"]
    assert report["next_step"] == "S1"


def test_fields_incomplete_partial(tmp_path, capsys):
    fields = json.loads(json.dumps(FIELDS))
    fields["banned"] = ["symbol"]
    fields["k_anon"] = 3
    root, adapter_args = make_root(tmp_path, fields=fields, contract=full_contract())
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    codes = [f["code"]
             for c in report["checks"] if c["id"] == "C4"
             for f in c["findings"]]
    assert "banned_incomplete" in codes
    assert "k_anon_mismatch" in codes


def full_contract():
    contract = json.loads(json.dumps(CONTRACT))
    contract["response"] = {key: "x" for key in FROZEN_KEYS + EXTRA_KEYS}
    return contract


# ---------------------------------------------------------------------------
# adapters hash / ADR 冲突
# ---------------------------------------------------------------------------
def test_adapter_hash_drift_partial_s3(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    args = [item for item in adapter_args]
    args[1] = args[1].split("=")[0] + "=deadbeef"  # 第一个 adapter 期望 hash 不匹配
    rc = _run(root, args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    adapters = [c for c in report["checks"] if c["id"] == "C5"][0]
    assert adapters["status"] == "PARTIAL"
    assert adapters["evidence"]["adapters"][0]["status"] == "drift"
    assert "S3" in report["suggested_steps"]


def test_adapter_missing_fails(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    missing = root / "portal" / "dev_legion_risk_real.py"
    missing.unlink()
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 2
    adapters = [c for c in report["checks"] if c["id"] == "C5"][0]
    assert adapters["status"] == "FAIL"


def test_adr_number_conflict_fails(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    _write(root / "docs" / "adr" / "ADR-015-conflict.md", "# ADR-015\n")
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 2
    adr = [c for c in report["checks"] if c["id"] == "C6"][0]
    assert adr["status"] == "FAIL"
    assert any(f["code"] == "expected_adr_occupied"
               for f in adr["findings"])


def test_adr009_append_missing_partial(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract(),
                                   adr_append=False)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    adr = [c for c in report["checks"] if c["id"] == "C6"][0]
    assert adr["status"] == "PARTIAL"
    assert any(f["code"] == "adr009_append_missing"
               for f in adr["findings"])
    assert "S2" in adr["findings"][0]["suggest_steps"] or any(
        "S2" in f["suggest_steps"] for f in adr["findings"])


def test_adr_duplicate_is_governance_debt_not_fail(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract(),
                                   duplicate_adr009=True)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    # 重号 ADR-009 记 PARTIAL（治理债），不 FAIL
    adr = [c for c in report["checks"] if c["id"] == "C6"][0]
    assert adr["status"] == "PARTIAL"
    assert any(f["code"] == "adr_duplicate_numbers"
               for f in adr["findings"])
    assert rc == 1


# ---------------------------------------------------------------------------
# live 不可达降级 / 只读零写 / 输出形态
# ---------------------------------------------------------------------------
def test_live_unreachable_degrades_partial(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    rc = PREFLIGHT.main(["--root", str(root), "--live-url",
                         "http://127.0.0.1:1/api/dev/legion",
                         "--timeout", "0.2", "--json", *adapter_args])
    report = _report(capsys)
    assert rc == 1
    live = [c for c in report["checks"] if c["id"] == "C2"][0]
    assert live["status"] == "PARTIAL"
    assert live["evidence"]["degraded"] is True
    drift = [c for c in report["checks"] if c["id"] == "C3"][0]
    assert drift["status"] == "SKIP"
    assert any(f["code"] == "live_unavailable"
               for f in live["findings"])
    assert "S0" in report["suggested_steps"]


def test_offline_degrades_partial(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    rc = PREFLIGHT.main(["--root", str(root), "--offline", "--json",
                         *adapter_args])
    report = _report(capsys)
    assert rc == 1
    live = [c for c in report["checks"] if c["id"] == "C2"][0]
    assert live["status"] == "PARTIAL"
    assert live["evidence"]["source"] == "offline"


def _tree(root):
    rows = []
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_file():
            rows.append((rel, path.stat().st_size, path.stat().st_mtime_ns))
        else:
            rows.append((rel + "/", -1, 0))
    return rows


def test_read_only_zero_write(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    before = _tree(root)
    cwd_before = set(os.listdir("."))
    rc = _run(root, adapter_args, tmp_path)
    _report(capsys)
    assert rc == 0
    assert _tree(root) == before, "预检器不得写仓内文件（零写）"
    assert set(os.listdir(".")) == cwd_before, "预检器不得在 cwd 落文件"


def test_summary_text_output(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path)
    rc = PREFLIGHT.main(["--root", str(root), "--live-snapshot",
                         str(Path(tmp_path) / "live_snapshot.json"),
                         *adapter_args])
    out = capsys.readouterr().out
    assert rc == 1
    assert "状态=PARTIAL" in out
    assert "建议先做" in out
    assert "C3" in out


def test_invalid_contract_fails(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract())
    (root / "docs" / "dev" / "legion_screen_contract.json").write_text(
        "{bad json", encoding="utf-8")
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 2
    contract = [c for c in report["checks"] if c["id"] == "C1"][0]
    assert contract["status"] == "FAIL"


def test_missing_manifest_suggests_s0(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract(),
                                   manifest=False)
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    baseline = [c for c in report["checks"] if c["id"] == "C0"][0]
    assert baseline["status"] == "PARTIAL"
    assert "S0" in report["suggested_steps"]
    assert report["next_step"] == "S0"


def test_doc_drift_partial_s5(tmp_path, capsys):
    root, adapter_args = make_root(tmp_path, contract=full_contract(),
                                   doc_text="# v1.1\n\n- 待重启生效（M4 后维护窗口）\n")
    rc = _run(root, adapter_args, tmp_path)
    report = _report(capsys)
    assert rc == 1
    doc = [c for c in report["checks"] if c["id"] == "C7"][0]
    assert doc["status"] == "PARTIAL"
    assert "S5" in report["suggested_steps"]
