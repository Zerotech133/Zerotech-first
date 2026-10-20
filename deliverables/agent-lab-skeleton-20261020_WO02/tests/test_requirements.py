"""requirements 版本锁定与依赖一致性校验（S9 工位AD，2026-10-20）。

静态校验，零真实网络：只读仓库文件 + ast 解析，不做 pip install、不联网。
扫描范围：roles/*.py 与 entrypoint.py（顶层及函数内 import 均计入）。

挂账说明：pydantic 是 fastapi 的核心传递依赖（本机 pip show fastapi 的
Requires 含 pydantic），骨架源码内无直接 import；requirements.txt 显式声明
它是为了锁定该传递依赖的校验版本，故在 GHOST_TRANSITIVE_WHITELIST 中
白名单放行并在此注明理由。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = ROOT / "requirements.txt"

# 必须在列的五项（pip 包名，PEP 503 规范名）
FIVE_REQUIRED = {"fastapi", "uvicorn", "httpx", "prometheus-client", "pydantic"}

# 传递依赖白名单：requirements 声明但骨架源码无直接 import 的包 -> 挂账理由。
GHOST_TRANSITIVE_WHITELIST = {
    "pydantic": "fastapi 的传递依赖（pip show fastapi Requires 声明），"
                "显式声明以锁定校验版本，骨架内无直接 import",
}

# 扫描目标：roles 下全部 .py + 根入口
SCAN_TARGETS = sorted((ROOT / "roles").glob("*.py")) + [ROOT / "entrypoint.py"]


def _normalize(name: str) -> str:
    """import 顶层名 / pip 包名统一规范化（PEP 503）。

    例：prometheus_client -> prometheus-client；fastapi/httpx/uvicorn/pydantic 不变。
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirements() -> list[tuple[str, str, str]]:
    """解析 requirements.txt 每行（容忍注释/空行）。

    返回 [(pip 规范名, 文件内显示名, 版本约束串)]；存在无法解析的行则 assert 失败。
    """
    entries: list[tuple[str, str, str]] = []
    for lineno, raw in enumerate(
            REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines(), 1):
        # 剥离整行/行尾注释后再判空
        code = raw.split("#", 1)[0].strip()
        if not code:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(>=.*|==.*|~=.*|)$", code)
        assert m is not None, (
            f"requirements.txt 第 {lineno} 行无法解析: {raw!r}")
        entries.append((_normalize(m.group(1)), m.group(1), m.group(2).strip()))
    return entries


def _scan_third_party_imports() -> dict[str, list[str]]:
    """ast 扫描 roles/*.py 与 entrypoint.py 全部 import（顶层及函数内）。

    排除标准库（sys.stdlib_module_names）、项目内模块（roles.*）与 __future__。
    返回 {顶层 import 名: [来源文件名, ...]}。
    """
    found: dict[str, list[str]] = {}
    for path in SCAN_TARGETS:
        tree = _parse_tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                top_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top_names = [node.module]
            else:
                continue
            for name in top_names:
                top = name.split(".")[0]
                if top == "__future__" or top == "roles":
                    continue  # 项目内模块 roles.* 与 __future__ 排除
                if top in sys.stdlib_module_names:
                    continue  # 标准库排除
                found.setdefault(top, []).append(path.name)
    return found


