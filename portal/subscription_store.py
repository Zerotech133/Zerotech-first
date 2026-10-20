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
    "list_subscribers",
    "iter_all_subscribers",
    "OWN_DOMAIN_OPEN_IDS",
    "SEED_PLANS",
    "ensure_schema",
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


# ---------------------------------------------------------------------------
# FE-B（飞书中台 P2.6 · 2026-10-20）订阅者全集只读列表——只追加不改写
# ---------------------------------------------------------------------------

def list_subscribers(limit=50, now=None, offset=0):
    """订阅者全集只读列表（P2.6 商务面 /api/subscription/list 数据出口）。

    返回 dict（契约见派工单 FE-B 节 + P3.9-lite N-A 分页追加）::

        {"ok": True, "count": N,
         "subscribers": [{open_id, display_name, plan_id, status,
                          start, end, daily_quota, used_today}, ...],
         "offset": 生效偏移, "has_more": bool}

    - ORDER BY open_id 稳定序；limit 钳制 [1,200]（非法输入按默认 50）；
      count 为返回的 subscribers 行数（与列表严格一致，不冒充总量）。
    - offset 为跳过行数（SQL ``LIMIT ? OFFSET ?`` 参数化）；非法值（负数 /
      非 int / 数字字符串）一律归一到 0，不抛异常。offset 放在签名末尾，
      既有位置传参 ``list_subscribers(200)`` 仍表示 limit=200。
    - ``has_more``：本次行数 == 生效 limit 即视为「可能还有」（无 total
      计数，宁松不紧）。True 不代表确定还有，False 才表示已到底。
    - 每个订阅者一行：优先取 status=active 且覆盖 now 的订阅行，否则取
      end 最新一行（与 get_subscriber_summary 的 reason 口径一致）；
      无任何订阅行时 plan_id/status/start/end/daily_quota 为 None（诚实空）。
    - used_today：entitlement_log 当日 allowed=1 行按订阅者级合计
      （口径与 is_entitled 额度计数一致——该计数即同日同前缀放行数，
       订阅者级合计为其跨前缀求和）。
    - 纯只读：连接 create=False，读路径不建库（同 is_entitled 判例），
      不写任何表，不落 entitlement_log。
    - closed on failure：store 文件缺失或任何异常 →
      {"ok": False, "error": "store_unavailable"}（不抛，原样透传给
      portal_api branch 即可，调用方无需自行兜底）。
    """
    try:
        try:
            _limit = int(limit)
        except (TypeError, ValueError):
            _limit = 50
        _limit = max(1, min(200, _limit))
        # 非法 offset（负数 / 非 int / 数字字符串）一律归一到 0，不抛异常
        _offset = offset if isinstance(offset, int) and offset >= 0 else 0

        path = _db_path()
        if not path.exists():
            return {"ok": False, "error": "store_unavailable"}
        now_str = _norm_now(now)
        with _connect(create=False) as conn:
            who = conn.execute(
                "SELECT open_id, display_name FROM subscriber"
                " ORDER BY open_id LIMIT ? OFFSET ?",
                (_limit, _offset),
            ).fetchall()
            sub_rows = conn.execute(
                "SELECT subscription.subscriber_open_id, subscription.plan_id,"
                " subscription.status, subscription.start, subscription.end,"
                " plan.daily_quota FROM subscription"
                " LEFT JOIN plan ON plan.plan_id=subscription.plan_id"
                " ORDER BY subscription.end DESC"
            ).fetchall()
            used_rows = conn.execute(
                "SELECT open_id, COUNT(*) FROM entitlement_log"
                " WHERE allowed=1 AND substr(ts,1,10)=? GROUP BY open_id",
                (_today(now_str),),
            ).fetchall()
        by_open = {}
        for r in sub_rows:
            by_open.setdefault(r[0], []).append(r[1:])
        used = {r[0]: r[1] for r in used_rows}
        subscribers = []
        for open_id, display_name in who:
            recs = by_open.get(open_id) or []
            pick = None
            for r in recs:
                if r[1] == STATUS_ACTIVE and r[2] <= now_str <= r[3]:
                    pick = r
                    break
            if pick is None and recs:
                pick = recs[0]  # end DESC 的最新一行
            subscribers.append({
                "open_id": open_id,
                "display_name": display_name,
                "plan_id": pick[0] if pick else None,
                "status": pick[1] if pick else None,
                "start": pick[2] if pick else None,
                "end": pick[3] if pick else None,
                "daily_quota": pick[4] if pick else None,
                "used_today": used.get(open_id, 0),
            })
        subscribers = subscribers[:_limit]
        return {"ok": True, "count": len(subscribers),
                "subscribers": subscribers,
                "offset": _offset,
                "has_more": len(subscribers) == _limit}
    except Exception:  # closed on failure：不抛，诚实报错
        return {"ok": False, "error": "store_unavailable"}


