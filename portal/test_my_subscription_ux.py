# -*- coding: utf-8 -*-
"""客户订阅面「到期可视化」离线契约测试（飞书中台 P3.9-lite · N-C 工位 2026-10-20）。

问题：``portal/static/my_subscription.html`` 原先只把「档位 / 开始 / 结束 / 状态」
四个原始字段摊给客户看，客户看不出还剩几天，也不会在即将到期时被提醒。

做法：**纯前端**。状态端点的字段已按 P3.7-lite L-A 白名单最小化
（plan_id / start / end / status + 掩码 open_id），本工位不得扩展该端点，
故剩余天数必须由页面用 start / end 自算。本文件全部读源码断言，
零网络、零服务启动、零浏览器，钉住以下边界：

 1. 页面不依赖任何后端派生的剩余天数字段（前端自算这条边界）；
 2. 存在日历日归一实现（本地 0 点对齐 + 整数日差），不是裸毫秒差取整；
 3. 三档阈值（>3 / 1~3 / 0 或过期）文案齐全；
 4. 到期日解析失败走「无法解析到期日」显式分支；
 5. 端点报错只说「无法读取订阅状态」，绝不出现「未订阅」（读不到 ≠ 没有订阅）；
 6. 全页无金额符号、无「立即购买」（定价未定、收银台未上线）；
 7. 既有四字段渲染锚点（plan_id / start / end / status）仍在用；
 8. P3.7-lite L-A 加的那句「本页不展示金额信息」仍在；
 9. 页面不发任何写请求；
10. 续费引导为静态文案，不含二维码 / 外链；
11. 三档色语义复用既有 CSS 变量；
12. 到期会失去什么写清楚（但不写任何额度数字）；
13. 「已到期」与「今天到期」分开显示；
14. 未知状态原样透出并标注，不映射成「正常」。
"""

import re
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
PAGE = PORTAL / "static" / "my_subscription.html"


def page_src() -> str:
    assert PAGE.exists(), f"缺少页面文件: {PAGE}"
    return PAGE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 边界：剩余天数必须在前端自算，不依赖后端派生字段
# ---------------------------------------------------------------------------

def test_01_no_backend_derived_days_field():
    src = page_src()
    for banned in ("days_left", "daysLeft", "remaining_days", "remainingDays",
                   "expires_in", "expiresIn", "days_remaining"):
        assert banned not in src, (
            f"页面出现对后端派生字段 {banned} 的依赖——status 端点字段已最小化，"
            "剩余天数只能由 start / end 在前端自算")
    # 自算的入口必须在页面内，且读的是既有 end 字段
    assert "function calendarDay(" in src, "缺日历日归一函数"
    assert "function expiryState(" in src, "缺到期状态判定函数"
    assert "expiryState(sum.status,sum.end)" in src, "到期判定应只吃 status / end 两个既有字段"


# ---------------------------------------------------------------------------
# 2. 日历日归一：本地 0 点对齐 + 四舍五入整数日差，不是裸毫秒差截断
# ---------------------------------------------------------------------------

def test_02_calendar_day_normalization_not_ms_division():
    src = page_src()
    assert "const DAY_MS=86400000" in src, "缺一天毫秒常量（应集中定义，便于审计）"
    # 纯日期按当日结束计：与后端 renewal_reminder 的 23:59:59 口径一致
    assert "23,59,59" in src, "缺「纯日期按当日结束」的归一（否则今天到期会被算成已过期）"
    assert "dateOnly" in src, "缺纯日期 / 带时刻的分支标记"
    # 用本地 0 点重建日期，避免 Date 对纯日期串按 UTC 解析导致东八区串日
    assert "new Date(abs.getFullYear(),abs.getMonth(),abs.getDate())" in src, \
        "带时刻的值未按本地年月日重建 0 点"
    assert "getFullYear()!==y" in src, "缺非法日期（如 2026-02-30）的进位拦截"
    # 整数日差必须是「0 点相减后四舍五入」，所有 /DAY_MS 都应包在 Math.round 里
    assert re.search(r"Math\.round\(\(\w+\.getTime\(\)-\w+\.getTime\(\)\)/DAY_MS\)", src), \
        "缺日历日整数差实现"
    for m in re.finditer(r"/\s*DAY_MS", src):
        head = src[max(0, m.start() - 80):m.start()]
        assert "Math.round" in head, f"发现未四舍五入的毫秒差除法：…{head[-80:]}"
    assert "Math.floor" not in src, "不得用 floor 截断毫秒差（会把今天 23:00 算成 0 天）"


