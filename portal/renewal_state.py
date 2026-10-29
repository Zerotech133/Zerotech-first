# -*- coding: utf-8 -*-
"""订阅到期提醒 · 时区口径 + 已发去重状态（P3.8-lite · M-A 工位 2026-10-20）。

本模块收口 L-B（``renewal_reminder.py``）留下的两个口径风险：

1. **时区口径固定**（原来取宿主机本地时区，生产机时区一变 ``days_left`` 就漂）::

       resolve_tz(name=None) -> (tzinfo, meta)
       local_now(now=None, tz=None) -> datetime(naive)
       today_str(now=None, tz=None) -> "YYYY-MM-DD"

   优先级：入参 name > 环境变量 ``RENEWAL_TZ``（IANA 名，如 ``Asia/Shanghai``）
   > 缺省 ``Asia/Shanghai``。用 ``zoneinfo.ZoneInfo`` 解析；Windows 未装
   tzdata 时 ``ZoneInfoNotFoundError``（或非法时区名）**不静默退化**：

   - ``UTC`` / ``GMT`` / ``UTC+8`` / ``+08:00`` 这类固定偏移写法，退回
     ``timezone(timedelta(...))`` 且语义等价，meta 记
     ``note="zoneinfo_unavailable_fixed_offset"``；
   - 其余名字退回固定 ``+08:00``，meta 记 ``degraded=True`` +
     ``reason="zoneinfo_not_found"`` + ``note``，调用方须把 meta 交日志对账。

2. **已发标记持久化**（原来「去重与留痕由发送侧承担」，而发送侧并不存在）::

       load(path=None) -> dict
       already_sent(state, open_id, plan_id, stage, day) -> bool
       mark_sent(state, item, day, path=None) -> dict
       prune(state, keep_days=30, today=None) -> int
       filter_new(items, state, day) -> (新项列表, 已发项列表)

   纯文件 JSON，**不建表、不碰订阅账本**。默认路径
   ``portal/work/renewal_state.json``（``portal/work/`` 已入 .gitignore，
   符合本项目运行态不入库惯例），环境变量 ``RENEWAL_STATE_PATH`` 可覆盖。
   键口径：``f"{open_id}|{plan_id}|{stage}|{day}"``，``day`` 为 ``YYYY-MM-DD``
   （按上面的时区取，见 ``today_str``）。

并发模型：**单写者假设**——同一时刻只应有一个发送进程/线程写该文件；
读-改-写不加锁，多写者会互相覆盖（本波不接排程，单日单进程调用）。
坏文件一律 ``corrupt=True`` + ``problems`` 标记，``mark_sent`` 读到 corrupt
直接拒绝写，**不以空态覆盖丢数据**（closed on failure）。
"""

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
import logging

try:  # Python 3.9+ 标准库；Windows 缺 tzdata 时 ZoneInfo() 才报错，import 本身安全
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - 3.8 及以下无 zoneinfo，直接走固定偏移兜底
    ZoneInfo = None
    ZoneInfoNotFoundError = KeyError

