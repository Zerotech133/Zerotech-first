# -*- coding: utf-8 -*-
"""订阅到期前提醒 · 纯函数能力（飞书中台 P3.7-lite · L-B 工位 2026-10-20）。

问题：当前只有 ``expire_sweep`` 的自然到期（一到点即失权），没有任何到期前
提醒，客户会在毫无预兆的情况下失去盯盘 / 预警 / 新闻推送能力。

本模块只交付"算谁该被提醒"与"提醒正文"两件纯函数，**零 IO**：
  - 不读盘、不连库、不发网络、不写排程、不真发消息；
  - 入参由调用方从只读契约 ``subscription_store.list_subscribers()`` 取得；
    本模块只 import 其状态字面量（``STATUS_ACTIVE``），不碰账本写路径。

对外函数::

    due_for_reminder(subscribers, now=None, lead_days=(3, 1)) -> list[dict]
        待提醒项，每项 {open_id, display_name, plan_id, end, days_left, stage}
    plan_reminders(subscribers, now=None, lead_days=(3, 1)) -> dict
        同上 + 跳过计数 / 问题清单（对账用；due_for_reminder 是它的 items 视图）
    build_reminder_text(item) -> str
        提醒正文（不含任何金额、不含明文 open_id、不含购买链接）
    mask_open_id(open_id) -> str
        open_id 掩码（正文与日志统一口径）

判定口径（逐条写死，测试钉住）：
  - 只处理 ``status == "active"`` 且尚未过期的行；已过期由 ``expire_sweep``
    收口，不再提醒；其它状态一律跳过；
  - ``days_left`` 按**日历日**计算：(end 的日期 - now 的日期).days；
    end 为纯日期（"2026-11-19"）时按当日 23:59:59 视为有效，避免整天误判过期；
    end 为带时刻 / 带时区串时先归一到**固定时区**（默认 ``Asia/Shanghai``，
    可用 env ``RENEWAL_TZ`` 切换，见 ``renewal_state.resolve_tz``），再按上述
    日历日取整；时区名解析失败退回固定 +08:00 并在 ``report["tz"]`` /
    ``report["warnings"]`` 里显式标退化（不静默）；
  - ``days_left`` 命中 ``lead_days`` 某档 → ``stage = "t<days_left>"``（t3 / t1）；
  - 同一 ``(open_id, plan_id)`` 同一 stage 只出现一次（同一天重复行去重）；
  - 解析失败**显式跳过并计数**（``skipped["bad_end"]`` + ``problems`` 明细），
    绝不静默把整批当空——本项目有 fail-open 禁令，这里宁可报问题不装绿。

取数与去重（P3.8-lite 收口；本波不接线，排程须老田五要素批准后另批）::

    import subscription_store as store
    import renewal_state as state_mod
    from renewal_reminder import (collect_subscribers, plan_reminders,
                                 build_reminder_text)

    store.expire_sweep()                        # 1. 自然到期唯一出口
    fetched = collect_subscribers(store=store)  # 2. 拉全集（默认 200，截断有警告）
    report = plan_reminders(fetched)            # 3. 算谁该被提醒
    today = state_mod.today_str()               # 4. 与判定同口径的 day
    sent_state = state_mod.load()
    todo, done = state_mod.filter_new(report["items"], sent_state, today)
    for item in todo:                           # 5. 逐条交发送侧
        text = build_reminder_text(item)
        # send(kind="subscription_expiring", text=text)  # 需另批接线 + 真发授权
        state_mod.mark_sent(sent_state, item, today)  # 6. 发后落已发标记
    state_mod.prune(sent_state)                 # 7. 清老记录（默认留 30 天）
    # report["skipped"] / report["problems"] / fetched["warnings"] /
    # report["tz"] 必须交日志对账，不得丢弃

  stage 是日粒度，故「每日任务在 expire_sweep 之后调用一次」即可（先清过期
  再算待提醒，避免提醒到刚被清扫的行）。已发标记的持久化在
  ``renewal_state.py``（文件型，键含 day），同一天重跑不会重复提醒同批人。
"""

from datetime import date, datetime, time

from renewal_state import local_now, resolve_tz
from subscription_store import STATUS_ACTIVE

__all__ = [
    "collect_subscribers",
    "due_for_reminder",
    "plan_reminders",
    "build_reminder_text",
    "mask_open_id",
]

