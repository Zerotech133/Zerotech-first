# -*- coding: utf-8 -*-
"""飞书预警推送（K42/K42-b）：量化域五路预警 + 大盘层，定时定量推飞书。

模式:
    python alert_push.py             盘后全量五路（16:05 计划任务）
    python alert_push.py --morning   晨推缠论+九维（7:10 计划任务，当日新扫描）

数据源: 门户 /api/alerts（与量化域首页「今日预警速览」同源同口径）；
         门户不可达时兜底 import portal/portal_api.build_alerts()。
大盘层: 新浪三大指数（上证/深成/创业板）+ 两市成交额——失败静默缺项。
通道:    news_config.json push_targets 多目标（个人=card_build 完整通道
         含二维码/lark-cli 回退；群=OpenAPI 直连 send_content_to），
         卡失败降纯文本（对齐 daily_report 判例）。
着色:    A 股习惯红涨绿跌（与门户 quant.html 预警速览一致）。
状态:    outbox/alert_push_last.json（/intel 飞书卡显示上次推送结果）。
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import logging
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
ALERTS_URL = "http://127.0.0.1:8091/api/alerts"
MORNING_KEEP = {"缠论信号", "九维警报池"}
SOURCE_NAMES = {
    "premarket": "盘前倾向",
    "chanlun": "缠论信号",
    "nine_dim": "九维警报池",
    "weak": "弱于板块在案",
    "cross": "共振做多",
    "council": "🏛 投委会",
}
MORNING_SOURCES = ("chanlun", "nine_dim")
EOD_SOURCES = ("premarket", "chanlun", "nine_dim", "weak", "cross")
CORE_SOURCES = ("premarket", "chanlun", "nine_dim")
SOURCE_ERROR_REASONS = {
    "missing",
    "invalid_schema",
    "future_anchor",
    "unproven_completion",
    "invalid_anchor",
    "stale_anchor",
    "invalid_json",
    "upstream_blocked",
    "upstream_degraded",
    "source_error",
}
SHANGHAI = timezone(timedelta(hours=8))

# 2026-10-26 EXEC-R35：交易日历口径（周末/节后开盘前，EOD 源天然只有上一交易
# 日批次，服务端会标 stale_anchor；若消费端坚持"as_of 必须当天"，AlertWatch
# 每个周末+周一上午都会按设计拒跑 60h+，健康卡 ops.alert_watch_liveness 长期黄。
# 这里只放宽"可接受的业务日期 = 今天 ∪ 上一交易日"；business_date 更早（真断更）
# 仍然 stale，不会被洗绿。日历缺失时退回 weekday 口径，绝不阻断告警链。
try:
    import trading_cal as _tcal
except Exception:  # noqa: BLE001
    _tcal = None


def _prev_weekday(day):
    probe = day
    while probe.weekday() >= 5:
        probe -= timedelta(days=1)
    return probe


def _acceptable_source_days(now):
    """当期可接受业务日期集合：今天 + 上一交易日（R35，见上）。"""
    days = {now.date()}
    prev = now.date() - timedelta(days=1)
    try:
        days.add(
            _tcal.last_trading_day(prev) if _tcal is not None else _prev_weekday(prev)
        )
    except Exception:  # noqa: BLE001
        days.add(_prev_weekday(prev))
    return days


def _parse_day(value):
    """business_date 字符串 → date；非法/缺失返回 None（不判死，交给原逻辑）。"""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# 新鲜窗口：源的完成时间戳距今多久内算"当期"。
# 2026-10-26 OS-FIX-ALERT2：默认 5 → **1080 分钟（18 小时）**，与
# portal/api_common.py 的 ALERT_FRESH_WINDOW 对齐（服务端已先合入 18h）。
# 口径 = "当日 EOD 批次即当期"：覆盖 06:50（nine_dim）到收盘后 EOD 全量的真实
# 节奏；跨日由 validated_alerts 的 parsed.date() != now.date() 排除，不会把
# 昨天的批次当成今天。消费端只用它校验 checked_at（响应时刻）新鲜度，对源只
# 要求 as_of 当日，因此与服务端放宽后的口径兼容。
# 环境变量 ALERT_FRESH_WINDOW_MIN 保留覆盖（运维可临时收紧/放宽）；两处必须用
# 同一个环境变量，否则客户端与服务端互相对不上。
ALERT_FRESH_WINDOW = timedelta(
    minutes=float(os.environ.get("ALERT_FRESH_WINDOW_MIN", "1080"))
)
IDX_CODES = [("sh000001", "上证"), ("sz399001", "深成"), ("sz399006", "创业板")]


def fetch_alerts():
    """通道 1：门户常驻进程聚合（cross_poll 60s 缓存已热）。"""
    with safe_urlopen(ALERTS_URL, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def _fallback_alerts():
    """通道 2：门户挂了也不漏推——直接 import 同一函数读落盘快照。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "portal"))
        import portal_api

        return portal_api.build_alerts()
    except Exception as e:
        print("fallback build_alerts err %s" % str(e)[:100])  # C2批2 吞错落盘
        return None


