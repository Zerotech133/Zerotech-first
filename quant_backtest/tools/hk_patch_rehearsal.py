# -*- coding: utf-8 -*-
"""HK 方案乙对拍预演脚本（hk-patch-dev，2026-10-18）。

只读预演：本脚本不修改 xq_classify.py 与任何生产文件；生产库只以
sqlite3 file:...?mode=ro URI 打开（零写连接）。亦不 import xq_classify
——采用「源码摘录法」：RE_HK v1.6 两行 lookahead 从源码 :161-165 原样
摘录（已逐字符核对），extract 镜像逻辑摘自 :240-264。选型说明：
模块级审计确认 import xq_classify 无副作用（模块级仅常量/函数定义，
main 有 __name__ 守卫），但预演以零依赖为稳妥优先，故不 import。

对拍两版逻辑：
  v1.6  = HEAD 现行为（后缀排除集 [元块万亿手点关附]，紧邻一位）
  乙字面 = 实施指令字面式：lookahead 改 (?!\\s*(?:整数)?[关附])
           （注意：该写法替换整个原 lookahead，会移除 元/块/万/手/点 排除位）
  乙并集 = 保留原集合、仅对 关/附 允许「整数」插入：
           (?!\\s*(?:[元块万亿手点]|(?:整数)?[关附]))

主对拍表按指令字面式出列；乙并集另列一节对照，用于明晨实施微调建议。
"""

import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DB_PATH = PROJECT / "data" / "xueqiu" / "xueqiu_info.db"

# ── 源码摘录区（xq_classify.py:161-173, 191-198, 240-264 镜像，勿手改语义）──

RE_A = re.compile(r"(?<![0-9.%])([0-9]{6})(?![0-9])(?!\s*[元块])")

RE_HK_V16 = re.compile(
    r"(?:港股|HK:)\s*(?<![0-9])(?P<pfx>[0-9]{5})(?![0-9])"
    r"|(?<![0-9.])(?P<sfx>[0-9]{5})(?![0-9])\.?[Hh][Kk](?![A-Za-z0-9])"
    r"|(?<![0-9.\-])(?P<bare>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*[元块万亿手点关附])"
    r"|[(（$](?P<ct>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*[元块万亿手点关附])"
)

# 方案乙字面式（实施指令原文）：(?!\s*(?:整数)?[关附]) 替换 :164-165 两处后缀 lookahead
RE_HK_B_LITERAL = re.compile(
    r"(?:港股|HK:)\s*(?<![0-9])(?P<pfx>[0-9]{5})(?![0-9])"
    r"|(?<![0-9.])(?P<sfx>[0-9]{5})(?![0-9])\.?[Hh][Kk](?![A-Za-z0-9])"
    r"|(?<![0-9.\-])(?P<bare>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*(?:整数)?[关附])"
    r"|[(（$](?P<ct>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*(?:整数)?[关附])"
)

# 方案乙并集式：原排除集保留，关/附 位允许「整数」插入隔断（仍同属一个负向前瞻）
RE_HK_B_UNION = re.compile(
    r"(?:港股|HK:)\s*(?<![0-9])(?P<pfx>[0-9]{5})(?![0-9])"
    r"|(?<![0-9.])(?P<sfx>[0-9]{5})(?![0-9])\.?[Hh][Kk](?![A-Za-z0-9])"
    r"|(?<![0-9.\-])(?P<bare>[0-9]{5})(?![0-9\-])(?![.．][0-9])"
    r"(?!\s*(?:[元块万亿手点]|(?:整数)?[关附]))"
    r"|[(（$](?P<ct>[0-9]{5})(?![0-9\-])(?![.．][0-9])"
    r"(?!\s*(?:[元块万亿手点]|(?:整数)?[关附]))"
)

_HK_CONTEXT_RE = re.compile(
    r"港股|港交所|恒指|恒生指数|恒生科技|国企指数|红筹|(?<![A-Za-z])[Hh][Kk](?![A-Za-z])"
)
_ADJ_NOISE = set("了的到至达约近超破剩付花收给缴补返省欠赔是价净均倍赢输")
RE_FUND = re.compile(
    r"([0-9]{6})(?:\.OF| 基金)|((?:沪深300|中证500|中证1000|创业板|科创50|纳斯达克|"
    r"标普500|恒生科技|红利|白酒|医药|半导体|新能源|军工|黄金|债券|货币)(?:ETF|基金|指数|LOF|联接))"
)
_RE_NOISE = re.compile(r"[<>]|br/|[的了这那哦吧呀]")
_CODE_SEGS = (
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "302",
    "430",
    "920",
    "83",
    "87",
    "50",
    "51",
    "52",
    "56",
    "58",
    "15",
    "16",
)


