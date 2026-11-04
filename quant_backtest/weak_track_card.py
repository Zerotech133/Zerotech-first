# -*- coding: utf-8 -*-
"""弱于板块案例 · 飞书卡片推送构造器（K35 §8/§9 卡片版）。

发送通路复用 news_collector/send_feishu.py 的 interactive 模式
（文件内容即卡片 JSON，第二参数 "interactive"）——通路已验证，本模块
只负责把 §8 触发 / §9 结案的数据构造成飞书 interactive 卡片 dict。

卡片 schema：K43-d 起发送前统一 to2() 转 2.0（schema=2.0 + body.elements：
markdown 行式/note→灰字/按钮→md 超链接），构造函数仍写 v1 组件树，
手机端不再空白（K42-d 判例）。header 颜色：结案 A绿/B橙/C红；触发 ⚠ 红。

CLI（默认只打印 JSON 预览，--send 真发）:
  python weak_track_card.py 301216 --close A --note "修复兑现"
  python weak_track_card.py 301216 --close A --note "…" --send
  python weak_track_card.py 301216 --alert            # 用状态流最新行构造触发卡
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_close as wc  # noqa: E402
import logging

NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
HEADER_T = {"A": "green", "B": "orange", "C": "red"}
ICON_T = {"A": "🏁", "B": "➡️", "C": "⚠"}


def _md(s):
    return {"tag": "lark_md", "content": str(s)}


def _f2(name, val):
    return {"is_short": True, "text": _md("**%s**\n%s" % (name, val))}


def to2(card):
    """v1 卡片 → 2.0 结构（K43-d）。单点收口于 send_card，构造函数不动。

    判例（K42-d/K43）：顶层 elements 的混血卡手机端正文空白；
    2.0 下禁 v1 action 按钮（服务端 400）、img（200621）。
    转换：div fields→markdown 行式（**名** 值 每字段一行）；
    div(lark_md)→markdown 组件；note→灰字 markdown；action 按钮→
    md 超链接行；hr 等 2.0 合法组件原样保留。已是 2.0 的原样返回。
    """
    if card.get("schema") == "2.0":
        return card
    body = []
    for el in card.get("elements", []):
        tag = el.get("tag")
        if tag == "div" and "fields" in el:
            body.append(
                {
                    "tag": "markdown",
                    "content": "\n".join(
                        fd["text"]["content"].replace("\n", " ") for fd in el["fields"]
                    ),
                }
            )
        elif tag == "div" and "text" in el:
            body.append({"tag": "markdown", "content": el["text"]["content"]})
        elif tag == "note":
            txt = "\n".join(x.get("content", "") for x in el.get("elements", []))
            body.append(
                {"tag": "markdown", "content": "<font color='grey'>%s</font>" % txt}
            )
        elif tag == "action":
            links = " · ".join(
                "[%s](%s)" % (b["text"]["content"], b["url"])
                for b in el.get("actions", [])
                if b.get("url")
            )
            if links:
                body.append({"tag": "markdown", "content": links})
        else:
            body.append(el)
    cfg = {"update_multi": True, "width_mode": "default"}
    return {
        "schema": "2.0",
        "config": cfg,
        "header": card.get("header"),
        "body": {"elements": body},
    }


def close_card(f):
    """结案报告卡片（f=weak_track_close.close_fields 的 dict）。

    K35-t 增强：标的名称/区间涨跌幅+超额变化方向（fields 行）、
    触发时间线（最近 3 条 md 列表块）、档案与 B 类新案进 note。
    """
    t = next((k for k, v in wc.CTYPE.items() if v == f["ctype"]), "A")
    els = [
        {
            "tag": "div",
            "fields": [
                _f2("标的", "%s %s" % (f["code"], f.get("name") or "—")),
                _f2("类型", f["ctype"]),
                _f2("跟踪", f["n"]),
                _f2(
                    "区间涨跌",
                    "%s → %s（%s）" % (f.get("px0"), f.get("px1"), f.get("chg", "—")),
                ),
                _f2(
                    "超额250",
                    "%s → %s（%s）" % (f.get("ex0"), f.get("ex1"), f.get("exd", "—")),
                ),
                _f2("时间", "%s → %s" % (f["first"], f["last"])),
            ],
        },
        {"tag": "hr"},
    ]
    hist = str(f.get("trig_hist") or "")
    if hist and hist != "无":  # 触发时间线（最近 3 条）
        els.append(
            {
                "tag": "div",
                "text": _md(
                    "**触发时间线**（共 %s 次，最近 %d 条）\n%s"
                    % (
                        f["ntrig"],
                        len(hist.split("｜")),
                        "\n".join("- " + x for x in hist.split("｜")),
                    )
                ),
            }
        )
        els.append({"tag": "hr"})
    els.append({"tag": "div", "text": _md("**结论**：%s" % f["note"])})
    note = [
        "任务已停用（可 Enable 重启）｜状态流已归档 %s" % f["arch"],
        "案例档案：%s" % (f.get("rev") or "—"),
    ]
    if f.get("ncase"):  # B 类滚动新案闭环
        note.append(str(f["ncase"]))
    note.append(str(f.get("ts", "")))
    els.append(
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "\n".join(note)}]}
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": HEADER_T[t],
            "title": {
                "tag": "plain_text",
                "content": "%s 结案报告 · %s"
                % (
                    ICON_T[t],
                    " ".join(x for x in [f["code"], f.get("name") or ""] if x),
                ),
            },
        },
        "elements": els,
    }


def alert_card(state):
    """§8 复评触发卡片（state=weak_track_check 落状态流的那行 dict）。"""
    trig = state.get("trig") or []
    ex = state.get("ex250")
    exs = "%+.1fpp" % ex if ex is not None else "—"
    trend = "/".join(
        str(x).replace("（均线缠绕）", "·缠")[:8] for x in (state.get("trend") or [])
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "content": "⚠ %s 复评触发（按 §8 SOP）" % state.get("code", "?"),
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _f2("触发", "；".join(trig) or "—"),
                    _f2(
                        "数据日·现价",
                        "%s·%s" % (str(state.get("px_d", "?"))[5:], state.get("px")),
                    ),
                    _f2("超额250", exs),
                    _f2("趋势 个/板/沪", trend),
                ],
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": _md(
                    "**第二步**：触发1 → turn_lookup 双窗口辨方向（个股涨=修复"
                    "/板块跌=环境恶化）；触发2 → 新信号 3买须过环境核验；"
                    "触发3 → 重跑六问"
                ),
            },
            {
                "tag": "div",
                "text": _md("**第三步**：§4+追记区落款｜**第四步**：结案三选一 → §9"),
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "状态 %s" % state.get("ts", "?")}
                ],
            },
        ],
    }


def summary_card(f):
    """§10 案例库结案汇总卡片（f=weak_track_summary.summary_fields 的 dict）。

    header 蓝（汇总中性色，与扫描卡同系）；明细 md 列表块最多 8 行。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": "📊 案例库结案汇总（§10）· %s 例" % f["n"],
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _f2(
                        "已结案",
                        "%s 例（%s ~ %s）" % (f["n"], f["first_ts"], f["last_ts"]),
                    ),
                    _f2("类型分布", "A %s｜B %s｜C %s" % (f["na"], f["nb"], f["nc"])),
                    _f2(
                        "区间涨跌",
                        "均 %s｜最好 %s｜最差 %s"
                        % (f["chg_avg"], f["chg_best"], f["chg_worst"]),
                    ),
                    _f2(
                        "超额修复",
                        "%s/%s（收敛或转正）" % (f["ex_fix"], f["ex_tagged"]),
                    ),
                    _f2("触发", "合计 %s 次（均 %s）" % (f["ntrig"], f["ntrig_avg"])),
                    _f2("生成", f["ts"]),
                ],
            },
            {"tag": "hr"},
            {"tag": "div", "text": _md("**明细**（最新 8 条）\n%s" % f["detail"])},
        ],
    }


