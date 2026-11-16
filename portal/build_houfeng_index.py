# -*- coding: utf-8 -*-
"""候风平台门户页生成器：模板 _templates/houfeng_index.src.html -> 产物 houfeng/index.html

为什么需要这一页
----------------
houfeng/ 下原有 10 个页面全是平级的 HTML 文件，没有入口页。后果实测：
  - /houfeng/ 返回 403（目录列表被禁，且没有 index.html）
  - 宿主首页候风卡片只能链到 HOUFENG_TODAY.html —— 把「日更状态页」当成了平台门面
  - 3 页对外 / 7 页内部的区分只写在 PUBLISH_SET.json 里，页面本身看不出来
这一页补的就是「平台入口」：今日 / 这套方法 / 账 / 对外 四区 + 两页未建占位。

一条不能违反的规则：数字不在这里重算
------------------------------------
今日那一块的结论词与四个数字，全部从 build_houfeng_today.render() 的产物里抽取，
本脚本不复制它的判定分支。理由：两页对同一天必须说同一句话。若拷贝一份判定逻辑，
下一次改判定就会只改一处，两页开始各说各的——这类「两个页面两个结论」比页面缺失严重。
抽不到就响亮失败（SystemExit 非 0），不降级、不猜。

用法：python build_houfeng_index.py
"""
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "_templates", "houfeng_index.src.html")
OUT = os.path.join(HERE, "houfeng", "index.html")
TODAY_PY = os.path.join(HERE, "daily_report", "build_houfeng_today.py")

BLOCK_RE = re.compile(r"(<!--TODAY_BEGIN-->)(.*?)(<!--TODAY_END-->)", re.S)
VERDICT_RE = re.compile(
    r'<div class="verdict (ok|warn|bad)">\s*<h2>(.*?)</h2>\s*<p>(.*?)</p>', re.S)
NUM_RE = re.compile(
    r'<div class="k">(.*?)</div>\s*<div class="v">(.*?)</div>\s*<div class="r">(.*?)</div>', re.S)

NUM_KEYS = ["可买批次", "候选篮", "观察池", "最后批次"]
STAMP = '<div class="badge">数据日 <b>未采集</b></div>'
GEN = '<div class="gen">模板骨架 · 未经生成器渲染</div>'

# ---- 边界页（boundary.html）：只搬冻结文件里的事实，不手写任何数字 ----
BTPL = os.path.join(HERE, "_templates", "houfeng_boundary.src.html")
BOUT = os.path.join(HERE, "houfeng", "boundary.html")
FROZEN = os.path.join(HERE, os.pardir, "quant_backtest", "_qpzm_rd_20261106", "M2_FROZEN.json")

# 入口页两个区的数据截止日：读各自数据源的 window.end，不写死。
# label 是页面上给读者看的名字，path 是它真正渲染的那份数据。
# 只收「数据算到哪一天」这一种日期。台账的 status_latest.today 是"台账更新日"，
# 语义不同，混进来就是拿两种日期互相比较（判据：比较先对齐口径）。故不入列。
DATE_SOURCES = [
    ("规则与冻结口径", "houfeng_snapshot.json"),
    ("竞技场 · 三年期", "houfeng_arena_36m.json"),
    ("持仓周期", "houfeng_holdgrid.json"),
    ("红利防御档", "houfeng_dividend.json"),
]


def load_mod(path, name):
    if not os.path.exists(path):
        raise SystemExit("找不到 %s" % path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_d(s):
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:]) if s and len(str(s)) == 8 else "未采集"


def extract_today(html):
    """从今日页渲染结果里取结论与四个数字。取不到就失败——宁可不出页，不出假页。"""
    vm = VERDICT_RE.search(html)
    if not vm:
        raise SystemExit("今日页渲染结果里找不到 verdict 块（结构与预期不符）")
    tone, head, kicker = vm.group(1), vm.group(2).strip(), vm.group(3).strip()

    nums = NUM_RE.findall(html)
    keys = [k.strip() for k, _, _ in nums]
    if keys != NUM_KEYS:
        raise SystemExit("今日页数字块与预期不符：期望 %r，实际 %r" % (NUM_KEYS, keys))
    return tone, head, kicker, nums