def _clean_ticker(code):
    if not code or len(code) > 10 or _RE_NOISE.search(code):
        return False
    if code.isdigit() and len(code) == 6:
        return code.startswith(_CODE_SEGS)
    return True


def extract_tickers(text, hk_re):
    """镜像 xq_classify.py:240-264，仅 RE_HK 参数化以承载两版 lookahead。"""
    out = []
    for m in RE_A.findall(text):
        code = m if isinstance(m, str) else m[0]
        if code not in out and _clean_ticker(code):
            out.append(code)
    hk_ctx = bool(_HK_CONTEXT_RE.search(text))
    for m in hk_re.finditer(text):
        code = m.group("pfx") or m.group("sfx") or m.group("ct")
        if not code:
            code = m.group("bare")
            if not hk_ctx:
                prev = text[m.start() - 1] if m.start() > 0 else ""
                if prev not in _ADJ_NOISE and "\u4e00" <= prev <= "\u9fff":
                    pass
                else:
                    continue
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
            out.append(code)
    for m in RE_FUND.findall(text):
        code = m[0] if m[0] else m[1]
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
            out.append(code)
    return out[:8]


# ── 生产库只读残留样本（task 指定 id 260113733 / 139380484）──


def fetch_residual_samples():
    """只读捞 20000 残留原文；任何失败都不抛——返回 (rows, note)。"""
    note = ""
    rows = []
    if not DB_PATH.exists():
        return [], "DB 不存在，按清单文档描述替代"
    try:
        con = sqlite3.connect(
            "file:%s?mode=ro" % DB_PATH.as_posix(), uri=True, timeout=5
        )
        con.execute("PRAGMA busy_timeout=5000")
        try:
            rows = con.execute(
                "SELECT status_id, title, desc, created_at FROM xq_status "
                "WHERE status_id IN (?, ?)",
                ("260113733", "139380484"),
            ).fetchall()
        finally:
            con.close()
        if not rows:
            note = "只读查询成功但两 id 未命中（可能非本库/已清理）"
    except sqlite3.Error as e:
        note = "只读打开失败(%s)，按清单文档描述替代" % type(e).__name__
    return rows, note


