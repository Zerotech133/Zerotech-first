"""文档-产物一致性校验（工位AG，2026-10-20 S10 波次）。

目的：机器守护"文档不撒谎"——README.md 的产物清单、draft.json 的
systemClaim 计数与 artifacts 声明、entrypoint/compose/prometheus 三处
端口口径，必须与磁盘事实互相对得上。

红线（本工位纪律）：
- **只读校验**：不写、不改任何文档或源码；发现不一致时由本文件抛出
  失败信息并**列出差异清单**，由编排者收口修正。
- 只校验 draft.json 内**已声明**的条目，不要求磁盘文件全部被声明
  （同波次其他工位并行新增的 tests/test_agent_api.py、
  tests/test_pending_delivery.py 可能尚未登记，属正常在途状态）。
- 用例数口径（Z）：systemClaim 的"240 个单元用例"与 README 声明值做
  文档间一致性比对，不取实时 pytest 收集数——在途新增用例会改变实时
  收集数，取实时数会把正常在途状态误报为文档撒谎。

YAML 解析依赖 pyyaml（经 PYTHONPATH 挂载用户 site-packages），与
tests/test_deploy_config.py 同口径。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import entrypoint  # noqa: E402  # 纯常量模块，main 有 __main__ 保护

README_PATH = ROOT / "README.md"
DRAFT_PATH = ROOT / "draft.json"

STATUS_RUNNABLE = "可运行"
STATUS_SKELETON = "骨架"


def _draft() -> dict:
    return json.loads(DRAFT_PATH.read_text(encoding="utf-8"))


def _artifacts() -> list[dict]:
    return _draft()["artifacts"]


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _readme_section(heading_prefix: str) -> str:
    """截取 README 中某个二级标题到下一个二级标题之间的正文。"""
    text = _readme()
    start = text.find(heading_prefix)
    assert start != -1, f"README 缺章节：{heading_prefix}"
    nxt = text.find("\n## ", start + len(heading_prefix))
    return text[start:] if nxt == -1 else text[start:nxt]


def _table_rows(section: str) -> list[list[str]]:
    """提取 markdown 表格行（跳过表头与分隔行），返回按 | 切分的单元格。"""
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows[1:]  # 首行为表头


def _claim_numbers() -> dict[str, int]:
    """解析 systemClaim 的四项计数（缺项直接失败，防口径漂移）。"""
    claim = _draft()["systemClaim"]
    patterns = {
        "total": r"产物\s*(\d+)\s*项",
        "runnable": r"(\d+)\s*项结构实现",
        "skeleton": r"(\d+)\s*项为骨架",
        "cases": r"(\d+)\s*个单元用例",
    }
    found: dict[str, int] = {}
    missing: list[str] = []
    for key, pat in patterns.items():
        m = re.search(pat, claim)
        if m is None:
            missing.append(f"{key}(/{pat}/)")
        else:
            found[key] = int(m.group(1))
    assert not missing, (
        f"systemClaim 缺可解析的计数声明：{missing}；原文：{claim[:200]}"
    )
    return found


def _readme_numbers() -> dict[str, int]:
    """解析 README 第三章标题与"结构实现"行的四项计数。"""
    section = _readme_section("## 三、产物清单")
    patterns = {
        "total": r"产物清单[（(]\s*(\d+)\s*项",
        "runnable": r"(\d+)\s*项结构实现",
        "skeleton": r"(\d+)\s*项骨架",
        "cases": r"(\d+)\s*个单元用例",
    }
    found: dict[str, int] = {}
    missing: list[str] = []
    for key, pat in patterns.items():
        m = re.search(pat, section)
        if m is None:
            missing.append(f"{key}(/{pat}/)")
        else:
            found[key] = int(m.group(1))
    assert not missing, f"README 第三章缺可解析的计数声明：{missing}"
    return found


def _compose_service_ports() -> dict[str, int]:
    """compose 各服务 command 的 --port → {服务名: 端口}。"""
    with open(ROOT / "deploy" / "docker-compose.yaml", encoding="utf-8") as f:
        docs = [d for d in yaml.safe_load_all(f) if d is not None]
    assert docs, "docker-compose.yaml 至少应有一个文档"
    ports: dict[str, int] = {}
    for name, svc in docs[0]["services"].items():
        cmd = svc.get("command") or []
        assert "--port" in cmd, f"compose 服务 {name} command 缺 --port"
        ports[name] = int(cmd[cmd.index("--port") + 1])
    return ports


def _prometheus_target_ports() -> set[int]:
    with open(ROOT / "monitoring" / "prometheus.yml", encoding="utf-8") as f:
        docs = [d for d in yaml.safe_load_all(f) if d is not None]
    assert docs, "prometheus.yml 至少应有一个文档"
    ports: set[int] = set()
    for job in docs[0].get("scrape_configs", []):
        for static in job.get("static_configs", []):
            for target in static.get("targets", []):
                host, _, port = str(target).rpartition(":")
                assert host and port.isdigit(), f"采集目标格式异常：{target}"
                ports.add(int(port))
    return ports


# ---------------------------------------------------------------- 用例 1
def test_artifact_files_all_exist() -> None:
    """draft.json artifacts 每条声明在骨架根下真实存在（缺失清单一次列全）。"""
    missing = [
        a["name"] for a in _artifacts()
        if not (ROOT / a["name"]).is_file()
    ]
    assert not missing, (
        f"draft.json 声明但磁盘缺失 {len(missing)} 个产物：{sorted(missing)}"
    )


# ---------------------------------------------------------------- 用例 2
def test_system_claim_counts_match_artifacts() -> None:
    """systemClaim 的产物/可运行/骨架/用例四项计数与 artifacts 实际计数一致。"""
    claim = _claim_numbers()
    artifacts = _artifacts()
    actual = {
        "total": len(artifacts),
        "runnable": sum(1 for a in artifacts if a.get("status") == STATUS_RUNNABLE),
        "skeleton": sum(1 for a in artifacts if a.get("status") == STATUS_SKELETON),
    }
    diffs = [
        f"{key}: systemClaim={claim[key]} 实际={actual[key]}"
        for key in ("total", "runnable", "skeleton")
        if claim[key] != actual[key]
    ]
    assert not diffs, (
        "systemClaim 计数与 artifacts 实际不一致：\n  " + "\n  ".join(diffs)
    )
    # 用例数 Z 的口径：文档间比对（见模块 docstring），不取实时收集数。
    readme_cases = _readme_numbers()["cases"]
    assert claim["cases"] == readme_cases, (
        f"用例数口径不一致：systemClaim={claim['cases']}，README={readme_cases}"
    )
    assert claim["cases"] > 0, "用例数声明应为正数"


# ---------------------------------------------------------------- 用例 3
def test_readme_artifact_table_paths_exist() -> None:
    """README 第三章产物表首列列出的每个代码/测试/配置路径均存在。"""
    section = _readme_section("## 三、产物清单")
    path_pat = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]*\.(?:py|ya?ml|md|json|txt)")
    declared: list[str] = []
    for row in _table_rows(section):
        if not row:
            continue
        declared.extend(path_pat.findall(row[0]))
    assert declared, "README 产物表未解析出任何文件路径（表结构可能已变更）"
    missing = sorted({p for p in declared if not (ROOT / p).is_file()})
    assert not missing, (
        f"README 产物表声明的路径在磁盘缺失：{missing}"
        f"（共声明 {len(set(declared))} 个路径）"
    )


# ---------------------------------------------------------------- 用例 4
def test_readme_counts_match_system_claim() -> None:
    """README 章节标题的产物项数/可运行数/骨架数与 draft.json systemClaim 一致。"""
    readme = _readme_numbers()
    claim = _claim_numbers()
    diffs = [
        f"{key}: README={readme[key]} systemClaim={claim[key]}"
        for key in ("total", "runnable", "skeleton", "cases")
        if readme[key] != claim[key]
    ]
    assert not diffs, "README 与 draft.json systemClaim 计数不一致：\n  " + \
        "\n  ".join(diffs)
    # 项数自洽：结构实现 + 骨架 = 产物总数（两份文档各自成立才算不撒谎）
    for label, nums in (("README", readme), ("systemClaim", claim)):
        assert nums["runnable"] + nums["skeleton"] == nums["total"], (
            f"{label} 分项之和 {nums['runnable']}+{nums['skeleton']} "
            f"≠ 产物总数 {nums['total']}"
        )


# ---------------------------------------------------------------- 用例 5
def test_roles_declare_four_elements() -> None:
    """draft.json 9 个角色四要素（name/duty/input/output）齐全且非空。"""
    draft = _draft()
    # 口径说明：draft.json 无顶层 roles 键，角色清单落在 brief.agentRoles；
    # 两者都查，兼容未来结构收敛，缺清单直接失败。
    roles = draft.get("roles") or draft.get("brief", {}).get("agentRoles")
    assert isinstance(roles, list) and roles, "draft.json 缺角色清单（roles/brief.agentRoles）"
    diffs: list[str] = []
    if len(roles) != 9:
        diffs.append(f"角色项数 {len(roles)} ≠ 9")
    seen: set[str] = set()
    for idx, role in enumerate(roles, start=1):
        for field in ("name", "duty", "input", "output"):
            value = role.get(field)
            if not isinstance(value, str) or not value.strip():
                diffs.append(f"第 {idx} 项角色 {role.get('name', '<无名>')} 的 {field} 缺失/为空")
        name = role.get("name")
        if isinstance(name, str) and name:
            if name in seen:
                diffs.append(f"角色名重复：{name}")
            seen.add(name)
    assert not diffs, "角色四要素校验失败：\n  " + "\n  ".join(diffs)


# ---------------------------------------------------------------- 用例 6
def test_ports_aligned_across_entrypoint_compose_prometheus() -> None:
    """端口口径三处一致：entrypoint.PORTS ⊆ compose 端口，且 compose 端口集合
    与 prometheus 采集目标端口集合逐项相等（同 test_deploy_config.py 口径）。

    说明：PORTS 只覆盖 8 个席位基座端口，compose/prometheus 另含平台席 5 个
    扩编实例（9480-9520），故对 PORTS 用包含关系、对两份部署清单用全集相等。
    """
    ports = entrypoint.PORTS
    compose = _compose_service_ports()
    compose_ports = set(compose.values())
    prom_ports = _prometheus_target_ports()

    formula = {
        role: 9400 + 10 * i for i, role in enumerate(entrypoint.KNOWN_ROLES)
    }
    formula_diffs = [
        f"{role}: PORTS={ports.get(role)} 约定={expected}"
        for role, expected in formula.items()
        if ports.get(role) != expected
    ]
    assert not formula_diffs, (
        "entrypoint.PORTS 不符合 9400+10×序号 口径：\n  " + "\n  ".join(formula_diffs)
    )

    owner = {port: role for role, port in ports.items()}
    missing_compose = sorted(
        f"{owner[p]}({p})" for p in set(ports.values()) - compose_ports
    )
    missing_prom = sorted(
        f"{owner[p]}({p})" for p in set(ports.values()) - prom_ports
    )
    assert not missing_compose, (
        f"entrypoint.PORTS 端口在 compose 服务端口中缺失：{missing_compose}"
    )
    assert not missing_prom, (
        f"entrypoint.PORTS 端口在 prometheus 采集目标中缺失：{missing_prom}"
    )

    assert compose_ports == prom_ports, (
        f"compose 独有端口：{sorted(compose_ports - prom_ports)}；"
        f"prometheus 独有端口：{sorted(prom_ports - compose_ports)}"
    )


# ---------------------------------------------------------------- 用例 7
def test_skeleton_artifacts_have_nonempty_todo() -> None:
    """每条骨架条目的 todo 字段必须存在且非空（骨架披露在位，防骨架冒充成品）。"""
    diffs: list[str] = []
    for artifact in _artifacts():
        if artifact.get("status") != STATUS_SKELETON:
            continue
        todo = artifact.get("todo")
        if not isinstance(todo, str) or not todo.strip():
            diffs.append(f"{artifact.get('name')}: todo 缺失或为空")
    assert not diffs, "骨架条目缺待补披露：\n  " + "\n  ".join(diffs)


# ---------------------------------------------------------------- 用例 8
def test_artifact_status_enum_and_verified_flag() -> None:
    """status 只允许 可运行/骨架；可运行条目必须显式 verified=true。"""
    allowed = {STATUS_RUNNABLE, STATUS_SKELETON}
    bad_status = [
        f"{a.get('name')}: status={a.get('status')!r}"
        for a in _artifacts() if a.get("status") not in allowed
    ]
    unverified = [
        a.get("name") for a in _artifacts()
        if a.get("status") == STATUS_RUNNABLE and a.get("verified") is not True
    ]
    assert not bad_status, f"artifacts status 取值非法（应为 {sorted(allowed)}）：{bad_status}"
    assert not unverified, f"可运行条目未标 verified=true：{unverified}"


# ---------------------------------------------------------------- 用例 9
def test_artifact_names_unique_and_relative() -> None:
    """artifacts 名称唯一且为相对骨架根的路径（绝对路径/../ 逃逸直接失败）。"""
    names = [a.get("name") for a in _artifacts()]
    dups = sorted(n for n, c in Counter(names).items() if c > 1)
    assert not dups, f"artifacts 名称重复：{dups}"
    escaped = [
        n for n in names
        if not n or Path(n).is_absolute() or ".." in Path(n).parts
    ]
    assert not escaped, f"artifacts 名称须为骨架根内相对路径，异常项：{escaped}"


# ---------------------------------------------------------------- 用例 10
def test_readme_role_table_matches_draft_roles() -> None:
    """README 第二章角色表 9 个代号与 draft.json 角色清单逐一对应。"""
    section = _readme_section("## 二、角色定义表")
    rows = _table_rows(section)
    names: list[str] = []
    for row in rows:
        if len(row) < 2:
            continue
        m = re.search(r"[a-z][a-z_]{2,}", row[1])
        if m:
            names.append(m.group(0))
    draft = _draft()
    roles = draft.get("roles") or draft.get("brief", {}).get("agentRoles") or []
    draft_names = [r.get("name") for r in roles]
    assert len(names) == 9, f"README 角色表应 9 行，实际解析出 {len(names)}：{names}"
    assert set(names) == set(draft_names), (
        f"README 独有：{sorted(set(names) - set(draft_names))}；"
        f"draft 独有：{sorted(set(draft_names) - set(names))}"
    )
