# -*- coding: utf-8 -*-
"""订阅账本勾稽自检（飞书中台 P3.7-lite · L-D / WO-FS-104）。

只读工具：只执行 SELECT 与只读 PRAGMA，不写库、不修复、不删行。
修复动作一律另立工单（本工具只负责把不自洽之处暴露出来）。

库路径规则（不猜，取自 portal/subscription_store.py）：
- 该模块 ``_db_path()``（:58）在每次调用时读 env ``SUBSCRIPTION_DB``，
  非空则用之，否则 ``<repo>/portal/work/subscription.sqlite3``。
- 本工具只 import ``_db_path``，**刻意不用 ``_connect(create=True)``**：
  后者会建 5 张表并向 plan 表写种子档位，属于写操作，与只读契约冲突。
- 因此连接一律走 sqlite URI ``mode=ro``（库不存在时打开失败，不会新建文件），
  并额外置 ``PRAGMA query_only=ON`` 作双保险。

校验项与稳定 code（每项独立执行，互不短路）：

===================================  ============================================
code                                 含义
===================================  ============================================
orphan_open_id                       subscription 引用的 open_id 不在 subscriber
unknown_plan                         subscription.plan_id 不在 plan
bad_range                            start > end
stale_active                         status=active 但 end < now（待清扫）
status_contradiction                 status=expired 但 end >= now（状态与时间矛盾；有 reason="revoked" 撤销留痕的 open_id 豁免，见口径说明 4）
orphan_entitlement                   allowed=1 的 kind 不被任何 plan.kinds 覆盖
paid_without_subscription            payment_order 已支付但该 open_id 无任何订阅行
check_failed                         任一校验项自身执行异常（含库缺失、表缺失）
===================================  ============================================

判定口径说明（写进 docstring 是为了让后来人可复核，不是装饰）：

1. orphan_entitlement 的判定方法
   plan.kinds 存 JSON 数组（见 subscription_store.SEED_PLANS），例：``["*"]``、
   ``["news.*"]``。kind 的前缀规则与 ``subscription_store._kind_prefix`` 一致：
   取首个 ``.`` 之前的部分（``news.brief`` → ``news``）。一个放行行
   （``allowed=1``）的 kind 视为「能对应到 plan」当且仅当存在某个 plan 的
   kinds 中有一条 pattern 满足下列任一条件：
     a. pattern == ``*``（全放行）；
     b. pattern 与 kind 全等（精确列举）；
     c. pattern 与 kind 前缀全等（无点写法，如 ``news``）；
     d. pattern 以 ``.*`` 结尾且其去尾后与 kind 前缀全等（``news.*``）。
   匹配前双方统一 strip + lower。**注意这是「声明覆盖」校验，不是额度校验**：
   它只回答「这个 kind 有人声明过吗」，不回答「这个 open_id 该不该被放行」。
   若 plan.kinds 本身不是合法 JSON 数组，无法完成判定 → 抛错落 check_failed，
   不把解析失败当成「无覆盖」或「无问题」。

2. paid_without_subscription 的判定方法
   payment_order 表只有 open_id，没有 order_id ↔ subscription 的关联列，
   故只能按 open_id 关联：已支付订单的付款人若在 subscription 中**一行都没有**
   → 判漏单。这个口径无法识别「付了款但发的是别的档位/别的一单」的错配，
   是本表结构的已知盲区，已在报告里标为风险，不在本工具内改表。

3. 「已支付」状态字面量
   代码库里没有 payment_order 的写入方，派工单给出的状态机为
   「创建 → 待支付 → 已支付 → 已开通 / 已关闭 / 已退款」。
   故取 ``paid`` 与 ``已支付`` 两个等价写法（strip + lower 后比较），
   常量集中在 ``PAID_STATUSES``；状态机落地后如改用别的字面量，改这一处。

4. status_contradiction 的撤销豁免（2026-10-21，方案 a 咬合）
   ``revoke_subscription``（subscription_store）把生效行立即置 expired 而
   ``end`` 不动——``status=expired 但 end >= now`` 在形态上与状态矛盾全等。
   豁免判据：entitlement_log 存在该 open_id 的 ``allowed=0`` 且
   ``reason="revoked"``（strip+lower 比较）。无留痕的提前 expired 行**仍然
   报矛盾**——即撤销必须走显式函数，直接改库依旧会被勾稽逮住，豁免不会
   变成改库后门。

4. checked 的定义
   5 张表行数之和（subscriber + plan + subscription + entitlement_log +
   payment_order），即本次自检实际扫过的账本行规模。某张表行数读取失败时
   该表不计入，并落一条 check_failed（不静默少算当干净）。

fail-visible 铁律：任一校验项内部异常必须落 ``code="check_failed"`` 条目并继续
其余检查；库不存在、表不存在同样如实记 check_failed，**绝不允许 except 后 pass
或返回零 issue**。本项目有 fail-open 禁令，静默归零是 P0 级隐患。

输出与退出码：默认人读摘要；``--json`` 输出
``{"ok": bool, "checked": n, "issues": [{"code","detail"}]}``。
ok=true → 0；发现任何 issue（含 check_failed）→ 1；工具自身失败 → 2。
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORTAL_DIR = _REPO_ROOT / "portal"
if str(_PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(_PORTAL_DIR))

import subscription_store as _store  # noqa: E402

__all__ = ["reconcile", "render_human", "main", "TABLES", "PAID_STATUSES"]

# 账本 5 表（顺序即人读输出顺序）
TABLES = ("subscriber", "plan", "subscription", "entitlement_log", "payment_order")

# 已支付状态字面量（见模块 docstring 第 3 条）
PAID_STATUSES = ("paid", "已支付")

STATUS_ACTIVE = _store.STATUS_ACTIVE
STATUS_EXPIRED = _store.STATUS_EXPIRED

SAMPLE_LIMIT = 3


def _now_str(now=None) -> str:
    """归一化时间：None → 本地当前时间，格式与账本一致 'YYYY-MM-DD HH:MM:SS'。"""
    if now is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(now, datetime):
        return now.strftime("%Y-%m-%d %H:%M:%S")
    return str(now)


def _open_readonly(path) -> sqlite3.Connection:
    """以 sqlite URI ``mode=ro`` 打开，库/文件不存在即抛错（不新建文件）。"""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


# ---------------------------------------------------------------------------
# 校验项：每个函数只读，返回 issues 列表；异常由上层统一落 check_failed
# ---------------------------------------------------------------------------

def _check_orphan_open_id(conn, now_str):
    rows = conn.execute(
        "SELECT s.subscriber_open_id, COUNT(*) FROM subscription s"
        " LEFT JOIN subscriber sub ON sub.open_id = s.subscriber_open_id"
        " WHERE sub.open_id IS NULL"
        " GROUP BY s.subscriber_open_id"
        " ORDER BY s.subscriber_open_id"
    ).fetchall()
    return [
        {"code": "orphan_open_id",
         "detail": "subscription 引用的 open_id 不在 subscriber：%s（%d 行订阅）"
                   % (open_id, n)}
        for open_id, n in rows
    ]


def _check_unknown_plan(conn, now_str):
    rows = conn.execute(
        "SELECT s.plan_id, COUNT(*), MIN(s.subscriber_open_id) FROM subscription s"
        " LEFT JOIN plan p ON p.plan_id = s.plan_id"
        " WHERE p.plan_id IS NULL"
        " GROUP BY s.plan_id"
        " ORDER BY s.plan_id"
    ).fetchall()
    return [
        {"code": "unknown_plan",
         "detail": "subscription.plan_id 不在 plan 表：%s（%d 行订阅，样例 open_id=%s）"
                   % (plan_id, n, sample)}
        for plan_id, n, sample in rows
    ]


def _check_bad_range(conn, now_str):
    rows = conn.execute(
        "SELECT subscriber_open_id, plan_id, start, end, status FROM subscription"
        " WHERE start > end"
        " ORDER BY subscriber_open_id, start"
    ).fetchall()
    return [
        {"code": "bad_range",
         "detail": "start > end：open_id=%s plan_id=%s start=%s end=%s status=%s"
                   % row}
        for row in rows
    ]


def _check_stale_active(conn, now_str):
    rows = conn.execute(
        "SELECT subscriber_open_id, plan_id, start, end FROM subscription"
        " WHERE status=? AND end < ?"
        " ORDER BY end",
        (STATUS_ACTIVE, now_str),
    ).fetchall()
    return [
        {"code": "stale_active",
         "detail": "status=active 但 end < now(%s)：open_id=%s plan_id=%s start=%s end=%s"
                   % ((now_str,) + tuple(row))}
        for row in rows
    ]


def _check_status_contradiction(conn, now_str):
    rows = conn.execute(
        "SELECT subscriber_open_id, plan_id, start, end FROM subscription"
        " WHERE status=? AND end >= ?"
        " ORDER BY end",
        (STATUS_EXPIRED, now_str),
    ).fetchall()
    # 口径 4（方案 a 撤销咬合，2026-10-21）：显式撤销（revoke_subscription）
    # 会把生效行立即置 expired 且 end 仍在未来——形态上与 status_contradiction
    # 全等。豁免判据=entitlement_log 存在该 open_id 的 allowed=0 且
    # reason="revoked" 留痕。entitlement_log 无 plan_id 列，豁免只能做到
    # open_id 级；产生"提前 expired"形态的唯一生产路径就是显式撤销
    # （expire_sweep 只清 end<now），误豁免面可忽略，此处显式声明而非含糊。
    revoked = {r[0] for r in conn.execute(
        "SELECT DISTINCT open_id FROM entitlement_log"
        " WHERE allowed=0 AND LOWER(TRIM(reason))='revoked'"
    )}
    return [
        {"code": "status_contradiction",
         "detail": "status=expired 但 end >= now(%s)：open_id=%s plan_id=%s start=%s end=%s"
                   % ((now_str,) + tuple(row))}
        for row in rows if row[0] not in revoked
    ]


def _kind_prefix(kind: str) -> str:
    """与 subscription_store._kind_prefix 同口径：首个 '.' 之前；无点原样。"""
    return kind.split(".", 1)[0] if kind else kind


def _load_plan_kinds(conn):
    """读 plan.kinds 为 [(plan_id, [pattern...])]；非法 JSON 直接抛错。"""
    parsed = []
    for plan_id, raw in conn.execute("SELECT plan_id, kinds FROM plan"):
        try:
            kinds = json.loads(raw if raw is not None else "[]")
        except Exception as exc:
            raise ValueError(
                "plan.kinds 不是合法 JSON（plan_id=%s）：%s" % (plan_id, exc))
        if not isinstance(kinds, list):
            raise ValueError("plan.kinds 不是数组（plan_id=%s）" % plan_id)
        pats = [str(k).strip().lower() for k in kinds if str(k).strip()]
        parsed.append((plan_id, pats))
    return parsed


def _kind_covered(kind: str, parsed_plans) -> bool:
    """见模块 docstring「orphan_entitlement 的判定方法」a~d 四条。"""
    k = (kind or "").strip().lower()
    prefix = _kind_prefix(k)
    for _plan_id, patterns in parsed_plans:
        for pat in patterns:
            if pat == "*":
                return True
            if pat == k or pat == prefix:
                return True
            if pat.endswith(".*") and pat[:-2] == prefix:
                return True
    return False


def _check_orphan_entitlement(conn, now_str):
    parsed_plans = _load_plan_kinds(conn)
    rows = conn.execute(
        "SELECT kind, COUNT(*), MIN(open_id) FROM entitlement_log"
        " WHERE CAST(allowed AS INTEGER) = 1"
        " GROUP BY kind"
        " ORDER BY kind"
    ).fetchall()
    return [
        {"code": "orphan_entitlement",
         "detail": "allowed=1 的 kind 不被任何 plan.kinds 覆盖：%s（%d 行放行，样例 open_id=%s）"
                   % (kind, n, sample)}
        for kind, n, sample in rows
        if not _kind_covered(kind, parsed_plans)
    ]


def _check_paid_without_subscription(conn, now_str):
    rows = conn.execute(
        "SELECT o.order_id, o.open_id, o.status, o.created_at FROM payment_order o"
        " WHERE lower(trim(o.status)) IN (?, ?)"
        "   AND NOT EXISTS (SELECT 1 FROM subscription s"
        "                   WHERE s.subscriber_open_id = o.open_id)"
        " ORDER BY o.created_at, o.order_id",
        PAID_STATUSES,
    ).fetchall()
    return [
        {"code": "paid_without_subscription",
         "detail": "已支付订单无任何订阅（按 open_id 关联）：order_id=%s open_id=%s status=%s created_at=%s"
                   % row}
        for row in rows
    ]


CHECKS = (
    ("orphan_open_id", _check_orphan_open_id),
    ("unknown_plan", _check_unknown_plan),
    ("bad_range", _check_bad_range),
    ("stale_active", _check_stale_active),
    ("status_contradiction", _check_status_contradiction),
    ("orphan_entitlement", _check_orphan_entitlement),
    ("paid_without_subscription", _check_paid_without_subscription),
)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def reconcile(db_path=None, now=None):
    """执行全部校验，返回 ``{"ok", "checked", "issues"}``。

    - 任一项异常 → 追加 code=check_failed 并继续后续项（fail-visible）。
    - 库不可打开 → 每个校验项各记一条 check_failed，checked=0，ok=False。
    """
    path = Path(db_path) if db_path is not None else _db_path_default()
    now_str = _now_str(now)
    issues = []
    checked = 0

    try:
        conn = _open_readonly(path)
    except Exception as exc:
        for name, _fn in CHECKS:
            issues.append({
                "code": "check_failed",
                "detail": "%s: 账本不可只读打开（%s）：%s"
                          % (name, path, exc),
            })
        return {"ok": False, "checked": 0, "issues": issues}

    try:
        for table in TABLES:
            try:
                checked += conn.execute(
                    "SELECT COUNT(*) FROM %s" % table).fetchone()[0]  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
            except Exception as exc:
                # 表缺失/损坏不静默：少算的行数必须留痕
                issues.append({
                    "code": "check_failed",
                    "detail": "row_count(%s): %s: %s"
                              % (table, type(exc).__name__, exc),
                })

        for name, fn in CHECKS:
            try:
                issues.extend(fn(conn, now_str))
            except Exception as exc:
                issues.append({
                    "code": "check_failed",
                    "detail": "%s: %s: %s" % (name, type(exc).__name__, exc),
                })
    finally:
        # closed on failure：连接必须关；但上面的异常已全部落 issue，不吞
        conn.close()

    return {"ok": not issues, "checked": checked, "issues": issues}


def _db_path_default():
    """取模块口径的真实库路径（env SUBSCRIPTION_DB 优先，调用时读取）。"""
    return _store._db_path()


def render_human(result, db_path=None):
    """人读摘要：每个 code 的命中数与样例行。"""
    lines = ["订阅账本勾稽自检（只读）"]
    if db_path is not None:
        lines.append("库路径: %s" % db_path)
    lines.append("检查行数: %d" % result.get("checked", 0))

    grouped = {}
    for issue in result.get("issues", []):
        grouped.setdefault(issue["code"], []).append(issue.get("detail", ""))

    ordered = [code for code, _fn in CHECKS] + ["check_failed"]
    if not grouped:
        lines.append("")
        lines.append("结果: 账本自洽，0 问题")
        return "\n".join(lines)

    for code in ordered:
        items = grouped.get(code)
        if not items:
            continue
        lines.append("")
        lines.append("[%s] 命中 %d 条" % (code, len(items)))
        for detail in items[:SAMPLE_LIMIT]:
            lines.append("  - %s" % detail)
        if len(items) > SAMPLE_LIMIT:
            lines.append("  - ...（其余 %d 条略，用 --json 看全量）"
                         % (len(items) - SAMPLE_LIMIT))

    lines.append("")
    lines.append("结果: 发现 %d 条问题（%d 类）"
                 % (len(result.get("issues", [])), len(grouped)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="订阅账本勾稽自检（只读，不修复）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON：{\"ok\",\"checked\",\"issues\"}")
    args = parser.parse_args(argv)

    try:
        result = reconcile()
    except Exception as exc:
        # 工具自身失败（非账本内容问题）：退出码 2，且仍然可见
        print(json.dumps({
            "ok": False, "checked": 0,
            "issues": [{"code": "check_failed",
                        "detail": "fatal: %s: %s" % (type(exc).__name__, exc)}],
        }, ensure_ascii=False))
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(render_human(result, _db_path_default()))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
