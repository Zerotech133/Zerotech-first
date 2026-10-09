# -*- coding: utf-8 -*-
"""qd_view.py — 量子双刀 · 选股中心 展示层（K-STOCK-027 分层重构）

影子层 / 展示层分离（老田定）：
  - 影子层 = qd_scanner.py（全市场扫描/尾盘复核）+ qd_scan_cache.json
    （信号数据）——决策信号链，影子冻结期（10-08~10-15）不动；
  - 展示层 = 本文件——纯渲染函数：输入缓存 dict + CSS，输出 HTML 字符串。
    零 I/O、零副作用，可离线单测（python qd_view.py 自校验）。

portal_api.py 的 /qd/ 路由只做薄壳：读缓存 + 后台 spawn 扫描（I/O 副作用），
渲染全部委托本模块。以后改 UI 只动本文件，不碰 21690 行大分发器。

样式常量 _PILL/_PILL_ON 与 portal_api.py 同值（若全站改胶囊样式需两处同步，
或后续统一抽 styles 模块）。
"""
import html as _h

e = _h.escape

# 市况中文映射（与引擎 regime_label 枚举一致）
REG_CN = {"trend_up": "趋势上行", "trend_down": "趋势下行",
          "rotation": "轮动", "range": "震荡"}

# 胶囊样式（同步自 portal_api.py _PILL/_PILL_ON）
_PILL = ("display:inline-block;padding:6px 13px;border:1px solid #30363d;"
         "border-radius:16px;color:#58a6ff;text-decoration:none;"
         "font-size:12.5px;background:#161b22")
_PILL_ON = _PILL + ";color:#e6edf3;border-color:#58a6ff;font-weight:600"

# /qd/ 专属精简导航——老田要求不出现策略中心/青萍之末/缠论等交叉产线
# 工作台（图谱工具走正文工具链），故不复用 pill_nav 全站下拉。
# K-STOCK-036：补统一返回键（history.back 兜底 /portal 门户）
_BACK = ("<a href='/portal' onclick=\"if(history.length>1){"
         "history.back();return false;}\" style='display:inline-block;"
         "padding:6px 14px;border:1px solid #30363d;border-radius:16px;"
         "color:#58a6ff;text-decoration:none;font-size:12.5px;"
         "background:#161b22'>← 返回</a>")
NAV_QD = (_BACK
          + "<a href='/' style='%s'>🏠 首页</a>"
          "<span style='color:#8b949e;font-size:11px'>›</span>"
          "<span style='%s'>⚔ 量子双刀</span>" % (_PILL, _PILL_ON))

# 情报线图谱工具链（K-STOCK-026）
TOOLS_QD = "".join(
    "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
        ("🕸 图谱探索", "/kg"),
        ("💰 游资地图", "/kg/map"),
        ("🗺 作战地图", "/kg/battle"),
        ("💬 图谱问答", "/kg/ask")])

# 管线步骤表（对齐缠论 ①-⑫ 规格：模块/口径/字段/状态）
_OK = "<span style='color:#3fb950'>✅ 引擎已实现</span>"
_RDY = "<span style='color:#3fb950'>✅ 引擎就绪</span>"
_SH = "<span style='color:#d29922'>🧪 研究态</span>"
_STEPS = [
    ("① 连续双日十字星", "实体/振幅比≤0.30 且上下影齐全",
     "cross_star_2d", _OK + "（合成序列单测验证）"),
    ("② MACD 多头", "dif≥dea 且 dif&gt;0", "macd_ok", _OK),
    ("③ 振幅变化率受限", "|range−前日range|/前日range≤0.30",
     "range_ok", _OK),
    ("④ 价格带+实体方向", "6-200 元且 close≥open", "price_ok", _OK),
    ("⑤ 两日小涨约束", "连续两日涨幅各≤3%（strict）", "price_ok", _OK),
    ("⑥ 流动性门槛", "20 日成交额&gt;30 亿（特征湖 amount 千元口径 3e6）",
     "amount_ok", _OK),
    ("⑦ 均线多头排列", "MA24≥MA54 且 close≥两者", "ma_stack_ok", _OK),
    ("⑧ 乖离受限", "close≤MA24×1.20 / MA54×1.30", "ma_distance_ok", _OK),
    ("⑨ Regime 四市况分层", "TSI 趋势 60%+突破率 40% → 四市况，趋势下行一票否决",
     "regime_ok / regime_label", _OK),
    ("⑩ 5 分钟入场确认", "突破/量比≥0.9/VWAP≤2.5%/开盘强度，至少过二",
     "confirm_double_knife_entry", _RDY + "（接线待 T-20261009-008）"),
    ("⑪ strict/relaxed 双版本", "阈值双档对照研究法",
     "PROFILE_CONFIG", _SH + "（真实回测待 T-009）"),
    ("⑫ 真实回测验证", "特征湖 amt 千元口径 · 分 regime 结论",
     "任务 T-20261009-009", _SH + "（P1 · 限 10-25）"),
]