# ---------------------------------------------------------------------------
# 3. 三档阈值与文案
# ---------------------------------------------------------------------------

def test_03_three_tier_thresholds_and_copy():
    src = page_src()
    # 阈值：>3 中性 / <=3 警示 / <=0 或已过期 失效
    assert "days<=3" in src, "缺 1~3 天档阈值"
    assert "days<=0" in src, "缺 0 天档阈值"
    assert "head:'剩余 '+days+' 天'" in src, "缺 >3 天的中性文案「剩余 N 天」"
    assert "剩余 '+days+' 天 · 即将到期" in src, "缺 1~3 天的警示文案「剩余 N 天 · 即将到期」"
    assert "head:'今天到期'" in src, "缺 0 天文案「今天到期」"
    assert "head:'已到期'" in src, "缺过期文案「已到期」"
    # 三档各带独立 tier 类，供色语义挂靠
    assert "tier:'neutral'" in src and "tier:'warn'" in src and "tier:'severe'" in src, \
        "三档应各带一个 tier 标记"
    # >3 天必须真的走中性档：不能所有分支都落 warn
    assert src.count("tier:'neutral'") == 1 and src.count("tier:'warn'") == 1, \
        "中性 / 警示档应各只有一个出口"


# ---------------------------------------------------------------------------
# 4. 解析失败：显式报「无法解析到期日」，不蒙混成 0 天 / 负数
# ---------------------------------------------------------------------------

def test_04_unparseable_end_branch():
    src = page_src()
    assert "无法解析到期日" in src, "缺解析失败分支文案"
    assert "本页不猜测剩余天数" in src, "解析失败应说明不猜测天数"
    # 解析失败即返回 null 给调用方，不能返回 0 让页面显示「今天到期」
    m = re.search(r"function calendarDay\(raw\)\{(.*?)\n\}", src, re.S)
    assert m, "calendarDay 结构异常"
    body = m.group(1)
    assert body.count("return null") >= 3, \
        "空值 / 非法日期 / 非日期串都应 return null（不得退化为 0）"


# ---------------------------------------------------------------------------
# 5. 降级纪律：读不到 ≠ 没有订阅
# ---------------------------------------------------------------------------

def test_05_error_state_says_cannot_read_not_unsubscribed():
    src = page_src()
    assert "无法读取订阅状态" in src, "缺「无法读取订阅状态」错误态文案"
    assert "未订阅" not in src, "错误态不得出现「未订阅」（读不到不等于没有订阅）"
    assert "订阅不存在" not in src and "无有效订阅" not in src, \
        "错误态不得断言订阅有无"
    assert "读不到不等于没有订阅" in src, "降级区应显式写清「读不到 ≠ 没有订阅」"
    # 错误路径必须把到期条一起收起，避免残留上一轮的「剩余 N 天」
    assert src.count("$('expiry-bar').hidden=true") >= 2, \
        "无 open_id 与读取失败两条路径都应收起到期条"
    assert "error" in src and "d.error" in src, "error 响应应原样透出"


# ---------------------------------------------------------------------------
# 6. 无金额、无收银台按钮
# ---------------------------------------------------------------------------

def test_06_no_money_no_buy_button():
    src = page_src()
    for banned in ("¥", "￥", "元", "价格", "定价", "立即购买", "去支付", "扫码"):
        assert banned not in src, f"页面不得出现 {banned}（定价未定 / 收银台未上线）"


# ---------------------------------------------------------------------------
# 7. 既有四字段渲染锚点不得丢失
# ---------------------------------------------------------------------------

def test_07_four_field_anchors_kept():
    src = page_src()
    for anchor in ("sum.plan_id", "sum.start", "sum.end", "sum.status"):
        assert anchor in src, f"缺既有四字段渲染锚点 {anchor}"
    assert "fmtRange(sum.start,sum.end)" in src, "有效期区间渲染被改动"


# ---------------------------------------------------------------------------
# 8. P3.7-lite L-A 的金额声明仍在
# ---------------------------------------------------------------------------

def test_08_p37_amount_disclaimer_kept():
    src = page_src()
    assert "本页不展示金额信息" in src, "P3.7-lite 的金额声明被删"
    assert "通过飞书联系" in src, "金额同句的续费口径被删"