_DAY_END = time(23, 59, 59)
_TS_FMT = "%Y-%m-%d %H:%M:%S"
# subscription_store.list_subscribers 自身把 limit 钳在 [1,200]，超过这个数
# 再传也拿不到更多行——本模块据此判定「已到上限，仍可能截断」。
_STORE_LIMIT_MAX = 200


# ---------------------------------------------------------------------------
# 入参归一 / 解析
# ---------------------------------------------------------------------------

def _as_rows(subscribers):
    """归一入参为 (行列表, 错误码)。

    兼容两种形态：``list_subscribers()["subscribers"]`` 列表，或整个返回
    dict（``{"ok": True, "count": N, "subscribers": [...]}``）。
    store 不可用（ok=False）或形态不可识别时返回空列表 + 诚实错误码，
    **不伪装成"今天没人该提醒"**。
    """
    if isinstance(subscribers, dict):
        if subscribers.get("ok") is False:
            return [], "store_unavailable"
        rows = subscribers.get("subscribers")
    elif isinstance(subscribers, (list, tuple)):
        rows = subscribers
    else:
        return [], "bad_input"
    if not isinstance(rows, (list, tuple)):
        return [], "bad_input"
    return list(rows), None


def _parse_ts(value, tz=None):
    """解析时间值为 (无时区 datetime, 是否纯日期)；解析失败返回 (None, False)。

    接受 datetime/date 对象与 ISO 串（"2026-11-19"、"2026-11-19 10:00:00"、
    "2026-11-19T10:00:00"、带 ``Z`` 或 ``+08:00`` 偏移）。带时区的先转
    ``tz``（默认 ``resolve_tz()``，即 RENEWAL_TZ 或 Asia/Shanghai）再去掉
    tzinfo，保证与 now 同口径比较——不再取宿主机本地时区。
    """
    date_only = False
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time(0, 0, 0))
        date_only = True
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None, False
        if "T" not in s and " " not in s:
            date_only = True
        if s.endswith("Z") or s.endswith("z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None, False
        if date_only:
            dt = datetime.combine(dt.date(), time(0, 0, 0))
    else:
        return None, False
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz or resolve_tz()[0]).replace(tzinfo=None)
    return dt, date_only


def _coerce_now(now, tz=None):
    """now 归一：None→该时区当前时间；date→当日 0 点；str→解析。

    传入 naive datetime 时视为该时区的墙上时间（不做二次换算），与 L-B
    在 +08 宿主机上的行为一致；aware datetime 换算到该时区。
    """
    tz = tz or resolve_tz()[0]
    if now is None:
        return local_now(None, tz)
    if isinstance(now, (datetime, date)):
        return local_now(now, tz)
    dt, _ = _parse_ts(now, tz)
    if dt is None:
        raise ValueError("now 无法解析: %r" % (now,))
    return dt


def _normalize_lead_days(lead_days):
    """档位归一：转 int、去负值、去重、降序（t3 先于 t1 输出）。"""
    seen = set()
    for d in lead_days or ():
        try:
            v = int(d)
        except (TypeError, ValueError):
            continue  # 非法档位单独丢弃，不影响其余档位
        if v >= 0:
            seen.add(v)
    return tuple(sorted(seen, reverse=True))


def _fmt_end(raw, end_dt):
    """end 字段输出：字符串原样（保真），对象则格式化。"""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return end_dt.strftime(_TS_FMT)


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------

def mask_open_id(open_id):
    """open_id 掩码：前 3 + ``***`` + 后 3；6 位以内整体打码。"""
    s = str(open_id or "")
    if len(s) <= 6:
        return "***" if s else ""
    return s[:3] + "***" + s[-3:]


