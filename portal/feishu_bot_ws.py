# -*- coding: utf-8 -*-
"""飞书下钻机器人（L1）· WebSocket 长连接，无需公网 IP。

指令（P2P 给机器人发消息，或群里 @）：
  缠论 605100            → 多层折叠主卡（判定/中枢/情报/应对/附录）
  详情 605100 筹码        → 单维度视图卡
  选股 [T1|T2|全部]       → 今日选股清单卡（默认 T1）
  帮助                    → 指令表
卡片按钮（card.action.trigger）：value 形如 dim:chips:605100.SH → 回发对应维度卡。

驻进程保活：SYSTEM 计划任务 FeishuBotWatchdog 每 5 分钟拉起（ZhiceWatchdog 同配方）。
日志：portal/logs/feishu_bot.log（长跑落文件铁律）。
前提：飞书开放平台该应用「事件与回调→接收方式」启用长连接；订阅
  im.message.receive_v1 与 card.action.trigger 两个事件。
"""
import json
import os
import sys
import threading
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))

LOG = os.path.join(HERE, "logs", "feishu_bot.log")
PICKS = os.path.join(HERE, "data", "todays_picks.json")
DDIR = os.path.join(HERE, "data", "pick_details")
DIMS = ("chanlun", "chips", "sectors", "notices", "lhb", "vol_events",
        "resonance", "no_buy_reasons")

_log_lock = threading.Lock()


def log(msg):
    line = "%s %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with _log_lock:
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    print(line, flush=True)


# ── L2 引擎接合 ─────────────────────────────────────────────
def _portal_shim():
    import portal_api
    import portal_report

    class _P:
        _chanlun_sl_tp = staticmethod(portal_api.Handler._chanlun_sl_tp)
        _stock_chip_summary = staticmethod(portal_api._stock_chip_summary)
        _stock_sector_rows = staticmethod(portal_api._stock_sector_rows)

    return _P(), portal_report


def get_facts(code):
    """当日事实包优先（秒回），否则现抓（~66s）。"""
    fp = os.path.join(DDIR, "%s.json" % code)
    if os.path.exists(fp):
        try:
            old = json.load(open(fp, encoding="utf-8"))
            if (old.get("ts") or "").startswith(
                    datetime.now().strftime("%Y-%m-%d")):
                return old, True
        except Exception:  # noqa: BLE001
            pass
    portal, pr = _portal_shim()
    return pr.collect_facts(portal, code), False


def md_chanlun(f):
    cl = f.get("chanlun") or {}
    if not cl:
        return "无缠论中枢记录"
    lines = ["**日线中枢** %s~%s（%s）" % (cl.get("dzd"), cl.get("dzg"),
                                          cl.get("pivot_state")),
             "**30m 中枢** %s~%s" % (cl.get("mzd"), cl.get("mzg")),
             "**操作** %s" % cl.get("plan"),
             "止损 %s（%s%%）· tp1 %s · tp2 %s" % (
                 cl.get("stop"), cl.get("stop_pct"), cl.get("tp1"),
                 cl.get("tp2"))]
    rows = [json.loads(l) for l in open(
        os.path.join(os.path.dirname(HERE), "quant_backtest", "data",
                     "chanlun_pool.jsonl"), encoding="utf-8") if l.strip()]
    hist = [r for r in rows if r.get("code") == f.get("code")]
    if hist:
        lines.append("**池内历史信号**：%s" % "；".join(
            "%s %s@%s" % (r.get("dt"), r.get("type"), r.get("price"))
            for r in hist[-3:]))
    return "\n".join(lines)


