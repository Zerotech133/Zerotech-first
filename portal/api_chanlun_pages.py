# -*- coding: utf-8 -*-
"""P4 批 5：缠论页面 Mixin（chanlun 页/标注/PNG/池族）。
Handler 壳经基座多继承并入（禁 import *）。
"""
import json
import logging
import os
import sys
from api_common import (
    _read_json,
    _env_market_key,
    pill_nav,
    STATION,
    QBT_DIR,
    REPORTS_DIR,
    _sched_rc_html, MKT_LABEL, _pool_bear_news, run)

from api_chanlun_sl_tp import _ChanlunSlTpMixin  # A5FROZEN：止损/止盈与自动线拆出


def _chanlun_pool_push(rec):
    """工作台手动推池：缠论分析台检出信号 → 缠论信号池（CN/US/HK）。

    复用 chanlun_scan.push_manual（与每日扫描同款增强管线 + 三层幂等，
    source='manual'，进程锁与扫描互斥）。body: {code, type?, dt?, market?}。
    """
    try:
        import sys as _sp
        if QBT_DIR not in _sp.path:
            _sp.path.insert(0, QBT_DIR)
        from chanlun_scan import push_manual
        mkt = str(rec.get("market") or "CN").strip().upper() or "CN"
        return push_manual(
            str(rec.get("code") or "").strip(),
            sig_type=str(rec.get("type") or "").strip() or None,
            sig_dt=str(rec.get("dt") or "").strip() or None,
            market=mkt)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}



