"""P1 可观测性只读审计。

审计四条边界：告警封装覆盖、/market 账本接线、07:05 调度配置与真实产物、
三源（qd/qpzm/ai_team）雇佣跳过证据。工具只读取源码和已有产物，不执行
调度、不调用训练器、不写业务账本。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


_SCAN_DIRS = ("news_collector", "quant_backtest", "portal", "gzh",
              "k_stock", "agent_platform")
_CODE_SUFFIXES = frozenset({".py", ".ps1", ".cmd", ".bat", ".xml"})
_DIRECT_ALERT = re.compile(r"(?<!wrap)alert_fail\.py(?![A-Za-z0-9_])")
_WRAPPER_ALERT = re.compile(r"alert_fail_wrap\.py")
_PYTHON_ALERT_CALL = re.compile(
    r"(?:subprocess\.(?:run|Popen|call|check_call|check_output)|"
    r"os\.(?:system|popen)|\b(?:run|Popen|call|check_call|check_output)\s*\()"
)
_SUBPROCESS_METHODS = frozenset({
    "run", "Popen", "call", "check_call", "check_output",
})
_SOURCES = ("qd", "qpzm", "ai_team")


def _source_files(root: Path):
    """列出受控源码目录，跳过第三方和 Git 工作区。"""
    for dirname in _SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if (path.is_file() and path.suffix.lower() in _CODE_SUFFIXES
                    and not any(part in {".git", "venv", "node_modules",
                                        "_study", "__pycache__"}
                                for part in path.parts)):
                yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _logical_shell_lines(text: str) -> list[str]:
    """把 PowerShell 反引号续行拼成可审计的逻辑行。

    任务脚本常把解释器、脚本路径和参数拆成多行。逐物理行扫描会漏掉
    ``alert_fail.py`` 或 ``alert_fail_wrap.py``，因此在过滤注释后先还原
    PowerShell 的反引号续行。其他格式按原行处理。
    """
    logical: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.endswith("`"):
            pending += line[:-1]
            continue
        if pending:
            logical.append(pending + line)
            pending = ""
        else:
            logical.append(line)
    if pending:
        logical.append(pending)
    return logical


def _has_alert_call(path: Path, text: str, target: re.Pattern[str]) -> bool:
    """只识别实际执行入口，忽略注释和文档字符串。"""
    if not target.search(text):
        return False
    if path.suffix.lower() == ".py":
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            tree = None
        if tree is not None:
            module_aliases = {"subprocess"}
            call_aliases: set[str] = set()
            target_vars: set[str] = set()
            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        if alias.name == "subprocess":
                            module_aliases.add(alias.asname or "subprocess")
                elif (isinstance(item, ast.ImportFrom)
                      and item.module == "subprocess"):
                    for alias in item.names:
                        if alias.name in _SUBPROCESS_METHODS:
                            call_aliases.add(alias.asname or alias.name)
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    value = item.value
                    has_target = any(
                        isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                        and target.search(child.value)
                        for child in ast.walk(value))
                    if has_target:
                        targets = (item.targets if isinstance(item, ast.Assign)
                                   else [item.target])
                        target_vars.update(
                            node.id for node in targets
                            if isinstance(node, ast.Name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                segment = ast.get_source_segment(text, node) or ""
                func = node.func
                is_exec = False
                if isinstance(func, ast.Name):
                    is_exec = func.id in call_aliases
                elif isinstance(func, ast.Attribute):
                    owner = func.value.id if isinstance(func.value, ast.Name) else ""
                    is_exec = ((owner in module_aliases
                                and func.attr in _SUBPROCESS_METHODS)
                               or (owner == "os"
                                   and func.attr in {"system", "popen"}))
                if ((target.search(segment) and (
                        is_exec or _PYTHON_ALERT_CALL.search(segment)))
                        or (is_exec and any(
                            isinstance(child, ast.Name)
                            and child.id in target_vars
                            for child in ast.walk(node)))):
                    return True
            return False
        # 语法不完整时保守回退到去注释行扫描，避免漏报。
    shell_text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    shell_text = re.sub(r"<!--.*?-->", "", shell_text, flags=re.DOTALL)
    shell_exec = re.compile(
        r"(?:\bpython(?:\.exe)?\b|\bstart-process\b|"
        r"\bcmd(?:\.exe)?\b|\bcall\s+|&\s*)", re.IGNORECASE)
    for line in _logical_shell_lines(shell_text):
        stripped = line.lstrip()
        if (stripped.startswith("#") or stripped.startswith("::")
                or stripped.lower().startswith("rem ")):
            continue
        code = line.split("#", 1)[0]
        lower = code.lstrip().lower()
        if lower.startswith(("write-output ", "write-host ", "echo ",
                              "add-content ", "out-file ")):
            continue
        if target.search(code) and shell_exec.search(code):
            return True
    return False


def _has_direct_alert_call(path: Path, text: str) -> bool:
    """判断是否仍有绕过包装器的旧告警入口。"""
    if path.suffix.lower() == ".py":
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            tree = None
        if tree is not None:
            function_aliases: set[str] = set()
            module_aliases: set[str] = set()
            wildcard_import = False
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom)
                        and node.module == "alert_fail"):
                    function_aliases.update(
                        alias.asname or alias.name for alias in node.names
                        if alias.name != "*")
                    wildcard_import |= any(alias.name == "*"
                                           for alias in node.names)
                elif isinstance(node, ast.Import):
                    module_aliases.update(
                        alias.asname or alias.name
                        for alias in node.names if alias.name == "alert_fail")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name) and func.id in function_aliases:
                    return True
                if (isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id in module_aliases):
                    return True
                if (wildcard_import and isinstance(func, ast.Name)
                        and func.id == "alert"):
                    return True
    return _has_alert_call(path, text, _DIRECT_ALERT)


def _alert_coverage(root: Path) -> dict[str, Any]:
    direct, wrapper = [], []
    for path in _source_files(root):
        if (path.name in {"alert_fail.py", "alert_fail_wrap.py"}
                or path.name.startswith("test_")):
            continue
        text = _read_text(path)
        if _has_direct_alert_call(path, text):
            direct.append(path.relative_to(root).as_posix())
        if _has_alert_call(path, text, _WRAPPER_ALERT):
            wrapper.append(path.relative_to(root).as_posix())
    wrapper_path = root / "news_collector" / "alert_fail_wrap.py"
    wrapper_text = _read_text(wrapper_path)
    schema = ("task_rc", "log_path", "result_summary", "exception_class")
    return {
        "direct_files": sorted(direct),
        "wrapper_files": sorted(wrapper),
        "direct_file_count": len(direct),
        "wrapper_file_count": len(wrapper),
        "wrapper_schema_complete": bool(wrapper_text)
        and all(field in wrapper_text for field in schema),
        "coverage_gap": bool(direct),
    }


def _market(root: Path) -> dict[str, Any]:
    api = _read_text(root / "portal" / "portal_api.py")
    page = _read_text(root / "portal" / "market.html")
    nginx = _read_text(root / "portal" / "nginx.conf")
    route_wired = ("/api/market_performance" in api
                   and "/api/market_performance" in page
                   and "location = /market" in nginx)
    ledger = root / "portal" / "data" / "performance_ledger.json"
    return {
        "route_wired": route_wired,
        "ledger_path": str(ledger),
        "ledger_present": ledger.is_file(),
        "ledger_read_only": "financial_execution_enabled" in
        _read_text(root / "portal" / "market_performance.py"),
        "status": "ready" if route_wired and ledger.is_file() else "blocked",
    }


def _evidence_files(root: Path) -> list[Path]:
    """只扫运行产物目录，排除 docs 中的验收模板，避免把计划当回执。"""
    candidates = []
    for dirname in ("work", "agent_platform/data", "k_stock/data"):
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.stat().st_size <= 2_000_000:
                candidates.append(path)
    return candidates


def _employment(root: Path) -> dict[str, Any]:
    hires_path = root / "k_stock" / "paper" / "hires.json"
    active_sources: set[str] = set()
    malformed = False
    if hires_path.is_file():
        try:
            data = json.loads(hires_path.read_text(encoding="utf-8"))
            rows = data.get("hires") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                malformed = True
                rows = []
            for row in rows:
                if isinstance(row, dict) and row.get("status") == "active":
                    source = row.get("source")
                    if source in _SOURCES:
                        active_sources.add(source)
        except (OSError, UnicodeError, json.JSONDecodeError):
            malformed = True

    evidence_paths: list[str] = []
    source_evidence = {source: False for source in _SOURCES}
    for path in _evidence_files(root):
        text = _read_text(path)
        if "三源无雇佣跳过" not in text:
            continue
        evidence_paths.append(str(path.relative_to(root)))
        for source in _SOURCES:
            source_evidence[source] |= bool(re.search(
                rf"{re.escape(source)}[^\r\n]{{0,160}}无雇佣跳过", text))
    return {
        "hires_path": str(hires_path),
        "hires_malformed": malformed,
        "active_sources": sorted(active_sources),
        "expected_skip_sources": [s for s in _SOURCES
                                  if s not in active_sources],
        "evidence_paths": sorted(evidence_paths),
        "source_skip_evidence": source_evidence,
        "three_source_skip_verified": (
            not active_sources and all(source_evidence.values())),
    }


def _scheduler(root: Path) -> dict[str, Any]:
    text_parts = []
    for path in (root / "k_stock").rglob("*") if (root / "k_stock").is_dir() else []:
        if path.is_file() and path.suffix.lower() in {".md", ".py", ".ps1", ".json"}:
            text_parts.append(_read_text(path))
    configured_text = "\n".join(text_parts)
    evidence_text = "\n".join(_read_text(p) for p in _evidence_files(root))
    return {
        "configured": "07:05" in configured_text and "58dd6940" in configured_text,
        "actual_execution_evidence": "58dd6940" in evidence_text,
        "execution_evidence_scope": "runtime_artifacts_only",
    }


def audit(root: str | Path) -> dict[str, Any]:
    """返回可序列化审计结果；默认 fail-closed，不把缺证据当成功。"""
    root_path = Path(root).resolve()
    return {
        "schema": "p1_observability_audit/v0.1",
        "root": str(root_path),
        "read_only": True,
        "alert_fail": _alert_coverage(root_path),
        "market": _market(root_path),
        "scheduler": _scheduler(root_path),
        "employment": _employment(root_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 可观测性只读审计")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = audit(args.root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps({
        "alert_direct": report["alert_fail"]["direct_file_count"],
        "alert_wrapper": report["alert_fail"]["wrapper_file_count"],
        "market": report["market"]["status"],
        "scheduler_configured": report["scheduler"]["configured"],
        "scheduler_actual_evidence": report["scheduler"]["actual_execution_evidence"],
        "three_source_skip_verified": report["employment"]["three_source_skip_verified"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
