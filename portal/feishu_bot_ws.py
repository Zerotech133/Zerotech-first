# -*- coding: utf-8 -*-
"""飞书下钻机器人（L1）· WebSocket 长连接，无需公网 IP。

指令（P2P 给机器人发消息，或群里 @）：
  缠论 605100            → 多层折叠主卡（判定/中枢/情报/应对/附录）
  详情 605100 筹码        → 单维度视图卡
  选股 [T1|T2|全部]       → 今日选股清单卡（默认 T1）
  开通/订阅查询/订阅巡查  → 订阅管理员指令（仅管理员，P3-lite H-A）
  帮助                    → 指令表
卡片按钮（card.action.trigger）：er:* 持仓线位交互 / ctl:* 控制中枢（feishu_control）。
dim:* 分支仅为旧 schema v1 卡兼容保留——schema 2.0 钻取卡无回调按钮（URL 走 behaviors）。

驻进程保活：SYSTEM 计划任务 FeishuBotWatchdog 每 5 分钟拉起（ZhiceWatchdog 同配方）。
日志：portal/logs/feishu_bot.log（长跑落文件铁律）。
前提：飞书开放平台该应用「事件与回调→接收方式」启用长连接；订阅
  im.message.receive_v1 与 card.action.trigger 两个事件。
"""

import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "news_collector"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))

# 语音输入兜底默认（2026-10-24）：不依赖看护脚本是否成功注入 env；
# 显式设 0/false 可关闭。模型为本地 faster-whisper（ModelScope 下载）。
if not (os.environ.get("VOICE_INGEST_ENABLED") or "").strip():
    os.environ["VOICE_INGEST_ENABLED"] = "1"
if not (os.environ.get("VOICE_ASR_PROVIDER") or "").strip():
    os.environ["VOICE_ASR_PROVIDER"] = "local"
if not (os.environ.get("VOICE_ASR_MODEL") or "").strip():
    os.environ["VOICE_ASR_MODEL"] = r"C:\aios\models\faster-whisper-small"
if not (os.environ.get("VOICE_AUTO_EXECUTE") or "").strip():
    os.environ["VOICE_AUTO_EXECUTE"] = "1"

LOG = os.path.join(HERE, "logs", "feishu_bot.log")
PICKS = os.path.join(HERE, "data", "todays_picks.json")
DDIR = os.path.join(HERE, "data", "pick_details")
DIMS = (
    "chanlun",
    "chips",
    "sectors",
    "notices",
    "lhb",
    "vol_events",
    "resonance",
    "no_buy_reasons",
)

_log_lock = threading.Lock()

# K-STOCK-299：飞书 receive_v1 里 @本app机器人 渲染为 @_user_N 脱敏占位
_AT_PLACEHOLDER = re.compile(r"@_user_\d+\s*")


def log(msg):
    line = "%s %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with _log_lock:
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "feishu_bot_ws.py", exc
            )
    print(line, flush=True)


# ── 看护加固（2026-10-24）：心跳 + WS 连接自检 ─────────────────
HEARTBEAT = os.path.join(HERE, "logs", "feishu_bot_heartbeat.txt")
_WS_CLIENT = {"cli": None}
_WS_MISS = {"n": 0}


def _heartbeat(tag=""):
    """写心跳文件；看护 loop 用它识别"进程活着但假在线/假死"。"""
    try:
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), tag))
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "feishu_bot_ws.py", exc
        )


def _ws_guard():
    """每 120s 查 lark WS 连接；连续 5 次无连接（~10min）主动退出，看护重拉。"""
    while True:
        time.sleep(120)
        try:
            cli = _WS_CLIENT.get("cli")
            conn = getattr(cli, "_conn", None) if cli is not None else None
            if cli is not None and conn is None:
                _WS_MISS["n"] += 1
                log("ws guard: no connection (%d/5)" % _WS_MISS["n"])
                if _WS_MISS["n"] >= 5:
                    log("ws guard: ws dead >10min, exit(3) for watchdog restart")
                    os._exit(3)
            else:
                _WS_MISS["n"] = 0
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "feishu_bot_ws.py", exc
            )


# ── L2 引擎接合 ─────────────────────────────────────────────
def _portal_shim():
    import api_common
    import portal_api
    import portal_report

    class _P:
        _chanlun_sl_tp = staticmethod(portal_api.Handler._chanlun_sl_tp)
        _stock_chip_summary = staticmethod(api_common._stock_chip_summary)
        _stock_sector_rows = staticmethod(api_common._stock_sector_rows)

    return _P(), portal_report


def get_facts(code):
    """当日事实包优先（秒回），否则现抓（~66s）。"""
    fp = os.path.join(DDIR, "%s.json" % code)
    if os.path.exists(fp):
        try:
            old = json.load(open(fp, encoding="utf-8"))
            if (old.get("ts") or "").startswith(datetime.now().strftime("%Y-%m-%d")):
                return old, True
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "feishu_bot_ws.py", exc
            )
    portal, pr = _portal_shim()
    return pr.collect_facts(portal, code), False


def md_chanlun(f):
    cl = f.get("chanlun") or {}
    if not cl:
        return "无缠论中枢记录"
    lines = [
        "**日线中枢** %s~%s（%s）"
        % (cl.get("dzd"), cl.get("dzg"), cl.get("pivot_state")),
        "**30m 中枢** %s~%s" % (cl.get("mzd"), cl.get("mzg")),
        "**操作** %s" % cl.get("plan"),
        "止损 %s（%s%%）· tp1 %s · tp2 %s"
        % (cl.get("stop"), cl.get("stop_pct"), cl.get("tp1"), cl.get("tp2")),
    ]
    rows = [
        json.loads(l)
        for l in open(
            os.path.join(
                os.path.dirname(HERE), "quant_backtest", "data", "chanlun_pool.jsonl"
            ),
            encoding="utf-8",
        )
        if l.strip()
    ]
    hist = [r for r in rows if r.get("code") == f.get("code")]
    if hist:
        lines.append(
            "**池内历史信号**：%s"
            % "；".join(
                "%s %s@%s" % (r.get("dt"), r.get("type"), r.get("price"))
                for r in hist[-3:]
            )
        )
    return "\n".join(lines)


def md_intel(f):
    ve = f.get("vol_events") or []
    lines = []
    if ve:
        lines.append(
            "**量能事件**："
            + "；".join(
                "%s %s ×%s（%s%%）"
                % (e.get("date"), e.get("tag"), e.get("mult"), e.get("pct"))
                for e in ve[-3:]
            )
        )
    ns = f.get("notices") or []
    if ns:
        lines.append(
            "**近端公告**："
            + "；".join(
                "%s %s"
                % (
                    n.get("date"),
                    (n.get("title") or "")
                    .replace("华丰股份:", "")
                    .replace("华丰动力股份有限公司", ""),
                )[:38]
                for n in ns[:3]
            )
        )
    lines.append(
        "**龙虎榜**：%s"
        % ("近 130 天无上榜记录" if not f.get("lhb") else "有上榜（见门户）")
    )
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
            out.append("  当前：%s → 需要：%s" % (r.get("cur"), r.get("need")))
    out.append(
        "**三情形**：① 上沿冲高回落等回踩 ② 回踩缩量企稳=三买雏形 "
        "③ 站稳回踩不破=三买候选"
    )
    return "\n".join(out)


def card_main(code, facts, cached):
    from feishu_card_v2 import build_drill_card

    secs = [
        ("📈 缠论中枢与操作方案", md_chanlun(facts)),
        ("🔍 四源情报（公告/资金/量能/新闻）", md_intel(facts)),
        ("🎯 决策链与三情形应对", md_play(facts)),
        (
            "🗂 数据附录",
            "事实包 ts=%s（%s）\n维度：quote/chanlun/chips/"
            "sectors/notices/lhb/vol_events/no_buy_reasons"
            % (facts.get("ts"), "缓存秒回" if cached else "现抓 66s"),
        ),
    ]
    extra = "" if cached else " · 现抓"
    return build_drill_card(code, facts, secs, title_extra="缠论×情报" + extra)


def card_dim(code, facts, dim):
    from feishu_card_v2 import build_drill_card

    v = facts.get(dim)
    md = json.dumps(v, ensure_ascii=False, indent=1)[:1800] if v else "该维度暂无数据"
    secs = [("📋 %s 原始视图" % dim, md)]
    return build_drill_card(code, facts, secs, title_extra=dim)