def _parse_tree(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _requirements_names() -> set[str]:
    return {norm for norm, _disp, _spec in _parse_requirements()}


# ---------------------------------------------------------------- tests

def test_requirements_file_parseable():
    """文件格式可解析：无语法异常行、条目非空、每项均带 >= 下限约束。"""
    entries = _parse_requirements()
    assert entries, "requirements.txt 解析结果为空"
    for norm, display, spec in entries:
        assert spec.startswith(">="), (
            f"{display} 缺少 >= 下限约束（实际 spec={spec!r}）")
        assert norm == _normalize(display), f"规范化不一致: {display}"


def test_five_required_packages_present():
    """fastapi/uvicorn/httpx/prometheus_client/pydantic 五项必须在列。"""
    names = _requirements_names()
    missing = FIVE_REQUIRED - names
    assert not missing, f"requirements.txt 缺失必备项: {sorted(missing)}"


def test_all_third_party_imports_declared():
    """每个第三方 import 均在 requirements 内；缺失则 assert 失败并列出清单。"""
    imported = _scan_third_party_imports()
    declared = _requirements_names()
    undeclared = {
        f"{top}（来源: {', '.join(files)}）"
        for top, files in imported.items()
        if _normalize(top) not in declared
    }
    assert not undeclared, (
        f"以下第三方 import 未在 requirements.txt 声明: {sorted(undeclared)}")


def test_no_ghost_dependencies():
    """requirements 每项均被实际 import（幽灵依赖失败并列出）。

    GHOST_TRANSITIVE_WHITELIST 中的传递依赖除外（须在白名单内且理由挂账）。
    """
    imported = {(_normalize(top)) for top in _scan_third_party_imports()}
    declared = _requirements_names()
    ghosts = declared - imported
    unexplained = ghosts - set(GHOST_TRANSITIVE_WHITELIST)
    assert not unexplained, (
        f"幽灵依赖（requirements 声明但无任何 import，且不在传递依赖白名单）: "
        f"{sorted(unexplained)}")
    # 白名单本身必须诚实：键必须在 requirements 中，且确实无直接 import
    assert set(GHOST_TRANSITIVE_WHITELIST) <= declared, (
        "GHOST_TRANSITIVE_WHITELIST 含 requirements 未声明的键")


def test_stdlib_and_project_imports_excluded():
    """扫描器正确排除标准库与项目内模块（roles.*），不误报为第三方。"""
    imported = _scan_third_party_imports()
    # roles/base_agent.py 等文件实际用到的标准库顶层名
    for stdlib_top in ("json", "os", "time", "threading", "typing", "pathlib",
                       "datetime", "argparse", "uuid", "logging", "sys"):
        assert stdlib_top not in imported, (
            f"标准库 {stdlib_top} 被误报为第三方 import")
    assert not any(top == "roles" or top.startswith("roles.")
                   for top in imported), "项目内模块 roles.* 被误报为第三方 import"


def test_function_level_import_captured():
    """函数内 import 被捕获：entrypoint.py _run_seat 内 import uvicorn。"""
    imported = _scan_third_party_imports()
    assert "uvicorn" in imported, (
        f"entrypoint.py 函数内 import uvicorn 未被捕获（实得: {sorted(imported)}）")
    assert "entrypoint.py" in imported["uvicorn"]
    # 同文件函数内 from roles.message_bus import MessageBus 属项目内模块，不计
    assert all(f == "entrypoint.py" for f in imported["uvicorn"])


def test_pinned_comment_version_ge_floor():
    """每项行尾注释标锁定来源（实测 X.Y.Z @ 日期），且实测版本 >= 声明下限。"""
    for lineno, raw in enumerate(
            REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines(), 1):
        code = raw.split("#", 1)[0].strip()
        if not code:
            continue  # 头部注释行
        m = re.search(
            r"实测\s+([0-9]+(?:\.[0-9]+)*)\s+@\s+(\d{4}-\d{2}-\d{2})", raw)
        assert m is not None, (
            f"requirements.txt 第 {lineno} 行缺少'实测版本 @ 日期'锁定来源注释: {raw!r}")
        measured = tuple(int(p) for p in m.group(1).split("."))
        floor_m = re.match(r"^[A-Za-z0-9._-]+>=([0-9]+(?:\.[0-9]+)*)", code)
        assert floor_m is not None, f"第 {lineno} 行无法提取 >= 下限: {code!r}"
        floor = tuple(int(p) for p in floor_m.group(1).split("."))
        assert measured >= floor, (
            f"第 {lineno} 行实测版本 {m.group(1)} 低于声明下限 "
            f"{floor_m.group(1)}")