def main():
    residual, res_note = fetch_residual_samples()

    samples = []  # (编号, 样本来源标签, title, desc, 期望描述, extract文本或None)

    # ① 核心修复形：「整数关口」隔断（v1.6 误收 → 方案乙应排除）
    samples.append(
        (
            "①1",
            "新发现漏网（清单§①）",
            "恒指下跌1%，回撤至31000整数关口附近",
            "",
            "[]（「整数关口」属点位语境，103804289 挂的断言）",
        )
    )
    samples.append(
        (
            "①2",
            "变体自造",
            "恒指站上31000整数关口",
            "",
            "[]（同①1，站上/回撤两个动词位都验）",
        )
    )

    # ② 紧邻「关」形：两版都应排除（v1.6 已修，回归看乙不回退）
    samples.append(
        (
            "②1",
            "tests 悬置改动",
            "港股这边，指数站上 20000 关口",
            "",
            "[]（v1.6 翻转用例，\\s* 隔空位）",
        )
    )
    samples.append(
        ("②2", "变体自造", "恒指回撤至20000关口", "", "[]（紧邻关，无空位）")
    )

    # ③ lead 排除形（动词引导 + 关口后缀）与无后缀对照
    samples.append(
        (
            "③1",
            "tests 悬置改动",
            "港股复盘：腾讯重挫 恒指险守31000关口 美联储今晨加息",
            "",
            "[]（103804289 标题形）",
        )
    )
    samples.append(
        (
            "③2",
            "行为对照（无后缀）",
            "港股这边，指数站上20000",
            "",
            "两版一致=收（无排除后缀时既有放行行为，方案乙不引入新变化）",
        )
    )

    # ④ A 股语境分支边界：HK 分支不应被触发
    samples.append(
        (
            "④1",
            "分支边界",
            "沪指站上3300关口",
            "",
            "[]（4 位数字，RE_HK/RE_A 均不应命中）",
        )
    )
    samples.append(
        (
            "④2",
            "分支边界对照",
            "沪指站上33000关口",
            "",
            "[]预期按乙（关排除）；注：无港股语境但紧贴名通道存在，两版一致性是看点",
        )
    )

    # ⑤ 正常 HK 个股价位形：红线回归
    samples.append(
        (
            "⑤1",
            "tests 悬置改动",
            "港股焦点：腾讯00700 回购注销",
            "",
            "['00700']（紧贴名+语境共现，v1.6 主力形态不回退）",
        )
    )
    samples.append(
        (
            "⑤2",
            "tests 悬置改动",
            "$腾讯控股(00700)$ 发布财报",
            "",
            "['00700']（cashtag 通道）",
        )
    )
    samples.append(
        ("⑤3", "tests 悬置改动", "00700.HK 创历史新高", "", "['00700']（.HK 后缀通道）")
    )
    samples.append(
        (
            "⑤4",
            "任务指定价位形",
            "腾讯420港元上方",
            "",
            "注：420 非 5 位，现行三通道本就不提 HK 码，两版一致=[]（非回归）",
        )
    )
    samples.append(
        ("⑤5", "⑤4 补全形", "腾讯00700 420港元上方", "", "['00700']（码收、价位不收）")
    )
    samples.append(
        ("⑤6", "tests 悬置改动", "港股02331 中报超预期", "", "['02331']（前缀通道）")
    )

    # ⑥ 生产库 20000 残留原文（只读，全量 title+desc 参与 extract，不截断）
    for sid, title, desc, created in residual:
        full = (title or "") + " " + (desc or "")
        samples.append(
            (
                "⑥",
                "生产库残留 status_id=%s(%s)" % (sid, created[:10]),
                (title or "")[:60],
                "(全文 %d 字参与提取；预览)%s" % (len(full), (desc or "")[:60]),
                "对拍实证：20000 残留形能否被乙出清",
                full,
            )
        )
    if not residual:
        samples.append(
            (
                "⑥",
                "清单§2.1 描述替代",
                "（存量池画像 ticker=20000，2023-09-02，未打标行）",
                "",
                "库只读捞取未命中：%s" % res_note,
                None,
            )
        )
    samples.append(
        (
            "⑥x",
            "⑥同形自造（港币后缀）",
            "入金20000港币以上再送300港币",
            "",
            "对拍实证：「币」不在后缀排除集，紧贴名通道「金20000」形两版行为",
            "入金20000港币以上再送300港币",
        )
    )

    # ⑦ 「附」字后缀形
    samples.append(
        (
            "⑦1",
            "tests 悬置改动",
            "港股上周走势再度先扬后抑，恒指退守到关键点位30500附近",
            "",
            "[]（107875256）",
        )
    )
    samples.append(
        (
            "⑦2",
            "tests 悬置改动",
            "2019年8月6日恒指重新回到25500附近，pe約為12",
            "",
            "[]（130710432）",
        )
    )
    samples.append(
        (
            "⑦3",
            "「整数」插入变体",
            "恒指回撤至30500整数附近",
            "",
            "[]（乙并集覆盖；乙字面亦覆盖——附在 (?:整数)? 后）",
        )
    )

    # ⑧ 附加边界：元/块/万/手/点 排除位（乙字面的潜在回归面）
    samples.append(
        (
            "⑧1",
            "tests 悬置改动（144660897）",
            "今日港股表现尤为亮眼，恒生指数盘中一度大涨4.42%，触及22669.04点。内房股集体大涨",
            "",
            "[]（小数前瞻堵，两版皆应过）",
        )
    )
    samples.append(
        (
            "⑧2",
            "tests 悬置改动（140221805）",
            "午后翻绿。恒生指数收盘报26312.63点，跌幅0.52%",
            "",
            "[]（小数前瞻堵，两版皆应过）",
        )
    )
    samples.append(
        (
            "⑧3",
            "v1.3 既有（144250900 缩样）",
            "$恒生指数(HKHSI)$ 1、港股现在有多低估？底部恒指10676点，涨到现在23200点，如今在18000-20000之间。",
            "",
            "[]（「点」后缀排除——乙字面的重点风险位）",
        )
    )
    samples.append(
        (
            "⑧4",
            "tests 悬置改动（112510264）",
            "北京地区住房公积金累计归集住房公积金11116.27亿元，首次突破万亿大关",
            "",
            "[]（紧贴名+小数金额双堵）",
        )
    )

    # 未显式给 extract 文本的样本 = title + " " + desc
    samples = [s if len(s) == 6 else s + (None,) for s in samples]

    versions = [
        ("v1.6现行为", RE_HK_V16),
        ("乙字面式", RE_HK_B_LITERAL),
        ("乙并集式", RE_HK_B_UNION),
    ]

    print("=" * 100)
    print(
        "HK 方案乙对拍预演  %s"
        % (
            "库残留样本 %d 条（%s）" % (len(residual), res_note)
            if res_note or residual
            else ""
        )
    )
    print("=" * 100)
    header = "%-4s %-26s %-28s | %-16s | %-16s | %-16s" % (
        "编号",
        "来源",
        "样本",
        "v1.6现行为",
        "乙字面式",
        "乙并集式",
    )
    print(header)
    print("-" * 100)

    rows_out = []
    for no, src, title, desc, expect, ext_text in samples:
        text = ext_text if ext_text is not None else (title or "") + " " + (desc or "")
        res = {name: extract_tickers(text, rx) for name, rx in versions}
        rows_out.append((no, src, title, desc, res, expect))
        print(
            "%-4s %-26s %-28s | %-16s | %-16s | %-16s"
            % (
                no,
                src[:26],
                (title[:26] if title else "(空)"),
                str(res["v1.6现行为"])[:16],
                str(res["乙字面式"])[:16],
                str(res["乙并集式"])[:16],
            )
        )
        if desc:
            print("%73s desc: %s" % ("", desc[:60]))
    print("-" * 100)

    print("\n逐样本期望对照（正文）：")
    for no, src, title, desc, res, expect in rows_out:
        print("  %s [%s] 期望: %s" % (no, src, expect))
        print(
            "       v1.6=%s  乙字面=%s  乙并集=%s"
            % (res["v1.6现行为"], res["乙字面式"], res["乙并集式"])
        )

    # 修复判定汇总
    print("\n结论要点：")
    core_fixed = all(
        res["乙字面式"] == [] and res["乙并集式"] == []
        for no, src, t, d, res, e in rows_out
        if no.startswith("①")
    )
    print(
        "  ①「整数关口」核心修复：乙字面%s 乙并集%s"
        % (
            "已修复"
            if all(
                r["乙字面式"] == []
                for n, s, t, d, r, e in rows_out
                if n.startswith("①")
            )
            else "未修复",
            "已修复" if core_fixed else "未修复",
        )
    )
    p_reg = [
        (no, res["乙字面式"])
        for no, src, t, d, res, e in rows_out
        if no.startswith("⑧") and res["v1.6现行为"] == [] and res["乙字面式"] != []
    ]
    print(
        "  ⑧「点/元」排除位回归（乙字面引入的新副作用）：%s"
        % ("; ".join("%s→%s" % (n, r) for n, r in p_reg) if p_reg else "无")
    )
    p_reg_u = [
        (no, res["乙并集式"])
        for no, src, t, d, res, e in rows_out
        if no.startswith("⑧") and res["v1.6现行为"] == [] and res["乙并集式"] != []
    ]
    print(
        "  ⑧ 乙并集式同类回归：无"
        if not p_reg_u
        else "  ⑧ 乙并集式同类回归：%s" % p_reg_u
    )
    legit = all(
        res["乙并集式"] == res["v1.6现行为"]
        for no, src, t, d, res, e in rows_out
        if no.startswith("⑤")
    )
    print(
        "  ⑤ 红线回归（乙并集式与 v1.6 完全一致）：%s"
        % ("是" if legit else "否——需人工核对")
    )
    for no, src, t, d, res, e in rows_out:
        if no.startswith("⑥"):
            print(
                "  ⑥ 残留 %s: v1.6=%s 乙字面=%s 乙并集=%s"
                % (
                    src.split("(")[-1].rstrip(")"),
                    res["v1.6现行为"],
                    res["乙字面式"],
                    res["乙并集式"],
                )
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