class Handler(_ChanlunSlTpMixin):  # Mixin 壳：经基座多继承并入
        # ── 缠论预警池（全A股扫描 → 模拟交易，影子观察） ──
    # ── K72 缠论当日结构图：PNG 服务 + 预警池展示区块 ──
    _ANNO_PATH = os.path.join(STATION, "quant_backtest", "data",
                              "chanlun_annotations.json")


    # ── L2 策略工作台：缠论 · 结构交易（筹备中占位页） ──
    def _chanlun_page(self):
        steps = "".join(
            "<tr><td><b>%s</b></td><td>%s</td><td class='dim'>%s</td><td>%s</td></tr>"
            % (m, tool, fld, stt)
            for m, tool, fld, stt in [
                ("① 三级结构识别",
                 "<a href='/chanlun-png?c=600519.SH&p=1d'>结构图 PNG</a> · "
                 "本页查看器叠加",
                 "fractals / bis / segments / pivots",
                 "<span style='color:#3fb950'>✅ 已上线</span>（分型→笔→线段"
                 "→中枢，2026-09-26）"),
                ("② 三类买卖点",
                 "本页查看器圆标 · <a href='/bt-chanlun-pool'>当日信号池</a>",
                 "signals[].type",
                 "<span style='color:#3fb950'>✅ 已上线</span>（1/2/3 类简化"
                 "口径，全类型入池）"),
                ("③ 背驰 · 盘整（P5）",
                 "本页样本统计面板 P5 列",
                 "bei / fv",
                 "🧪 影子标注 · 不拦截（回踩笔力度弱于离开笔）"),
                ("④ 背驰 · MACD（P6-a）",
                 "本页样本统计面板 P6-a 列",
                 "mbei / mfv",
                 "🧪 影子（仅 1买/1卖，DIF 反向收敛简化口径）"),
                ("⑤ 背驰 · 同向腿（P6-b）",
                 "本页样本统计面板 P6-b 列",
                 "bei2 / fv2",
                 "🧪 影子（正宗 a+A+b 同向力度衰竭）"),
                ("⑥ 背驰 · 趋势（P6-c）",
                 "<a href='/bt-tbe'>tbe/pbe 分层报表</a>",
                 "tbe / tfv / npv",
                 "🧪 影子（a+A+b+B+c 双中枢完整版，仅 1买/1卖）"),
                ("⑦ 中枢演化 + 置信度",
                 "gray v4 灰度层（推池行自动附全字段）",
                 "div_area / fv_area / pv_state / pv_dir / pv_fv / conf / "
                 "n_prior",
                 "🧪 影子（双口径背驰 + 中枢演化 + 置信度融合）"),
                ("⑧ 多级别联立（P7）",
                 "本页样本统计面板 P7 列 · 查看器次✓",
                 "mt / mtv",
                 "🧪 影子（日线×60m 共振，大级别定位次级别择时）"),
                ("⑨ 资金 / 大盘（P8）",
                 "本页样本统计面板 P8 列 · 查看器量✓/势↑↓",
                 "vv / reg",
                 "🧪 影子（缩量配合 + MA20/60 大盘顺风）"),
                ("⑩ 复合叠加（P9）",
                 "本页复合叠加面板",
                 "comp 规则表（chanlun_composite.py）",
                 "🧪 影子预研（全因子同时成立，按日对照）"),
                ("⑪ MLF 质量分",
                 "池行 mls 字段（chanlun_mlf）",
                 "mls",
                 "🧪 影子打分（LightGBM，不拦截）"),
                ("⑫ 手动推池",
                 "本页信号清单「⇪ 推池」按钮",
                 "pool[source=manual]",
                 "<span style='color:#3fb950'>✅ 新上线</span>（与扫描同款"
                 "增强管线 · 三层幂等 · 进程锁互斥）"),
            ])
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🛟 当日信号池", "/bt-chanlun-pool"),
                ("🌊 青萍之末工作台", "/bt-qpzm"),
                ("🧪 基础策略库", "/bt"),
                ("🚦 明日信号", "/bt-signals")])
        tools += (" <a class='tp' id='ck-png' "
                  "href='/chanlun-png?c=600519.SH&p=1d'>📈 结构图 PNG</a>"
                  " <a class='tp' id='ck-anno' href='/chanlun-anno-report'>"
                  "✍️ 标注报告</a>"
                  " <a class='tp' href='/kg/card'>🃏 推演卡</a>")

        # ── 样本统计面板（读每日样本任务写出的重放评估缓存） ──
        ev = _read_json(os.path.join(
            STATION, "quant_backtest", "data", "chanlun_eval_cache.json"))
        comp_panel = ""
        if ev.get("types"):

            def _c(v, suf="%"):
                return "—" if v is None else ("%s%s" % (v, suf))

            rows_h = ""
            for t, d in ev["types"].items():
                det = d.get("detect") or {}
                sl = d.get("slip") or {}
                th = d.get("theo") or {}
                ex = d.get("exe") or {}
                fl = d.get("flt")
                if fl:
                    fe = fl.get("exe") or {}
                    fl_h = ("✓%s/%s · n=%s · %s%% · %s%%"
                            % (fl.get("bei_n", "—"), fl.get("detect_n", "—"),
                               fe.get("n", "—"), fe.get("win", "—"),
                               fe.get("avg", "—")))
                else:
                    fl_h = "—"
                fl2 = d.get("flt2")
                if fl2:
                    f2e = fl2.get("exe") or {}
                    fl2_h = ("盘✓%s/%s · n=%s · %s%% · %s%%"
                             % (fl2.get("bei_n", "—"), fl2.get("detect_n", "—"),
                                f2e.get("n", "—"), f2e.get("win", "—"),
                                f2e.get("avg", "—")))
                else:
                    fl2_h = "—"
                fl3 = d.get("flt3")
                if fl3:
                    f3e = fl3.get("exe") or {}
                    fl3_h = ("M✓%s/%s · n=%s · %s%% · %s%%"
                             % (fl3.get("bei_n", "—"), fl3.get("detect_n", "—"),
                                f3e.get("n", "—"), f3e.get("win", "—"),
                                f3e.get("avg", "—")))
                else:
                    fl3_h = "—"
                fl4 = d.get("flt4")
                if fl4 and fl4.get("cov_n"):
                    f4e = fl4.get("exe") or {}
                    fl4_h = ("次✓%s/覆盖%s · n=%s · %s%% · %s%%"
                             % (fl4.get("bei_n", "—"), fl4.get("cov_n", "—"),
                                f4e.get("n", "—"), f4e.get("win", "—"),
                                f4e.get("avg", "—")))
                else:
                    fl4_h = "<span class='dim'>待分钟数据积累</span>"
                fl5 = d.get("flt5")
                if fl5 and fl5.get("cov_n"):
                    f5e = fl5.get("exe") or {}
                    fl5_h = ("量✓%s/覆盖%s · n=%s · %s%% · %s%%"
                             % (fl5.get("bei_n", "—"), fl5.get("cov_n", "—"),
                                f5e.get("n", "—"), f5e.get("win", "—"),
                                f5e.get("avg", "—")))
                else:
                    fl5_h = "<span class='dim'>待量能数据</span>"
                fl6 = d.get("flt6")
                if fl6 and fl6.get("cov_n"):
                    f6e = fl6.get("exe") or {}
                    fl6_h = ("势顺%s/覆盖%s · n=%s · %s%% · %s%%"
                             % (fl6.get("bei_n", "—"), fl6.get("cov_n", "—"),
                                f6e.get("n", "—"), f6e.get("win", "—"),
                                f6e.get("avg", "—")))
                else:
                    fl6_h = "<span class='dim'>待指数数据</span>"
                fl7 = d.get("flt7")
                if fl7 and fl7.get("cov_n"):
                    f7e = fl7.get("exe") or {}
                    fl7_h = ("趋✓%s/覆盖%s · n=%s · %s%% · %s%%"
                             % (fl7.get("bei_n", "—"), fl7.get("cov_n", "—"),
                                f7e.get("n", "—"), f7e.get("win", "—"),
                                f7e.get("avg", "—")))
                else:
                    fl7_h = "<span class='dim'>待双中枢结构积累</span>"
                fl8 = d.get("flt8")
                if fl8 and fl8.get("cov_n"):
                    f8e = fl8.get("exe") or {}
                    fl8_h = ("牛✓%s/覆盖%s · n=%s · %s%% · %s%%"
                             % (fl8.get("bei_n", "—"), fl8.get("cov_n", "—"),
                                f8e.get("n", "—"), f8e.get("win", "—"),
                                f8e.get("avg", "—")))
                else:
                    fl8_h = "<span class='dim'>待MA200积累</span>"
                avg = ex.get("avg")
                avg_h = ("—" if avg is None else
                         "<span style='color:%s'>%s%%</span>"
                         % ("#f85149" if avg > 0 else "#3fb950", avg))
                rows_h += (
                    "<tr><td><b>%s</b></td><td>%s/%s</td><td>%s</td>"
                    "<td>%s</td><td>n=%s · %s%% · %s%%</td>"
                    "<td>n=%s · %s%% · %s（中位%s%%）</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td></tr>"
                    % (t, _c(det.get("n"), ""), d.get("n", "—"),
                       _c(det.get("lag_med"), "根"), _c(sl.get("avg")),
                       th.get("n", "—"), th.get("win", "—"),
                       th.get("avg", "—"),
                       ex.get("n", "—"), ex.get("win", "—"), avg_h,
                       ex.get("med", "—"),
                       fl_h, fl2_h, fl3_h, fl4_h, fl5_h, fl6_h, fl7_h, fl8_h,
                       "<span style='color:#3fb950'>达标</span>"
                       if d.get("pass") else
                       "<span style='color:#d29922'>未达标</span>"))
            ev_panel = (
                "<div class='sec'><details><summary style='cursor:pointer;"
                "font-weight:600'>📉 样本统计 · 可成交口径"
                "（确认滞后重放）<span class='dim' style='font-size:12px;"
                "font-weight:400'>（P5~P9 影子过滤逐类对照 · 点击展开）"
                "</span></summary>"
                "<table style='margin-top:10px'><tr><th>类型</th><th>检出/样本</th>"
                "<th>确认滞后中位</th><th>确认时偏离</th>"
                "<th>理论价 r20</th><th>可成交 r20</th>"
                "<th>P5 回踩背驰后 r20</th><th>P6-b 同向腿背驰后 r20</th>"
                "<th>P6-a MACD背驰后 r20</th><th>P7 次级别(60m)共振 r20</th>"
                "<th>P8-a 量能缩量后 r20</th><th>P8-b 大盘顺风后 r20</th>"
                "<th>P6-c 趋势背驰(双中枢)后 r20</th>"
                "<th>P9 长周期牛熊(close>MA200)后 r20</th>"
                "<th>达标线</th></tr>"
                + rows_h + "</table>"
                "<div class='dim' style='font-size:12px;line-height:2'>"
                "口径：截断重放找信号最早可检出时点（lag），以检测窗末根"
                "收盘为可成交基准重算方向收益；理论价口径 win 偏高系确认"
                "滞后未来函数假象。达标线 = 可成交 n≥10 且方向均值不反转。"
                "三列力度过滤均为影子标注、<b>只标注不拦截</b>：P5 ✓=回踩笔力度"
                "弱于离开笔；P6-b 盘✓=本次回踩/反抽弱于离开笔之前最近同向"
                "腿（教学 a+A+b 同向力度衰竭）；P6-a M✓=1类信号 MACD 背驰"
                "（价创新低/高而 DIF 反向收敛，仅 1买/1卖）。P7「次✓」=日线"
                "信号终结笔窗口内，60m 出现同向 1类+MACD 背驰（多级别共振，"
                "教学「大级别定位、次级别择时」）；分钟存量短（US 约 08-18、"
                "HK 约 09-02 起），窗口未覆盖记 None 不计 ✗，故分母单列"
                "「覆盖」数；各子集按日并行对比择优。"
                "P8「量✓」=信号终结笔均量缩于上一同向笔（买=卖压衰竭/回踩"
                "惜售、卖=追高乏力，全类型）；「势顺」=大盘指数环境顺风"
                "（CN 上证 / CRYPTO BTC 收盘>MA20>MA60 为 up，买类顺风；"
                "反之为 down，卖类顺风；US/HK 库内无指数不判，分母单列）。"
                "P6-c「趋✓」=教学完整版趋势背驰（a+A+b+B+c，仅 1买/1卖）："
                "信号前 ≥2 个同向不重叠中枢（后中枢整个区间越过前中枢，"
                "ZG/ZD 沿判）、信号价在末中枢趋势侧之外、末段 c 笔力度弱于"
                "A→B 连接段 b（含贯穿中枢的离开笔）；信号前不足 2 中枢记 "
                "None 不判，结构可见但不成立记 ✗，覆盖数单列。"
                "P9「牛✓」=长周期趋势 regime（close>MA200=True 牛市 regime，"
                "全类型不区分方向）：与 P8-b MA20/MA60 短周期环境并行影子、"
                "口径不互相推翻——MA200 抓结构性牛熊分水岭（2018-2020 工作 / "
                "2021-2023 失效的根因），MA20/MA60 抓中短期趋势配合；时间外扩"
                "（2018-2023 tushare 8 标的）on 夏普 1.37 / off -1.73，样本内"
                "达标，仍需在线影子积累 n≥10 再评估启用。"
                "样本库 %s 条 / %s 标的 · 生成于 %s UTC · %s</div></details></div>"
                % (ev.get("n_rows", "—"), ev.get("n_symbols", "—"),
                   ev.get("generated_at", "—"), ev.get("verdict", "")))
            # P9 复合叠加 · 影子预研（读评估缓存 comp 区，只评估不拦截）
            comp_rows = ""
            for t, d in ev["types"].items():
                for c in (d.get("comp") or []):
                    ce = c.get("exe") or {}
                    if c.get("cov"):
                        cov_h = "✓%s / 可判 %s" % (c.get("n", 0),
                                                   c.get("cov", 0))
                        if ce:
                            avg = ce.get("avg")
                            stat_h = ("n=%s · %s%% · <span style="
                                      "'color:%s'>%s%%</span>"
                                      % (ce.get("n"), ce.get("win"),
                                         "#f85149" if avg and avg > 0
                                         else "#3fb950", avg))
                        else:
                            stat_h = ("<span class='dim'>r20 未成熟"
                                      "</span>")
                    else:
                        cov_h = "<span class='dim'>待可判样本</span>"
                        stat_h = "<span class='dim'>—</span>"
                    comp_rows += (
                        "<tr><td><b>%s</b></td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td class='dim'>%s</td></tr>"
                        % (t, c.get("name"), cov_h, stat_h,
                           "+".join(c.get("factors") or [])))
            comp_panel = (
                "<div class='sec'><details><summary style='cursor:pointer;"
                "font-weight:600'>🔗 复合叠加 · 影子预研"
                "<span class='dim' style='font-size:12px;font-weight:400'>"
                "（P9 · 只评估不拦截 · 点击展开）</span></summary>"
                "<table style='margin-top:10px'><tr><th>类型</th><th>叠加规则</th>"
                "<th>成立/可判</th><th>可成交 r20</th><th>因子</th></tr>"
                + (comp_rows or "<tr><td colspan='5' class='dim'>暂无"
                   "</td></tr>") + "</table>"
                "<div class='dim' style='font-size:12px;line-height:2'>"
                "复合口径：全部因子同时成立才算「成立」（✓/可判 分母单列）；"
                "任一因子不可判（60m 未覆盖 / 无指数 / 无前序笔 / 无量数据 /"
                " 信号前中枢不足 → None）整条记不可判、<b>绝不按 ✗ 处理</b>。"
                "样本口径=截断重放最早可检出时点收盘基准；与单因子列并行"
                "影子对照，达标（n≥10 且显著优于全集）经用户确认后才谈拦截。"
                "规则表：chanlun_composite.py，新增规则评估与页面自动跟随。"
                "</div></details></div>")
        else:
            ev_panel = ("<div class='sec'><h2>📉 样本统计 · 可成交口径</h2>"
                        "<div class='dim'>评估缓存待生成：每日 06:00 样本"
                        "任务（ChanlunSample）首跑后自动写入。</div></div>")

        # ── K154 tbe/pbe 影子分层（chanlun_bi 谱系，读离线统计缓存） ──
        tbe = _read_json(os.path.join(
            STATION, "quant_backtest", "data", "chanlun_tbe_stats.json"))
        tbe_panel = ""
        if tbe.get("groups"):
            _nm = {"b1_tbe": "买1 · 趋势背驰", "b1_pbe": "买1 · 盘整背驰",
                   "s1_tbe": "卖1 · 趋势背驰", "s1_pbe": "卖1 · 盘整背驰"}
            trows = ""
            for k, lab in _nm.items():
                g = (tbe.get("groups") or {}).get(k)
                if not g:
                    continue

                def _cell(x):
                    if not x or not x.get("n"):
                        return "<span class='dim'>—</span>"
                    return "n=%s · %s%% · <span style='color:%s'>%s%%</span>" \
                        % (x["n"], x.get("win"),
                           "#f85149" if (x.get("avg") or 0) > 0 else "#3fb950",
                           x.get("avg"))

                trows += ("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                          % (lab, _cell(g.get("d5")), _cell(g.get("d20"))))
            if trows:
                tbe_panel = (
                    "<div class='sec'><details><summary style='cursor:pointer;"
                    "font-weight:600'>📉 买1/卖1 · 趋势背驰 vs 盘整背驰"
                    "（影子分层）<span class='dim' style='font-size:12px;"
                    "font-weight:400'>（d5/d20 方向收益 · 点击展开）"
                    "</span></summary>"
                    "<table style='margin-top:10px'><tr><th>子类</th><th>d5 方向收益</th>"
                    "<th>d20 方向收益</th></tr>" + trows + "</table>"
                    "<div class='dim' style='font-size:12px;line-height:2'>"
                    "chanlun_bi 谱系（详情页 K 线菱形「·趋/·盘」同口径）："
                    "tbe=紧邻前序中枢与当前中枢同向排列且无重叠（趋势第二"
                    "中枢后离开段），pbe=单中枢盘背。理论口径（基准=信号日"
                    "收盘，含确认滞后前视成分，d5 win 整体偏高系此）；"
                    "%s 标的 / %s 信号 · %s 生成 · 手跑 "
                    "chanlun_tbe_stats.py 刷新 · "
                    "<a href='/bt-tbe'>完整报表（持有期曲线/年度分层/"
                       "分布）→</a></div></details></div>"
                    % (tbe.get("n_symbols", "—"), tbe.get("n_signals", "—"),
                       (tbe.get("generated_at") or "—")[:16]))

        viewer = (
            "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px'>"
            "<input id='ck-code' placeholder='600519 / AAPL / 00700 / BTC/USDT' "
            "style='flex:1;min-width:200px;background:#0d1117;border:1px solid "
            "#30363d;border-radius:8px;color:#e6edf3;padding:8px 12px;font-size:14px'>"
            "<select id='ck-period' style='background:#0d1117;border:1px solid "
            "#30363d;border-radius:8px;color:#e6edf3;padding:8px;font-size:14px'>"
            "<option value='1d'>日线</option><option value='60m'>60分钟</option>"
            "<option value='30m'>30分钟</option></select>"
            "<select id='ck-last' title='尾部K线根数' style='background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:8px;"
            "font-size:14px'><option value='120'>最近120根</option>"
            "<option value='240'>最近240根</option>"
            "<option value='500' selected>最近500根</option>"
            "<option value='0'>全部</option></select>"
            "<button onclick='ckLoad()' style='background:#238636;color:#fff;"
            "border:0;border-radius:8px;padding:8px 18px;font-size:14px;"
            "cursor:pointer'>加载 K 线</button></div>"
            "<div id='ck-meta' class='dim' style='font-size:13px;min-height:18px'>"
            "输入代码加载 · 图层可开关 · 悬停看数值</div>"
            "<div id='ck-toggles' style='display:flex;gap:6px;flex-wrap:wrap;"
            "margin:8px 0'></div>"
            "<div id='ck-chart' style='background:#0d1117;border:1px solid "
            "#30363d;border-radius:10px;padding:6px'></div>"
            "<div id='ck-heat' style='margin-top:12px'></div>"
            "<div id='ck-sigcards' style='margin-top:12px'></div>"
            "<div id='ck-bt' style='margin-top:12px'></div>"
            "<div id='ck-sigs' style='margin-top:12px'></div>")
        js = """<script>
var CK={layers:{bars:1,vol:1,fr:1,bi:1,seg:1,pv:1,sig:1},data:null,bt:null};
function $(id){return document.getElementById(id)}
function ckToggle(k,btn){
 CK.layers[k]=!CK.layers[k];btn.style.opacity=CK.layers[k]?1:.35;
 if(CK.data)ckDraw(CK.data);
}
function ckInitToggles(){
 var defs=[['bars','K线',1],['vol','成交量',1],['fr','分型',1],['bi','笔',1],
           ['seg','线段',1],['pv','中枢',1],['sig','买卖点',1]];
 var el=$('ck-toggles');el.innerHTML='';
 defs.forEach(function(d){
  var b=document.createElement('button');
  b.textContent=d[1];
  b.style.cssText='background:#21262d;color:#e6edf3;border:1px solid #30363d;'
   +'border-radius:14px;padding:2px 12px;font-size:12px;cursor:pointer;opacity:'
   +(d[2]?1:.35);
  b.onclick=function(){ckToggle(d[0],b)};
  el.appendChild(b);
 });
}
function ckLoad(){
 var c=$('ck-code').value.trim(),p=$('ck-period').value,lastN=parseInt($('ck-last').value,10)||0;
 var m=$('ck-meta'),el=$('ck-chart');
 if(!c){m.innerHTML="<span style='color:#d29922'>请先输入代码</span>";el.innerHTML='';return}
 m.textContent='加载中…';el.innerHTML='';
 fetch('/api/chanlun/structure?code='+encodeURIComponent(c)+'&period='+p)
 .then(function(r){return r.json()})
 .then(function(j){
  if(!j.ok){m.innerHTML="<span style='color:#f85149'>"+j.error+"</span>";return}
  if(lastN>0&&j.bars.length>lastN){
   var cut=j.bars[j.bars.length-lastN].dt;
   j.bars=j.bars.slice(-lastN);
   var inR=function(d){return d>=cut};
   j.fractals=(j.fractals||[]).filter(function(f){return inR(f.dt)});
   j.bis=(j.bis||[]).filter(function(b){return inR(b.e_dt)});
   j.segments=(j.segments||[]).filter(function(s){return inR(s.e_dt)});
   j.pivots=(j.pivots||[]).filter(function(v){return inR(v.e_dt)});
  }
  CK.data=j;
  var b=j.bars,frst=b[0],last=b[b.length-1];
  var nt=0,nb=0;(j.fractals||[]).forEach(function(f){f.type=='顶'?nt++:nb++});
  m.innerHTML='来源 '+j.source+' · '+j.market+' '+j.code+' · '+b.length+' 根 · '
   +frst.dt+' → '+last.dt+' · 最新收盘 <b style="color:#e6edf3">'+last.c+'</b>'
   +' · <span style="color:#3fb950">顶 '+nt+'</span>/<span style="color:#f85149">底 '+nb+'</span>'
   +'（合并后 '+j.merged_n+' 根）'
   +' · <span style="color:#e3b341">笔 '+(j.bis||[]).length+'</span>'
   +' · <span style="color:#58a6ff">段 '+(j.segments||[]).length+'</span>'
   +' · <span style="color:#a371f7">枢 '+(j.pivots||[]).length+'</span>'
   +' · <span style="color:#3fb950">信号 '+(j.signals||[]).length+'</span>';
  ckDraw(j);ckHeat();ckSigCards(j);ckSigs(j);
  var pl=$('ck-png');
  if(pl)pl.href='/chanlun-png?c='+encodeURIComponent(j.code)+'&p='+j.period;
  fetch('/api/chanlun/backtest?code='+encodeURIComponent(c)+'&period='+p)
  .then(function(r){return r.json()}).then(function(bt){
   CK.bt=bt;var el2=$('ck-bt');
   if(!bt.ok){el2.innerHTML="<span style='color:#f85149'>验证失败："+bt.error+"</span>";return}
   ckBtTable(bt);
  }).catch(function(){});
 }).catch(function(){m.innerHTML="<span style='color:#f85149'>请求失败，请重试</span>"});
}
function ckDraw(j){
 var b=j.bars,n=b.length;
 var W=1160,H=460,HV=90,PL=8,PR=64,PT=10,PB=24,GAP=6;
 var HP=PT+HV+GAP,HC=H-HP-PB;
 var el=$('ck-chart');
 var hi=Math.max.apply(null,b.map(function(x){return x.h}));
 var lo=Math.min.apply(null,b.map(function(x){return x.l}));
 var rng=(hi-lo)||1, pad=rng*0.04; hi+=pad; lo-=pad; rng=hi-lo;
 var mv=Math.max.apply(null,b.map(function(x){return x.v}))||1;
 function X(i){return PL+(i+0.5)*(W-PL-PR)/n}
 function Y(v){return PT+(hi-v)/rng*HC}
 function YV(v){return HP+HV-(v/mv)*HV}
 var cw=Math.max(1.2,(W-PL-PR)/n*0.62),g='',i;
 for(var k=0;k<=5;k++){
  var v=lo+rng*k/5,yy=Y(v);
  g+='<line x1="'+PL+'" x2="'+(W-PR)+'" y1="'+yy+'" y2="'+yy+'" stroke="#21262d"/>'
   +'<text x="'+(W-PR+6)+'" y="'+(yy+4)+'" fill="#8b949e" font-size="10">'
   +(v>=100?v.toFixed(0):v.toFixed(2))+'</text>';}
 if(CK.layers.vol){
  g+='<line x1="'+PL+'" x2="'+(W-PR)+'" y1="'+(HP+HV)+'" y2="'+(HP+HV)+'" stroke="#21262d"/>';
  for(i=0;i<n;i++){var vv2=b[i],up2=vv2.c>=vv2.o,vc=up2?'#da6b6b':'#4a9e70';
   g+='<rect x="'+(X(i)-cw/2)+'" y="'+YV(vv2.v)+'" width="'+cw+'" height="'
    +Math.max(.6,HV-(vv2.v/mv)*HV)+'" fill="'+vc+'" opacity=".55"/>';}
  g+='<text x="'+PL+'" y="'+(HP+10)+'" fill="#8b949e" font-size="9">量</text>';
 }
 if(CK.layers.bars){
  for(i=0;i<n;i++){var bb=b[i],up=bb.c>=bb.o,col=up?'#f85149':'#3fb950';
   var x1=X(i),yo=Y(bb.o),yc=Y(bb.c);
   g+='<line x1="'+x1+'" x2="'+x1+'" y1="'+Y(bb.h)+'" y2="'+Y(bb.l)+'" stroke="'+col+'"/>'
    +'<rect x="'+(x1-cw/2)+'" y="'+Math.min(yo,yc)+'" width="'+cw+'" height="'
    +Math.max(1,Math.abs(yc-yo))+'" fill="'+col+'"><title>'+bb.dt+' O'+bb.o+' H'+bb.h
    +' L'+bb.l+' C'+bb.c+' V'+bb.v+'</title></rect>';}
 }
 var idx=[];var step=Math.max(1,Math.floor(n/6));
 for(i=0;i<n;i+=step){idx.push(i)}
 if(idx[idx.length-1]!==n-1)idx.push(n-1);
 idx.forEach(function(i2){
  g+='<text x="'+X(i2)+'" y="'+(H-6)+'" fill="#8b949e" font-size="10" text-anchor="middle">'
   +b[i2].dt.slice(5,10)+'</text>';});
 var map={};for(i=0;i<n;i++){map[b[i].dt]=i}
 if(CK.layers.pv){
  (j.pivots||[]).forEach(function(pv){
   var xi=map[pv.s_dt],xo=map[pv.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<rect x="'+(X(xi)-cw/2)+'" y="'+Y(pv.zg)+'" width="'+Math.max(1,X(xo)-X(xi))
    +'" height="'+Math.max(1,Y(pv.zd)-Y(pv.zg))
    +'" fill="rgba(163,113,247,0.10)" stroke="#a371f7" stroke-dasharray="4 3">'
    +'<title>中枢 '+pv.s_dt+'~'+pv.e_dt+' ZG '+pv.zg+' ZD '+pv.zd+'（'+pv.n_bi+' 笔）</title></rect>';
   g+='<text x="'+(X(xi)+2)+'" y="'+(Y(pv.zg)-3)+'" fill="#a371f7" font-size="9">ZG '+pv.zg+'</text>'
    +'<text x="'+(X(xi)+2)+'" y="'+(Y(pv.zd)+10)+'" fill="#a371f7" font-size="9">ZD '+pv.zd+'</text>';
  });
 }
 if(CK.layers.seg){
  (j.segments||[]).forEach(function(sg){
   var xi=map[sg.s_dt],xo=map[sg.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<line x1="'+X(xi)+'" y1="'+Y(sg.s_price)+'" x2="'+X(xo)+'" y2="'+Y(sg.e_price)
    +'" stroke="#58a6ff" stroke-width="2.5" stroke-linecap="round">'
    +'<title>线段 '+(sg.dir>0?'↑':'↓')+' '+sg.s_dt+' '+sg.s_price+' → '+sg.e_dt+' '+sg.e_price
    +'（'+sg.n_bi+' 笔）</title></line>';
  });
 }
 if(CK.layers.bi){
  (j.bis||[]).forEach(function(bi){
   var xi=map[bi.s_dt],xo=map[bi.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<line x1="'+X(xi)+'" y1="'+Y(bi.s_price)+'" x2="'+X(xo)+'" y2="'+Y(bi.e_price)
    +'" stroke="#e3b341" stroke-width="1.2">'
    +'<title>笔 '+(bi.dir>0?'↑':'↓')+' '+bi.s_dt+' '+bi.s_price+' → '+bi.e_dt+' '+bi.e_price+'</title></line>';
  });
 }
 if(CK.layers.fr){
  (j.fractals||[]).forEach(function(f){
   var i2=map[f.dt];if(i2===undefined)return;
   var y2=f.type=='顶'?Y(f.price)-6:Y(f.price)+12;
   g+='<text x="'+X(i2)+'" y="'+y2+'" font-size="10" text-anchor="middle" fill="'
    +(f.type=='顶'?'#3fb950':'#f85149')+'">'+(f.type=='顶'?'▼':'▲')
    +'<title>'+f.type+'分型 '+f.dt+' '+f.price+'</title></text>';
  });
 }
 if(CK.layers.sig){
  (j.signals||[]).forEach(function(sg2){
   var i2=map[sg2.dt];if(i2===undefined)return;
   var buy=sg2.type.indexOf('买')>=0;
   var cy=buy?Y(sg2.price)+14:Y(sg2.price)-8;
   g+='<circle cx="'+X(i2)+'" cy="'+cy+'" r="3.2" fill="'+(buy?'#f85149':'#3fb950')+'">'
    +'<title>'+sg2.type+' '+sg2.dt+' @'+sg2.price+'\\n'+(sg2.note||'')+'</title></circle>';
   g+='<text x="'+X(i2)+'" y="'+(buy?cy+12:cy-6)+'" font-size="11" font-weight="bold"'
    +' text-anchor="middle" fill="'+(buy?'#f85149':'#3fb950')+'">'+sg2.type+'</text>';
  });
 }
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;display:block">'+g+'</svg>';
}
function ckBtTable(bt){
 var el=$('ck-bt'),st=bt.stats||{},ev=bt.events||[],ty=bt.by_type||{};
 var rows='',k;
 for(k in ty){rows+='<span style="margin-right:12px">'+k+' n='+ty[k].n
  +' · 胜率'+ty[k].win+'% · 均值'+ty[k].avg+'%</span>'}
 var evr='';
 if(ev.length){
  evr='<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:6px">'
   +'<tr style="color:#8b949e"><th style="text-align:left">类型</th><th>信号日</th>'
   +'<th>信号价</th><th>+5日</th><th>+10日</th><th>+20日</th></tr>';
  ev.forEach(function(x){
   var f=function(v){return v==null?'—':(v>=0?'+':'')+(v*100).toFixed(1)+'%'};
   var c=function(v){return v==null?'#8b949e':(v>=0?'#f85149':'#3fb950')};
   evr+='<tr><td><b>'+x.type+'</b></td><td style="text-align:right">'+x.dt
    +'</td><td style="text-align:right">'+x.price+'</td>'
    +'<td style="text-align:right;color:'+c(x.r5)+'">'+f(x.r5)+'</td>'
    +'<td style="text-align:right;color:'+c(x.r10)+'">'+f(x.r10)+'</td>'
    +'<td style="text-align:right;color:'+c(x.r20)+'">'+f(x.r20)+'</td></tr>';
  });
  evr+='</table>';
 }
 el.innerHTML='<b style="color:#e6edf3">📈 信号事件复盘</b>（理论价口径 · 纸面参考）· '
  +bt.n_signals+' 个信号 · 策略 '+(st.trades||0)+' 笔 · 胜率 '
  +(st.win_rate==null?'—':st.win_rate+'%')+' · 累计 '
  +(st.total_return==null?'—':st.total_return+'%')+' · 回撤 '+(st.max_dd||0)
  +'% · 买入持有 '+(st.buy_hold==null?'—':st.buy_hold+'%')
  +(st.open_pos?' · <span style="color:#d29922">期末持仓未平</span>':'')
  +(rows?'<br><span style="font-size:12px">'+rows+'</span>':'')
  +evr;
}
function ckHeat(){
 var el=$('ck-heat');
 fetch('/api/chanlun/pool/heat')
 .then(function(r){return r.ok?r.json():null})
 .then(function(d){
  if(!d||!d.ok){el.innerHTML='';return}
  var types=['1买','2买','3买','1卖','2卖','3卖'],mx=1;
  types.forEach(function(t){if((d.counts[t]||0)>mx)mx=d.counts[t]});
  var cells='';
  types.forEach(function(t){
   var v=d.counts[t]||0,w=v/mx*100,buy=t.indexOf('买')>=0;
   cells+='<div style="display:grid;grid-template-columns:36px 1fr 44px;gap:6px;'
    +'align-items:center;font-size:11px;font-family:monospace;margin:2px 0">'
    +'<span>'+t+'</span><span style="height:12px;background:#161b22;'
    +'border-radius:3px;position:relative;overflow:hidden"><span style="position:'
    +'absolute;left:0;top:0;bottom:0;width:'+w+'%;background:'
    +(buy?'#f85149':'#3fb950')+'"></span></span>'
    +'<span style="text-align:right">'+v+'</span></div>';
  });
  el.innerHTML='<b style="font-size:13px;color:#e6edf3">🔥 市场信号热度</b>'
   +'<span class="dim" style="font-size:11.5px;margin-left:8px">'+d.detect_date
   +' 扫描检出 · 全市场六类买卖点</span>'
   +'<div style="max-width:420px;margin-top:4px">'+cells+'</div>';
 }).catch(function(){el.innerHTML=''});
}
function ckSigCards(j){
 var el=$('ck-sigcards'),sigs=j.signals||[];
 if(!sigs.length){el.innerHTML="<div class='dim' style='font-size:12.5px'>"
  +"本票当前无检出信号（结构已出，可切周期复核）。</div>";return}
 var cards='';
 sigs.forEach(function(s){
  var buy=s.type.indexOf('买')>=0;
  var badges='';
  if(s.mbei===true)badges+='<span style="color:#bc8cff">M✓MACD背驰</span> ';
  if(s.tbe===true)badges+='<span style="color:#f778ba">趋✓双中枢</span> ';
  if(s.vv===true)badges+='<span style="color:#f0883e">量✓缩量</span> ';
  if(s.mt===true)badges+='<span style="color:#2dd4bf">次✓共振</span> ';
  if(s.pt)badges+='<span style="color:#d29922">K线·'+(s.pt_name||'形态')+'</span> ';
  var detail='';
  if(s.mfv)detail+='<div style="font-size:10.5px;color:#8b949e">MACD 快'
   +s.mfv[0].toFixed(2)+' 慢'+s.mfv[1].toFixed(2)+' 柱'
   +s.mfv[2].toFixed(2)+'/'+s.mfv[3].toFixed(2)+'</div>';
  if(s.vv_ev)detail+='<div style="font-size:10.5px;color:#8b949e">量能 前期'
   +Math.round(s.vv_ev[0])+' vs 信号'+Math.round(s.vv_ev[1])+'</div>';
  cards+='<div style="border:1px solid #30363d;border-left:3px solid '
   +(buy?'#f85149':'#3fb950')+';border-radius:8px;padding:10px 14px;'
   +'margin-bottom:8px;background:#0d1117">'
   +'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
   +'<b style="color:'+(buy?'#f85149':'#3fb950')+';font-size:15px">'+s.type+'</b>'
   +'<span style="font-family:monospace">'+s.dt+'</span>'
   +'<span style="font-family:monospace">@ '+(s.price==null?'—':s.price)+'</span>'
   +'<span class="dim" style="font-size:12px">'+(s.note||'')+'</span></div>'
   +(badges?'<div style="margin-top:4px;font-size:12px">'+badges+'</div>':'')
   +detail+'</div>';
 });
 el.innerHTML='<b style="font-size:13.5px;color:#e6edf3">🎯 检出信号 · 影子证据卡</b>'
  +'<div style="margin-top:6px">'+cards+'</div>';
}
function ckSigs(j){
 var el=$('ck-sigs');
 var sigs=j.signals||[],b=j.bars||[];
 var lastdt=b.length?b[b.length-1].dt:'';
 if(j.period!=='1d'){
  el.innerHTML="<div class='dim' style='font-size:12.5px'>⇪ 推池仅支持日线信号；当前 "
   +j.period+" 仅作结构观察。</div>";return}
 if(j.market!=='CN'&&j.market!=='US'&&j.market!=='HK'){
  el.innerHTML="<div class='dim' style='font-size:12.5px'>⇪ 推池支持 CN/US/HK 日线"
   +"（独立市场池链）；当前 "+j.market+" 仅作结构观察。</div>";return}
 if(!sigs.length){el.innerHTML='';return}
 var rows='',i;
 for(i=0;i<sigs.length;i++){var s=sigs[i];
  var buy=s.type.indexOf('买')>=0,old=(s.dt!==lastdt);
  rows+='<tr><td><b style="color:'+(buy?'#3fb950':'#f85149')+'">'+s.type+'</b></td>'
   +'<td>'+s.dt+'</td><td>'+(s.price==null?'—':s.price)+'</td>'
   +'<td>'+(old?"<span style='color:#e3b341'>⚠ 旧信号</span>"
     :'<span style="color:#3fb950">最新</span>')+'</td>'
   +'<td><button id="ckpb_'+i+'" onclick="ckPush('+i+')" style="background:#238636;'
   +'color:#fff;border:0;border-radius:6px;padding:2px 10px;cursor:pointer;'
   +'font-size:12px">⇪ 推池</button></td></tr>';}
 el.innerHTML='<div style="font-size:13.5px;margin-bottom:4px">'
  +'<b style="color:#e6edf3">信号入池</b>（source=manual · 三层幂等）</div>'
  +'<table><tr><th>类型</th><th>信号日</th><th>信号价</th><th>时效</th>'
  +'<th>入池</th></tr>'+rows+'</table>';
 window._ckSig=sigs;window._ckCode=j.code;window._ckMkt=j.market;
}
function ckPush(i){
 var s=(window._ckSig||[])[i];if(!s)return;
 var btn=$('ckpb_'+i);
 btn.disabled=true;btn.textContent='推池中…';
 fetch('/api/chanlun/pool/push',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({code:window._ckCode,type:s.type,dt:s.dt,market:window._ckMkt})})
 .then(function(r){return r.json()})
 .then(function(j2){
  if(j2.ok){
   var hit=(j2.pushed||[]).some(function(x){return x.type===s.type&&x.dt===s.dt});
   btn.textContent=hit?'已入池 ✓':'已在池';
   btn.style.background='#30363d';
  }else{btn.textContent='失败';btn.style.background='#b35900';
   btn.title=(j2.error||'').slice(0,80);btn.disabled=false;}
 }).catch(function(){btn.textContent='重试';btn.disabled=false;});
}
ckInitToggles();
</script>"""
        # ── 五段式对齐青萍之末（2026-10-05 改版）：标题+态势条 → 说明书 →
        #    环境闸门 → 信号清单 → 面板/查看器/工具；方法详表与⏳详述维持
        #    details 折叠作参考层 ──
        import html as _h
        import re as _rp
        import time as _tt
        e = _h.escape

        # ① 三市场扫描任务状态（60s 进程内缓存，单次 PowerShell 查三任务）
        _now3 = _tt.time()
        _tsc = getattr(type(self), "_cw_tstat_cache", None)
        if _tsc and _now3 - _tsc[0] < 60:
            _tmap = _tsc[1]
        else:
            out = run(["powershell", "-NoProfile", "-Command",
                       "[Console]::OutputEncoding="
                       "[System.Text.Encoding]::UTF8;"
                       "foreach($n in 'ChanlunScan','ChanlunScanUS',"
                       "'ChanlunScanHK'){$i=Get-ScheduledTaskInfo "
                       "-TaskName $n -ErrorAction SilentlyContinue;"
                       "if($i){$n+' last='+$i.LastRunTime+'  result='"
                       "+$i.LastTaskResult+'  next='+$i.NextRunTime}"
                       "else{$n+' not registered'}}"], timeout=15)
            _tmap = {}
            for _ln in (out or "").splitlines():
                _ln = _ln.strip()
                _mm = _rp.match(r"(\w+)\s+(.*)", _ln) if _ln else None
                if _mm:
                    _tmap[_mm.group(1)] = _mm.group(2)
            type(self)._cw_tstat_cache = (_now3, _tmap)

        # 判色走共享 _sched_rc_html（SCHED_S 信息码语义化，267011≠异常）
        _cn_disp, _cn_rc = _sched_rc_html(_tmap.get("ChanlunScan"))
        _us_disp, _us_rc = _sched_rc_html(_tmap.get("ChanlunScanUS"))
        _hk_disp, _hk_rc = _sched_rc_html(_tmap.get("ChanlunScanHK"))

        def _cw_pool(sfx):
            """读市场池文件 → 记录列表（新→旧）；无文件/损坏 → []。"""
            try:
                with open(os.path.join(QBT_DIR, "data",
                                       "chanlun_pool%s.jsonl" % sfx),
                          encoding="utf-8") as f:
                    rs = [json.loads(l) for l in f if l.strip()]
                rs.reverse()
                return rs
            except Exception:
                return []

        _cn_rs = _cw_pool("")
        _us_rs = _cw_pool("_us")
        _hk_rs = _cw_pool("_hk")

        def _cw_last(rs):
            return max((r.get("detect_date") or "" for r in rs), default="")

        _sig_c = {"1买": "#f85149", "2买": "#f85149", "3买": "#f85149",
                  "1卖": "#3fb950", "2卖": "#3fb950", "3卖": "#3fb950"}

        def _cw_rows(rs, mkt):
            """信号清单行：词汇系=信号日(dt)/检出日(detect_date)/入场价/
            现价(last_px)。四市场代码统一 /bt/stock?c= 下钻（详情页按
            代码前缀推断市场，resolve 失败显示识别表单优雅降级）。"""
            if not rs:
                return ("<tr><td colspan='7' class='dim'>暂无信号</td></tr>")
            rows = []
            for r in rs[:10]:
                _c = r.get("code") or "—"
                _cd = ("<a href='/kg/stock/%s'><b>%s</b></a>"
                       % (_c, _c))
                _t = r.get("type") or "—"
                _px = r.get("entry_px")
                _lp = r.get("last_px")
                rows.append(
                    "<tr><td>%s</td><td>%s</td>"
                    "<td><b style='color:%s'>%s</b></td>"
                    "<td class='dim'>%s</td><td class='dim'>%s</td>"
                    "<td style='text-align:right'>%s</td>"
                    "<td style='text-align:right'>%s</td></tr>"
                    % (_cd, r.get("name") or "—",
                       _sig_c.get(_t, "#e6edf3"), _t,
                       r.get("dt") or "—", r.get("detect_date") or "—",
                       ("%.2f" % _px) if isinstance(_px, (int, float))
                       else "—",
                       ("%.2f" % _lp) if isinstance(_lp, (int, float))
                       else "—"))
            return "".join(rows)

        def _cw_tbl(emoji, tname, rs, mkt):
            return ("<div style='margin-top:10px'><b>%s %s</b>"
                    "<span class='dim' style='font-size:12px;margin-left:8px'>"
                    "池 %d 条 · 最新检出 %s · 点代码下钻个股详情</span>"
                    "<table style='margin-top:6px'><tr><th>代码</th><th>名称"
                    "</th><th>信号</th><th>信号日</th><th>检出日</th>"
                    "<th>入场价</th><th>现价</th></tr>%s</table></div>"
                    % (emoji, tname, len(rs), _cw_last(rs) or "—",
                       _cw_rows(rs, mkt)))

        _sig_html = (_cw_tbl("🇨🇳", "A股 · ChanlunScan", _cn_rs, "CN")
                     + _cw_tbl("🇺🇸", "美股 · ChanlunScanUS", _us_rs, "US")
                     + _cw_tbl("🇭🇰", "港股 · ChanlunScanHK", _hk_rs, "HK"))

        # ② 环境闸门（与青萍之末同源 rps_latest；缠论仅影子呈现不否决）
        _snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        _gates = []
        for ev in _snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            _raw = ev.get("safe")
            safe = bool(_raw)
            _gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span class='g-state' style='float:right;font-size:14px;"
                "font-weight:700'>%s</span>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                "<div style='margin-top:6px;font-size:12.5px;color:%s'>%s"
                "</div></a>"
                % (("g-na" if _raw is None else ("g-on" if safe else "g-off")),
                   mk, MKT_LABEL[mk],
                   e(str(ev.get("score", "—"))),
                   e(str(ev.get("state", ""))),
                   ("var(--color-gate-na,#5b7a99)" if _raw is None
                    else ("var(--color-gate-ok,#2f9e8f)" if safe
                          else "var(--color-gate-bad,#c98a1e)")),
                   ("— 缺维降权 · 不误杀" if _raw is None
                    else ("✅ 放行建仓" if safe
                          else "⛔ 否决建仓"))))
        _gates_html = ("<div class='gates'>%s</div>" % "".join(_gates)
                       if _gates else "<div class='dim'>暂无快照</div>")

        # ⑤ 大盘阶段判断（上证指数缠论视角；键=当天日期，当日缓存——
        #    日线库盘后才更新，同一天重复开页零开销）
        def _cw_market_stage():
            """上证 000001.SH 日线缠论阶段：走势类型+中枢位置+最近信号
            +大盘环境；返回完整 sec HTML，数据不可用返回 None。"""
            import sys as _sys
            if QBT_DIR not in _sys.path:
                _sys.path.insert(0, QBT_DIR)
            try:
                from chanlun_data import load_kline
                from chanlun_signal import analyze
                from chanlun_flow import regime_at
                ri = load_kline("000001.SH", "1d", 500)
                if not (ri.get("ok") and ri.get("bars")):
                    return None
                bars = ri["bars"]
                px = float(bars[-1]["c"])
                st = analyze(bars)
                allp = [(float(p["zg"]), float(p["zd"]))
                        for p in (st.get("pivots") or [])]
                sigs = st.get("signals") or []
                try:
                    reg = regime_at("CN", str(bars[-1]["dt"])[:10], bars)
                    reg_s, reg_ext = str(reg[0]), list(reg[1] or [])
                except Exception:
                    reg_s, reg_ext = None, []
            except Exception:
                return None
            # 现价所在/下方最近中枢（与扫描链同款 zd<px 过滤）
            below = [p for p in allp if p[1] < px]
            cur = below[-1] if below else (allp[-1] if allp else None)
            # 走势类型：最近两个中枢位置关系（B 抬升于 A 上=趋势）
            trend = "盘整震荡"
            if len(allp) >= 2:
                (a_zg, a_zd), (b_zg, b_zd) = allp[-2], allp[-1]
                if b_zd > a_zg:
                    trend = "上升趋势"
                elif b_zg < a_zd:
                    trend = "下降趋势"
            if cur:
                zg, zd = cur
                pos = ("中枢上方（强势区）" if px > zg
                       else "中枢下方（弱势区）" if px < zd
                       else "中枢内（震荡区）")
                if px > zg:
                    ins = ("回踩不破中枢上沿 <b>%.0f</b> 为三类买点观察位"
                           % zg)
                elif px < zd:
                    ins = ("现价于中枢下沿 <b>%.0f</b> 下方弱势区——反抽"
                           "不回中枢区间为三类卖点观察" % zd)
                else:
                    ins = ("中枢 [<b>%.0f</b>, <b>%.0f</b>] 内震荡——向上"
                           "离开后回踩不破上沿为三买观察，向下离开后反抽"
                           "不回区间为三卖观察" % (zd, zg))
            else:
                pos, ins = "无中枢参照", "近端结构未形成有效中枢"
            # 特判：趋势中跌离最新中枢（趋势回踩/破坏，大盘判断核心场景）
            if len(allp) >= 2:
                lzg, lzd = allp[-1]
                if px < lzd and allp[-2][0] < lzg:
                    ins = ("现价已跌离最新中枢 [<b>%.0f</b>, <b>%.0f</b>]"
                           "、回到前中枢 [<b>%.0f</b>, <b>%.0f</b>] 区间——"
                           "关注 <b>%.0f</b>（前中枢上沿）争夺：收复属趋势"
                           "回踩确认，跌破 <b>%.0f</b>（前中枢下沿）则日线"
                           "升势结构存疑"
                           % (lzd, lzg, allp[-2][1], allp[-2][0],
                              allp[-2][0], allp[-2][1]))
                elif px > lzg:
                    ins = ("现价站上最新中枢上沿 <b>%.0f</b>——回踩不破"
                           "则延续向上离开段" % lzg)
            # 大盘环境（P8 同源 regime_at）
            if reg_s == "up":
                env = "<b style='color:#3fb950'>顺风 up</b>（收盘&gt;MA20&gt;MA60）"
            elif reg_s == "down":
                env = "<b style='color:#f85149'>逆风 down</b>（收盘&lt;MA20&lt;MA60）"
            elif reg_s:
                env = "<b style='color:#d29922'>中性 flat</b>（MA20≈MA60 粘合）"
            else:
                env = "—"
            _ma = (" · MA20 <b>%.0f</b> · MA60 <b>%.0f</b>"
                   % (float(reg_ext[1]), float(reg_ext[2]))
                   if len(reg_ext) >= 3 else "")
            sig_html = " · ".join(
                "<b style='color:%s'>%s（%s）</b>"
                % ("#f85149" if "买" in str(s.get("type")) else "#3fb950",
                   s.get("type"), str(s.get("dt"))[:10])
                for s in sigs[-3:]) or "近端无信号"
            _key = ("关键位：最近中枢 [<b>%.0f</b>, <b>%.0f</b>] · 现价 "
                    "<b>%.0f</b>%s" % (cur[1], cur[0], px, _ma)
                    if cur else "现价 <b>%.0f</b>%s" % (px, _ma))
            return ("<div class='sec'><h2>🧭 大盘阶段 · 缠论视角"
                    "<span class='dim' style='font-size:12px'>（上证指数 "
                    "000001.SH · 日线 · 引擎与全市场扫描同源 · 最后一根 "
                    "%s）</span></h2>"
                    "<div style='font-size:16.5px;line-height:2'><b>%s</b>"
                    " · 现价 <b style='color:#e6edf3'>%.0f</b> %s</div>"
                    "<div style='font-size:13.5px;line-height:2;margin-top:4px'>"
                    "%s</div>"
                    "<div class='dim' style='font-size:13px;line-height:2.1'>"
                    "%s ｜ 环境：%s ｜ 最近信号：%s</div>"
                    "<img src='/chanlun-png?c=000001.SH&amp;p=1d' "
                    "loading='lazy' style='width:100%%;max-width:960px;"
                    "margin-top:10px;border:1px solid #30363d;"
                    "border-radius:8px' alt='上证日线缠论结构图'></div>"
                    % (str(bars[-1]["dt"])[:10], trend, px, pos, ins,
                       _key, env, sig_html))

        from datetime import date as _date
        _sk = str(_date.today())
        _sc = getattr(type(self), "_cw_stage_cache", None)
        if _sc and _sc[0] == _sk:
            _cw_stage_html = _sc[1]
        else:
            _cw_stage_html = _cw_market_stage() or ""
            if _cw_stage_html:
                type(self)._cw_stage_cache = (_sk, _cw_stage_html)

        # ⑥ 级别联动明细（30m/5m 实时盘感；60s TTL——分钟级盘中会动，
        #    与日线叙事当日缓存分离，防盘中冻表）
        def _cw_stage_rows():
            import sys as _sys
            import time as _t2
            if QBT_DIR not in _sys.path:
                _sys.path.insert(0, QBT_DIR)
            _now4 = _t2.time()
            _c4 = getattr(type(self), "_cw_ml_cache", None)
            if _c4 and _now4 - _c4[0] < 60:
                return _c4[1]
            try:
                import chanlun_market as _CM
            except Exception:
                return ""
            _rows = _CM.levels()
            rows = []
            for _b in _rows:
                try:
                    trend = _b["trend"]
                    _lb = {"1d": "📅 日线", "30m": "⏱ 30分钟",
                           "5m": "⚡ 5分钟"}.get(_b["per"], _b["per"])
                    posc = ("#8b949e" if _b["pos"] == "—"
                            else "#f85149" if "上方" in _b["pos"]
                            else "#3fb950" if "下方" in _b["pos"]
                            else "#d29922")
                    sg = _b.get("sig")
                    sgt = ("%s（%s）" % (sg.get("type"),
                                         str(sg.get("dt"))[5:10])
                           if sg else "—")
                    sgc = ("#f85149" if sg and "买" in str(sg.get("type"))
                           else "#3fb950")
                    tcc = ("#f85149" if trend == "上升"
                           else "#3fb950" if trend == "下降" else "#d29922")
                    rows.append(
                        "<tr><td><b>%s</b></td><td><b style='color:%s'>%s"
                        "</b></td><td>%s</td><td><b style='color:%s'>%s"
                        "</b></td><td class='dim'>%s</td>"
                        "<td><b style='color:%s'>%s</b></td></tr>"
                        % (_lb, tcc, trend, _b["piv"], posc, _b["pos"],
                           _b["tip"], sgc, sgt))
                except Exception:
                    continue
            if not rows:
                return ""
            # 联动一句话：日线定方向、30m 定节奏、5m 定日内
            _d = next((b for b in _rows if b["per"] == "1d"), None)
            _m30 = next((b for b in _rows if b["per"] == "30m"), None)
            _m5 = next((b for b in _rows if b["per"] == "5m"), None)
            parts = []
            if _d:
                parts.append("日线<b>%s</b>、现价%s%s——大级别方向锚"
                             % (_d["trend"], _d["pos"], _d["piv"]))
            if _m30:
                parts.append("30分钟现价%s%s、<b>%s</b>——笔未完成前可上"
                             "可下，这里定节奏"
                             % (_m30["pos"], _m30["piv"], _m30["tip"]))
            if _m5:
                parts.append("5分钟%s%s——日内方向锚"
                             % (_m5["pos"], _m5["piv"]))
            concl = " · ".join(parts)

            # 综合研判卡：判定矩阵/关键位口径走共享单点 chanlun_market.py
            # （晨报①大盘研判节同源消费，改判定规则只改那里），此处只拼 HTML
            def _verdict_card():
                if not _rows:
                    return ""
                _v, _ck, _sem = _CM.verdict(_rows)
                _vc = _CM.VERDICT_COLOR.get(_ck, "#d29922")
                _keys = _CM.key_levels(_rows)
                _v5 = next((b for b in _rows if b["per"] == "5m"), None)
                _m5seg = (" ｜ 日内 5m%s %s" % (_v5["pos"], _v5["piv"])
                          if _v5 else "")
                # 💼 操作建议子块：买卖点位+仓位档（口径走共享 trade_plan）
                _plan_html = ""
                _plan = _CM.trade_plan(_rows)
                if _plan:
                    _m = _plan["mgmt"]
                    _pg = ["💼 <b>操作建议</b>：仓位档 <b>%s</b>（%s）"
                           % (_plan["size"], _plan["ladder"])]
                    _pg.append("分批：<b>%s</b> ｜ 上限：%s"
                               % (_m["steps"], _m["cap"]))
                    _pg.append("调仓：%s" % _m["rebalance"])
                    _dyn = _CM.dynamic_pos(_rows)
                    _pg.append("🔄 <b>动态调仓</b>：目标 <b>%s</b>（≤%d%%）"
                               "｜ 动作：<b>%s</b>"
                               "<span class='dim' style='font-size:11px'>"
                               "（影子账本·自动对比上次档位·不下单）</span>"
                               % (_dyn["name"], _dyn["pct"],
                                  _dyn["action"]))
                    if _plan["buy"]:
                        _pg.append("<b style='color:#f85149'>买</b>：%s"
                                   % " ｜ ".join(_plan["buy"]))
                    if _plan["sell"]:
                        _pg.append("<b style='color:#3fb950'>卖</b>：%s"
                                   % " ｜ ".join(_plan["sell"]))
                    _pg.append("<span class='dim' style='font-size:11px'>"
                               "研究视角 · 非投资建议 · 仓位百分比为策略"
                               "配置参数（chanlun_market.POS_SIZE）</span>")
                    _plan_html = ("<div style='font-size:13px;"
                                  "line-height:1.9;margin-top:6px;"
                                  "border-top:1px dashed #30363d;"
                                  "padding-top:6px'>%s</div>"
                                  % "<br>".join(_pg))
                return ("<div style='border:1px solid %s;border-radius:8px;"
                        "padding:10px 14px;margin-top:10px;"
                        "background:#161b22'>"
                        "<div style='font-size:15px'><b style='color:%s'>"
                        "🎯 综合研判：%s</b><span class='dim' "
                        "style='font-size:12px'>（日线%s × 30m%s × 5m%s）"
                        "</span></div>"
                        "<div class='dim' style='font-size:13px;"
                        "line-height:1.9;margin-top:4px'>%s</div>"
                        "<div style='font-size:13px;line-height:1.9;"
                        "margin-top:4px'>🔑 多空分界：%s%s</div>%s</div>"
                        % (_vc + "44", _vc, _v,
                           _d["trend"] if _d else "—",
                           _m30["trend"] if _m30 else "—",
                           _v5["trend"] if _v5 else "—",
                           _sem, " ｜ ".join(_keys), _m5seg, _plan_html))

            _vcard = _verdict_card()
            html = ("<div class='sec'><h2>📐 级别联动 · 多级别拆解"
                    "<span class='dim' style='font-size:12px'>（上证指数 "
                    "· 引擎实时 · 60s 刷新 · 周线数据未接入，日线两中枢"
                    "上移已隐含日线级趋势）</span></h2>"
                    "<table><tr><th>级别</th><th>走势</th><th>最近中枢</th>"
                    "<th>现价位置</th><th>观察语义</th><th>最近信号</th>"
                    "</tr>%s</table>"
                    "<div style='font-size:13.5px;line-height:2;margin-top:8px'>"
                    "🔗 <b>联动</b>：%s</div>%s</div>"
                    % ("".join(rows), concl, _vcard))
            type(self)._cw_ml_cache = (_now4, html)
            return html

        _cw_ml_html = _cw_stage_rows()

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>缠论 · 结构交易工作台</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("chanlun")
                + "<h1>🧭 缠论 · 结构交易工作台</h1><div class='dim'>"
                "一句话：<b style='color:#e6edf3'>分型→笔→线段→中枢 → 三类"
                "买卖点 → 全侧面影子标注（M✓/盘✓/量✓/势✓/趋✓/npv/mls），"
                "只呈现不拦截</b> · 🇨🇳A股已接入 · 🇺🇸美股已接入 · 🇭🇰港股"
                "已接入 · 🪙加密待接数据源 · 检出日收盘入场 · 20 交易日持有"
                " · 每日定时跑数→本页看信号→"
                "<a href='/bt-chanlun-pool'>🛟 预警池明细</a>（全量+持仓盯市）"
                "</div>"
                # K-STOCK-030 统一骨架②：工具链紧贴标题（原沉底尾部）
                + "<div style='display:flex;gap:6px;flex-wrap:wrap;"
                "margin:10px 0'>" + tools + "</div>"
                # ① 态势状态条（三市场扫描任务真实状态 + 池水位）
                + ("<div class='sec' style='margin-top:12px;padding:10px 15px;"
                   "display:flex;gap:6px 22px;flex-wrap:wrap;"
                   "align-items:baseline;font-size:13px'>%s%s%s</div>"
                   % ("<span>🇨🇳 <b>ChanlunScan</b> %s · %s ｜ 📥 池 <b>%d</b>"
                      " 条 · 最新检出 <b>%s</b></span>"
                      % (_cn_disp, _cn_rc,
                         len(_cn_rs), _cw_last(_cn_rs) or "—"),
                      "<span>🇺🇸 <b>ChanlunScanUS</b> %s · %s ｜ 📥 池 <b>%d</b>"
                      " 条 · 最新检出 <b>%s</b></span>"
                      % (_us_disp, _us_rc,
                         len(_us_rs), _cw_last(_us_rs) or "—"),
                      "<span>🇭🇰 <b>ChanlunScanHK</b> %s · %s ｜ 📥 池 <b>%d</b>"
                      " 条 · 最新检出 <b>%s</b></span>"
                      % (_hk_disp, _hk_rc,
                         len(_hk_rs), _cw_last(_hk_rs) or "—")))
                # ①.5 大盘阶段判断（首页第一屏核心）
                + _cw_stage_html
                + _cw_ml_html
                # ② 策略说明书
                + ("<div class='sec'><h2>📖 策略说明书</h2><div class='dim' "
                   "style='font-size:13px;line-height:2.1'>"
                   "信号口径：检出日收盘入场 · 20 交易日持有 · 跨除权剔除 · "
                   "卖点为方向统计（跌为胜）<br>"
                   "数据链：🇨🇳 A股 tushare 日线（23:35 增量同步）· 每日 "
                   "15:35 盘后扫描 · 06:30 次日补齐重扫 ｜ 🇺🇸 美股 zhice-db "
                   "日线（每日增量）· 09:00 扫描 ｜ 🇭🇰 港股 zhice-db 日线 · "
                   "17:30 扫描 · 存量过 120 根后自动出信号<br>"
                   "扩展：缠论引擎纯 K 线驱动、市场无关——任一市场接日线数据"
                   "源即可开跑，口径不变</div></div>")
                # ③ 环境闸门（影子呈现，不否决）
                + ("<div class='sec'><h2>🚦 环境闸门"
                   "<span class='dim' style='font-size:12px'>（与青萍之末"
                   "同源快照 · 缠论 P8 大盘侧为影子标注 势↑/势↓——只呈现，"
                   "不参与一票否决、不改入池口径）</span></h2>%s</div>"
                   % _gates_html)
                # ④ 信号清单（三市场各最近 10 条）
                + ("<div class='sec'><h2>🎯 信号清单"
                   "<span class='dim' style='font-size:12px'>（各市场最近 "
                   "10 条 · 新→旧 · 全量与持仓盯市见 "
                   "<a href='/bt-chanlun-pool'>🛟 预警池</a>）</span></h2>%s"
                   "</div>" % _sig_html)
                + "<div class='sec'><details><summary style='cursor:pointer;"
                "font-weight:600'>🗺 方法体系 → 工具箱映射"
                "<span class='dim' style='font-size:12px;font-weight:400'>"
                "（12 项方法详表 · 点击展开）</span></summary>"
                "<table style='margin-top:10px'><tr><th>方法</th><th>工具入口"
                "</th><th>数据字段</th><th>状态</th></tr>"
                + steps + "</table></details></div>"
                + "<div class='sec'><details><summary style='cursor:pointer;"
                "font-weight:600'>⏳ 当前状态 / 方法详述"
                "<span class='dim' style='font-size:12px;font-weight:400'>"
                "（P1~P9 全量进度 · 点击展开）</span></summary>"
                "<div class='dim' "
                "style='font-size:13px;line-height:2.1'>P1 已完成（2026-09-26）：四市场多周期 K 线统一加载 + 查看"
                "器上线。数据口径：日线 = zhice-db 四市场统一；60/30 分钟 "
                "CN = tushare 原生（历史深）、加密 = 币安公共 K 线、US/HK "
                "= db 1 分钟基线重采样（存量较短：US 约 2026-08-18 起、HK "
                "约 2026-09-02 起，部分标的缺失，属真实存量而非缺陷）。缠"
                "论结构识别（P2 全部三层）已于 2026-09-26 上线并在下方查"
                "看器叠加展示：红▼=顶 / 绿▲=底（处理后分型严格交替）、黄"
                "线=笔（新笔口径：相邻异性分型相连、跨度≥4）、蓝线=线段"
                "（破坏确认法简化口径，终结点必为线段极值）、紫框=中枢"
                "（笔中枢：三笔重叠定义）。P3 信号层已于同日上线：绿圆标="
                "1/2/3 类买点、红圆标=1/2/3 类卖点（简化口径：一类=线段终"
                "结+段内动量衰减，二类=转折后首回踩不破极值，三类=中枢离"
                "开后回踩不回区间，回踩笔须完结确认）。P4 验证层已于同日上"
                "线：查看器加载 K 线时同步给出三类买卖点的事件统计（20 根"
                "窗口方向胜率）与策略回测摘要（买点开/卖点平、理论价±成本，"
                "信号确认依赖后续笔形成，非可成交价）。2026-09-27 样本首评"
                "（27 条）：以「确认滞后重放」可成交口径复核，理论价 win=100%"
                " 系确认滞后未来函数假象（1买确认滞后中位 21 根、确认时价格"
                "平均已高于理论低点 23.6%）；可成交口径仅 1买达标（n=12 · "
                "胜率 73% · 均值 +7.6%），3买现追高负贡献嫌疑（确认时已 "
                "+11%、其后 20 根均值 -12.5%）。信号推送链路保持冻结；下方"
                "「样本统计」面板随每日样本任务刷新持续跟踪。P5「3买背驰"
                "过滤」已于 2026-09-27 以影子模式上线：3买/3卖信号附盘整背驰"
                "标注——回踩/反抽笔力度弱于中枢离开笔（与一类动量衰减同一"
                "力度口径，阈值 1.0）记 ✓，仅标注不拦截，查看器圆标可见；"
                "首评中两条已成熟且亏损的 3买（AMAT/PANW）均为无背驰 ✗，"
                "3卖背驰子集 +8.34% 优于全集 +4.35%，但背驰 ✓ 的 3买 r20 "
                "窗口尚未成熟。面板按日对比过滤子集，待子集 n≥10 且可成交"
                "口径显著优于全集后再启用拦截。2026-09-27 教学复刻盘点后"
                "P6-b 正宗盘整背驰同日以影子模式上线（bei2/fv2）：本次回踩"
                "/反抽笔力度弱于离开笔之前最近一根同向腿（教学 a+A+b 的同向"
                "力度衰竭表达，阈值 1.0）记「盘✓」，与 P5 ✓ 并行影子、面板"
                "两列按日对比择优，同样不拦截。P6-a MACD 背驰同日以影子模式"
                "上线（mbei/mfv，仅 1买/1卖）：信号笔终点价格创新低/高而该点"
                "DIF 较上一同向笔终点反向收敛记 M✓（DIF 极值位简化口径，非"
                "红绿柱面积），查看器圆标紫色 M✓/M✗；首日影子数据 1买 M✓ 子集"
                " 2/12 · 可成交 r20 均值 +0.2%（全集 +7.6%），1卖 1/5 · +5.2%"
                "（全集 +9.1%）——样本极少不构成结论，暂未显示出过滤优势，"
                "继续按日积累。P7 多级别联立于同日以影子模式上线（mt/mtv）："
                "日线信号终结笔窗口内 60m 出现同向 1类+MACD 背驰记「次✓」，"
                "即教学「大级别定位、次级别择时」；受分钟存量所限（US 约 "
                "08-18、HK 约 09-02 起，CN 走 tushare、CRYPTO 走币安覆盖较"
                "深），窗口未覆盖一律 None 不计 ✗，面板单列覆盖数，样本随"
                "每日任务向前积累。P8 资金/大盘共振于 2026-09-26 以影子模式"
                "上线（vv/reg）：量能侧取信号终结笔均量 vs 上一同向笔，缩量"
                "记「量✓」（买=卖压衰竭/回踩惜售、卖=追高乏力，全类型）；"
                "大盘侧取 CN 上证 / CRYPTO BTC 收盘 vs MA20/60，买 up、卖 "
                "down 为顺风记「势↑/势↓」，US/HK 库内无指数一律 None 不计；"
                "存量 27 条首日回填 15 个标注，量✓ 1买子集 3 例可成交 r20 "
                "均值 +17.3%（全集 +7.6%）、3卖 3 例 +4.1%，大盘顺风覆盖尚少"
                "（指数库自 2025-07 起、US/HK 无指数），均继续按日影子积累，"
                "达标择优后再谈拦截。P6-c 趋势背驰完整版 2026-09-26 影子上线"
                "（tbe/tfv，教学 a+A+b+B+c）：信号前需 ≥2 个同向不重叠中枢"
                "（趋势方向由两中枢位置关系定，非离开笔瞬时方向）、信号价在"
                "末中枢趋势侧之外、末段 c 笔力度弱于 A→B 连接段 b（b 取 A 末"
                "笔至 B 成枢段内最强同向笔——find_pivots 按区间重叠延伸中枢，"
                "贯穿中枢的大离开笔会被记为 A 末笔）；ZG/ZD 区间沿判定为严格"
                "GG/DD 口径的简化。日线存量下多数 1 类信号前不足 2 中枢（记 "
                "None 不判），首日仅 600519 1买 2026-06-26 可判且趋✓（A "
                "1532~1576、B 1401~1452，b 243.8 > c 144.0），但可成交 r20 "
                "为 -5.18%（单点不构成结论，且与 60m 深历史验证中 ETH 两例"
                "趋✓后大涨方向一致，继续按日积累）；60m 级别同样生效。</div>"
                "</details></div>"
                + ev_panel
                + tbe_panel
                + comp_panel
                + "<div class='sec'><h2>📊 K 线查看器 · 结构叠加（分型/笔/线段/中枢/买卖点）</h2>"
                + viewer + "</div>"
                + "<div class='dim' style='margin-top:20px'>纸面研究不构成"
                "投资建议 · 缠论结构交易工作台 · P1~P4 已上线 · P5~P9 影子"
                "验证中 · 口径/样本/验证总纲见本页面板区与折叠详述</div>"
                + js + "</body></html>")
        self._html(body)


    def _chanlun_anno(self, qs):
        """/chanlun-anno?c=&p=  GET→列表 JSON；POST price/label/color/del。
        手工标注：像画图一样在缠论图上标关键位（存 JSON，出图时叠加）。"""
        import json as _jp
        import re as _re
        from urllib.parse import parse_qs as _pq
        c = (_pq(qs).get("c") or [""])[0].strip()[:12]
        p = (_pq(qs).get("p") or ["1d"])[0].strip()
        if not _re.fullmatch(r"[0-9A-Za-z.]+", c) or \
                p not in ("1d", "60m", "30m", "5m", "1w", "1M"):
            self._json({"ok": False, "err": "bad code/period"}, 400)
            return
        key = self._anno_key(c, p)
        if self.command == "POST":
            ln = int(self.headers.get("Content-Length") or 0)
            body = {}
            if ln:
                try:
                    body = _jp.loads(self.rfile.read(ln).decode("utf-8"))
                except Exception:
                    body = {}
            data = self._anno_load()
            rows = [r for r in data.get(key, [])
                    if isinstance(r, dict)]
            if body.get("del") is not None:
                try:
                    rows.pop(int(body["del"]))
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
            else:
                try:
                    price = round(float(body.get("price")), 2)
                    label = str(body.get("label") or "")[:24].strip()
                    color = str(body.get("color") or "#f85149")
                    if color not in ("#f85149", "#3fb950", "#e3b341",
                                     "#58a6ff", "#f778ba"):
                        color = "#f85149"
                    if price > 0 and label:
                        rows.append({"price": price, "label": label,
                                     "color": color})
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
            data[key] = rows
            os.makedirs(os.path.dirname(self._ANNO_PATH), exist_ok=True)
            with open(self._ANNO_PATH, "w", encoding="utf-8") as f:
                _jp.dump(data, f, ensure_ascii=False, indent=1)
            self._json({"ok": True, "rows": rows})
            return
        self._json({"ok": True, "rows": self._anno_load().get(key, [])})


    @staticmethod
    def _anno_key(c, p):
        return "%s|%s" % (c, p)


    def _anno_load(self):
        data = _read_json(self._ANNO_PATH)
        return data if isinstance(data, dict) else {}


    def _chanlun_png(self, qs):
        """GET /chanlun-png?c=000001.SZ&p=1d → 缠论结构图 PNG。

        优先 reports/card_png 缓存；有手工标注或标注新于缓存时现场
        重生成并叠加标注横线 + 筹码密集带；无缓存时兜底生成。"""
        import glob as _glob
        import re as _re
        import html as _h
        from urllib.parse import parse_qs as _pq
        c = (_pq(qs).get("c") or [""])[0].strip()[:12]
        if not _re.fullmatch(r"[0-9A-Za-z.]+", c):
            return self._html("<h3>bad code</h3>", 400)
        # 前缀归一：北交所 92 开头码强制 bj（CD.load_kline 不认 sz920950）
        _m6 = _re.fullmatch(r"(sh|sz|bj)(\d{6})", c, _re.I)
        if _m6:
            c = ("bj" if _m6.group(2).startswith("92")
                 else _m6.group(1).lower()) + _m6.group(2)
        p = (_pq(qs).get("p") or ["1d"])[0].strip()
        if p not in ("1d", "60m", "30m", "5m", "1w", "1M"):
            p = "1d"
        out_dir = os.path.join(STATION, "reports", "card_png")
        png = None
        # plot_level 文件名会把 code 的 '.' 换成 '_'（000025.SZ→000025_SZ）
        hits = _glob.glob(os.path.join(out_dir, "%s_%s_*.png"
                                       % (c.replace(".", "_"), p)))
        if hits:
            png = max(hits, key=os.path.getmtime)
        annos = self._anno_load().get(self._anno_key(c, p), [])
        need_regen = bool(annos) and (
            png is None
            or (os.path.exists(self._ANNO_PATH)
                and os.path.getmtime(self._ANNO_PATH)
                > os.path.getmtime(png)))
        if need_regen or png is None:
            try:
                import importlib as _il
                _cp = _il.import_module("chanlun_plot")
                ovs = [(a.get("price"), a.get("label"), a.get("color"))
                       for a in annos if isinstance(a, dict)]
                if not ovs:
                    # K78b: 信号票无手工标注时自动叠加 入场/止损/目标 线
                    ovs = self._chanlun_auto_lines(c)
                ch = None
                if (_pq(qs).get("chip") or ["1"])[0] not in ("0", "false"):
                    ch = _cp.chip_band(c, p)
                np_ = _cp.plot_level(c, p, _cp.SHOW_DEFAULT.get(p, 500),
                                     overlays=ovs, chip=ch)
                if np_ and os.path.exists(str(np_)):
                    png = np_
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
        if png and os.path.exists(str(png)):
            data = open(png, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._html("<h3>no chart for %s（该级别数据不足，"
                   "换日线/30分钟查看）</h3>" % _h.escape(c))


    def _chanlun_day_plots_sec(self):
        """当日信号缠论结构图网格（读 plot_day_signals 写的 manifest）。"""
        import html as _h
        mf = os.path.join(STATION, "quant_backtest", "data",
                          "chanlun_plots_manifest.json")
        rows = _read_json(mf)
        if not rows:
            return ("<div class='sec'><h2>🖼 当日结构图</h2><div class='dim'>"
                    "今日暂无出图（盘后 15:35 主跑后自动生成；也可直接点"
                    "信号行下钻查看）</div></div>")
        cells = ""
        for r in rows[:12]:
            c = str(r.get("code") or "")
            cells += ("<div style='background:#161b22;border:1px solid "
                      "#30363d;border-radius:8px;padding:8px'>"
                      "<div style='font-size:12.5px;margin-bottom:4px'>"
                      "<b>%s</b> %s · <span style='color:%s'>%s</span>"
                      " · %s</div>"
                      "<a href='/chanlun-png?c=%s' target='_blank' "
                      "title='点击放大'><img src='/chanlun-png?c=%s' "
                      "loading='lazy' style='width:100%%;border-radius:"
                      "4px'></a></div>"
                      % (_h.escape(c), _h.escape(str(r.get("name") or "")),
                         "#f85149" if "卖" in str(r.get("type")) else "#3fb950",
                         _h.escape(str(r.get("type") or "")),
                         _h.escape(str(r.get("dt") or "")), c, c))
        more = "" if len(rows) <= 12 else (
            "<div class='dim' style='margin-top:6px'>仅展示前 12 / 共 %d 张"
            "</div>" % len(rows))
        return ("<div class='sec'><h2>🖼 当日结构图"
                "<span class='dim' style='font-size:12px'>（盘后主跑自动"
                "出图 · 次日 06:30 数据补齐后重出 · 点击放大）</span></h2>"
                "<div style='display:grid;grid-template-columns:repeat("
                "auto-fill,minmax(400px,1fr));gap:12px'>%s</div>%s</div>"
                % (cells, more))


    def _chanlun_pool_page(self, qs=""):
        """🛟 预警池：缠论扫描 → 模拟交易战绩（影子观察，不推送）。

        ?m=US|HK 切换市场池链（独立文件 _us/_hk 后缀）；默认 CN 零变化。
        数据源（每日盘后扫描任务刷新，页面只读不重放）：chanlun_pool*.jsonl
        （入池信号）、chanlun_paper*.json（模拟持仓账本）、
        chanlun_pool_eval_cache*.json（侧面战绩缓存）。"""
        import json as _jp
        from urllib.parse import parse_qs as _pqs0
        mkt = ((_pqs0(qs or "").get("m") or [""])[0]
               or "CN").strip().upper()
        if mkt not in ("CN", "US", "HK"):
            mkt = "CN"
        _sfx = {"CN": "", "US": "_us", "HK": "_hk"}[mkt]
        pool = []
        try:
            with open(os.path.join(QBT_DIR, "data",
                                   "chanlun_pool%s.jsonl" % _sfx),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        pool.append(_jp.loads(line))
                    except Exception:
                        continue
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
        paper, ev = {}, {}
        try:
            with open(os.path.join(QBT_DIR, "data",
                                   "chanlun_paper%s.json" % _sfx),
                      encoding="utf-8") as f:
                paper = _jp.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
        try:
            with open(os.path.join(QBT_DIR, "data",
                                   "chanlun_pool_eval_cache%s.json" % _sfx),
                      encoding="utf-8") as f:
                ev = _jp.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
        # 影子日志：1卖+tbe=True 专项追踪（chanlun_scan.refresh_shadow_1sell_tbe
        # 投影自 pool+paper，记录 type/dt/code/price/tbe/lag/exe20）
        shadow = []
        try:
            with open(os.path.join(QBT_DIR, "data",
                                   "chanlun_shadow_1sell_tbe%s.jsonl" % _sfx),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        shadow.append(_jp.loads(line))
                    except Exception:
                        continue
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)

        # 侧面标签（配色对齐缠论查看器图例；True 彩色 / False 灰✗）
        _S = [("M✓", "#bc8cff", "mbei"), ("✓", "#f85149", "bei"),
              ("盘✓", "#58a6ff", "bei2"), ("量✓", "#f0883e", "vv"),
              ("趋✓", "#f778ba", "tbe")]

        def _tags(row):
            sp = []
            for lb, col, k in _S:
                v = row.get(k)
                if v is True:
                    sp.append("<span style='color:%s;font-weight:600'>%s"
                              "</span>" % (col, lb))
                elif v is False:
                    sp.append("<span style='color:#484f58'>%s✗</span>" % lb)
            r = row.get("reg")
            if r == "up":
                sp.append("<span style='color:#79c0ff;font-weight:600'>"
                          "势↑</span>")
            elif r == "down":
                sp.append("<span style='color:#79c0ff;font-weight:600'>"
                          "势↓</span>")
            elif r is not None:
                sp.append("<span style='color:#484f58'>势–</span>")
            return " ".join(sp) or "<span class='dim'>—</span>"

        def _mls_h(r):
            """E3 mls 影子分（LightGBM win 概率 0~1）：中性灰展示，
            K28 产线接入（2026-10-02）后的新信号自动带分，旧信号 —。"""
            v = r.get("mls")
            if v is None:
                return "<span class='dim'>—</span>"
            return "<span style='color:#8b949e'>%.3f</span>" % v

        # 最新检出日提前计算（rows 行高亮与 strong 预警共用）
        last_day = max(((r.get("detect_date") or "") for r in pool),
                       default="")
        rows = pool[-100:][::-1]

        def _tp(t, hot):
            # 类型徽章色（卖=红 / 买=绿，同工作台信号清单口径）；
            # hot=1卖·趋✓ 保留红粗 + 🚨 强预警标记
            col = "#f85149" if "卖" in str(t) else "#3fb950"
            inner = "<b style='color:%s'>%s</b>" % (col, t)
            if hot:
                inner = ("<span style='color:#f85149;font-weight:600'>"
                         "%s 🚨</span>" % t)
            if t in ("2买", "3买"):
                # K267 复评 10-08 老田拍板 A：日线 20 天口径负期望信号族
                # ——徽章追加 ⚠（悬停战绩），入池/研究链不动
                inner += (" <span style='color:#d29922'"
                          " title='负期望信号族：日线20天 win 38%/32%，"
                          "avg −2.3%/−5.3%（10-08 复评）——仅研究参考'"
                          ">⚠</span>")
            # 下钻：点信号类型 → 缠论样本面板（同类型战绩/重放）；
            # 样本面板为 CN 口径，US/HK 徽章不带链接
            if mkt != "CN":
                return inner
            return ("<a href='/bt-chanlun' style='text-decoration:none' "
                    "title='下钻到缠论样本面板'>%s</a>" % inner)

        def _code_lk(code):
            # 下钻：点代码 → 个股全景页（四市场：_bt_stock_page 按代码
            # 前缀推断市场，resolve 失败显示识别表单优雅降级）
            return ("<a href='/kg/stock/%s'><b>%s</b></a>"
                    % (code, code))

        tr_rows = "".join(
            "<tr%s><td>%s</td><td>%s</td><td>%s</td>"
            "<td class='dim'>%s</td><td%s>%s</td>"
            "<td style='text-align:right'>%.2f</td><td>%s</td>"
            "<td>%s</td></tr>"
            % ((" style='box-shadow:inset 2px 0 0 #58a6ff'"
                if r.get("detect_date") == last_day else ""),
               _code_lk(r.get("code")), (r.get("name") or "—"),
               _tp(r.get("type"), r.get("type") == "1卖"
                   and r.get("tbe") is True),
               r.get("dt"),
               (" style='font-weight:600'"
                if r.get("detect_date") == last_day else " class='dim'"),
               r.get("detect_date"), r.get("entry_px") or 0,
               _mls_h(r), _tags(r)) for r in rows) or (
            "<tr><td colspan='8' class='dim'>尚无入池信号——%s</td></tr>"
            % {"CN": "首次回填 / 每日扫描完成后展示",
               "US": "每日 09:00 扫描（ChanlunScanUS）完成后展示",
               "HK": "db 存量约 98 根/只（需 120），过线后自动出信号"}[mkt])

        SIDES_C = [("M✓ MACD", "mbei"), ("✓ 盘整", "bei"),
                   ("盘✓ 同向腿", "bei2"), ("量✓ 缩量", "vv"),
                   ("势✓ 顺风", "reg"), ("趋✓ 趋势", "tbe")]

        def _cell(s):
            if not s:
                return "<span class='dim'>—</span>"
            sty = "" if s["n"] >= 10 else " style='color:#484f58'"
            return ("<span%s>n=%d · 胜%.0f%% · %+.2f%%</span>"
                    % (sty, s["n"], s["win"], s["avg"]))

        tr_eval = ""
        for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
            a = (ev.get("types") or {}).get(t)
            if not a:
                continue
            cells = "".join("<td>%s</td>" % _cell(a["sides"].get(k))
                            for _, k in SIDES_C)
            tr_eval += ("<tr><td><b style='color:%s'>%s</b></td><td>%s</td>"
                        "<td class='dim'>已平 %s</td>%s</tr>"
                        % ("#f85149" if "卖" in t else "#3fb950",
                           t, _cell(a.get("all")),
                           a.get("closed_n") or 0, cells))
        if not tr_eval:
            tr_eval = ("<tr><td colspan='8' class='dim'>尚无战绩——"
                       "模拟仓位到期平仓后积累</td></tr>")

        op = (paper.get("open") or [])[:30]
        tr_open = "".join(
            "<tr><td>%s<span class='dim'> %s</span></td><td>%s</td>"
            "<td class='dim'>%s</td><td style='text-align:right'>%d</td>"
            "<td style='text-align:right'>%.2f</td>"
            "<td style='text-align:right;color:%s'>%+.2f%%</td></tr>"
            % (_code_lk(p.get("code")), (p.get("name") or "—"),
               _tp(p.get("type"), p.get("type") == "1卖"
                   and (p.get("tags") or {}).get("tbe") is True),
               p.get("entry_date"), p.get("days_held") or 0,
               p.get("last_px") or 0,
               "#f85149" if (p.get("pnl") or 0) > 0
               else "#3fb950" if (p.get("pnl") or 0) < 0 else "#8b949e",
               (p.get("pnl") or 0) * 100.0) for p in op) or (
            "<tr><td colspan='6' class='dim'>当前无持仓</td></tr>")

        # 态势感知数据源：任务状态查询走 60s 进程内缓存（曾每次开页同步跑
        # PowerShell 最长 15s，是打开慢/内容后弹的原因之一）；缓存按市场
        # 分键——曾单键跨市场串味（?m=US 命中 CN 页 60s 内刚写的缓存）
        import time as _tt
        _now = _tt.time()
        _tname = {"CN": "ChanlunScan", "US": "ChanlunScanUS",
                  "HK": "ChanlunScanHK"}[mkt]
        _tsc = getattr(type(self), "_tstat_cache", None) or {}
        _ent = _tsc.get(mkt)
        if _ent and _now - _ent[0] < 60:
            tstat = _ent[1]
        else:
            out = run(["powershell", "-NoProfile", "-Command",
                       "[Console]::OutputEncoding="
                       "[System.Text.Encoding]::UTF8;"
                       "$i=Get-ScheduledTaskInfo -TaskName '%s' "
                       "-ErrorAction SilentlyContinue;"
                       "if($i){'last=' + $i.LastRunTime + '  result=' + "
                       "$i.LastTaskResult + '  next=' + $i.NextRunTime}"
                       "else{'not registered'}" % _tname], timeout=15)
            lines = [x for x in (out or "").strip().splitlines() if x.strip()]
            tstat = lines[-1] if lines else "任务状态查询失败"
            _tsc2 = dict(getattr(type(self), "_tstat_cache", None) or {})
            _tsc2[mkt] = (_now, tstat)
            type(self)._tstat_cache = _tsc2

        # ── 利空消息警示（回测 v2：偏空 T+1 跑输市场 ~0.5pp；
        #    只提示不拦截，口径见 docs/ops/portal_llm观察记录.md）──
        cn_map = {}
        for r in pool[-200:]:
            if r.get("code"):
                cn_map[r["code"]] = (r.get("name") or "").strip()
        for p in (paper.get("open") or []):
            if p.get("code"):
                cn_map.setdefault(p["code"], (p.get("name") or "").strip())
        # 开页慢专项：bear_news 每开页同步查 news.db（冷态 ~600ms）；
        # 300s TTL 进程内缓存（仅提示场景，5 分钟滞后可接受）
        import time as _tt2
        _now2 = _tt2.time()
        _bnc = getattr(type(self), "_bearnews_cache", None)
        if _bnc and _now2 - _bnc[0] < 300:
            bear_news = _bnc[1]
        else:
            try:
                bear_news = _pool_bear_news(cn_map) if mkt == "CN" else []
            except Exception:
                bear_news = []
            type(self)._bearnews_cache = (_now2, bear_news)

        _mkt_cn = {"CN": "全A股", "US": "美股", "HK": "港股"}[mkt]
        _scan_desc = {
            "CN": "🇨🇳 A股每日 15:35 盘后扫描约 5,593 只",
            "US": "🇺🇸 美股每日 09:00 扫描（ChanlunScanUS · 活跃 ~503 只/日）",
            "HK": "🇭🇰 港股每日 17:30 扫描（ChanlunScanHK · 存量积累中）",
        }[mkt]
        _drill_tip = ("点代码进个股看结构图细节" if mkt == "CN"
                      else "US/HK 明细页暂无个股下钻与结构图")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>缠论预警池 · %s扫描 · "
                "模拟交易（影子）</title>" % _mkt_cn + self._COCKPIT_CSS
                + "</head><body>" + pill_nav("chanlun-pool")
                + "<h1>🛟 缠论预警池 · 结构信号工作台</h1><div class='dim'>"
                "全市场缠论方法论（<b style='color:#e6edf3'>只看结构不看市场"
                "</b>——有 K 线即可跑）：🇨🇳 A股已接入 · 🇺🇸 美股已接入 · "
                "🇭🇰 港股已接入 · 🪙 加密待接数据源 · %s · 检出日收盘"
                "入场 · 20 交易日持有 · <b style='color:#d29922'>影子"
                "观察 · 不推送不拦截</b> · 每日定时跑数→本页看信号→%s · "
                "口径/样本/验证见 <a href='/bt-chanlun'>"
                "🔬 缠论分析台</a>（方法论总纲在下方 📖 tab）</div>"
                % (_scan_desc, _drill_tip))
        strong = [r for r in pool if r.get("detect_date") == last_day
                  and r.get("type") == "1卖" and r.get("tbe") is True]
        _hot_sub = ("复评 n=79 胜60.8% avg+4.94%" if mkt == "CN"
                    else "影子呈现 · 复评基线仅 CN")
        if strong:
            items = " · ".join(
                "<b style='color:#3fb950'>%s %s</b>（信号 %s）"
                % (r.get("code"), r.get("name") or "", r.get("dt"))
                for r in strong[:20])
            more = " 等 %d 只" % len(strong) if len(strong) > 20 else ""
            alert = ("<div class='sec'><h2>🚨 今日强预警 · 1卖+趋✓"
                     "<span class='dim' style='font-size:12px'>"
                     "（%s 检出 · %s）"
                     "</span></h2><div style='font-size:14px'>%s%s</div>"
                     "</div>" % (last_day, _hot_sub, items, more))
        else:
            alert = ("<div class='sec'><h2>🚨 今日强预警 · 1卖+趋✓</h2>"
                     "<div class='dim'>%s 无 1卖·趋✓ 强预警信号（%s）"
                     "</div></div>" % (last_day or "—", _hot_sub))
        # ── 顶部态势感知状态条：扫描任务真实状态 + 数据水位 + 持仓水位 ──
        # 判色走共享 _sched_rc_html（SCHED_S 信息码语义化，267011≠异常）
        tstat_disp, _rc_h = _sched_rc_html(tstat)
        body += ("<div class='sec' style='margin-top:12px;padding:10px 15px;"
                 "display:flex;gap:6px 22px;flex-wrap:wrap;"
                 "align-items:baseline;font-size:13px'>"
                 "<span>⏰ <b>%s</b> %s · %s</span>"
                 "<span>📥 池 <b>%d</b> 条 · 最新检出 <b>%s</b></span>"
                 "<span>💼 持仓 <b>%d</b> · 已平 <b>%d</b></span></div>"
                 % (_tname, tstat_disp, _rc_h, len(pool), last_day or "—",
                    len(paper.get("open") or []),
                    len(paper.get("closed") or [])))
        # ── 策略说明书（学青萍之末 spec 范式：一句话总纲+数据源+扩展） ──
        body += ("<div class='sec' style='margin-top:12px'><h2>📖 策略说明书"
                 "</h2><div class='dim' style='font-size:13px;line-height:2.1'>"
                 "一句话：<b style='color:#e6edf3'>分型→笔→线段→中枢 → 三类"
                 "买卖点 → 全侧面影子标注（M✓/盘✓/量✓/势✓/趋✓/npv/mls），"
                 "只呈现不拦截</b><br>"
                 "信号口径：检出日收盘入场 · 20 交易日持有 · 跨除权剔除 · "
                 "卖点为方向统计（跌为胜）<br>"
                 "%s<br>"
                 "扩展：缠论引擎纯 K 线驱动、市场无关——任一市场接日线数据"
                 "源（zhice-db 已就绪）即可开跑，口径不变</div></div>"
                 % {"CN": "数据：A股 tushare 日线（23:35 增量同步）· 每日 "
                          "15:35 盘后扫描（ChanlunScan）· 06:30 次日补齐重扫",
                    "US": "数据：美股 zhice-db 日线（yfinance watchlist 每日"
                          "增量，活跃 ~503 只/日）· 每日 09:00 扫描"
                          "（ChanlunScanUS）",
                    "HK": "数据：港股 zhice-db 日线（tencent_hk 每日增量）· "
                          "每日 17:30 扫描（ChanlunScanHK）· db 存量过 120 "
                          "根后自动出信号"}[mkt])
        # ── 市场覆盖卡（学青萍之末 gates 范式）：动态水位——US/HK 扫描
        #    已接入（chanlun_scan --market US|HK，独立池链 _us/_hk），
        #    HK db 存量 <120 根/只，积累过线后自动出信号 ──
        def _mk_stat(sfx):
            """读市场池文件水位 → (n, last_detect)；无文件/空 → (0, None)。"""
            try:
                with open(os.path.join(QBT_DIR, "data",
                                       "chanlun_pool%s.jsonl" % sfx),
                          encoding="utf-8") as f:
                    rs = [json.loads(l) for l in f if l.strip()]
                return (len(rs),
                        max((r.get("detect_date") or ""
                             for r in rs), default="") or None)
            except Exception:
                return (0, None)
        _us_n, _us_d = _mk_stat("_us")
        _hk_n, _hk_d = _mk_stat("_hk")
        _cur_hint = ("<div style='margin-top:6px;font-size:12.5px;"
                     "color:#3fb950'>✅ 本页即 %s信号池</div>"
                     % {"CN": "A股", "US": "美股", "HK": "港股"}[mkt])
        _go_hint = ("<div style='margin-top:6px;font-size:12.5px;"
                    "color:#58a6ff'>🔗 点卡切换到该市场信号明细</div>")
        _mk_cards = (
            "<a class='gate g-on' href='/bt-chanlun-pool'><b>🇨🇳 A股</b>"
            "<span style='float:right;font-size:14px;font-weight:700;"
            "color:#3fb950'>已接入</span><div class='dim' style='margin-top:6px'>"
            "每日 15:35 盘后全市场扫描 · 池 %d 条 · 最新检出 %s</div>%s</a>"
            % (len(pool), last_day or "—",
               _cur_hint if mkt == "CN" else _go_hint))
        _mk_cards += (
            "<a class='gate g-on' href='/bt-chanlun-pool?m=US'><b>🇺🇸 美股</b>"
            "<span style='float:right;font-size:14px;font-weight:700;"
            "color:#3fb950'>已接入</span><div class='dim' style='margin-top:6px'>"
            "zhice-db 日线每日增量 → 缠论扫描 · 池 <b>%d</b> 条 · 最新检出 %s"
            "</div>%s</a>"
            % (_us_n, _us_d or "—",
               _cur_hint if mkt == "US" else _go_hint))
        _mk_cards += (
            "<a class='gate g-on' href='/bt-chanlun-pool?m=HK'><b>🇭🇰 港股</b>"
            "<span style='float:right;font-size:14px;font-weight:700;"
            "color:#d29922'>积累中</span><div class='dim' style='margin-top:6px'>"
            "zhice-db 日线每日增量 → 缠论扫描 · 池 <b>%d</b> 条%s</div>%s</a>"
            % (_hk_n, " · 最新检出 %s" % _hk_d if _hk_d else "",
               _cur_hint if mkt == "HK" else _go_hint))
        _mk_cards += (
            "<a class='gate' style='opacity:.62;cursor:default'>"
            "<b>🪙 加密</b><span style='float:right;font-size:14px;"
            "font-weight:700;color:#8b949e'>待接入</span>"
            "<div class='dim' style='margin-top:6px'>缠论引擎纯 K 线驱动"
            "（市场无关）</div><div style='margin-top:6px;font-size:12.5px;"
            "color:#8b949e'>🔌 zhice-db 日线就绪 · 接入即扫描</div></a>")
        body += ("<div class='sec' style='margin-top:12px'><h2>🗺 市场覆盖"
                 "<span class='dim' style='font-size:12px'>（缠论只认结构不认"
                 "市场：三类买卖点 + 全侧面标注口径全市场一致）</span></h2>"
                 "<div class='gates'>%s</div></div>" % _mk_cards)
        body += ("<div class='tabwrap'><div class='tabs'>"
                 "<button class='on' onclick=\"_tab(this,'cpt-today')\">"
                 "🚨 今日预警与信号</button>"
                 "<button onclick=\"_tab(this,'cpt-shadow')\">👁 影子追踪"
                 "与口径</button>"
                 "<button onclick=\"_tab(this,'cpt-hist')\">🏆 战绩档案"
                 "</button>"
                 "<button onclick=\"_tab(this,'cpt-doc')\">📖 方法论总纲"
                 "</button></div>"
                 "<div class='tabc on' id='cpt-today'>")
        body += alert
        # 利空消息警示块（仅提示；无命中时不占卡片、一行淡字带过）
        if bear_news:
            import html as _hb
            brows = "".join(
                "<tr><td class='dim'>%s</td>"
                "<td><a href='/kg/stock/%s'><b>%s</b></a>"
                "<span class='dim'> %s</span></td>"
                "<td style='max-width:460px;white-space:nowrap;overflow:hidden;"
                "text-overflow:ellipsis' title='%s'>%s</td>"
                "<td><span style='display:inline-block;padding:1px 9px;"
                "border-radius:10px;font-size:12px;font-weight:600;"
                "color:#fff;background:%s'>%d%%</span></td></tr>"
                % (b["ts"][:10], b["code"], b["code"],
                   b["name"] or "—",
                   _hb.escape(b["title"] or "", quote=True),
                   (b["title"][:48] + "…") if len(b["title"] or "") > 48
                   else b["title"],
                   "#f85149" if (b["conf"] or 0) >= 85 else "#d29922",
                   b["conf"])
                for b in bear_news)
            body += ("<div class='sec'><h2>⚠️ 利空消息警示"
                     "<span class='dim' style='font-size:12px'>"
                     "（近5日 %d 条 · LLM 利空判定 · 置信≥75%% · 回测 v2 偏空"
                     " T+1 跑输市场 0.49pp · 仅提示不拦截 · 悬浮看全文）"
                     "</span></h2>"
                     "<table><tr><th>消息日</th><th>代码 / 名称</th>"
                     "<th>消息</th><th>置信</th></tr>%s</table></div>"
                     % (len(bear_news), brows))
        elif mkt == "CN":
            body += ("<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                     "⚠️ 利空消息警示：近5日池内个股无高置信（≥75%）利空判定"
                     "（回测 v2 偏空 T+1 跑输市场 0.49pp，仅提示不拦截）</div>")
        body += ("<div class='sec'><h2>💼 当前模拟持仓"
                 "<span class='dim' style='font-size:12px'>（前 30，"
                 "盯市至最后收盘 · 入场=检出日收盘）</span></h2>"
                 "<table><tr><th>代码 / 名称</th><th>类型</th>"
                 "<th>检出日</th><th>持有日</th><th>现价</th>"
                 "<th>浮动</th></tr>%s</table></div>" % tr_open)
        body += ("<div class='sec'><h2>🆕 最近入池信号"
                 "<span class='dim' style='font-size:12px'>（最新 100 · "
                 "今日新入 <b style='color:#58a6ff'>%d</b> 条（蓝条行） · "
                 "mls=质量分影子）</span></h2>"
                 "<table><tr><th>代码</th><th>名称</th><th>类型</th>"
                 "<th>信号日</th><th>检出日</th><th>入场价</th>"
                 "<th>mls</th><th>侧面标签</th></tr>%s</table></div>"
                 % (sum(1 for r in rows if r.get("detect_date") == last_day),
                    tr_rows))
        # 图墙撤除收纳：图跟随个股，一行淡字置于信号清单之后（上方即信号行）
        if mkt == "CN":
            body += ("<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                     "🖼 缠论结构图已跟随个股：点击上方信号行的「代码」进个股页"
                     "查看该票日线/30分钟结构图（含手工标注与筹码密集区）</div>")
        else:
            body += ("<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                     "🖼 %s 市场暂不产结构图、无个股下钻（本页为信号明细呈现）"
                     " · 引擎与口径同 CN</div>" % mkt)
        # ── 影子模式状态：1卖+tbe=True 专项追踪 ──
        # baseline: chanlun_samples.jsonl A股导入子集 n=10 win=90% avg=+18.88%
        body += "</div><div class='tabc' id='cpt-shadow'>"
        # closed exe20 来自 paper.ret；open 取浮动 pnl；pending=无匹配仓位
        n_tot = len(shadow)
        n_c = sum(1 for r in shadow if r.get("status") == "closed")
        n_o = sum(1 for r in shadow if r.get("status") == "open")
        n_p = sum(1 for r in shadow if r.get("status") == "pending")
        closed = [r for r in shadow if r.get("status") == "closed"
                  and r.get("exe20") is not None]
        n_ce = len(closed)
        win_c = sum(1 for r in closed if r["exe20"] > 0)
        avg_c = (sum(r["exe20"] for r in closed) / n_ce) if n_ce else 0.0
        win_pct = (win_c * 100.0 / n_ce) if n_ce else 0.0
        avg_pct = avg_c * 100.0 if n_ce else 0.0
        # 与回测基线对比
        gap_w = win_pct - 90.0
        gap_a = avg_pct - 18.88
        gap_w_h = ("<b style='color:%s'>%+.1f%%</b>"
                   % ("#3fb950" if gap_w < -5 else
                      ("#f85149" if gap_w > 0 else "#d29922"), gap_w))
        gap_a_h = ("<b style='color:%s'>%+.2f%%</b>"
                   % ("#3fb950" if gap_a < -5 else
                      ("#f85149" if gap_a > 0 else "#d29922"), gap_a))
        # 基线对照仅 CN（A股导入子集）；US/HK 无跨市场基线，只看绝对水位
        if mkt == "CN":
            _sh_cmp_txt = ("vs 回测基线（A股导入子集 n=10 win=90%% "
                           "avg=+18.88%%）：偏差 win %s · avg %s · 积累 n≥30 "
                           "且偏差收敛后经用户确认才谈启用推送（pending 说明"
                           "见口径折叠）" % (gap_w_h, gap_a_h))
            _sh_cmp_th = "<th>vs 基线 win</th><th>vs 基线 avg</th>"
            _sh_cmp_td = "<td>%s</td><td>%s</td>" % (gap_w_h, gap_a_h)
        else:
            _sh_cmp_txt = ("（%s 影子积累 · 无跨市场基线对照，仅观察绝对水位）"
                           % mkt)
            _sh_cmp_th = ""
            _sh_cmp_td = ""
        # 最近 20 条影子记录（按 detect_date 倒序）
        recent = sorted(shadow,
                        key=lambda x: (x.get("detect_date") or "",
                                       x.get("code") or ""),
                        reverse=True)[:20]
        def _st_color(st):
            return {"closed": "#3fb950", "open": "#d29922",
                    "pending": "#8b949e"}.get(st, "#8b949e")
        def _exe_h(r):
            v = r.get("exe20")
            if v is None:
                return "<span class='dim'>—</span>"
            col = "#f85149" if v > 0 else ("#3fb950" if v < 0 else "#8b949e")
            return "<span style='color:%s'>%+.2f%%</span>" % (col, v * 100)
        # P10 结构时序标注（影子）：npv=扫描窗内已完成中枢数（chanlun_tgraph
        # 回填研究 n=79：≤2枢 win72% avg+6.86%，深左尾集中于 3+枢组）
        def _npv_h(r):
            v = r.get("npv")
            if v is None:
                return "<span class='dim'>—</span>"
            col = "#3fb950" if v <= 2 else "#d29922"
            return "<span style='color:%s'>%d%s</span>" % (
                col, v, "≤2" if v <= 2 else "+")

        def _npv_line(g):
            if not g:
                return "<span class='dim'>待样本</span>"
            n = len(g)
            w = sum(1 for r in g if r["exe20"] > 0) * 100.0 / n
            a = sum(r["exe20"] for r in g) / n * 100.0
            return "<b>n=%d win=%.0f%% avg=%+.2f%%</b>" % (n, w, a)
        npv_le2_h = _npv_line([r for r in closed
                               if r.get("npv") is not None and r["npv"] <= 2])
        npv_3p_h = _npv_line([r for r in closed
                              if r.get("npv") is not None and r["npv"] >= 3])
        rows_h = "".join(
            "<tr><td>%s<span class='dim'> %s</span></td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td style='color:%s'>%s</td><td>%s</td></tr>" % (
                _code_lk(r.get("code") or ""), r.get("name") or "—",
                r.get("dt") or "", r.get("detect_date") or "",
                r.get("lag") if r.get("lag") is not None else "—",
                ("%.2f" % r["entry_px"]) if r.get("entry_px") else "—",
                _npv_h(r),
                _st_color(r.get("status")), r.get("status") or "—",
                _exe_h(r))
            for r in recent)
        body += ("<div class='sec'><h2>👁 影子模式 · 1卖+趋✓ 专项追踪"
                 "<span class='dim' style='font-size:12px'>"
                 "（不推送 · 待 20 根成熟后回填 exe20）</span></h2>"
                 "<div class='dim' style='font-size:13px'>"
                 "实盘扫描口径 <b>n=%d win=%.0f%% avg=%+.2f%%</b>（closed 基"
                 "础，含跨除权剔除）%s</div>"
                 "<table><tr><th>总数</th><th>已平仓</th><th>持仓中</th>"
                 "<th>待开仓</th><th>closed win%%</th><th>closed avg%%</th>"
                 "%s</tr>"
                 "<tr><td><b>%d</b></td><td style='color:#3fb950'>%d</td>"
                 "<td style='color:#d29922'>%d</td>"
                 "<td style='color:#8b949e'>%d</td>"
                 "<td><b>%.0f%%</b></td><td><b>%+.2f%%</b></td>"
                 "%s</tr></table>"
                 "<div class='dim' style='font-size:13px;margin-top:8px'>"
                 "结构分层（P10 影子 · npv=扫描窗内已完成中枢数，历史回填"
                 "口径）：≤2枢 %s · 3+枢 %s</div>"
                 "<h3 style='margin-top:14px'>最近 20 条影子记录</h3>"
                 "<table><tr><th>代码 / 名称</th><th>信号日</th>"
                 "<th>检出日</th><th>lag</th><th>入场价</th><th>npv</th>"
                 "<th>状态</th><th>exe20</th></tr>%s</table></div>"
                 % (n_ce, win_pct, avg_pct, _sh_cmp_txt, _sh_cmp_th,
                    n_tot, n_c, n_o, n_p, win_pct, avg_pct,
                    _sh_cmp_td, npv_le2_h, npv_3p_h, rows_h))
        body += ("<div class='sec'><details><summary style='cursor:pointer;"
                 "font-weight:600'>📋 口径说明"
                 "<span class='dim' style='font-size:12px;font-weight:400'>"
                 "（基线+8 项口径 · 点击展开）</span></summary><div class='dim' "
                 "style='font-size:13px;line-height:2.1'>"
                 + ("" if mkt == "CN" else
                    "◆ 本页为 %s 市场影子池：引擎与口径同 CN（检出日收盘入场"
                    " · 20日持有 · 跨除权剔除 · 不推送不出图），下列 ⓪~⑧ 以 "
                    "CN 产线为例。<br>" % mkt)
                 + "⓪ 基线=chanlun_samples.jsonl A股导入子集（n=10 win=90% "
                 "avg=+18.88%）；pending 条目多为指数信号或跨除权剔除未开仓，"
                 "不开仓不计入 closed 战绩；"
                 "① 扫描输入=截至最后收盘的日线（tushare 不复权，23:35 "
                 "由 AI量化RPS共振 任务增量同步），信号「检出日」=扫描日，"
                 "可成交价=检出日收盘——逐日扫描天然含确认滞后，无未来函数；"
                 "② 模拟交易：检出日收盘入场，持有 20 个交易日或同票出现"
                 "反向类别信号提前离场；卖点为方向统计（跌为胜），非做空"
                 "损益；③ 跨除权事件的仓位剔除（不复权收益失真）；"
                 "④ 全类型信号+全侧面标签入池，各侧面独立积累战绩；"
                 "⑤ 少数信号仅在线全窗分析可见而离线重放检不出（以 2买/2卖"
                 " 为主，信号点可能较早），按检出日收盘入场，属在线/离线"
                 " 不一致缝隙、占比约 0.1%；"
                 "⑥ 1卖·趋✓ 已升级强预警标注（复评 2026-09-26：n=79 "
                 "胜60.8% avg+4.94% med+6.14%，全集 +2.31%）；左尾风险："
                 "信号后反涨30%+ 案例 5 条（轧空无保护），标注仅提示顶部"
                 "结构成熟度，不构成做空或拦截依据；1买·趋✓ 无增益不启用。"
                 "n&lt;10 灰显不构成结论，达标后经确认才谈拦截。"
                 "⑦ P10 结构时序标注（2026-09-28 影子）：1卖·趋✓ 附 "
                 "npv=扫描窗内已完成中枢数（≤2 绿 / 3+ 橙）；终修订结构"
                 "回填（n=79）≤2枢 win72% avg+6.86%、左尾浅于 3+枢"
                 "（-49% 级深左尾集中于 3+枢组），样本 n=18 偏小，仅标注"
                "不拦截，n≥30 后再评估升级。"
                "⑧ mls 质量分影子（E3 特征化，2026-10-02 K28 产线接入）："
                "LightGBM 对信号 20 交易日收益&gt;0 概率的估计（0~1），"
                "特征=灰度侧面+结构时序+图谱截面（kg_* 含前视污染声明、"
                "启用前须时点切片复评）；当前为 5 树弱基线（AUC 0.5159），"
                "<b>仅排序观察实验，不构成买卖/推送/拦截依据</b>；接入前"
                "旧信号显示 —，mls≥300 样本后做（mls 分位×exe20）对照面板。"
                "</div></details></div>")
        body += "</div><div class='tabc' id='cpt-hist'>"
        body += ("<div class='sec'><h2>🏆 模拟交易侧面战绩"
                 "<span class='dim' style='font-size:12px'>"
                 "（缓存 %s · 池 %s · 持仓 %s · 已平 %s）</span></h2>"
                 "<table><tr><th>类型</th><th>全集</th><th>平仓数</th>"
                 "<th>M✓ MACD</th><th>✓ 盘整</th><th>盘✓ 同向腿</th>"
                 "<th>量✓ 缩量</th><th>势✓ 顺风</th><th>趋✓ 趋势</th>"
                 "<th>形✓ 形态</th></tr>%s</table></div>"
                 % (ev.get("generated_at") or "—",
                    "{:,}".format(ev.get("n_pool") or len(pool)),
                    ev.get("n_open") if ev else len(paper.get("open", [])),
                    ev.get("n_closed") if ev else
                    len(paper.get("closed", [])), tr_eval))
        # P9 复合叠加战绩（读 pool_eval 缓存 comp 区，影子预研不拦截）
        comp_rows = ""
        for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
            for c in (((ev.get("types") or {}).get(t) or {})
                      .get("comp") or []):
                if c.get("cov"):
                    cov_h = "✓%s / 可判 %s" % (c.get("n", 0), c.get("cov", 0))
                    stat_h = (_cell(c.get("stats")) if c.get("stats") else
                              "<span class='dim'>无成立样本</span>")
                else:
                    cov_h = "<span class='dim'>待可判样本</span>"
                    stat_h = "<span class='dim'>—</span>"
                comp_rows += ("<tr><td><b>%s</b></td><td>%s</td><td>%s</td>"
                              "<td>%s</td><td class='dim'>%s</td></tr>"
                              % (t, c.get("name"), cov_h, stat_h,
                                 "+".join(c.get("factors") or [])))
        body += ("<div class='sec'><h2>🔗 复合叠加战绩"
                 "<span class='dim' style='font-size:12px'>（P9 影子预研"
                 " · 只评估不拦截）</span></h2>"
                 "<table><tr><th>类型</th><th>叠加规则</th><th>成立/可判"
                 "</th><th>纸面收益（20日持有）</th><th>因子</th></tr>%s"
                 "</table><details style='margin-top:8px'><summary "
                 "style='cursor:pointer;font-weight:600'>📋 复合口径"
                 "<span class='dim' style='font-size:12px;font-weight:400'>"
                 "（点击展开）</span></summary><div class='dim' style="
                 "'font-size:12px;line-height:2'>复合口径：全部因子同时成立"
                 "才算「成立」（✓/可判 分母单列）；任一因子不可判（None："
                 "无量数据 / 无指数 / 无前序笔 / 中枢不足 / 旧行无形态标注；"
                 "池口径无 60m 次级别）整条"
                 "记不可判、<b>绝不按 ✗ 处理</b>。纸面口径与上表一致（检出"
                 "日收盘入场+20交易日持有、跨除权剔除、卖点为方向统计）；"
                 "n&lt;10 灰显不构成结论。规则表：chanlun_composite.py，"
                 "新增规则评估与页面自动跟随。</div></details></div>" % comp_rows)
        # ── 先例战绩（Semantica 决策智能 · precedents_cache.json） ──
        try:
            with open(os.path.join(QBT_DIR, "data", "semantica",
                                   "precedents_cache.json"),
                      encoding="utf-8") as f:
                prec = _jp.load(f)
        except Exception:
            prec = {}
        prec_by = prec.get("by_key") or {}

        def _pc(key):
            s = prec_by.get(key)
            if not s:
                return "<span class='dim'>—</span>"
            sty = "" if s["n"] >= 10 else " style='color:#484f58'"
            return ("<span%s>n=%d · 胜%.0f%% · %+.2f%%</span>"
                    % (sty, s["n"], s["win_rate"] * 100,
                       s["avg_return"] * 100))
        prec_rows = ""
        for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
            hot = (t == "1卖")
            t_h = ("<b style='color:#f85149'>%s 🚨</b>" % t
                   if hot else
                   "<b style='color:%s'>%s</b>"
                   % ("#f85149" if "卖" in t else "#3fb950", t))
            prec_rows += ("<tr><td>%s</td><td>%s</td><td>%s</td>"
                          "<td>%s</td></tr>"
                          % (t_h, _pc(t), _pc("%s_tbe" % t),
                             _pc("%s_notbe" % t)))
        body += ("" if mkt != "CN" else ("<div class='sec'><h2>📋 先例战绩 · Semantica 决策智能"
                 "<span class='dim' style='font-size:12px'>"
                 "（缓存 %s · 向量先例检索）</span></h2>"
                 "<table><tr><th>类型</th><th>全集</th>"
                 "<th>趋✓ 子集</th><th>非趋✓ 子集</th></tr>%s</table>"
                 "<details style='margin-top:8px'><summary style='cursor:"
                 "pointer;font-weight:600'>📋 Semantica 说明"
                 "<span class='dim' style='font-size:12px;font-weight:400'>"
                 "（决策因果链 · 点击展开）</span></summary><div class='dim' "
                 "style='font-size:12px;line-height:2'>"
                 "Semantica 决策因果链：缠论入池信号按 category/scenario/"
                 "reasoning/entities 持久化，新信号触发时向量检索同型先例"
                 "（fastembed BAAI/bge-small-zh-v1.5）。1卖·趋✓ 是首个达标"
                 "显著有效因子（n=84 胜63.1%% avg+5.90%% vs 基线 56.4%%/"
                 "+3.51%%，+9.2pp/+2.39pp）；1买·趋✓ 无增益不启用。"
                 "飞书推送时附 enrich_signal 实时摘要。</div></details></div>"
                 % (prec.get("updated_at") or "—", prec_rows)))
        # ── 近期强事件（event_graph 市场事件面，近7天 bull/bear 前 8） ──
        # 开页慢专项：每开页同步查事件库（冷态 ~900ms）；300s TTL 缓存
        _now3 = _tt2.time()
        _egc = getattr(type(self), "_egs_cache", None)
        if _egc and _now3 - _egc[0] < 300:
            _egs = _egc[1]
        else:
            _egs = []
            try:
                sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
                from event_graph import recent_strong as _rs
                _egs = _rs(n=8, days=7) if mkt == "CN" else []
            except Exception:
                _egs = []
            type(self)._egs_cache = (_now3, _egs)
        if _egs:
            import html as _heg
            _erows = ""
            for _ev in _egs:
                _b = _ev.get("bias")
                _tc = "t-bull" if _b == "bull" else "t-bear"
                _bm = "🟢" if _b == "bull" else "🔴"
                _code = (_ev.get("stocks") or [""])[0]
                _link = ("<a href='/kg/stock/%s'>%s</a> "
                         % (_heg.escape(_code), _heg.escape(_code))
                         if _code else "")
                _erows += ("<tr><td>%s</td><td><span class='tag %s'>%s</span>"
                           "%s</td><td>%s</td><td>%s</td></tr>"
                           % ((_ev.get("ts") or "")[:10], _tc, _bm,
                              _heg.escape((_ev.get("text") or "")[:50]),
                              _heg.escape(_ev.get("tag") or "—"),
                              _link))
            body += ("<div class='sec'><h2>🗞 近期强事件 · 事件图谱"
                     "<span class='dim' style='font-size:12px'>"
                     "（近7天 bull/bear 前 8 · 规则+jev 判偏）</span></h2>"
                     "<table><tr><th>事件日</th><th>事件</th><th>标签</th>"
                     "<th>标的</th></tr>%s</table></div>" % _erows)
        # ⏰ 扫描任务块已撤除：页顶态势感知状态条（last/result/next）已覆盖，
        #    tab 内重复显示无信息增量
        # ── 方法论总纲 tab：系统灌输的缠论口径一页总览（源自引擎文件头注释）──
        body += "</div><div class='tabc' id='cpt-doc'>"
        body += ("<div class='sec'><h2>🧱 结构识别三层"
                 "<span class='dim' style='font-size:12px'>"
                 "（chanlun_fractal → chanlun_bi，全部日线级、无未来函数）"
                 "</span></h2><div class='dim' style='font-size:13px;"
                 "line-height:2.1'>"
                 "① <b>分型</b>：K 线先做包含关系合并，再取顶/底分型"
                 "<code>chanlun_fractal._merge_inclusion → find_fractals</code>；"
                 "② <b>笔</b>：相邻异性分型相连，两分型中点处理后索引差 ≥4"
                 "（一笔至少 5 根处理后 K 线，常量 BI_MIN_GAP=4）；同性分型保留"
                 "更极值者，已确认笔终点随之修正延伸"
                 "<code>chanlun_bi.find_bis</code>；"
                 "③ <b>线段</b>（破坏确认法）：反向笔破线段内前一同向笔极值"
                 "成候选，其后第一个同向笔不创线段新极值 → 确认终结于线段"
                 "极值（创则解除）；终结点必为线段极值"
                 "<code>chanlun_bi.find_segments</code>；"
                 "④ <b>中枢</b>（笔中枢）：连续三笔高低点区间重叠定 [ZD,ZG]"
                 "（ZD=三笔低点最高、ZG=三笔高点最低）；后续笔与区间有重叠"
                 "则延伸（区间不变），某笔完全离开 → 中枢终结，该笔为下一"
                 "中枢候选起点<code>chanlun_bi.find_pivots</code>。"
                 "总入口<code>chanlun_bi.analyze(bars)</code> 返回 "
                 "bis/segments/pivots（dt 均为分型 dt，前端可定位）。</div></div>")
        body += ("<div class='sec'><h2>🎯 三类买卖点判定"
                 "<span class='dim' style='font-size:12px'>"
                 "（chanlun_signal，按 dt 升序同位去重）</span></h2>"
                 "<div class='dim' style='font-size:13px;line-height:2.1'>"
                 "① <b>一类 · 趋势转折（动量衰减简化）</b>：线段被确认终结"
                 "（存在后续段）时，段内最后两根同向笔幅度收窄 → 线段极值记"
                 " 1买（向下段）/ 1卖（向上段）；同向笔不足两根的过渡段不产"
                 "生信号<code>chanlun_signal._seg_signals</code>（笔力度="
                 "端点绝对价差 <code>_bi_force</code>）。"
                 "② <b>二类 · 首次回踩不破极值</b>：1买之后新向上段的第一根"
                 "向下笔低点高于 1买价 → 该回调笔终点记 2买；1卖后反弹笔高点"
                 "低于 1卖价对称记 2卖"
                 "<code>chanlun_signal._second_signals(bis, segs, firsts)</code>。"
                 "③ <b>三类 · 中枢离开回踩不回区间</b>：中枢终结后第一根向上"
                 "笔突破 ZG、其后第一根向下笔低点仍高于 ZG → 该低点记 3买；"
                 "跌破 ZD 反弹不回对称记 3卖；同一中枢只报首个三类信号"
                 "<code>chanlun_signal._pivot_signals</code>。"
                 "④ <b>完结确认</b>：二/三类依据的回调/反弹笔不能是最后一根"
                 "笔（最后一笔终点随行情延伸可能修正），其后续笔必须已存在"
                 "——逐日扫描天然含确认滞后。"
                 "合并输出<code>chanlun_signal.find_signals(bis, segments, "
                 "pivots)</code>（dt 升序同位去重）→ <code>analyze(bars, "
                 "closes)</code> 在结构结果上追加 signals。盘整背驰辅助判定 "
                 "BEICHI_RATIO=1.0<code>_panzheng_beichi / "
                 "_entry_leave_beichi</code>。</div></div>")
        body += ("<div class='sec'><h2>🕵 影子标注层（九项 · 只标注不拦截）"
                 "<span class='dim' style='font-size:12px'>"
                 "（教学口径：背驰只描述动能衰减，真正决定方向的是资金与"
                 "环境——多条件共振、攒样本达标后才逐项启用）</span></h2>"
                 "<table><tr><th>标签</th><th>口径</th><th>判定规则 / 参数"
                 "</th><th>实现</th></tr>"
                 "<tr><td><b style='color:#bc8cff'>M✓</b>（P6-a，仅1买/1卖）"
                 "</td><td>MACD 背驰</td><td>信号笔终点价创新低/高，而该点 "
                 "DIF 较上一同向笔终点反向收敛（底/顶背驰）</td><td><code>"
                 "chanlun_macd.divergence</code>（12/26/9）；灰度双口径 "
                 "<code>chanlun_gray._div_dual</code></td></tr>"
                 "<tr><td><b style='color:#f85149'>✓</b>（P5 盘整背驰）</td>"
                 "<td>回踩 vs 离开笔</td><td>回踩/反抽笔力度弱于离开笔；力度"
                 "=笔端点绝对价差，BEICHI_RATIO=1.0</td><td><code>"
                 "chanlun_signal._panzheng_beichi</code></td></tr>"
                 "<tr><td><b style='color:#58a6ff'>盘✓</b>（P6-b 同向腿）"
                 "</td><td>教学复刻盘整背驰</td><td>回踩力度弱于离开笔之前"
                 "最近一根同向前序腿 a（3买=前序下跌腿）；更高低点/更低高点"
                 "的力度表达</td><td><code>chanlun_signal."
                 "_entry_leave_beichi</code></td></tr>"
                 "<tr><td><b style='color:#f0883e'>量✓</b>（P8-a 量能共振）"
                 "</td><td>资金配合</td><td>终结笔均量缩于前同向笔均量"
                 "（VOL_RATIO=1.0）：买类=下跌缩量卖压衰竭/回踩惜售，卖类="
                 "上涨缩量追高乏力</td><td><code>chanlun_flow."
                 "volume_confluence</code>（<code>_bi_mean_vol</code>）</td>"
                 "</tr>"
                 "<tr><td><b style='color:#79c0ff'>势↑↓</b>（P8-b 大盘环境）"
                 "</td><td>中短期趋势配合</td><td>信号日截断指数日线（防未来"
                 "函数）：收 vs MA20/MA60 → up/down/flat；指数代理 CN=上证 "
                 "000001.SH、CRYPTO=BTC/USDT；买类+up、卖类+down 为顺风</td>"
                 "<td><code>chanlun_flow.regime_at</code> + <code>aligned"
                 "</code>（REGIME_INDEX）</td></tr>"
                 "<tr><td><b style='color:#f778ba'>趋✓</b>（P6-c 趋势背驰，"
                 "仅1买/1卖）</td><td>教学 a+A+b+B+c 结构</td><td>信号前存在"
                 " ≥2 个同向不重叠中枢（后中枢完全越过前中枢）+ 两中枢离开笔"
                 "同向 + 信号价在末中枢趋势侧之外 + 末段 c 力度弱于连接段 b"
                 "</td><td><code>chanlun_trend</code>（tbe 口径）；样本回填 "
                 "<code>chanlun_mlf.backfill_trend</code></td></tr>"
                 "<tr><td>牛熊（P9 长周期）</td><td>结构性牛熊分水岭</td><td>"
                 "收盘 vs MA200：&gt;MA200=牛市 regime（外推验证 2018-2023："
                 "close&gt;MA200 子集夏普 1.37 vs 其余 -1.73，差 3.10）</td>"
                 "<td><code>chanlun_flow.regime_trend_at</code>"
                 "（MA_TREND=200）</td></tr>"
                 "<tr><td>npv（P10 结构时序）</td><td>中枢计数</td><td>扫描窗"
                 "内已完成中枢数：≤2 枢 win72% avg+6.86%，深左尾集中于 3+ 枢"
                 "组</td><td><code>chanlun_scan._npv_of</code></td></tr>"
                 "<tr><td>mls（E3 质量分）</td><td>ML 排序</td><td>LightGBM "
                 "对 20 交易日收益&gt;0 概率估计（0~1，5 树弱基线 AUC 0.5159，"
                 "仅排序观察实验）</td><td><code>chanlun_mlf.score_rows / "
                 "train</code>（特征 <code>feature_cols</code>）</td></tr>"
                 "</table>"
                 "<div class='dim' style='font-size:12px;margin-top:4px'>"
                 "灰度富化注记 <code>chanlun_gray.enrich</code>（div_area/"
                 "fv_area/DIF 面积特征）；60 分钟级别共振 <code>chanlun_mtf."
                 "confluence</code>（SUB_PERIOD=60m）；复合判定规则表 "
                 "<code>chanlun_composite.RULES / judge_all</code>。</div>")
        body += ("<div class='sec'><h2>💰 交易规则与达标门槛"
                 "<span class='dim' style='font-size:12px'>"
                 "（影子观察：不推送不拦截，达标经确认才逐项启用）</span></h2>"
                 "<div class='dim' style='font-size:13px;line-height:2.1'>"
                 "① <b>扫描</b>：每日 15:35 盘后全A股约 5,593 只，全类型信号+全"
                 "侧面标签入池<code>chanlun_scan.scan_all</code>（喂 500 根 "
                 "LIMIT_BARS、次新 MIN_BARS=120 排除、<code>_check_fresh</code>"
                 " 数据同步校验、TDX 兜底 <code>_tdx_rescue</code>）→ "
                 "<code>_make_row</code> 写池 <code>data/chanlun_pool.jsonl</code>"
                 "（<code>_pool_dual_write_sqlite</code> 双写 SQLite）；"
                 "② <b>入场/离场</b>：检出日收盘入场（逐日扫描天然含确认滞后"
                 "，无未来函数）→ 持有 20 个交易日到期平仓（HOLD_DAYS=20），或"
                 "同票出现反向类别信号提前离场；跨除权事件仓位剔除"
                 "<code>_exdiv_between</code>（不复权收益失真）；卖点为方向统计"
                 "（跌为胜），非做空损益；账本 <code>chanlun_scan.update_paper"
                 "</code>（closed 截留 5000）；"
                 "③ <b>强预警</b>：1卖·趋✓（复评 n=79 胜60.8% avg+4.94%，"
                 "全集 +2.31%；Semantica 向量先例 n=84 胜63.1%）；左尾风险="
                 "信号后反涨 30%+ 案例 5 条（轧空无保护）——标注顶部结构成熟"
                 "度，不构成做空或拦截依据；1买·趋✓ 无增益不启用；推送链 "
                 "<code>refresh_shadow_1sell_tbe → push_1sell_tbe_alert</code>"
                 "（<code>_push_feishu_text</code> 飞书、<code>_load_pushed"
                 "</code> 去重防重发）；"
                 "④ <b>达标门槛</b>：可成交复核<code>chanlun_backtest."
                 "eval_samples(max_lag=60)</code>（逐根重放 lag/slip/exe、"
                 "成本 cost_bps=3+slip_bps=1，PASS_N=10 且正向均值）；影子样本"
                 " n≥30 且在线/回测偏差收敛 → 经用户确认后才谈启用推送/拦截；"
                 "n&lt;10 灰显不构成结论。"
                 "</div><div class='dim' style='font-size:12px;margin-top:6px'>"
                 "口径源码：chanlun_fractal.py（分型）→ chanlun_bi.py（笔/线段"
                 "/中枢）→ chanlun_signal.py（三类买卖点）→ chanlun_macd / "
                 "chanlun_flow / chanlun_trend / chanlun_gray / chanlun_mlf / "
                 "chanlun_mtf / chanlun_composite（影子·富化·共振·复合）→ "
                 "chanlun_scan（每日扫描入池+模拟账本）→ chanlun_backtest"
                 "（可成交复核）→ chanlun_card（个股五面卡）"
                 "</div></div>")
        body += "</div></div>"
        body += "</body></html>"
        self._html(body)


    def _chanlun_stock_sec(self, code):
        """个股页缠论结构图区块：1d/30m/1w 切换（K77）。
        K235 精简：手工标注管理（高手工具）已迁往 /chanlun-anno-report
        缠论专项页——个股页只留看图（级别切换+放大），结论卡上移页首。"""
        import html as _h
        cq = _h.escape(code, quote=True)
        return (
            "<div class='sec'><h2>🧭 缠论结构图"
            "<span style='font-size:11px;color:#e3b341;border:1px solid "
            "#e3b341;border-radius:10px;padding:1px 8px;margin-left:8px;"
            "vertical-align:2px'>🧭 缠论产线</span>"
            "<span class='dim' style='font-size:12px'>（每日盘后自动生成 ·"
            " 金色横带=筹码密集区 · <a href='/chanlun-anno-report?c=" + cq +
            "'>✍ 关键位标注管理（专项）</a>）</span></h2>"
            "<div style='margin-bottom:6px'>"
            "<button id='clt-1d' class='on' onclick='_clP(this,\"1d\")'>"
            "日线</button>"
            "<button id='clt-30m' onclick='_clP(this,\"30m\")'>30分钟</button>"
            "<button id='clt-1w' onclick='_clP(this,\"1w\")'>周线</button>"
            "<span class='dim' style='font-size:11.5px;color:#8b949e'>"
            "（月线需 ≥30 个月日线数据，当前日线库约 400 根暂不支持）</span>"
            "<span class='dim' style='font-size:12px;margin-left:8px'>"
            "首次生成约需 10-60 秒，点击图可放大</span>"
            "<span id='cl-ld' style='display:none;color:#e3b341;"
            "margin-left:8px;font-size:12.5px'>⏳ 生成中，请稍候"
            "（最长约 60 秒）…</span></div>"
            "<a href='/chanlun-png?c=" + cq + "&p=1d' target='_blank' "
            "title='点击放大'>"
            "<img id='cl-img' src='/chanlun-png?c=" + cq + "&p=1d&t=1' "
            "onload='var l=document.getElementById(\"cl-ld\");"
            "if(l)l.style.display=\"none\";' "
            "onerror='var l=document.getElementById(\"cl-ld\");"
            "if(l){l.style.display=\"inline\";"
            "l.innerHTML=\"该级别暂无足够数据（新股或数据缺失），"
            "换日线/30分钟查看\";}' "
            "style='max-width:100%%;border:1px solid #30363d;border-radius:"
            "6px'></a></div>"
            "<script>var _clC='%s';"
            "function _clP(b,p){for(var x of['clt-1d','clt-30m','clt-1w'])"
            "document.getElementById(x).className='';b.className='on';"
            "var l=document.getElementById('cl-ld');if(l)l.style.display="
            "'inline';"
            "document.getElementById('cl-img').src='/chanlun-png?c='+_clC+"
            "'&p='+p+'&t='+Date.now();}"
            "</script>"
            % cq)




    def _chanlun_anno_report(self):
        """K77b: /chanlun-anno-report 标注总结报告（动态，随标注更新）。"""
        import html as _h
        import datetime as _dt
        data = self._anno_load()
        # 池内信号对照
        try:
            import sys as _sys
            if r'C:\项目\老田的AI助手\quant_backtest' not in _sys.path:
                _sys.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
            import importlib as _il
            S = _il.import_module("chanlun_scan")
            pool = S._load_pool()
        except Exception:
            pool = []
        sig = {}
        for r in pool:
            c = str(r.get("code") or "")
            if c and c not in sig:
                sig[c] = r
        rows = []
        for key in sorted(data.keys()):
            try:
                c, p = key.split("|", 1)
            except ValueError:
                continue
            ann = [a for a in data[key] if isinstance(a, dict)]
            if not ann:
                continue
            rows.append((c, p, ann))
        body = [self._BT_CSS,
                "<h1>✍ 缠论标注总结报告"
                "<span class='dim' style='font-size:13px'>（动态页："
                "随手工标注实时更新 · %d 票 · 生成于 %s）</span></h1>"
                % (len({c for c, _, _ in rows}),
                   _dt.datetime.now().strftime("%m-%d %H:%M"))]
        if not rows:
            body.append("<div class='sec dim'>暂无任何标注。到个股页"
                        "（预警池点代码）给图加关键位后回到本页查看。"
                        "</div>")
        seen = set()
        for c, p, ann in rows:
            if c in seen and p != "1d":
                continue
            seen.add(c)
            sigr = sig.get(c) or {}
            chip = ""
            try:
                import importlib as _il2
                _cp = _il2.import_module("chanlun_plot")
                b = _cp.chip_band(c, "1d")
                if b:
                    chip = "%.2f – %.2f（峰 %.2f）" % b
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_chanlun_pages.py", exc)
            stp = self._chanlun_sl_tp(c)
            stp_html = ""
            if stp:
                stp_html = (
                    "<table><tr><th>现价</th><th>🛑 止损点（日线结构）"
                    "</th><th>🎯 止盈目标1（30分钟）</th>"
                    "<th>🎯 止盈目标2</th><th>级别参照</th></tr>"
                    "<tr><td><b>%.2f</b></td>"
                    "<td style='color:#f85149'><b>%.2f</b><div class='dim' "
                    "style='font-size:11.5px'>%s</div></td>"
                    "<td style='color:#3fb950'><b>%.2f</b></td>"
                    "<td style='color:#3fb950'><b>%.2f</b></td>"
                    "<td class='dim' style='font-size:11.5px'>日线中枢 "
                    "%.2f-%.2f<br>%s</td></tr></table>"
                    % (stp["px"], stp["stop"], _h.escape(stp["basis"]),
                       stp["tp1"], stp["tp2"],
                       stp.get("dzg", 0), stp.get("dzd", 0),
                       _h.escape(str(stp.get("tpb") or ""))))
            trs = "".join(
                "<tr><td>%.2f</td><td style='color:%s'>■ %s</td>"
                "<td>%s</td></tr>"
                % (float(a.get("price") or 0),
                   _h.escape(str(a.get("color") or "#f85149")),
                   _h.escape(str(a.get("label") or "")),
                   "日线" if p == "1d" else "30分钟")
                for a in ann)
            body.append(
                "<div class='sec'><h2><a href='/kg/stock/%s'>%s</a> "
                "<span class='dim'>(%s)%s</span></h2>"
                "<table><tr><th>系统信号</th><th>信号日</th><th>入场价</th>"
                "<th>筹码密集区(120日)</th></tr>"
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                "</table>"
                "<table><tr><th>价格</th><th>标注</th><th>周期</th></tr>%s"
                "</table>"
                "<a href='/chanlun-png?c=%s&p=%s' target='_blank'>"
                "<img src='/chanlun-png?c=%s&p=%s' loading='lazy' "
                "style='max-width:100%%;border:1px solid #30363d;"
                "border-radius:6px'></a></div>"
                % (c, _h.escape(str(sigr.get("name") or c)), c,
                   "" if c not in sig else " · 当日信号 " +
                   _h.escape(str(sigr.get("type") or "")),
                   _h.escape(str(sigr.get("type") or "—")),
                   _h.escape(str(sigr.get("detect_date") or "—")),
                   _h.escape(str(sigr.get("entry_px") or "—")), chip,
                   trs, c, p, c, p))
            body.append(stp_html)
        # 尚未标注的当日信号票清单（含系统自动止损/止盈预测）
        un = [(str(r.get("code")), r.get("name"), r.get("type"))
              for r in pool[:20]
              if str(r.get("code")) not in seen]
        if un:
            body.append("<div class='sec'><h2>📋 当日信号待标注"
                        "<span class='dim' style='font-size:12px'>"
                        "（点代码进个股页标注关键位；"
                        "🛑/🎯 为系统自动预测）</span></h2><table>"
                        "<tr><th>代码</th><th>名称</th><th>信号</th>"
                        "<th>🛑 止损</th><th>🎯 目标1</th>"
                        "<th>🎯 目标2</th><th>止损依据</th></tr>")
            for c, nm, tp in un:
                stp = self._chanlun_sl_tp(c)
                if stp:
                    body.append(
                        "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                        "<td>%s</td><td>%s</td>"
                        "<td style='color:#f85149'><b>%.2f</b></td>"
                        "<td style='color:#3fb950'>%.2f</td>"
                        "<td style='color:#3fb950'>%.2f</td>"
                        "<td class='dim' style='font-size:11.5px'>%s</td>"
                        "</tr>" % (c, c, _h.escape(str(nm or "")),
                                   _h.escape(str(tp or "")),
                                   stp["stop"], stp["tp1"], stp["tp2"],
                                   _h.escape(str(stp.get("basis") or ""))))
                else:
                    body.append(
                        "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                        "<td>%s</td><td>%s</td><td colspan='4' class='dim'>"
                        "结构数据不足，暂无预测</td></tr>"
                        % (c, c, _h.escape(str(nm or "")),
                           _h.escape(str(tp or ""))))
            body.append("</table></div>")
        self._html("".join(body))
