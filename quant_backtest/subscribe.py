# -*- coding: utf-8 -*-
"""青萍之末 · 飞书订阅推送引擎（K245）。

设计目标（老田语音「后面可以做按键订阅，通过飞书推送到指定飞书/他人飞书」）：
    把「个人单点推送」升级为「订阅体系」——用户可自选订阅什么、推给谁。

四类订阅（kind）:
    stock_report   个股分析师报告——出报告即推（复用 K244 portal_report）
    daily_batch    每日选股批次——当日选股 + 批次回测表现
    alert_signal   预警信号触发——缠论/九维/共振等五路变化（复用 alert_watch 规则）
    quote_move     自选股异动——涨跌幅/量能超阈（复用 quote_alert 阈值）

两类触发时机（when）:
    intraday       盘中定时（30min 计划任务），只推「有变化」的
    eod            盘后汇总（15:30 计划任务），全量出一份

接收人（receivers.json）:
    名单可配（open_id / chat_id），每个订阅可指定 receiver 名单，
    为后续「他人飞书付费订阅」铺路。

状态与防轰炸:
    outbox/subscribe_state.json  记录每个订阅上次推送值与日期，
    配合 daily_cap 每日熔断（对齐 alert_watch 判例：每路每日 ≤3 推）。

用法:
    python subscribe.py --scan intraday     # 盘中扫描推变化
    python subscribe.py --scan eod          # 盘后全量汇总
    python subscribe.py --list              # 打印订阅清单（调试）
    python subscribe.py --test <sub_id>     # 试推某订阅（门户一键试推）
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
NEWS_DIR = os.path.join(STATION, "news_collector")
PORTAL_API = "http://127.0.0.1:8091"
SUB_PATH = os.path.join(HERE, "subscriptions.json")
RECV_PATH = os.path.join(HERE, "receivers.json")
STATE_PATH = os.path.join(HERE, "outbox", "subscribe_state.json")

DAILY_CAP = 3  # 每个订阅每日最多推 3 次（盘中防轰炸，对齐 alert_watch）
SUBP_BAND_PCT = 1.5  # K250 买点触发带半宽（%）：回踩到约定位 ±1.5% 视为「到位」
ALERT_URL = PORTAL_API + "/api/alerts"


# ---------------------------------------------------------------- IO


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_subs():
    """订阅清单（list）。"""
    d = _read_json(SUB_PATH, {"subscriptions": []})
    return d.get("subscriptions") or []


def load_receivers():
    """接收人名单：{name: {"type":..., "id":...}}。"""
    d = _read_json(RECV_PATH, {"receivers": []})
    out = {}
    for r in d.get("receivers") or []:
        if r.get("name"):
            out[r["name"]] = {"type": r.get("type", "open_id"), "id": r.get("id")}
    return out


def _valid_target(target):
    """仅接受显式有效的同一身份域目标，不让发送器隐式选择个人。"""
    if not isinstance(target, dict) or target.get("type") not in ("open_id", "chat_id"):
        return False
    identifier = target.get("id")
    prefix = "ou_" if target["type"] == "open_id" else "oc_"
    name = target.get("name")
    return (
        isinstance(identifier, str)
        and identifier.startswith(prefix)
        and bool(identifier[len(prefix) :].strip())
        and isinstance(name, str)
        and bool(name.strip())
        and not name.startswith("_")
    )


def _resolve_targets(sub, receivers):
    """解析全部指定收件人；任何缺失或不合法均整组失败关闭。"""
    names = sub.get("receivers")
    if names is None:
        cfg = _read_json(os.path.join(NEWS_DIR, "news_config.json"), {})
        targets = cfg.get("push_targets") if isinstance(cfg, dict) else None
    elif isinstance(names, list) and all(isinstance(n, str) for n in names):
        targets = [
            dict(name=n, **receivers[n]) if n in receivers else None for n in names
        ]
    else:
        return []
    if (
        not isinstance(targets, list)
        or not targets
        or not all(_valid_target(t) for t in targets)
        or len({t["name"] for t in targets}) != len(targets)
        or len({(t["type"], t["id"]) for t in targets}) != len(targets)
    ):
        return []
    return targets


# ---------------------------------------------------------------- 状态 / 熔断


def _load_state():
    return _read_json(STATE_PATH, {})


def _today():
    return time.strftime("%Y-%m-%d")


def _cap_ok(state, sub_id):
    """每日熔断：同订阅当日已推 >= DAILY_CAP 则跳过。"""
    st = state.get(sub_id) or {}
    if st.get("day") != _today():
        return True
    return int(st.get("count", 0)) < DAILY_CAP


def _mark_pushed(state, sub_id):
    st = state.get(sub_id) or {}
    if st.get("day") != _today():
        st = {"day": _today(), "count": 0}
    st["count"] = int(st.get("count", 0)) + 1
    st["last_ts"] = time.strftime("%Y-%m-%d %H:%M")
    state[sub_id] = st


# ---------------------------------------------------------------- 推送


def _push_card(card, targets):
    """所有消息均按目标显式发送；逐目标卡片失败才回退文本。"""
    if not targets or not all(_valid_target(t) for t in targets):
        return {"_err": "invalid_targets"}
    if len({(t["type"], t["id"]) for t in targets}) != len(targets) or len(
        {t["name"] for t in targets}
    ) != len(targets):
        return {"_err": "duplicate_targets"}
    try:
        sys.path.insert(0, NEWS_DIR)
        from send_feishu import send_content_to
    except Exception as e:
        return {"_err": "import fail %s" % str(e)[:100]}

    results = {}
    card_json = json.dumps(card, ensure_ascii=False)
    for t in targets:
        try:
            sent = send_content_to(t["id"], t["type"], card_json, "interactive")
            results[t["name"]] = sent is True
        except Exception as e:
            results[t["name"]] = False
            results["_carderr_" + t["name"]] = str(e)[:100]
        if not results[t["name"]]:
            try:
                text = json.dumps({"text": _strip_md_card(card)}, ensure_ascii=False)
                sent = send_content_to(t["id"], t["type"], text, "text")
                results["_textfb_" + t["name"]] = sent is True
            except Exception as e:
                results["_textfb_" + t["name"]] = False
                results["_texterr_" + t["name"]] = str(e)[:100]
    return results


def _delivery_ok(results, targets):
    """以原始显式目标集核验全部回执，绝不从结果键名推断收件人。"""
    if (
        not isinstance(results, dict)
        or not isinstance(targets, list)
        or not targets
        or not all(_valid_target(t) for t in targets)
        or len({t["name"] for t in targets}) != len(targets)
        or len({(t["type"], t["id"]) for t in targets}) != len(targets)
    ):
        return False
    return all(
        results.get(t["name"]) is True or results.get("_textfb_" + t["name"]) is True
        for t in targets
    )


def _strip_md_card(card):
    """卡片 → 纯文本兜底（标题 + 各 md 块去标记）。"""
    title = (card.get("header") or {}).get("title", {}).get("content", "")
    els = card.get("elements") or (card.get("body") or {}).get("elements") or []
    parts = [title]
    for e in els:
        if e.get("tag") == "markdown":
            s = e.get("content") or ""
            s = (
                s.replace("**", "")
                .replace("<font color='red'>", "")
                .replace("<font color='green'>", "")
                .replace("<font color='grey'>", "")
                .replace("</font>", "")
            )
            if s.strip():
                parts.append(s.strip())
    return "\n".join(parts)


def _portal_link(kind="subscribe_push"):
    """K-PUSH-002 A2：卡片深链 = 选股页 + 分享溯源 token。

    2026-10-25：优先 /m H5 选股页（st 归因由页面埋点回落 share_tokens.visit），
    未启用 H5 时回退旧公网 picks.html?key= 链；token 每次现签（jti 唯一）。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "portal"))
        import share_tokens
        from feishu_card_v2 import PORTAL_BASE, _load_portal_key, m_url

        tok = share_tokens.gen(kind=kind, ttl_hours=72)
        url = m_url("picks", st=tok)
        if not url:
            url = "%s/picks.html?key=%s" % (PORTAL_BASE, _load_portal_key())
            if tok:
                url += "&st=" + tok
        return url
    except Exception:
        return ""