def today_block(tone, head, kicker, nums, miss):
    kpis = "".join(
        '<div class="kpi"><div class="k">%s</div><div class="v">%s</div>'
        '<div class="r">%s</div></div>' % (esc(k), esc(v), esc(r))
        for k, v, r in nums)
    missline = ("".join("<li>%s</li>" % esc(m) for m in miss)) if miss else ""
    missblk = ('<p class="note" style="color:#a33">以下项未采集，本页不对这些项作任何判断：'
               + "、".join(esc(m) for m in miss) + "</p>") if missline else ""
    return (
        '<div class="verdict %s">\n'
        '  <h2>%s</h2>\n'
        '  <p class="why">%s</p>\n'
        '  <div class="kpis">%s</div>\n'
        '  %s\n'
        '  <div class="drill">'
        '<a href="HOUFENG_TODAY.html">看逐闸解释</a>'
        '<a href="../daily_report/houfeng.html">看信号台账</a>'
        '<a href="HOUFENG_PLAYBOOK.html">看回放日历</a>'
        '</div>\n'
        '</div>' % (tone, head, esc(kicker), kpis, missblk))


def stamp_block(asof, lag):
    if isinstance(lag, int) and lag > 1:
        style = ' style="background:#fdf6e6;border-color:#edd9a3"'
        tail = " · 落后 %d 个交易日" % lag
    elif isinstance(lag, int) and lag == 1:
        style = ' style="background:#fdf6e6;border-color:#edd9a3"'
        tail = " · 落后 1 个交易日"
    elif asof:
        style = ""
        tail = " · 已是最新已收盘交易日"
    else:
        style = ' style="background:#fdeeee;border-color:#eebfbf"'
        tail = " · 交易日历未采集"
    return '<div class="badge"%s>数据日 <b>%s</b>%s</div>' % (style, esc(fmt_d(asof)), tail)


def read_json_soft(path):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def _end_of(fn, d):
    """从各数据源里取「这份数据算到哪一天」。字段位置各页不同，所以逐源分派。"""
    if fn == "houfeng_dividend.json":
        w = ((d.get("meta") or {}).get("window") or [None, None])
        return str(w[1]) if len(w) > 1 and w[1] else None
    if fn == "houfeng_pick_20261106.json":
        v = (d.get("meta") or {}).get("signal_date")
        return str(v) if v else None
    if fn == "houfeng_snapshot.json":
        v = d.get("data_date")
        return str(v) if v else None
    w = d.get("window")
    if isinstance(w, dict):
        v = w.get("end")
        return str(v) if v else None
    return None


def date_sources(sources):
    """[(label, end8)]；读不到 end 的记 None，由渲染层如实报「未采集」。"""
    out = []
    for label, fn in sources:
        out.append((label, _end_of(fn, read_json_soft(os.path.join(HERE, "houfeng", fn)))))
    return out


def asof_span(dates, base_asof, days):
    """区标题旁的截止日。一致就一句话；不一致就逐项列出来——绝不平均成一个数。

    为什么写在这一层而不是每页页头：那 5 个方法页是静态 HTML，页面上写死的日期
    会随数据重跑而失真（写 11-06、实际重跑到 11-20，读者看到的是过期的日期还以为是新的）。
    这一类"写死的日期"比没有日期更糟。所以日期只出现在渲染层。
    """
    if not dates or any(e is None for _, e in dates):
        missing = [l for l, e in dates if e is None] or ["全部"]
        return ('<span style="color:#d93025">数据截止日未采集（%s）</span>'
                % esc("、".join(missing)))
    ends = sorted({e for _, e in dates})
    if len(ends) == 1:
        e = ends[0]
        tail = ""
        if base_asof and e != base_asof and days:
            n = len([x for x in days if e < x <= base_asof])
            if n > 0:
                tail = "，较今日板块落后 %d 个交易日" % n
        tone = ' style="color:#d98600"' if tail else ""
        return ('<span%s>数据算到 <b>%s</b>%s</span>' % (tone, esc(fmt_d(e)), tail))
    items = "；".join("%s %s" % (esc(l), esc(fmt_d(e)))
                     for l, e in sorted(dates, key=lambda x: x[1]))
    return ('<span style="color:#d93025">各页数据日不一致，逐项列出 —— %s</span>' % items)


