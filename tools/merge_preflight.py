#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并前预检（pre-merge-commit 钩子用，2026-10-28 · P13）。

在 git 生成 merge commit 之前运行：
  - 计算 HEAD 与 MERGE_HEAD 的改动文件；
  - 组合门 + 内核守卫（P1.2，ADR-015）：compositions/** 改动校验计划漂移，
    内核契约 artifact 改动必须带 ADR-号（防直连 git merge 绕过 devtask 门）；
  - 纯文档/配置变更 → 直接放行；
  - 否则跑 `tools/test_gate.ps1 -Suite fast`，新增失败 → exit 1（git 中止合并）。

环境变量：
  DEVTOOL_MERGE_GATE=0  → 显式跳过（回滚/紧急，须在回单注明"绕过质量闸"）
  DEVTOOL_MERGE_SUITE   → 覆盖套件（默认 fast）

2026-10-31 H5-b/A9（K-ORG-20261031-MERGEGATE，审计 v2）：
  - 不再认裸 `DEVTOOL_PREFLIGHT_DONE=1`（env 可被分支/包装器伪造跳过质量闸）；
    只认 devtask 质量闸**真跑通过**后写入的一次性 token 文件（TID+HEAD 绑定，
    校验 MERGE_HEAD、TTL，用后即删）；
  - A9 `docs_only` 去掉 `.js`：`portal/static/*.js` 属前端代码，必须跑测试。

2026-11-03 WO-MERGE-HOOK-MERGEHEAD-20261103（pre-merge-commit 误拒修复）：
  - Git 2.55/windows 实测：pre-merge-commit 触发时 **MERGE_HEAD 与 MERGE_MSG 均尚未落盘**
    （merge 已成功、提交尚未生成），原 `git log MERGE_HEAD` 读到空串 → kernel_guard 把
    `compositions/registry.json` 误判为"内核契约改动无 ADR"（分支 tip 实际带 `[ADR-020]`）；
  - `merge_head_commit_msg()` 回退链：MERGE_HEAD → `git rev-parse --git-path MERGE_MSG` 读文件
    → `GITHEAD_<40hex sha>` 环境变量（git merge 注入待合入 tip sha）→ `git log -1 --format=%B <sha>`；
    只更换"待合入分支 tip 提交信息"的取数来源，不改任何门禁语义（读不到仍空串 → kernel_guard 判拒）。

退出码：0=放行，1=拒绝合并，2=预检自身异常（按拒绝处理）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(os.environ.get("TEMP", r"C:\Temp"))
# A9（2026-10-31）：.js 不再按文档免测；html/css 保留为纯静态资产
DOC_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".csv", ".html", ".css")
DOC_PREFIX = ("docs/", "deliverables/", "portal/static/", "agent_platform/skills/")
DEPS_FILES = ("uv.lock", "pyproject.toml")
DEPS_PREFIX = ("requirements/",)
DEP_AGE_HINT_LIMIT = 40
DEP_AGE_HINT_TIMEOUT = 120

# H5-b（2026-10-31）：一次性 preflight token（devtask 写、钩子校验后删）
PREFLIGHT_TOKEN_DIR = TMP / "devtool_preflight"
PREFLIGHT_TOKEN_TTL_S = 900


def _token_dir(token_dir=None) -> Path:
    return Path(token_dir) if token_dir else PREFLIGHT_TOKEN_DIR


def _token_path(head: str, token_dir=None) -> Path:
    return _token_dir(token_dir) / ("%s.json" % str(head or "").strip()[:16])


def _cleanup_tokens(token_dir=None, now=None) -> None:
    d = _token_dir(token_dir)
    if not d.is_dir():
        return
    now = time.time() if now is None else now
    for p in d.glob("*.json"):
        try:
            if now - p.stat().st_mtime > PREFLIGHT_TOKEN_TTL_S * 4:
                p.unlink()
        except OSError:
            continue


def write_preflight_token(tid: str, head: str, token_dir=None) -> str:
    """devtask 质量闸真跑通过后写一次性 token（TID+HEAD 绑定，原子写）。"""
    head = str(head or "").strip()
    if not head:
        raise ValueError("预检 token 需要非空 HEAD")
    d = _token_dir(token_dir)
    d.mkdir(parents=True, exist_ok=True)
    _cleanup_tokens(token_dir)
    payload = {"schema": "devtool_preflight_token/v1", "tid": str(tid or ""),
               "head": head, "ts": time.time(), "nonce": uuid.uuid4().hex}
    path = _token_path(head, token_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def consume_preflight_token(head: str, token_dir=None,
                            ttl: int = PREFLIGHT_TOKEN_TTL_S):
    """校验并删除一次性 token → (ok, detail)。**不读任何裸 env**。"""
    path = _token_path(head, token_dir)
    if not path.is_file():
        return False, "无 token 文件（%s）" % path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        try:
            path.unlink()
        except OSError:
            pass
        return False, "token 解析失败（%s: %s）" % (type(exc).__name__, exc)
    tok_head = str(payload.get("head") or "")
    try:
        age = time.time() - float(payload.get("ts") or 0)
    except (TypeError, ValueError):
        age = 1e9
    head_s = str(head or "").strip()
    if not tok_head or not head_s or tok_head[:16] != head_s[:16]:
        try:
            path.unlink()
        except OSError:
            pass
        return False, "token HEAD 不匹配（token=%s / merge=%s）" % (
            tok_head[:16] or "?", head_s[:16] or "?")
    if age > ttl or age < -60:
        try:
            path.unlink()
        except OSError:
            pass
        return False, "token 已过期/时钟异常（age=%.0fs，ttl=%ss）" % (age, ttl)
    try:
        path.unlink()
    except OSError as exc:
        return False, "token 删除失败（%s）→ 保守重跑测试" % exc
    return True, "预检 token 命中 tid=%s head=%s（一次性，已删除）" % (
        payload.get("tid") or "?", tok_head[:12])


def changed_files() -> list[str]:
    r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                        "diff", "--name-only", "HEAD", "MERGE_HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        # 兜底：暂存区 vs HEAD
        r = subprocess.run(["git", "-C", str(REPO), "-c", "core.quotepath=false",
                            "diff", "--name-only", "--cached", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for x in (r.stdout or "").splitlines():
        x = x.strip().replace("\\", "/")
        if len(x) >= 2 and x[0] == '"' and x[-1] == '"':
            x = x[1:-1]
        if x:
            out.append(x)
    return out


def is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    return all(f.endswith(DOC_SUFFIX) and f.startswith(DOC_PREFIX) for f in files)


def deps_changed(files: list[str]) -> bool:
    for f in files or []:
        name = f.replace("\\", "/")
        if name in DEPS_FILES or any(name.startswith(p) for p in DEPS_PREFIX):
            return True
    return False


# ---- 组合门 + 内核守卫（P1.2，ADR-015 §2.4/§7）----
# 第二道（hooks）：防直连 `git merge` 绕过 devtask 门；同口径只读复检。
# 规格：docs/specs/2026-11-02_composition_gate_devtask_v1.md
# devtask.py::_composition_kernel_gate 复用本文件 composition_kernel_check（单一内核）。
COMPOSITION_TRIGGER_PREFIX = "compositions/"
COMPOSITION_TOOL = "composition.py"
KERNEL_GUARD_TOOL = "kernel_guard.py"


def _parse_tool_json(proc):
    """解析分支工具 --json stdout → (payload, error)。非 JSON 一律 error（fail-visible）。"""
    raw = (proc.stdout or "").strip()
    try:
        return json.loads(raw), ""
    except ValueError as exc:
        return None, "输出非 JSON（rc=%s，%s）：%s" % (
            getattr(proc, "returncode", "?"), type(exc).__name__, raw[:160])


def composition_kernel_check(files, run_tool, commit_msg):
    """组合门 + 内核守卫共用判定内核（P1.2）。返回 (ok, detail)。

    files      : 改动文件列表（仓库相对路径，正/反斜杠均可）；
    run_tool   : run_tool(tool_rel, args) -> (proc, error)，在**待合并内容**上跑
                 tools/<tool_rel>；proc 需有 returncode/stdout；出错返回 (None, err)；
    commit_msg : 待合入分支 tip 提交信息（kernel_guard --commit-msg）。
    口径：compositions/** → composition plan --strict（漂移=拒）；
          变更的 manifest → composition validate（errors=拒，warnings 只打印）；
          内核契约 artifact → kernel_guard --files ... --commit-msg（无 ADR=拒）。
    工具缺失/异常/输出不可解析/超时 → 拒（fail-closed，不静默放行）。
    """
    files = [str(f).replace("\\", "/") for f in (files or []) if str(f).strip()]
    if not files:
        return True, "无改动文件（跳过）"
    detail = []

    if any(f.startswith(COMPOSITION_TRIGGER_PREFIX) for f in files):
        proc, err = run_tool(COMPOSITION_TOOL, ["plan", "--strict", "--json"])
        if err or proc is None:
            return False, err or "composition plan 无法执行"
        payload, jerr = _parse_tool_json(proc)
        if jerr:
            return False, "composition plan --strict " + jerr
        if not isinstance(payload, dict):
            return False, "composition plan 输出非对象（rc=%s）" % proc.returncode
        findings = payload.get("findings") or []
        if proc.returncode != 0 or not payload.get("ok", False) or findings:
            brief = "；".join("%s:%s" % (x.get("kind"), x.get("id"))
                             for x in findings[:5])
            return False, ("composition plan --strict 拒收（rc=%s）漂移：%s"
                           % (proc.returncode, brief or "无 findings 但 rc/ok 异常"))
        detail.append("composition plan ✓")

        manifests = [f for f in files
                     if f.startswith(COMPOSITION_TRIGGER_PREFIX)
                     and f.endswith("/manifest.yaml")]
        for rel in manifests:
            proc, err = run_tool(COMPOSITION_TOOL, ["validate", rel, "--json"])
            if err or proc is None:
                return False, err or "composition validate 无法执行"
            rows, jerr = _parse_tool_json(proc)
            if jerr:
                return False, "composition validate %s %s" % (rel, jerr)
            if not isinstance(rows, list):
                return False, "composition validate %s 输出非列表（rc=%s）" % (
                    rel, proc.returncode)
            bad = [r for r in rows if not r.get("ok")]
            if proc.returncode != 0 or bad:
                first = ((bad[0].get("errors") if bad else "") or "")
                return False, "composition validate 拒收 %s：%s" % (rel, first)
            for r in rows:
                for w in (r.get("warnings") or [])[:3]:
                    print("[merge-gate] 组合告警 %s：%s" % (rel, w))
        detail.append("composition validate ✓（manifest %d）" % len(manifests))

    proc, err = run_tool(KERNEL_GUARD_TOOL,
                         ["--files", ",".join(files),
                          "--commit-msg", commit_msg or ""])
    if err or proc is None:
        return False, err or "kernel_guard 无法执行"
    if proc.returncode != 0:
        return False, ("kernel guard 拒收（待合入分支 tip 提交信息需带 ADR-号）：%s"
                       % ((proc.stdout or "").strip()[-240:]))
    detail.append("kernel_guard ✓")
    return True, "；".join(detail)


def run_repo_tool(tool_rel, args, timeout=600):
    """在仓库根跑 master/合并工作区侧工具 → (proc, error)。工具缺失/异常=error（fail-visible）。"""
    tool = HERE / tool_rel
    if not tool.is_file():
        return None, "仓库缺 tools/%s" % tool_rel
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(tool), *args],
                              cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 —— 超时/环境异常按拒收
        return None, "运行 tools/%s 异常：%r" % (tool_rel, exc)
    return proc, ""


def _git_stdout(args, timeout=30):
    """在 REPO 上跑 git，返回 (rc, stdout.strip)；异常向上抛由调用方兜。"""
    r = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return int(r.returncode), (r.stdout or "").strip()


def _read_git_path_file(name):
    """用 `git rev-parse --git-path <name>` 定位并读取 git 目录内文件 → 文本/空串。"""
    try:
        rc, rel = _git_stdout(["rev-parse", "--git-path", name])
        if rc != 0 or not rel:
            return ""
        p = Path(rel)
        if not p.is_absolute():
            p = Path(REPO) / p
        if not p.is_file():
            return ""
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 —— 读不到=空串，由 kernel_guard 判拒
        return ""


def _merged_head_sha_from_env(environ=None):
    """从 git merge 注入的 `GITHEAD_<40hex>` 环境变量取出待合入 tip sha。

    实测（Git 2.55/Win，2026-11-03）：pre-merge-commit 阶段 MERGE_HEAD/MERGE_MSG 尚未落盘，
    但 git 以 `GITHEAD_<大写40hex>=<合并入 ref>` 形式提供待合入提交；用于兜底取 commit msg。
    """
    env = os.environ if environ is None else environ
    prefix = "GITHEAD_"
    for key in env:
        k = str(key)
        if not k.startswith(prefix):
            continue
        sha = k[len(prefix):]
        if len(sha) == 40 and all(c in "0123456789abcdefABCDEF" for c in sha):
            return sha
    return ""


def merge_head_commit_msg(environ=None):
    """待合入分支 tip 提交信息（pre-merge-commit 阶段 HEAD=目标分支，取 MERGE_HEAD 侧）。

    2026-11-03 修复（WO-MERGE-HOOK-MERGEHEAD-20261103）：Git 2.55/Win 实测
    pre-merge-commit 时 MERGE_HEAD、MERGE_MSG 均尚未落盘，原实现返回空导致
    kernel_guard 误报"内核契约改动无 ADR"。回退顺序（只换取数来源，不改门禁语义）：
      1) `git log -1 --format=%B MERGE_HEAD`（旧行为；合并状态仍存在时优先）；
      2) `git rev-parse --git-path MERGE_MSG` 定位并读文件（存在时用）；
      3) `GITHEAD_<40hex>` 环境变量 → `git log -1 --format=%B <sha>`（实测可用兜底）；
    全部读不到 → 空串（kernel_guard fail-closed 判拒）。
    """
    try:
        _rc, out = _git_stdout(["log", "-1", "--format=%B", "MERGE_HEAD"])
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    msg = _read_git_path_file("MERGE_MSG")
    if msg:
        return msg
    sha = _merged_head_sha_from_env(environ)
    if sha:
        try:
            _rc, out = _git_stdout(["log", "-1", "--format=%B", sha])
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
    return ""


def composition_kernel_preflight(files):
    """钩子侧入口：在已合并工作区上做同口径只读复检 → (ok, detail)。"""
    return composition_kernel_check(files, run_repo_tool, merge_head_commit_msg())


# ---- merge train 门（W1/UF-09，ADGS v1.1 §七；复用 tools/merge_train.py 单一口径）----
# 窗口 16:30–17:00 / 21:40–22:10；禁并期/窗外禁止**直连 git merge**（防绕过 devtask）。
# 钩子侧不提供 force：紧急走 devtask --train-force 留痕，或既有 DEVTOOL_MERGE_GATE=0 显式绕过。
def merge_train_preflight(files, now=None):
    """同口径 merge train 判定（钩子侧，无 force）→ (ok, detail)。

    now 为 ISO 时间串（单测注入；None=当前 Asia/Shanghai 时间）。
    工具缺失/判定异常=拒（fail-visible，不静默放行）。
    """
    tools_dir = str(HERE)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import merge_train as _mt
    except Exception as exc:  # noqa: BLE001 —— 组件缺失=拒（fail-visible）
        return False, "仓库缺 tools/merge_train.py：%r" % exc
    try:
        payload = _mt.evaluate(_mt.parse_now(now), files=list(files or []), force=False)
    except Exception as exc:  # noqa: BLE001 —— 判定异常=拒（fail-visible）
        return False, "merge train 判定异常：%r" % exc
    if payload.get("allowed"):
        return True, payload.get("reason") or "merge train 放行"
    return False, payload.get("reason") or "窗口外/禁并期禁止合并"


def dep_age_hint(files: list[str], runner=None):
    """依赖文件变更时打印年龄 warn；**永不阻断合并**（warn-only）。"""
    if not deps_changed(files):
        return None
    if os.environ.get("DEVTOOL_DEP_AGE_HINT", "1") == "0":
        print("[merge-gate] dep-age hint：DEVTOOL_DEP_AGE_HINT=0 → 跳过（warn-only）")
        return {"skipped": True}
    cmd = [sys.executable, "-X", "utf8", str(HERE / "dep_age_check.py"),
           "--limit", str(DEP_AGE_HINT_LIMIT), "--json"]
    try:
        run = runner or subprocess.run
        proc = run(cmd, cwd=str(REPO), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=DEP_AGE_HINT_TIMEOUT)
        payload = json.loads((proc.stdout or "").strip())
        fresh = payload.get("fresh") or []
        out = {"checked": payload.get("checked", 0),
               "fresh_count": payload.get("fresh_count", len(fresh)),
               "error_count": payload.get("error_count", 0),
               "fresh_top": [(x.get("name"), x.get("version"), x.get("age_days"))
                             for x in fresh[:5]]}
        print("[merge-gate] dep-age hint：checked=%d fresh=%d errors=%d（仅提示，不阻断）" % (
            out["checked"], out["fresh_count"], out["error_count"]))
        for name, ver, age in out["fresh_top"]:
            print("  DEP-FRESH %s==%s age=%sd" % (name, ver, age))
        return out
    except Exception as exc:  # noqa: BLE001
        print("[merge-gate] dep-age hint 跳过（%s: %s；不阻断）" % (
            type(exc).__name__, str(exc)[:120]))
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


def main() -> int:
    # 2026-10-31 P110：主仓 core.bare 自愈守卫（hook 直连 git merge 前）。
    try:
        sys.path.insert(0, str(HERE))
        from git_worktree_guard import ensure_worktree
        if not ensure_worktree(REPO):
            print("preflight: 主仓 core.bare 异常且修复失败 → 拒绝合并")
            return 1
    except Exception as exc:  # noqa: BLE001
        print("preflight: core.bare 守卫不可用（跳过）：%s" % exc)
    # 2026-10-29 合并串行锁：直连 git merge 的 hook 也需持锁（父进程已持锁则复用）
    if not os.environ.get("AIOS_MERGE_LOCK_TOKEN"):
        try:
            import sys as _sys
            _tools = os.path.dirname(os.path.abspath(__file__))
            if _tools not in _sys.path:
                _sys.path.insert(0, _tools)
            from merge_lock import acquire as _ml_acquire, release as _ml_release, ENV_TOKEN as _ML_ENV
            _tok = _ml_acquire(cmd="pre-merge-commit", timeout=0)
            if not _tok:
                print("[merge-gate] merge lock busy，拒绝并发合并")
                return 1
            os.environ[_ML_ENV] = _tok
            import atexit as _atexit
            _atexit.register(_ml_release, _tok)
        except ImportError:
            pass
    if os.environ.get("DEVTOOL_MERGE_GATE") == "0":
        print("[merge-gate] DEVTOOL_MERGE_GATE=0 → 显式跳过（回滚/紧急）")
        return 0
    # 2026-10-31 H5-b：只认 devtask 质量闸真跑通过后写的一次性 token
    # （TID+HEAD 绑定、TTL、用后即删）；裸 env DEVTOOL_PREFLIGHT_DONE 不再有跳过效果。
    try:
        _mh = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "MERGE_HEAD"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace").stdout.strip()
    except Exception as exc:  # noqa: BLE001
        _mh = ""
        print("[merge-gate] MERGE_HEAD 读取异常：%s（将正常跑测试）" % exc)
    if _mh:
        _tok_ok, _tok_detail = consume_preflight_token(_mh)
        if _tok_ok:
            print("[merge-gate] %s → 跳过重复测试" % _tok_detail)
            return 0
        print("[merge-gate] 预检 token 未命中（HEAD=%s，%s）→ 正常跑测试门"
              % (_mh[:12], _tok_detail))
    suite = os.environ.get("DEVTOOL_MERGE_SUITE", "fast")
    try:
        files = changed_files()
    except Exception as exc:  # 预检异常按拒绝处理
        print("[merge-gate] 预检异常：%s" % exc)
        return 2
    docs = is_docs_only(files)
    print("[merge-gate] 合并改动 %d 文件；docs_only=%s；套件=%s" % (len(files), docs, suite))
    # P1.2（ADR-015）：组合门 + 内核守卫复检（在 docs_only 放行之前，防内核契约
    # 文档类 artifact 走纯文档豁免被绕过；检查只读且 <2s，对所有合并都跑）。
    try:
        _ck_ok, _ck_detail = composition_kernel_preflight(files)
    except Exception as exc:  # noqa: BLE001 —— 复检自身异常按拒绝处理（fail-visible）
        print("[merge-gate] 组合/内核复检异常：%r → 拒绝合并" % exc)
        return 2
    if not _ck_ok:
        print("[merge-gate] FAIL：组合/内核门未过 → 拒绝合并：%s" % _ck_detail)
        return 1
    print("[merge-gate] 组合/内核门 PASS：%s" % _ck_detail)
    if docs:
        print("[merge-gate] 纯文档/配置变更 → 放行（不跑测试）")
        return 0
    # W1/UF-09（ADGS §七）：merge train——窗口外/禁并期禁止直连 git merge。
    # docs_only 已在上方放行（= 文档/组合声明旁路）；此处只对代码改动生效。
    try:
        _mt_ok, _mt_detail = merge_train_preflight(files)
    except Exception as exc:  # noqa: BLE001 —— 复检自身异常按拒绝处理（fail-visible）
        print("[merge-gate] merge train 复检异常：%r → 拒绝合并" % exc)
        return 2
    if not _mt_ok:
        print("[merge-gate] FAIL：merge train 未过 → 拒绝合并：%s" % _mt_detail)
        print("[merge-gate] 处置：窗口内重试（主 16:30–17:00 / 次 21:40–22:10）；"
              "紧急走 devtask --train-force 留痕，或 DEVTOOL_MERGE_GATE=0 显式绕过")
        return 1
    print("[merge-gate] merge train PASS：%s" % _mt_detail)
    log = TMP / ("merge_hook_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    print("[merge-gate] 跑测试门：test_gate -Suite %s（日志 %s）" % (suite, log))
    with log.open("w", encoding="utf-8") as f:
        rc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(HERE / "test_gate.ps1"), "-Suite", suite],
            cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT,
        ).returncode
    if rc != 0:
        print("[merge-gate] FAIL：测试门未过（rc=%d），拒绝合并。日志：%s" % (rc, log))
        try:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            for ln in tail:
                print("  |", ln)
        except Exception:
            pass
        return 1
    print("[merge-gate] PASS：测试门通过，放行合并")
    dep_age_hint(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
