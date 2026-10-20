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
    end 为带时刻 / 带时区串时先归一到本机时区，再按上述日历日取整；
  - ``days_left`` 命中 ``lead_days`` 某档 → ``stage = "t<days_left>"``（t3 / t1）；
  - 同一 ``(open_id, plan_id)`` 同一 stage 只出现一次（同一天重复行去重）；
  - 解析失败**显式跳过并计数**（``skipped["bad_end"]`` + ``problems`` 明细），
    绝不静默把整批当空——本项目有 fail-open 禁令，这里宁可报问题不装绿。

调用入口建议（本波不接线；排程须老田五要素批准后另批）::

    import subscription_store as store
    from renewal_reminder import plan_reminders, build_reminder_text

    store.expire_sweep()                               # 1. 自然到期唯一出口
    report = plan_reminders(store.list_subscribers(limit=200))
    for item in report["items"]:                       # 2. 逐条交发送侧
        text = build_reminder_text(item)
        # send(kind="subscription_expiring", text=text)  # 需另批接线 + 真发授权
    # report["skipped"] / report["problems"] 必须交日志对账，不得丢弃

  stage 是日粒度，故「每日任务在 expire_sweep 之后调用一次」即可（先清过期
  再算待提醒，避免提醒到刚被清扫的行）。已发标记的持久化不在本模块，
  重跑同一天会重复产出同批 items，去重与留痕由发送侧承担（见工单风险项）。
"""

from datetime import date, datetime, time

from subscription_store import STATUS_ACTIVE

__all__ = [
    "due_for_reminder",
    "plan_reminders",
    "build_reminder_text",
    "mask_open_id",
]

_DAY_END = time(23, 59, 59)
_TS_FMT = "%Y-%m-%d %H:%M:%S"


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


def _parse_ts(value):
    """解析时间值为 (无时区 datetime, 是否纯日期)；解析失败返回 (None, False)。

    接受 datetime/date 对象与 ISO 串（"2026-11-19"、"2026-11-19 10:00:00"、
    "2026-11-19T10:00:00"、带 ``Z`` 或 ``+08:00`` 偏移）。带时区的先转本机
    时区再去掉 tzinfo，保证与 now 同口径比较。
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
        dt = dt.astimezone().replace(tzinfo=None)
    return dt, date_only


def _coerce_now(now):
    """now 归一：None→本机当前时间；date→当日 0 点；str→解析。"""
    if now is None:
        return datetime.now()
    if isinstance(now, datetime):
        return now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    if isinstance(now, date):
        return datetime.combine(now, time(0, 0, 0))
    dt, _ = _parse_ts(now)
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


def plan_reminders(subscribers, now=None, lead_days=(3, 1)):
    """扫描一批订阅行，产出待提醒项与对账信息（纯函数，零 IO）。

    返回::

        {"ok": True, "error": None, "now": "YYYY-MM-DD HH:MM:SS",
         "lead_days": [3, 1], "count": n,
         "items": [{open_id, display_name, plan_id, end, days_left, stage}],
         "skipped": {not_active, expired, no_end, bad_end, not_due,
                     duplicate, no_identity, bad_row},
         "problems": [{row, open_id(掩码), plan_id, end, reason}]}

    items 按 (days_left, open_id, plan_id) 稳定排序；skip 计数与 items 之和
    等于输入行数，任何行都不会凭空消失（解析失败走 bad_end + problems）。
    """
    now_dt = _coerce_now(now)
    stages = _normalize_lead_days(lead_days)
    stage_of = {d: "t%d" % d for d in stages}
    rows, input_error = _as_rows(subscribers)

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

        end_dt, date_only = _parse_ts(raw_end)
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