def repl_block(tpl, tag, body):
    """替换 <!--TAG_BEGIN-->…<!--TAG_END--> 且断言锚点恰好一处。"""
    pat = re.compile(r"(<!--%s_BEGIN-->)(.*?)(<!--%s_END-->)" % (tag, tag), re.S)
    n = len(pat.findall(tpl))
    if n != 1:
        raise SystemExit("模板锚点异常：%s 出现 %d 处（应为 1）" % (tag, n))
    return pat.sub(lambda m: m.group(1) + body + m.group(3), tpl, count=1)


def render_boundary(fz, today, days):
    """边界页：成本 / 已否决 / 已披露 / 回炉条件。全部读冻结文件，不手写数字。"""
    tpl = io.open(BTPL, encoding="utf-8").read()
    form = fz.get("form") or {}
    st = fz.get("status") or ""
    frozen_at = fz.get("frozen_at") or ""

    ver = "未采集"
    m = re.match(r"(FROZEN[^-]*)", st)
    if m:
        ver = m.group(1).strip()
    stamp = ('<div class="badge">冻结口径 <b>%s</b>%s</div>'
             % (esc(ver), (" · " + esc(frozen_at)) if frozen_at else ""))

    # 一、成本
    c = form.get("cost_roundtrip")
    cost = '<p>未采集：冻结文件里没有 cost_roundtrip。</p>'
    if isinstance(c, (int, float)):
        half = c / 2.0
        cost = (
            '<div class="big">%.2f%%<small>往返（买 + 卖）</small></div>'
            '<p style="margin-top:8px">拆开就是单边 <b>%.2f%%</b>：买入时按成交价上浮、'
            '卖出时按成交价下调，两边各扣一次。</p>'
            '<table class="kv">'
            '<tr><th>怎么用的</th><td>持仓到期卖出时，买入成本按 <code>价×(1+%.4f)</code>、'
            '卖出按 <code>价×(1−%.4f)</code> 计算</td></tr>'
            '<tr><th>这个数是哪来的</th><td>假设值，不是从成交回单里统计出来的。'
            '所以它是一个<b>口径</b>，不是一次<b>测量</b>。</td></tr>'
            '<tr><th>影响方向</th><td>如果真实摩擦（冲击成本、滑点、排队未成交）比它高，'
            '本文所有收益都是<b>高估</b>。冻结文件的披露项第一条说的就是这件事。</td></tr>'
            '</table>' % (c * 100, half * 100, half, half))

    # 二、已否决
    rej = fz.get("rejected") or []
    if rej:
        rows = []
        for r in rej:
            rows.append(
                '<div class="item bad"><p class="hd">%s<span class="vd">%s</span></p>'
                '<p class="ev">%s · %s</p></div>'
                % (esc(r.get("item", "")), esc(r.get("verdict", "")),
                   esc(r.get("date", "")), esc(r.get("evidence", ""))))
        reject = "".join(rows) + (
            '<p class="note">这三条都是<b>试过之后变差</b>，不是没试。'
            '其中两项动的是排序键与止损，属于同一类失败：'
            '这套方法的收益来源是"坏日子不交易 + 持仓到期自然了结"，'
            '任何在高位加规则的动作都在剪碎这个波段。</p>')
    else:
        reject = '<p>未采集：冻结文件里没有 rejected 列表。</p>'

    # 三、已披露
    disc = fz.get("disclosures") or []
    if disc:
        disclosed = ('<ol class="disc">%s</ol>'
                     % "".join("<li>%s</li>" % esc(x) for x in disc))
    else:
        disclosed = '<p>未采集：冻结文件里没有 disclosures 列表。</p>'

    # 四、回炉条件
    ng = fz.get("next_gate") or {}
    if ng:
        blocks = []
        if ng.get("A5_confirm"):
            blocks.append('<div class="g"><div class="t">进入下一步的门（A5）</div>'
                          '<div class="c">%s</div></div>' % esc(ng["A5_confirm"]))
        if ng.get("rollback"):
            blocks.append('<div class="g"><div class="t">发现问题的处置</div>'
                          '<div class="c">%s</div></div>' % esc(ng["rollback"]))
        gate = '<div class="gate">%s</div>' % "".join(blocks)
    else:
        gate = '<p>未采集：冻结文件里没有 next_gate。</p>'
    if st:
        gate += ('<p class="note">当前状态原文：%s</p>' % esc(st))

    tpl = repl_block(tpl, "STAMP", stamp)
    tpl = repl_block(tpl, "COST", cost)
    tpl = repl_block(tpl, "REJECT", reject)
    tpl = repl_block(tpl, "DISC", disclosed)
    tpl = repl_block(tpl, "GATE", gate)
    return tpl


