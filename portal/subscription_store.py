# -*- coding: utf-8 -*-
"""订阅账本最小集（飞书中台 P2 · FC-B 工位，契约先行）。

供入向/出向订阅闸（FC-C）消费的只读判定 + 本模块内写操作。
契约（派工单 FC-B 节，逐字，不得更改签名）::

    is_entitled(open_id, kind, now=None) -> (bool, reason)
      # True/False + 人读理由；store 文件缺失或任何异常 → (True, "store_unavailable_fail_open")
      # owner/ops 域身份（白名单名单内 open_id）→ (True, "domain_exempt")
      # 判定口径：订阅行 status=生效 AND now∈[start,end] AND 当日额度（daily_quota，按 kind 前缀计数）未耗尽
    upsert_subscriber(open_id, display_name="") / grant_subscription(open_id, plan_id, days) /
    expire_sweep(now=None) / entitlement_log(open_id, kind, allowed, reason)

设计要点：
- sqlite 默认 portal/work/subscription.sqlite3，首次连接自动建 5 表（幂等）。
- DB 路径 env ``SUBSCRIPTION_DB`` 可覆盖（仅测试用，沿 api_notify AIOS_ROOT 判例，
  每次调用时读取 env，不缓存，便于 monkeypatch）。
- 种子档位以 SEED_PLANS 常量随库（portal/work/ 整目录被 .gitignore 忽略，
  plans_seed.json 无法入库，故首建 plan 表时写种子，见 SEED_PLANS 注释）。
- owner/ops 豁免名单：本模块 OWN_DOMAIN_OPEN_IDS 常量（默认空集）。
  **不 import inbound_gateway.is_allowed**（避免循环依赖与越 FC-C 地盘）；
  名单由部署侧 env ``SUBSCRIPTION_OWN_DOMAIN`` 提供（逗号分隔 open_id），
  is_entitled 每次先查该集合（常量 + env 并集）。
- 并发说明：每次操作独立开单连接（connect ... 上下文即开即关），单连接锁
  可接受；不使用 mode=ro，因为本模块还承担 entitlement_log 写入。
- closed on failure：is_entitled 任何异常均 fail-open 并给诚实理由，
  不造数不假绿；写函数异常向上抛由调用方处置。

代码落盘 ≠ 线上生效：本模块为离线新增，未重启 8097/8091/8787，
线上入向闸消费本契约需 FC-C 接线并另批重启。
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

__all__ = [
    "is_entitled",
    "upsert_subscriber",
    "grant_subscription",
    "expire_sweep",
    "entitlement_log",
    "get_subscriber_summary",
    "OWN_DOMAIN_OPEN_IDS",
    "SEED_PLANS",
]

_MODULE_DIR = Path(__file__).resolve().parent

# 订阅行“生效”状态字面量（判定口径：status=生效）。
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"


def _db_path() -> Path:
    """DB 路径锚：env SUBSCRIPTION_DB 可覆盖（仅测试用，调用时读取）。"""
    env = os.environ.get("SUBSCRIPTION_DB")
    if env:
        return Path(env)
    return _MODULE_DIR / "work" / "subscription.sqlite3"


# ---------------------------------------------------------------------------
# owner/ops 域豁免名单
# ---------------------------------------------------------------------------
# 默认空集：名单由部署侧 env SUBSCRIPTION_OWN_DOMAIN 提供（逗号分隔 open_id），
# 部署时也可直接在部署侧向本常量注入。is_entitled 每次调用先查该集合。
# 刻意不 import inbound_gateway.is_allowed：避免循环依赖，且那是 FC-C 地盘。
OWN_DOMAIN_OPEN_IDS = frozenset()


def _own_domain_ids() -> frozenset:
    """OWN_DOMAIN_OPEN_IDS 常量与 env SUBSCRIPTION_OWN_DOMAIN（逗号分隔）的并集。"""
    extra = os.environ.get("SUBSCRIPTION_OWN_DOMAIN", "")
    names = {s.strip() for s in extra.split(",") if s.strip()}
    return frozenset(OWN_DOMAIN_OPEN_IDS) | frozenset(names)


# ---------------------------------------------------------------------------
# 种子档位（占位，价格待老田定价）
# ---------------------------------------------------------------------------
# portal/work/ 被 .gitignore 整目录忽略（.gitignore:187 `work/`），
# plans_seed.json 无法随库，故种子以常量随代码：首次建表时若 plan 表为空
# 则写入以下两条占位档位。
#   internal：owner 用，quota 无限（daily_quota<=0 视为不限额）
#   basic_monthly：示例档 30 天 / 日 20 条；price 为 placeholder，待老田定价
SEED_PLANS = [
    {
        "plan_id": "internal",
        "name": "内部档（owner/ops）",
        "price": "0（内部免费）",
        "cycle_days": 36500,
        "daily_quota": 0,  # 0 = 不限额
        "kinds": ["*"],
    },
    {
        "plan_id": "basic_monthly",
        "name": "基础月度档",
        # placeholder 待老田定价 —— M3 收银台前仅占位，不构成真实报价
        "price": "placeholder 待老田定价",
        "cycle_days": 30,
        "daily_quota": 20,
        "kinds": ["news.*"],
    },
]

_DDL = """
CREATE TABLE IF NOT EXISTS subscriber (
    open_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan (
    plan_id    TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    price      TEXT NOT NULL DEFAULT '',
    cycle_days INTEGER NOT NULL DEFAULT 30,
    daily_quota INTEGER,             -- NULL 或 <=0 视为不限额
    kinds      TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS subscription (
    subscriber_open_id TEXT NOT NULL,
    plan_id            TEXT NOT NULL,
    start              TEXT NOT NULL,
    end                TEXT NOT NULL,
    status             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entitlement_log (
    ts     TEXT NOT NULL,
    open_id TEXT NOT NULL,
    kind   TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_order (
    order_id   TEXT PRIMARY KEY,
    open_id    TEXT NOT NULL,
    amount     TEXT NOT NULL DEFAULT '',
    channel    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _connect(create: bool = True):
    """打开 sqlite 连接。create=True 时确保目录与 5 表存在（幂等）。

    首建 plan 表时若为空则写入 SEED_PLANS 种子档位。
    """
    path = _db_path()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    if create:
        conn.executescript(_DDL)
        _seed_plans_if_empty(conn)
    return conn


def _seed_plans_if_empty(conn) -> None:
    row = conn.execute("SELECT COUNT(*) FROM plan").fetchone()
    if row and row[0] == 0:
        for p in SEED_PLANS:
            conn.execute(
                "INSERT OR IGNORE INTO plan(plan_id, name, price, cycle_days,"
                " daily_quota, kinds) VALUES (?,?,?,?,?,?)",
                (
                    p["plan_id"],
                    p["name"],
                    p["price"],
                    p["cycle_days"],
                    p["daily_quota"],
                    json.dumps(p["kinds"], ensure_ascii=False),
                ),
            )
        conn.commit()


def _norm_now(now) -> str:
    """归一化时间参数为 'YYYY-MM-DD HH:MM:SS' 字符串（None→本地当前时间）。"""
    if now is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(now, datetime):
        return now.strftime("%Y-%m-%d %H:%M:%S")
    return str(now)


def _kind_prefix(kind: str) -> str:
    """kind 前缀：取首个 '.' 之前的部分（如 'news.brief'→'news'；无点→原样）。

    当日额度按该前缀计数：同前缀的 kind 共享当日额度池。
    """
    return kind.split(".", 1)[0] if kind else kind


def _today(now_str: str) -> str:
    return now_str[:10]


# ---------------------------------------------------------------------------
# 契约函数
# ---------------------------------------------------------------------------

def is_entitled(open_id, kind, now=None):
    """权益判定（只读）。返回 (bool, reason)；契约语义见模块 docstring。

    判定顺序：
      1. owner/ops 豁免名单（常量 + env）命中 → (True, "domain_exempt")
      2. store 文件缺失或任何异常 → (True, "store_unavailable_fail_open")
      3. 订阅行 status=生效 AND now∈[start,end] AND 当日额度未耗尽
         reason ∈ subscription_active / no_subscription /
                  subscription_expired / daily_quota_exhausted
    """
    # 1. 域豁免：无需触碰 store，最先判（closed on failure 之外的确定性放行）
    if open_id in _own_domain_ids():
        return True, "domain_exempt"

    # 2. store 文件缺失 → fail-open（读路径不建库）
    path = _db_path()
    if not path.exists():
        return True, "store_unavailable_fail_open"

    try:
        now_str = _norm_now(now)
        with _connect(create=False) as conn:
            # 生效订阅行（可能多行，任一覆盖 now 即视为生效）
            rows = conn.execute(
                "SELECT plan_id, start, end FROM subscription"
                " WHERE subscriber_open_id=? AND status=?",
                (open_id, STATUS_ACTIVE),
            ).fetchall()
            if not rows:
                return False, "no_subscription"

            covering = [r for r in rows if r[1] <= now_str <= r[2]]
            if not covering:
                if any(r[2] < now_str for r in rows):
                    return False, "subscription_expired"
                return False, "no_subscription"  # 订阅尚未开始（start>now）

            plan_id = covering[0][0]
            plan = conn.execute(
                "SELECT daily_quota FROM plan WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if not plan:
                # 订阅行指向不存在的档位：按无额度判定，诚实理由
                return False, "no_subscription"

            daily_quota = plan[0]
            if daily_quota is None or daily_quota <= 0:
                return True, "subscription_active"  # 不限额

            # 当日额度：entitlement_log 中当日、同 kind 前缀、放行的计数
            prefix = _kind_prefix(kind)
            today = _today(now_str)
            used = conn.execute(
                "SELECT COUNT(*) FROM entitlement_log"
                " WHERE open_id=? AND allowed=1 AND substr(ts,1,10)=?"
                " AND (kind=? OR kind LIKE ?)",
                (open_id, today, prefix, prefix + ".%"),
            ).fetchone()[0]
            if used >= daily_quota:
                return False, "daily_quota_exhausted"
            return True, "subscription_active"
    except Exception:
        # closed on failure：账本任何异常都不拦截用户，但理由必须诚实
        return True, "store_unavailable_fail_open"


def upsert_subscriber(open_id, display_name=""):
    """注册/更新订阅者。存在则更新 display_name（传入非空时），返回 open_id。"""
    now_str = _norm_now(None)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO subscriber(open_id, display_name, created_at)"
            " VALUES (?,?,?)"
            " ON CONFLICT(open_id) DO UPDATE SET"
            " display_name=CASE WHEN excluded.display_name!=''"
            " THEN excluded.display_name ELSE subscriber.display_name END",
            (open_id, display_name, now_str),
        )
        conn.commit()
    return open_id


def grant_subscription(open_id, plan_id, days):
    """授予订阅：从当前时刻起 days 天，status=生效。档位不存在则 ValueError。"""
    days = int(days)
    if days <= 0:
        raise ValueError("days 必须为正整数")
    now_str = _norm_now(None)
    start = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
    end = (start + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        plan = conn.execute(
            "SELECT plan_id FROM plan WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError("plan 不存在: %s" % plan_id)
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start, end,"
            " status) VALUES (?,?,?,?,?)",
            (open_id, plan_id, now_str, end, STATUS_ACTIVE),
        )
        conn.commit()
    return {"open_id": open_id, "plan_id": plan_id,
            "start": now_str, "end": end, "status": STATUS_ACTIVE}


def expire_sweep(now=None):
    """过期清扫：status=生效 且 end<now 的订阅行置为 expired。返回清扫行数。"""
    now_str = _norm_now(now)
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE subscription SET status=? WHERE status=? AND end<?",
            (STATUS_EXPIRED, STATUS_ACTIVE, now_str),
        )
        conn.commit()
        return cur.rowcount


def entitlement_log(open_id, kind, allowed, reason):
    """落一行判定留痕（闸门只读判定 + 经本函数写日志）。返回 ts。"""
    ts = _norm_now(None)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO entitlement_log(ts, open_id, kind, allowed, reason)"
            " VALUES (?,?,?,?,?)",
            (ts, open_id, kind, 1 if allowed else 0, reason),
        )
        conn.commit()
    return ts


# ---------------------------------------------------------------------------
# FD-A（飞书中台 P2.5 · 2026-10-20）客户订阅面只读摘要——只追加不改写
# ---------------------------------------------------------------------------

def get_subscriber_summary(open_id, now=None):
    """订阅者只读摘要（P2.5 客户订阅面 /api/subscription/status 数据出口）。

    返回 dict（契约见派工单 FD-A 节）::

        {open_id, display_name,
         subscriptions: [{plan_id, start, end, status}]（按 end 降序）,
         today_used_by_prefix: {kind 前缀: 当日放行计数},
         daily_quota_by_plan:  {plan_id: daily_quota（None/<=0 视为不限额）},
         plan_name_by_id:      {plan_id: 档位名},
         reason, generated_at}

    - reason 与 is_entitled 同口径的诚实判定（不含 owner 豁免与 fail-open
      语义——本函数是纯账本投影，不承担闸门职责）：
      subscription_active / subscription_expired / no_subscription。
    - 纯只读：连接 create=False，不写任何表，不落 entitlement_log。
    - closed on failure：store 文件缺失返回 {"error": "store_unavailable"}
      （读路径不建库，同 is_entitled）；任何异常返回 {"error": ...} 不抛，
      调用方（portal_api branch）原样透传即可，无需自行兜底。
    """
    try:
        path = _db_path()
        if not path.exists():
            return {"error": "store_unavailable"}
        now_str = _norm_now(now)
        with _connect(create=False) as conn:
            sub = conn.execute(
                "SELECT display_name FROM subscriber WHERE open_id=?",
                (open_id,),
            ).fetchone()
            subs = conn.execute(
                "SELECT plan_id, start, end, status FROM subscription"
                " WHERE subscriber_open_id=? ORDER BY end DESC",
                (open_id,),
            ).fetchall()
            used_rows = conn.execute(
                "SELECT kind FROM entitlement_log WHERE open_id=?"
                " AND allowed=1 AND substr(ts,1,10)=?",
                (open_id, _today(now_str)),
            ).fetchall()
            plans = conn.execute(
                "SELECT plan_id, name, daily_quota FROM plan"
            ).fetchall()
        used = {}
        for (k,) in used_rows:
            p = _kind_prefix(k)
            used[p] = used.get(p, 0) + 1
        # reason 判定与 is_entitled 同口径（只看 status=active 的行）
        active = [r for r in subs if r[3] == STATUS_ACTIVE]
        if any(r[1] <= now_str <= r[2] for r in active):
            reason = "subscription_active"
        elif any(r[2] < now_str for r in active):
            reason = "subscription_expired"
        else:
            reason = "no_subscription"  # 无行或订阅尚未开始（start>now）
        return {
            "open_id": open_id,
            "display_name": sub[0] if sub else "",
            "subscriptions": [
                {"plan_id": r[0], "start": r[1], "end": r[2], "status": r[3]}
                for r in subs
            ],
            "today_used_by_prefix": used,
            "daily_quota_by_plan": {r[0]: r[2] for r in plans},
            "plan_name_by_id": {r[0]: r[1] for r in plans},
            "reason": reason,
            "generated_at": now_str,
        }
    except Exception as exc:  # closed on failure：不抛，诚实报错
        return {"error": "store_unavailable: %s" % exc}
