#!/bin/sh
# =============================================================================
# [WB-ID-GUARD] id 稳定性守卫 · 2026-11-12（R-01）/ 2026-11-13 分域升级（R-03）
# -----------------------------------------------------------------------------
# 要解决的问题：Python 内置 hash() 按进程加盐（CPython 默认随机 PYTHONHASHSEED），
#   **跨进程不稳定**。用它生成 id / 快照号 / 去重键/ 随机种子，会出现
#   「同一输入、两次运行、两个 id」——
#   判例 R22：sse 采集器 1774 行仅 672 唯一，62% 重复膨胀（每日重抓全量重复追加）；
#   判例 R-01：obs_bridge snapshot_id 同病（已改 sha256 内容指纹）。
#
# 做法：对**暂存的 .py** 调 tools/id_stability_lint.py 做 tokenize 精确判定——
#   只认 NAME token 恰为 hash 且后接 '(' 的调用，天然排除 hashlib / 注释 /
#   字符串 / 属性调用 xx.hash()。不靠 grep，不误伤。
#
# 三档（分域，避免「一刀切 enforce 误伤他线重构」）：
#   1) news_collector/*   硬拦（enforce）。本线基线为 0命中，新增裸 hash 必须阻断。
#   2) 其余目录           audit。存量 12 处已入基线不阻断，新增只告警——
#                         因为其他行的既有命中在重构时可能行变 → 键失配 → 误报。
#   3) WB_ID_GUARD_ENFORCE=1  全量强制 enforce（基线内仍豁免）。
#
# 基线：tools/id_stability_baseline.json（按「文件 + 源码行指纹」匹配，
#   行号漂移不影响；该行被改写则失配重报——修了要复核、改了要重审）。
#   收缩基线：python tools/id_stability_lint.py --all --write-baseline
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

OWN=""
OTHER=""
for f in $STAGED; do
    [ -f "$f" ] || continue
    case "$f" in
        news_collector/*) OWN="$OWN $f" ;;
        *) OTHER="$OTHER $f" ;;
    esac
done

LOG="$(git rev-parse --git-common-dir 2>/dev/null)/wb-id-guard.log"

# ---------- 1) 强制全量 enforce（显式开启） ----------
if [ "${WB_ID_GUARD_ENFORCE:-0}" = "1" ]; then
    echo "[wb-id] id 稳定性检查（全量 enforce）…"
    python -B tools/id_stability_lint.py --paths $STAGED --enforce
    RC=$?
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') enforce-all rc=$RC ==="
        echo "files:$STAGED"
    } >> "$LOG" 2>/dev/null || true
    if [ "$RC" -ne 0 ]; then
        echo "[wb-id] 已阻断：暂存 .py 含新增裸 hash()。改用 hashlib.sha256(...).hexdigest()[:16]，" >&2
        echo "[wb-id] 或该行加 '# noqa: unstable-hash' 并说明理由。" >&2
        exit 1
    fi
    exit 0
fi

# ---------- 2) 本线硬拦（news_collector/ 基线为 0） ----------
if [ -n "$OWN" ]; then
    OUT_OWN="$(python -B tools/id_stability_lint.py --paths $OWN --enforce 2>&1)"
    RC_OWN=$?
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') own-enforce rc=$RC_OWN ==="
        echo "files:$OWN"
        printf '%s\n' "$OUT_OWN"
    } >> "$LOG" 2>/dev/null || true
    if [ "$RC_OWN" -ne 0 ]; then
        echo "[wb-id] 已阻断（采集主线 news_collector/）：新增裸 hash() —— id 会跨进程漂移。" >&2
        printf '%s\n' "$OUT_OWN" | grep -E '^\[HIT\]' | head -n 5 >&2
        echo "[wb-id] 改用 hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]；" >&2
        echo "[wb-id] 确属无害请该行加 '# noqa: unstable-hash' 并写明理由。" >&2
        exit 1
    fi
fi

# ---------- 3) 其余目录：audit 只告警 ----------
if [ -n "$OTHER" ]; then
    OUT="$(python -B tools/id_stability_lint.py --paths $OTHER 2>&1)"
    {
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') other-audit ==="
        echo "files:$OTHER"
        printf '%s\n' "$OUT"
    } >> "$LOG" 2>/dev/null || true
    if printf '%s' "$OUT" | grep -q '^\[HIT\]'; then
        echo "[wb-id] ⚠ 暂存 .py 检出新增裸 hash()（非采集主线，audit 不阻断）："
        printf '%s\n' "$OUT" | grep -E '^\[HIT\]' | head -n 5
        echo "[wb-id] 明细：$LOG ；要全量拦截请 WB_ID_GUARD_ENFORCE=1 git commit ..."
    fi
fi
exit 0
