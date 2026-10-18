# -*- coding: utf-8 -*-
"""pre_observation（周一 17:25 前置一键）测试。

用 subprocess + sys.executable 调真实 CLI（env PYTHONIOENCODING=utf-8），覆盖：
  1. --dry-run 不产生任何写入（tmp 根快照前后一致，连 evidence/ 都不出现）；
  2. 退出码归一：observation 子工具 rc=1（全 PENDING 中性）→ 不判 FAIL；
     含端到端实跑（tmp 根上巡检自然返回 1，wrapper 必须 rc 0）；
  3. --json 可 json.loads，steps 恒为三段且顺序固定；
  4. 非法参数 / 不存在的 root → rc 2；
  5. 实跑只把证据仓写进 tmp 数据根（store 注入生效，不落真仓）。

红线：本文件所有 root 一律 tmp_path 隔离，绝不指向真仓 data/；
绝不跑计划任务、绝不改任何既有文件。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

AP = Path(__file__).resolve().parent.parent          # .../agent_platform
TOOL = AP / "tools" / "pre_observation.py"


def make_repo(tmp_path: Path) -> Path:
    """最小隔离仓库：只有 <repo>/agent_platform/data 目录（空）。"""
    repo = tmp_path / "repo"
    (repo / "agent_platform" / "data").mkdir(parents=True)
    return repo


def run_cli(repo_root: Path, *args):
    """调 CLI；cwd=agent_platform（与现场一致），输出强制 utf-8。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(repo_root), *args],
        cwd=str(AP),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def snapshot(root: Path):
    """目录树快照：相对路径 → (mtime_ns, size[, is_dir])；含目录本身。"""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        try:
            st = p.stat()
        except OSError:
            continue
        out[rel] = (st.st_mtime_ns, st.st_size, p.is_dir())
    return out


def load_module():
    """直接加载工具模块，单测纯函数（禁写 pycache，避免碰真仓）。"""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("pre_observation_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 1. dry-run
def test_dry_run_writes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    before = snapshot(repo)

    r = run_cli(repo, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert snapshot(repo) == before
    assert not (repo / "agent_platform" / "data" / "evidence").exists()

    out = r.stdout
    # 三段顺序固定：收编 → 判读 → 复核
    i1 = out.index("证据收编")
    i2 = out.index("观察点判读")
    i3 = out.index("账本独立复核")
    assert i1 < i2 < i3
    assert "未执行任何命令、未写任何文件" in out


# ------------------------------------------------- 2. 退出码归一（核心正确性）
def test_normalize_rc_observation_pending_is_not_fail():
    mod = load_module()
    assert mod.normalize_rc(mod.STEP_OBSERVE, 0) == mod.PASS
    # 关键：全 PENDING 中性 = 1，绝不能判 FAIL
    assert mod.normalize_rc(mod.STEP_OBSERVE, 1) == mod.PENDING
    assert mod.normalize_rc(mod.STEP_OBSERVE, 2) == mod.FAIL
    # 同一个 1 在对账工具里是真红
    assert mod.normalize_rc(mod.STEP_VERIFY, 1) == mod.FAIL
    assert mod.normalize_rc(mod.STEP_VERIFY, 0) == mod.PASS
    # 未登记组合 / 异常退出 → ERROR（不冒充 FAIL，也不冒充 PASS）
    assert mod.normalize_rc(mod.STEP_OBSERVE, None) == mod.ERROR
    assert mod.normalize_rc(mod.STEP_OBSERVE, -1) == mod.ERROR
    assert mod.normalize_rc(mod.STEP_OBSERVE, 7) == mod.ERROR


def test_aggregate_pending_maps_to_zero():
    mod = load_module()
    assert mod.aggregate([mod.PENDING, mod.PASS, mod.SKIPPED]) == (
        mod.PASS_WITH_PENDING, 0)
    assert mod.aggregate([mod.PENDING, mod.PENDING]) == (mod.PASS_WITH_PENDING, 0)
    assert mod.aggregate([mod.PASS, mod.FAIL]) == (mod.FAIL, 1)
    assert mod.aggregate([mod.FAIL, mod.ERROR]) == (mod.ERROR, 2)
    assert mod.aggregate([mod.PASS, mod.SKIPPED]) == (mod.PASS, 0)


def test_end_to_end_pending_subtool_does_not_fail_wrapper(tmp_path):
    """tmp 空 root 上巡检必然 rc=1（全 PENDING）；wrapper 必须 rc=0 且不判 FAIL。"""
    repo = make_repo(tmp_path)
    r = run_cli(repo, "--json")
    assert r.returncode == 0, (r.returncode, r.stderr, r.stdout)
    rep = json.loads(r.stdout)
    obs = [s for s in rep["steps"] if s["key"] == "observation"][0]
    assert obs["rc"] == 1, obs
    assert obs["status"] == "PENDING", obs
    assert rep["status"] != "FAIL"
    assert rep["exit_code"] == 0
    assert "PENDING" in r.stdout


# ------------------------------------------------------------ 3. --json 契约
def test_json_dry_run_has_three_steps(tmp_path):
    repo = make_repo(tmp_path)
    r = run_cli(repo, "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)

    assert rep["tool"] == "pre_observation"
    assert rep["dry_run"] is True
    assert rep["exit_code"] == 0
    assert [s["key"] for s in rep["steps"]] == [
        "collect", "observation", "verify"]
    for s in rep["steps"]:
        assert s["status"] == "PLANNED"
        assert s["cmd"]
        assert s["rc"] is None
    assert [s["enabled"] for s in rep["steps"]] == [True, True, False]
    assert rep["steps"][2]["conclusion"].startswith("未启用")


# -------------------------------------------------------- 4. 参数/环境错误 = 2
def test_invalid_flag_returns_2(tmp_path):
    repo = make_repo(tmp_path)
    r = run_cli(repo, "--no-such-flag")
    assert r.returncode == 2


def test_missing_repo_root_returns_2(tmp_path):
    r = run_cli(tmp_path / "does_not_exist", "--dry-run")
    assert r.returncode == 2
    assert "参数/环境错误" in r.stderr


def test_repo_root_without_data_returns_2(tmp_path):
    """指错层（给了 agent_platform 而非仓库根）→ 数据根缺失，rc 2 而非误跑。"""
    repo = tmp_path / "wrong_layer"
    (repo / "agent_platform").mkdir(parents=True)
    r = run_cli(repo, "--dry-run")
    assert r.returncode == 2
    assert "数据根不存在" in r.stderr


# ------------------------------------------- 5. 实跑写面只在 tmp 数据根内
def test_real_run_writes_evidence_only_under_tmp(tmp_path):
    repo = make_repo(tmp_path)
    real_db = AP / "data" / "evidence" / "evidence.db"
    before = (real_db.stat().st_mtime_ns, real_db.stat().st_size) \
        if real_db.exists() else None

    r = run_cli(repo, "--json")
    assert r.returncode == 0, (r.returncode, r.stderr, r.stdout)

    tmp_db = repo / "agent_platform" / "data" / "evidence" / "evidence.db"
    assert tmp_db.exists(), "证据仓应落在注入的 tmp 数据根"
    after = (real_db.stat().st_mtime_ns, real_db.stat().st_size) \
        if real_db.exists() else None
    assert after == before, "真仓证据仓不得被测试触碰"