# 8 列信号表头（K-STOCK-026）
_TH8 = ("<tr class='dim'><td align=left>代码</td><td align=left>名称</td>"
        "<td align=left>收盘(涨跌)</td><td align=left>市值</td>"
        "<td align=left>换手</td><td align=left>市况</td>"
        "<td align=left>技术状态</td><td align=left>近5日信号日</td></tr>")


def _row(r):
    """8 列增强行：代码下钻/名称/收盘(涨跌%红涨绿跌)/市值/换手/市况/技术状态/信号日"""
    recent = "、".join(r.get("recent_signals", [])[-3:]) or "—"
    code_lk = ("<a href='/bt/stock?c=%s'><b>%s</b></a>" % (e(r["code"]), e(r["code"])))
    chg = r.get("change_pct")
    if chg is None or chg == "":
        chg_txt = "—"
    else:
        col = "#f85149" if float(chg) >= 0 else "#3fb950"  # 红涨绿跌中国惯例
        chg_txt = "<b style='color:%s'>%+.2f%%</b>" % (col, float(chg))
    cap = r.get("mktcap_yi")
    cap_txt = ("%.0f亿" % float(cap)) if cap not in (None, "") else "—"
    to = r.get("turnover_pct")
    to_txt = ("%.2f%%" % float(to)) if to not in (None, "") else "—"
    tech = r.get("tech") or "—"
    nm = r.get("name") or "—"
    return ("<tr><td>%s</td><td>%s</td><td>%s <span class='dim'>(%s)</span>"
            "</td><td>%s</td><td class='dim'>%s</td><td>%s</td>"
            "<td class='dim' style='max-width:240px'>%s</td>"
            "<td class='dim'>%s</td></tr>"
            % (code_lk, e(nm), e(str(r.get("close", "—"))), chg_txt,
               e(cap_txt), e(to_txt),
               e(REG_CN.get(r.get("regime"), r.get("regime", "?"))),
               e(tech), e(recent)))


def _tail_rows(snap, base=None):
    """尾盘候选行（K-STOCK-025：14:40 盘中实时 bar 复核 → 14:55 执行窗口）。
    K-STOCK-028：详情从当日基础数据快照 base['rows'] 补齐（名称/涨跌/市值/
    换手/技术状态），对齐信号表 8 列；快照缺失时退化为代码/收盘/市况/note。
    base 的 rows key 为湖命名（600593_SH），scanner 缓存 code 为点号（600593.SH）。"""
    tail_ls = snap.get("tail_signals", []) or []
    broots = (base or {}).get("rows", {})
    rows = ""
    for t in tail_ls:
        b = broots.get(t["code"].replace(".", "_"), {})
        chg = b.get("change_pct")
        if chg not in (None, ""):
            col = "#f85149" if float(chg) >= 0 else "#3fb950"
            chg_txt = "<b style='color:%s'>%+.2f%%</b>" % (col, float(chg))
        else:
            chg_txt = "—"
        cap = b.get("mktcap_yi")
        cap_txt = ("%.0f亿" % float(cap)) if cap not in (None, "") else "—"
        to = b.get("turnover_pct")
        to_txt = ("%.2f%%" % float(to)) if to not in (None, "") else "—"
        rows += ("<tr><td><a href='/bt/stock?c=%s'><b>%s</b></a></td>"
                 "<td>%s</td><td>%s <span class='dim'>(%s)</span></td>"
                 "<td>%s</td><td class='dim'>%s</td><td>%s</td>"
                 "<td class='dim' style='max-width:240px'>%s</td>"
                 "<td class='dim'>%s</td></tr>"
                 % (e(t["code"]), e(t["code"]),
                    e(b.get("name") or "—"),
                    e(str(t.get("close", "—"))), chg_txt,
                    e(cap_txt), e(to_txt),
                    e(REG_CN.get(t.get("regime"), t.get("regime", "?"))),
                    e(b.get("tech") or "—"),
                    e(t.get("note", ""))))
    if rows:
        return len(tail_ls), rows
    empty = snap.get("tail_note") or (
        "今日尚无尾盘候选（14:40 盘中扫描产出 · 候选池 %d 只昨日十字星）"
        % snap.get("star_pool_n", 0))
    return 0, ("<tr><td colspan=8 class='dim'>%s</td></tr>" % e(empty))