def md_intel(f):
    ve = f.get("vol_events") or []
    lines = []
    if ve:
        lines.append("**量能事件**：" + "；".join(
            "%s %s ×%s（%s%%）" % (e.get("date"), e.get("tag"),
                                  e.get("mult"), e.get("pct"))
            for e in ve[-3:]))
    ns = f.get("notices") or []
    if ns:
        lines.append("**近端公告**：" + "；".join(
            "%s %s" % (n.get("date"), (n.get("title") or "").replace(
                "华丰股份:", "").replace("华丰动力股份有限公司", ""))[:38]
            for n in ns[:3]))
    lines.append("**龙虎榜**：%s" % ("近 130 天无上榜记录" if not f.get("lhb")
                                    else "有上榜（见门户）"))
    links = f.get("notice_vol_links") or []
    if links:
        lines.append("**公告-量能联动**：%d 组" % len(links))
    return "\n".join(lines) or "各情报源近端安静"


def md_play(f):
    nb = f.get("no_buy_reasons") or {}
    out = ["**判定** %s" % nb.get("verdict", "—")]
    for r in (nb.get("reasons") or [])[:3]:
        out.append("· **%s** %s" % (r.get("dim"), r.get("why")))
        if r.get("cur"):
            out.append("  当前：%s → 需要：%s" % (r.get("cur"),
                                                 r.get("need")))
    out.append("**三情形**：① 上沿冲高回落等回踩 ② 回踩缩量企稳=三买雏形 "
               "③ 站稳回踩不破=三买候选")
    return "\n".join(out)


def card_main(code, facts, cached):
    from feishu_card_v2 import build_drill_card
    secs = [("📈 缠论中枢与操作方案", md_chanlun(facts)),
            ("🔍 四源情报（公告/资金/量能/新闻）", md_intel(facts)),
            ("🎯 决策链与三情形应对", md_play(facts)),
            ("🗂 数据附录", "事实包 ts=%s（%s）\n维度：quote/chanlun/chips/"
              "sectors/notices/lhb/vol_events/no_buy_reasons"
              % (facts.get("ts"), "缓存秒回" if cached else "现抓 66s"))]
    extra = "" if cached else " · 现抓"
    return build_drill_card(code, facts, secs, title_extra="缠论×情报" + extra)


def card_dim(code, facts, dim):
    from feishu_card_v2 import build_drill_card
    v = facts.get(dim)
    md = json.dumps(v, ensure_ascii=False, indent=1)[:1800] if v \
        else "该维度暂无数据"
    secs = [("📋 %s 原始视图" % dim, md)]
    return build_drill_card(code, facts, secs, title_extra=dim)


def card_picks(tier):
    from feishu_card_v2 import build_drill_card
    doc = json.load(open(PICKS, encoding="utf-8"))
    rows = doc.get("stocks") or []
    if tier in ("T1", "T2"):
        rows = [r for r in rows if r["tier"] == tier]
    rows = rows[:40]
    md = "\n".join("%s %s ×%d %s" % (
        r["code"], r["name"], r["n_hit"],
        "/".join(sorted({s["s"] for s in r["sources"]} - {"cross"})))
        for r in rows)
    f = {"name": "今日选股 %s" % (tier or "全部"),
         "no_buy_reasons": {"verdict": "清单视图"},
         "quote": {"px": None},
         "chanlun": {}, "ts": doc.get("generated_at")}
    secs = [("🎯 清单 %d 只（最多展示 40）" % len(rows), md or "空")]
    return build_drill_card("PICKS", f, secs, title_extra=tier or "全部",
                            trade_basis=doc.get("trade_basis") or "")


HELP_MD = """**指令表**
· `缠论 605100` — 多层折叠主卡（中枢/情报/决策链）
· `详情 605100 筹码` — 单维度：筹码/板块/公告/龙虎榜/量能/共振/决策链/缠论
· `选股 T1` — 今日选股（T1/T2/全部）
· `止盈 605100 8` — 持仓止盈线（涨 8% 触发，模拟盘次日开盘清仓）
· `止损 605100 5` — 持仓止损线（跌 5% 触发）
· `止盈单` — 当前全部持仓线位快照
· `撤线 605100 tp` — 撤止盈（sl=撤止损）
· `帮助` — 本表
按钮下钻：主卡各折叠层展开 + 顶部按钮跳门户详情页。"""