def _cfg():
    try:
        with open(os.path.join(HERE, "news_config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(
            "cfg read err %s" % str(e)[:100]
        )  # C2批2 吞错落盘（配置损坏会静默改推送目标）
        return {}


def _targets(cfg):
    """推送目标（config push_targets；缺省仅个人走原通道）。"""
    ts = cfg.get("push_targets") or []
    return ts if ts else [{"name": "个人", "type": "open_id", "id": None}]


def fetch_index():
    """新浪三大指数 + 两市成交额（与 quote_snapshot 同接口同解析）。
    失败静默返回 None——大盘层缺项不阻断五路推送。"""
    try:
        import requests

        r = requests.get(
            "https://hq.sinajs.cn/list=" + ",".join(c for c, _ in IDX_CODES),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://finance.sina.com.cn",
            },
            timeout=12,
        )
        r.raise_for_status()
        r.encoding = "gbk"
        out = {}
        for line in r.text.strip().splitlines():
            if '="' not in line:
                continue
            c = line.split("hq_str_", 1)[-1].split("=", 1)[0].strip()
            f = line.split('="', 1)[-1].rstrip('";').split(",")
            if len(f) < 10:
                continue
            try:
                prev, cur, amt = float(f[2]), float(f[3]), float(f[9])
            except ValueError:
                continue  # 豁免（C2批2）：行情行数值坏段跳过，正常容错
            if cur > 0 and prev > 0:
                out[c] = {
                    "name": f[0],
                    "pct": round((cur - prev) / prev * 100, 2),
                    "amount_yi": amt / 1e8,
                }
        return out or None
    except Exception:
        # 豁免（C2批2）：docstring 声明——大盘层缺项不阻断五路推送（fetch_index 失败静默返回 None）
        return None


def _cnpct(v):
    """A 股着色：涨红跌绿（门户 quant.html 口径）。"""
    return "<font color='%s'>%+.2f%%</font>" % ("red" if v >= 0 else "green", v)


def market_block(idx):
    """大盘层 md 块：三大指数 + 两市成交额；无数据返回 None。"""
    if not idx:
        return None
    parts = []
    for c, label in IDX_CODES:
        d = idx.get(c)
        if d:
            parts.append("%s %s" % (label, _cnpct(d["pct"])))
    sh, sz = idx.get("sh000001"), idx.get("sz399001")
    if sh and sz:
        parts.append(
            "两市成交 %s 亿" % format(round(sh["amount_yi"] + sz["amount_yi"]), ",")
        )
    if not parts:
        return None
    return "**🌏 大盘** " + " · ".join(parts)