# ---------------------------------------------------------------------------
# 9. 零写请求
# ---------------------------------------------------------------------------

def test_09_no_write_requests():
    src = page_src()
    assert "POST" not in src, "页面不得出现 POST（零写操作契约）"
    assert not re.search(r"method\s*:\s*['\"](PUT|PATCH|DELETE)['\"]", src, re.I)
    assert not re.search(r"<form\b", src, re.I), "不得含 form 提交"
    fetches = re.findall(r"fetch\(([^)]*)\)", src)
    assert fetches, "页面应有 fetch 调用"
    for f in fetches:
        assert "'GET'" in f or '"GET"' in f, f"fetch 非 GET: {f}"
    assert src.count("fetch(") == len(fetches), "存在无法静态核验的 fetch 写法"


# ---------------------------------------------------------------------------
# 10. 续费引导：静态文案，无二维码 / 无外链 / 无按钮
# ---------------------------------------------------------------------------

def test_10_static_renew_guide_without_qr_or_link():
    src = page_src()
    assert "续费方式：通过飞书联系管理员" in src, "缺续费引导静态文案"
    assert "二维码" not in src, "续费引导不得含二维码"
    assert not re.search(r"<a\s[^>]*href=\"https?://", src, re.I), "不得引入外部链接"
    assert not re.search(r"<img\b", src, re.I), "不得贴图（含收款码）"
    assert not re.search(r"<button[^>]*>[^<]*(购买|支付)", src), "不得出现收银台暗示按钮"


# ---------------------------------------------------------------------------
# 11. 色语义复用既有 CSS 变量
# ---------------------------------------------------------------------------

def test_11_tier_colors_reuse_existing_css_vars():
    src = page_src()
    assert ".expiry.neutral" in src and "var(--color-ok)" in src
    assert ".expiry.warn" in src and "var(--color-warn)" in src
    assert ".expiry.severe" in src and "var(--color-err)" in src
    # 不引入外部资源
    assert not re.search(r"<link[^>]*href=\"https?://", src, re.I), "不得引入 CDN 样式"
    assert src.count("<script src=") == 1, "只允许既有的 nav.js 一个外链脚本"
    assert "/static/nav.js" in src and "/static/tokens.css" in src


# ---------------------------------------------------------------------------
# 12. 到期后失去什么：写清三类推送，但不写额度数字
# ---------------------------------------------------------------------------

def test_12_expiry_copy_lists_stopped_capabilities():
    src = page_src()
    assert "到期后：盯盘推送、预警推送、新闻推送将停止" in src, "缺到期后果文案"
    # 不得写具体功能额度数字（额度口径未定）：文案里不出现「每天 N 条」式表述
    assert not re.search(r"每天\s*\d+\s*[条次]", src), "不得写功能额度数字"


# ---------------------------------------------------------------------------
# 13. 「已到期」与「今天到期」必须分开
# ---------------------------------------------------------------------------

def test_13_expired_and_today_are_distinct():
    src = page_src()
    m = re.search(r"function expiryState\(.*?\n\}", src, re.S)
    assert m, "expiryState 结构异常"
    body = m.group(0)
    # 两个分支各自独立 return，不是同一分支里二选一拼字符串
    assert body.count("head:'已到期'") == 1, "缺「已到期」独立分支"
    assert body.count("head:'今天到期'") == 1, "缺「今天到期」独立分支"
    expired_at = body.index("head:'已到期'")
    today_at = body.index("head:'今天到期'")
    assert expired_at < today_at, "过期判定应早于 0 天判定（否则过期会被误显示成今天到期）"
    # 已过期既可由 status 判定，也可由剩余天数 < 0 判定
    assert "st==='expired'||passed" in body, "已过期判定应同时覆盖 status 与到期时点"


# ---------------------------------------------------------------------------
# 14. 未知状态原样透出，不映射成正常
# ---------------------------------------------------------------------------

def test_14_unknown_status_not_mapped_to_normal():
    src = page_src()
    assert "未知状态" in src, "缺「未知状态」标注"
    assert "st!=='active'&&st!=='expired'" in src, "缺未知状态分支"
    assert "不在已知状态（active / expired）内" in src, "未知状态分支应写清已知状态取值"
    # 状态卡的 ok 档只能在 active 时命中
    assert "const known=(st==='active'||st==='expired');" in src, \
        "状态卡应先判已知状态再上色，未知值不得落 ok"
