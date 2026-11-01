# -*- coding: utf-8 -*-
"""11-03 组织落地复核只读聚合器测试（2026-11-01 WO-ORG-LANDING-REVIEW-20261101）。

覆盖：全 PASS（exit 0）／部分缺（PARTIAL，exit 1）／硬缺口+坏 JSON+缺文件（FAIL，
不崩，exit 1）／坏 root 与内部异常（exit 2）／只读零写／真实仓只读 smoke。

纪律：夹具全落 tmp（`--root` 指向临时仓骨架），不读写真实 approvals/派工/
deliverables；只读用例用文件快照（内容哈希+路径集合）断言零写。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import m4_signoff_status as M4  # noqa: E402
import org_landing_review as O  # noqa: E402
import seat_iteration_receipts as R  # noqa: E402

SEATS = list(R.SEATS)


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def _seat_cfg(root, seat):
    """一席完整迭代配置（evidence 非空/导师确认/阶段 passed）。"""
    doc = {
        "schema": R.SCHEMA_SEAT_ITER, "seat": seat, "cn_name": seat,
        "mentor": "ARCH1", "version": 1, "baseline": {}, "history": [],
        "updated_at": "2026-11-01",
        "iteration": {
            "id": "IT1", "start": "2026-10-29", "target_capability": "fixture",
            "learning": {"status": "passed", "evidence": "cmd:fixture:learning"},
            "practice": {"status": "passed", "evidence": "cmd:fixture:practice"},
            "assessment": {"status": "passed", "mentor": "ARCH1",
                           "evidence": "cmd:fixture:assessment",
                           "mentor_confirmed": True},
        },
    }
    _write_json(Path(root) / "agent_platform" / "config" / "seat_iterations"
                / (seat + ".json"), doc)


def _dispatch_row(seat, status, task=None, at="2026-11-01T08:00:00+0800"):
    return {"schema": "tianfeng_dispatch/v1",
            "id": "dsp-%s-%s" % (seat, status), "to": "研发部/" + seat,
            "task": task or ("%s 任务" % seat), "status": status,
            "at": at, "note": ""}


def _write_dispatch(root, rows, extra_raw=""):
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    _write(Path(root) / "work" / "tianfeng_dispatch.jsonl", body + extra_raw)


def _write_m4(root, complete=True):
    """写 approvals sidecar + 签字角色映射 + M4 必需证据文件（只读夹具）。"""
    roles = list(M4.DEFAULT_REQUIRED)
    mapping = {"ou_" + role.lower(): role for role in roles}
    _write_json(Path(root) / "agent_platform" / "config" / "approval_signers.json",
                {"map": mapping, "note": "fixture"})
    rows = []
    for i, role in enumerate(roles if complete else roles[:1]):
        rows.append({"ts": "2026-11-01T09:%02d:00" % i, "schema": "approval/v1",
                     "task": M4.DEFAULT_TASK, "action": "approve",
                     "operator": "ou_" + role.lower(), "level": "S2"})
    _write(Path(root) / "agent_platform" / "data" / "approvals.jsonl",
           "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    for spec in M4.EVIDENCE_SPECS:
        if spec.get("required"):
            _write(Path(root) / spec["candidates"][0],
                   "fixture evidence for %s\n" % spec["key"])


def _write_v11(root, docs=True, signed=True):
    base = Path(root) / "deliverables"
    if docs:
        _write(base / "2026-10-31_v1.1批文决策包_v1.md", "# 决策包\n批文 0/1（fixture）\n")
        _write(base / "2026-10-31_研发计划_11月01至11月15_v1.md", "# 计划\n")
        _write(base / "2026-11-01_v1.1_M0契约冻结执行清单_v1.md", "# M0\n")
    if signed:
        _write(base / "2026-11-02_v1.1批文签署回执_v1.md", "# 回执\nV11: 批文=签\n")


def _full_fixture(root):
    for seat in SEATS:
        _seat_cfg(root, seat)
    rows = [_dispatch_row(seat, "done") for seat in SEATS]
    for seat in O.IFACE_SEATS + O.MENTOR_PAIR + O.DELIVERY_PAIR:
        rows.append(_dispatch_row(seat, "done"))
    _write_dispatch(root, rows)
    _write_m4(root, complete=True)
    _write_v11(root, docs=True, signed=True)


def _item(review, item_id):
    return next(it for it in review["items"] if it["id"] == item_id)


def _snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


# ----------------------------------------------------------------------
# 判据 1：全 PASS（exit 0）
# ----------------------------------------------------------------------
def test_all_pass_exit0(tmp_path, capsys):
    _full_fixture(tmp_path)
    review = O.build_review(tmp_path)
    assert review["schema"] == O.SCHEMA
    assert review["read_only"] is True
    assert review["overall"] == O.PASS, [
        (it["id"], it["status"], it["reasons"]) for it in review["items"]]
    assert review["summary"] == {"total": 6, O.PASS: 6, O.PARTIAL: 0, O.FAIL: 0}
    assert all(it["status"] == O.PASS for it in review["items"])
    # CLI JSON + 人类摘要 + exit 0
    rc = O.main(["--root", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["overall"] == O.PASS
    rc2 = O.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc2 == 0
    assert "11-03 组织落地硬复核" in out and "[PASS] 11 席能力迭代回执" in out


# ----------------------------------------------------------------------
# 判据 2：部分缺（PARTIAL，exit 1）
# ----------------------------------------------------------------------
def test_partial_items_exit1(tmp_path, capsys):
    _full_fixture(tmp_path)
    # ① v1.1 批文未签（仅决策包/计划/M0）
    (tmp_path / "deliverables" / "2026-11-02_v1.1批文签署回执_v1.md").unlink()
    # ② 接口人 PM1 仍 dispatched（未回执）
    rows = [_dispatch_row(seat, "done") for seat in SEATS]
    for seat in O.IFACE_SEATS + O.MENTOR_PAIR + O.DELIVERY_PAIR:
        rows.append(_dispatch_row(seat, "dispatched" if seat == "PM1" else "done"))
    _write_dispatch(tmp_path, rows)
    review = O.build_review(tmp_path)
    assert review["overall"] == O.PARTIAL
    assert _item(review, "v11_batch")["status"] == O.PARTIAL
    assert _item(review, "iface_seats")["status"] == O.PARTIAL
    assert any("PM1" in r for r in _item(review, "iface_seats")["reasons"])
    assert _item(review, "seat_receipts")["status"] == O.PASS
    rc = O.main(["--root", str(tmp_path), "--json"])
    assert rc == 1
    capsys.readouterr()


def test_seat_receipts_partial_exit1(tmp_path, capsys):
    _full_fixture(tmp_path)
    # 一席 evidence 清空 → 11 席回执 10/11 → PARTIAL
    p = tmp_path / "agent_platform" / "config" / "seat_iterations" / "DEV7.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    for stage in ("learning", "practice", "assessment"):
        doc["iteration"][stage]["evidence"] = ""
    doc["iteration"]["assessment"].pop("mentor_confirmed", None)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    review = O.build_review(tmp_path)
    assert _item(review, "seat_receipts")["status"] == O.PARTIAL
    assert review["overall"] == O.PARTIAL
    rc = O.main(["--root", str(tmp_path)])
    assert rc == 1
    capsys.readouterr()


# ----------------------------------------------------------------------
# 判据 3：硬缺口/坏 JSON/缺文件（FAIL，不崩，exit 1）
# ----------------------------------------------------------------------
def test_hard_gaps_fail_not_crash(tmp_path, capsys):
    _full_fixture(tmp_path)
    # 接口人 QA1 blocked → 组 FAIL
    rows = [_dispatch_row(seat, "done") for seat in SEATS]
    for seat in O.IFACE_SEATS + O.MENTOR_PAIR + O.DELIVERY_PAIR:
        rows.append(_dispatch_row(seat, "blocked" if seat == "QA1" else "done"))
    _write_dispatch(tmp_path, rows, extra_raw="{bad json\n[]\n")
    # M4 必需证据缺失 + 签字不足 → FAIL
    for spec in M4.EVIDENCE_SPECS:
        if spec.get("required"):
            (tmp_path / spec["candidates"][0]).unlink()
    _write_m4(tmp_path, complete=False)
    # v1.1 文件全缺 → FAIL
    for p in (tmp_path / "deliverables").glob("*.md"):
        p.unlink()
    review = O.build_review(tmp_path)
    assert review["overall"] == O.FAIL
    assert _item(review, "iface_seats")["status"] == O.FAIL
    assert "QA1" in " ".join(_item(review, "iface_seats")["reasons"])
    assert _item(review, "m4_signoff")["status"] == O.FAIL
    assert _item(review, "v11_batch")["status"] == O.FAIL
    assert review["dispatch_source"]["bad_lines"] == 2
    assert review["dispatch_source"]["exists"] is True
    rc = O.main(["--root", str(tmp_path), "--json"])
    assert rc == 1
    capsys.readouterr()


def test_missing_files_and_bad_ledger_not_crash(tmp_path, capsys):
    # 空 root：无 config/无派工/无 approvals/无 deliverables
    review = O.build_review(tmp_path)
    assert review["schema"] == O.SCHEMA
    assert review["summary"]["total"] == 6
    assert review["overall"] == O.FAIL
    assert _item(review, "seat_receipts")["status"] == O.FAIL
    assert _item(review, "m4_signoff")["status"] == O.FAIL
    assert _item(review, "v11_batch")["status"] == O.FAIL
    assert review["dispatch_source"]["exists"] is False
    rc = O.main(["--root", str(tmp_path), "--json"])
    assert rc == 1
    capsys.readouterr()


# ----------------------------------------------------------------------
# 判据 4：自身错误 exit 2
# ----------------------------------------------------------------------
def test_bad_root_exit2(tmp_path):
    assert O.main(["--root", str(tmp_path / "nope")]) == 2


def test_internal_error_exit2(tmp_path, monkeypatch, capsys):
    def boom(root=None, dispatch_path=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(O, "build_review", boom)
    assert O.main(["--root", str(tmp_path)]) == 2
    assert "内部错误" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 判据 5：只读零写
# ----------------------------------------------------------------------
def test_read_only_zero_write(tmp_path, capsys):
    _full_fixture(tmp_path)
    before = _snapshot(tmp_path)
    rc1 = O.main(["--root", str(tmp_path), "--json"])
    rc2 = O.main(["--root", str(tmp_path)])
    assert rc1 == 0 and rc2 == 0
    assert "PASS" in capsys.readouterr().out
    after = _snapshot(tmp_path)
    assert after == before, "只读聚合器不得修改/新增/删除任何文件"


# ----------------------------------------------------------------------
# 判据 6：派工组规则单测 + 真实仓只读 smoke
# ----------------------------------------------------------------------
def test_dispatch_group_status_mapping():
    meta = {"path": "fixture", "exists": True, "rows": 2, "bad_lines": 0}
    ok = {s: _dispatch_row(s, "done") for s in O.IFACE_SEATS}
    item = O._dispatch_group_item("iface", "接口人", O.IFACE_SEATS, ok, meta)
    assert item["status"] == O.PASS
    mid = dict(ok)
    mid["PM1"] = _dispatch_row("PM1", "dispatched")
    item = O._dispatch_group_item("iface", "接口人", O.IFACE_SEATS, mid, meta)
    assert item["status"] == O.PARTIAL
    bad = dict(ok)
    bad["DSGN1"] = _dispatch_row("DSGN1", "blocked")
    item = O._dispatch_group_item("iface", "接口人", O.IFACE_SEATS, bad, meta)
    assert item["status"] == O.FAIL
    ghost = {s: ok[s] for s in ("PM1", "DSGN1")}
    item = O._dispatch_group_item("iface", "接口人", O.IFACE_SEATS, ghost, meta)
    assert item["status"] == O.FAIL and any("QA1" in r for r in item["reasons"])


def test_current_repo_smoke_readonly():
    """真实仓 smoke：6 项、可 JSON 序列化、read_only；不写盘。"""
    review = O.build_review(ROOT)
    assert review["schema"] == O.SCHEMA
    assert len(review["items"]) == 6
    assert review["read_only"] is True
    json.dumps(review, ensure_ascii=False)
    assert review["overall"] in (O.PASS, O.PARTIAL, O.FAIL)
