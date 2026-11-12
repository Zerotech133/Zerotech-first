#!/bin/sh
# =============================================================================
# [WB-ID-GUARD] id 稳定性守卫 · 2026-11-12（R-01）
# -----------------------------------------------------------------------------
# 要解决的问题：Python 内置 hash() 按进程加盐（CPython 默认随机 PYTHONHASHSEED），
#   **跨进程不稳定**。用它生成 id / 快照号 / 去重键，会出现
#   「同一输入、两次运行、两个 id」——
#   判例 R22：sse 采集器 1774 行仅 672 唯一，62% 重复膨胀（每日重抓全量重复追加）；
#   判例 R-01：obs_bridge snapshot_id 同病（已改 sha256 内容指纹）。
#
# 做法：对**暂存的 .py** 调 tools/id_stability_lint.py 做 tokenize 精确判定——
#   只认 NAME token 恰为 hash 且后接 '(' 的调用，天然排除 hashlib / 注释 /
#   字符串 / 属性调用 xx.hash()。不靠 grep，不误伤。
#
# 两档（默认 audit，因为存量尚有他线命中未清）：
#   audit   打印告警 + 留痕，不阻断提交；
#   enforce 命中即 exit 1。开启方式（单次）：
#             WB_ID_GUARD_ENFORCE=1 git commit ...
#
# 豁免：该行加 `# noqa: unstable-hash`（须带理由，评审用）
# 卸装：python tools/git-hooks/install_guard.py --uninstall
# =============================================================================
set -u

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

STAGED="$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '\.py$' || true)"
[ -z "$STAGED" ] && exit 0

if ! command -v python >/dev/null 2>&1; then
    echo "[wb-id] WARN: python 不在 PATH，跳过 id 检查（fail-open）"
    exit 0
fi
if [ ! -f tools/id_stability_lint.py ]; then
    echo "[wb-id] WARN: 缺 tools/id_stability_lint.py，跳过 id 检查（fail-open）"
    exit 0
fi

FILES=""
for f in $STAGED; do
    if [ -f "$f" ]; then
        FILES="$FILES $f"
    fi
done
[ -z "$FILES" ] && exit 0

LOG="$(git rev-parse --git-common-dir 2>/dev/null)/wb-id-guard.log"

if [ "${WB_ID_GUARD_ENFORCE:-0}" = "1" ]; then
    echo "[wb-id] id 稳定性检查（enforce 模式）…"
    python -B tools/id_stability_lint.py --paths $FILES --enforce
    RC=$?
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') enforce rc=$RC ==="
        echo "files:$FILES"
    } >> "$LOG" 2>/dev/null || true
    if [ "$RC" -ne 0 ]; then
        echo "[wb-id] 已阻断：暂存 .py 含裸 hash()。改用 hashlib.sha256(...).hexdigest()[:16]，" >&2
        echo "[wb-id] 或该行加 '# noqa: unstable-hash' 并说明理由。" >&2
        exit 1
    fi
    exit 0
fi

# audit：只告警
OUT="$(python -B tools/id_stability_lint.py --paths $FILES 2>&1)"
RC=$?
{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') audit rc=$RC ==="
    printf '%s\n' "$OUT"
} >> "$LOG" 2>/dev/null || true

if printf '%s' "$OUT" | grep -q '^\[HIT\]'; then
    echo "[wb-id] ⚠ 暂存 .py 检出裸 hash()（audit 模式，不阻断）："
    printf '%s\n' "$OUT" | grep -E '^\[HIT\]' | head -n 5
    echo "[wb-id] 明细：$LOG ；要拦截请 WB_ID_GUARD_ENFORCE=1 git commit ..."
fi
exit 0