# ── K-STOCK-122 M7：手机端止盈止损指令（exit_rules CLI 消费端）──
ER_SCRIPT = os.path.join(os.path.dirname(HERE), "k_stock", "paper",
                         "exit_rules.py")
# 白名单：env EXIT_ALLOW_OPEN_IDS（逗号分隔 open_id）；未配置=放行并警告
# （单用户部署默认宽松；配置后非白名单指令一律拒绝）


def _er_allowed(open_id):
    allow = os.environ.get("EXIT_ALLOW_OPEN_IDS", "").strip()
    if not allow:
        return True
    return open_id and open_id in {x.strip() for x in allow.split(",")}


def _er_cli(args):
    """子进程调 exit_rules CLI（UTF-8 管道，同 _paper_run_json 配方）。"""
    import subprocess
    r = subprocess.run(
        [sys.executable, "-X", "utf8", ER_SCRIPT, "--json"] + args,
        capture_output=True, timeout=120)
    out = r.stdout.decode("utf-8", "replace").strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": (r.stderr.decode("utf-8", "replace")
                                       or out)[-200:]}


def _fmt_lines(r):
    """list 结果 → 手机速览文本。"""
    if not r.get("ok"):
        return "查询失败：%s" % r.get("error")
    lines = ["**持仓线位快照**（%s）" % r.get("account")]
    for p in r.get("positions") or []:
        lines.append("· %s ×%s 成本 %.2f%s%s" % (
            p["stock"], p["qty"], p["avg_cost"],
            " ｜ 止损 %.2f（距 %.2f%%）" % (p["stop_price"],
                                            p["stop_distance_pct"])
            if p.get("stop_price") else " ｜ 无止损",
            ""))
    for t in r.get("tp_rules") or []:
        lines.append("· %s 止盈线 %.2f（+%s%%）" % (t["stock"], t["line"],
                                                   t["pct"]))
    if not (r.get("positions") or r.get("tp_rules")):
        lines.append("（无持仓、无线位）")
    return "\n".join(lines)


# ── M7 交互卡：卡片按钮 → 指令回路（点按→这端执行→回推结果卡）──

def _btn(text, value, ptype="default"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": text},
            "type": ptype, "value": value}


def card_er_lines(r):
    """持仓线位交互卡：每只持仓带 止盈/止损/撤线 按钮（点按进入幅度选择）。"""
    if not r.get("ok"):
        return {"config": {"wide_screen_mode": True},
                "header": {"template": "red", "title": {"tag": "plain_text",
                            "content": "持仓查询失败"}},
                "elements": [{"tag": "markdown",
                              "content": _esc(r.get("error"))}]}
    accts = r.get("account") or "main"
    els = []
    for p in r.get("positions") or []:
        code6 = (p["stock"] or "").split(".")[0]
        line = "**%s** ×%s 成本 %.2f" % (p["stock"], p["qty"], p["avg_cost"])
        if p.get("stop_price"):
            line += " · 止损 %.2f" % p["stop_price"]
        if p.get("stop_distance_pct") is not None:
            line += "（现价距 %.2f%%）" % p["stop_distance_pct"]
        els.append({"tag": "markdown", "content": line})
        els.append({"tag": "action", "actions": [
            _btn("止盈", {"tag": "er:choice", "code": code6, "kind": "tp"},
                 "primary"),
            _btn("止损", {"tag": "er:choice", "code": code6, "kind": "sl"},
                 "danger"),
            _btn("撤线", {"tag": "er:clear", "code": code6},
                 "default"),
        ]})
    for t in r.get("tp_rules") or []:
        els.append({"tag": "markdown", "content":
                    "**%s** 止盈线 %.2f（+%s%%）" % (t["stock"], t["line"],
                                                     t["pct"])})
    if not els:
        els.append({"tag": "markdown", "content": "（无持仓、无线位）"})
    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
                "点按按钮 → 选幅度 → 确认执行（模拟盘）"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text",
                       "content": "持仓线位 · %s" % accts}},
            "elements": els}