def learn_card(f):
    """§11 案例库复盘学习卡片（f=weak_track_learn.learn_fields 的 dict）。

    header purple（学习层专属色，区别 §10 蓝汇总）；经验条目+明细 md 块。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {
                "tag": "plain_text",
                "content": "🧠 复盘学习（§11）· %s 例·%s" % (f["n"], f["gate"]),
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _f2(
                        "样本",
                        "%s 例（%s ~ %s）" % (f["n"], f["first_ts"], f["last_ts"]),
                    ),
                    _f2("剧本兑现", "A %s/%s（%s）" % (f["na"], f["n"], f["a_rate"])),
                    _f2(
                        "跟踪时长",
                        "均 %s 日历天（%s~%s，可用 %s）"
                        % (f["dur_avg"], f["dur_min"], f["dur_max"], f["ndays"]),
                    ),
                    _f2(
                        "触发密度", "A 均 %s vs B/C 均 %s" % (f["trig_a"], f["trig_bc"])
                    ),
                    _f2(
                        "超额修复",
                        "%s/%s（A 类 %s/%s）"
                        % (f["ex_fix"], f["ex_tagged"], f["fix_a"], f["tag_a"]),
                    ),
                    _f2("生成", f["ts"]),
                ],
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": _md("**经验条目**（门槛 %s）\n%s" % (f["gate"], f["lessons"])),
            },
            {"tag": "div", "text": _md("**明细**（最新 5 条）\n%s" % f["detail"])},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "数据源：reports/weak_cases 结案 md（立案日取自档案名）"
                        "｜n<5 只积累不下结论｜样本门槛 %d" % 5,
                    }
                ],
            },
        ],
    }


def watch_card(f):
    """§12 超期未结案巡检卡片（f=weak_track_watch.watch_fields 的 dict）。

    header orange 催办警告色 ⏰；异常案明细 md 块（每案判定+行动）。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "⏰ 超期未结案巡检（§12）· "
                "异常 %s/%s 案" % (f["n_bad"], f["n"]),
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _f2("在案", "%s 案" % f["n"]),
                    _f2("异常", "%s 案" % f["n_bad"]),
                    _f2("巡检", "超龄 60 天 / 停滞 3 天 / 任务失联"),
                    _f2("生成", f["ts"]),
                ],
            },
            {"tag": "hr"},
            {"tag": "div", "text": _md("**异常明细与催办动作**\n%s" % f["detail"])},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "数据源：在案 weak_track_*.jsonl（首末行 ts+任务状态）"
                        "｜§8 盯价格信号，§12 盯生命周期｜口径可用 --max-days/"
                        "--stall-days 调",
                    }
                ],
            },
        ],
    }


