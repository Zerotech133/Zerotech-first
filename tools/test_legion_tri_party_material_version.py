# -*- coding: utf-8 -*-
"""会议材料指纹和并发确认的离线反例。"""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import legion_tri_party_meeting as meeting  # noqa: E402


def _create(directory, meeting_id="material-test", context="原始材料"):
    return meeting.create_meeting("会议主题", context, meeting_id=meeting_id,
                                  dry_run=True, data_dir=directory, audit=False)


def test_fingerprint_is_canonical_and_visible_on_create_and_load(tmp_path):
    created = _create(tmp_path)
    material = {key: created[key] for key in ("subject", "context", "views", "disagreements")}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    assert created["material_fingerprint"] == digest
    assert meeting.load_meeting("material-test", data_dir=tmp_path)["material_fingerprint"] == digest
    assert meeting.list_meetings(data_dir=tmp_path)[0]["meeting_id"] == "material-test"
    assert _create(tmp_path / "other", context="新材料")["material_fingerprint"] != digest


def test_missing_and_wrong_fingerprint_do_not_change_archive(tmp_path):
    created = _create(tmp_path)
    path = tmp_path / "material-test.json"
    original = path.read_bytes()
    with pytest.raises(meeting.MeetingError, match="expected_material_fingerprint"):
        meeting.update_confirmation("material-test", "confirmed", "自报人", data_dir=tmp_path, audit=False)
    with pytest.raises(meeting.MeetingError, match="expected_material_fingerprint"):
        meeting.update_confirmation("material-test", "confirmed", "自报人",
                                    expected_material_fingerprint="", data_dir=tmp_path, audit=False)
    with pytest.raises(meeting.MeetingError, match="指纹不匹配"):
        meeting.update_confirmation("material-test", "confirmed", "自报人",
                                    expected_material_fingerprint="0" * 64, data_dir=tmp_path, audit=False)
    assert path.read_bytes() == original
    assert created["confirmation"]["status"] == "pending"


def test_duplicate_id_rejected_without_overwriting_even_concurrently(tmp_path):
    original = _create(tmp_path)
    path = tmp_path / "material-test.json"
    content = path.read_bytes()
    with pytest.raises(meeting.MeetingError, match="已存在"):
        _create(tmp_path, context="覆盖")
    assert path.read_bytes() == content
    def attempt(number):
        try:
            _create(tmp_path, meeting_id="new-race", context=str(number))
            return "ok"
        except meeting.MeetingError:
            return "error"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("ok") == 1
    assert meeting.load_meeting("material-test", data_dir=tmp_path) == original


def test_legacy_material_calculated_but_historical_confirmation_not_rebound(tmp_path):
    created = _create(tmp_path)
    path = tmp_path / "material-test.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    old.pop("material_fingerprint")
    old["confirmation"] = {"status": "confirmed", "confirmed_by": "历史自报", "confirmed_at": "yesterday", "note": "旧签"}
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    loaded = meeting.load_meeting("material-test", data_dir=tmp_path)
    assert loaded["material_fingerprint"] == created["material_fingerprint"]
    assert "material_fingerprint" not in loaded["confirmation"]
    with pytest.raises(meeting.MeetingError, match="不得覆盖"):
        meeting.update_confirmation("material-test", "confirmed", "新自报",
                                    expected_material_fingerprint=loaded["material_fingerprint"],
                                    data_dir=tmp_path, audit=False)
    assert "material_fingerprint" not in json.loads(path.read_text(encoding="utf-8"))["confirmation"]


def test_legacy_pending_can_bind_real_material_and_self_report_is_unverified(tmp_path):
    created = _create(tmp_path)
    path = tmp_path / "material-test.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    old.pop("material_fingerprint")
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    result = meeting.update_confirmation("material-test", "confirmed", "自报名字",
                                         expected_material_fingerprint=created["material_fingerprint"],
                                         data_dir=tmp_path, audit=False)
    assert result["confirmation"]["material_fingerprint"] == created["material_fingerprint"]
    assert result["confirmation"]["verification_status"] == "unverified"
    assert result["execution_policy"]["auto_dispatch"] is False
    assert result["execution_policy"]["dispatch_status"] == "not_requested"
    assert result["execution_policy"]["requires_user_confirmation"] is True


def test_tampered_material_is_rejected(tmp_path):
    created = _create(tmp_path)
    path = tmp_path / "material-test.json"
    altered = json.loads(path.read_text(encoding="utf-8"))
    altered["context"] = "被更换的材料"
    path.write_text(json.dumps(altered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(meeting.MeetingError, match="指纹与实存材料不一致"):
        meeting.update_confirmation("material-test", "confirmed", "自报人",
                                    expected_material_fingerprint=created["material_fingerprint"],
                                    data_dir=tmp_path, audit=False)


def test_two_processes_competing_confirmations_only_one_wins(tmp_path):
    created = _create(tmp_path, meeting_id="race-test")
    fingerprint = created["material_fingerprint"]
    script = (
        "import json,sys; from legion_tri_party_meeting import update_confirmation,MeetingError; "
        "d,f,b=sys.argv[1:]; "
        "\ntry:\n r=update_confirmation('race-test','confirmed',b,"
        "expected_material_fingerprint=f,data_dir=d,audit=False); "
        "print(json.dumps(['ok',r['confirmation']['confirmed_by']]))"
        "\nexcept MeetingError as e: print(json.dumps(['error',str(e)]))"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
    workers = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), fingerprint, by],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
               for by in ("甲", "乙")]
    outputs = [worker.communicate(timeout=15) for worker in workers]
    assert all(worker.returncode == 0 for worker in workers), outputs
    outcomes = [json.loads(output.strip()) for output, _ in outputs]
    assert sorted(item[0] for item in outcomes) == ["error", "ok"]
    assert "不得覆盖" in next(item[1] for item in outcomes if item[0] == "error")
    winner = next(item[1] for item in outcomes if item[0] == "ok")
    saved = meeting.load_meeting("race-test", data_dir=tmp_path)
    assert saved["confirmation"]["confirmed_by"] == winner
    assert saved["confirmation"]["material_fingerprint"] == fingerprint