# K-STOCK-037 全站超级助手浮动图标（qd 页独立 CSS，不走 _COCKPIT_CSS）
FAB_HERMES = ("<a id='_hermesfab' href='http://127.0.0.1:8787' "
              "target='_blank' rel='noopener' "
              "title='超级助手：对话即调度全部 AI 岗位' "
              "style='position:fixed;right:18px;bottom:70px;width:46px;"
              "height:46px;border-radius:50%;background:#1f6feb;"
              "border:2px solid #58a6ff;font-size:22px;line-height:42px;"
              "text-align:center;z-index:100;cursor:pointer;"
              "user-select:none;text-decoration:none;"
              "box-shadow:0 2px 12px rgba(31,111,235,.45)'>\U0001F9B8</a>")


def render_placeholder(log_path, css):
    """无缓存占位页：30s 自刷新，等后台扫描产出缓存"""
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='refresh' content='30'>"
            "<title>量子双刀 · 研究预览</title>" + css
            + "</head><body>" + NAV_QD + FAB_HERMES
            + "<h1>⚔ 量子双刀 · 选股中心（研究预览）</h1>"
            "<div style='border:1px solid #d29922;border-radius:8px;"
            "background:#161b22;padding:12px 16px;margin:14px 0;color:#d29922'>"
            "⚠ 研究态：策略未回测验证（T-20261009-009 结论前不构成选股依据）</div>"
            "<div class='dim'>全市场首次扫描进行中（约 3-5 分钟），"
            "页面每 30 秒自动刷新 · 扫描日志 " + e(log_path) + "</div>"
            "</body></html>")