def audit_card(f):
    """§13 结案质量审计卡片（f=weak_track_audit.audit_fields 的 dict）。

    header turquoise 审计色 🧾；异常案明细 md 块（每案问题+修复动作）。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {
                "tag": "plain_text",
                "content": "🧾 结案质量审计（§13）· "
                "异常 %s/%s 例" % (f["n_bad"], f["n"]),
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    _f2("已结案", "%s 例" % f["n"]),
                    _f2("异常", "%s 例" % f["n_bad"]),
                    _f2("审计项", "字段/归档/档案/任务停/B类新案"),
                    _f2("生成", f["ts"]),
                ],
            },
            {"tag": "hr"},
            {"tag": "div", "text": _md("**问题明细与修复动作**\n%s" % f["detail"])},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "数据源：weak_cases close md 声明 vs reports/ 实物"
                        "（§12 盯在案失速，§13 盯已结案完整性）",
                    }
                ],
            },
        ],
    }


def archive_card(f):
    """§14 案例库飞书归档卡片（f=weak_track_archive.archive_send 构造）。

    header wathet 归档色 📚；K35-aa 增强补最近结案 3 案明细块（有
    结案才显示）与触发总次数字段；文档链接 action button。
    """
    els = [
        {
            "tag": "div",
            "fields": [
                _f2("快照", f["snap"]),
                _f2("已结案", "%s 例" % f["n"]),
                _f2("类型分布", "A %s / B %s / C %s" % (f["na"], f["nb"], f["nc"])),
                _f2("触发", "累计 %s 次" % f.get("trig", 0)),
                _f2("生成", f["ts"]),
            ],
        }
    ]
    if f.get("recent"):
        els += [
            {"tag": "hr"},
            {"tag": "div", "text": _md("**最近结案**\n%s" % f["recent"])},
        ]
    els += [
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "打开案例库文档"},
                    "type": "primary",
                    "url": f["doc_url"],
                }
            ],
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "全量快照式：每次一篇《weak 案例库 YYYYMMDD》"
                    "，最新即全量（含在案观察节）｜专属文件夹归置"
                    "，导入后清理源 md",
                }
            ],
        },
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "wathet",
            "title": {
                "tag": "plain_text",
                "content": "📚 案例库已归档（§14）· %s" % f["snap"],
            },
        },
        "elements": els,
    }


_EMOJI_T = {"A": "🟢", "B": "🟠", "C": "🔴"}


def recent_lines(cases, n=3):
    """最近结案 n 案色标一行式（§14 归档卡/§15 周报卡共用辅助）。"""
    rec = sorted(cases, key=lambda c: c["ts"], reverse=True)[:n]
    return "\n".join(
        "- %s %s：%s%%"
        % (
            _EMOJI_T.get(c["type"], "⚪"),
            " ".join(x for x in [c["code"], c.get("name") or ""] if x),
            ("%+.1f" % c["chg"]) if c.get("chg") is not None else "—",
        )
        for c in rec
    )


def weekly_card(f):
    """§15 周报聚合卡片（f=weak_track_weekly.weekly_fields 的 dict）。

    header indigo 周报色 📋；K35-ab 增强补「较上周」环比字段、「本周动态」
    块（新立案/结案/打点/触发+活跃明细）与最近结案块（recent_lines 公共
    辅助）；§12/§13 明细块条件渲染；全绿时 note 显示 verdict。
    """
    els = [
        {
            "tag": "div",
            "fields": [
                _f2("在案", "%s 案｜异常 %s" % (f["n_open"], f["n_open_bad"])),
                _f2("已结案", "%s 例｜异常 %s" % (f["n_closed"], f["n_closed_bad"])),
                _f2("较上周", f["trend"]),
                _f2("生成", f["ts"]),
            ],
        }
    ]
    els += [
        {"tag": "hr"},
        {
            "tag": "div",
            "text": _md(
                "**本周动态**：新立案 %s｜结案 %s｜跟踪打点 %s 行｜触发 %s 次"
                % (f["new_open"], f["closed_week"], f["rows_week"], f["trig_week"])
            ),
        },
    ]
    if f.get("act_detail"):
        els.append({"tag": "div", "text": _md(f["act_detail"])})
    if f.get("recent"):
        els += [
            {"tag": "hr"},
            {"tag": "div", "text": _md("**最近结案**\n%s" % f["recent"])},
        ]
    if f["n_open_bad"]:
        els += [
            {"tag": "hr"},
            {"tag": "div", "text": _md("**巡检明细（§12）**\n%s" % f["wdetail"])},
        ]
    if f["n_closed_bad"]:
        els += [
            {"tag": "hr"},
            {"tag": "div", "text": _md("**审计明细（§13）**\n%s" % f["adetail"])},
        ]
    els.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f["verdict"] + "｜周报：全绿也推送，主动汇报产线状态"
                    "（§12/§13 单跑静默，异常才喊）",
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "indigo",
            "title": {
                "tag": "plain_text",
                "content": "📋 weak 产线周报（§15）· "
                + ("全绿" if f["green"] else "有待办"),
            },
        },
        "elements": els,
    }


def health_card(f):
    """§16 任务群健康守护卡片（f=weak_track_health.health_fields 产物）。

    header red 告警色 🚨（区别巡检橙——本节盯自动化本身，巡检盯案例）。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "content": "🚨 任务群健康异常（§16）· %s 个" % f["n"],
            },
        },
        "elements": [
            {
                "tag": "div",
                "fields": [_f2("异常任务", "%s 个" % f["n"]), _f2("生成", f["ts"])],
            },
            {"tag": "hr"},
            {"tag": "div", "text": _md(f["detail"])},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "看守守夜人：WeakScan/月度三任务/周报/案例级"
                        "跟踪全族巡检；全绿静默不推送（§12 同款纪律）",
                    }
                ],
            },
        ],
    }