def iter_all_subscribers(page_size=200, now=None, max_pages=50):
    """逐页取订阅者全集（批量任务用，P3.9-lite N-A）——截断与异常都显式上报。

    背景：``list_subscribers`` 单页上限 200，订阅者超过 200 时一次性取数必然
    漏人；本函数用 offset 逐页推进，把「是否取完」变成可判定的返回值。

    返回 dict::

        {"ok": bool,
         "error": None|"store_unavailable"|"store_exception"|"bad_payload",
         "count": N, "subscribers": [...],
         "page_size": 生效页大小, "pages": 已取页数, "max_pages": 生效上限,
         "truncated": bool, "complete": bool, "problems": [...]}

    - ``page_size`` 钳制 [1,200]（与 list_subscribers 同口径，非法按 200）；
      ``max_pages`` 钳制 [1,1000]（非法按 50），防账本异常膨胀时跑飞。
    - ``truncated``：``max_pages`` 用尽且最后一页仍取满 → True，显式报告
      「没取完」并给出 ``pages``，绝不静默停下。
    - ``complete``：仅当 ``ok=True`` 且 ``truncated=False`` 时为 True，
      是唯一能声称「已到底」的组合。
    - 某一页 store 返回 ok=False / 抛异常 / 形态不可识别 → 立即中止：已取
      行原样保留，``ok=False`` + ``error`` 错误码 + ``problems`` 带页码，
      **不当成「没有更多」**（fail-open 禁令）。此时 ``truncated`` 仍为
      False，调用方必须看 ``ok``/``complete``，不能只看 ``truncated``。
    - ``now`` 在遍历开始归一一次并固定传给每页，避免跨页 used_today 与选行
      口径随时钟推进漂移。
    - 一致性：offset 分页不做快照；遍历期间若有写入，可能重复或遗漏。
      批量任务应在写入静默窗口内跑（如 expire_sweep 之后）。
    - 只读取数，全部走 ``list_subscribers``（create=False，读路径不建库）；
      closed on failure：任何异常都不抛，落到 ``error``/``problems``。
    """
    try:
        _size = int(page_size)
    except (TypeError, ValueError):
        _size = 200
    _size = max(1, min(200, _size))
    try:
        _cap = int(max_pages)
    except (TypeError, ValueError):
        _cap = 50
    _cap = max(1, min(1000, _cap))
    now_str = _norm_now(now)

    rows = []
    pages = 0
    problems = []
    error = None
    reached_end = False
    while pages < _cap:
        try:
            payload = list_subscribers(limit=_size, now=now_str,
                                       offset=pages * _size)
        except Exception as exc:  # closed on failure：不当成「没有更多」
            error = "store_exception"
            problems.append({"reason": "store_exception", "page": pages + 1,
                             "detail": str(exc)[:200]})
            break
        if not isinstance(payload, dict):
            error = "bad_payload"
            problems.append({"reason": "bad_payload", "page": pages + 1,
                             "detail": "list_subscribers 返回 %s"
                                       % type(payload).__name__})
            break
        if payload.get("ok") is False:
            error = str(payload.get("error") or "store_unavailable")
            problems.append({"reason": "store_unavailable", "page": pages + 1,
                             "detail": error[:200]})
            break
        page_rows = payload.get("subscribers")
        if not isinstance(page_rows, (list, tuple)):
            error = "bad_payload"
            problems.append({"reason": "bad_payload", "page": pages + 1,
                             "detail": "subscribers 不是列表"})
            break
        page_rows = list(page_rows)
        rows.extend(page_rows)
        pages += 1
        if len(page_rows) < _size:  # 与 has_more 同口径：未取满即到底
            reached_end = True
            break

    truncated = error is None and not reached_end
    return {"ok": error is None, "error": error, "count": len(rows),
            "subscribers": rows, "page_size": _size, "pages": pages,
            "max_pages": _cap, "truncated": truncated,
            "complete": error is None and not truncated,
            "problems": problems}


