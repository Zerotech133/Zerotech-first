# -*- coding: utf-8 -*-
"""黄忠变更影响面验证工具（P0-2，2026-10-31，**只读**）。

按 commit 改动文件映射影响面测试，可选执行，输出 JSON/MD 与 PASS/REJECT/OBSERVE
**建议**（工具只出建议，不改验收 verdict、不改 accept/review 语义）。供黄忠 2.0
「验证深度」使用。

流程：
  1) 解析 commit（git rev-parse），取第一父 + 标题（merge commit 用第一父做 diff）；
  2) `git diff --name-only <第一父> <sha>` 取改动文件；失败/根提交 → 数据错误 fail-visible；
  3) 文件分类：tools/ portal/ agent_platform/ quant_backtest/ docs/ other；
  4) 确定性映射（只映射**存在**的文件）：
       · 改动文件本身是测试文件（test_*.py / *_test.py）→ 映射自身；
       · tools/foo_bar.py → tools/test_foo_bar.py；兼容 tools/tests/ 下
         test_foo_bar*.py / test_*foo_bar*.py 布局；
       · agent_platform/x.py → agent_platform/tests/**/test_x*.py 及 test_*x*.py；
       · portal/x.py → portal/tests/**/test_x*.py 及 test_*x*.py（兼容 portal/test_x*.py）；
       · docs/**、quant_backtest/**、other → 无测试映射（计入 unmapped）；
  5) risk_level：docs→low；tools/portal/agent_platform→medium；关键词（data/security/
     auth/secret/token/migration/schema/sql/database 等，按路径 token 匹配）或
     高影响目录（quant_backtest）→high；其他目录保守 medium；取最高；
  6) `--run` 才逐个执行映射测试（subprocess，逐文件 --timeout，默认 300s），
     结果 passed/failed/error + 输出尾部 500 字；默认只映射不跑；
  7) 决策建议（不改 verdict）：run 有 failed/error→REJECT；无映射/未映射占比高
     （>50%%）或仅映射未执行→OBSERVE；run 全过且映射完整→PASS；
  8) md 报告：仅当给了 --work-dir/--out-dir 时落盘（缺省不写任何文件）。

CLI：--commit <sha|HEAD> [--run] [--json] [--timeout 300] [--work-dir DIR]
     [--repo-root DIR]；git 命令统一走模块级 `_run`（测试可注入）。
退出码：0 正常（含建议 REJECT/OBSERVE——建议不改变命令成功性）；2 数据错误
     （commit/diff/仓库不可读等 fail-visible）；argparse 参数错误同为 2。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
DEFAULT_TIMEOUT = 300
UNMAPPED_RATIO_THRESHOLD = 0.5
GIT_TIMEOUT = 60
CLASS_DIRS = ("tools", "portal", "agent_platform", "quant_backtest", "docs")
HIGH_KEYWORDS = {
    "data", "security", "auth", "secret", "token", "tokenize",
    "migration", "migrate", "migrations", "credential", "credentials",
    "password", "passwords", "payment", "payments", "schema", "schemas",
    "sql", "database", "db", "backup", "delete", "drop", "private_key",
    "apikey", "api_key",
}
HIGH_DIRS = ("quant_backtest",)
MEDIUM_DIRS = ("tools", "portal", "agent_platform")


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _run(cmd, cwd=None, timeout=None) -> dict:
    """执行子进程（模块级可注入）：返回 {ok, returncode, stdout, stderr[, error]}。

    任何异常（超时/无法启动）不抛出，转为 {ok: False, returncode: None, error: ...}，
    由上层按 fail-visible 处理——不编造结果。
    """
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": r.returncode == 0, "returncode": r.returncode,
                "stdout": r.stdout or "", "stderr": r.stderr or ""}
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return {"ok": False, "returncode": None, "stdout": out,
                "stderr": "超时（>%ss）" % timeout, "error": "timeout"}
    except Exception as e:  # noqa: BLE001 —— 执行失败 fail-visible
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": "%s: %s" % (type(e).__name__, e),
                "error": "%s: %s" % (type(e).__name__, e)}


def _git(run_fn, repo: Path, *args, timeout: int = GIT_TIMEOUT) -> dict:
    # core.quotepath=false：中文等非 ASCII 路径原样输出（配合 -z 更稳）
    return run_fn(["git", "-C", str(repo), "-c", "core.quotepath=false"]
                  + [str(a) for a in args], cwd=str(repo), timeout=timeout)


# ------------------------------------------------------------------ 文件分类

def _classify(path: str) -> str:
    top = path.replace("\\", "/").split("/", 1)[0]
    return top if top in CLASS_DIRS else "other"


def _is_test_name(name: str) -> bool:
    return (name.startswith("test_") and name.endswith(".py")) \
        or name.endswith("_test.py")


def _tokens(path: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", path.replace("\\", "/").lower()))


def _risk_of(path: str) -> tuple[str, str]:
    """单文件风险：docs→low；高关键词/高影响目录→high；tools/portal/agent_platform→medium。"""
    p = path.replace("\\", "/").lower()
    top = p.split("/", 1)[0]
    if top == "docs":  # 文档不改运行行为 → low（即使文件名含 data 等词）
        return "low", "docs 文档"
    hit = sorted(_tokens(p) & HIGH_KEYWORDS)
    if hit:
        return "high", "关键词 %s" % "/".join(hit)
    if top in HIGH_DIRS:
        return "high", "高影响目录 %s" % top
    if top in MEDIUM_DIRS:
        return "medium", "目录 %s" % top
    return "medium", "其他目录 %s（保守）" % top


_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _risk_level(files: list[str]) -> tuple[str, list[str]]:
    level = "none"
    reasons: list[str] = []
    for f in files:
        lv, why = _risk_of(f)
        reasons.append("%s：%s" % (f, why))
        if _RISK_ORDER[lv] > _RISK_ORDER[level]:
            level = lv
    return level, reasons


# ------------------------------------------------------------------ 测试映射

def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _glob_tests(tdir: Path, stem: str) -> list[Path]:
    """在测试目录里找 test_<stem>*.py / test_*<stem>*.py（前缀+包含双匹配）。"""
    if not tdir.is_dir():
        return []
    out = list(tdir.glob("test_%s*.py" % stem))
    out += list(tdir.glob("test_*%s*.py" % stem))
    if tdir.name == "tests":
        out += list(tdir.rglob("test_%s*.py" % stem))
        out += list(tdir.rglob("test_*%s*.py" % stem))
    return out


def _map_tests(path: str, repo_root: Path) -> list[str]:
    """确定性映射：返回存在的影响面测试相对路径（排序去重；空=unmapped）。"""
    p = path.replace("\\", "/")
    name = p.split("/")[-1]
    if not name.endswith(".py"):
        return []
    if _is_test_name(name):  # 改动测试文件本身 → 自身即影响面
        cand = repo_root / p
        return [p] if cand.is_file() else []
    top = p.split("/", 1)[0]
    stem = name[:-3]
    cands: list[Path] = []
    if top == "tools":
        cands.append(repo_root / "tools" / ("test_%s.py" % stem))
        cands += _glob_tests(repo_root / "tools" / "tests", stem)
    elif top == "agent_platform":
        cands += _glob_tests(repo_root / "agent_platform" / "tests", stem)
    elif top == "portal":
        cands += _glob_tests(repo_root / "portal" / "tests", stem)
        cands += _glob_tests(repo_root / "portal", stem)  # 兼容 portal/test_*.py 旧布局
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c.is_file():
            rel = _rel(repo_root, c)
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return sorted(out)


# ------------------------------------------------------------------ 判定建议

def _suggest(run_flag: bool, run_summary: dict, impacted: list[str],
             unmapped: list[str], changed_count: int) -> tuple[str, list[str]]:
    """决策建议（只给建议，不改 verdict）。"""
    ratio = (round(len(unmapped) / changed_count, 4) if changed_count else None)
    if changed_count == 0:
        return "OBSERVE", ["改动文件为空（可能无差异/根提交），无法验证影响面"]
    if run_flag:
        if run_summary.get("failed") or run_summary.get("error"):
            reasons = ["映射测试存在失败/错误：" \
                       "failed=%s error=%s" % (run_summary.get("failed"),
                                               run_summary.get("error"))]
            if ratio is not None and ratio > UNMAPPED_RATIO_THRESHOLD:
                reasons.append("另有未映射文件 %d/%d（%.0f%%）"
                               % (len(unmapped), changed_count, ratio * 100))
            return "REJECT", reasons
        if not impacted:
            return "OBSERVE", ["无可映射测试（改动文件未命中映射规则），未执行任何测试"]
        if ratio is not None and ratio > UNMAPPED_RATIO_THRESHOLD:
            return "OBSERVE", ["未映射文件 %d/%d（%.0f%%）> %.0f%%（验证覆盖不足）"
                               % (len(unmapped), changed_count, ratio * 100,
                                  UNMAPPED_RATIO_THRESHOLD * 100)]
        if unmapped:
            return "OBSERVE", ["映射测试全部通过（%d 个），但仍有未映射文件 %d/%d（%.0f%%），"
                               "建议人工补验" % (len(impacted), len(unmapped),
                                                changed_count, ratio * 100)]
        return "PASS", ["映射测试全部通过（%d 个测试文件）" % len(impacted)]
    if not impacted:
        return "OBSERVE", ["仅映射未执行：无可映射测试（改动文件未命中映射规则）"]
    if ratio is not None and ratio > UNMAPPED_RATIO_THRESHOLD:
        return "OBSERVE", ["仅映射未执行；未映射文件 %d/%d（%.0f%%）> %.0f%%"
                           % (len(unmapped), changed_count, ratio * 100,
                              UNMAPPED_RATIO_THRESHOLD * 100)]
    if not unmapped:
        return "OBSERVE", ["仅映射未执行（加 --run 才实测）；映射覆盖完整"]
    return "OBSERVE", ["仅映射未执行（加 --run 才实测）；未映射文件 %d/%d（%.0f%%）"
                       % (len(unmapped), changed_count, ratio * 100)]


# ------------------------------------------------------------------ 报告/MD

def _build_md(rep: dict) -> str:
    c = rep.get("commit") or {}
    lines = [
        "# 黄忠变更影响面验证报告（P0-2）", "",
        "- 生成时间：%s" % rep["generated_at"],
        "- Commit：`%s` %s" % (c.get("sha", "?")[:12], c.get("subject") or ""),
        "- merge commit：%s（父提交：%s）"
        % ("是" if c.get("is_merge") else "否",
           ", ".join(p[:12] for p in (c.get("parents") or [])) or "-"),
        "- 改动文件数：%d；分类：%s"
        % (rep["changed_count"],
           json.dumps(rep["class_counts"], ensure_ascii=False)),
        "- 影响面测试（映射后存在）：%d" % len(rep["impacted_tests"]),
        "- 未映射文件：%d（占比 %s）"
        % (len(rep["unmapped_files"]), _pct(rep["unmapped_ratio"])),
        "- 风险等级：**%s**" % rep["risk_level"],
        "- 执行测试：%s" % ("是" if rep["run"] else "否（仅映射）"),
        "- 决策建议：**%s**（工具只出建议，不改 verdict）" % rep["suggestion"],
        "",
        "## 改动文件与映射", "",
        "| 文件 | 分类 | 影响面测试 | 风险 |",
        "|---|---|---|---|",
    ]
    for m in rep["mapping"]:
        lv, _why = _risk_of(m["file"])
        lines.append("| %s | %s | %s | %s |"
                     % (m["file"], m["class"], ", ".join(m["tests"]) or "-", lv))
    lines += ["", "## 未映射文件（无对应测试，逐条列出）", ""]
    lines += (["- `%s`" % f for f in rep["unmapped_files"]] or ["无"])
    lines += ["", "## 风险说明", ""]
    lines += (["- " + r for r in rep["risk_reasons"][:20]] or ["- 无改动文件"])
    lines += ["", "## 测试执行结果", ""]
    if rep["run"]:
        if rep["run_results"]:
            lines += ["| 测试 | 结果 | 返回码 | 输出尾部 |", "|---|---|---|---|"]
            for r in rep["run_results"]:
                tail = (r.get("tail") or "").replace("|", "\\|").replace("\n", " ")[-200:]
                lines.append("| %s | %s | %s | %s |"
                             % (r["test"], r["status"], r["returncode"], tail or "-"))
        else:
            lines.append("无可执行测试")
    else:
        lines.append("未执行（默认仅映射；加 `--run` 实测）")
    lines += ["", "## 决策建议理由", ""]
    lines += ["- " + x for x in rep["suggestion_reasons"]]
    lines += ["", "> 只读工具：不写生产 work/、不改验收单/verdict；建议仅供黄忠人工采纳。", ""]
    return "\n".join(lines)


def _pct(v) -> str:
    return "-" if v is None else "%.1f%%" % (v * 100)


_DEFAULT_OUT_PREFIX = "变更影响面"


# ------------------------------------------------------------------ 主流程

def analyze(commit: str = "HEAD", run_tests: bool = False,
            timeout: int = DEFAULT_TIMEOUT, repo_root=None, out_dir=None,
            runner=None, now: dt.datetime | None = None) -> dict:
    """分析 commit 影响面（默认只映射不跑）；返回结构化报告。"""
    now = now or _now()
    run_fn = runner or _run
    raw_root = str(repo_root if repo_root is not None
                   else (os.environ.get("HUANGZHONG_REPO_ROOT") or ROOT)).strip()
    repo = Path(raw_root)
    rep: dict = {
        "ok": True, "generated_at": now.isoformat(timespec="seconds"),
        "repo_root": str(repo), "requested_commit": commit,
        "run": bool(run_tests), "timeout": int(timeout),
        "commit": None, "changed_files": [], "changed_count": 0,
        "class_counts": {k: 0 for k in CLASS_DIRS + ("other",)},
        "mapping": [], "impacted_tests": [], "unmapped_files": [],
        "unmapped_ratio": None,
        "risk_level": "none", "risk_reasons": [],
        "run_results": [], "run_summary": {"passed": 0, "failed": 0, "error": 0},
        "suggestion": None, "suggestion_reasons": [],
        "md_report": None,
    }
    # 仓库校验（fail-visible）
    if not repo.is_dir():
        rep.update(ok=False, error="repo 根不存在：%s" % repo)
        return rep
    # 1) 解析 commit（HEAD/sha）
    r = _git(run_fn, repo, "rev-parse", "--verify", "%s^{commit}" % commit)
    if not r.get("ok"):
        rep.update(ok=False, error="commit 无法解析：%s"
                   % ((r.get("stderr") or "").strip()[-200:] or "git rev-parse 失败"))
        return rep
    sha = ((r.get("stdout") or "").strip().splitlines() or [""])[0].strip()
    if not sha:
        rep.update(ok=False, error="commit 无法解析：git rev-parse 无输出")
        return rep
    # 2) 父提交 + 标题（merge commit 用第一父）；%P 输出空格分隔父 sha、%s 独立一行
    r2 = _git(run_fn, repo, "show", "-s", "--format=%P%n%s", sha)
    if not r2.get("ok"):
        rep.update(ok=False, error="commit 信息读取失败：%s"
                   % ((r2.get("stderr") or "").strip()[-200:] or "git show 失败"))
        return rep
    lines = (r2.get("stdout") or "").replace("\r\n", "\n").split("\n")
    parents = lines[0].split() if lines else []
    subject = lines[1] if len(lines) > 1 else ""
    rep["commit"] = {"sha": sha, "short": sha[:8], "subject": subject,
                     "parents": parents, "is_merge": len(parents) > 1}
    if not parents:
        rep.update(ok=False, error="根提交无父提交，无法计算 diff（fail-visible，不编造）")
        return rep
    first_parent = parents[0]
    # 3) diff（第一父 → sha；-z 防引号/转义，中文路径原样）
    r3 = _git(run_fn, repo, "diff", "--name-only", "-z", first_parent, sha)
    if not r3.get("ok"):
        rep.update(ok=False, error="git diff 失败：%s"
                   % ((r3.get("stderr") or "").strip()[-200:] or "git diff 非零退出"))
        return rep
    raw = r3.get("stdout") or ""
    if "\x00" in raw:
        files = [p for p in raw.split("\x00") if p]
    else:  # 兼容无 NUL 的注入输出
        files = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    files = [f.replace("\\", "/") for f in files]
    rep["changed_files"] = files
    rep["changed_count"] = len(files)
    for f in files:
        rep["class_counts"][_classify(f)] += 1
    # 4) 映射
    mapping = []
    impacted: list[str] = []
    unmapped: list[str] = []
    for f in files:
        tests = _map_tests(f, repo)
        mapping.append({"file": f, "class": _classify(f), "tests": tests,
                        "mapped": bool(tests)})
        if tests:
            for t in tests:
                if t not in impacted:
                    impacted.append(t)
        else:
            unmapped.append(f)
    rep["mapping"] = mapping
    rep["impacted_tests"] = sorted(impacted)
    rep["unmapped_files"] = unmapped
    rep["unmapped_ratio"] = (round(len(unmapped) / len(files), 4) if files else None)
    # 5) 风险
    risk, risk_reasons = _risk_level(files)
    rep["risk_level"] = risk
    rep["risk_reasons"] = risk_reasons
    # 6) 可选执行（逐文件超时；异常→error，不编造）
    if run_tests and rep["impacted_tests"]:
        exe = sys.executable or "python"
        results = []
        for test in rep["impacted_tests"]:
            rr = run_fn([exe, "-m", "pytest", test, "-q", "-p", "no:cacheprovider"],
                        cwd=str(repo), timeout=timeout)
            rc = rr.get("returncode")
            if rr.get("error") or rc is None:
                status = "error"
            elif rc == 0:
                status = "passed"
            else:
                status = "failed"
            tail = ((rr.get("stdout") or "") + (rr.get("stderr") or ""))[-500:]
            results.append({"test": test, "status": status,
                            "returncode": rc, "tail": tail})
        rep["run_results"] = results
        rep["run_summary"] = {
            "passed": sum(1 for x in results if x["status"] == "passed"),
            "failed": sum(1 for x in results if x["status"] == "failed"),
            "error": sum(1 for x in results if x["status"] == "error"),
        }
    # 7) 建议
    suggestion, reasons = _suggest(run_tests, rep["run_summary"],
                                   rep["impacted_tests"],
                                   rep["unmapped_files"], rep["changed_count"])
    rep["suggestion"] = suggestion
    rep["suggestion_reasons"] = reasons
    # 8) md（仅 --work-dir/--out-dir 时落盘；失败 fail-visible）
    if out_dir:
        try:
            od = Path(out_dir)
            od.mkdir(parents=True, exist_ok=True)
            mp = od / ("%s_%s_%s_%s.md"
                       % (now.strftime("%Y-%m-%d"), _DEFAULT_OUT_PREFIX,
                          sha[:8], now.strftime("%H%M%S")))
            mp.write_text(_build_md(rep), encoding="utf-8")
            rep["md_report"] = str(mp)
        except OSError as e:
            rep.update(ok=False, error="md 报告写入失败：%s: %s"
                       % (type(e).__name__, e))
    return rep


def _summary_line(rep: dict) -> str:
    if not rep.get("ok"):
        return "变更影响面：数据错误 → %s" % rep.get("error", "unknown")
    c = rep.get("commit") or {}
    return ("变更影响面：commit=%s 改动 %d（%s）｜映射测试 %d｜unmapped %d（%s）"
            "｜risk=%s｜run=%s｜建议=%s"
            % (c.get("short", "?"), rep["changed_count"],
               json.dumps(rep["class_counts"], ensure_ascii=False),
               len(rep["impacted_tests"]), len(rep["unmapped_files"]),
               _pct(rep["unmapped_ratio"]), rep["risk_level"],
               "是" if rep["run"] else "否", rep["suggestion"]))


def _positive_int(text: str) -> int:
    try:
        v = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("需要正整数（秒），如 300")
    if v < 1:
        raise argparse.ArgumentTypeError("超时必须 ≥1 秒")
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠变更影响面验证工具（只读；映射/可选执行/建议，P0-2）")
    ap.add_argument("--commit", default="HEAD", help="待分析 commit（sha 或 HEAD）")
    ap.add_argument("--run", action="store_true",
                    help="执行映射测试（默认只映射不跑）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--timeout", type=_positive_int, default=DEFAULT_TIMEOUT,
                    help="单个测试文件执行超时秒数（默认 300）")
    ap.add_argument("--work-dir", "--out-dir", dest="out_dir", default="",
                    help="md 报告输出目录（缺省不落盘；建议 deliverables）")
    ap.add_argument("--repo-root", default="",
                    help="git 仓库根（默认脚本所在仓根；env HUANGZHONG_REPO_ROOT）")
    args = ap.parse_args(argv)
    try:
        rep = analyze(args.commit, run_tests=args.run, timeout=args.timeout,
                      repo_root=(args.repo_root or None),
                      out_dir=(args.out_dir or None))
    except Exception as e:  # noqa: BLE001 —— 内部错误也 fail-visible（rc2）
        rep = {"ok": False, "error": "内部错误：%s: %s" % (type(e).__name__, e),
               "commit": None, "suggestion": None}
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else _summary_line(rep))
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