def collect_subscribers(store=None, limit=200, now=None):
    """拉取订阅者全集，**对「可能截断」与「取数失败」都显式上报**。

    返回::

        {"ok": bool, "error": None|"store_unavailable"|"store_exception"|
                           "bad_payload",
         "limit": 生效 limit, "count": 实际行数,
         "subscribers": [...], "warnings": [...], "problems": [...]}

    口径：
      - 默认 ``limit=200``（``list_subscribers`` 自身钳 [1,200]，传更大也只
        到 200，故按 ``min(limit, 200)`` 生效并给 ``limit_clamped:*`` 警告）；
      - **返回行数达到生效 limit ⇒ ``warnings`` 加 ``may_be_truncated:<limit>``**，
        绝不假装拿全了——行数恰好等于上限时无法区分"刚好这么多"与"被截断"；
      - store 返回 ``ok=False`` / 抛异常 / 形态不可识别 ⇒ ``ok=False`` + 错误码
        + ``problems``，**不当成空列表**（fail-open 禁令）；调用方可直接把它
        喂给 ``plan_reminders()``，后者同样会报 ``store_unavailable``；
      - ``store`` 可注入（测试用假 store），默认 ``import subscription_store``。
    """
    warnings = []
    problems = []

    try:
        eff = int(limit)
    except (TypeError, ValueError):
        eff = _STORE_LIMIT_MAX
        warnings.append("bad_limit:%r" % (limit,))
    if eff < 1:
        eff = 1
        warnings.append("bad_limit_clamped:1")
    if eff > _STORE_LIMIT_MAX:
        warnings.append("limit_clamped:%d->%d" % (eff, _STORE_LIMIT_MAX))
        eff = _STORE_LIMIT_MAX

    if store is None:
        import subscription_store as store

    kwargs = {"limit": eff}
    if now is not None:
        kwargs["now"] = now
    try:
        payload = store.list_subscribers(**kwargs)
    except Exception as exc:  # closed on failure：不抛，返回带 problems 的降级结构
        problems.append({"reason": "store_exception",
                         "detail": str(exc)[:200]})
        return {"ok": False, "error": "store_exception", "limit": eff,
                "count": 0, "subscribers": [], "warnings": warnings,
                "problems": problems}

    if not isinstance(payload, dict):
        problems.append({"reason": "bad_payload",
                         "detail": "list_subscribers 返回 %s"
                                   % type(payload).__name__})
        return {"ok": False, "error": "bad_payload", "limit": eff,
                "count": 0, "subscribers": [], "warnings": warnings,
                "problems": problems}

    if payload.get("ok") is False:
        err = str(payload.get("error") or "store_unavailable")
        problems.append({"reason": "store_unavailable", "detail": err[:200]})
        return {"ok": False, "error": err, "limit": eff, "count": 0,
                "subscribers": [], "warnings": warnings,
                "problems": problems}

    rows = payload.get("subscribers")
    if not isinstance(rows, (list, tuple)):
        problems.append({"reason": "bad_payload",
                         "detail": "subscribers 不是列表"})
        return {"ok": False, "error": "bad_payload", "limit": eff,
                "count": 0, "subscribers": [], "warnings": warnings,
                "problems": problems}

    rows = list(rows)
    if len(rows) >= eff:
        warnings.append("may_be_truncated:%d" % eff)
    return {"ok": True, "error": None, "limit": eff, "count": len(rows),
            "subscribers": rows, "warnings": warnings, "problems": problems}