# ---------------------------------------------------------------------------
# P3.8-lite（WO-FS-113 · M-C）离线建库入口——只追加不改写既有函数
# ---------------------------------------------------------------------------
# 背景：读路径按设计不建库（见 is_entitled 第 2 步），故账本从未初始化时
# 两个管理面只能降级。本函数给运维一个干净、幂等、可离线执行的建库入口，
# 替代「在飞书手工执行一次开通指令触发建库」的旧补救办法（旧办法会写入
# 一条假业务数据，且依赖在役 bot 可用）。
# 刻意只做「确保」：不新增 DDL、不改表结构、不删任何行。

def ensure_schema(now=None) -> dict:
    """幂等确保账本目录与 5 表存在并写入种子档位。返回结构化结果。

    返回 dict::

        {"ok": bool, "db_path": str, "created": bool,
         "tables": [已存在的表名（按 5 表口径排序）],
         "seeded_plans": int,  # plan 表中与 SEED_PLANS 的 plan_id 重合条数
         "problems": [str]}

    - 复用既有 ``_connect(create=True)`` 路径（含 _DDL 与
      _seed_plans_if_empty），**不另写一套 DDL**，避免表结构漂移。
    - ``created``：调用前库文件不存在 → True；已存在 → False。
      仅表示「本次是否新建了库文件」，不表示是否写入过任何行
      （_connect 本身幂等，已存在的库只会补缺表/补种子）。
    - ``problems`` 的 code：``missing_table:<表名>``（建表后仍缺）、
      ``ensure_failed:<异常类型>: <异常信息>``（closed on failure）。
    - closed on failure：任何异常都落 ``problems`` 且 ``ok=False``，不抛。
    - ``now`` 仅为调用侧签名对齐保留：本函数不写任何时间戳列
      （created_at 等由各自的写函数负责）。
    """
    path = _db_path()
    created = not path.exists()
    problems = []
    tables = []
    seeded = 0
    # 5 表口径就地声明（不新增模块级常量，保持本次改动只有「追加一个函数」）
    ledger_tables = (
        "subscriber", "plan", "subscription", "entitlement_log", "payment_order",
    )
    seed_ids = [p["plan_id"] for p in SEED_PLANS]

    try:
        conn = _connect(create=True)
        try:
            present = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            tables = [t for t in ledger_tables if t in present]
            for t in ledger_tables:
                if t not in present:
                    problems.append("missing_table:%s" % t)
            if "plan" in present:
                got = {
                    row[0] for row in conn.execute(
                        "SELECT plan_id FROM plan").fetchall()
                }
                seeded = len([pid for pid in seed_ids if pid in got])
        finally:
            # closed on failure：连接必须关（with 连接的上下文只管事务，
            # 不代关）；Windows 下未关的连接会锁住库文件。
            conn.close()
    except Exception as exc:  # closed on failure：落 problems，不抛
        problems.append(
            "ensure_failed:%s: %s" % (type(exc).__name__, exc))

    return {
        "ok": not problems,
        "db_path": str(path),
        "created": created,
        "tables": tables,
        "seeded_plans": seeded,
        "problems": problems,
    }