def guide_card(groups):
    """§17 产线导航卡片（groups=weak_track_guide.GROUPS）。

    默认色 header 🗺️（产线色系已满，导航用中性色）；每节奏组一个
    div 块（组名加粗+每节两行）；结构化数据直渲染。
    """
    els = []
    for gname, items in groups:
        md = "**%s**\n%s" % (
            gname,
            "\n".join(
                "- %s %s：%s\n  命令/任务：%s" % (s, n, d, c)
                + (("｜%s" % note) if note else "")
                for s, n, d, c, note in items
            ),
        )
        els.append({"tag": "div", "text": _md(md)})
        els.append({"tag": "hr"})
    els[-1] = {
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": "17 节全貌：改文案改 weak_track_guide.py 的 GROUPS；"
                "任务详情见 README §二",
            }
        ],
    }
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey",
            "title": {
                "tag": "plain_text",
                "content": "🗺️ weak 产线导航（§17）· 节奏 × 17 节",
            },
        },
        "elements": els,
    }


def scan_card(new_hits, meta):
    """信号2 扫描结果卡片（new_hits=新增候选 dict 列表，调用方已重点优先排序；
    meta=last_day/n_total/n_new/fp/scope[/n_focus]。★=二道筛重点）。"""
    top = new_hits[:8]
    lines = []
    for h in top:
        tag = ("  [%s]" % h["focus"]) if h.get("focus") else ""
        lines.append(
            "%s**%s %s**  超额 %.1fpp｜%s 名 %.0f%%（%s）%s"
            % (
                "★" if h.get("focus") else "　",
                h["code"],
                h["name"][:5],
                h["ex"],
                h["industry"],
                h["pct_in"],
                h["rank_in"],
                tag,
            )
        )
    more = len(new_hits) - len(top)
    if more > 0:
        lines.append("*…另有 %d 只见 CSV*" % more)
    flds = [
        _f2("数据日", meta["last_day"]),
        _f2("候选总数", "%d 只" % meta["n_total"]),
        _f2("口径", meta["scope"]),
        _f2("落盘", os.path.basename(meta["fp"])),
    ]
    if "n_focus" in meta:
        flds.insert(2, _f2("重点(二道筛)", "%d 只" % meta["n_focus"]))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": "🔎 信号2 扫描 · 新增 %d 只候选" % meta["n_new"],
            },
        },
        "elements": [
            {"tag": "div", "fields": flds},
            {"tag": "hr"},
            {
                "tag": "div",
                "text": _md(
                    "**新增候选（重点优先前 %d，★=二道筛重点）**\n%s"
                    % (len(top), "\n".join(lines))
                ),
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "建案：python weak_track_newcase.py <code> --name <名称>"
                        "｜★强转弱=前强补跌型优先建案｜日线粗筛，建案后走联动页三周期核验",
                    }
                ],
            },
        ],
    }


