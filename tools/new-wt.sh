#!/bin/sh
# =============================================================================
# 一键开一条独立开发线（worktree）
# -----------------------------------------------------------------------------
# 为什么需要它：主工作区被多条并行线共用，任何人在那里直接改都会污染共享状态。
# 光靠 pre-commit 拦住人不够——还得让人「一步就能走对路」，否则大家会去找
# --no-verify 绕过。这个脚本就是那条正确的路。

# 用法：
#   tools/new-wt.sh <名字>              # 从 master 开一条新线
#   tools/new-wt.sh <名字> <基点>       # 从指定提交/分支开
#   tools/new-wt.sh --list              # 看现有 worktree
#
# 结果：
#   目录  .worktrees/<名字>
#   分支  task/<名字>（已存在则复用，不新建）
# =============================================================================
set -eu

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

[ $# -ge 1 ] || usage 1

if [ "$1" = "--list" ] || [ "$1" = "-l" ]; then
    git worktree list
    exit 0
fi

NAME="$1"
BASE="${2:-master}"

# 去掉可能带上的 task/ 前缀，避免出现 task/task-xxx
NAME="${NAME#task/}"
NAME="${NAME#/}"

BRANCH="task/${NAME}"
DIR=".worktrees/${NAME}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [ -e "$DIR" ]; then
    echo "目录已存在：$DIR" >&2
    echo "如果是中断的创建，先 git worktree remove --force $DIR" >&2
    exit 1
fi

# 基点必须存在
if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
    echo "基点不存在：$BASE" >&2
    exit 1
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    echo "分支 $BRANCH 已存在，直接挂上去（不新建）"
    git worktree add "$DIR" "$BRANCH"
else
    echo "新建分支 $BRANCH（基点 $BASE）"
    git worktree add -b "$BRANCH" "$DIR" "$BASE"
fi

cat <<EOF

已就绪。接下来：

  cd $DIR
  # 在这里改、在这里提交、在这里跑测试

提醒：
  - 同一个分支不能被两个 worktree 同时占用
  - 干完合回主线后：git worktree remove $DIR（分支按需删）
EOF
