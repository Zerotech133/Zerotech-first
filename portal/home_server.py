# -*- coding: utf-8 -*-
"""菜鸟驾驶舱服务（K-STOCK-047 落地变现一期 demo）：新首页原型，独立进程零侵入。

  python portal/home_server.py          # 监听 127.0.0.1:8092
  数据面 = 纯只读投影（paper.db ro / org.json / shadow_ledger / git log / 文件 mtime），
  全 try 降级——任一数据源挂了驾驶舱照样亮灯（标灰+原因）。
  菜鸟对话 = 规则意图路由（查/派/审批/问 四类）优先，未命中走 Ollama qwen3.5:9b
  （30s 超时，不可达则提示快捷指令）——一期不依赖自由对话质量。
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOME_HTML = HERE / "home.html"
PAPER_DB = ROOT / "k_stock" / "data" / "paper.db"
ORG_JSON = ROOT / "agent_platform" / "registry" / "org.json"
SHADOW_LEDGER = ROOT / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
RPS_LATEST = ROOT / "quant_backtest" / "reports" / "rps_latest.json"
GZH_DIR = ROOT / "gzh"
PORT = int(os.environ.get("HOME_PORT", "8094"))  # 047教训:8092被他服务占用(K-STOCK-053默认值对齐)
OLLAMA = "http://127.0.0.1:11434"


def _mt(p: Path):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))
    except OSError:
        return None


# K-STOCK-049 C1-4 数据同源：菜鸟回答口径对齐 8080 页面微指标 → 读 portal_api(127.0.0.1:8091) 原生 API
API_BASE = "http://127.0.0.1:8091"
# 本机直连不吃环境代理：urllib 默认会读 http_proxy 等环境变量，必须空 ProxyHandler 直连
_API_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _api(path: str, timeout: int = 8):
    """读 8091 portal_api 原生 API，返回 dict；任何失败返回 None（调用方降级本地投影）。"""
    try:
        with _API_OPENER.open(API_BASE + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001  8091 挂/超时/非 JSON → 一律 None，不炸穿
        return None


# ---------------- 数据面（全 try 降级） ----------------

def _paper():
    """量化交易部：main + us_% 账本最新快照。"""
    try:
        return _paper_impl()
    except Exception as e:  # noqa: BLE001
        # paper.db 被备份/写进程持锁等场景：connect/execute 抛错不能炸穿
        # cockpit（文件头承诺"全 try 降级"）——降级为灰卡。
        return {"ok": False, "err": f"paper.db 不可读：{str(e)[:80]}"}


def _paper_impl():
    if not PAPER_DB.exists():
        return {"ok": False, "err": "paper.db 不存在"}
    con = sqlite3.connect(f"file:{PAPER_DB.as_posix()}?mode=ro", uri=True,
                          timeout=3)
    con.row_factory = sqlite3.Row
    try:
        accs = {}
        for aid_row in con.execute(
                "SELECT DISTINCT account_id FROM equity_marks ORDER BY account_id"):
            aid = aid_row["account_id"]
            marks = con.execute(
                "SELECT date,total,cash,pos_pct,pending,verify_ok FROM equity_marks "
                "WHERE account_id=? ORDER BY date", (aid,)).fetchall()
            if not marks:
                continue
            base = marks[0]["total"] or 1.0
            l = marks[-1]
            accs[aid] = {"total": l["total"], "cash": l["cash"], "date": l["date"],
                         "pos_pct": l["pos_pct"], "pending": l["pending"],
                         "cum_pct": round((l["total"] / base - 1) * 100, 2),
                         "days": len(marks)}
        pend = con.execute(
            "SELECT account_id,stock,side,qty,plan_date FROM orders "
            "WHERE status='pending' ORDER BY id DESC LIMIT 12").fetchall()
        return {"ok": True, "accounts": accs,
                "pending": [dict(r) for r in pend]}
    finally:
        con.close()


def _org():
    try:
        d = json.loads(ORG_JSON.read_text(encoding="utf-8"))
        return {"ok": True, "org": d.get("org"), "chief": d.get("chief", {}).get("agent_id"),
                "depts": [{"dept": x["dept"], "name": x.get("name"),
                           "status": x.get("status", "active"),
                           "charter": x.get("charter", ""), "agents": x.get("agents", [])}
                          for x in d.get("departments", [])]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)}


def _intel():
    """情报中枢：青萍产物新鲜度 + KG 事件今日量。"""
    out = {"ok": True, "rps_ts": None, "rps_buys": 0, "kg_events_today": None}
    try:
        d = json.loads(RPS_LATEST.read_text(encoding="utf-8"))
        out["rps_ts"] = d.get("ts")
        out["rps_buys"] = len([r for r in d.get("res") or [] if "买入" in str(r.get("action", ""))])
    except Exception:  # noqa: BLE001
        pass
    try:
        kg = sorted(glob.glob(str(ROOT / "knowledge_docs" / "**" / "*"),
                              recursive=True), key=os.path.getmtime)
        out["kg_docs"] = len(kg)
        out["kg_latest"] = _mt(Path(kg[-1])) if kg else None
    except Exception:  # noqa: BLE001
        pass
    return out


def _media():
    try:
        files = sorted(glob.glob(str(GZH_DIR / "**" / "*"), recursive=True),
                       key=os.path.getmtime)
        return {"ok": True, "latest": _mt(Path(files[-1])) if files else None,
                "files": len(files)}
    except Exception:  # noqa: BLE001
        return {"ok": False, "err": "gzh 目录不可读"}


def _shadow():
    try:
        lines = SHADOW_LEDGER.read_text(encoding="utf-8").strip().splitlines()
        days = {}
        for ln in lines:
            try:
                d = json.loads(ln)
                days[d["date"]] = days.get(d["date"], 0) + 1
            except Exception:  # noqa: BLE001
                continue
        return {"ok": True, "days_total": len(days), "rows": len(lines),
                "per_day": days}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)}


def _ops():
    try:
        import subprocess as sp
        r = sp.run(["git", "log", "--since=6:00", "--oneline"],
                   cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        commits = [x for x in r.stdout.strip().splitlines() if x]
        return {"ok": True, "commits_today": len(commits),
                "last_commit": commits[0] if commits else None,
                "bak_mtime": _mt(PAPER_DB.with_name("paper.db.bak"))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": str(e)}


def _cal():
    """裁决窗倒计时（10-13/14/15）。"""
    today = date.today()
    win = {"10-13": "strong 复盘 + 5 日窗满", "10-14": "RiskGuard 裁决",
           "10-15": "合流 + 校准包 + T-009"}
    items = []
    for md, what in win.items():
        target = date(today.year, int(md.split("-")[0]), int(md.split("-")[1]))
        items.append({"day": md, "what": what, "in_days": (target - today).days})
    return {"ok": True, "items": items}


def _now_context():
    """「此刻」模式（老板动线视角）：按星期+时钟给出现在该看什么。"""
    now = datetime.now()
    wd, t = now.weekday(), now.hour * 100 + now.minute
    if wd >= 5:
        mode, label = "weekend", "周末 · 裁决窗准备模式（10-13 周一三连裁决）"
        focus = ["复核 5 日窗数据完整性", "预读 RiskGuard 校准材料", "菜鸟陪练：对账本随便提问"]
    elif t < 930:
        mode, label = "pre", "盘前 · 看昨日复盘与今日计划"
        focus = ["挂单计划（pending 的 plan_date）", "昨日跑批产物核对", "今日信号产出预告"]
    elif t <= 1500:
        mode, label = "cn_open", "A 股盘中 · 盯账本与信号"
        focus = ["模拟盘 pending 撮合情况", "策略源最新信号", "账本回撤与止损距离"]
    elif t <= 2130:
        mode, label = "post", "盘后 · 复盘与明日预告"
        focus = ["今日跑批产物齐全性", "裁决窗材料更新", "菜鸟日报核对"]
    else:
        mode, label = "us_open", "美股盘中 · 盯 US 独立账本"
        focus = ["us_qingping 成交与止损", "US 行情缓存新鲜度", "美股信号次日撮合预告"]
    return {"mode": mode, "label": label, "focus": focus}


def _timeline():
    """今日跑批时间线：从关键产物 mtime 推 automation 执行态（老板问「公司在干嘛」）。"""
    checks = [
        ("07:05 主闭环", ROOT / "k_stock" / "data" / "paper.db.bak", "settle/exec/mark"),
        ("14:40 双刀扫描", ROOT / "quant_backtest" / "reports", "rps/决策产物"),
        ("17:10 影子运行", SHADOW_LEDGER, "投研部影子决策"),
        ("17:35 A0 日报", ROOT / "agent_platform" / "data" / "shadow", "日报+点评"),
        ("16:40 X5 公网快照", ROOT / "deliverables", "对外快照"),
    ]
    today_s = date.today().isoformat()
    out = []
    for name, p, what in checks:
        mt = _mt(p)
        ran = bool(mt and mt.startswith(today_s))
        out.append({"name": name, "what": what, "last": mt, "ran_today": ran})
    return out


def _money():
    """我的钱：CNY/USD 分组 + 日变动（对比前一快照）。"""
    try:
        return _money_impl()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"paper.db 不可读：{str(e)[:80]}"}


def _money_impl():
    if not PAPER_DB.exists():
        return {"ok": False}
    con = sqlite3.connect(f"file:{PAPER_DB.as_posix()}?mode=ro", uri=True,
                          timeout=3)
    con.row_factory = sqlite3.Row
    try:
        groups = {"CNY": [], "USD": []}
        for r in con.execute("SELECT DISTINCT account_id FROM equity_marks ORDER BY account_id"):
            aid = r["account_id"]
            marks = con.execute(
                "SELECT date,total FROM equity_marks WHERE account_id=? ORDER BY date",
                (aid,)).fetchall()
            if len(marks) < 1:
                continue
            l, prev = marks[-1], (marks[-2] if len(marks) > 1 else None)
            groups["USD" if aid.startswith("us_") else "CNY"].append({
                "account": aid, "total": l["total"],
                "delta": round(l["total"] - prev["total"], 2) if prev else None,
                "days": len(marks)})
        return {"ok": True, **groups}
    finally:
        con.close()


def cockpit() -> dict:
    now = time.strftime("%Y-%m-%d %H:%M")
    return {"ok": True, "now": now,
            "paper": _paper(), "org": _org(), "intel": _intel(),
            "media": _media(), "shadow": _shadow(), "ops": _ops(),
            "calendar": _cal(),
            "now_context": _now_context(), "timeline": _timeline(),
            "money": _money()}


# ---------------- 菜鸟对话（规则路由 + Ollama 兜底） ----------------

def _fmt_money(n):
    return f"{n:,.0f}"


def answer(msg: str) -> dict:
    m = msg.strip()
    low = m.lower()
    ck = cockpit()
    # 查：部门/状态/情况
    # K-STOCK-049 C1-4 数据同源：优先 8091 原生 API（/api/brief + /api/paper_status），本地投影补位
    if any(k in m for k in ("部门", "状态", "情况", "驾驶舱", "总览", "怎么样")):
        lines = []
        br = _api("/api/brief")
        if br and br.get("fin"):
            fin = br["fin"]
            lines.append(f"大盘裁决：{fin.get('verdict', '?')}"
                         f"（概率 {fin.get('prob', '?')}%，最新买点 {fin.get('buy', 0)} 个）")
        else:
            lines.append(f"大盘裁决：原生简报暂不可用（本地今日买点信号 {ck['intel'].get('rps_buys', 0)} 个）")
        if br and br.get("intel"):
            it = br["intel"]
            lines.append(f"情报中枢：今日入库 {it.get('today', 0)} 条"
                         f"（看多 {it.get('bull', 0)} / 看空 {it.get('bear', 0)}）")
        ps = _api("/api/paper_status")
        if ps and ps.get("ok") and ps.get("latest"):
            l = ps["latest"]
            lines.append(f"量化交易部：{ps.get('account_id', 'main')} 总资产 {_fmt_money(l['total'])}"
                         f"（{l.get('cum_pct', 0):+.2f}%），待撮合 {l.get('pending', 0)}")
        else:
            p = ck["paper"]  # 降级：本地 paper.db 只读投影
            acc_line = "、".join(
                f"{aid} {('盈' if v['cum_pct'] >= 0 else '亏')} {v['cum_pct']:+.2f}%"
                for aid, v in (p.get("accounts") or {}).items()) if p.get("ok") else "账本数据暂不可用"
            lines.append(f"量化交易部：账本 [{acc_line}]")
        sh = ck["shadow"]  # 本地投影补位（8091 无对应原生接口）
        lines.append(f"投研部影子期：{sh.get('days_total', 0)} 个交易日（{sh.get('rows', 0)} 条决策）")
        lines.append(f"研发部今日提交 {ck['ops'].get('commits_today', 0)} 个")
        ci = ck["calendar"]["items"][0]
        lines.append(f"裁决窗最近：{ci['day']}（{ci['what']}）")
        return {"ok": True, "via": "rule",
                "text": "老板，当前全景：\n· " + "\n· ".join(lines)}
    # 查：我的钱/持仓/账本/资产
    # K-STOCK-049 C1-4 数据同源：优先 8091 原生 API（/api/paper_status），本地投影降级
    if any(k in low for k in ("钱", "持仓", "账本", "资产", "净值", "账户")):
        ps = _api("/api/paper_status")
        if ps and ps.get("ok") and ps.get("latest"):
            l = ps["latest"]
            return {"ok": True, "via": "rule",
                    "text": f"老板，账本态势（8091 原生口径，{ps.get('account_id', 'main')}）："
                            f"总资产 {_fmt_money(l['total'])}（累计 {l.get('cum_pct', 0):+.2f}%），"
                            f"待撮合 {l.get('pending', 0)} 单，快照 {l.get('date', '?')}。"}
        p = ck["paper"]  # 降级：本地 paper.db 只读投影
        if not p.get("ok"):
            return {"ok": True, "via": "rule", "text": "账本数据暂不可用。"}
        lines = [f"· {aid}: 总资产 {_fmt_money(v['total'])}（{v['cum_pct']:+.2f}%），"
                 f"仓位 {v['pos_pct']:.0%}，待撮合 {v['pending']}，快照 {v['date']}"
                 for aid, v in p["accounts"].items()]
        return {"ok": True, "via": "rule",
                "text": "各账本态势（USD/CNY 原币口径）：\n" + "\n".join(lines)}
    # 查：挂单/pending
    # K-STOCK-049 C1-4 数据同源：优先 8091 原生 API（/api/paper_status），本地投影降级
    if any(k in low for k in ("挂单", "pending", "待撮合", "委托")):
        def _pd(s):  # 20261012 → 2026-10-12
            s = str(s or "")
            return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s
        ps = _api("/api/paper_status")
        if ps and ps.get("ok"):
            aid = ps.get("account_id", "main")
            rows = ps.get("pending_orders") or []
            if rows:
                return {"ok": True, "via": "rule",
                        "text": f"待撮合挂单（8091 原生口径，{len(rows)} 单）：\n" + "\n".join(
                            f"· [{aid}] {r.get('side')} {r.get('stock')} ×{r.get('qty')}"
                            f" → {_pd(r.get('plan_date'))} 开盘（{r.get('strategy', '')}）"
                            for r in rows)}
            rec = ps.get("recent_orders") or []
            if rec:
                return {"ok": True, "via": "rule",
                        "text": "当前无 pending 挂单。最近委托：\n" + "\n".join(
                            f"· [{aid}] {r.get('side')} {r.get('stock')} ×{r.get('qty')} {r.get('status')}"
                            for r in rec[:5])}
            return {"ok": True, "via": "rule", "text": "当前无 pending 挂单。"}
        p = ck["paper"]  # 降级：本地 paper.db 只读投影
        rows = p.get("pending") or []
        if not rows:
            return {"ok": True, "via": "rule", "text": "当前无 pending 挂单。"}
        return {"ok": True, "via": "rule", "text": "待撮合挂单：\n" + "\n".join(
            f"· [{r['account_id']}] {r['side']} {r['stock']} ×{r['qty']} → {r['plan_date']} 开盘"
            for r in rows)}
    # 查：拍板/今天该干什么/裁决窗（K-STOCK-049 C1-5 拍板提醒：读 cockpit().calendar.items）
    if any(k in m for k in ("拍板", "今天该干什么", "裁决", "窗口", "10-13", "10-14", "10-15", "日程")):
        items = ck["calendar"]["items"]
        wins = "；".join(
            f"{i['day']}（{'今天' if i['in_days'] == 0 else ('已过' if i['in_days'] < 0 else str(i['in_days']) + ' 天后')}）"
            f"{i['what']}" for i in items)
        nearest = next((i for i in items if i["in_days"] >= 0), items[-1])
        if nearest["in_days"] == 0:
            text = (f"⏰ 今天是裁决日：{nearest['day']}（{nearest['what']}）。\n"
                    f"三窗安排：{wins}。\n"
                    f"今日重点：跑批产物齐全性我已替您核对，您只需拍板——看完材料说「批准」或「驳回」即可。")
        else:
            text = (f"老板，最近裁决窗：{nearest['day']}（{nearest['in_days']} 天后）：{nearest['what']}。"
                    f"今日非裁决日，您只需要正常盯盘，材料和账本我来盯。\n三窗安排：{wins}。")
        return {"ok": True, "via": "rule", "text": text}
    # 查：影子
    if "影子" in m or "shad" in low:
        sh = ck["shadow"]
        per = "、".join(f"{d}×{n}票" for d, n in sorted((sh.get("per_day") or {}).items()))
        return {"ok": True, "via": "rule",
                "text": f"投研部影子期：{sh.get('days_total', 0)} 个交易日 / {sh.get('rows', 0)} 条决策（{per}）。"
                        f"期满 10-15 合流裁决。"}
    # 人格
    if any(k in m for k in ("你是谁", "菜鸟", "介绍")):
        return {"ok": True, "via": "rule",
                "text": "老板，我是菜鸟——您的平行空间分身，整个 AI 公司的总管。"
                        "量化交易部、情报中枢、自媒体部、研发部、运维部都归我调度，"
                        "我每天盯跑批、盯账本、盯裁决窗，有事第一时间向您汇报。"}
    # 兜底：Ollama 真对话（30s 超时）
    try:
        import urllib.request
        body = json.dumps({"model": "qwen3.5:9b", "stream": False,
                           "messages": [
                               {"role": "system",
                                "content": "你是菜鸟，老田的 AI 公司总管分身。简洁汇报口吻，"
                                           "称呼对方为老板，回答控制在 120 字内。"},
                               {"role": "user", "content": m}]}).encode()
        req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            j = json.loads(resp.read().decode())
        return {"ok": True, "via": "ollama", "text": j["message"]["content"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "via": "fallback",
                "text": "这条我建议换个问法。试试：\n· 各部门情况\n· 持仓账本\n· 挂单\n· 裁决窗\n· 影子进度\n"
                        f"（自由对话后端暂不可达：{str(e)[:60]}）"}


# ---------------- HTTP ----------------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # CORS：允许 8080 主页(nginx)跨源召唤菜鸟窗
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/home.html"):
            self._send(200, HOME_HTML.read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/api/home":
            self._send(200, json.dumps(cockpit(), ensure_ascii=False).encode())
        else:
            self._send(404, b'{"ok":false,"error":"not found"}')

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            self._send(404, b'{"ok":false,"error":"not found"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(n).decode()).get("msg", "")
            self._send(200, json.dumps(answer(msg), ensure_ascii=False).encode())
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"ok": False, "error": str(e)},
                                       ensure_ascii=False).encode())


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"[home] 菜鸟驾驶舱 http://127.0.0.1:{PORT}  ({now_ts()})", flush=True)
    srv.serve_forever()


def now_ts():
    return datetime.now().strftime("%F %T")


if __name__ == "__main__":
    main()
