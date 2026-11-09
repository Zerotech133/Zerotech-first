# -*- coding: utf-8 -*-
"""K-ORG-20261031-BUNDLEP2：bundle Phase2 dry-run 规划器测试（只读/零写入）。

覆盖：
- JSON 契约（schema/字段/计划步骤）与"未执行"语义；
- 消费方清单完整（nginx/前端/devtask/rebake/生成器/8103/启动器/静态测试/部署副本/容器挂载）；
- dry-run 零写入（文件 sha/mtime 不变、git 索引不变、bundle 仍 tracked）；
- L2 门禁：未批准 → blocked；--require-approved → exit 2；--l2-approved → exit 0；
- Phase2-A 可行性依赖容器挂载 _vault（注入伪造 docker 信息验证）；
- 非 git 仓库/极小仓库优雅降级。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "bundle_phase2_dryrun.py"
BUNDLE = ROOT / "portal" / "agent_ide_bundle.json"

REQUIRED_STEP_IDS = ("B1", "B2", "B3", "B4", "B5", "B6", "B7")
REQUIRED_CONSUMER_IDS = (
    "nginx_location", "frontend_fetch", "devtask_autorebuild", "rebake_script",
    "generator", "ide_serve_8103", "start_ide_script", "static_nginx_test",
    "vault_deploy_copy", "nginx_container_mount",
)


def _load():
    spec = importlib.util.spec_from_file_location("bundle_phase2_dryrun_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def _manifest():
    paths = [ROOT / ".gitignore", BUNDLE, ROOT / "portal" / "nginx.conf",
             ROOT / "ops" / "rebake_ide_bundle.ps1", ROOT / "tools" / "devtask.py"]
    snap = {}
    for p in paths:
        st = p.stat() if p.exists() else None
        snap[str(p)] = (st.st_size if st else 0, st.st_mtime_ns if st else 0, _sha(p))
    return snap


def _fake_mounts(vault_visible: bool):
    sources = [r"C:\项目\老田的AI助手\portal",
               r"C:\项目\老田的AI助手\portal\nginx.conf"]
    if vault_visible:
        sources.append(r"C:\项目\_vault\legion_ide")
    return {"available": True, "sources": sources,
            "portal_mounted": True, "vault_legion_ide_mounted": vault_visible}


def test_json_schema_and_blocked_by_default():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off", now="2026-10-31T12:00:00+08:00")
    assert res["schema"] == "bundle_phase2_dryrun/v1"
    assert res["ok"] is True
    assert res["generated_at"] == "2026-10-31T12:00:00+08:00"
    # dry-run 语义
    assert res["dry_run"]["executed"] is False
    assert res["dry_run"]["self_check"]["no_writes"] is True
    # L2 未批准 → blocked
    assert res["l2"]["approved"] is False
    assert res["l2"]["status"] == "blocked"
    assert res["execution"]["status"] == "blocked"
    # 推荐方案 B 且不需要 L2；A 需要
    assert res["recommended_option"] == m.OPTION_B
    opts = {o["id"]: o for o in res["options"]}
    assert opts[m.OPTION_B]["l2_required"] is False
    assert opts[m.OPTION_A]["l2_required"] is True
    # 计划 B1-B7 且全部未执行
    ids = [s["id"] for s in res["phase2_plan"]]
    assert ids == list(REQUIRED_STEP_IDS)
    assert all(s["executed"] is False for s in res["phase2_plan"])
    b2 = next(s for s in res["phase2_plan"] if s["id"] == "B2")
    assert "git rm --cached" in " ".join(b2["commands"])  # 仅命令文本，未执行
    assert "executed=False" not in json.dumps(res)  # 没有把执行态写成 true
    # 回滚清单存在
    assert "B_tracked_restore" in res["rollback"]


def test_consumers_complete():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off", docker_info=_fake_mounts(False))
    consumers = {c["id"]: c for c in res["consumers"]}
    for cid in REQUIRED_CONSUMER_IDS:
        assert cid in consumers, cid
        c = consumers[cid]
        for key in ("id", "kind", "path", "exists", "found", "detail"):
            assert key in c, (cid, key)
    # 关键消费方必须能在当前仓库命中
    assert consumers["nginx_location"]["found"] is True
    assert consumers["frontend_fetch"]["found"] is True
    assert consumers["devtask_autorebuild"]["found"] is True
    assert consumers["rebake_script"]["found"] is True
    assert consumers["generator"]["found"] is True
    assert consumers["ide_serve_8103"]["found"] is True
    assert consumers["static_nginx_test"]["found"] is True
    # 容器挂载探测：本机实际未挂 _vault
    mount = consumers["nginx_container_mount"]
    assert mount["vault_legion_ide_mounted"] is False


def test_phase2a_infeasible_without_vault_mount():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off", docker_info=_fake_mounts(False))
    opt_a = next(o for o in res["options"] if o["id"] == m.OPTION_A)
    assert opt_a["feasible"] is False
    assert any("未挂载" in b for b in opt_a["blockers"])
    assert any("PHASE2-A 不可行" in b for b in res["blockers"])


def test_phase2a_feasible_with_vault_mount():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off", docker_info=_fake_mounts(True))
    opt_a = next(o for o in res["options"] if o["id"] == m.OPTION_A)
    assert opt_a["feasible"] is True
    assert not opt_a["blockers"]


def test_dry_run_zero_write_and_no_git_mutation():
    m = _load()
    before = _manifest()
    ls_before = _git("ls-files", "-s", "portal/agent_ide_bundle.json").stdout
    status_before = _git("status", "--porcelain", "--", "portal/agent_ide_bundle.json").stdout
    res = m.analyze(repo=ROOT, docker_mode="off")
    after = _manifest()
    assert res["dry_run"]["self_check"]["no_writes"] is True
    assert before == after, "dry-run 修改了文件（size/mtime/sha 变化）"
    assert _git("ls-files", "-s", "portal/agent_ide_bundle.json").stdout == ls_before
    assert _git("status", "--porcelain", "--", "portal/agent_ide_bundle.json").stdout == status_before
    # Phase2-B 已在 worktree 落地：bundle 不再 tracked、且被 .gitignore 覆盖
    assert res["tracked"]["tracked"] is False
    assert res["tracked"]["ignored"] is True


def test_implementation_b1_b4_landed():
    """Phase2-B 实现状态：B1-B4 应全部 landed，但步骤仍未"执行"（dry-run 语义）。"""
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off")
    impl = res["implementation"]
    assert impl["done"] is True
    for key in ("b1_gitignore", "b2_untracked", "b3_rebake_no_checkout",
                "b4_devtask_no_commit", "deploy_copy_helper"):
        assert impl[key] is True, key
    assert not any("W5-P2-1" in b or "W5-P2-2" in b for b in res["blockers"])
    landed = {s["id"]: s["landed"] for s in res["phase2_plan"]}
    assert landed["B1"] and landed["B2"] and landed["B3"] and landed["B4"]
    assert all(s["executed"] is False for s in res["phase2_plan"])


def test_l2_approved_flag_changes_status_but_not_execution():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off", l2_approved=True)
    assert res["l2"]["approved"] is True
    assert res["l2"]["status"] == "approved"
    assert res["execution"]["status"] == "plan_ready"
    assert res["dry_run"]["executed"] is False
    assert res["dry_run"]["self_check"]["no_writes"] is True


def test_cli_require_approved_exit_codes(capsys):
    m = _load()
    rc = m.main(["--json", "--docker", "off", "--require-approved"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["execution"]["status"] == "blocked"

    rc = m.main(["--json", "--docker", "off", "--require-approved", "--l2-approved"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["execution"]["status"] == "plan_ready"
    assert out["dry_run"]["executed"] is False


def test_cli_json_is_single_valid_document(capsys):
    m = _load()
    rc = m.main(["--json", "--docker", "off", "--now", "2026-10-31T00:00:00+08:00"])
    assert rc == 0
    captured = capsys.readouterr().out
    res = json.loads(captured)  # 必须整段都是 JSON
    assert res["schema"] == "bundle_phase2_dryrun/v1"
    assert res["generated_at"] == "2026-10-31T00:00:00+08:00"


def test_human_output_mentions_dry_run_and_blockers(capsys):
    m = _load()
    rc = m.main(["--docker", "off"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "只读" in text and "未执行" in text
    assert "L2 门禁: blocked" in text
    assert "推荐方案: " + m.OPTION_B in text
    assert "Phase2-B 实现: done=True" in text


def test_non_git_repo_degrades(tmp_path):
    m = _load()
    res = m.analyze(repo=tmp_path, docker_mode="off")
    assert res["ok"] is False
    assert res["error"] == "not_a_git_repo"
    assert res["execution"]["dry_run"] is True


def test_churn_window_param():
    m = _load()
    res8 = m.analyze(repo=ROOT, days=8, docker_mode="off")
    res1 = m.analyze(repo=ROOT, days=1, docker_mode="off")
    assert res8["churn"]["window_days"] == 8
    assert res1["churn"]["window_days"] == 1
    assert isinstance(res8["churn"]["bundle_commits"], int)
    assert res8["churn"]["total_commits"] >= res1["churn"]["total_commits"]
    if res8["churn"]["ratio"] is not None:
        assert 0.0 <= res8["churn"]["ratio"] <= 1.0


def test_vault_copy_consistency_field():
    m = _load()
    res = m.analyze(repo=ROOT, docker_mode="off")
    vc = res["vault_copy"]
    assert "exists" in vc and "same_as_repo_copy" in vc
    if vc["exists"]:
        assert isinstance(vc["sha256"], str) and len(vc["sha256"]) == 64