def card_picks(tier):
    from feishu_card_v2 import build_drill_card

    doc = json.load(open(PICKS, encoding="utf-8"))
    rows = doc.get("stocks") or []
    if tier in ("T1", "T2"):
        rows = [r for r in rows if r["tier"] == tier]
    rows = rows[:40]
    md = "\n".join(
        "%s %s ×%d %s"
        % (
            r["code"],
            r["name"],
            r["n_hit"],
            "/".join(sorted({s["s"] for s in r["sources"]} - {"cross"})),
        )
        for r in rows
    )
    f = {
        "name": "今日选股 %s" % (tier or "全部"),
        "no_buy_reasons": {"verdict": "清单视图"},
        "quote": {"px": None},
        "chanlun": {},
        "ts": doc.get("generated_at"),
    }
    secs = [("🎯 清单 %d 只（最多展示 40）" % len(rows), md or "空")]
    return build_drill_card(
        "PICKS",
        f,
        secs,
        title_extra=tier or "全部",
        trade_basis=doc.get("trade_basis") or "",
    )


# ── 股票血缘关系（KG Neo4j 查询）───────────────────────────
# D3（N8-1）_SYS_PY 参数化：env KG_SYS_PY 优先，缺省回退系统
# Python311（_kg_stock：CowAgent venv 缺 pandas/neo4j，借道系统 Python）
_SYS_PY = os.environ.get("KG_SYS_PY") or r"C:\Program Files\Python311\python.exe"
_KG_QUERY = os.path.join(HERE, "kg_stock_query.py")


def _kg_stock(code, days=90):
    """subprocess 调系统 Python 跑 kg_portal.stock()，拿 JSON。
    CowAgent venv 缺 pandas/neo4j，借道系统 Python。"""
    import subprocess as _sp

    try:
        r = _sp.run(
            [_SYS_PY, _KG_QUERY, code, str(days)], capture_output=True, timeout=20
        )
        if r.returncode != 0:
            return {"ok": False, "error": ("kg query exit=%d" % r.returncode)}
        return json.loads(r.stdout.decode("utf-8", errors="replace"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def card_lineage(code, data):
    """把 kg_portal.stock() 返回 dict 格式化为折叠卡 sections。"""
    from feishu_card_v2 import build_drill_card

    basic = data.get("basic") or {}
    name = basic.get("name") or code
    facts = {"code": code.split(".")[0], "name": name}

    sections = []

    # ① 本体概览
    overview = (
        f"**公司** {basic.get('company') or basic.get('name', '—')}\n"
        f"**行业** {basic.get('industry') or '—'} ｜ **地区** {basic.get('area') or '—'}\n"
        f"**上市日** {basic.get('list_date') or '—'}"
    )
    cov = data.get("candle_cov") or {}
    if cov:
        overview += f"\n**K线覆盖** {'✅' if cov.get('covered') else '❌'}（总 {cov.get('n_cov', '?')} 票）"
    cyq = data.get("cyq") or {}
    if cyq:
        overview += (
            f"\n**筹码分布** 赢家比例 {cyq.get('winner_rate', '—')}%"
            f"｜成本跨度 {cyq.get('cost_span', '—')}"
        )
    sections.append(("🧬 本体概览", overview))

    # ② K线相似（SIMILAR_K 边）
    sim = data.get("similar") or []
    if sim:
        sim_lines = [
            "| 代码 | 名称 | 距离 | 相关系数 | 窗口 |",
            "|------|------|------|----------|------|",
        ]
        for s in sim:
            sim_lines.append(
                f"| {s.get('tc', '—')} | {s.get('nm', '—')} | "
                f"{round(s.get('dist', 0), 3)} | {s.get('corr', '—')} | "
                f"{s.get('ws', '—')}~{s.get('we', '—')} |"
            )
        sections.append(("🔗 K线相似 Top-5", "\n".join(sim_lines)))

    # ③ 游资痕迹（HotMoney → Seat → TRADED_ON）
    hm = data.get("hotmoney") or []
    if hm:
        hm_lines = [
            "| 游资 | 席位 | 净额 | 次数 | 最近 |",
            "|------|------|------|------|------|",
        ]
        for h in hm[:10]:
            hm_lines.append(
                f"| {h.get('hm', '—')} | | "
                f"{h.get('net', 0):.0f} | {h.get('n', 0)} | {h.get('last', '—')} |"
            )
        sections.append(("💰 游资痕迹", "\n".join(hm_lines)))

    # ④ 驱动事件（EVENT → DRIVE → MarketEvent 聚类）
    drive = data.get("drive_groups") or []
    if drive:
        drv_lines = []
        for g in drive[:8]:
            tag = "📌" if g.get("baseline") else "🎯"
            txt = g.get("texts") or []
            drv_lines.append(
                f"{tag} **{g.get('grp', '—')}** ×{g.get('n', 0)} "
                f"（{g.get('last', '—')}）\n  " + ("；".join(txt[:2]) if txt else "—")
            )
        sections.append(("⚡ 驱动事件聚类", "\n\n".join(drv_lines)))

    # ⑤ 机构持仓（Organization → HOLDS）
    holders = data.get("holders") or []
    if holders:
        # 按 period 分组
        by_period = {}
        for h in holders:
            p = h.get("period", "")
            by_period.setdefault(p, []).append(h)
        h_lines = []
        for p in sorted(by_period.keys(), reverse=True)[:3]:
            h_lines.append(f"**{p[:4]}-{p[4:6]} 期末**")
            for h in sorted(
                by_period[p], key=lambda x: x.get("ratio", 0), reverse=True
            )[:5]:
                h_lines.append(
                    f"· {h.get('name', '—')[:20]} "
                    f"**{h.get('ratio', 0):.2f}%** ｜ "
                    f"持仓 {h.get('amt', 0):.0f}"
                )
        sections.append(("🏛 机构持仓", "\n".join(h_lines)))

    # ⑥ 结构信号（HAS_STRUCTURE → MarketEvent.type=中枢/买卖点等）
    structures = data.get("structures") or []
    if structures:
        st_lines = []
        for s in structures[:10]:
            st_lines.append(
                f"· **{s.get('date', '—')}**  {s.get('type', '—')}  "
                f"置信 {s.get('conf', '—')} ｜ 结构 {s.get('q_struct', '—')}  "
                f"证据 {s.get('q_evid', '—')}  HM {s.get('q_hm', '—')}"
            )
        sections.append(("📐 结构信号（近90天）", "\n".join(st_lines)))

    # 兜底
    if not sections:
        sections.append(("📋 数据说明", "未查到关联数据（Neo4j 可能暂无覆盖）"))

    extras = f"（窗口 {data.get('window_days', 90)} 天）"
    return build_drill_card(
        code,
        facts,
        sections,
        title_extra=f"血缘{extras}",
        trade_basis="KG Neo4j · 实时查询",
    )


HELP_MD = """**指令表**
· `缠论 605100` — 多层折叠主卡（中枢/情报/决策链）
· `血缘 605100` — KG 血缘关系卡（相似K线/游资/驱动/持仓/结构）
· `关联 605100` — 血缘别名
· `详情 605100 筹码` — 单维度：筹码/板块/公告/龙虎榜/量能/共振/决策链/缠论
· `选股 T1` — 今日选股（T1/T2/全部）
· `止盈 605100 8` — 持仓止盈线（涨 8% 触发，模拟盘次日开盘清仓）
· `止损 605100 5` — 持仓止损线（跌 5% 触发）
· `止盈单` — 当前全部持仓线位快照
· `撤线 605100 tp` — 撤止盈（sl=撤止损）
· `控制` — 控制中枢卡：推送开关/参数档位/告警处置/工单看板
· `机器` / `新对话` / `问：<问题>` — **已迁移**：请私聊「零界·执行」（连续对话/远程入口）
· `调优 <想法>` — 模型调优反馈（K-PUSH-002 闭环入口，语音输入亦可）
· `审批 <req_id> 通过|驳回 [原因]` — 调优请求审批（仅管理员）
管理员指令（仅管理员）：
· `开通 <open_id> <档位> [天数]` — 开通订阅（默认 30 天）
· `订阅查询 <open_id>` — 订阅摘要查询
· `订阅巡查` — 过期清扫 + 订阅者概要
· `撤销 <open_id> <档位>` — 显式撤销订阅（留痕 revoked，耗用不回冲）
· `帮助` — 本表
按钮下钻：主卡各折叠层展开 + 顶部按钮跳门户详情页。"""

# ── K-STOCK-122 M7：手机端止盈止损指令（exit_rules CLI 消费端）──
ER_SCRIPT = os.path.join(os.path.dirname(HERE), "k_stock", "paper", "exit_rules.py")
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
        capture_output=True,
        timeout=120,
    )
    out = r.stdout.decode("utf-8", "replace").strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {
            "ok": False,
            "error": (r.stderr.decode("utf-8", "replace") or out)[-200:],
        }


def _fmt_lines(r):
    """list 结果 → 手机速览文本。"""
    if not r.get("ok"):
        return "查询失败：%s" % r.get("error")
    lines = ["**持仓线位快照**（%s）" % r.get("account")]
    for p in r.get("positions") or []:
        lines.append(
            "· %s ×%s 成本 %.2f%s%s"
            % (
                p["stock"],
                p["qty"],
                p["avg_cost"],
                " ｜ 止损 %.2f（距 %.2f%%）" % (p["stop_price"], p["stop_distance_pct"])
                if p.get("stop_price")
                else " ｜ 无止损",
                "",
            )
        )
    for t in r.get("tp_rules") or []:
        lines.append("· %s 止盈线 %.2f（+%s%%）" % (t["stock"], t["line"], t["pct"]))
    if not (r.get("positions") or r.get("tp_rules")):
        lines.append("（无持仓、无线位）")
    return "\n".join(lines)


# ── M7 交互卡：卡片按钮 → 指令回路（点按→这端执行→回推结果卡）──


def _btn(text, value, ptype="default"):
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": ptype,
        "value": value,
    }


