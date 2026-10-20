# -*- coding: utf-8 -*-
"""订阅账本离线初始化工具（飞书中台 P3.8-lite · M-C / WO-FS-113）。

背景（实测，不是推测）：上一波 L-D 的 ``tools/reconcile_subscription.py``
在真实环境跑出 ``ok=false, checked=0, check_failed×7``，因为订阅账本从未建过
（``portal/work/subscription.sqlite3`` 不存在、``SUBSCRIPTION_DB`` 未设置）。
``portal/subscription_store.py`` 的读路径按设计不建库（首次写入才建），
所以重启后「我的订阅」「订阅运营」两个管理面都会降级。

旧补救办法是「由管理员在飞书执行一次开通指令触发建库」：要先有真实 open_id、
会往账本写一条假业务数据、还依赖在役 bot 可用。本工具用一个干净、幂等、
可离线执行的初始化入口替代它。

用法::

    python tools/init_subscription_db.py            # 默认 --check，只读探测
    python tools/init_subscription_db.py --check --json
    python tools/init_subscription_db.py --init     # 建库（幂等）
    python tools/init_subscription_db.py --init --json

库路径规则（不猜，取自 portal/subscription_store.py）：
``_db_path()`` 每次调用读 env ``SUBSCRIPTION_DB``，非空则用之，否则
``<repo>/portal/work/subscription.sqlite3``。本工具不重造路径逻辑，直接复用。

安全约束（写进 docstring，也在代码里实际校验）：

1. 默认 ``--check`` 只读：以 sqlite URI ``mode=ro`` + ``PRAGMA query_only=ON``
   打开，只跑 ``PRAGMA quick_check`` 与 ``sqlite_master`` 查询，
   **不创建目录、不创建文件、不建表、不写种子**。
2. ``--init`` 在「库已存在 + 5 表齐备 + 种子档位齐全 + quick_check 通过」时
   必须是 **no-op**：代码路径直接返回，不调用任何写接口，不重建、不清空、
   不删行（连文件 mtime 都不动）。
3. 发现库文件损坏（``quick_check`` 非 ok，或文件不可作为 sqlite 打开）时
   **拒绝自动修复**：只报 ``db_corrupt`` 并要求人工处置。损坏库上执行
   ``--init`` 不会写一个字节。
4. ``--init`` 只会补「缺的表」和「空 plan 表里的种子档位」——建表与种子
   完全复用 ``subscription_store.ensure_schema()``（其内部即
   ``_connect(create=True)``），本工具不另写 DDL，避免表结构漂移。
5. 本工具本身不改动任何服务、不接排程、不发消息；生产路径上的 ``--init``
   属于重启后的动作，须老田批准后执行（本工位未在任何真实库上执行）。

问题 code（``problems`` 数组元素）::

    db_missing            库文件不存在（首次未初始化，非异常）
    db_corrupt            库文件存在但不可读/quick_check 报错（异常，拒绝自动修复）
    missing_table:<名字>  库存在但该表缺失（异常）
    seed_missing          plan 表在但种子档位不全（异常，本工具不造档位）
    ensure_failed:<...>   ensure_schema 执行异常（closed on failure）

「库不存在」与「库存在但结构缺失」在运维上含义完全不同，故人读摘要在
这两种情形下给不同措辞：前者是「尚未初始化」，后者是「异常」。

退出码：ok=true → 0；ok=false（结构缺失且未 --init、初始化失败、库损坏）→ 1；
工具自身执行失败 → 2。
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORTAL_DIR = _REPO_ROOT / "portal"
if str(_PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(_PORTAL_DIR))

import subscription_store as _store  # noqa: E402  （仅取 _db_path / ensure_schema）

__all__ = ["probe", "init_db", "check_db", "render_human", "main", "TABLES"]

# 账本 5 表（与 subscription_store._DDL 同口径；顺序即人读输出顺序）
TABLES = ("subscriber", "plan", "subscription", "entitlement_log", "payment_order")

# 探测状态
STATE_MISSING = "missing"       # 库文件不存在：首次未初始化
STATE_CORRUPT = "corrupt"       # 库文件存在但不可读/损坏：异常，拒绝自动修复
STATE_INCOMPLETE = "incomplete"  # 库存在但表缺/种子缺：异常
STATE_READY = "ready"           # 5 表齐备 + quick_check 通过 + 种子齐备


def _open_readonly(path) -> sqlite3.Connection:
    """以 sqlite URI ``mode=ro`` 打开，库不存在即抛错（不新建文件）。"""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def probe(path=None) -> dict:
    """只读探测账本结构与可写性，绝不创建任何东西。

    返回 ``{"state", "tables", "seeded_plans", "problems", "detail"}``：
    state ∈ missing / corrupt / incomplete / ready；problems 同模块 docstring。
    """
    p = Path(path) if path is not None else _store._db_path()
    seed_ids = [x["plan_id"] for x in _store.SEED_PLANS]

    if not p.exists():
        # 库不存在 ≠ 库坏了：措辞分开（见 render_human）
        return {"state": STATE_MISSING, "tables": [], "seeded_plans": 0,
                "problems": ["db_missing"],
                "detail": "库文件不存在（首次未初始化）"}

    problems = []
    try:
        conn = _open_readonly(p)
    except Exception as exc:
        return {"state": STATE_CORRUPT, "tables": [], "seeded_plans": 0,
                "problems": ["db_corrupt"],
                "detail": "库不可只读打开：%s: %s" % (type(exc).__name__, exc)}

    try:
        quick = [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
        if quick != ["ok"]:
            sample = "；".join(str(x) for x in quick[:3])
            return {"state": STATE_CORRUPT, "tables": [], "seeded_plans": 0,
                    "problems": ["db_corrupt"],
                    "detail": "quick_check 未通过：%s" % (sample or "（空结果）")}

        present = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        tables = [t for t in TABLES if t in present]
        for t in TABLES:
            if t not in present:
                problems.append("missing_table:%s" % t)

        seeded = 0
        if "plan" in present:
            got = {
                row[0] for row in conn.execute(
                    "SELECT plan_id FROM plan").fetchall()
            }
            seeded = len([pid for pid in seed_ids if pid in got])
            if seeded < len(seed_ids):
                problems.append("seed_missing")
    except Exception as exc:  # closed on failure：损坏/不可读一律判 corrupt
        return {"state": STATE_CORRUPT, "tables": [], "seeded_plans": 0,
                "problems": ["db_corrupt"],
                "detail": "读取库结构失败：%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()

    state = STATE_READY if not problems else STATE_INCOMPLETE
    detail = {
        STATE_READY: "5 表齐备，quick_check 通过，种子档位齐备",
        STATE_INCOMPLETE: "库存在但结构缺失（异常）",
    }[state]
    return {"state": state, "tables": tables, "seeded_plans": seeded,
            "problems": problems, "detail": detail}


def _result(mode, db_path, created, prob, problems):
    """组装对外的 7 键结构（键集固定，--json 与内部调用共用）。"""
    return {
        "ok": not problems,
        "db_path": str(db_path),
        "mode": mode,
        "created": created,
        "tables": list(prob.get("tables", [])),
        "seeded_plans": int(prob.get("seeded_plans", 0)),
        "problems": list(problems),
    }


def check_db(path=None) -> dict:
    """只读检查（默认动作）。结构不齐 → ok=False，但不修、不建、不写。"""
    p = Path(path) if path is not None else _store._db_path()
    prob = probe(p)
    return _result("check", p, False, prob, prob["problems"])


def init_db(path=None, now=None) -> dict:
    """幂等初始化：库缺失则建库，缺表则补表，空 plan 表则写种子档位。

    - 已 ready → 直接返回 no-op（created=False，不调用任何写接口）。
    - 损坏 → 回 db_corrupt，拒绝自动修复，不写任何字节。
    - 其余 → 调 ``subscription_store.ensure_schema()`` 后重新探测。
    """
    p = Path(path) if path is not None else _store._db_path()
    before = probe(p)

    if before["state"] == STATE_READY:
        # 安全约束 2：已齐备即 no-op，连写连接都不开
        return _result("init", p, False, before, [])

    if before["state"] == STATE_CORRUPT:
        # 安全约束 3：拒绝自动修复，只报 code 让人工处置
        return _result("init", p, False, before, before["problems"])

    try:
        ensured = _ensure_schema_at(p, now)
    except Exception as exc:  # 理论上 ensure_schema 不抛；仍然 fail-visible
        problems = ["ensure_failed:%s: %s" % (type(exc).__name__, exc)]
        return _result("init", p, False, before, problems)

    after = probe(p)
    problems = list(after["problems"]) + [
        code for code in ensured.get("problems", []) if code not in after["problems"]
    ]
    return _result("init", p, bool(ensured.get("created")), after, problems)


def _ensure_schema_at(path, now=None) -> dict:
    """调 subscription_store.ensure_schema()，保证其落在 ``path`` 这个库上。

    ensure_schema 无路径参数（其签名由工单锁定），库锚只认 env
    ``SUBSCRIPTION_DB`` 与默认路径。故当显式给的 path 与当前锚不一致时，
    临时把 env 指向 path（调用结束即还原），避免「报的是 A 库、写的是 B 库」。
    """
    anchor = _store._db_path()
    if Path(path) == anchor:
        return _store.ensure_schema(now=now)
    prev = os.environ.get("SUBSCRIPTION_DB")
    os.environ["SUBSCRIPTION_DB"] = str(path)
    try:
        return _store.ensure_schema(now=now)
    finally:
        if prev is None:
            os.environ.pop("SUBSCRIPTION_DB", None)
        else:
            os.environ["SUBSCRIPTION_DB"] = prev


def render_human(result) -> str:
    """人读摘要：库路径、模式、5 表逐项、种子档位数、结论。"""
    lines = ["订阅账本初始化检查（%s）"
             % ("初始化" if result["mode"] == "init" else "只读检查")]
    lines.append("库路径: %s" % result["db_path"])
    if result["mode"] == "init":
        lines.append("本次是否新建库文件: %s" % ("是" if result["created"] else "否"))
    lines.append("已存在表: %s" % (", ".join(result["tables"]) or "（无）"))
    missing = [t for t in TABLES if t not in result["tables"]]
    if missing and "db_missing" not in result["problems"]:
        # 库不存在时不逐表列「缺失」：那是没建过，不是丢表
        lines.append("缺失表: %s" % ", ".join(missing))
    lines.append("种子档位: %d/%d" % (result["seeded_plans"],
                                     len(_store.SEED_PLANS)))

    problems = result["problems"]
    lines.append("")
    if not problems:
        lines.append("结论: 账本结构齐备（5 表 + 种子档位），quick_check 通过")
        return "\n".join(lines)

    codes = [c.split(":", 1)[0] for c in problems]
    if "db_missing" in codes:
        # 与「结构缺失」措辞必须不同：这是没建过，不是坏了
        lines.append("结论: 库不存在 —— 账本从未初始化过（首次未初始化，不是损坏）。")
        lines.append("      读路径按设计不建库，故两个订阅管理面此时会降级。")
        lines.append("      处置: 经批准后执行 --init 建库（本工具不会自动建）。")
        return "\n".join(lines)
    if "db_corrupt" in codes:
        lines.append("结论: 库文件存在但已损坏 —— 拒绝自动修复，须人工处置。")
        lines.append("      本工具不会重建、不会清空、不会覆盖该文件。")
        lines.append("      处置: 先备份该文件，再人工确认可丢弃后另行建库。")
    else:
        lines.append("结论: 库存在但结构缺失（异常，非首次未初始化）。")

    for code in problems:
        lines.append("  - %s" % code)
    if result["mode"] == "check":
        lines.append("      处置: 结构缺失可执行 --init 补齐（只补缺表/补种子，不删数据）。")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="订阅账本离线初始化（默认只读检查；--init 幂等建库）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="只读探测（默认）：不创建任何东西")
    mode.add_argument("--init", action="store_true",
                      help="建库/补表/补种子（幂等；已齐备时 no-op）")
    parser.add_argument("--json", action="store_true",
                        help="结构化输出 {\"ok\",\"db_path\",\"mode\",\"created\","
                             "\"tables\",\"seeded_plans\",\"problems\"}")
    args = parser.parse_args(argv)

    try:
        result = init_db() if args.init else check_db()
    except Exception as exc:
        # 工具自身执行失败（非账本内容问题）：退出码 2，且仍然可见
        payload = {
            "ok": False,
            "db_path": str(_store._db_path()),
            "mode": "init" if args.init else "check",
            "created": False,
            "tables": [],
            "seeded_plans": 0,
            "problems": ["internal_error:%s: %s" % (type(exc).__name__, exc)],
        }
        print(json.dumps(payload, ensure_ascii=False) if args.json
              else "工具执行失败: %s: %s" % (type(exc).__name__, exc))
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(render_human(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
