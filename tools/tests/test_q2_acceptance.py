# -*- coding: utf-8 -*-
"""S2 Q2 验收编排测试（只读、不伪造登录、不连网；dw_builder 注入替身）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import q2_acceptance as qa  # noqa: E402

Q1 = qa.Q1_LEGIONS


def _evidence_md(cid: str, signed: bool = False) -> str:
    if signed:
        return ("# %s Q2 证据包\n\n## 签署（已完成）\n\n"
                "- owner：DATA1｜日期：2026-11-03｜结论：通过\n" % cid)
    return ("# %s Q2 证据包\n\n## 签署（待 owner）\n\n"
            "- owner：____｜日期：____｜结论：____\n" % cid)


def _fixture(tmp_path: Path, *, auto_ok: bool = True, signed=(), drop_matrix=False,
             corrupt=False, drop_evidence=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    q1_dir = tmp_path / "q2"
    q1_dir.mkdir(parents=True, exist_ok=True)
    signed = set(signed)
    drop_evidence = set(drop_evidence)
    for cid in Q1:
        if cid in drop_evidence:
            continue
        (q1_dir / ("%s_Q2_evidence_v1.md" % cid)).write_text(
            _evidence_md(cid, cid in signed), encoding="utf-8")
    matrix = {
        "schema": "q2/evidence-pack/v1",
        "generated_at": "2026-11-03T06:00:00+08:00",
        "read_only": True,
        "legions": [
            {"id": cid, "owner": "OWNER-" + cid, "auto_ok": auto_ok,
             "q2_status": "AUTO-EVIDENCE-COLLECTED（待 owner 验收/签署）",
             "manual_gate": "gate-" + cid, "probes": [], "tests": [],
             "generated_at": "2026-11-03T06:00:00+08:00",
             "manifest": "%s/manifest.yaml" % cid}
            for cid in Q1
        ],
        "summary": {"total": len(Q1)},
    }
    mpath = tmp_path / "matrix.json"
    if not drop_matrix:
        mpath.write_text("{" if corrupt else json.dumps(matrix, ensure_ascii=False),
                         encoding="utf-8")
    return mpath, q1_dir


def _fake_dw(ok: bool = True, auth: str = "PASS") -> dict:
    return {
        "schema": "dw/q2-probe/v1",
        "auth_state": auth,
        "ok": ok,
        "checks": [{"name": "api /api/dw/stats", "got": 200, "ms": 5.0, "ok": True}],
        "authenticated_pages": [{"path": "/dw", "got": 200, "ms": 4.0, "ok": True},
                                {"path": "/dataops", "got": 200, "ms": 4.0, "ok": True}],
        "slo": {"api_p95_ms": 5.0, "api_p95_ms_lt": 800, "slo_ok": True},
    }


# 1) 无 cookie：PENDING-LOGIN，不伪造登录
def test_no_cookie_is_pending_login_not_fake(monkeypatch, tmp_path, capsys):
    mpath, q1_dir = _fixture(tmp_path)
    monkeypatch.setattr(qa, "DEFAULT_COOKIE_FILE", tmp_path / "no_session.txt")
    rc = qa.main(["--json", "--matrix", str(mpath), "--q1-dir", str(q1_dir)])
    assert rc == qa.RC_PENDING
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["session"]["provided"] is False
    assert payload["dw"]["auth_state"] == "PENDING-LOGIN"
    assert payload["dw"]["ok"] is False
    assert "不伪造" in payload["dw"]["note"]
    assert payload["conclusion"]["state"] == "PENDING"
    assert payload["conclusion"]["auto_evidence"] == "PENDING"
    assert payload["conclusion"]["owner_signoff"] == "PENDING"


# 2) cookie 文件解析 + DW 探针注入 + 不泄漏 cookie
def test_cookie_file_parsing_and_dw_builder(tmp_path):
    f = tmp_path / "zb_session.txt"
    f.write_text("zb_session=SECRET123\n", encoding="utf-8")
    sess = qa.resolve_session(cookie_file=str(f), explicit=True)
    assert sess["provided"] is True and sess["cookie"] == "zb_session=SECRET123"
    f.write_text("plainvalue", encoding="utf-8")
    assert qa.resolve_session(cookie_file=str(f), explicit=True)["cookie"] == "zb_session=plainvalue"
    missing = qa.resolve_session(cookie_file=str(tmp_path / "nope.txt"), explicit=True)
    assert missing["provided"] is False and missing["error"]

    captured = {}

    def fake_builder(cookie):
        captured["cookie"] = cookie
        return _fake_dw()

    mpath, q1_dir = _fixture(tmp_path)
    payload = qa.build("zb_session=SECRET123", cookie_source="file",
                       dw_builder=fake_builder, matrix_path=mpath, q1_dir=q1_dir)
    assert captured["cookie"] == "zb_session=SECRET123"
    assert payload["dw"]["auth_state"] == "PASS"
    assert payload["session"]["provided"] is True
    # 安全：任何输出不得包含 cookie 值
    assert "SECRET123" not in json.dumps(payload, ensure_ascii=False)


# 3) Q1 状态汇总（4 军团/owner/自动证据/签署/缺口）
def test_q1_status_summary(tmp_path):
    mpath, q1_dir = _fixture(tmp_path, signed={"lusu-d1-flow"})
    payload = qa.build("", dw_builder=lambda c: _fake_dw(),
                       matrix_path=mpath, q1_dir=q1_dir)
    rows = payload["q1"]
    assert [r["id"] for r in rows] == list(Q1)
    assert all(r["owner"].startswith("OWNER-") for r in rows)
    assert all(r["auto_ok"] and r["evidence_file_exists"] and not r["gaps"] for r in rows)
    by = {r["id"]: r for r in rows}
    assert by["lusu-d1-flow"]["owner_signoff"] == "SIGNED"
    assert by["quant-domain"]["owner_signoff"] == "PENDING"

    mpath2, q1_dir2 = _fixture(tmp_path / "drop", drop_evidence={"quant-domain"})
    payload2 = qa.build("zb_session=x", dw_builder=lambda c: _fake_dw(),
                        matrix_path=mpath2, q1_dir=q1_dir2)
    row = next(r for r in payload2["q1"] if r["id"] == "quant-domain")
    assert row["evidence_file_exists"] is False
    assert "evidence-file-missing" in row["gaps"]
    assert payload2["conclusion"]["auto_evidence"] == "PENDING"  # 证据包缺失 → 不得 PASS


# 4) JSON / Markdown 输出 + --out 文件
def test_json_markdown_output_and_out_file(monkeypatch, tmp_path, capsys):
    mpath, q1_dir = _fixture(tmp_path)
    monkeypatch.setattr(qa, "DEFAULT_COOKIE_FILE", tmp_path / "no_session.txt")
    assert qa.main(["--json", "--matrix", str(mpath), "--q1-dir", str(q1_dir)]) == qa.RC_PENDING
    payload = json.loads(capsys.readouterr().out)
    for key in ("schema", "session", "dw", "q1", "summary", "conclusion"):
        assert key in payload

    assert qa.main(["--markdown", "--matrix", str(mpath), "--q1-dir", str(q1_dir)]) == qa.RC_PENDING
    md = capsys.readouterr().out
    assert "# Q2 验收编排" in md and "OWNER-SIGNOFF" in md and "PENDING-LOGIN" in md

    out = tmp_path / "out.json"
    assert qa.main(["--json", "--out", str(out), "--matrix", str(mpath),
                    "--q1-dir", str(q1_dir)]) == qa.RC_PENDING
    assert out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["conclusion"]["owner_signoff"] == "PENDING"


# 5) fail-visible：证据矩阵缺失/损坏 → ERROR rc=3
def test_fail_visible_matrix_missing_or_corrupt(tmp_path):
    mpath, q1_dir = _fixture(tmp_path, drop_matrix=True)
    payload = qa.build("", matrix_path=mpath, q1_dir=q1_dir)
    assert payload["conclusion"]["state"] == "ERROR"
    assert payload["errors"] and qa.exit_code(payload) == qa.RC_ERROR

    mpath2, q1_dir2 = _fixture(tmp_path / "corrupt", corrupt=True)
    payload2 = qa.build("", matrix_path=mpath2, q1_dir=q1_dir2)
    assert payload2["conclusion"]["state"] == "ERROR"
    assert qa.exit_code(payload2) == qa.RC_ERROR


# 6) 显式 --cookie-file 缺失 → fail-visible rc=3
def test_explicit_cookie_file_missing_is_error(tmp_path, capsys):
    mpath, q1_dir = _fixture(tmp_path)
    rc = qa.main(["--json", "--cookie-file", str(tmp_path / "missing_session.txt"),
                  "--matrix", str(mpath), "--q1-dir", str(q1_dir)])
    assert rc == qa.RC_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["conclusion"]["state"] == "ERROR"
    assert any("会话文件不可用" in e for e in payload["errors"])


# 7) 自动证据 PASS 与 OWNER-SIGNOFF 区分（本工具不代签）
def test_auto_pass_still_separates_owner_signoff(tmp_path):
    mpath, q1_dir = _fixture(tmp_path)
    payload = qa.build("zb_session=x", dw_builder=lambda c: _fake_dw(),
                       matrix_path=mpath, q1_dir=q1_dir)
    assert payload["conclusion"]["auto_evidence"] == "PASS"
    assert payload["conclusion"]["owner_signoff"] == "PENDING"   # 未签不伪造
    assert payload["conclusion"]["state"] == "AUTO-EVIDENCE-PASS"
    assert qa.exit_code(payload) == qa.RC_PASS
    assert "OWNER-SIGNOFF" in payload["conclusion"]["statement"]

    signed_dir = tmp_path / "signed"
    mpath2, q1_dir2 = _fixture(signed_dir, signed=set(Q1))
    payload2 = qa.build("zb_session=x", dw_builder=lambda c: _fake_dw(),
                        matrix_path=mpath2, q1_dir=q1_dir2)
    assert payload2["conclusion"]["owner_signoff"] == "SIGNED"
    assert payload2["conclusion"]["auto_evidence"] == "PASS"


# 8) DW 探针异常 → fail-visible rc=3
def test_dw_probe_exception_is_fail_visible(tmp_path):
    mpath, q1_dir = _fixture(tmp_path)

    def boom(cookie):
        raise RuntimeError("probe boom")

    payload = qa.build("zb_session=x", dw_builder=boom,
                       matrix_path=mpath, q1_dir=q1_dir)
    assert payload["conclusion"]["state"] == "ERROR"
    assert any("DW 探针异常" in e for e in payload["errors"])
    assert qa.exit_code(payload) == qa.RC_ERROR