def _esc(s):
    """错误文本进 markdown 前的轻转义（K-STOCK-137：原调用处无定义，
    er:list 失败路径 NameError 被吞=点了零反馈）。"""
    return str(s or "").replace("`", "'").replace("*", "")[:200]


def card_er_lines(r):
    """持仓线位交互卡：每只持仓带 止盈/止损/撤线 按钮（点按进入幅度选择）。"""
    if not r.get("ok"):
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": "持仓查询失败"},
            },
            "elements": [{"tag": "markdown", "content": _esc(r.get("error"))}],
        }
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
        els.append(
            {
                "tag": "action",
                "actions": [
                    _btn(
                        "止盈",
                        {"tag": "er:choice", "code": code6, "kind": "tp"},
                        "primary",
                    ),
                    _btn(
                        "止损",
                        {"tag": "er:choice", "code": code6, "kind": "sl"},
                        "danger",
                    ),
                    _btn("撤线", {"tag": "er:clear", "code": code6}, "default"),
                ],
            }
        )
    for t in r.get("tp_rules") or []:
        els.append(
            {
                "tag": "markdown",
                "content": "**%s** 止盈线 %.2f（+%s%%）"
                % (t["stock"], t["line"], t["pct"]),
            }
        )
    if not els:
        els.append({"tag": "markdown", "content": "（无持仓、无线位）"})
    els.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "点按按钮 → 选幅度 → 确认执行（模拟盘）",
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "持仓线位 · %s" % accts},
        },
        "elements": els,
    }


def card_er_pct(code, kind):
    """幅度选择卡：点幅度即执行（er:set），附撤销按钮。"""
    label = "止盈" if kind == "tp" else "止损"
    pcts = ["5", "8", "10", "15"] if kind == "tp" else ["3", "5", "8"]
    els = [
        {
            "tag": "markdown",
            "content": "为 **%s** 设置**%s**线，请点幅度（成本比例）：" % (code, label),
        }
    ]
    els.append(
        {
            "tag": "action",
            "actions": [
                _btn(
                    "+%s%%" % p if kind == "tp" else "-%s%%" % p,
                    {"tag": "er:set", "code": code, "kind": kind, "pct": p},
                    "primary",
                )
                for p in pcts
            ],
        }
    )
    els.append(
        {"tag": "action", "actions": [_btn("取消", {"tag": "er:noop"}, "default")]}
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "%s %s 线设置" % (code, label)},
        },
        "elements": els,
    }


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
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "feishu_bot_ws.py", exc
            )
    return ""


def _card_ctx(ev):
    """取卡片回调的定位坐标（message_id / token），供跨模块转发用。"""
    ctx = getattr(ev, "context", None)
    mid = getattr(ctx, "open_message_id", None) if ctx is not None else None
    tok = getattr(ev, "token", None)
    return {"message_id": mid or "", "token": tok or ""}


# ── JEV 告警卡并轨转发（FB-A，2026-11-05） ──────────────────
# JEV 原先自建第二个长连接消费者（lark_card_handler 走 lark-cli event consume），
# 与本体争同一个 app 的唯一 WS，机器人一重启就把它顶死。改为：本 bot 独占 WS，
# 把 value 带 alert_id 的 JEV 卡片动作转发到 JEV 本地 HTTP 接入（127.0.0.1:9106）。
JEV_CARD_ACK_URL = os.environ.get(
    "JEV_CARD_ACK_URL", "http://127.0.0.1:9106/card_action"
)


