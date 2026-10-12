# -*- coding: utf-8 -*-
"""
agent_auto_commit.py — 各 Agent 代码定时强制入库（portal 统揽板块运维）
========================================================================
用途：monorepo（E:/老田的AI助手）下多个 Agent（scanner/integration/dashboard/news 等）
并行开发，本脚本由定时自动化周期调用，把工作区所有改动强制快照入库，
保证任一 Agent 的成果不因后续操作丢失（"强制"= 不挑改动，add -A 全收，
.gitignore 挡运行时产物）。

行为：
  1. git add -A（带在途文件排除表 EXCLUDE_PATHSPECS：MEMORY*/雇佣与纪要账本/
     work/tmp*，见 2026-10-12 拍板材料方案 B；add 撞 index.lock 同款重试）
  2. 膨胀守卫：新增文件数/总字节/单文件超阈值 → 拒绝本次入库（unstage，
     工作区文件不动），状态记 BLOAT_GUARD，防止运行时产物击穿 .gitignore
  3. 无暂存改动 → 记"空跑"状态，退出 0
  4. 有改动 → git commit -m "auto: 各Agent代码定时入库快照 ..."（遇 index.lock 重试 3 次）
  5. 状态落 portal/logs/agent_commit_status.json（供自动化读取汇报），日志追加 portal/logs/auto_commit.log

退出码：0 = 成功/空跑/守卫拦截；2 = 提交失败（重试耗尽）
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"E:/老田的AI助手")
LOG_DIR = REPO / "portal" / "logs"
STATUS_FILE = LOG_DIR / "agent_commit_status.json"
LOG_FILE = LOG_DIR / "auto_commit.log"
LOCK_WAIT_RETRY, LOCK_WAIT_SEC = 3, 10

# ---- 膨胀守卫阈值（2026-10-08）：防大文件/大批量新文件击穿快照库 ----
BLOAT_MAX_NEW_FILES = 300   # 单次提交新增文件数上限
BLOAT_MAX_TOTAL_MB = 50     # 单次提交新增文件总字节上限
BLOAT_MAX_SINGLE_MB = 20    # 单个新增文件体积上限

# ---- 在途文件排除表（2026-10-12 拍板材料方案 B 轻量版）----
# 演练账本/Agent 记忆类文件高频翻转，扫进快照只会污染历史；真成果由对应
# Agent 正式提交。新增同类产物时在此追加一行即可。
EXCLUDE_PATHSPECS = [
    ":(exclude)**/MEMORY*",                 # Agent 记忆/笔记（多项目通用）
    ":(exclude)**/hires.json",              # 操盘手雇佣演练账本
    ":(exclude)**/manager_hires.json",      # 经理雇佣账本（K-STOCK-137，同类）
    ":(exclude)**/minutes.json",            # 纪要账本（K-STOCK-137 S-c，同类）
    ":(exclude)**/minutes_inbox.json",      # 站内纪要收件箱（运行时翻转）
    ":(exclude)**/minutes_trader_feed.json",  # 操盘手纪要订阅流（运行时翻转）
    ":(exclude)work/tmp*",                  # work/ 下临时产物
]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def commit_with_retry(msg: str) -> subprocess.CompletedProcess:
    last = None
    for i in range(LOCK_WAIT_RETRY):
        r = git("commit", "-m", msg, check=False)
        if r.returncode == 0:
            return r
        last = r
        err = (r.stderr or "")
        if "index.lock" in err:
            time.sleep(LOCK_WAIT_SEC)  # 另一 Agent 的 git 进程持锁，等待重试
            continue
        break
    return last


def staged_new_files() -> list:
    out = git("diff", "--cached", "--name-status").stdout
    return [line.split("\t")[-1] for line in out.splitlines() if line.startswith("A\t")]


# ---- 敏感内容守卫（K-STOCK-185）：密钥/凭据不入 git 历史 ----
# 文件名闸不受截断影响；内容闸对暂存 diff 全文扫（超长截断，漏扫残余
# 风险可接受）。命中即 reset+SKIP 留人工甄别，与膨胀守卫同款处置。
SECRET_FILE_RE = re.compile(
    r"(^|/)(\.env(\..*)?|id_rsa[^/]*|\.netrc"
    r"|[^/]*\.(pem|pfx|p12|keystore|token)$"
    r"|[^/]*_(token|secret|key)$"
    r"|[^/]*(secret|credential|api[_-]?key|private[_-]?key|access[_-]?token)"
    r"[^/]*\.(json|ya?ml|txt|ini|cfg|conf|py)$)", re.I)
SECRET_BODY_RE = re.compile(
    r"(BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY"
    r"|sk-[A-Za-z0-9_\-]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[abp]-[A-Za-z0-9\-]{10,}"
    r"|(?:GLM_API_KEY|API_KEY|SECRET_KEY)\s*=\s*[\"'][^\"']{8,}[\"'])")
SECRET_SCAN_CAP = 8_000_000  # 暂存 diff 扫描字节上限


def secret_reason() -> str:
    """暂存改动命中敏感文件名/密钥特征则返回拦截原因，否则返回空串。"""
    files = git("diff", "--cached", "--name-only", check=False).stdout.split()
    for f in files:
        if SECRET_FILE_RE.search(f.replace("\\", "/")):
            return f"敏感文件名: {f}"
    diff = git("diff", "--cached", "-U0", check=False).stdout
    m = SECRET_BODY_RE.search(diff[:SECRET_SCAN_CAP])
    if m:
        return f"疑似密钥内容: {m.group(0)[:16]}***"
    return ""


def bloat_reason() -> str:
    """新增文件超过任一阈值则返回拦截原因，否则返回空串。"""
    new_files = staged_new_files()
    if not new_files:
        return ""
    sizes, total = [], 0
    for f in new_files:
        try:
            sz = (REPO / f).stat().st_size
        except OSError:
            sz = 0
        total += sz
        sizes.append((sz, f))
    sizes.sort(reverse=True)
    top = "; ".join(f"{f}({s / 1048576:.1f}MB)" for s, f in sizes[:5])
    if (len(new_files) > BLOAT_MAX_NEW_FILES
            or total > BLOAT_MAX_TOTAL_MB * 1048576
            or sizes[0][0] > BLOAT_MAX_SINGLE_MB * 1048576):
        return (f"新增文件数={len(new_files)} 总量={total / 1048576:.1f}MB "
                f"超阈值(>{BLOAT_MAX_NEW_FILES}个 或 总量>{BLOAT_MAX_TOTAL_MB}MB "
                f"或 单文件>{BLOAT_MAX_SINGLE_MB}MB) top5: {top}")
    return ""


def main() -> int:
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    status = {"time": ts, "ok": False, "committed": False, "detail": ""}

    try:
        # 忽略 autocrlf 状态差异：快照提交只关心"有无实质改动"
        st = git("status", "--porcelain").stdout
        mod = sum(1 for l in st.splitlines() if l.startswith(("M", " M", "MM")))
        new = sum(1 for l in st.splitlines() if l.startswith("??"))
        dele = sum(1 for l in st.splitlines() if l.startswith(("D", " D")))
        if not st.strip():
            status.update(ok=True, detail="工作区无改动，空跑")
            print(f"[{ts}] 空跑：无改动")
            return finish(status)

        # 方案 C 撞车强化：add 也可能撞 index.lock（人工提交/其他 Agent 持锁），
        # 与 commit 同款重试；重试间隙 HEAD 可能变动 → add 后再核对暂存区
        add_last = None
        for i in range(LOCK_WAIT_RETRY):
            add_last = git("add", "-A", "--", *EXCLUDE_PATHSPECS, check=False)
            if add_last.returncode == 0:
                break
            if "index.lock" in (add_last.stderr or ""):
                time.sleep(LOCK_WAIT_SEC)
                continue
            break
        if add_last.returncode != 0:
            status["detail"] = f"add 失败: {(add_last.stderr or '').strip()[:200]}"
            print(f"[{ts}] FAIL {status['detail']}")
            return finish(status, code=2)

        # 撞车判定：status 到 add 之间人工提交抢先落库 → 排除表过滤后暂存区
        # 可能为空，属正常空跑而非失败
        staged_files = git("diff", "--cached", "--name-only", check=False).stdout.strip()
        if not staged_files:
            status.update(ok=True, detail="撞车或全被排除：暂存区为空，空跑")
            print(f"[{ts}] 空跑：暂存区为空（人工已提交或命中排除表）")
            return finish(status)

        bloat = bloat_reason()
        if bloat:
            git("reset", "-q")  # 退暂存；工作区文件不动，等人工甄别
            status.update(ok=True, committed=False, detail=f"BLOAT_GUARD: {bloat}")
            print(f"[{ts}] SKIP {status['detail']}")
            return finish(status)

        sec = secret_reason()  # K-STOCK-185：密钥/凭据不入 git 历史
        if sec:
            git("reset", "-q")
            status.update(ok=True, committed=False, detail=f"SECRET_GUARD: {sec}")
            print(f"[{ts}] SKIP {status['detail']}")
            return finish(status)

        staged = git("diff", "--cached", "--stat", check=False).stdout.strip().splitlines()
        stat_line = staged[-1].strip() if staged else ""

        r = commit_with_retry(
            f"auto: 各Agent代码定时入库快照 {now.strftime('%m-%d %H:%M')}（改{mod} 新增{new} 删{dele}）"
        )
        if r is None or r.returncode != 0:
            err = ((r.stderr if r else '') or '').strip()
            # 撞车兜底：commit 时人工已把暂存区提交走 → nothing to commit 属空跑
            if "nothing to commit" in err or "没有已暂存的变更" in err:
                status.update(ok=True, detail="撞车：暂存已被人工提交抢先，空跑")
                print(f"[{ts}] 空跑：暂存已被人工提交抢先")
                return finish(status)
            status["detail"] = f"提交失败: {err[:200]}"
            print(f"[{ts}] FAIL {status['detail']}")
            return finish(status, code=2)

        head = git("log", "-1", "--format=%h %s").stdout.strip()
        status.update(ok=True, committed=True,
                      detail=f"{head} | diffstat: {stat_line}")
        print(f"[{ts}] OK {head}\n  改动: 改{mod} 新增{new} 删{dele} | {stat_line}")
        return finish(status)
    except Exception as e:  # noqa: BLE001
        status["detail"] = f"异常: {e}"
        print(f"[{ts}] ERROR {e}")
        return finish(status, code=2)


def finish(status: dict, code: int = 0) -> int:
    try:
        STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{status['time']} | {'OK' if status['ok'] else 'FAIL'} | {status['detail']}\n")
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