def send_card(card):
    """卡片 dict → pending 文件 → send_feishu.py interactive 模式直发。

    K43-d：发送前统一 to2() 转 2.0 结构（全族 11 张卡唯一出口）。
    """
    card = to2(card)
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_card.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(card, ensure_ascii=False))
    r = subprocess.run(
        [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp, "interactive"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return (r.stdout or "").strip()[:80]


def _last_state(fp):
    row = None
    with open(fp, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line.lstrip("\ufeff"))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_card.py", exc)
    return row


def main():
    ap = argparse.ArgumentParser(description="弱于板块 · 飞书卡片推送（§8/§9）")
    ap.add_argument("code", help="股票代码（默认读其状态流）")
    ap.add_argument("--close", choices=list(wc.CTYPE), help="结案类型 A/B/C")
    ap.add_argument("--note", default="", help="结案结论（--close 用）")
    ap.add_argument(
        "--alert", action="store_true", help="构造 §8 触发卡片（用状态流最新行）"
    )
    ap.add_argument("--send", action="store_true", help="真发（默认只打印）")
    a = ap.parse_args()
    c6 = a.code.strip().upper().split(".")[0]
    fp = os.path.join(wc.REP, "weak_track_%s.jsonl" % c6)
    rows = wc._load(fp)
    if not rows:
        print("状态流不存在或为空：%s" % fp)
        return 1
    if a.close:
        arch = os.path.basename(fp).replace(
            ".jsonl", ".closed_%s.jsonl" % __import__("time").strftime("%Y%m%d")
        )
        card = close_card(wc.close_fields(rows, wc.CTYPE[a.close], a.note, arch))
    elif a.alert:
        card = alert_card(rows[-1])
    else:
        print("指定 --close A|B|C 或 --alert")
        return 2
    print(json.dumps(card, ensure_ascii=False, indent=1))
    if a.send:
        print("发送：" + send_card(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
