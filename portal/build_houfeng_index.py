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
    return out


def main():
    if os.path.abspath(TPL) == os.path.abspath(OUT):
        raise SystemExit("模板与产物是同一文件，拒绝写入（会把真源覆盖掉）")
    if not os.path.exists(TPL):
        raise SystemExit("模板不存在：%s" % TPL)

    bt = load_mod(TODAY_PY, "_hf_today_for_index")
    d = bt.build()
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
    for must in ("这套方法", "还缺的两页", "HOUFENG_BROCHURE.html", "</html>"):
        if must not in back:
            raise SystemExit("写后回读：产物缺少 %r，渲染不完整" % must)
    tpl_bytes = len(io.open(TPL, encoding="utf-8").read().encode("utf-8"))
    if len(back.encode("utf-8")) < tpl_bytes - 1500:
        raise SystemExit("写后回读：产物 %d 字节，比模板 %d 字节短太多"
                         % (len(back.encode("utf-8")), tpl_bytes))

    print("IDX: houfeng/index.html %dB tone=%s 数据日=%s 落后=%s 数字=%s"
          % (len(back.encode("utf-8")), tone, fmt_d(d["asof"]), d["lag_completed"],
             " / ".join(v for _, v, _ in nums)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