def _forward_jev_card(val, sender, ctx=None):
    """转发 JEV 卡片动作。返回 (ok, 说明)。超时/不可达不抛，交调用方回执。"""
    import urllib.request

    ctx = ctx or {}
    body = {
        "source": val.get("source") or "jev",
        "action": val.get("action") or "",
        "alert_id": val.get("alert_id") or "",
        "operator_id": sender or "",
        "message_id": ctx.get("message_id") or "",
        "token": ctx.get("token") or "",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        JEV_CARD_ACK_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        return bool(payload.get("ok")), ""
    except Exception as e:  # noqa: BLE001
        log("jev card forward fail: %s" % str(e)[:160])
        return False, str(e)[:100]


# ── 发送 ────────────────────────────────────────────────────
_cli_lock = threading.Lock()
_cli_cache = {}  # {(app_id, app_secret): lark.Client}——凭证轮换自动重建


def _lark_client():
    """复用 lark.Client。原 reply_card/send_text 每条消息新建：build() 装配
    ~58 个 Service 对象（纯构造、无网络 IO），纯属重复分配；Client 跨线程
    共享安全（_config 只读，token 走 SDK 全局缓存）。每次仍读 config.json
    取凭证——键控缓存，凭证轮换后自动重建。"""
    import lark_oapi as lark

    app_id, app_secret = _creds()
    key = (app_id, app_secret)
    with _cli_lock:
        cli = _cli_cache.get(key)
        if cli is None:
            _cli_cache.clear()  # 单应用部署：只留当前凭证的实例
            cli = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
            _cli_cache[key] = cli
        return cli


def reply_card(card, message_id):
    from feishu_card_v2 import send_card  # noqa: F401

    # K-STOCK-141：类名实为 ReplyMessageRequestBody（原 ReplyMessageBody
    # 在已装 lark_oapi 中不存在，import 即炸→卡片回复从未送达）
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

    cli = _lark_client()
    body = (
        ReplyMessageRequestBody.builder()
        .content(
            json.dumps(
                {
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                }
            )
        )
        .msg_type("interactive")
        .build()
    )
    req = (
        ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    )
    resp = cli.im.v1.message.reply(req)
    return resp.success()


def _creds():
    from send_feishu import _load_app_creds

    return _load_app_creds()


def send_text(text, message_id=None):
    """message_id 有值=会话内回复；无值=OpenAPI 直发本人（卡片回调路径用）。
    （K-STOCK-135 修复：on_card 原以单参调用本函数，必填 message_id 会
    TypeError 被外层吞掉——卡片按钮的文本回执从未真正送达。）"""
    if not message_id:
        from send_feishu import send_text as _sf_send_text

        return _sf_send_text(text)
    # K-STOCK-141：同 reply_card——正确类名 ReplyMessageRequestBody
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

    cli = _lark_client()
    body = (
        ReplyMessageRequestBody.builder()
        .content(json.dumps({"text": text}, ensure_ascii=False))
        .msg_type("text")
        .build()
    )
    req = (
        ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    )
    return cli.im.v1.message.reply(req).success()


def _machine_entry_text():
    """远程入口链接：读 dsh web 表层的带 token URL（每次重启 token 会变）。
    供「机器」指令使用——手机飞书里拿到链接后在浏览器打开即可操作本机。
    容错：入口日志可能被 web 进程独占/BOM/UTF-16，读不到就退回 current_url.txt。"""
    remote_dir = os.environ.get("DSH_REMOTE_DIR") or r"C:\项目\dsh_runtime"
    candidates = [
        os.environ.get("DSH_REMOTE_URL_FILE") or "",
        os.path.join(remote_dir, "current_url.txt"),
        os.environ.get("DSH_REMOTE_LOG") or "",
        os.path.join(remote_dir, "dsh_web.log"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        lines = []
        for enc in ("utf-8-sig", "utf-16", "gbk"):
            try:
                lines = data.decode(enc).splitlines()
                break
            except Exception:  # noqa: BLE001
                continue
        for line in reversed(lines):
            line = line.strip().lstrip("\ufeff").strip()
            if line.startswith("dsh web: "):
                url = line[len("dsh web: ") :].strip()
                return (
                    "🖥 远程入口（手机浏览器打开）：\n"
                    + url
                    + "\n\n· 链接含访问令牌，请勿转发/截图外发。\n"
                    "· 入口进程重启后 token 会变，重新发「机器」取最新。"
                )
            if line.startswith("https://") and "token=" in line:
                return (
                    "🖥 远程入口（手机浏览器打开）：\n"
                    + line
                    + "\n\n· 链接含访问令牌，请勿转发/截图外发。"
                )
    return "机器入口未就绪：本机 dsh web 表层未运行（找 Lead 拉起）。"


def _handle_audio(msg, data):
    """P0：飞书语音消息 → 文本（默认关闭）。

    为什么这样写：
      · **默认零行为变化**：VOICE_INGEST_ENABLED 不为 1 时直接返回，机器人仍走
        原来的 skip non-text 分支，开关一关即回滚到旧行为。
      · **重活放线程**：on_msg 跑在 SDK 的 asyncio 单循环里，同步下载 + 识别
        会卡住 WS 心跳（判例：辅助线程不得反噬主链）。
      · **群聊不接管**：语音指令只在 p2p 生效，避免群里一条语音触发全局动作。
      · **语音只转文字**：默认不执行任何指令，见 voice_ingest 的 VOICE_AUTO_EXECUTE。
    """
    try:
        import voice_ingest as vi
    except Exception as e:  # noqa: BLE001
        log("voice ingest import failed: %s" % (str(e)[:120],))
        return
    if not vi.enabled():
        log("skip audio (VOICE_INGEST_ENABLED=0)")
        return
    mid = getattr(msg, "message_id", "") or ""
    chat_type = getattr(msg, "chat_type", "") or ""
    if chat_type != "p2p":
        log("skip audio in %s chat: %s" % (chat_type, mid[-8:]))
        return
    sender_id = ""
    try:
        sender_id = data.event.sender.sender_id.open_id or ""
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "feishu_bot_ws.py", exc
        )

    def _run():
        try:

            def _download(meta):
                from lark_oapi.api.im.v1 import GetMessageResourceRequest

                req = (
                    GetMessageResourceRequest.builder()
                    .message_id(mid)
                    .file_key(meta["file_key"])
                    .type("audio")
                    .build()
                )
                resp = _lark_client().im.v1.message_resource.get(req)
                if not getattr(resp, "success", lambda: False)():
                    raise vi.VoiceError(
                        "DOWNLOAD_FAILED", "取回语音失败（飞书接口返回失败）"
                    )
                fh = getattr(resp, "file", None)
                payload = fh.read() if hasattr(fh, "read") else b""
                return payload, (lambda: None)

            transcriber, provider, why = vi.build_transcriber()
            rec = vi.ingest_audio(
                message_id=mid,
                content=msg.content,
                sender_id=sender_id,
                chat_type=chat_type,
                download=_download,
                transcriber=transcriber,
                notify=lambda t: send_text(t, mid),
                dispatch=lambda t: handle_cmd(t, mid, sender_id, chat_type),
                log=log,
                auto_execute=vi.auto_execute(),
                transcriber_reason=why,
            )
            log(
                "voice %s: status=%s provider=%s dur=%sms bytes=%s chars=%s sha=%s"
                % (
                    mid[-8:],
                    rec.get("status"),
                    provider,
                    rec.get("duration_ms"),
                    rec.get("bytes"),
                    rec.get("text_chars"),
                    (rec.get("audio_sha256") or "")[:12],
                )
            )
        except Exception:  # noqa: BLE001
            log("voice err " + traceback.format_exc()[-240:])

    threading.Thread(target=_run, daemon=True).start()


# ── 指令解析 ────────────────────────────────────────────────
def _norm_code(tok):
    from quote_snapshot import normalize_code

    c = normalize_code(tok)
    return (
        {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}.get(c[:2], "")
        and c[2:] + {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}[c[:2]]
        if c[:2] in ("sh", "sz", "bj")
        else (
            c
            if "." in c
            else c
            + {
                "6": ".SH",
                "5": ".SH",
                "0": ".SZ",
                "3": ".SZ",
                "4": ".BJ",
                "8": ".BJ",
            }.get(c[:1], ".SZ")
        )
        if c.isdigit() and len(c) == 6
        else c.upper()
    )


# ── 飞书中台 P3-lite（H-A）：订阅管理员指令族 ────────────────
def mask_open_id(open_id):
    """open_id 掩码纯函数（回执展示用，便于单测）：ou_abc12** 样式——
    保留前 8 位 + "**"；过短 id 降级保留前 4/前 2 位；空入参回 "**"。"""
    s = str(open_id or "")
    if len(s) > 8:
        return s[:8] + "**"
    if len(s) > 4:
        return s[:4] + "**"
    if s:
        return s[:2] + "**"
    return "**"


def handle_cmd(text, message_id, open_id="", chat_type="p2p"):
    # W43：chat_type 随消息透传——入向受理器仅受理 P2P 投喂，群聊不进
    text = (text or "").strip()
    low = text.lower()
    try:
        # 2026-10-25 W2/W3 分域（旧 bot 回归纯指令）：
        # DSH 问答/连续对话/远程入口/新对话 已迁移到「零界·执行」(zb.owner.exec)，
        # 旧 bot 只保留量化指令与 W43 投喂，不再承载执行能力。
        if low.rstrip("。！!?？. ") in (
            "新对话",
            "重置对话",
            "重置",
            "清空对话",
            "忘记上下文",
        ):
            send_text("该能力已迁移：请私聊「零界·执行」发「新对话」。", message_id)
            return
        for _p in ("机器问：", "机器问:", "问：", "问:", "AI：", "AI:", "ai：", "ai:"):
            if text.startswith(_p):
                send_text("该能力已迁移：DSH 问答请私聊「零界·执行」。", message_id)
                return
        # M7 止盈止损：先于其余指令做白名单闸门
        if low.startswith(("止盈", "止损", "止盈单", "撤线")) and not _er_allowed(
            open_id
        ):
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
                send_text(
                    "用法：%s 605100 8 [账本]（幅度正数，账本缺省 main）"
                    % ("止盈" if kind == "tp" else "止损"),
                    message_id,
                )
                return
            args = ["set", "--code", parts[1], "--kind", kind, "--pct", parts[2]]
            if len(parts) > 3:
                args += ["--account", parts[3]]
            r = _er_cli(args)
            send_text(
                ("✅ " if r.get("ok") else "❌ ")
                + (
                    "%s %s 线 %.2f 已设（成本 %.2f，%s%%）· %s"
                    % (
                        r.get("stock"),
                        "止盈" if kind == "tp" else "止损",
                        r.get("line", 0),
                        r.get("avg_cost", 0),
                        r.get("pct"),
                        r.get("note", ""),
                    )
                    if r.get("ok")
                    else r.get("error")
                ),
                message_id,
            )
        elif low.startswith("撤线"):
            parts = text.split()
            if len(parts) < 3 or parts[2] not in ("tp", "sl"):
                send_text("用法：撤线 605100 tp|sl [账本]", message_id)
                return
            args = ["clear", "--code", parts[1], "--kind", parts[2]]
            if len(parts) > 3:
                args += ["--account", parts[3]]
            r = _er_cli(args)
            send_text(
                ("✅ " if r.get("ok") else "❌ ") + (r.get("note") or r.get("error")),
                message_id,
            )
        elif low.startswith("控制") or low.startswith("面板") or low == "ctrl":
            # K-STOCK-135 控制中枢：四域状态 + 按钮（ctl:* 动作同样受白名单）
            import feishu_control

            if not feishu_control.allowed(open_id):
                send_text("无权限：控制中枢仅限白名单用户。", message_id)
                return
            reply_card(feishu_control.build_control_card(), message_id)
        elif low.startswith("调优"):
            # K-PUSH-002 B4：客户反馈调优闭环入口——语音输入经飞书客户端
            # 自带转文字后同样落此指令（零自建 ASR，绕开公网静态站约束
            # K-STOCK-411）。customer=open_id（VIP 私有空间的唯一键）。
            body = text[2:].strip()
            if len(body) < 5:
                send_text(
                    "用法：调优 <您的想法>\n例：调优 我对缠论中枢的理解"
                    "和你们不一样，二买应该在…",
                    message_id,
                )
                return
            import tune_gate

            rid = tune_gate.submit(
                body, customer=open_id or "飞书客户", plan="internal", source="text"
            )
            send_text(
                "✅ 已受理 %s\n流程：审核 → 沙盒回测对比 → 落地您的"
                "专属参数空间。结果将以回执卡通知。" % rid,
                message_id,
            )
        elif low.startswith("审批"):
            # K-PUSH-002 B2：审批闸门 bot 通道（管理员白名单 =
            # work/tune_admins.json；空=内测开放，上线收费前必须配置）
            parts = text.split()
            if len(parts) < 3 or parts[2] not in ("通过", "驳回"):
                send_text("用法：审批 <req_id> 通过|驳回 [原因]", message_id)
                return
            import tune_gate

            if not tune_gate.is_admin(open_id):
                send_text("无权限：调优审批仅限管理员。", message_id)
                return
            if parts[2] == "通过":
                r = tune_gate.approve(parts[1], operator=open_id or "飞书审批")
            else:
                r = tune_gate.reject(
                    parts[1], operator=open_id or "飞书审批", reason=" ".join(parts[3:])
                )
            send_text(
                ("✅ " if r.get("ok") else "❌ ")
                + (r.get("error") or ("已处理 " + parts[1])),
                message_id,
            )
        elif low.startswith(("开通", "订阅查询", "订阅巡查", "撤销")):
            # 飞书中台 P3-lite（H-A）：订阅管理员指令族——判例照「审批」族
            # （前缀分发 + 管理员闸 + send_text 回执）。管理员闸复用
            # tune_gate.is_admin（tune_admins.json 已配置非空=严格校验），
            # 不另建 sub_admins.json（单人部署复制第二份白名单无分离收益）。
            # 非管理员一律"无权限"拒绝，usage 提示在闸后，不透漏指令存在。
            # P3.6-lite（K-B）回执通道验证：本族回执 message_id 恒有值
            # （on_msg 唯一入口 mid=msg.message_id），send_text 带
            # message_id 走 Reply API（im.v1.message.reply），不经 8097
            # 推送中台闸/静默窗（那是 message_id 为空时 send_feishu 直发链路）。
            import tune_gate

            if not tune_gate.is_admin(open_id):
                send_text("无权限。", message_id)
                return
            parts = text.split()
            import subscription_store

            if low.startswith("开通"):
                if len(parts) < 3:
                    send_text(
                        "用法：开通 <open_id> <档位> [天数]（天数默认 30）", message_id
                    )
                    return
                try:
                    subscription_store.upsert_subscriber(parts[1])
                    # P3.6-lite（K-B）重复开通幂等（指令层查重）：已有生效
                    # 同档位（active 且覆盖当前时刻）则诚实回执、不重复插行
                    # ——重复指令多为误触/重试，直接叠插会悄悄叠加时长（最
                    # 小惊讶原则）；换档/提前续期待撤销订阅能力落地后走显式
                    # 链路。查重走 get_subscriber_summary 只读投影，零改
                    # store 契约与 test_subscription_store 快照。
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # 查重异常降级放行（closed on failure 判例：store 判定
                    # 异常不阻塞开通，grant 自身校验兜底；亦兼容指令层
                    # stub 测试环境无该方法的情形）。
                    try:
                        s = subscription_store.get_subscriber_summary(parts[1])
                        dup = next(
                            (
                                x
                                for x in (s.get("subscriptions") or [])
                                if x.get("plan_id") == parts[2]
                                and x.get("status") == "active"
                                and str(x.get("start", ""))
                                <= now_str
                                <= str(x.get("end", ""))
                            ),
                            None,
                        )
                    except Exception:  # noqa: BLE001
                        dup = None
                    if dup:
                        send_text(
                            "ℹ️ %s 已有生效的 %s 档位订阅（至 %s），"
                            "未重复开通。"
                            % (mask_open_id(parts[1]), parts[2], dup["end"]),
                            message_id,
                        )
                        return
                    r = subscription_store.grant_subscription(
                        parts[1], parts[2], int(parts[3]) if len(parts) > 3 else 30
                    )
                except ValueError as e:
                    send_text("❌ %s" % e, message_id)
                    return
                send_text(
                    "✅ 已开通 %s 档位 %s，到期 %s"
                    % (mask_open_id(parts[1]), parts[2], r["end"]),
                    message_id,
                )
            elif low.startswith("订阅查询"):
                if len(parts) < 2:
                    send_text("用法：订阅查询 <open_id>", message_id)
                    return
                s = subscription_store.get_subscriber_summary(parts[1])
                if s.get("error"):
                    send_text("❌ 查询失败：%s" % s["error"], message_id)
                    return
                lines = [
                    "**订阅摘要** %s（%s）"
                    % (mask_open_id(parts[1]), s.get("display_name") or "未命名"),
                    "状态：%s" % s.get("reason"),
                ]
                subs = s.get("subscriptions") or []
                for sub in subs:
                    lines.append(
                        "· %s %s ~ %s（%s）"
                        % (
                            sub.get("plan_id"),
                            sub.get("start"),
                            sub.get("end"),
                            sub.get("status"),
                        )
                    )
                if not subs:
                    lines.append("· 暂无订阅行")
                send_text("\n".join(lines), message_id)
            elif low.startswith("撤销"):
                # 方案 a（2026-10-21 老田拍板）：显式撤销，store 留痕
                # reason="revoked"；ValueError（无生效行/档位不存在）
                # 照开通族转诚实回执，不臆造。
                if len(parts) < 3:
                    send_text("用法：撤销 <open_id> <档位>", message_id)
                    return
                try:
                    n = subscription_store.revoke_subscription(parts[1], parts[2])
                except ValueError as e:
                    send_text("❌ %s" % e, message_id)
                    return
                send_text(
                    "✅ 已撤销 %s 的 %s 档位（%d 行，留痕 revoked；"
                    "当日已耗用额度不回冲）" % (mask_open_id(parts[1]), parts[2], n),
                    message_id,
                )
            else:  # 订阅巡查
                swept = subscription_store.expire_sweep()
                lst = subscription_store.list_subscribers()
                if not lst.get("ok"):
                    send_text(
                        "❌ 巡查失败：%s（已清扫过期 %d 行）"
                        % (lst.get("error"), swept),
                        message_id,
                    )
                    return
                rows = lst.get("subscribers") or []
                n_active = sum(1 for x in rows if x.get("status") == "active")
                lines = [
                    "**订阅巡查** 清扫过期 %d 行，订阅者 %d 人"
                    "（生效 %d）" % (swept, lst.get("count", 0), n_active)
                ]
                for x in rows[:10]:
                    lines.append(
                        "· %s %s %s 至 %s"
                        % (
                            mask_open_id(x.get("open_id")),
                            x.get("plan_id") or "无档位",
                            x.get("status") or "-",
                            (x.get("end") or "-")[:10],
                        )
                    )
                if lst.get("count", 0) > 10:
                    lines.append("· …其余 %d 人见 8097 商务面" % (lst["count"] - 10))
                send_text("\n".join(lines), message_id)
        elif low.startswith("帮助") or low in ("help", "?", "？"):
            send_text(HELP_MD, message_id)
        elif low.startswith("选股"):
            tier = "T1"
            for t in ("T1", "T2", "全部"):
                # K-STOCK-400：在 low 里匹配——原对 text 匹配，小写
                # "选股 t2" 命不中 T2，静默回退 T1 清单。
                if t.lower() in low:
                    tier = t
            reply_card(card_picks(tier), message_id)
        elif low.startswith("详情"):
            parts = text.split()
            if len(parts) < 2:
                send_text(
                    "用法：详情 605100 [筹码|板块|公告|龙虎榜|量能|共振|缠论]",
                    message_id,
                )
                return
            code = _norm_code(parts[1])
            dimmap = {
                "筹码": "chips",
                "板块": "sectors",
                "公告": "notices",
                "龙虎榜": "lhb",
                "量能": "vol_events",
                "共振": "resonance",
                "缠论": "chanlun",
                "决策": "no_buy_reasons",
            }
            dim = dimmap.get(parts[2] if len(parts) > 2 else "缠论", "chanlun")
            f, cached = get_facts(code)
            reply_card(card_dim(code, f, dim), message_id)
        elif low.startswith(("血缘", "关联", "关系")):
            # KG 血缘关系：Neo4j 实时查询，非 facts 缓存
            parts = text.split()
            if len(parts) < 2:
                send_text(
                    "用法：血缘 605100 [60/90/180]\n· 关联 605100（别名）", message_id
                )
                return
            code = _norm_code(parts[1])
            days = 90
            if len(parts) > 2:
                try:
                    days = int(parts[2])
                except ValueError as exc:
                    logging.getLogger(__name__).warning(
                        "silent-except %s: %s", "feishu_bot_ws.py", exc
                    )
            send_text(f"🧬 {code} KG 血缘查询中（{days} 天窗口）…", message_id)
            data = _kg_stock(code, days=days)
            if not data.get("ok"):
                send_text("❌ 查询失败：%s" % data.get("error", "未知"), message_id)
                return
            reply_card(card_lineage(code, data), message_id)
        elif low.startswith("缠论"):
            parts = text.split()
            if len(parts) < 2:
                send_text("用法：缠论 605100", message_id)
                return
            code = _norm_code(parts[1])
            send_text("%s 生成中（~1 分钟，当日有缓存则秒回）…" % code, message_id)
            f, cached = get_facts(code)
            reply_card(card_main(code, f, cached), message_id)
        elif low.rstrip("。！!?？. ") in (
            "机器",
            "打开机器",
            "机器入口",
            "入口",
            "dsh",
            "远程",
        ):
            # 2026-10-25 分域：远程入口能力已迁移到零界·执行
            send_text(
                "该能力已迁移：请私聊「零界·执行」发「机器」取入口链接。", message_id
            )
        else:
            # 2026-10-25 分域：私聊短文本不再交给 DSH（已迁移零界·执行）。
            # 仍保留 W43 投喂环：P2P 兜底文本先过投喂环（身份闸/类型路由/
            # 双回执走 8097 闸门）；不消费再走兜底指路文案。
            consumed = False
            try:
                import inbound_gateway

                r = inbound_gateway.handle_inbound(
                    open_id, text, message_id, chat_type=chat_type
                )
                consumed = bool(r.get("consumed"))
            except Exception:  # noqa: BLE001
                log("inbound err " + traceback.format_exc()[-300:])
            if not consumed:
                send_text(
                    "未识别指令。我是零界·指令，发「帮助」看指令表；"
                    "自由问答请发「零界·问答」，"
                    "执行/远程/语音请私聊「零界·执行」。",
                    message_id,
                )
    except Exception as e:  # noqa: BLE001
        log("cmd err: %s" % traceback.format_exc()[-300:])
        try:
            send_text("处理失败：%s" % str(e)[:100], message_id)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "feishu_bot_ws.py", exc
            )


# ── M4 签字角色映射（2026-10-31 WO-M4-SIGNERMAP-20261031）──────────────
# 契约：agent_platform/config/approval_signers.json
#   {"map": {"ou_xxx": "A0", "ou_yyy": "QA1"}, "note": "..."}   允许空 map。
APPROVAL_SIGNERS_CONFIG = os.path.join(
    os.path.dirname(HERE), "agent_platform", "config", "approval_signers.json")
_APPROVAL_SIGNERS_CACHE = {"stamp": None, "map": {}}


def _load_approval_signers(path=None, use_cache=True):
    """读 open_id → M4 签字角色映射（QA1/GUANYU/VERIFY1/A0；只读）。

    - 缺失/坏 JSON/结构非法 → 空映射（fail-safe：审批记账不崩，角色未知 →
      approval_control 侧 roles_unresolved，**不猜**）；
    - 值归一：open_id 去空白、role 字符串 trim + 大写；非字符串/空值丢弃；
    - 缓存键 = (绝对路径, mtime_ns, size)：修改配置后下次回调自动生效，无需重启。
    """
    p = path or APPROVAL_SIGNERS_CONFIG
    try:
        st = os.stat(p)
        stamp = (os.path.abspath(p), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if use_cache and stamp is not None and _APPROVAL_SIGNERS_CACHE["stamp"] == stamp:
        return dict(_APPROVAL_SIGNERS_CACHE["map"])
    mapping = {}
    try:
        # utf-8-sig：容忍 Windows/PowerShell 写出的 UTF-8 BOM（否则 json 解析失败
        # → 空映射 → 静默 roles_unresolved，运维以为配了却永远签不齐）。
        with open(p, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("map"), dict):
        for key, role in doc["map"].items():
            op = str(key or "").strip()
            role = str(role).strip().upper() if isinstance(role, str) else ""
            if op and role:
                mapping[op] = role
    if use_cache and stamp is not None:
        _APPROVAL_SIGNERS_CACHE["stamp"] = stamp
        _APPROVAL_SIGNERS_CACHE["map"] = dict(mapping)
    return mapping


# ── ws 主程序 ───────────────────────────────────────────────
def _handle_approval_card(val, sender):
    """D3 接线：approval:* 卡片按钮 → approval_control.handle_action（只记账/回执）。

    红线：不触发重启/发布/权限变更。白名单取环境变量 APPROVAL_OPERATORS
    （逗号分隔 open_id）；未配置时记录全部操作人（pilot，审计可查）。

    2026-10-31 WO-M4-SIGNERMAP-20261031：加载 approval_signers.json 的
    open_id→角色映射并作为 ``signoff_roles`` 传给 handle_action（M4 四方判签）。
    配置缺失/坏 → 空映射；未知 open_id 不在映射内 → roles_unresolved（不猜）。
    """
    import approval_control

    try:
        signoff_roles = _load_approval_signers()
    except Exception as exc:  # noqa: BLE001 映射加载异常不得挡审批记账
        log("approval signers load error: %s" % str(exc)[:120])
        signoff_roles = {}
    allowed = str(os.environ.get("APPROVAL_OPERATORS", "")).strip()
    allowed_ops = {x for x in allowed.split(",") if x} or None
    payload = {"operator": {"open_id": sender}, "action": {"value": val}}
    return approval_control.handle_action(
        payload, allowed_ops=allowed_ops,
        owner_ids=val.get("owner_ids") if isinstance(val, dict) else None,
        signoff_roles=signoff_roles or None)


def _handle_card(val, sender, ctx=None):
    """卡片动作执行体（线程侧）：tag 分发 + 回执。
    K-STOCK-137：lark_oapi ws 是 asyncio 单循环，on_card 若同步执行
    重动作（ctl:board 子进程 180s / er:set 120s / dim: 现抓 66s）会卡死
    心跳与全部消息——对齐 on_msg 线程化判例，on_card 只解析不执行。
    K-STOCK-138：平移出 main() 闭包后 send_card 名字丢失（原由闭包捕获
    main 内局部 import），所有发卡按钮分支 NameError 被吞——函数内补局部
    import（reply_card 同款模式）。"""
    try:
        from feishu_card_v2 import send_card  # K-STOCK-138：闭包平移补名

        # 2026-11-05 FB-A 并轨：JEV 告警卡（value 带 alert_id，无 tag/action_id）
        # 不在本进程内解释，转发给 JEV 本地接入（它持有 v2_alert / gate_ignored 语义）。
        if (
            isinstance(val, dict)
            and val.get("alert_id")
            and not val.get("tag")
            and not val.get("action_id")
        ):
            fok, fwhy = _forward_jev_card(val, sender, ctx)
            act = str(val.get("action") or "?")
            if fok:
                send_text("✅ 已记录：%s" % act)
            else:
                send_text("❌ 未记录（JEV 接入不可达）：%s" % (fwhy or act))
            log(
                "card jev forward action=%s ok=%s alert=%s"
                % (act, fok, str(val.get("alert_id"))[:40])
            )
            return

        # K-PUSH-004：v3 统一注册表分发入口（value 带 action_id 字段即走
        # card_actions.dispatch：注册校验 + 审计流水 + handler 执行）。
        # 老前缀（dim:/er:/ctl:）继续走下方原链，兼容存量在飞卡。
        aid = val.get("action_id") if isinstance(val, dict) else None
        if aid:
            import card_actions

            # W40：HMAC 验签 + request_id 幂等（旧卡无签名→兼容告警；
            # CARD_SIGN_ENFORCE=1 时强制拒绝未签名旧卡）
            try:
                import card_sign as _cs

                _vok, _vwhy = _cs.verify(val)
                if not _vok:
                    if str(os.environ.get("CARD_SIGN_ENFORCE", "0")) == "1":
                        send_text("❌ 卡片校验失败：%s" % _vwhy)
                        log(
                            "card v3:%s verify_fail=%s (sender=%s)"
                            % (aid, _vwhy, sender)
                        )
                        return
                    log("card v3:%s unsigned(%s) 兼容放行" % (aid, _vwhy))
                else:
                    _gok, _gwhy = _cs.guard(val)
                    if not _gok:
                        send_text("✅ 重复操作已忽略（request_id 幂等）")
                        log("card v3:%s duplicate rid ignored" % aid)
                        return
            except Exception as _se:  # noqa: BLE001
                log("card sign guard error: %s" % str(_se)[:120])
            ok, msg = card_actions.dispatch(str(aid), val, sender)
            send_text(("✅ " if ok else "❌ ") + str(msg)[:150])
            log("card v3:%s ok=%s (sender=%s)" % (aid, ok, sender))
            return
        tag = val.get("tag", "")
        if tag.startswith("approval:"):
            res = _handle_approval_card(val, sender)
            if res.get("ok"):
                dup = "（重复点击已忽略）" if res.get("duplicate") else ""
                send_text("✅ 审批已记录：%s %s%s" % (res.get("action"), res.get("task"), dup))
            else:
                send_text("❌ 审批未记录：%s" % str(res.get("error"))[:100])
            log("card approval ok=%s action=%s" % (res.get("ok"), res.get("action")))
            return
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
            ok, why = send_card(card_er_pct(val.get("code"), val.get("kind")))
            log("card er:choice %s" % (ok or why))
        elif tag == "er:set":
            r = _er_cli(
                [
                    "set",
                    "--code",
                    str(val.get("code")),
                    "--kind",
                    str(val.get("kind")),
                    "--pct",
                    str(val.get("pct")),
                ]
            )
            send_text(
                ("✅ " if r.get("ok") else "❌ ")
                + (
                    "%s %s 线 %.2f 已设（成本 %.2f）· 触线后次日开盘清仓"
                    % (
                        r.get("stock"),
                        "止盈" if val.get("kind") == "tp" else "止损",
                        r.get("line", 0),
                        r.get("avg_cost", 0),
                    )
                    if r.get("ok")
                    else r.get("error")
                )
            )
            log("card er:set %s" % r.get("ok"))
        elif tag == "er:clear":
            # 点撤线 → 先发撤线选择卡（tp/sl 二选一，再点执行）
            code = val.get("code")
            els = [
                {"tag": "markdown", "content": "撤销 **%s** 的哪条线？" % code},
                {
                    "tag": "action",
                    "actions": [
                        _btn(
                            "撤止盈",
                            {"tag": "er:clear2", "code": code, "kind": "tp"},
                            "primary",
                        ),
                        _btn(
                            "撤止损",
                            {"tag": "er:clear2", "code": code, "kind": "sl"},
                            "danger",
                        ),
                        _btn("取消", {"tag": "er:noop"}),
                    ],
                },
            ]
            send_card(
                {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "grey",
                        "title": {"tag": "plain_text", "content": "撤线 %s" % code},
                    },
                    "elements": els,
                }
            )
        elif tag == "er:clear2":
            r = _er_cli(
                [
                    "clear",
                    "--code",
                    str(val.get("code")),
                    "--kind",
                    str(val.get("kind")),
                ]
            )
            send_text(
                ("✅ " if r.get("ok") else "❌ ") + (r.get("note") or r.get("error"))
            )
            log("card er:clear2 %s" % r.get("ok"))
        # er:noop → 无操作
        elif tag.startswith("ctl:"):
            # K-STOCK-135 控制中枢动作：白名单 → 执行 → 回执 + 重推新卡
            import feishu_control

            if not feishu_control.allowed(sender):
                send_text("无权限：控制操作仅限白名单用户。")
                log("card ctl denied (sender=%s)" % sender)
            else:
                ok, text, card2 = feishu_control.handle_ctl(val, sender)
                send_text(
                    ("✅ " if ok else "❌ ") + text
                    if not text.startswith(("✅", "❌"))
                    else text
                )
                if card2 is not None:
                    send_card(card2)
                log("card %s ok=%s" % (tag, ok))
    except Exception:  # noqa: BLE001
        log("card err " + traceback.format_exc()[-300:])


# ── ws 主程序 ───────────────────────────────────────────────
def _patch_ws_card_route():
    """K-PUSH-005 核心修复（翻 K-PUSH-004「后台订阅断」结论）：
    本 venv 的 lark_oapi ws client 收到 CARD 帧（MessageType.CARD）
    直接 `return` 静默丢弃（ws/client.py _handle_data_frame 原文
    `elif message_type == MessageType.CARD: return`）——
    register_p2_card_action_trigger 注册的 handler 永远收不到
    事件，卡片按钮零回调的真凶在 SDK，非飞书后台订阅。
    dispatcher 侧（_do_without_validation）本就完整支持
    p2.card.action.trigger 键路由 callback 派发器，天然分流，
    这里只需方法级替换 _handle_data_frame 让 CARD 与 EVENT
    同路派发（照抄原函数，仅改 CARD 分支一行语义）。"""
    import base64
    import http as _http
    import time as _time

    from lark_oapi.core.const import UTF_8
    from lark_oapi.core.json import JSON
    from lark_oapi.ws import client as _wsc
    from lark_oapi.ws.enum import MessageType
    from lark_oapi.ws.model import Response

    async def _fixed(self, frame):
        try:
            hs = frame.headers
            msg_id = _wsc._get_by_key(hs, _wsc.HEADER_MESSAGE_ID)
            trace_id = _wsc._get_by_key(hs, _wsc.HEADER_TRACE_ID)
            sum_ = int(_wsc._get_by_key(hs, _wsc.HEADER_SUM))
            seq = int(_wsc._get_by_key(hs, _wsc.HEADER_SEQ))
            type_ = _wsc._get_by_key(hs, _wsc.HEADER_TYPE)
            pl = frame.payload
            if sum_ > 1:
                pl = self._combine(msg_id, sum_, seq, pl)
                if pl is None:
                    return
            message_type = MessageType(type_)
            resp = Response(code=_http.HTTPStatus.OK)
            try:
                start = int(round(_time.time() * 1000))
                # 原版此处 CARD 分支直接 return 丢弃——唯一改动点
                if message_type in (MessageType.EVENT, MessageType.CARD):
                    result = self._event_handler._do_without_validation(pl)
                else:
                    return
                end = int(round(_time.time() * 1000))
                header = hs.add()
                header.key = _wsc.HEADER_BIZ_RT
                header.value = str(end - start)
                if result is not None:
                    resp.data = base64.b64encode(JSON.marshal(result).encode(UTF_8))
            except Exception as e:  # noqa: BLE001
                _wsc.logger.error(
                    self._fmt_log(
                        "handle message failed, message_type: {}, "
                        "message_id: {}, trace_id: {}, err: {}",
                        message_type.value,
                        msg_id,
                        trace_id,
                        e,
                    )
                )
                resp = Response(code=_http.HTTPStatus.INTERNAL_SERVER_ERROR)
            frame.payload = JSON.marshal(resp).encode(UTF_8)
            await self._write_message(frame.SerializeToString())
        except Exception as e:  # noqa: BLE001
            _wsc.logger.error(self._fmt_log("handle data frame failed, err: {}", e))

    _wsc.Client._handle_data_frame = _fixed


def next_backoff(delay, alive_secs):
    """P2.7 重连退避纯函数（便于单测）。delay=上次退避秒数（首次传 0）；
    连接稳定存活≥60s 后异常视为新断连周期，退避重置 1s；否则指数倍增
    （0→1 起），上限 60s。"""
    if alive_secs >= 60:
        return 1.0
    if delay <= 0:
        return 1.0
    return min(delay * 2.0, 60.0)


def main():
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    from lark_oapi.ws import Client

    app_id, app_secret = _creds()
    if not app_id:
        log("no app creds, exit")
        return

    def on_msg(data: P2ImMessageReceiveV1):
        try:
            msg = data.event.message
            # W43 排障插桩：非文本消息此前静默丢弃无日志，链路断无从分辨
            # 「事件未达」还是「到达被滤」——先留痕全部入口帧。
            log(
                "msg frame: type=%s chat=%s sender=%s"
                % (
                    msg.message_type,
                    msg.chat_type,
                    (getattr(data.event.sender.sender_id, "open_id", "") or "-")[:12],
                )
            )
            _heartbeat("msg")
            if msg.message_type != "text":
                # P0 语音入口：仅当 VOICE_INGEST_ENABLED=1 时才接管 audio，
                # 否则行为与改动前完全一致（继续 skip non-text）。
                if msg.message_type == "audio":
                    _handle_audio(msg, data)
                    return
                log("skip non-text: %s" % msg.message_type)
                return
            mid = msg.message_id
            content = json.loads(msg.content or "{}")
            text = content.get("text", "")
            sender_id = ""
            try:  # K-PUSH-005 修复：sender 与 message 平级（SDK 实证
                # P2ImMessageReceiveV1Data._types={"sender","message"}，
                # 正确路径 data.event.sender.sender_id.open_id）。旧代码从
                # msg.sender 取——EventMessage 无 sender 字段，
                # AttributeError 被 except 吞 → 恒空（recv 日志恒
                # sender=- 的根因，白名单被迫降级空开放的根源）。
                sender_id = data.event.sender.sender_id.open_id or ""
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "feishu_bot_ws.py", exc
                )
            # K-STOCK-299：群消息只响应 @本bot 的（content 渲染为 @_user_N
            # 占位）。双 bot 并存期，@ 新 bot 的消息是 <at user_id=ou_...>
            # 原文——一律沉默，防抢答回「没听懂」污染对方会话。
            # K-STOCK-300：<at 原文（= @ 的不是本 app）扩展到所有会话类型
            # （p2p 里误 @ 他 bot 同样沉默）。
            if "<at user_id=" in text:
                log("skip at-other-bot: %r" % text[:40])
                return
            if msg.chat_type == "group" and "@_user_" not in text:
                log("skip group not-@me: %r" % text[:40])
                return
            text = _AT_PLACEHOLDER.sub("", text).strip()
            if not text:
                return
            # K-STOCK-188：recv 行补记 open_id——F-006 白名单清单的原始
            # 积累通道（此前日志不含发送者，白名单无从收集）。
            log(
                "recv: %r (chat=%s, sender=%s)"
                % (text[:40], msg.chat_type, sender_id or "-")
            )
            threading.Thread(
                target=handle_cmd,
                args=(text, mid, sender_id, msg.chat_type),
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001
            log("msg err " + traceback.format_exc()[-300:])

    def on_card(data):
        # K-STOCK-137 薄壳：只解析事件、不执行动作（执行体=_handle_card
        # 线程侧），防 asyncio 单循环被长任务卡死心跳
        try:
            ev = getattr(data, "event", None) or {}
            action = getattr(ev, "action", None) or {}
            val = getattr(action, "value", None) or {}
            if isinstance(val, str):
                val = json.loads(val)
            # K-PUSH-005：入口留痕——此前 on_card 成功路径零日志，断链时
            # 分不清「CARD 帧未达」还是「解析后静默」。
            log(
                "card cb recv: val=%r op=%s"
                % (str(val)[:80], _card_sender_id(ev) or "-")
            )
            threading.Thread(
                target=_handle_card,
                args=(val, _card_sender_id(ev), _card_ctx(ev)),
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001
            log("card err " + traceback.format_exc()[-300:])

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_msg)
        # K-STOCK-367：app 后台订阅了已读回执（im.message.message_read_v1）
        # 但无业务消费方——不注册处理器 SDK 每条报「processor not found」
        # ERROR（10-07 起累计 1099 条纯噪音），no-op 注册消噪。
        .register_p2_im_message_message_read_v1(lambda data: None)
        .register_p2_card_action_trigger(on_card)
        .build()
    )
    cli = Client(
        app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO
    )
    _WS_CLIENT["cli"] = cli
    threading.Thread(target=_ws_guard, daemon=True).start()
    # K-PUSH-005：CARD 帧路由。P2.7 侦察结论：该 patch 是对 SDK 类
    # （lark_oapi.ws.client.Client）的一次性属性赋值（不读旧值、不叠加
    # 包装），重复调用幂等，且类级 patch 对环内重建的新 Client 实例
    # 天然生效——故环外调用一次即可，语义等价于每次 start 前已 patch。
    _patch_ws_card_route()

    # K5.1 配套（WO-FS-148-9）：派单超时巡视线程。
    # 每 5 分钟扫 bridge_inbox 中超过 deadline 的派单：首次超时回「稍等」卡，
    # 超过 15 分钟升级话术（升级动作本身仍是回卡，真正的归口由田丰侧处理）。
    # 开关 COMM_DISPATCH_ENABLED + COMM_DISPATCH_PATROL（默认 1）双闸；
    # 线程内任何异常只落日志，不影响 WS 主链（判例：辅助线程不得反噬主链）。
    def _patrol_send(open_id, text, dispatch_id, stage):
        """巡视提示直达客户：经 8097 闸门 openapi 直发（receive_id=open_id）。

        WO-FS-156 修复：原实现误用 send_text(txt, "-")——message_id="-" 会
        走「回复会话」语义，飞书必然拒发（message_id 无效），巡视提示从未
        真正送达。改为与 inbound 回卡同通道（feishu_client.send_msg），
        critical=True（超时提示不可被静默窗吞）。
        """
        try:
            import json as _j

            _nc = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "news_collector",
            )
            if _nc not in sys.path:
                sys.path.insert(0, _nc)
            from feishu_client import send_msg as _sm

            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "orange" if stage == 1 else "red",
                    "title": {"tag": "plain_text", "content": "⏱ 处理进度提醒"},
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": text}}
                ],
            }
            ok, why = _sm(
                _j.dumps(card, ensure_ascii=False),
                "interactive",
                open_id,
                receive_id_type="open_id",
                kind="comm_patrol",
                critical=True,
            )
            log(
                "巡视提示 %s stage=%d 发送 %s（%s）"
                % (dispatch_id, stage, "成功" if ok else "失败", why)
            )
        except Exception as _ex:  # noqa: BLE001
            log("巡视提示异常 %s: %s" % (dispatch_id, traceback.format_exc()[-160:]))

    def _dispatch_patrol():
        import comm_dispatch as _cd
        import time as _t

        _seen = {}  # dispatch_id -> 已提示阶段（1=稍等, 2=升级）
        _seen_approvals = set()  # W40：已升级过的待确认公告 decision_id
        while True:
            try:
                _t.sleep(300)
                _heartbeat("patrol")
                if not _cd.enabled():
                    continue
                for it in _cd.sweep_overdue():
                    stage = 2 if it.get("escalate") else 1
                    # 2026-10-25：统一走 ticket_loop.auto_nudge —— 同阶段只发一次
                    # （stage 持久化，重启不重发）；owner==发起人时只发一张合并卡；
                    # 超 24h 历史工单只提醒一次「建议关闭/重开」
                    try:
                        import ticket_loop as _tl

                        r = _tl.auto_nudge(it["dispatch_id"], stage)
                    except Exception as _ne:  # noqa: BLE001
                        log("自动催办异常 %s: %s" % (it["dispatch_id"], str(_ne)[:120]))
                        continue
                    log(
                        "派单巡视 %s stage=%d waited=%.1fmin skip=%s"
                        % (
                            it["dispatch_id"],
                            stage,
                            it.get("waited_min", 0),
                            (r or {}).get("skipped"),
                        )
                    )
                # W40：待确认公告超时升级（>30min 未审批 → owner 提醒，每决策一次）
                try:
                    import notification_decision as _ndp

                    for _ap in _ndp.stale_approvals(30):
                        _aid = str(_ap.get("id") or "")
                        if not _aid or _aid in _seen_approvals:
                            continue
                        _seen_approvals.add(_aid)
                        send_text(
                            "⏰ 待确认公告超时未审批：%s → %s（登记 %s）。"
                            "请到 8097 通知决策处理。"
                            % (
                                _aid[:8],
                                _ap.get("target_name") or "-",
                                str(_ap.get("created_at") or "")[:16],
                            )
                        )
                        log("approval escalation %s" % _aid[:8])
                except Exception:  # noqa: BLE001
                    log("approval escalation error " + traceback.format_exc()[-160:])
                # W41：死信自动补达（退避+封顶+独立流水；旧死信无 payload 跳过）
                try:
                    import redeliver as _rd

                    _rr = _rd.run_once(_rd.default_sender)
                    if _rr.get("tried"):
                        log("redeliver patrol %s" % _rr)
                except Exception:  # noqa: BLE001
                    log("redeliver error " + traceback.format_exc()[-160:])
            except Exception:  # noqa: BLE001
                log("派单巡视异常 " + traceback.format_exc()[-200:])

    if str(os.environ.get("COMM_DISPATCH_PATROL", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        threading.Thread(target=_dispatch_patrol, daemon=True).start()
        log("dispatch patrol thread started (interval=300s)")
    log("bot ws starting (app=%s)" % app_id)
    try:
        import voice_ingest as _vi

        log(
            "voice: enabled=%s provider=%s model=%s"
            % (
                _vi.enabled(),
                os.environ.get("VOICE_ASR_PROVIDER") or "none",
                os.environ.get("VOICE_ASR_MODEL") or "",
            )
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "feishu_bot_ws.py", exc
        )
    # P2.7 内部重连环：原 cli.start() 为单次阻塞调用，WS 断线=进程退出，
    # 只能靠 watchdog 每 5min 整机拉起兜底（恢复延迟最长 5min）。本环把
    # 恢复延迟降到秒级（指数退避：初始 1s 倍增上限 60s；存活≥60s 后异常
    # 视为稳定连接的新断连周期，退避重置 1s）；watchdog 整机拉起仍是
    # 兜底，保留不撤。closed on failure：断线原因落日志（traceback 尾
    # ≤200 字符）。start() 正常返回（SDK 语义上不应发生）则按原行为退出。
    delay = 0.0
    reconnect_n = 0
    while True:
        started = time.time()
        try:
            cli.start()
            log("bot ws exited cleanly")
            return  # 按现行为退出进程，watchdog 兜底路径保留
        except Exception:  # noqa: BLE001
            alive_secs = time.time() - started
            log(
                "bot ws closed on failure (alive=%.0fs): %s"
                % (alive_secs, traceback.format_exc()[-200:])
            )
            delay = next_backoff(delay, alive_secs)
            reconnect_n += 1
            log("bot ws reconnect #%d in %.1fs" % (reconnect_n, delay))
            time.sleep(delay)
            # Client 重建在环内（app_id/app_secret/handler 复用环外对象）
            cli = Client(
                app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO
            )
            _WS_CLIENT["cli"] = cli


if __name__ == "__main__":
    main()