def render(d, tone, head, kicker, nums):
    tpl = io.open(TPL, encoding="utf-8").read()

    # 三处锚点都必须恰好一处。少一处说明模板被改坏了，多一处说明有重复块。
    n_block = len(BLOCK_RE.findall(tpl))
    n_stamp = tpl.count(STAMP)
    n_gen = tpl.count(GEN)
    if (n_block, n_stamp, n_gen) != (1, 1, 1):
        raise SystemExit("模板锚点异常：TODAY=%d STAMP=%d GEN=%d（都应为 1）"
                         % (n_block, n_stamp, n_gen))

    out = BLOCK_RE.sub(lambda m: m.group(1) + today_block(tone, head, kicker, nums, d["miss"]) + m.group(3),
                       tpl, count=1)
    out = out.replace(STAMP, stamp_block(d["asof"], d["lag_completed"]), 1)
    out = out.replace(GEN, '<div class="gen">%s 生成</div>'
                      % datetime.now().strftime("%Y-%m-%d %H:%M"), 1)

    # 两个区的数据截止日：各自读真源，不写死、不合并成平均值。
    days = d.get("_days") or []
    method = date_sources(DATE_SOURCES)
    ledger = date_sources([("交易回放日历", "houfeng_playbook.json"),
                           ("历史选股快照", "houfeng_pick_20261106.json")])
    out = repl_block(out, "ASOF_METHOD", asof_span(method, d.get("asof"), days))
    out = repl_block(out, "ASOF_LEDGER", asof_span(ledger, d.get("asof"), days))

    # 边界区的两张卡：摘要也读冻结文件，避免卡片说一套、详情页说另一套。
    fz = read_json_soft(FROZEN)
    form = fz.get("form") or {}
    c = form.get("cost_roundtrip")
    if isinstance(c, (int, float)):
        cost_tag = "%0.2f%% 往返" % (c * 100)
        cost_lede = ('<p>单边 %0.2f%%、往返 %0.2f%%。来源是假设值，'
                     '没有成交回单校准 —— 真实摩擦更高时，收益是高估的。</p>'
                     % (c * 50, c * 100))
    else:
        cost_tag = "未采集"
        cost_lede = '<p>未采集：冻结文件不可读，本卡不对成本作任何陈述。</p>'
    nrej = len(fz.get("rejected") or [])
    ndisc = len(fz.get("disclosures") or [])
    if nrej:
        rej_tag = "已否决 %d 项" % nrej
        rej_lede = ('<p>试过并判负 %d 项、已知偏差 %d 条、回炉条件写在同一个文件里。'
                    '点进去看原文，不改写。</p>' % (nrej, ndisc))
    else:
        rej_tag = "未采集"
        rej_lede = '<p>未采集：冻结文件不可读，本卡不作任何陈述。</p>'
    out = repl_block(out, "COST_TAG", '<span class="tagx">%s</span>' % esc(cost_tag))
    out = repl_block(out, "COST_LEDE", cost_lede)
    out = repl_block(out, "REJ_TAG", '<span class="tagx">%s</span>' % esc(rej_tag))
    out = repl_block(out, "REJ_LEDE", rej_lede)
    return out


