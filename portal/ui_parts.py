# -*- coding: utf-8 -*-
"""缠论新域 UI 组件（I1a 步 1）· 复用智测 L3 布局范式。

来源与纪律
──────────
· 样式值不是发明的，逐项抄自 `portal/quant.html`（智测 /quant 门户）：
  `.sec` margin-top:28px / `.sec-head` flex+space-between / `.sec-title`
  font-size:13px+700+letter-spacing:2.5px / `.sec-note` 11px 等宽 /
  `.kpi` padding:14px 16px 12px + flex column。
· 颜色一律读 `tokens.css` 语义变量（--color-ok / --color-warn / --color-err /
  --text-dim / --bg-surface / --border-weak），**禁止硬编码 hex**。
· 纯函数：无 I/O、无全局状态、无时间副作用（`today` 可注入，便于测）。

⚠️ asof 的唯一铁律（I1 施工单 §1.1）
──────────────────────────────────
`data_day` 只接受「数据自身声明的日期」。**禁止把文件 mtime 传进来顶替**——
实测 `reports/opt.html` 的 mtime=10-03 而内容数据日=09-26，用 mtime 会凭空
新鲜 7 天，等于用一个新假绿替换旧的「无提示」。取不到就传 None，
渲染成「数据日未知」，比编一个日期安全。
"""
import html as _h
from datetime import date as _date

__all__ = ["STYLE", "style_block", "sec", "asof_badge", "kpi"]

# 只含新域用到的类；页面 head 里放一次 style_block()。
# 类名统一 ui- 前缀，避免与老页 .sec/.kpi 同名互相污染。
STYLE = """<style>
.ui-sec{margin-top:28px}
.ui-sec-head{display:flex;align-items:baseline;justify-content:space-between;
gap:12px;margin-bottom:12px;flex-wrap:wrap}
.ui-sec-title{font-size:13px;font-weight:700;letter-spacing:2.5px;
color:var(--text-dim)}
.ui-sec-title::before{content:"\\25E4";margin-right:6px;color:var(--border)}
.ui-sec-note{font-size:11px;color:var(--text-dim);font-family:var(--font-mono);
display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ui-asof{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;
border-radius:var(--r-pill);font-size:10.5px;line-height:1.7;
border:1px solid var(--border);color:var(--text-dim)}
.ui-asof--fresh{color:var(--color-ok);border-color:var(--color-ok)}
.ui-asof--lag{color:var(--color-warn);border-color:var(--color-warn)}
.ui-asof--dead{color:var(--color-err);border-color:var(--color-err);opacity:.85}
.ui-asof--unknown{color:var(--text-dim);border-color:var(--border);
border-style:dashed}
.ui-kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:1px;background:var(--border-weak);border:1px solid var(--border-weak);
border-radius:var(--r-m);overflow:hidden}
.ui-kpi{background:var(--bg-surface);padding:14px 16px 12px;display:flex;
flex-direction:column;gap:3px;min-width:0}
.ui-kpi-l{font-size:11px;color:var(--text-dim);letter-spacing:.5px}
.ui-kpi-v{font-size:22px;font-weight:700;color:var(--text);
font-variant-numeric:tabular-nums}
.ui-kpi-sub{font-size:12px;color:var(--text-dim);font-variant-numeric:tabular-nums}
.ui-kpi-n{font-size:10.5px;color:var(--text-dim);font-family:var(--font-mono)}
</style>"""


def style_block():
    """返回内联样式块。页面 <head> 里放一次即可（不写 tokens.css，避免动全站）。"""
    return STYLE


def _state_of(data_day, today=None):
    """按数据日与今天的天数差判定状态。data_day 非法/缺失 → unknown。"""
    if not data_day:
        return "unknown"
    try:
        d = _date.fromisoformat(str(data_day)[:10])
    except (ValueError, TypeError):
        return "unknown"
    t = today or _date.today()
    gap = (t - d).days
    if gap <= 2:
        return "fresh"
    if gap <= 10:
        return "lag"
    return "dead"


def asof_badge(data_day, gen_at="", state=None, today=None):
    """数据日徽章。

    data_day : 'YYYY-MM-DD' 或 None（None 渲染「数据日未知」，**不要传 mtime**）
    gen_at   : 生成时刻文本，与数据日分列显示（两者不是一回事）
    state    : 强制覆盖 fresh/lag/dead/unknown；None 按天数自动判
    today    : 注入"今天"以便单测；None 取系统日期
    """
    st = state or _state_of(data_day, today)
    if st == "unknown" or not data_day:
        text = "数据日未知"
        tip = "该页未提供数据自身声明的日期；按纪律不以文件修改时间顶替"
    else:
        text = "数据日 " + str(data_day)[:10]
        try:
            gap = (today or _date.today()) - _date.fromisoformat(
                str(data_day)[:10])
            days = gap.days
            text += (" · 停更 %d 天" % days) if days > 0 else " · 当日"
            tip = "数据日 %s（距今天 %d 天）" % (str(data_day)[:10], days)
        except (ValueError, TypeError):
            tip = "数据日 %s" % str(data_day)[:10]
    parts = ['<span class="ui-asof ui-asof--%s" title="%s">%s</span>'
             % (_h.escape(st), _h.escape(tip), _h.escape(text))]
    if gen_at:
        parts.append('<span class="ui-gen">生成 %s</span>'
                     % _h.escape(str(gen_at)))
    return "".join(parts)


def sec(title, note="", body="", anchor=""):
    """分区：◤ 标题 + 右上 note（asof/生成时刻等已渲染 HTML）+ 内容。

    title 会被转义；note 视为可信 HTML（由本模块的 asof_badge 等生成）。
    """
    idattr = ' id="%s"' % _h.escape(anchor, quote=True) if anchor else ""
    head = ('<div class="ui-sec-head"><div class="ui-sec-title">%s</div>%s</div>'
            % (_h.escape(str(title)),
               ('<div class="ui-sec-note">%s</div>' % note) if note else ""))
    return '<div class="ui-sec"%s>%s%s</div>' % (idattr, head, body)


def kpi(label, value, note=""):
    """KPI 卡。value 传 (主值, 副值) 二元组可出次行。

    多个 KPI 请用 `<div class="ui-kpi-row">` 包裹（该类已在本模块 STYLE 内定义，
    页面不要私写 grid 容器）。
    """
    if isinstance(value, (tuple, list)) and len(value) == 2:
        main, sub = value
        vhtml = ('<div class="ui-kpi-v">%s</div><div class="ui-kpi-sub">%s</div>'
                 % (_h.escape(str(main)), _h.escape(str(sub))))
    else:
        vhtml = '<div class="ui-kpi-v">%s</div>' % _h.escape(str(value))
    return ('<div class="ui-kpi"><div class="ui-kpi-l">%s</div>%s%s</div>'
            % (_h.escape(str(label)), vhtml,
               ('<div class="ui-kpi-n">%s</div>' % _h.escape(str(note)))
               if note else ""))