def _unified_alert(legacy, title, blocks, now, level="warn"):
    """2026-10-25 P2：预警卡统一分装（失败回落旧卡）。"""
    try:
        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"
            ),
        )
        from push_unified_adapter import adapt as _adapt
        from feishu_card_v2 import m_url as _m

        _u = _m("entry")
        _btn = [("📱 移动端总览（H5）", _u, "primary")] if _u else None
        return _adapt(
            "alert",
            legacy,
            "alert",
            title,
            blocks,
            ts=now,
            source="预警引擎",
            footer="AI工作站 · 飞书预警输出 · 不构成投资建议",
            buttons=_btn,
            level=level,
        )
    except Exception:
        return legacy


def build_blocks(res, morning, idx):
    """大盘层（首块）+ 五路预警块（delta 红涨绿跌）。"""
    blocks = []
    mb = market_block(idx)
    if mb:
        blocks.append(mb)
    lines = []
    for a in res.get("alerts") or []:
        if morning and a.get("name") not in MORNING_KEEP:
            continue
        ln = "**%s** %s —— %s" % (
            a.get("name", "—"),
            a.get("value", "—"),
            a.get("sub") or "",
        )
        d = a.get("delta")
        if isinstance(d, int):
            ln += " <font color='%s'>（较前日 %s%d）</font>" % (
                "red" if d >= 0 else "green",
                "+" if d >= 0 else "",
                d,
            )
        lines.append(ln)
    blocks.append("\n".join(lines) or "（暂无预警数据）")
    return blocks