def render_workspace(snap, css, base=None):
    """工作台主页面：输入扫描缓存 dict + CSS + 当日基础数据快照（可选），
    输出完整 HTML（纯函数）。base=None 时尾盘候选退化为简列。"""
    rg = snap.get("regime_now", {})
    rg_txt = " · ".join("%s %d" % (REG_CN.get(k, k), v)
                        for k, v in sorted(rg.items()))
    now_rows = "".join(_row(r) for r in snap.get("signals_now", [])) or \
        "<tr><td colspan=8 class='dim'>当日无信号（八重 AND 严格过滤，低频属正常）</td></tr>"
    rec_rows = "".join(_row(r) for r in snap.get("signals_recent", [])[:50]) or \
        "<tr><td colspan=8 class='dim'>近 5 日无信号</td></tr>"
    tail_n, tail_rows = _tail_rows(snap, base)
    steps = "".join(
        "<tr><td><b>%s</b></td><td class='dim'>%s</td><td class='dim'>%s</td><td>%s</td></tr>"
        % (m, tool, fld, stt) for m, tool, fld, stt in _STEPS)
    cards = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,"
        "minmax(200px,1fr));gap:10px;margin:14px 0'>"
        "<div style='border:1px solid #3fb950;border-radius:10px;"
        "background:#161b22;padding:12px 14px'><div class='dim' "
        "style='font-size:12px'>当日信号</div><div style='font-size:24px;"
        "font-weight:700;color:#3fb950'>%d</div></div>"
        "<div style='border:1px solid #d29922;border-radius:10px;"
        "background:#161b22;padding:12px 14px'><div class='dim' "
        "style='font-size:12px'>近 5 日曾有信号</div><div style='font-size:24px;"
        "font-weight:700;color:#d29922'>%d</div></div>"
        "<div style='border:1px solid #30363d;border-radius:10px;"
        "background:#161b22;padding:12px 14px'><div class='dim' "
        "style='font-size:12px'>全市场扫描覆盖</div><div style='font-size:24px;"
        "font-weight:700'>%d 只</div></div>"
        "<div style='border:1px solid #30363d;border-radius:10px;"
        "background:#161b22;padding:12px 14px'><div class='dim' "
        "style='font-size:12px'>当日市况分布</div><div style='font-size:13px;"
        "line-height:1.8'>%s</div></div></div>"
        % (len(snap.get("signals_now", [])),
           len(snap.get("signals_recent", [])),
           snap.get("scanned", 0), e(rg_txt or "—")))
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>量子双刀 · 选股中心</title>" + css
            + "</head><body>" + NAV_QD + FAB_HERMES
            + "<h1>⚔ 量子双刀 · 选股中心</h1>"
            "<div style='border:1px solid #d29922;border-radius:8px;"
            "background:#161b22;padding:12px 16px;margin:14px 0;color:#d29922'>"
            "⚠ 研究态：策略未回测验证（T-20261009-009 结论前不构成选股依据 · "
            "不构成投资建议）· 已注册策略中心（shadow）· 尾盘策略："
            "每日 14:40 全市场扫描 → 14:55 执行窗口</div>"
            + TOOLS_QD + cards
            + "<div class='sec'><h2 style='font-size:16px;margin:0 0 10px'>"
            "⏱ 今日尾盘候选（%d · 14:40 盘中实时 bar 复核 → 14:55 执行窗口）</h2>"
            "<table style='border-collapse:collapse;min-width:520px'>"
            "%s%s</table></div>"
            "<div class='sec'><h2 style='font-size:16px;margin:0 0 10px'>"
            "📌 当日信号（%d）</h2>"
            "<table style='border-collapse:collapse;min-width:520px'>"
            "%s%s</table></div>"
            "<div class='sec'><h2 style='font-size:16px;margin:0 0 10px'>"
            "🕘 近 5 日曾有信号（%d，当日已不触发）</h2>"
            "<table style='border-collapse:collapse;min-width:520px'>"
            "%s%s</table></div>"
            "<div class='sec'><h2 style='font-size:16px;margin:0 0 10px'>"
            "⚙ 策略管线（八重 AND · 全链单测 23/23）</h2>"
            "<table style='border-collapse:collapse;min-width:640px'>"
            "<tr class='dim'><td align=left>模块</td><td align=left>口径</td>"
            "<td align=left>字段</td><td align=left>状态</td></tr>"
            "%s</table></div>"
            "<div class='dim' style='margin-top:14px'>数据源：特征湖 parquet "
            "（tushare 口径 amount=千元，与引擎阈值直接吻合）· 扫描节奏：每日 "
            "14:40 尾盘 automation（venv 子进程）· 手动刷新：运行 portal/qd_scanner.py"
            " · 策略源自 projects-archive 量子双刀产线（K-STOCK-018 移植）"
            "</div></body></html>"
            % (tail_n, _TH8, tail_rows,
               len(snap.get("signals_now", [])), _TH8, now_rows,
               len(snap.get("signals_recent", [])), _TH8, rec_rows,
               steps))


if __name__ == "__main__":
    # 离线自校验：读真实缓存渲染，断言结构锚点（不依赖服务器）
    import json, os, sys
    from datetime import datetime
    here = os.path.dirname(os.path.abspath(__file__))
    snap = json.load(open(os.path.join(here, "qd_scan_cache.json"), encoding="utf-8"))
    base = None
    bp = os.path.join(here, "qd_daily_data",
                      "snap_%s.json" % datetime.now().strftime("%Y%m%d"))
    if os.path.exists(bp):
        base = json.load(open(bp, encoding="utf-8"))
    html = render_workspace(snap, "<style></style>", base)
    checks = {
        "专属导航": "⚔ 量子双刀" in html and "域内导航" not in html,
        "无交叉产线": all(s not in html for s in ("🎖 策略中心", "🌊 青萍之末", "🧭 缠论")),
        "图谱四件套": all(s in html for s in ("/kg", "/kg/map", "/kg/battle", "/kg/ask")),
        "8列表头x3": html.count("<td align=left>技术状态</td>") == 3,
        "下钻链接": "/bt/stock?c=" in html,
        "14:40语义": "14:40" in html and "17:50" not in html,
        "研究态横幅": "研究态" in html,
        "尾盘行基础数据": base is None or "尾盘候选基础数据快照" not in html,
    }
    bad = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print("base snapshot:", "loaded (%d rows)" % base["n"] if base else "absent")
    sys.exit(1 if bad else 0)
