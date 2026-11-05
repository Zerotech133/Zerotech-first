#!/bin/sh
# =============================================================================
# [WB-GUARD] 共享主 worktree 直提守卫 · 2026-11-06
# -----------------------------------------------------------------------------
# 要解决的问题：多条并行线都在同一个共享主工作区（仓库根）上直接改文件、直接提交。
#   症状：主工作区长期 80 已修改 / 335 未跟踪；提交混进同一个索引，无法区分谁改了什么；
#         一条线 rebase 或 reset 会碰到别人的活。
# 做法：主工作区视作「只读共享区」，所有改动必须落在 .worktrees/<name>/ 里。
#
# 判据（不依赖分支名、不依赖路径配置）：
#   主 worktree   →  git rev-parse --git-dir  ==  --git-common-dir
#   链接 worktree →  --git-dir = .git/worktrees/<name>，与 common-dir 不同
#
# 两档模式：
#   audit  （默认）  只记录 + 警告，不阻断。用于先量清「到底谁在共享区提交、提交多少」
#   enforce          拒绝提交，exit 1。由 $GIT_COMMON/WB_GUARD_ENFORCE 文件或
#                    WB_GUARD_MODE=enforce 环境变量开启
#
# 豁免（按优先级）：
#   WB_ALLOW_MAIN_WT=1              单次放行（脚本/迁移期用）
#   $GIT_COMMON/WB_ALLOW_MAIN_COMMIT 存在即放行（长期豁免，用完请删）
#
# 留痕：$GIT_COMMON/wb-main-wt-commits.log（audit 与 enforce 都会写）
# 卸载：python tools/git-hooks/install_guard.py --uninstall
# =============================================================================
set -u

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
GITDIR="$(git rev-parse --git-dir 2>/dev/null)" || exit 0
COMMONDIR="$(git rev-parse --git-common-dir 2>/dev/null)" || exit 0

# 相对路径按 ROOT 归一化（主 worktree 里 --git-dir 返回的是相对 ".git"）
norm() {
    case "$1" in
        /* | [A-Za-z]:[\\/]*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$ROOT" "$1" ;;
    esac
}

G="$(norm "$GITDIR")"
C="$(norm "$COMMONDIR")"

# 链接 worktree（或裸仓库场景）→ 不是共享主工作区，放行
if [ "$G" != "$C" ]; then
    exit 0
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
N="$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')"
SAMPLE="$(git diff --cached --name-only 2>/dev/null | head -4 | tr '\n' ' ')"
LOG="$C/wb-main-wt-commits.log"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# ---- 留痕（无论最终放行还是拒绝都先记一笔）----
printf '%s\tbranch=%s\tfiles=%s\t%s\n' "$STAMP" "$BRANCH" "$N" "$SAMPLE" >> "$LOG" 2>/dev/null || true

# ---- 显式豁免 ----
if [ "${WB_ALLOW_MAIN_WT:-}" = "1" ]; then
    echo "[wb-guard] WB_ALLOW_MAIN_WT=1 → 本次放行（已留痕）" >&2
    exit 0
fi
if [ -f "$C/WB_ALLOW_MAIN_COMMIT" ]; then
    echo "[wb-guard] 检测到 WB_ALLOW_MAIN_COMMIT → 放行（已留痕）；用完请删除该文件" >&2
    exit 0
fi

# ---- 模式判定 ----
MODE="${WB_GUARD_MODE:-audit}"
if [ -f "$C/WB_GUARD_ENFORCE" ]; then
    MODE="enforce"
fi

if [ "$MODE" != "enforce" ]; then
    echo "[wb-guard] 注意：正在共享主工作区提交（${N} 个文件，分支 ${BRANCH}）" >&2
    echo "[wb-guard] 当前为 audit 模式，放行并留痕：$LOG" >&2
    echo "[wb-guard] 规范做法：tools/new-wt.sh <名字>（等价于 git worktree add .worktrees/<名字> -b task/<名字>）" >&2
    exit 0
fi

# ---- enforce：拒绝 ----
cat >&2 <<'EOF'

============================================================
 提交被拒绝：这里是共享主工作区，不做开发提交
------------------------------------------------------------
 主工作区被多条并行线共用。在这里提交会把不同线的改动
 塞进同一个索引，出问题时无法分离，也没人敢 rebase。

 请改用独立 worktree（一条线一个目录）：

   git worktree add .worktrees/<名字> -b task/<任务名>
   cd .worktrees/<名字>
   # 在那边改、在那边提交、在那边跑测试

 临时放行（会留痕到 .git/wb-main-wt-commits.log）：

   WB_ALLOW_MAIN_WT=1 git commit ...

 确实需要长期放行：在 .git/ 下建空文件 WB_ALLOW_MAIN_COMMIT
============================================================

EOF
echo "[wb-guard] 已阻断。分支 $BRANCH，暂存 $N 个文件。明细见 $LOG" >&2
exit 1
