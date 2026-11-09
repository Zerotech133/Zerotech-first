#!/bin/sh
# =============================================================================
# [WB-EOL-GUARD] 行尾一致性守卫 · 2026-11-08
# -----------------------------------------------------------------------------
# 要解决的问题：.gitattributes 里的 `text eol=crlf` / `text eol=lf` **只在检出时生效**。
#   对已经存在于工作区的文件，属性变更不会让 git 去重写它 —— 于是工作区会长期停在
#   错的行尾上，而且是静默的：
#
#     1) git status 恒脏（改没改都显示 M），git diff 满屏行尾噪音；
#     2) 本机测试红、云端绿。云端 checkout 会应用属性，本机已有文件不会；
#     3) CRLF 的 .sh 拿到 Linux 执行会报 "\r: command not found"。
#
#   判例（2026-11-08）：2642d3583 给 portal/nginx.conf 加 `text eol=crlf` 之后，本机
#   round7 的 test_nginx_current_state_is_valid / test_absolute_redirect_is_server_level
#   恒红，而 CI 全绿；同文件还压着并行会话 49 行未提交改动，`git checkout --` 会丢改动。
#
# 判据：git ls-files --eol 的 w/ 字段（工作区实际行尾）对 attr 里的 eol= 声明。
#   只看本次暂存的文件（git diff --cached），不扫全仓库 —— 全量扫描在 1 万文件规模上
#   每次提交都要几秒，不值得。
#
# 放行：w/none（单行文件无换行）、w/-text（二进制）
# 豁免：WB_ALLOW_EOL_MISMATCH=1 单次放行
# 修复：python tools/git-hooks/fix_eol.py --apply
# =============================================================================
set -u

if [ "${WB_ALLOW_EOL_MISMATCH:-}" = "1" ]; then
    echo "[wb-eol] WB_ALLOW_EOL_MISMATCH=1 → 跳过行尾校验" >&2
    exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" 2>/dev/null || exit 0

STAGED="$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)"
[ -n "$STAGED" ] || exit 0

COUNT=0
LIST=""

while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || continue
    line="$(git ls-files --eol -- "$f" 2>/dev/null)"
    [ -n "$line" ] || continue
    actual="$(printf '%s' "$line" | awk '{print $2}')"
    case "$line" in
        *eol=crlf*)
            case "$line" in
                *"w/crlf"*|*"w/none"*|*"w/-text"*) ;;
                *)
                    LIST="$LIST  $f
      期望 CRLF（attr: text eol=crlf），实际 $actual
"
                    COUNT=$((COUNT + 1))
                    ;;
            esac
            ;;
        *eol=lf*)
            case "$line" in
                *"w/lf"*|*"w/none"*|*"w/-text"*) ;;
                *)
                    LIST="$LIST  $f
      期望 LF（attr: text eol=lf），实际 $actual
"
                    COUNT=$((COUNT + 1))
                    ;;
            esac
            ;;
    esac
done <<EOF
$STAGED
EOF

if [ "$COUNT" -eq 0 ]; then
    exit 0
fi

cat >&2 <<EOF

============================================================
 提交被拒绝：$COUNT 个文件的行尾与 .gitattributes 声明不一致
------------------------------------------------------------
$LIST
 为什么拦：eol 属性只在检出时生效，工作区里已存在的文件不会被
 自动重写。错的行尾会让本机测试红而云端绿，CRLF 的 .sh 拿到
 Linux 上还会报 "\r: command not found"。

 修复（只改行尾，内容零变化）：
   python tools/git-hooks/fix_eol.py --apply

 单次放行（不建议）：
   WB_ALLOW_EOL_MISMATCH=1 git commit ...
============================================================

EOF
echo "[wb-eol] 已阻断。明细见上方。" >&2
exit 1
