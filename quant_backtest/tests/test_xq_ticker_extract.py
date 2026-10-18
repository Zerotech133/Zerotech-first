# -*- coding: utf-8 -*-
"""标的提取器 v1.2 契约测试（A 股代码提取升级，2026-10-18 老田拍板）。

三个核心修复的回归锚：
  ① 中文紧贴不再漏提（v1.1 的 \\b 病根：中文与数字同为 \\w 无词边界）
  ② 6 位码段白名单（232020 等 23 开头噪音段 52 行实证污染出局）
  ③ 金额语义排除（「150000 元」不收）
跑法：pytest tests/test_xq_ticker_extract.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xq_classify import extract_tickers, _TICKER_VERSION


def test_version_is_v16():
    # v1.6：港股裸 5 位点位语境硬排除扩展（xq-quality-dev 刀二实证驱动）。
    # 版本谱系：v1.4 已被 --retry-degraded 语义占用、v1.5 已被速率旋钮占用，
    # ticker 谱系自 v1.3 直接跳 v1.6。
    assert _TICKER_VERSION == "v1.6"


# ── ① 中文紧贴挖掘（v1.1 漏提病根） ──────────────────────────

def test_chinese_adjacent_code():
    assert extract_tickers("贵州茅台600519，反弹目标位", "") == ["600519"]

def test_chinese_adjacent_code_desc():
    assert extract_tickers("", "宁德时代300750的产能利用率创新高") == ["300750"]

def test_sh_prefix_untouched():
    assert extract_tickers("SH600519 突破年线", "") == ["600519"]

def test_dot_suffix_format():
    assert extract_tickers("600519.SH 尾盘拉升", "") == ["600519"]


# ── ② 6 位码段白名单 ─────────────────────────────────────────

def test_noise_232020_rejected():
    assert extract_tickers("活动编号 232020 已登记", "") == []

def test_year_month_rejected():
    assert extract_tickers("数据截至 202410 更新", "") == []

def test_random_six_digits_rejected():
    assert extract_tickers("订单号 999888 已发货", "") == []

def test_all_major_segments_accepted():
    for code in ("600519", "601318", "603288", "688981",      # 沪
                 "000001", "002594", "300750",                 # 深/创业
                 "159915", "510300"):                          # ETF
        assert extract_tickers("代码 %s 走强" % code, "") == [code], code


# ── ③ 金额语义排除 ───────────────────────────────────────────

def test_amount_yuan_rejected():
    assert extract_tickers("需要资金 150000 元起步", "") == []

def test_amount_kuai_rejected():
    assert extract_tickers("亏了150000块", "") == []


# ── 保护性回归（既有行为不破坏） ─────────────────────────────

def test_hk_5digit_kept():
    assert extract_tickers("腾讯00700 业绩超预期", "") == ["00700"]

def test_decimal_protection():
    # 小数点后的 6 位不误提（净值/百分比防截取）
    assert extract_tickers("净值1.600519 保持", "") == []

def test_multi_codes_dedup_order():
    assert extract_tickers("600519 和 300750 谁强？我选600519", "") == ["600519", "300750"]

def test_fund_word_kept():
    assert extract_tickers("红利ETF 值得配置吗", "") == ["红利ETF"]

def test_html_noise_rejected():
    assert extract_tickers("债券B<br/>1、没有标的", "") == []


# ── ④ 港股裸 5 位三通道判定（v1.3，144250900 实证驱动） ──────

def test_amount_form_5digit_rejected():
    # 金额形 5 位 + 语境词也救不了点位/金额后缀硬排除（144250900 实证形态）
    assert extract_tickers("港股行情热议，赚了 18000 元落袋", "") == []
    assert extract_tickers("港股账户亏了 20000 块", "") == []

def test_hk_suffix_direct_hit():
    # .HK 后缀直接命中（无港股语境词也不受共现限制——.HK 本身即语境）
    assert extract_tickers("00700.HK 创历史新高", "") == ["00700"]

def test_hk_prefix_direct_hit():
    # 「港股」紧邻前缀直接命中
    assert extract_tickers("港股02331 中报超预期", "") == ["02331"]
    assert extract_tickers("HK:00700 回购注销", "") == ["00700"]

def test_chinese_adjacent_hk_name():
    # 中文紧贴名形（无语境词）：「讯」字紧贴 00700，前置字非虚词 → 收
    assert extract_tickers("美团03690 订单强劲", "") == ["03690"]

def test_xueqiu_cashtag_direct_hit():
    # 雪球 cashtag 形态 $腾讯控股(00700)$：代码被 ( / $ 紧邻包裹，直击不受共现限制
    # （回归锚 171914290/150923887：v1.3 首版 bare 通道误杀此主力形态）
    text = '<a href="https://xueqiu.com/S/00700">$腾讯控股(00700)$</a> 发布财报'
    assert extract_tickers(text, "") == ["00700"]
    assert extract_tickers("最近两个月，$阅文集团(00772)$经历高层换血", "") == ["00772"]

def test_contextless_bare_5digit_rejected():
    # 孤立裸 5 位、无港股语境、非紧贴名 → 拒
    assert extract_tickers("中签 20000 股，运气爆棚", "") == []
    assert extract_tickers("活动编号 10676 已登记", "") == []

def test_context_cooccurrence_hit():
    # v1.6 翻转（原 v1.3 用例锚死了误收行为）：「站上 20000 关口」是点位语境
    # 而非代码——「关」入后缀排除集后不再收（lead 观察项实证落锤 103804289 同款）
    assert extract_tickers("港股这边，指数站上 20000 关口", "") == []

def test_hk_contextless_verb_adjacent_rejected():
    # 动词+数字紧贴（「赚了18000」）不算中文紧贴名，且无语境 → 拒
    assert extract_tickers("赚了18000，请客吃饭", "") == []

def test_hk_index_points_and_range_rejected():
    # 144250900 回归锚：恒指点位（点后缀）+ 数字区间（连字符双界）全出局，
    # 即便文中满是港股/恒指/港股通语境词
    text = ("$恒生指数(HKHSI)$ 1、港股现在有多低估？底部恒指10676点，"
            "涨到现在23200点，如今在18000-20000之间。")
    assert extract_tickers(text, "") == []


# ── ⑤ 港股点位语境硬排除扩展（v1.6，xq-quality-dev 刀二实证驱动，
#      全量 19991 行只读扫描实证 103 条「收码∧点位语境」共现） ──

def test_decimal_index_level_rejected():
    # 144660897 实证：「触及22669.04点」——v1.3 的「点」后缀排除被小数部分
    # （.04）隔断而失效；裸/cashtag 通道加「小数前瞻」堵死所有 NNNNN.dd 形
    text = "今日港股表现尤为亮眼，恒生指数盘中一度大涨4.42%，触及22669.04点。内房股集体大涨"
    assert extract_tickers(text, "") == []
    # 140221805 同款：「恒生指数收盘报26312.63点」
    assert extract_tickers("午后翻绿。恒生指数收盘报26312.63点，跌幅0.52%", "") == []

def test_decimal_amount_adjacent_rejected():
    # 112510264 实证：「住房公积金11116.27亿元」——紧贴名通道把「金11116」
    # 当公司名紧贴误收；小数前瞻 + 「.27亿元」金额语义双堵
    text = "北京地区住房公积金累计归集住房公积金11116.27亿元，首次突破万亿大关"
    assert extract_tickers(text, "") == []

def test_guankou_suffix_rejected():
    # 103804289 实证：「恒指险守31000关口」「回撤至31000整数关口附近」
    # ——后缀排除集补「关」（关口/大关）
    assert extract_tickers("港股复盘：腾讯重挫 恒指险守31000关口 美联储今晨加息", "") == []
    assert extract_tickers("恒指下跌1%，回撤至31000整数关口附近", "") == []

def test_fujin_suffix_rejected():
    # 107875256/130710432 实证：「恒指退守到关键点位30500附近」「回到25500附近」
    # ——后缀排除集补「附」（附近）
    assert extract_tickers("港股上周走势再度先扬后抑，恒指退守到关键点位30500附近", "") == []
    assert extract_tickers("2019年8月6日恒指重新回到25500附近，pe約為12", "") == []

def test_legit_forms_survive_v16():
    # v1.6 红线回归：显式意图通道（.HK 后缀/港股前缀/cashtag）与紧贴名形态零影响
    assert extract_tickers("00700.HK 创历史新高", "") == ["00700"]
    assert extract_tickers("港股02331 中报超预期", "") == ["02331"]
    assert extract_tickers("$腾讯控股(00700)$ 发布财报", "") == ["00700"]
    assert extract_tickers("腾讯00700 业绩超预期", "") == ["00700"]
    assert extract_tickers("美团03690 订单强劲", "") == ["03690"]
    # 紧贴名 + 语境共现仍放行（v1.3 主力形态不回退）
    assert extract_tickers("港股焦点：腾讯00700 回购注销", "") == ["00700"]


# ── ⑥ 量词后缀「笔/币」（v1.6-b3，2026-10-19，20000×2 类残留实证） ──

def test_bi_counter_suffix_rejected():
    # 「成交2万笔」形：展开位「20000笔」不得当港股码捕获——后缀排除集补「笔」
    assert extract_tickers("成交2万笔，人气爆棚", "") == []
    assert extract_tickers("港股成交 20000笔，人气爆棚", "") == []

def test_bi_coin_suffix_rejected():
    # 「链上3万币」形：展开位「30000币」同堵——后缀排除集补「币」
    # （「上」紧贴名通道不放行，唯有「币」后缀硬排除兜底）
    assert extract_tickers("链上3万币 铭文火热", "") == []
    assert extract_tickers("链上30000币 铭文火热", "") == []
