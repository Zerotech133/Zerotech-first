# -*- coding: utf-8 -*-
"""模型档位执行点检查测试（WO-LEGION-DEVEXEC-20261027 · 报告 C4 · task-8）。

被测对象：`agent_platform/tools/model_binding_check.py`（只读 CLI）。

隔离纪律：全部用 pytest `tmp_path` 造迷你 org.json + model_tiers.json 夹具，
绝不触碰真仓 `agent_platform/registry/`；夹具目录内断言无写面（工具只读）。
覆盖：①合规 0 违规 ②E3 绑 9b（local 档）违规 ③E3 缺 fallback / 首项=主绑定违规
④未知模型 unknown（不冒充违规）⑤--strict 退出码（main 返回值 + subprocess 端到端）
⑥none 绑定=无 LLM 依赖跳过 ⑦注册表缺失 → 退出码 2
⑧范围过滤（--dept/--grades + scope/full_scope）：全组织存量违规不挡过滤后 strict，
  过滤条件归一化，空 scope 拒绝空门禁（退出码 2）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "model_binding_check.py"
_spec = importlib.util.spec_from_file_location("model_binding_check", str(_TOOL))
MBC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MBC)

# 迷你档位表：与真仓 model_tiers.json 同构（本地模型表内键不带 ollama/ 前缀）
TIERS = {
    "registry": "test_model_tiers",
    "version": "v1",
    "models": {
        "glm/glm-5.3": {"tier": "strong", "enabled": True},
        "glm/glm-5.3-flash": {"tier": "fast", "enabled": True},
        "qwen3.5:9b": {"tier": "local", "enabled": True},
        "openai/gpt-6.0": {"tier": "strong", "enabled": False},
    },
    "grade_policy": {
        "E3": {"label": "30年档 · 裁定", "allow": ["strong"],
               "default": "glm/glm-5.3"},
        "E2": {"label": "20年档 · 独立表态", "allow": ["strong", "fast"],
               "default": "glm/glm-5.3"},
        "E1": {"label": "10年档 · 执行", "allow": ["fast", "local"],
               "default": "glm/glm-5.3-flash"},
    },
    "degraded_fallback": "qwen3.5:9b",
}


def _profile(agent_id: str, grade: str, binding: str, fallback=None) -> dict:
    profile = {"agent_id": agent_id, "grade": grade, "llm_binding": binding}
    if fallback is not None:
        profile["llm_fallback"] = fallback
    return profile


def _dept(dept_id: str, profiles: list) -> dict:
    return {"dept": dept_id, "name": dept_id, "agent_profiles": profiles}


def _write_fixture(root: Path, profiles: list | None = None,
                   tiers: dict | None = None,
                   departments: list | None = None) -> Path:
    """在 root 下造 agent_platform/registry/{org.json,model_tiers.json} 迷你夹具。

    未给 departments 时默认单部门 dev（profiles 即 dev 席位）。
    """
    reg = root / "agent_platform" / "registry"
    reg.mkdir(parents=True)
    if departments is None:
        departments = [_dept("dev", profiles or [])]
    org = {"org": "test_org", "departments": departments}
    (reg / "org.json").write_text(
        json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (reg / "model_tiers.json").write_text(
        json.dumps(tiers or TIERS, ensure_ascii=False), encoding="utf-8")
    return root


def _run_json(root: Path, capsys, *extra: str):
    """以 --json 调 main()，返回 (退出码, JSON 报告)。"""
    rc = MBC.main(["--root", str(root), "--json", *extra])
    captured = capsys.readouterr()
    report = json.loads(captured.out) if captured.out.strip() else None
    return rc, report


def _kinds(report: dict) -> list:
    return [v["kind"] for v in report["violations"]]


# ---------------------------------------------------------------------------
# ① 合规夹具：0 违规，且工具只读（注册表文件内容指纹不变）
# ---------------------------------------------------------------------------

def test_compliant_fixture_zero_violations(tmp_path, capsys):
    profiles = [
        # E3：strong 档 + 降级链首项 != 主绑定
        _profile("ARCH1", "E3", "glm/glm-5.3",
                 ["glm/glm-5.3-flash", "ollama/qwen3.5:9b"]),
        # E2：fast 档在 allow 内
        _profile("DEV6", "E2", "glm/glm-5.3-flash", ["ollama/qwen3.5:9b"]),
        # E1：本地 9b 属 local 档，E1 allow 含 local（ollama/ 前缀兼容表内短键）
        _profile("DOC1", "E1", "ollama/qwen3.5:9b"),
        # none = 无 LLM 依赖的规则执行体，跳过而非 unknown
        _profile("BEACON1", "E2", "none"),
    ]
    root = _write_fixture(tmp_path, profiles)
    org_file = root / "agent_platform" / "registry" / "org.json"
    tiers_file = root / "agent_platform" / "registry" / "model_tiers.json"
    before = (org_file.read_bytes(), tiers_file.read_bytes())

    rc, report = _run_json(root, capsys)

    assert rc == 0
    assert report["ok"] is True
    summary = report["summary"]
    assert summary["checked"] == 3          # 3 个 LLM 席位（none 席不计 checked）
    assert summary["violations"] == 0
    assert summary["unknown"] == 0
    assert summary["unbound"] == 1
    assert report["violations"] == []
    # 只读红线：跑完注册表字节不变
    assert (org_file.read_bytes(), tiers_file.read_bytes()) == before


# ---------------------------------------------------------------------------
# ② E3 绑本地 9b → tier=local 不在 E3.allow=["strong"] → 违规
# ---------------------------------------------------------------------------

def test_e3_bound_local_9b_is_violation(tmp_path, capsys):
    profiles = [_profile("DEV1", "E3", "ollama/qwen3.5:9b",
                         ["glm/glm-5.3"])]
    root = _write_fixture(tmp_path, profiles)

    rc, report = _run_json(root, capsys)

    assert rc == 0                          # 报告口径不拦路
    assert report["ok"] is False
    assert report["summary"]["violations"] == 1
    violation = report["violations"][0]
    assert violation["agent_id"] == "DEV1"
    assert violation["kind"] == "tier_not_allowed"
    assert "local" in violation["detail"] and "strong" in violation["detail"]


# ---------------------------------------------------------------------------
# ③ E3 fallback：缺失 / 首项等于主绑定 → 违规
# ---------------------------------------------------------------------------

def test_e3_fallback_required_and_must_differ(tmp_path, capsys):
    profiles = [
        # 缺 llm_fallback
        _profile("DEV5", "E3", "glm/glm-5.3"),
        # 首项 == 主绑定：等于没有降级链
        _profile("OPS1", "E3", "glm/glm-5.3", ["glm/glm-5.3", "ollama/qwen3.5:9b"]),
        # 对照：首项 != 主绑定的合规 E3
        _profile("ARCH1", "E3", "glm/glm-5.3",
                 ["deepseek/deepseek-flash", "ollama/qwen3.5:9b"]),
    ]
    root = _write_fixture(tmp_path, profiles)

    rc, report = _run_json(root, capsys)

    assert rc == 0
    assert report["summary"]["violations"] == 2
    kinds = _kinds(report)
    assert kinds.count("fallback_missing") == 1
    assert kinds.count("fallback_same_as_binding") == 1
    assert "ARCH1" not in [v["agent_id"] for v in report["violations"]]


# ---------------------------------------------------------------------------
# ④ 未知模型：只报 unknown，不算违规（unknown 不拦截 strict）
# ---------------------------------------------------------------------------

def test_unknown_model_reported_separately(tmp_path, capsys):
    profiles = [
        _profile("DEV2", "E2", "ollama/qwen3.6:35b-a3b"),   # 档位表未收录
        _profile("GUANYU", "E3", "deepseek/deepseek-flash",
                 ["glm/glm-5.3"]),                           # 档位表未收录（E3 有 fallback）
    ]
    root = _write_fixture(tmp_path, profiles)

    rc, report = _run_json(root, capsys, "--strict")

    assert rc == 0                          # unknown 单独计数，不当档位违规拦门禁
    assert report["summary"]["unknown"] == 2
    assert report["summary"]["violations"] == 0
    assert sorted(u["model"] for u in report["unknown"]) == [
        "deepseek/deepseek-flash", "ollama/qwen3.6:35b-a3b"]
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# ⑤ --strict 退出码：有 violation → 1；无 violation → 0；缺注册表 → 2
# ---------------------------------------------------------------------------

def test_strict_exit_code_contract(tmp_path, capsys):
    bad_root = _write_fixture(tmp_path / "bad",
                              [_profile("DEV1", "E3", "ollama/qwen3.5:9b",
                                        ["glm/glm-5.3"])])
    good_root = _write_fixture(tmp_path / "good",
                               [_profile("ARCH1", "E3", "glm/glm-5.3",
                                         ["glm/glm-5.3-flash"])])

    assert MBC.main(["--root", str(bad_root), "--strict"]) == 1
    capsys.readouterr()
    assert MBC.main(["--root", str(bad_root)]) == 0
    capsys.readouterr()
    assert MBC.main(["--root", str(good_root), "--strict"]) == 0
    capsys.readouterr()
    # 注册表缺失：自身错误 → 2（不是违规 1）
    assert MBC.main(["--root", str(tmp_path / "nowhere"), "--json"]) == 2
    capsys.readouterr()


def test_cli_subprocess_strict_end_to_end(tmp_path):
    """真子进程跑 CLI，锁 argparse/__main__/退出码三段接线。"""
    root = _write_fixture(tmp_path,
                          [_profile("DEV1", "E3", "ollama/qwen3.5:9b",
                                    ["glm/glm-5.3"])])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    cmd = [sys.executable, "-X", "utf8", str(_TOOL),
           "--root", str(root), "--strict", "--json"]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=60, env=env)

    assert proc.returncode == 1, proc.stderr
    report = json.loads(proc.stdout)
    assert report["summary"]["violations"] == 1
    assert report["root"] == str(root.resolve())


# ---------------------------------------------------------------------------
# 已知但 enabled=false 的模型：提示（不计违规），不冒充 unknown
# ---------------------------------------------------------------------------

def test_disabled_model_is_warning_not_violation(tmp_path, capsys):
    profiles = [
        _profile("AI1", "E3", "openai/gpt-6.0", ["glm/glm-5.3"]),
    ]
    root = _write_fixture(tmp_path, profiles)

    rc, report = _run_json(root, capsys, "--strict")

    assert rc == 0
    assert report["summary"]["warnings"] == 1
    assert report["summary"]["violations"] == 0
    assert report["summary"]["unknown"] == 0
    assert "enabled=false" in report["warnings"][0]["detail"]


# ---------------------------------------------------------------------------
# ⑧ 范围过滤（Lead 校准）：全组织存量违规不挡 --dept/--grades 过滤后的 strict
# ---------------------------------------------------------------------------

def _mixed_org_fixture(root: Path) -> Path:
    """dev 全合规 + invest 两条存量违规（E2 绑 9b / E3 缺 fallback）。"""
    return _write_fixture(root, departments=[
        _dept("dev", [
            _profile("ARCH1", "E3", "glm/glm-5.3", ["glm/glm-5.3-flash"]),
        ]),
        _dept("invest", [
            _profile("T1", "E2", "ollama/qwen3.5:9b"),   # 存量：tier 违规
            _profile("D1", "E3", "glm/glm-5.3"),          # 存量：缺 fallback
        ]),
    ])


def test_filtered_scope_strict_ignores_legacy_violations(tmp_path, capsys):
    root = _mixed_org_fixture(tmp_path)

    # 全组织报告口径：3 席 / 2 条存量违规，strict 仍会红（非本次验收口径）
    rc, report = _run_json(root, capsys)
    assert rc == 0
    assert report["summary"]["checked"] == 3
    assert report["summary"]["violations"] == 2
    assert report["summary"]["scope"] == {"dept": [], "grades": []}
    assert report["summary"]["full_scope"]["violations"] == 2
    assert MBC.main(["--root", str(root), "--strict"]) == 1
    capsys.readouterr()

    # 验收口径：--dept dev --grades E3 --strict → 只看 dev/E3，存量违规不挡
    rc, report = _run_json(root, capsys, "--dept", "dev", "--grades", "E3",
                           "--strict")
    assert rc == 0
    assert report["ok"] is True
    summary = report["summary"]
    assert summary["scope"] == {"dept": ["dev"], "grades": ["E3"]}
    assert summary["checked"] == 1
    assert summary["violations"] == 0
    assert summary["full_scope"] == {"checked": 3, "violations": 2,
                                     "unknown": 0, "warnings": 0, "unbound": 0}
    assert [row["agent_id"] for row in report["rows"]] == ["ARCH1"]
    assert report["violations"] == []


def test_scope_filters_normalized_and_empty_scope_fails_visible(tmp_path, capsys):
    root = _mixed_org_fixture(tmp_path)

    # 大小写/空白归一 + 多部门/多职级拆分；E3 过滤下 invest D1 缺 fallback 计入
    rc, report = _run_json(root, capsys, "--dept", " DEV , invest ",
                           "--grades", "e3")
    assert rc == 0
    assert report["summary"]["scope"] == {
        "dept": ["dev", "invest"], "grades": ["E3"]}
    assert sorted(row["agent_id"] for row in report["rows"]) == ["ARCH1", "D1"]
    assert report["summary"]["violations"] == 1
    assert report["summary"]["full_scope"]["violations"] == 2

    # 过滤条件匹配不到任何 LLM 席 → 2（拒绝「空门禁假绿」，参数打错必须显形）
    rc = MBC.main(["--root", str(root), "--dept", "nosuchdept",
                   "--grades", "E3", "--strict", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["error"] == "scope_empty"
    assert data["full_scope"]["violations"] == 2

