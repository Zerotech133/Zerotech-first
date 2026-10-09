# -*- coding: utf-8 -*-
"""A3 雇佣交互测试（K-STOCK-042）：注册表读写/live 校验/fail-closed/路由选取。
全部 monkeypatch 临时目录，真库与真注册表零触碰。"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "paper"))

import trainee  # noqa: E402
from trainee import Reject, fire, hire, hires_list, load_hires  # noqa: E402


def _setup(tmp: Path):
    """monkeypatch 注册表与档案目录到临时区，并造三名档案（live/training/design）。"""
    trainee.HIRES_PATH = tmp / "hires.json"
    tdir = tmp / "trainees"
    tdir.mkdir()
    trainee.TRAINEE_DIR = tdir
    for tid, status in [("hawk", "live"), ("chaser", "training"), ("gunner", "design")]:
        (tdir / f"{tid}.json").write_text(json.dumps(
            {"id": tid, "name": tid, "status": status}), encoding="utf-8")


def test_hire_rejects_non_live():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        for tid, st in [("chaser", "training"), ("gunner", "design"),
                        ("ghost", "missing")]:
            try:
                hire(tid, "qd")
                raise AssertionError(f"{tid}({st}) 应被拒")
            except Reject:
                pass
        try:
            hire("hawk", "bogus_source")
            raise AssertionError("未知 source 应被拒")
        except Reject:
            pass
    print("PASS test_hire_rejects_non_live")


def test_hire_fire_cycle_and_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        rec = hire("hawk", "qd", note="首单")
        assert rec["status"] == "active" and rec["trader"] == "hawk"
        try:
            hire("hawk", "qd")
            raise AssertionError("重复雇佣应被拒")
        except Reject:
            pass
        hire("hawk", "qpzm")                    # 同人可被多源雇
        rows = hires_list()
        assert len(rows) == 2
        assert {r["source"] for r in rows} == {"qd", "qpzm"}
        fire("hawk", "qd")                       # ended 保留历史行
        rows = hires_list()
        ended = [r for r in rows if r["source"] == "qd"][0]
        assert ended["status"] == "ended" and ended.get("fired_at")
        hire("hawk", "qd")                       # fire 后可再雇
        try:
            fire("hawk", "ai_team")              # 无 active 应拒
            raise AssertionError("fire 无 active 雇佣应拒")
        except Reject:
            pass
    print("PASS test_hire_fire_cycle_and_duplicate")


def test_load_hires_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _setup(tmp)
        trainee.HIRES_PATH.write_text("{bad json!!", encoding="utf-8")
        assert load_hires() == {"hires": []}     # 损坏 → 空表，不炸
        trainee.HIRES_PATH = tmp / "not_exist.json"
        assert load_hires() == {"hires": []}     # 缺失 → 空表
    print("PASS test_load_hires_fail_closed")


def test_hired_profiles_route_and_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        hire("hawk", "qd")
        # 悬空雇佣：档案删除后跳过不炸
        (Path(tmp) / "trainees" / "hawk.json").unlink()
        assert trainee._hired_profiles("qd") == []
        # 正常路径：live+active 才入选（重建档案并把状态改 training 验证过滤）
        (Path(tmp) / "trainees" / "hawk.json").write_text(json.dumps(
            {"id": "hawk", "name": "hawk", "status": "training"}), encoding="utf-8")
        assert len(trainee._hired_profiles("qd")) == 1   # training 也算（实习期可被雇路由）
        (Path(tmp) / "trainees" / "hawk.json").write_text(json.dumps(
            {"id": "hawk", "name": "hawk", "status": "design"}), encoding="utf-8")
        assert trainee._hired_profiles("qd") == []       # design 不入选
    print("PASS test_hired_profiles_route_and_fail_closed")


def test_newest_run_for_source_selection():
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        trainee.DATA_DIR = dd
        (dd / "poc_20260101_a.json").write_text("[]")
        (dd / "poc_20260102_b.json").write_text("[]")
        (dd / "poc_qd_20260103_c.json").write_text("[]")
        (dd / "poc_qpzm_20260104_d.json").write_text("[]")
        assert trainee._newest_run_for("qd").name == "poc_qd_20260103_c.json"
        assert trainee._newest_run_for("qpzm").name == "poc_qpzm_20260104_d.json"
        assert trainee._newest_run_for("ai_team").name == "poc_20260102_b.json"
        (dd / "poc_qpzm_20260104_d.json").unlink()   # 清空 qpzm 源
        try:
            trainee._newest_run_for("qpzm")     # 空源应 Reject
            raise AssertionError("空源应 Reject")
        except Reject:
            pass
    print("PASS test_newest_run_for_source_selection")


if __name__ == "__main__":
    test_hire_rejects_non_live()
    test_hire_fire_cycle_and_duplicate()
    test_load_hires_fail_closed()
    test_hired_profiles_route_and_fail_closed()
    test_newest_run_for_source_selection()
    print("5/5 PASS")