__all__ = [
    "DEFAULT_STATE_PATH",
    "DEFAULT_TZ",
    "ENV_STATE_PATH",
    "ENV_TZ",
    "already_sent",
    "filter_new",
    "load",
    "local_now",
    "mark_sent",
    "prune",
    "resolve_tz",
    "sent_key",
    "state_path",
    "today_str",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_TZ = "Asia/Shanghai"
ENV_TZ = "RENEWAL_TZ"
ENV_STATE_PATH = "RENEWAL_STATE_PATH"

STATE_VERSION = 1
DEFAULT_KEEP_DAYS = 30

_FALLBACK_OFFSET = timedelta(hours=8)
_DAY_FMT = "%Y-%m-%d"
_TS_FMT = "%Y-%m-%d %H:%M:%S"

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_PATH = os.path.join(_MODULE_DIR, "work", "renewal_state.json")

# 同一 (名字 → 解析结果) 只解析一次；按名字缓存，测试切 env 不受影响
_TZ_CACHE = {}


# ---------------------------------------------------------------------------
# 时区口径
# ---------------------------------------------------------------------------


def _fmt_offset(td):
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rest = divmod(total, 3600)
    return "%s%02d:%02d" % (sign, hours, rest // 60)


def _fixed_offset(name):
    """解析固定偏移写法，返回 timedelta；不是固定偏移写法返回 None。

    支持 ``UTC`` / ``GMT`` / ``Z`` / ``UTC+8`` / ``GMT-3`` / ``+08:00`` /
    ``-0530``。``Asia/Shanghai`` 这类 IANA 名返回 None（不在此兜底）。
    """
    raw = (name or "").strip().upper()
    if not raw:
        return None
    if raw in ("Z", "UTC", "GMT", "ETC/UTC", "ETC/GMT", "UNIVERSAL", "ZULU"):
        return timedelta(0)
    body = raw
    for prefix in ("UTC", "GMT"):
        if body.startswith(prefix):
            body = body[len(prefix) :]
            break
    if not body or body[0] not in "+-":
        return None
    sign = 1 if body[0] == "+" else -1
    digits = body[1:].replace(":", "")
    if not digits.isdigit():
        return None
    if len(digits) <= 2:
        hours, minutes = int(digits), 0
    elif len(digits) == 3:
        hours, minutes = int(digits[:1]), int(digits[1:])
    elif len(digits) == 4:
        hours, minutes = int(digits[:2]), int(digits[2:])
    else:
        return None
    if hours > 23 or minutes > 59:
        return None
    return sign * timedelta(hours=hours, minutes=minutes)


def resolve_tz(name=None):
    """解析生效时区，返回 ``(tzinfo, meta)``；不抛异常。

    meta::

        {"name": 生效名字, "source": "arg"|"env"|"default",
         "degraded": bool, "reason": None|"zoneinfo_not_found",
         "note": None|str, "offset": "+08:00"}

    ``degraded=True`` 表示已退回固定 +08:00（与请求的时区名不等价），调用方
    必须把 meta 交日志对账，不得当作正常时区使用。
    """
    env_raw = (os.environ.get(ENV_TZ) or "").strip()
    if name is None:
        key = env_raw or DEFAULT_TZ
        source = "env" if env_raw else "default"
    else:
        key = str(name).strip() or DEFAULT_TZ
        source = "arg"

    cached = _TZ_CACHE.get(key)
    if cached is None:
        tz = None
        degraded = False
        reason = None
        note = None
        if ZoneInfo is not None:
            try:
                tz = ZoneInfo(key)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                # closed on failure：不抛，退回固定偏移并显式标记退化口径
                tz = None
        if tz is None:
            fixed = _fixed_offset(key)
            if fixed is not None:
                tz = timezone(fixed)
                note = "zoneinfo_unavailable_fixed_offset"
            else:
                tz = timezone(_FALLBACK_OFFSET)
                degraded = True
                reason = "zoneinfo_not_found"
                note = "fallback_fixed_offset:+08:00"
        meta = {
            "name": key,
            "source": source,
            "degraded": degraded,
            "reason": reason,
            "note": note,
            "offset": _fmt_offset(tz.utcoffset(datetime.now(tz))),
        }
        cached = (tz, meta)
        _TZ_CACHE[key] = cached

    tz, meta = cached
    meta = dict(meta)
    meta["source"] = source  # 同名不同来源（env / arg）不影响时区，仅影响来源标注
    return tz, meta


def local_now(now=None, tz=None):
    """归一为配置时区的 **naive 墙上时间**（与提醒判定同口径）。

    - ``None`` → 该时区的当前时刻；
    - aware datetime → 换算到该时区再去掉 tzinfo；
    - naive datetime → 原样视为该时区的墙上时间（不做二次换算）；
    - date → 当日 0 点。
    """
    tz = tz or resolve_tz()[0]
    if now is None:
        return datetime.now(tz).replace(tzinfo=None)
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            return now.astimezone(tz).replace(tzinfo=None)
        return now
    if isinstance(now, date):
        return datetime.combine(now, datetime.min.time())
    raise TypeError("now 类型不支持: %r" % (type(now).__name__,))


def today_str(now=None, tz=None):
    """当日日期串 ``YYYY-MM-DD``（按配置时区，已发去重键用这个 day）。"""
    return local_now(now, tz).strftime(_DAY_FMT)


# ---------------------------------------------------------------------------
# 已发去重状态（纯文件）
# ---------------------------------------------------------------------------


def state_path(path=None):
    """生效状态文件路径：入参 > env RENEWAL_STATE_PATH > portal/work/renewal_state.json。"""
    if path:
        return os.path.abspath(str(path))
    env_raw = (os.environ.get(ENV_STATE_PATH) or "").strip()
    if env_raw:
        return os.path.abspath(env_raw)
    return DEFAULT_STATE_PATH


def _oid_fp(open_id):
    """open_id 指纹：md5 前 16 位（不可逆、稳定、够防撞）。

    checklist 34 隐私加固（2026-10-21）：原键与条目直接落明文 open_id，
    portal/work/renewal_state.json 本机可读即等于身份明文可读。改指纹后
    去重语义不变（同客同档同阶段同天仍同键），但文件不再含身份明文。
    该文件此前从未落盘（checklist 19），故零迁移成本。
    """
    return hashlib.md5((open_id or "").encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:16]


def sent_key(open_id, plan_id, stage, day):
    """去重键：``<open_id指纹>|plan_id|stage|day``（day 为 YYYY-MM-DD）。

    指纹=md5(open_id)[:16]，不可逆；同客稳定、异客相异，去重语义与旧版
    一致。人工排查时按最后一段 day 反推，用 _oid_fp() 现算比对即可。
    """
    return "%s|%s|%s|%s" % (_oid_fp(open_id), plan_id, stage, day)


def _norm_day(day):
    """day 归一为 ``YYYY-MM-DD``；不可解析返回 None。"""
    if day is None:
        return today_str()
    if isinstance(day, datetime):
        return day.strftime(_DAY_FMT)
    if isinstance(day, date):
        return day.strftime(_DAY_FMT)
    s = str(day).strip()[:10]
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    return s


def _empty_state():
    return {"version": STATE_VERSION, "sent": {}}


def load(path=None):
    """读取已发状态，**不抛**。

    返回::

        {"ok": bool, "error": None|"state_corrupt"|"state_unreadable"|
                           "state_bad_shape",
         "path": 绝对路径, "corrupt": bool, "version": int,
         "sent": {key: {open_id, plan_id, stage, day, at}}, "problems": [...]}

    - 文件不存在 → 空结构，``ok=True``、``error=None``、``corrupt=False``；
    - 空文件 → 空结构 + ``problems=[{"reason": "state_empty"}]``（留痕）；
    - 坏 JSON / 形态不对 / 读不了 → 空结构 + ``corrupt=True`` + 错误码，
      调用方据此拒绝写（``mark_sent`` 已内置该拒绝）。
    """
    p = state_path(path)
    result = {
        "ok": True,
        "error": None,
        "path": p,
        "corrupt": False,
        "version": STATE_VERSION,
        "sent": {},
        "problems": [],
    }
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return result
    except OSError as exc:
        # closed on failure：读不了就不当空态，标记 corrupt 阻断后续覆盖写
        result.update(ok=False, error="state_unreadable", corrupt=True)
        result["problems"].append(
            {"reason": "state_unreadable", "path": p, "detail": str(exc)[:200]}
        )
        return result

    if not raw.strip():
        result["problems"].append({"reason": "state_empty", "path": p})
        return result

    try:
        data = json.loads(raw)
    except ValueError as exc:
        result.update(ok=False, error="state_corrupt", corrupt=True)
        result["problems"].append(
            {"reason": "state_corrupt", "path": p, "detail": str(exc)[:200]}
        )
        return result

    if not isinstance(data, dict) or not isinstance(data.get("sent"), dict):
        result.update(ok=False, error="state_bad_shape", corrupt=True)
        result["problems"].append(
            {
                "reason": "state_bad_shape",
                "path": p,
                "detail": "expect {'sent': {key: entry}}",
            }
        )
        return result

    sent = {}
    for key, entry in data["sent"].items():
        if isinstance(key, str) and isinstance(entry, dict):
            sent[key] = entry
        else:
            result["problems"].append(
                {"reason": "state_bad_entry", "path": p, "detail": str(key)[:80]}
            )
    result["sent"] = sent
    try:
        result["version"] = int(data.get("version") or STATE_VERSION)
    except (TypeError, ValueError):
        result["version"] = STATE_VERSION
    return result


def already_sent(state, open_id, plan_id, stage, day):
    """该 (open_id, plan_id, stage, day) 是否已标记发送。"""
    if not state:
        return False
    sent = state.get("sent")
    if not isinstance(sent, dict):
        return False
    key = sent_key(open_id, plan_id, stage, day)
    return key in sent


def _atomic_write(path, data):
    """原子写：先写 ``<path>.tmp`` 再 ``os.replace``。返回 (ok, error)。"""
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True, None
    except (OSError, TypeError, ValueError) as exc:
        # closed on failure：清掉半成品，原文件保持不动
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/renewal_state.py", exc)
        return False, str(exc)[:200]


def mark_sent(state, item, day, path=None):
    """标记一条已发并落盘（原子写）；state 就地更新。

    返回::

        {"ok": bool, "error": None|"bad_item"|"bad_day"|"state_corrupt"|
                           "write_failed",
         "key": 去重键, "path": 绝对路径, "written": bool, "problems": [...]}

    纪律：``state["corrupt"]`` 为真时**拒绝写**（坏文件不覆盖，避免丢已发记录）；
    写失败时内存里的 sent 已更新但 ``written=False``，调用方须按失败处理
    （下次重跑会重复提醒，属可见的重复而非静默漏发）。
    """
    item = item or {}
    p = state_path(path)
    open_id = str(item.get("open_id") or "")
    plan_id = item.get("plan_id")
    stage = item.get("stage")
    if not open_id or not plan_id or not stage:
        return {
            "ok": False,
            "error": "bad_item",
            "key": None,
            "path": p,
            "written": False,
            "problems": [
                {"reason": "bad_item", "path": p, "detail": "缺 open_id/plan_id/stage"}
            ],
        }
    day_str = _norm_day(day)
    if day_str is None:
        return {
            "ok": False,
            "error": "bad_day",
            "key": None,
            "path": p,
            "written": False,
            "problems": [{"reason": "bad_day", "path": p, "detail": str(day)[:40]}],
        }
    key = sent_key(open_id, plan_id, stage, day_str)
    if state is None or state.get("corrupt"):
        # closed on failure：坏状态文件只能上报，不能以空态覆盖
        return {
            "ok": False,
            "error": "state_corrupt",
            "key": key,
            "path": p,
            "written": False,
            "problems": [
                {
                    "reason": "state_corrupt",
                    "path": p,
                    "detail": "state marked corrupt, refuse to write",
                }
            ],
        }

    sent = state.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    sent[key] = {
        # checklist 34：不落明文 open_id，只留指纹（与键同源）
        "oid_fp": _oid_fp(open_id),
        "plan_id": str(plan_id),
        "stage": str(stage),
        "day": day_str,
        "at": local_now(None).strftime(_TS_FMT),
    }
    state["sent"] = sent
    state["version"] = STATE_VERSION

    written, err = _atomic_write(
        p,
        {
            "version": STATE_VERSION,
            "sent": sent,
            "updated_at": local_now(None).strftime(_TS_FMT),
        },
    )
    if not written:
        return {
            "ok": False,
            "error": "write_failed",
            "key": key,
            "path": p,
            "written": False,
            "problems": [{"reason": "write_failed", "path": p, "detail": err}],
        }
    return {
        "ok": True,
        "error": None,
        "key": key,
        "path": p,
        "written": True,
        "problems": [],
    }


def prune(state, keep_days=DEFAULT_KEEP_DAYS, today=None):
    """清掉 ``day`` 早于 (today - keep_days) 的记录，防状态文件无限增长。

    - ``keep_days`` 负数按 0 处理（只留今天）；
    - ``day`` 不可解析的老记录**保留**（宁可留痕，不凭猜删数据），不计入返回值；
    - 返回被删条数（state 就地更新，落盘由调用方决定）。
    """
    try:
        keep = int(keep_days)
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP_DAYS
    if keep < 0:
        keep = 0
    day_str = _norm_day(today) or today_str()
    try:
        cutoff = date.fromisoformat(day_str) - timedelta(days=keep)
    except ValueError:
        return 0

    sent = state.get("sent") if isinstance(state, dict) else None
    if not isinstance(sent, dict):
        return 0

    removed = 0
    for key in list(sent.keys()):
        entry = sent.get(key)
        raw_day = entry.get("day") if isinstance(entry, dict) else None
        try:
            entry_day = date.fromisoformat(str(raw_day)[:10])
        except (TypeError, ValueError):
            continue  # 认不出的保留，不凭猜删
        if entry_day < cutoff:
            sent.pop(key, None)
            removed += 1
    return removed


def filter_new(items, state, day):
    """把待提醒项按已发状态切成 ``(新项, 已发项)``。

    缺 ``open_id``/``plan_id``/``stage`` 的项无法去重，一律归入新项（宁可
    重复提醒，不静默漏发）；判定只读 state，不落盘。

    注意：``state["corrupt"]`` 为真时 sent 为空，本函数会把全部项判为新项；
    调用方须先看 ``state["corrupt"]`` 并停下人工处理，而不是拿这份切分继续发。
    """
    day_str = _norm_day(day)
    new_items = []
    sent_items = []
    for item in items or ():
        if not isinstance(item, dict):
            new_items.append(item)
            continue
        open_id = str(item.get("open_id") or "")
        plan_id = item.get("plan_id")
        stage = item.get("stage")
        if not open_id or not plan_id or not stage or day_str is None:
            new_items.append(item)
            continue
        if already_sent(state, open_id, plan_id, stage, day_str):
            sent_items.append(item)
        else:
            new_items.append(item)
    return new_items, sent_items
