# -*- coding: utf-8 -*-
"""黄忠安全扫描信号（P2-E · WO-HZ-SEC-20261031，只读）。

汇总四类安全信号，纳入黄忠验收证据面（不改安全工具/配置，不写 work/）：

  a) 明文凭据扫描：子进程 `[python, tools/scan_secrets.py]`（默认扫暂存区，
     pre-commit 口径）→ rc0=ok；rc1=命中→fail；rc2=工具异常→fail（fail-visible）；
  b) 端口注册：子进程 `[python, tools/check_ports.py, --strict]` → rc0=ok；
     非 0→fail（只读探测，不启停任何服务）；
  c) 敏感路径扫描（纯 Python）：扫描近期变更文件（`git diff --name-only
     HEAD~1 HEAD` 或 `--changed-files`）是否命中敏感路径关键词
     `work/credentials|models/keys|_vault|\\.env|secret|token` → 命中 fail/warn
     （前三项默认 fail、后三项默认 warn；可用配置覆盖关键词）；
  d) gitleaks 可用性：`gitleaks version` 能否执行 → 不可用仅 warn（不 fail），
     注明 gate 侧使用（merge 门禁），本信号只记录可用性。

输出：JSON（`--json`，stdout）+ 中文 Markdown（默认
`C:\\项目\\deliverables\\2026-10-31_黄忠安全信号_<date>.md`，`--out-dir` 可改，
`--no-write` 关闭落盘）。

退出码：0=ok/warn（无阻断安全信号）；1=fail（有安全信号）；2=工具/配置异常
（无法完成扫描，fail-visible，不静默当成 ok）。

只读边界：除 Markdown 报告外不写任何文件；不修改安全工具/配置；不写 work/。
所有外部命令经模块级 `_run(cmd, cwd, timeout)`，测试可 monkeypatch。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_CONFIG_REL = Path("work") / "huangzhong" / "security_signal.json"
SECRETS_TOOL_REL = Path("tools") / "scan_secrets.py"
PORTS_TOOL_REL = Path("tools") / "check_ports.py"
REPORT_PREFIX = "2026-10-31_黄忠安全信号"
RUN_TIMEOUT = 300.0
STATUS_RANK = {"ok": 0, "warn": 1, "fail": 2}
OK, WARN, FAIL = "ok", "warn", "fail"
ACTIONS = (WARN, FAIL)
MAX_LIST = 50

# 敏感路径默认关键词（与任务口径一致）；前三项默认 fail、后三项默认 warn。
DEFAULT_SENSITIVE_RULES = (
    {"pattern": "work/credentials", "action": FAIL},
    {"pattern": "models/keys", "action": FAIL},
    {"pattern": "_vault", "action": FAIL},
    {"pattern": ".env", "action": WARN},
    {"pattern": "secret", "action": WARN},
    {"pattern": "token", "action": WARN},
)


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    text, err = _read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return rows[:limit]


def _tail(text: str | None, limit: int = 1200) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _log(msg: str) -> None:
    print("[huangzhong_security_signal] " + str(msg), file=sys.stderr)


def _run(cmd: list, cwd: Path | str | None = None,
         timeout: float = RUN_TIMEOUT) -> dict:
    """执行外部命令（模块级；测试可 monkeypatch）。不抛异常，失败如实返回。

    返回 {ok, rc, stdout, stderr, error, cmd, cwd, elapsed_s}；ok 仅表示命令成功
    启动/未超时，业务返回码由调用方解释。
    """
    cmd = [str(c) for c in cmd]
    started = _now()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                "error": "%s: %s" % (type(e).__name__, e), "cmd": cmd,
                "cwd": str(cwd) if cwd else None,
                "elapsed_s": round((_now() - started).total_seconds(), 2)}
    return {"ok": True, "rc": proc.returncode,
            "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "error": None, "cmd": cmd, "cwd": str(cwd) if cwd else None,
            "elapsed_s": round((_now() - started).total_seconds(), 2)}


def _split_files(value) -> list[str]:
    """--changed-files / 列表输入 → 规范化去重文件列表（正斜杠、去空白）。"""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for item in items:
        s = str(item).strip().replace("\\", "/")
        while s.startswith("./"):
            s = s[2:]
        if s and s not in out:
            out.append(s)
    return out


def _subprocess_detail(res: dict) -> dict:
    return {"returncode": res.get("rc"), "ok": res.get("ok"),
            "error": res.get("error"), "elapsed_s": res.get("elapsed_s"),
            "stdout_tail": _tail(res.get("stdout")),
            "stderr_tail": _tail(res.get("stderr"))}


def _status_of(checks: list[dict]) -> str:
    status = OK
    for check in checks:
        if STATUS_RANK.get(check.get("status"), 0) > STATUS_RANK[status]:
            status = check["status"]
    return status


def _counts(checks: list[dict]) -> dict:
    out = {OK: 0, WARN: 0, FAIL: 0}
    for check in checks:
        out[check.get("status", OK)] = out.get(check.get("status", OK), 0) + 1
    return out


# --------------------------------------------------------------- 配置（可选）

def load_config(root: Path, config_path: Path | str | None = None) -> dict:
    """可选配置：work/huangzhong/security_signal.json 覆盖敏感路径关键词。

    支持结构：
      {"sensitive_rules": [{"pattern": "xxx", "action": "warn|fail"}, ...]}
      或 {"sensitive_keywords": ["a", "b"], "sensitive_action": "warn|fail"}
    返回 {ok, source(builtin|file), path, rules, error}；坏配置 → ok=False（rc2）。
    """
    root = Path(root)
    explicit = bool(config_path)
    path = Path(config_path) if explicit else root / DEFAULT_CONFIG_REL
    base = {"ok": True, "source": "builtin", "path": None,
            "rules": [dict(r) for r in DEFAULT_SENSITIVE_RULES], "error": None}
    if not path.exists():
        if explicit:
            return {"ok": False, "source": "file", "path": str(path),
                    "rules": [], "error": "配置文件不存在：%s" % path}
        return base
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        return {"ok": False, "source": "file", "path": str(path), "rules": [],
                "error": "配置文件不可读/顶层不是对象：%s（%s）"
                         % (path, err or "结构错误")}
    rules = None
    error = None
    raw_rules = data.get("sensitive_rules")
    if raw_rules is not None:
        if not isinstance(raw_rules, list):
            error = "sensitive_rules 须为数组"
        else:
            rules = []
            for i, raw in enumerate(raw_rules):
                if not isinstance(raw, dict):
                    error = "sensitive_rules[%d] 不是对象" % i
                    break
                pattern = raw.get("pattern")
                action = raw.get("action", WARN)
                if not isinstance(pattern, str) or not pattern.strip():
                    error = "sensitive_rules[%d].pattern 须为非空字符串" % i
                    break
                if action not in ACTIONS:
                    error = "sensitive_rules[%d].action 须为 warn/fail" % i
                    break
                rules.append({"pattern": pattern.strip(),
                              "action": action})
    if rules is None and not error:
        keywords = data.get("sensitive_keywords")
        if keywords is not None:
            action = data.get("sensitive_action", WARN)
            if not isinstance(keywords, list) or not all(
                    isinstance(k, str) and k.strip() for k in keywords):
                error = "sensitive_keywords 须为非空字符串数组"
            elif action not in ACTIONS:
                error = "sensitive_action 须为 warn/fail"
            else:
                rules = [{"pattern": k.strip(), "action": action}
                         for k in keywords]
    if rules is None and not error:
        rules = [dict(r) for r in DEFAULT_SENSITIVE_RULES]
    if error:
        return {"ok": False, "source": "file", "path": str(path), "rules": [],
                "error": "配置结构非法：%s" % error}
    return {"ok": True, "source": "file", "path": str(path),
            "rules": rules, "error": None}


# ------------------------------------------------- a) 明文凭据扫描（子进程）

def check_scan_secrets(root: Path, timeout: float) -> dict:
    root = Path(root)
    tool = root / SECRETS_TOOL_REL
    check = {"id": "scan_secrets", "name": "明文凭据扫描",
             "tool": str(tool), "cmd": [], "status": FAIL,
             "summary": "", "detail": {}, "tool_error": False}
    if not tool.exists():
        check["summary"] = "scan_secrets.py 不存在 → 无法扫描（fail-visible）"
        check["detail"] = {"error": "missing", "path": str(tool)}
        check["tool_error"] = True
        return check
    cmd = [sys.executable, "tools/scan_secrets.py"]
    check["cmd"] = cmd
    res = _run(cmd, cwd=root, timeout=timeout)
    check["detail"] = _subprocess_detail(res)
    if res.get("ok") is False:
        check["summary"] = ("scan_secrets 子进程无法执行（%s）→ fail-visible"
                            % (res.get("error") or "?"))
        check["tool_error"] = True
        return check
    rc = res.get("rc")
    stdout = res.get("stdout") or ""
    match = re.search(r"未豁免=(\d+)", stdout)
    hits = int(match.group(1)) if match else None
    if rc == 0:
        check["status"] = OK
        check["summary"] = ("未豁免命中 %d 行（rc=0 干净）" % hits
                            if hits is not None else "rc=0（未解析到摘要）")
    elif rc == 1:
        check["status"] = FAIL
        check["summary"] = ("存在未豁免凭据命中 %s 行（rc=1）→ fail"
                            % (hits if hits is not None else "?"))
    elif rc == 2:
        check["status"] = FAIL
        check["detail"]["tool_error"] = True
        check["summary"] = "scan_secrets 工具异常 rc=2 → fail（fail-visible）"
    else:
        check["status"] = FAIL
        check["summary"] = "scan_secrets 返回码 %s（非预期 0/1/2）→ fail-visible" % rc
    return check


# ------------------------------------------------- b) 端口注册（子进程只读）

def check_ports(root: Path, timeout: float) -> dict:
    root = Path(root)
    tool = root / PORTS_TOOL_REL
    check = {"id": "check_ports", "name": "端口注册（--strict）",
             "tool": str(tool), "cmd": [], "status": FAIL,
             "summary": "", "detail": {}, "tool_error": False}
    if not tool.exists():
        check["summary"] = "check_ports.py 不存在 → 无法检查（fail-visible）"
        check["detail"] = {"error": "missing", "path": str(tool)}
        check["tool_error"] = True
        return check
    cmd = [sys.executable, "tools/check_ports.py", "--strict"]
    check["cmd"] = cmd
    res = _run(cmd, cwd=root, timeout=timeout)
    check["detail"] = _subprocess_detail(res)
    if res.get("ok") is False:
        check["summary"] = ("check_ports 子进程无法执行（%s）→ fail-visible"
                            % (res.get("error") or "?"))
        check["tool_error"] = True
        return check
    rc = res.get("rc")
    stdout = res.get("stdout") or ""
    registered = re.search(r"注册\s*(\d+)\s*个", stdout)
    unknown = re.search(r"未登记\s*(\d+)", stdout)
    conflicts = re.search(r"冲突\s*(\d+)", stdout)
    if registered or unknown or conflicts:
        brief = "注册 %s 个；未登记 %s；冲突 %s（rc=%s）" % (
            registered.group(1) if registered else "?",
            unknown.group(1) if unknown else "?",
            conflicts.group(1) if conflicts else "?", rc)
    else:
        brief = "rc=%s（未解析到摘要，见 stdout_tail）" % rc
    check["detail"]["read_only_note"] = "只读探测（TCP/注册表），不启停服务"
    if rc == 0:
        check["status"] = OK
        check["summary"] = brief + "；ok"
    elif rc == 2:
        check["status"] = FAIL
        check["summary"] = brief + "；工具异常 rc=2 → fail（fail-visible）"
    else:
        check["status"] = FAIL
        check["summary"] = brief + "；端口注册 strict 失败 → fail"
    return check


# --------------------------------------- c) 敏感路径扫描（纯 Python，只读）

def _git_changed_files(root: Path, timeout: float) -> dict:
    """git diff --name-only HEAD~1 HEAD 取近期变更（经 _run 可注入）。

    加 `-c core.quotepath=false`，避免非 ASCII 路径被 Git 转成 C 风格转义引号串
    （否则中文路径无法正确显示/匹配）。
    """
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--name-only",
           "HEAD~1", "HEAD"]
    res = _run(cmd, cwd=root, timeout=timeout)
    info = {"cmd": cmd, "source": "git-diff HEAD~1 HEAD",
            **_subprocess_detail(res)}
    if res.get("ok") is False:
        info["error"] = "git 子进程无法执行：%s" % (res.get("error") or "?")
        return {"files": [], "info": info}
    if res.get("rc") != 0:
        info["error"] = "git diff rc=%s（HEAD~1 不可用等）" % res.get("rc")
        return {"files": [], "info": info}
    files = _split_files((res.get("stdout") or "").splitlines())
    return {"files": files, "info": info}


def check_sensitive_paths(root: Path, rules: list[dict], changed_files=None,
                          timeout: float = RUN_TIMEOUT) -> dict:
    """敏感路径扫描：变更文件路径 × 关键词 → 命中 fail/warn。"""
    root = Path(root)
    check = {"id": "sensitive_paths", "name": "敏感路径扫描",
             "cmd": [], "status": OK, "summary": "", "detail": {},
             "tool_error": False}
    explicit = _split_files(changed_files)
    if explicit:
        files, source = explicit, "changed-files"
        git_info = None
    else:
        got = _git_changed_files(root, timeout)
        files, source, git_info = got["files"], "git-diff", got["info"]
    patterns = [r["pattern"] for r in rules]
    hits, warn_hits, fail_hits = [], [], []
    for f in files:
        low = f.lower()
        matched = [{"pattern": r["pattern"], "action": r["action"]}
                   for r in rules if r["pattern"].lower() in low]
        if not matched:
            continue
        action = FAIL if any(m["action"] == FAIL for m in matched) else WARN
        row = {"file": f, "action": action,
               "matches": [m["pattern"] for m in matched]}
        hits.append(row)
        (fail_hits if action == FAIL else warn_hits).append(row)
    if fail_hits:
        check["status"] = FAIL
    elif warn_hits:
        check["status"] = WARN
    else:
        check["status"] = OK
    if git_info and git_info.get("error"):
        # 无法取近期变更：不虚判 ok，也不误判 fail；warn + fail-visible 标注
        if check["status"] == OK:
            check["status"] = WARN
        check["summary"] = ("无法获取近期变更（%s）→ 敏感路径未扫描"
                            "（fail-visible）" % git_info["error"])
    elif hits:
        check["summary"] = ("命中 %d 个敏感路径（fail=%d warn=%d）：%s"
                            % (len(hits), len(fail_hits), len(warn_hits),
                               "、".join(h["file"] for h in hits[:5])))
    else:
        check["summary"] = "无敏感路径命中（扫描 %d 个变更文件）" % len(files)
    check["detail"] = {
        "source": source, "scan_count": len(files),
        "keywords": patterns, "rules": [dict(r) for r in rules],
        "hits": _cap(hits), "fail_count": len(fail_hits),
        "warn_count": len(warn_hits), "git": git_info,
        "changed_files": _cap(files),
    }
    return check


# ------------------------------------------------- d) gitleaks 可用性

def check_gitleaks(root: Path, timeout: float) -> dict:
    root = Path(root)
    check = {"id": "gitleaks", "name": "gitleaks 可用性",
             "cmd": ["gitleaks", "version"], "status": WARN,
             "summary": "", "detail": {}, "tool_error": False}
    res = _run(["gitleaks", "version"], cwd=root,
               timeout=min(float(timeout), 60.0))
    check["detail"] = _subprocess_detail(res)
    if res.get("ok") is False:
        check["status"] = WARN
        check["summary"] = ("gitleaks 不可用（%s）→ warn（不 fail；gate 侧使用，"
                            "本信号只记录可用性）"
                            % (res.get("error") or "未安装"))
        return check
    raw = ((res.get("stdout") or "") + " " + (res.get("stderr") or "")).strip()
    version = raw.split()[0] if raw else "?"
    check["status"] = OK
    check["summary"] = ("gitleaks 可用（version %s）；gate 侧使用，本信号只记录可用性"
                        % version)
    check["detail"]["version"] = version
    return check


# ------------------------------------------------------------------ MD 报告

def _render_md(rep: dict) -> str:
    md = ["# 黄忠安全扫描信号报告（P2-E，只读）", "",
          "- 生成时间：%s（Asia/Shanghai）" % rep["generated_at"],
          "- 仓库根：`%s`" % rep["repo_root"],
          "- 超时：%ss" % rep["timeout"],
          "- 配置来源：**%s**%s"
          % (rep["config"]["source"],
             "（`%s`）" % rep["config"]["path"] if rep["config"].get("path")
             else "（内置默认关键词）"),
          "- 只读边界：除本 Markdown 外不写任何文件；不改安全工具/配置；不写 work/",
          "- **结论：status = %s｜exit_code = %d｜ok/warn/fail = %d/%d/%d**"
          % (rep["status"], rep["exit_code"], rep["counts"][OK],
             rep["counts"][WARN], rep["counts"][FAIL]),
          "", "## 一、检查表", "",
          "| # | 检查项 | 命令/来源 | 结果 | 状态 |",
          "|---|---|---|---|---|"]
    for i, check in enumerate(rep["checks"], 1):
        cmd = " ".join(str(c) for c in (check.get("cmd") or [])) or "—"
        md.append("| %d | %s | `%s` | %s | %s |"
                  % (i, check["name"], cmd, check["summary"], check["status"]))
    scr = rep["secret_scan"]
    md += ["", "## 二、明文凭据扫描（scan_secrets）", "",
           "- rc=%s；状态=%s；%s"
           % ((scr.get("detail") or {}).get("returncode"), scr["status"],
              scr["summary"])]
    if (scr.get("detail") or {}).get("stdout_tail"):
        md.append("- 输出摘要：")
        md.append("")
        md.append("```")
        md.append(scr["detail"]["stdout_tail"])
        md.append("```")
    prt = rep["ports"]
    md += ["", "## 三、端口注册（check_ports --strict）", "",
           "- rc=%s；状态=%s；%s"
           % ((prt.get("detail") or {}).get("returncode"), prt["status"],
              prt["summary"]),
           "- 口径：只读探测（TCP/注册表），不启停任何服务"]
    sen = rep["sensitive_paths"]
    md += ["", "## 四、敏感路径扫描", "",
           "- 变更来源：%s；扫描 %d 个文件；状态=%s"
           % (sen["detail"].get("source"), sen["detail"].get("scan_count", 0),
              sen["status"]),
           "- 关键词：%s" % "、".join(sen["detail"].get("keywords") or []),
           "- 结果：%s" % sen["summary"]]
    for hit in (sen["detail"].get("hits") or []):
        md.append("  - `%s` → %s（命中：%s）"
                  % (hit["file"], hit["action"], "、".join(hit["matches"])))
    gl = rep["gitleaks"]
    md += ["", "## 五、gitleaks 可用性", "",
           "- 状态=%s；%s" % (gl["status"], gl["summary"]),
           "- 说明：本信号只记录 gitleaks CLI 可用性；凭据门禁以 merge gate 的"
           "tools/scan_secrets.py 为准"]
    if rep.get("warnings"):
        md += ["", "## 六、Warnings", ""]
        md += ["- " + w for w in rep["warnings"]]
    else:
        md += ["", "## 六、Warnings", "", "- 无"]
    md += ["", "## 七、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_security_signal.py --json",
           "```",
           "",
           "> 本报告为只读安全信号快照，不替代 merge gate 的凭据/端口守卫。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def scan(repo_root: Path | str | None = None,
         out_dir: Path | str | None = None,
         write_report: bool = True, timeout: float = RUN_TIMEOUT,
         changed_files=None, config_path: Path | str | None = None,
         now: dt.datetime | None = None) -> dict:
    """执行四类安全信号扫描（只读），返回 JSON 结构并可选落 MD。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    timeout = float(timeout) if timeout and float(timeout) > 0 else RUN_TIMEOUT
    warnings: list[str] = []
    config = load_config(root, config_path)
    if not config["ok"]:
        rep = {"ok": False, "tool": "huangzhong_security_signal.py",
               "version": "v1 (2026-10-31)", "generated_at": _iso(now),
               "repo_root": str(root), "read_only": True,
               "error": config["error"], "exit_code": 2,
               "config": {"source": config["source"],
                          "path": config["path"],
                          "error": config["error"]},
               "status": FAIL, "checks": [], "counts": {OK: 0, WARN: 0,
                                                        FAIL: 0},
               "warnings": warnings, "report_md": None,
               "report_written": False}
        return rep
    _log("扫描开始：repo=%s 配置=%s" % (root, config["source"]))

    secret_scan = check_scan_secrets(root, timeout)
    _log("scan_secrets：%s" % secret_scan["status"])
    ports = check_ports(root, timeout)
    _log("check_ports：%s" % ports["status"])
    sensitive = check_sensitive_paths(root, config["rules"],
                                      changed_files=changed_files,
                                      timeout=timeout)
    _log("敏感路径：%s（扫描 %d）"
         % (sensitive["status"], sensitive["detail"].get("scan_count", 0)))
    gitleaks = check_gitleaks(root, timeout)
    _log("gitleaks：%s" % gitleaks["status"])
    checks = [secret_scan, ports, sensitive, gitleaks]
    status = _status_of(checks)
    counts = _counts(checks)
    tool_error = any(c.get("tool_error") for c in checks)
    if tool_error:
        exit_code = 2
    elif status == FAIL:
        exit_code = 1
    else:
        exit_code = 0
    if status == FAIL:
        warnings.append("存在安全 fail 信号：%s"
                        % "、".join("%s(%s)" % (c["name"], c["id"])
                                   for c in checks if c["status"] == FAIL))
    if tool_error:
        warnings.append("安全工具/配置异常（fail-visible），扫描结果不完整；"
                        "exit_code=2")
    if gitleaks["status"] == WARN:
        warnings.append("gitleaks 不可用：仅 warn，不 fail（gate 侧使用）")

    report_md = None
    if write_report:
        report_md = str(out / ("%s_%s.md"
                               % (REPORT_PREFIX, now.strftime("%Y%m%d"))))
    rep = {
        "ok": True,
        "tool": "huangzhong_security_signal.py",
        "version": "v1 (2026-10-31)",
        "generated_at": _iso(now),
        "repo_root": str(root),
        "read_only": True,
        "timeout": timeout,
        "status": status,
        "exit_code": exit_code,
        "counts": counts,
        "checks": checks,
        "secret_scan": secret_scan,
        "ports": ports,
        "sensitive_paths": sensitive,
        "gitleaks": gitleaks,
        "tool_error": tool_error,
        "config": {"source": config["source"], "path": config["path"],
                   "rules": config["rules"]},
        "warnings": warnings,
        "report_md": report_md,
        "report_written": False,
    }
    if write_report:
        try:
            out.mkdir(parents=True, exist_ok=True)
            Path(report_md).write_text(_render_md(rep), encoding="utf-8")
            rep["report_written"] = True
            _log("报告已落盘：%s" % report_md)
        except OSError as e:
            msg = "报告落盘失败：%s: %s" % (type(e).__name__, e)
            rep["report_error"] = msg
            warnings.append(msg)
            _log(msg)
    _log("扫描完成：status=%s exit=%d" % (rep["status"], rep["exit_code"]))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠安全扫描信号（P2-E，只读）：凭据/端口/敏感路径/gitleaks")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--out-dir", default="",
                    help="Markdown 报告输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--no-write", action="store_true",
                    help="不落盘 Markdown 报告（纯只读验证用）")
    ap.add_argument("--timeout", type=float, default=RUN_TIMEOUT,
                    help="子进程超时秒数（默认 300）")
    ap.add_argument("--changed-files", default="",
                    help="逗号分隔变更文件列表（默认 git diff --name-only "
                         "HEAD~1 HEAD）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--config", default="",
                    help="可选配置 JSON（默认 work/huangzhong/security_signal.json）")
    args = ap.parse_args(argv)
    rep = scan(repo_root=args.repo_root or None,
               out_dir=args.out_dir or None,
               write_report=not args.no_write,
               timeout=args.timeout,
               changed_files=args.changed_files or None,
               config_path=args.config or None)
    if args.json or not rep.get("ok"):
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("安全扫描信号：status=%s（exit=%s）；ok/warn/fail=%d/%d/%d；报告：%s"
              % (rep.get("status"), rep.get("exit_code"),
                 rep.get("counts", {}).get(OK, 0),
                 rep.get("counts", {}).get(WARN, 0),
                 rep.get("counts", {}).get(FAIL, 0),
                 rep.get("report_md")))
    return int(rep.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main())