def main():
    if os.path.abspath(TPL) == os.path.abspath(OUT):
        raise SystemExit("模板与产物是同一文件，拒绝写入（会把真源覆盖掉）")
    if not os.path.exists(TPL):
        raise SystemExit("模板不存在：%s" % TPL)

    bt = load_mod(TODAY_PY, "_hf_today_for_index")
    d = bt.build()
    d["_days"] = bt.trading_days()
    tone, head, kicker, nums = extract_today(bt.render(d))

    html = render(d, tone, head, kicker, nums)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(html)

    # 读回校验：写进去的东西必须真的在文件里，且骨架痕迹必须消失。
    back = io.open(OUT, encoding="utf-8").read()
    if head not in back:
        raise SystemExit("写后回读：结论词不在产物里")
    if "<b>未采集</b>" in back:
        raise SystemExit("写后回读：数据日徽标仍是骨架（未替换）")
    if "模板骨架" in back:
        raise SystemExit("写后回读：生成时间仍是骨架（未替换）")
    # 完整性看结构不看字节数阈值：阈值是拍的，漏一个区就查不出来。
    for must in ("这套方法", "边界与证伪", "boundary.html", "HOUFENG_BROCHURE.html", "</html>"):
        if must not in back:
            raise SystemExit("写后回读：产物缺少 %r，渲染不完整" % must)
    tpl_bytes = len(io.open(TPL, encoding="utf-8").read().encode("utf-8"))
    if len(back.encode("utf-8")) < tpl_bytes - 1500:
        raise SystemExit("写后回读：产物 %d 字节，比模板 %d 字节短太多"
                         % (len(back.encode("utf-8")), tpl_bytes))

    print("IDX: houfeng/index.html %dB tone=%s 数据日=%s 落后=%s 数字=%s"
          % (len(back.encode("utf-8")), tone, fmt_d(d["asof"]), d["lag_completed"],
             " / ".join(v for _, v, _ in nums)))

    # ---- 边界页 ----
    if os.path.abspath(BTPL) == os.path.abspath(BOUT):
        raise SystemExit("边界页模板与产物是同一文件，拒绝写入")
    if not os.path.exists(BTPL):
        raise SystemExit("边界页模板不存在：%s" % BTPL)
    if not os.path.exists(FROZEN):
        raise SystemExit("冻结口径文件不存在：%s（边界页没有真源就不该出页）" % FROZEN)
    fz = read_json_soft(FROZEN)
    if not fz:
        raise SystemExit("冻结口径文件读不出内容：%s" % FROZEN)

    bhtml = render_boundary(fz, d.get("today"), d["_days"])
    io.open(BOUT, "w", encoding="utf-8", newline="\n").write(bhtml)
    bback = io.open(BOUT, encoding="utf-8").read()
    for must in ('id="cost"', 'id="reject"', "已经知道的偏差", "什么情况下要回炉", "</html>"):
        if must not in bback:
            raise SystemExit("边界页回读：缺少 %r" % must)
    for bad in ("本页不对成本作任何陈述", "生成器未能读取冻结口径", "模板骨架"):
        if bad in bback:
            raise SystemExit("边界页回读：仍是骨架痕迹 %r" % bad)
    c = (fz.get("form") or {}).get("cost_roundtrip")
    print("BND: houfeng/boundary.html %dB 成本往返=%s 否决=%d 披露=%d 冻结=%s"
          % (len(bback.encode("utf-8")),
             ("%0.2f%%" % (c * 100)) if isinstance(c, (int, float)) else "未采集",
             len(fz.get("rejected") or []), len(fz.get("disclosures") or []),
             (fz.get("frozen_at") or "未采集")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