def card_er_pct(code, kind):
    """幅度选择卡：点幅度即执行（er:set），附撤销按钮。"""
    label = "止盈" if kind == "tp" else "止损"
    pcts = ["5", "8", "10", "15"] if kind == "tp" else ["3", "5", "8"]
    els = [{"tag": "markdown", "content":
            "为 **%s** 设置**%s**线，请点幅度（成本比例）："
            % (code, label)}]
    els.append({"tag": "action", "actions": [
        _btn("+%s%%" % p if kind == "tp" else "-%s%%" % p,
             {"tag": "er:set", "code": code, "kind": kind, "pct": p},
             "primary") for p in pcts]})
    els.append({"tag": "action", "actions": [
        _btn("取消", {"tag": "er:noop"}, "default")]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "orange", "title": {"tag": "plain_text",
                       "content": "%s %s 线设置" % (code, label)}},
            "elements": els}


def _card_sender_id(ev):
    """卡片回调里尽力取操作者 open_id（白名单用；取不到返回空）。"""
    op = getattr(ev, "operator", None)
    for att in ("open_id", "operator_open_id", "user_id"):
        v = getattr(op, att, None) if op is not None else None
        if v:
            return v
    ctx = getattr(ev, "context", None)
    if ctx is not None:
        try:
            d = ctx if isinstance(ctx, dict) else json.loads(str(ctx))
            return d.get("open_operator_id") or ""
        except Exception:  # noqa: BLE001
            pass
    return ""