def plan_reminders(subscribers, now=None, lead_days=(3, 1)):
    """扫描一批订阅行，产出待提醒项与对账信息（纯函数，零 IO）。

    返回::

        {"ok": True, "error": None, "now": "YYYY-MM-DD HH:MM:SS",
         "lead_days": [3, 1], "count": n,
         "items": [{open_id, display_name, plan_id, end, days_left, stage}],
         "skipped": {not_active, expired, no_end, bad_end, not_due,
                     duplicate, no_identity, bad_row},
         "tz": {name, source, degraded, reason, note, offset},
         "warnings": [...], "problems": [{row, open_id(掩码), plan_id, end, reason}]}

    items 按 (days_left, open_id, plan_id) 稳定排序；skip 计数与 items 之和
    等于输入行数，任何行都不会凭空消失（解析失败走 bad_end + problems）。
    ``tz.degraded`` 为真（zoneinfo 缺失/时区名非法退回固定 +08:00）时，
    ``warnings`` 会带 ``tz_degraded:<name>``，须交日志对账。
    """
    tz, tz_meta = resolve_tz()
    now_dt = _coerce_now(now, tz)
    stages = _normalize_lead_days(lead_days)
    stage_of = {d: "t%d" % d for d in stages}
    rows, input_error = _as_rows(subscribers)
    warnings = []
    if tz_meta.get("degraded"):
        warnings.append("tz_degraded:%s" % tz_meta.get("name"))

    skipped = {"not_active": 0, "expired": 0, "no_end": 0, "bad_end": 0,
               "not_due": 0, "duplicate": 0, "no_identity": 0, "bad_row": 0}
    problems = []
    items = []
    seen = set()

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            skipped["bad_row"] += 1
            problems.append({"row": index, "open_id": "", "plan_id": "",
                             "end": "", "reason": "bad_row"})
            continue

        open_id = str(row.get("open_id") or "")
        plan_id = row.get("plan_id")
        if not open_id or not plan_id:
            skipped["no_identity"] += 1
            problems.append({"row": index, "open_id": mask_open_id(open_id),
                             "plan_id": str(plan_id or ""),
                             "end": str(row.get("end") or ""),
                             "reason": "no_identity"})
            continue

        if row.get("status") != STATUS_ACTIVE:
            skipped["not_active"] += 1
            continue

        raw_end = row.get("end")
        if raw_end is None or raw_end == "":
            skipped["no_end"] += 1
            problems.append({"row": index, "open_id": mask_open_id(open_id),
                             "plan_id": str(plan_id), "end": "",
                             "reason": "no_end"})
            continue

        end_dt, date_only = _parse_ts(raw_end, tz)
        if end_dt is None:
            # closed on failure：该行显式跳过并计数，不牵连同批其它行
            skipped["bad_end"] += 1
            problems.append({"row": index, "open_id": mask_open_id(open_id),
                             "plan_id": str(plan_id),
                             "end": str(raw_end)[:40], "reason": "bad_end"})
            continue
        if date_only:
            end_dt = datetime.combine(end_dt.date(), _DAY_END)

        if end_dt < now_dt:
            skipped["expired"] += 1
            continue

        days_left = (end_dt.date() - now_dt.date()).days
        stage = stage_of.get(days_left)
        if stage is None:
            skipped["not_due"] += 1
            continue

        key = (open_id, plan_id, stage)
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)

        items.append({
            "open_id": open_id,
            "display_name": str(row.get("display_name") or ""),
            "plan_id": plan_id,
            "end": _fmt_end(raw_end, end_dt),
            "days_left": days_left,
            "stage": stage,
        })

    items.sort(key=lambda it: (it["days_left"], it["open_id"],
                               str(it["plan_id"])))
    return {
        "ok": input_error is None,
        "error": input_error,
        "now": now_dt.strftime(_TS_FMT),
        "lead_days": list(stages),
        "count": len(items),
        "items": items,
        "skipped": skipped,
        "tz": tz_meta,
        "warnings": warnings,
        "problems": problems,
    }


def due_for_reminder(subscribers, now=None, lead_days=(3, 1)):
    """待提醒项列表（``plan_reminders()["items"]`` 的便捷视图）。

    需要跳过计数 / 问题明细对账时改用 ``plan_reminders()``，否则解析失败
    的行只体现为该行"没出现在结果里"，与"本就不该提醒"不可区分。
    """
    return plan_reminders(subscribers, now=now,
                          lead_days=lead_days)["items"]


def build_reminder_text(item):
    """生成提醒正文：剩余天数 + 续费/联系开通引导。

    纪律：不含任何金额（档位定价未定）、不含明文 open_id（统一掩码）、
    不含购买链接（M3 收银台未上线）。文案按 lark_md 友好写，纯文本可读。
    """
    item = item or {}
    plan_id = item.get("plan_id") or "（未标注档位）"
    end = item.get("end") or "（到期日未知）"
    days_left = item.get("days_left")
    masked = mask_open_id(item.get("open_id"))

    if isinstance(days_left, int) and days_left >= 0:
        left = "剩余 **%d 天**（%s 到期）" % (days_left, end)
    else:
        left = "即将到期（%s）" % end

    lines = [
        "⏳ 订阅即将到期",
        "",
        "你的订阅（档位：%s）%s。" % (plan_id, left),
        "到期后，**盯盘 / 预警 / 新闻推送**等订阅能力会自动停止，"
        "不会再另行通知。",
        "",
        "如需继续使用，请**续费**或**联系管理员开通**，避免服务中断。",
    ]
    if masked:
        lines.extend(["", "账号：%s" % masked])
    return "\n".join(lines)
