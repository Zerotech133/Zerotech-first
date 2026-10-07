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
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
ALERTS_URL = "http://127.0.0.1:8091/api/alerts"
MORNING_KEEP = {"缠论信号", "九维警报池"}
IDX_CODES = [("sh000001", "上证"), ("sz399001", "深成"), ("sz399006", "创业板")]


def fetch_alerts():
    """通道 1：门户常驻进程聚合（cross_poll 60s 缓存已热）。"""
    with urllib.request.urlopen(ALERTS_URL, timeout=90) as r:
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
        with open(os.path.join(HERE, "news_config.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("cfg read err %s" % str(e)[:100])  # C2批2 吞错落盘（配置损坏会静默改推送目标）
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
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Referer": "https://finance.sina.com.cn"}, timeout=12)
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
                out[c] = {"name": f[0], "pct": round((cur - prev) / prev * 100, 2),
                          "amount_yi": amt / 1e8}
        return out or None
    except Exception:
        # 豁免（C2批2）：docstring 声明——大盘层缺项不阻断五路推送（fetch_index 失败静默返回 None）
        return None


def _cnpct(v):
    """A 股着色：涨红跌绿（门户 quant.html 口径）。"""
    return ("<font color='%s'>%+.2f%%</font>"
            % ("red" if v >= 0 else "green", v))


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
        parts.append("两市成交 %s 亿" % format(round(sh["amount_yi"] + sz["amount_yi"]), ","))
    if not parts:
        return None
    return "**🌏 大盘** " + " · ".join(parts)


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
            a.get("name", "—"), a.get("value", "—"), a.get("sub") or "")
        d = a.get("delta")
        if isinstance(d, int):
            ln += " <font color='%s'>（较前日 %s%d）</font>" % (
                "red" if d >= 0 else "green", "+" if d >= 0 else "", d)
        lines.append(ln)
    blocks.append("\n".join(lines) or "（暂无预警数据）")
    return blocks


def push_card_multi(card, qr_portal=None, targets=None):
    """多目标推送：首目标（个人）走 card_build 完整通道（二维码+回退），
    其余目标 OpenAPI 直连；返回 {目标名: ok}。"""
    import card_build
    from send_feishu import send_content_to
    targets = targets or [{"name": "个人", "type": "open_id", "id": None}]
    results = {}
    primary = targets[0]
    results[primary["name"]] = card_build.push_card(card, qr_portal=qr_portal)
    card_json = json.dumps(card, ensure_ascii=False)
    for t in targets[1:]:
        try:
            results[t["name"]] = send_content_to(
                t["id"], t["type"], card_json, "interactive")
        except Exception:
            results[t["name"]] = False
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
            results[t["name"]] = send_content_to(
                t["id"], t["type"], content, "text")
        except Exception:
            results[t["name"]] = False
    return results


def _strip_md(s):
    return (s.replace("**", "")
             .replace("<font color='red'>", "").replace("<font color='green'>", "")
             .replace("</font>", ""))


def _save_last(ok, morning, res, targets_res):
    """状态落盘（/intel 飞书卡数据源；E1 起原子写防半截 JSON）。"""
    try:
        fp = os.path.join(HERE, "outbox", "alert_push_last.json")
        from alert_io import atomic_write_json
        atomic_write_json(fp, {"ts": time.strftime("%Y-%m-%d %H:%M"),
                               "mode": "morning" if morning else "eod",
                               "ok": bool(ok),
                               "n": len(res.get("alerts") or []),
                               "targets": targets_res})
    except Exception as e:
        print("save_last err %s" % str(e)[:100])  # C2批2 吞错落盘（状态落盘失败 /intel 卡数据源将 stale）


def main():
    morning = "--morning" in sys.argv
    try:
        res = fetch_alerts()
    except Exception as e:
        print("portal unreachable (%s), fallback build_alerts"
              % str(e)[:100])
        res = _fallback_alerts()
    if not res or not res.get("alerts"):
        print("ALERT_PUSH_FAIL no alerts data")
        _save_last(False, morning, {"alerts": []}, {})
        return 1

    import card_build
    cfg = _cfg()
    targets = _targets(cfg)
    now = time.strftime("%Y-%m-%d %H:%M")
    blocks = build_blocks(res, morning, fetch_index())

    # K42-c：不做内网接入——卡内不放下钻按钮/门户二维码（外网飞书点不开）
    if morning:
        title, sub = "⚠️ 预警速览 · 晨", now + " · 当日新扫描（缠论+九维）"
    else:
        title, sub = "⚠️ 预警日报 · 盘后", now + " · 大盘 + 五路预警全量"

    card = card_build.build_card(
        title, sub, "orange", blocks,
        footer="AI工作站 · 飞书预警输出 · 不构成投资建议",
        use_schema2=True)
    try:
        tres = push_card_multi(card, targets=targets)
    except Exception as e:
        print("card err %s" % str(e)[:120])
        tres = {t["name"]: False for t in targets}
    if not any(tres.values()):  # 卡全军覆没 → 文本兜底
        try:
            txt = title + " " + sub + "\n" + "\n\n".join(
                _strip_md(b) for b in blocks)
            tres = push_text_multi(txt, targets)
        except Exception as e:
            print("text fallback err %s" % str(e)[:120])

    ok = any(tres.values())
    _save_last(ok, morning, res, tres)
    print("ALERT_PUSH_%s mode=%s alerts=%d targets=%s" % (
        "OK" if ok else "FAIL",
        "morning" if morning else "eod", len(res.get("alerts") or []),
        json.dumps(tres, ensure_ascii=False)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