# ── 发送 ────────────────────────────────────────────────────
def reply_card(card, message_id):
    from feishu_card_v2 import send_card  # noqa: F401  通道复用
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (ReplyMessageRequest,
                                     ReplyMessageBody)
    app_id, app_secret = _creds()
    cli = lark.Client.builder().app_id(app_id).app_secret(
        app_secret).build()
    body = ReplyMessageBody.builder().content(
        json.dumps({"msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False)})
    ).msg_type("interactive").build()
    req = (ReplyMessageRequest.builder().message_id(message_id)
           .request_body(body).build())
    resp = cli.im.v1.message.reply(req)
    return resp.success()


def _creds():
    from send_feishu import _load_app_creds
    return _load_app_creds()


def send_text(text, message_id):
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (ReplyMessageRequest,
                                     ReplyMessageBody)
    app_id, app_secret = _creds()
    cli = lark.Client.builder().app_id(app_id).app_secret(
        app_secret).build()
    body = (ReplyMessageBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text").build())
    req = (ReplyMessageRequest.builder().message_id(message_id)
           .request_body(body).build())
    return cli.im.v1.message.reply(req).success()


# ── 指令解析 ────────────────────────────────────────────────
def _norm_code(tok):
    from quote_snapshot import normalize_code
    c = normalize_code(tok)
    return {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}.get(c[:2], "") and \
        c[2:] + {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}[c[:2]] \
        if c[:2] in ("sh", "sz", "bj") else (
            c if "." in c else c + {"6": ".SH", "5": ".SH", "0": ".SZ",
                                    "3": ".SZ", "4": ".BJ", "8": ".BJ"
                                    }.get(c[:1], ".SZ")) if c.isdigit() and len(c) == 6 else c.upper()


def handle_cmd(text, message_id, open_id=""):
    text = (text or "").strip()
    low = text.lower()
    try:
        # M7 止盈止损：先于其余指令做白名单闸门
        if (low.startswith(("止盈", "止损", "止盈单", "撤线"))
                and not _er_allowed(open_id)):
            send_text("无权限：该指令仅限白名单用户。", message_id)
            return
        if low.startswith("止盈单"):
            send_text("查询中…", message_id)
            # 交互卡：持仓行带 止盈/止损/撤线 按钮（点按即走指令回路）
            reply_card(card_er_lines(_er_cli(["list"])), message_id)
        elif low.startswith("止盈") or low.startswith("止损"):
            kind = "tp" if low.startswith("止盈") else "sl"
            parts = text.split()
            if len(parts) < 3:
                send_text("用法：%s 605100 8 [账本]（幅度正数，账本缺省 main）"
                          % ("止盈" if kind == "tp" else "止损"), message_id)
                return
            args = ["set", "--code", parts[1], "--kind", kind,
                    "--pct", parts[2]]
            if len(parts) > 3:
                args += ["--account", parts[3]]
            r = _er_cli(args)
            send_text(("✅ " if r.get("ok") else "❌ ") + (
                "%s %s 线 %.2f 已设（成本 %.2f，%s%%）· %s"
                % (r.get("stock"), "止盈" if kind == "tp" else "止损",
                   r.get("line", 0), r.get("avg_cost", 0), r.get("pct"),
                   r.get("note", "")) if r.get("ok") else r.get("error")),
                message_id)
        elif low.startswith("撤线"):
            parts = text.split()
            if len(parts) < 3 or parts[2] not in ("tp", "sl"):
                send_text("用法：撤线 605100 tp|sl [账本]", message_id)
                return
            args = ["clear", "--code", parts[1], "--kind", parts[2]]
            if len(parts) > 3:
                args += ["--account", parts[3]]
            r = _er_cli(args)
            send_text(("✅ " if r.get("ok") else "❌ ")
                      + (r.get("note") or r.get("error")), message_id)
        elif low.startswith("帮助") or low in ("help", "?", "？"):
            send_text(HELP_MD, message_id)
        elif low.startswith("选股"):
            tier = "T1"
            for t in ("T1", "T2", "全部"):
                if t in text:
                    tier = t
            reply_card(card_picks(tier), message_id)
        elif low.startswith("详情"):
            parts = text.split()
            if len(parts) < 2:
                send_text("用法：详情 605100 [筹码|板块|公告|龙虎榜|量能|共振|缠论]",
                          message_id)
                return
            code = _norm_code(parts[1])
            dimmap = {"筹码": "chips", "板块": "sectors", "公告": "notices",
                      "龙虎榜": "lhb", "量能": "vol_events", "共振": "resonance",
                      "缠论": "chanlun", "决策": "no_buy_reasons"}
            dim = dimmap.get(parts[2] if len(parts) > 2 else "缠论",
                             "chanlun")
            f, cached = get_facts(code)
            reply_card(card_dim(code, f, dim), message_id)
        elif low.startswith("缠论"):
            parts = text.split()
            if len(parts) < 2:
                send_text("用法：缠论 605100", message_id)
                return
            code = _norm_code(parts[1])
            send_text("%s 生成中（~1 分钟，当日有缓存则秒回）…" % code,
                      message_id)
            f, cached = get_facts(code)
            reply_card(card_main(code, f, cached), message_id)
        else:
            send_text("没听懂。发「帮助」看指令表。", message_id)
    except Exception as e:  # noqa: BLE001
        log("cmd err: %s" % traceback.format_exc()[-300:])
        try:
            send_text("处理失败：%s" % str(e)[:100], message_id)
        except Exception:  # noqa: BLE001
            pass


# ── ws 主程序 ───────────────────────────────────────────────
def main():
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    from lark_oapi.ws import Client
    from feishu_card_v2 import send_card

    app_id, app_secret = _creds()
    if not app_id:
        log("no app creds, exit")
        return

    def on_msg(data: P2ImMessageReceiveV1):
        try:
            msg = data.event.message
            if msg.message_type != "text":
                return
            mid = msg.message_id
            content = json.loads(msg.content or "{}")
            text = content.get("text", "")
            log("recv: %r (chat=%s)" % (text[:40], msg.chat_type))
            sender_id = ""
            try:  # M7 白名单用：sender → sender_id → open_id（多层防御取值）
                sender_id = (msg.sender.sender_id.open_id or "")
            except Exception:  # noqa: BLE001
                pass
            threading.Thread(target=handle_cmd, args=(text, mid, sender_id),
                             daemon=True).start()
        except Exception:  # noqa: BLE001
            log("msg err " + traceback.format_exc()[-300:])

    def on_card(data):
        try:
            ev = getattr(data, "event", None) or {}
            action = (getattr(ev, "action", None) or {})
            val = getattr(action, "value", None) or {}
            if isinstance(val, str):
                val = json.loads(val)
            tag = val.get("tag", "")
            sender = _card_sender_id(ev)
            if tag.startswith("dim:"):
                _, dim, code = tag.split(":", 2)
                f, _ = get_facts(code)
                ok, why = send_card(card_dim(code, f, dim))
                log("card->%s %s %s" % (dim, code, ok or why))
            elif tag.startswith("er:") and not _er_allowed(sender):
                send_text("无权限：该操作仅限白名单用户。")
                log("card er denied (sender=%s)" % sender)
            elif tag == "er:list":
                ok, why = send_card(card_er_lines(_er_cli(["list"])))
                log("card er:list %s" % (ok or why))
            elif tag == "er:help":
                send_text(HELP_MD)
                log("card er:help")
            elif tag == "er:choice":
                ok, why = send_card(card_er_pct(val.get("code"),
                                                val.get("kind")))
                log("card er:choice %s" % (ok or why))
            elif tag == "er:set":
                r = _er_cli(["set", "--code", str(val.get("code")),
                             "--kind", str(val.get("kind")),
                             "--pct", str(val.get("pct"))])
                send_text(("✅ " if r.get("ok") else "❌ ")
                          + ("%s %s 线 %.2f 已设（成本 %.2f）· 触线后次日开盘清仓"
                             % (r.get("stock"), "止盈" if val.get("kind") == "tp"
                                else "止损", r.get("line", 0),
                                r.get("avg_cost", 0))
                             if r.get("ok") else r.get("error")))
                log("card er:set %s" % r.get("ok"))
            elif tag == "er:clear":
                # 点撤线 → 先发撤线选择卡（tp/sl 二选一，再点执行）
                code = val.get("code")
                els = [{"tag": "markdown", "content":
                        "撤销 **%s** 的哪条线？" % code},
                       {"tag": "action", "actions": [
                           _btn("撤止盈", {"tag": "er:clear2", "code": code,
                                           "kind": "tp"}, "primary"),
                           _btn("撤止损", {"tag": "er:clear2", "code": code,
                                           "kind": "sl"}, "danger"),
                           _btn("取消", {"tag": "er:noop"})]}]
                send_card({"config": {"wide_screen_mode": True},
                           "header": {"template": "grey",
                                      "title": {"tag": "plain_text",
                                                "content": "撤线 %s" % code}},
                           "elements": els})
            elif tag == "er:clear2":
                r = _er_cli(["clear", "--code", str(val.get("code")),
                             "--kind", str(val.get("kind"))])
                send_text(("✅ " if r.get("ok") else "❌ ")
                          + (r.get("note") or r.get("error")))
                log("card er:clear2 %s" % r.get("ok"))
            # er:noop → 无操作
        except Exception:  # noqa: BLE001
            log("card err " + traceback.format_exc()[-300:])

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_msg)
               .register_p2_card_action_trigger(on_card).build())
    cli = Client(app_id, app_secret, event_handler=handler,
                 log_level=lark.LogLevel.INFO)
    log("bot ws starting (app=%s)" % app_id)
    cli.start()


if __name__ == "__main__":
    main()