def push_card_multi(card, qr_portal=None, targets=None, kind="daily:alert"):
    """多目标推送：首目标（个人）走 card_build 完整通道（二维码+回退），
    其余目标 OpenAPI 直连；返回 {目标名: ok}。
    kind：统一发送流水分类（daily:alert 预警 / daily:report 统一日报）。"""
    import card_build
    from send_feishu import send_content_to

    targets = targets or [{"name": "个人", "type": "open_id", "id": None}]
    results = {}
    primary = targets[0]
    results[primary["name"]] = card_build.push_card(
        card, qr_portal=qr_portal, kind=kind
    )
    card_json = json.dumps(card, ensure_ascii=False)
    for t in targets[1:]:
        try:
            results[t["name"]] = send_content_to(
                t["id"], t["type"], card_json, "interactive", kind=kind
            )
        except Exception:
            results[t["name"]] = False
    # 统一发送流水（商业化计费依据；fail-safe）
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import push_flow

        push_flow.record(
            kind=kind,
            channel="feishu",
            target=",".join(t["name"] for t in targets),
            count=1,
            ok=any(v for k, v in results.items() if not k.startswith("_")),
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/alert_push.py", exc)
    return results


def push_text_multi(txt, targets=None):
    """文本兜底多目标（个人 send_text 完整通道；群 OpenAPI text）。"""
    from send_feishu import send_text, send_content_to

    targets = targets or [{"name": "个人", "type": "open_id", "id": None}]
    results = {}
    results[targets[0]["name"]] = send_text(txt)
    content = json.dumps({"text": txt}, ensure_ascii=False)
    for t in targets[1:]:
        try:
            results[t["name"]] = send_content_to(t["id"], t["type"], content, "text")
        except Exception:
            results[t["name"]] = False
    return results


def _strip_md(s):
    return (
        s.replace("**", "")
        .replace("<font color='red'>", "")
        .replace("<font color='green'>", "")
        .replace("</font>", "")
    )


def _save_last(ok, morning, res, targets_res, status=None):
    """状态落盘（/intel 飞书卡数据源；E1 起原子写防半截 JSON）。"""
    try:
        fp = os.path.join(HERE, "outbox", "alert_push_last.json")
        from alert_io import atomic_write_json

        atomic_write_json(
            fp,
            {
                "ts": time.strftime("%Y-%m-%d %H:%M"),
                "mode": "morning" if morning else "eod",
                "ok": bool(ok),
                "status": status or ("sent" if ok else "failed"),
                "n": len(res.get("alerts") or []),
                "targets": targets_res,
            },
        )
    except Exception as e:
        print(
            "save_last err %s" % str(e)[:100]
        )  # C2批2 吞错落盘（状态落盘失败 /intel 卡数据源将 stale）


def _parse_source_time(value):
    """Only explicit China-local source timestamps can establish current coverage."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00", value
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() != timedelta(hours=8):
        return None
    return parsed.astimezone(SHANGHAI)


def validated_alerts(res, morning=False, include_baseline=False):
    """Return (safe alerts, unhealthy routes), or None for untrusted coverage.

    A fresh HTTP response does not refresh an underlying source. In the absence
    of a per-source scheduling contract, previous-day source data is not current.
    """
    if (
        not isinstance(res, dict)
        or not isinstance(res.get("alerts"), list)
        or any(not isinstance(a, dict) for a in res["alerts"])
    ):
        return None
    health = res.get("source_health")
    if (
        not isinstance(health, dict)
        or type(health.get("version")) is not int
        or health["version"] != 1
    ):
        return None
    checked = _parse_source_time(health.get("checked_at"))
    now = datetime.now(SHANGHAI)
    if checked is None or not timedelta(0) <= now - checked <= ALERT_FRESH_WINDOW:
        return None
    sources = health.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_NAMES):
        return None
    outdated = set()
    reconciled = set()  # EXEC-R35：交易日历复核通过的"上一交易日批次"源
    acceptable_days = _acceptable_source_days(now)
    for source in SOURCE_NAMES:
        item = sources[source]
        if not isinstance(item, dict) or item.get("status") not in (
            "ok",
            "empty",
            "error",
            "stale",
        ):
            return None
        reason = item.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", reason)
        ):
            return None
        status = item["status"]
        if (status in ("ok", "empty") and reason is not None) or (
            status in ("error", "stale") and reason not in SOURCE_ERROR_REASONS
        ):
            return None
        source_time = item.get("as_of")
        parsed = _parse_source_time(source_time) if source_time is not None else None
        if source_time is not None and parsed is None:
            return None
        biz = _parse_day(item.get("business_date"))
        if status in ("ok", "empty") and (
            parsed is None
            or (biz is not None and (biz not in acceptable_days or parsed.date() < biz))
            or (biz is None and parsed.date() not in acceptable_days)
            or parsed > checked + ALERT_FRESH_WINDOW
        ):
            # checked_at is observation time, never a substitute for as_of.
            # 2026-10-26 EXEC-R36：与 portal/api_common.dated() 统一口径——
            # business_date ∈ {今天, 上一交易日} 且 as_of 不早于该业务日、不超前
            # 即当期；不再要求 as_of 自身日期也落在当期集合（否则周末/跨夜完成的
            # EOD 批次会被下游二次判 outdated，把服务端已判 ok 的核心源丢掉）。
            outdated.add(source)
            continue
        # EXEC-R35：服务端口径"business_date 必须=今天"，周末/节后开盘前会把
        # 上一交易日批次判 stale_anchor。消费端按交易日历复核：business_date 在
        # {今天, 上一交易日} 且 as_of 不早于该业务日、不超前 → 视为当期。
        # business_date 更早（真断更）不在此列，仍由 bad 路径拒跑。
        if status == "stale" and reason in ("stale_anchor", "unproven_completion"):
            if (
                biz is not None
                and biz in acceptable_days
                and parsed is not None
                and parsed.date() >= biz
                and parsed <= now + ALERT_FRESH_WINDOW
            ):
                reconciled.add(source)
    scope = MORNING_SOURCES if morning else EOD_SOURCES
    good = {
        source
        for source in scope
        if (sources[source]["status"] in ("ok", "empty") and source not in outdated)
        or source in reconciled
    }
    # Weak/cross are UI summaries; without a direct core feed they cannot
    # establish actionable partial coverage even if their status says ok.
    if not good or (not morning and not good.intersection(CORE_SOURCES)):
        return None
    bad = tuple(source for source in scope if source not in good)
    filtered = []
    seen = set()
    for alert in res["alerts"]:
        name = alert.get("name")
        if not isinstance(name, str) or name not in SOURCE_NAMES.values():
            return None  # Unknown schema must not silently certify a normal empty day.
        source = next(key for key, label in SOURCE_NAMES.items() if label == name)
        if source == "council":
            continue  # Optional council UI summary is not an actionable route.
        if name in seen:
            return None
        seen.add(name)
        if source not in good:
            continue  # Unhealthy source may expose a stale, misleading zero card.
        if sources[source]["status"] == "empty":
            # A completed empty source cannot simultaneously report nonzero
            # results. Reject the entire envelope rather than converting this
            # contradiction into a misleading normal empty-day report.
            value = alert.get("value")
            empty_pattern = {
                "nine_dim": r"\s*0\s*只\s*",
                "weak": r"\s*0\s*案\s*",
                "cross": r"\s*0\s*只\s*",
                "chanlun": r"\s*买\s*0\s*·\s*卖\s*0\s*",
            }.get(source)
            if source == "premarket":
                if not isinstance(value, str) or value.strip() not in (
                    "",
                    "—",
                    "-",
                    "暂无",
                ):
                    return None
            elif (
                empty_pattern is None
                or not isinstance(value, str)
                or re.fullmatch(empty_pattern, value) is None
            ):
                return None
            if include_baseline and source == "nine_dim":
                filtered.append(alert)  # Trustworthy zero is a watch baseline.
            continue  # Empty source UI zero cards are not actionable signals.
        if source in ("weak", "cross", "nine_dim") and re.fullmatch(
            r"\s*0\s*(只|案)\s*", str(alert.get("value", ""))
        ):
            if include_baseline and source == "nine_dim":
                filtered.append(alert)
            continue  # A zero-count summary is not a positive actionable signal.
        if source == "chanlun" and re.fullmatch(
            r"\s*买\s*0\s*·\s*卖\s*0\s*", str(alert.get("value", ""))
        ):
            continue
        if source == "premarket" and str(alert.get("value", "")).strip() in (
            "",
            "—",
            "-",
            "暂无",
            "未知",
        ):
            continue
        if not isinstance(alert.get("value"), str):
            return None
        filtered.append(alert)
    return {"alerts": filtered, "source_health": health}, bad


def main():
    morning = "--morning" in sys.argv
    try:
        res = fetch_alerts()
    except Exception as e:
        print("portal unreachable (%s), fallback build_alerts" % str(e)[:100])
        res = None
    validated = validated_alerts(res, morning)
    if validated is None:
        res = _fallback_alerts()
        validated = validated_alerts(res, morning)
    if validated is None:
        # 不把来源全故障表述为「今日暂无预警，数据链路正常」。
        _save_last(False, morning, {"alerts": []}, {}, status="source_unavailable")
        print(
            "ALERT_PUSH_SOURCE_UNAVAILABLE mode=%s" % ("morning" if morning else "eod")
        )
        return 1
    res, bad = validated
    if bad and not res["alerts"]:
        # A partially available empty response cannot prove a normal empty day.
        _save_last(False, morning, res, {}, status="source_unavailable")
        print("ALERT_PUSH_SOURCE_UNAVAILABLE partial=%s" % ",".join(bad))
        return 1
    if not res["alerts"]:
        # K-STOCK-098 #1 归因修复：晨扫/盘后「无预警数据」不是推送失败——
        # 原实现直接 return 1，计划任务记 rc=1 且无人知晓（静默失败假象）。
        # 改为发一张「今日暂无预警」信息卡（ok=True）；只有这张卡也发不
        # 出去（通道故障）才 return 1 交 alert_fail_wrap 兜底告警。
        print("ALERT_PUSH_EMPTY no alerts data -> info card")
        import card_build

        cfg = _cfg()
        targets = _targets(cfg)
        now = time.strftime("%Y-%m-%d %H:%M")
        if morning:
            ititle, isub = "⚠️ 预警速览 · 晨", now + " · 当日新扫描暂无缠论/九维预警"
        else:
            ititle, isub = "⚠️ 预警日报 · 盘后", now + " · 五路预警今日均为空"
        empty_res = {"alerts": [], "empty": True}
        blocks = build_blocks(empty_res, morning, fetch_index())
        card = card_build.build_card3(  # K-PUSH-008：空报走 v3 绿域例行
            "daily",
            ititle,
            isub,
            blocks,
            footer="AI工作站 · 飞书预警输出 · 不构成投资建议",
        )
        card = _unified_alert(card, ititle, blocks, now, level="info")
        try:
            tres = push_card_multi(card, targets=targets)
        except Exception as e:
            print("card err %s" % str(e)[:120])
            tres = {t["name"]: False for t in targets}
        if not any(tres.values()):  # 卡全军覆没 → 文本兜底
            try:
                txt = ititle + " " + isub + "\n今日暂无预警，数据链路正常。"
                tres = push_text_multi(txt, targets)
            except Exception as e:
                print("text fallback err %s" % str(e)[:120])
        ok = any(tres.values())
        _save_last(ok, morning, empty_res, tres, status="sent" if ok else "failed")
        print(
            "ALERT_PUSH_%s mode=%s status=%s alerts=0 targets=%s"
            % (
                "OK" if ok else "FAIL",
                "morning" if morning else "eod",
                "sent" if ok else "failed",
                json.dumps(tres, ensure_ascii=False),
            )
        )
        return 0 if ok else 1

    import card_build

    cfg = _cfg()
    targets = _targets(cfg)
    now = time.strftime("%Y-%m-%d %H:%M")
    blocks = build_blocks(res, morning, fetch_index())
    coverage = (
        (
            "覆盖不足：%s 源不可用；仅展示已验证源，不代表全量。"
            % "、".join(SOURCE_NAMES[source] for source in bad)
        )
        if bad
        else ""
    )
    if coverage:
        blocks.insert(0, "**⚠️ " + coverage + "**")

    # K42-c：不做内网接入——卡内不放下钻按钮/门户二维码（外网飞书点不开）
    if morning:
        title, sub = "⚠️ 预警速览 · 晨", now + " · 当日新扫描（缠论+九维）"
    else:
        title, sub = "⚠️ 预警日报 · 盘后", now + " · 大盘 + 五路预警全量"

    if coverage:
        sub += " · " + coverage
    card = card_build.build_card3(  # K-PUSH-008：全量报走 v3 红域预警
        "alert", title, sub, blocks, footer="AI工作站 · 飞书预警输出 · 不构成投资建议"
    )
    card = _unified_alert(card, title, blocks, now, level="bad")
    try:
        tres = push_card_multi(card, targets=targets)
    except Exception as e:
        print("card err %s" % str(e)[:120])
        tres = {t["name"]: False for t in targets}
    if not any(tres.values()):  # 卡全军覆没 → 文本兜底
        try:
            txt = title + " " + sub + "\n" + "\n\n".join(_strip_md(b) for b in blocks)
            tres = push_text_multi(txt, targets)
        except Exception as e:
            print("text fallback err %s" % str(e)[:120])

    ok = any(tres.values())
    _save_last(ok, morning, res, tres, status="sent" if ok else "failed")
    print(
        "ALERT_PUSH_%s mode=%s status=%s alerts=%d targets=%s"
        % (
            "OK" if ok else "FAIL",
            "morning" if morning else "eod",
            "sent" if ok else "failed",
            len(res.get("alerts") or []),
            json.dumps(tres, ensure_ascii=False),
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