def _build_card(title, sub, blocks, template="blue", footer=None, url_button=None):
    sys.path.insert(0, NEWS_DIR)
    import card_build

    card = card_build.build_card(
        title,
        sub,
        template,
        blocks,
        footer=footer or "AI工作站 · 订阅推送 · 不构成投资建议",
        use_schema2=True,
    )
    # K-PUSH-002 A2：2.0 卡深链按钮。坑位判例 K-STOCK-354：card_build
    # 的 buttons 参数走 v1 tag=action，2.0 卡下实测拒发——必须原生
    # button 元素 + behaviors.open_url（push_picks_public_link 实证可点）。
    # 插在尾部 hr+footer 之前（按钮在灰脚注上方）。
    if url_button:
        label, url = url_button
        els = card["body"]["elements"]
        els.insert(
            max(len(els) - 2, 0),
            {
                "tag": "button",
                "type": "primary",
                "size": "medium",
                "text": {"tag": "plain_text", "content": label},
                "behaviors": [{"type": "open_url", "default_url": url}],
            },
        )
    # 2026-10-25 P2：统一分装（失败回落旧卡）
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "portal"))
        from push_unified_adapter import adapt as _adapt

        card = _adapt(
            "subscribe",
            card,
            "subscription",
            title,
            blocks,
            ts="",
            source="量化订阅引擎",
            footer=footer or "AI工作站 · 订阅推送 · 不构成投资建议",
            buttons=[url_button] if url_button else None,
            level="info",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/subscribe.py", exc)
    return card


def _cnpct(v):
    """行情生产者 pct 已经是百分数单位，不再做比例转换。"""
    return "<font color='%s'>%+.2f%%</font>" % ("red" if v >= 0 else "green", v)


# ---------------------------------------------------------------- 订阅构造器


def _sub_stock_report(sub, mode):
    """个股分析师报告：调门户 /api/analyst-report 取报告。"""
    code = sub.get("params", {}).get("code")
    if not code:
        return None
    url = "%s/api/analyst-report?c=%s" % (PORTAL_API, code)
    try:
        with safe_urlopen(url, timeout=90) as r:
            rep = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return (
            "⚠️ 报告订阅异常",
            "生成失败 %s" % str(e)[:80],
            ["该股报告暂时无法生成，请稍后重试。"],
            "orange",
        )
    if not rep.get("ok"):
        return None
    name = rep.get("name") or code
    md = rep.get("md") or ""
    # 拆分 md 段（## 标题）为多块
    blocks = _split_md_blocks(md)
    title = "📄 %s 分析师报告" % name
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 青萍之末分析师"
    return (title, sub2, blocks, "blue")


def _split_md_blocks(md):
    """按 ## 段切块，控制飞书单块长度。"""
    lines = (md or "").split("\n")
    blocks, cur = [], []
    for ln in lines:
        if ln.startswith("## ") and cur:
            blocks.append("\n".join(cur).strip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append("\n".join(cur).strip())
    blocks = [b for b in blocks if b]
    # 单块超 1800 字则合并裁剪（飞书卡片单块上限约 2000）
    out = []
    for b in blocks:
        if len(b) > 1800:
            b = b[:1800] + "\n…（详见门户）"
        out.append(b)
    return out or ["（报告为空）"]


def _sub_daily_batch(sub, mode):
    """每日选股批次：读 rps_daily_batches.jsonl 最新 + rps_batch_backtest 表现。"""
    rep_dir = os.path.join(HERE, "reports")
    jl = os.path.join(rep_dir, "rps_daily_batches.jsonl")
    if not os.path.exists(jl):
        return None
    try:
        lines = [l for l in open(jl, encoding="utf-8").read().strip().split("\n") if l]
        latest = json.loads(lines[-1])
    except Exception:
        return None
    # intraday 模式：只有日期变化才推
    if mode == "intraday" and sub.get("_last_date") == latest.get("date"):
        return None
    date = latest.get("date", "")
    dstr = "%s-%s-%s" % (date[:4], date[4:6], date[6:8]) if len(date) == 8 else date
    env = latest.get("env") or {}
    picks = latest.get("picks") or []
    buy = [p for p in picks if "买入" in (p.get("action") or "")]
    obs = [p for p in picks if "观察" in (p.get("action") or "")]
    blocks = []
    blocks.append(
        "**📅 批次日期** %s · 源 %s\n**🌡 环境** %s"
        % (dstr, latest.get("source", "—"), env.get("state", "—"))
    )
    if buy:
        bl = ["**结构偏多观察（%d）**" % len(buy)]
        for p in buy[:10]:
            bl.append(
                "%s %s  score %s"
                % (p.get("code", ""), p.get("name", ""), p.get("score", ""))
            )
        blocks.append("\n".join(bl))
    if obs:
        bl = ["**👀 观察（%d）**" % len(obs)]
        for p in obs[:10]:
            bl.append(
                "%s %s  score %s"
                % (p.get("code", ""), p.get("name", ""), p.get("score", ""))
            )
        blocks.append("\n".join(bl))
    if not picks:
        blocks.append("**（当日选股为空——环境闸门收紧）**")
    # 批次回测表现（最近批次）
    perf = _batch_perf_line(date)
    if perf:
        blocks.append(perf)
    title = "🎯 每日选股 %s" % dstr
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 共 %d 只" % len(picks)
    return (title, sub2, blocks, "turquoise")


def _batch_perf_line(date):
    """最近批次回测表现（5/10/20 日超额 + K251 逐日明细）。"""
    p = os.path.join(HERE, "reports", "rps_batch_backtest.json")
    d = _read_json(p, {})
    for b in d.get("batches") or []:
        if b.get("date") == date:
            hs = b.get("holds") or {}
            parts = []
            for h in ("5", "10", "20"):
                hd = hs.get(h)
                if hd and hd.get("excess") is not None:
                    ex = float(hd["excess"])
                    parts.append(
                        "持%s日 %s"
                        % (
                            h,
                            (
                                "<font color='%s'>%+.2f%%</font>"
                                % ("red" if ex >= 0 else "green", ex * 100)
                            ),
                        )
                    )
            if not parts:
                return None
            line = "**📊 批次后验（至今/已满期）** " + " · ".join(parts)
            # K251 逐日盯市：每天的收益不打包，全展开（最多 20 日）
            dl = b.get("daily") or {}
            ks, days, cums = (
                dl.get("k") or [],
                dl.get("day") or [],
                dl.get("cum") or [],
            )
            if ks and days:
                seg = " ".join(
                    "D%s%s"
                    % (
                        k,
                        (
                            "<font color='%s'>%+.2f</font>"
                            % ("red" if v >= 0 else "green", v * 100)
                        )
                        if v is not None
                        else "—",
                    )
                    for k, v in list(zip(ks, days))[:20]
                )
                last = cums[-1] if cums else None
                tail = (
                    ""
                    if last is None
                    else " ｜ 累计净 <font color='%s'>%+.2f%%</font>%s"
                    % (
                        "red" if last >= 0 else "green",
                        last * 100,
                        "（至今）" if dl.get("partial") else "",
                    )
                )
                line += "\n**📅 逐日（当日收益%%，T+1 开盘进场起）**\n%s%s" % (
                    seg,
                    tail,
                )
            return line
    return None


_ALERT_SOURCES = {
    "盘前倾向": "premarket",
    "缠论信号": "chanlun",
    "九维警报池": "nine_dim",
    "弱于板块在案": "weak",
    "共振做多": "cross",
    "🏛 投委会": "council",
}
_CST = timezone(timedelta(hours=8))


def _fresh_time(value, minutes=15):
    """仅接受带时区且落在当前近窗内的生产时间，不能以本机文件时间代替。"""
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = datetime.now(_CST) - stamp
        return stamp.utcoffset() is not None and timedelta(0) <= age <= timedelta(
            minutes=minutes
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _sub_alert_signal(sub, mode):
    """只展示有可信来源的聚合速览变化，不将卡片计数称作买卖点。"""
    try:
        with safe_urlopen(ALERT_URL, timeout=90) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(res, dict):
        return None
    health = res.get("source_health")
    if (
        not isinstance(health, dict)
        or type(health.get("version")) is not int
        or health["version"] != 1
        or not _fresh_time(health.get("checked_at"), minutes=5)
    ):
        return None
    sources = health.get("sources")
    alerts = res.get("alerts")
    if not isinstance(sources, dict) or not isinstance(alerts, list):
        return None
    # 聚合卡只报告六源速览；缺任一核心源时无法宣称完整变化。
    for source_name in ("premarket", "chanlun", "nine_dim", "weak", "cross"):
        source = sources.get(source_name)
        if (
            not isinstance(source, dict)
            or source.get("status") not in ("ok", "empty")
            or source.get("reason") is not None
            or not _fresh_time(source.get("as_of"), minutes=5)
        ):
            return None
    trusted = []
    for a in alerts:
        if not isinstance(a, dict):
            return None
        nm, val = a.get("name"), a.get("value")
        source_name = _ALERT_SOURCES.get(nm)
        source = sources.get(source_name)
        if isinstance(source, dict) and source.get("status") == "empty":
            # 完成的零结果源与非零展示卡矛盾：整包拒绝，不能只删矛盾卡。
            zero_pattern = {
                "chanlun": r"\s*买\s*0\s*·\s*卖\s*0\s*",
                "nine_dim": r"\s*0\s*只\s*",
                "weak": r"\s*0\s*案\s*",
                "cross": r"\s*0\s*只\s*",
            }.get(source_name)
            if source_name == "premarket":
                if (
                    source.get("reason") is not None
                    or not isinstance(val, str)
                    or val.strip() not in ("", "—", "-", "暂无")
                ):
                    return None
            elif (
                zero_pattern is None
                or not isinstance(val, str)
                or re.fullmatch(zero_pattern, val) is None
            ):
                return None
            continue
        if (
            not isinstance(nm, str)
            or not isinstance(source, dict)
            or source.get("status") != "ok"
            or source.get("reason") is not None
            or not _fresh_time(source.get("as_of"), minutes=5)
            or not isinstance(val, str)
            or not val.strip()
        ):
            continue
        trusted.append(a)
    if not trusted:
        return None
    state = sub.get("_watch_state") or {}
    if not isinstance(state, dict):
        state = {}
    changed = [a for a in trusted if state.get(a["name"]) != a["value"]]
    if mode == "intraday" and not changed:
        return None
    shown = changed if mode == "intraday" else trusted
    blocks = []
    for a in shown[:12]:
        ln = "**%s** %s —— %s" % (a["name"], a["value"], a.get("sub") or "")
        d = a.get("delta")
        if isinstance(d, int) and not isinstance(d, bool):
            ln += " <font color='%s'>（较前日 %s%d）</font>" % (
                "red" if d >= 0 else "green",
                "+" if d >= 0 else "",
                d,
            )
        blocks.append(ln)
    sub["_alerts_new"] = {a["name"]: a["value"] for a in trusted}
    title = "⚡ 预警速览" + ("变化" if mode == "intraday" else "汇总")
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 已验证来源的展示卡变化，非盘中买卖点"
    return (title, sub2, blocks, "orange")


def _quote_time(value):
    """行情生产者以 CST 无时区分钟字符串落盘；只接受当前近窗。"""
    try:
        stamp = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=_CST)
        age = datetime.now(_CST) - stamp
        # 分钟取整的本分钟合法（秒级正龄）；下一个整分钟必为未来。
        return timedelta(0) <= age <= timedelta(minutes=15)
    except (TypeError, ValueError):
        return False


def _sub_quote_move(sub, mode):
    """依赖健康的 quotes_status.items 实测涨跌幅；冷却状态不是行情。"""
    st = _read_json(os.path.join(NEWS_DIR, "quotes_status.json"), None)
    if (
        not isinstance(st, dict)
        or st.get("ok") is not True
        or not _quote_time(st.get("updated"))
        or not isinstance(st.get("items"), list)
        or type(st.get("count")) is not int
        or st["count"] != len(st["items"])
    ):
        return None
    threshold = st.get("alert_threshold")
    if (
        type(threshold) not in (int, float)
        or not math.isfinite(threshold)
        or threshold <= 0
    ):
        return None
    hits = []
    for item in st["items"]:
        if not isinstance(item, dict):
            return None
        code, pct = item.get("code"), item.get("pct")
        # 港美行情无源事件时间，生产者会用本机 updated 回填 ts；整包拒绝。
        # A 股缺原生时间时生产者亦可能回填，消费端无法辨别，尚不可生产放行。
        if (
            not isinstance(code, str)
            or re.fullmatch(r"(?:sh|sz|bj)\d{6}", code) is None
            or not _quote_time(item.get("ts"))
        ):
            return None
        if pct is None:
            continue
        if type(pct) not in (int, float) or not math.isfinite(pct) or abs(pct) > 25.0:
            return None
        if abs(pct) >= threshold:
            hits.append({"code": code, "pct": float(pct)})
    if not hits:
        return None
    fp = ",".join(sorted("%s:%.2f" % (h["code"], h["pct"]) for h in hits))
    if mode == "intraday" and sub.get("_last_fp") == fp:
        return None
    sub["_fp_new"] = fp
    blocks = ["**📈 A 股自选行情涨跌幅达到 %.1f%%（%d 只）**" % (threshold, len(hits))]
    for h in hits[:12]:
        blocks.append("%s %s" % (h["code"], _cnpct(h["pct"])))
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · A 股快照涨跌幅，非独立买卖点"
    return ("📈 A 股自选行情变动", sub2, blocks, "purple")


def _sub_buy_point(sub, mode):
    """暂无可信盘中逐笔与回踩确认契约，买卖触发不可判定，不发送。"""
    return None


_BUILDERS = {
    "stock_report": _sub_stock_report,
    "daily_batch": _sub_daily_batch,
    "alert_signal": _sub_alert_signal,
    "quote_move": _sub_quote_move,
    "buy_point": _sub_buy_point,
}


# ---------------------------------------------------------------- 主流程


def run_scan(mode, only_sub=None, force=False):
    """扫描全部启用的订阅，触发即推。返回 {sub_id: (ok, targets_res)}。"""
    subs = load_subs()
    receivers = load_receivers()
    state = _load_state()
    # 记录 intraday 去重所需的上下文
    for s in subs:
        sid = s.get("id")
        st = state.get(sid) or {}
        s["_last_date"] = st.get("last_date")
        s["_last_fp"] = st.get("last_fp")
        s["_watch_state"] = st.get("watch_state")

    out = {}
    for s in subs:
        if only_sub and s.get("id") != only_sub:
            continue
        # force（--test 试推）忽略 enabled/when 限制，便于门户试推未启用项
        if not force and not s.get("enabled", True):
            continue
        when = s.get("when") or ["intraday", "eod"]
        if mode not in when and not force:
            continue
        sid = s.get("id")
        if not force and not _cap_ok(state, sid):
            out[sid] = ("capped", {})
            continue
        kind = s.get("kind")
        builder = _BUILDERS.get(kind)
        if not builder:
            out[sid] = ("unknown_kind:%s" % kind, {})
            continue
        try:
            built = builder(s, mode)
        except Exception as e:
            out[sid] = ("build_err:%s" % str(e)[:120], {})
            continue
        if not built:
            out[sid] = ("no_change", {})
            continue
        title, sub2, blocks, tpl = built
        try:
            # K-PUSH-002 A2：订阅卡统一挂「看板直达」深链按钮（带码+st）
            link = _portal_link("subscribe_push")
            card = _build_card(
                title,
                sub2,
                blocks,
                template=tpl,
                url_button=("看板直达", link) if link else None,
            )
        except Exception as e:
            out[sid] = ("card_err:%s" % str(e)[:120], {})
            continue
        targets = _resolve_targets(s, receivers)
        if not targets:
            out[sid] = ("invalid_targets", {})
            continue
        # 配额生效（仅非 internal 套餐；internal 永不拦）
        try:
            sys.path.insert(0, STATION)  # K-STOCK-401：push_flow 在仓库根，
            # 此前缺该行 → ModuleNotFoundError 被吞 → 配额检查静默旁路
            import push_flow as _pf

            _plan, _q, _sent, _blk = _pf.quota_check()
        except Exception:
            _blk = False
        if _blk:
            out[sid] = ("quota_block", {})
            try:
                _pf.record(
                    kind="quota_block:subscribe:%s" % kind,
                    channel="feishu",
                    target=",".join(t["name"] for t in targets),
                    count=1,
                    ok=False,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/subscribe.py", exc)
            continue
        tres = _push_card(card, targets)
        ok = _delivery_ok(tres, targets)
        out[sid] = ("ok" if ok else "fail", tres)
        # 统一发送流水（商业化计费依据；fail-safe，不影响主链路）
        try:
            sys.path.insert(0, STATION)
            import push_flow

            push_flow.record(
                kind="subscribe:%s" % kind,
                channel="feishu",
                target=",".join(t["name"] for t in targets),
                count=1,
                ok=ok,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/subscribe.py", exc)
        # 更新状态（K-STOCK-401：仅成功才落去重指纹——此前失败也写
        # last_date/last_fp，下轮去重误判「已推过」→ 当日漏推）
        st = state.get(sid) or {}
        if ok:
            _mark_pushed(state, sid)
            st = state.get(sid)
            st["last_date"] = _extract_date(blocks)
            st["last_fp"] = s.get("_fp_new") or _extract_fp(kind, blocks)
            if kind == "alert_signal" and s.get("_alerts_new") is not None:
                st["watch_state"] = s["_alerts_new"]
        state[sid] = st
    _atomic(STATE_PATH, state)
    return out


def _extract_date(blocks):
    for b in blocks:
        if "批次日期" in b:
            for tok in b.replace("·", " ").split():
                if len(tok) == 10 and tok[4] == "-":
                    return tok
    return time.strftime("%Y-%m-%d")


def _extract_fp(kind, blocks):
    if kind == "quote_move":
        return "\n".join(blocks)[:300]
    return ""


def main():
    args = sys.argv[1:]
    mode = "intraday"
    if "--eod" in args or "--scan" in args and "eod" in args:
        mode = "eod"
    if "--eod" in args:
        mode = "eod"
    if "--list" in args:
        for s in load_subs():
            print(
                "%s  kind=%s  when=%s  enabled=%s  recv=%s"
                % (
                    s.get("id"),
                    s.get("kind"),
                    s.get("when"),
                    s.get("enabled"),
                    s.get("receivers"),
                )
            )
        return 0
    only = None
    force = False
    if "--test" in args:
        i = args.index("--test")
        only = args[i + 1] if i + 1 < len(args) else None
        force = True
    if "--intraday" in args:
        mode = "intraday"
    res = run_scan(mode, only_sub=only, force=force)
    ok_n = sum(1 for v in res.values() if v[0] == "ok")
    print(
        "SUBSCRIBE_%s mode=%s subs=%d ok=%d  %s"
        % (
            "OK" if ok_n else "IDLE",
            mode,
            len(res),
            ok_n,
            json.dumps({k: v[0] for k, v in res.items()}, ensure_ascii=False),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
