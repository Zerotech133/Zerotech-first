# -*- coding: utf-8 -*-
"""tier3 规则化人审判定引擎（从 tier2 580 条显式判定沉淀，2026-11-08）

动作口径（同 tier2）：
- pass   ：claim 承载完整、自洽的知识内容，可独立阅读
- revise ：有内容但需修订（fragment/single_line/garbled_partial/time_sensitive/hype）
- reject ：纯噪声（cta_only/promo/lyrics/off_domain/garbled）

用法：
    python rules_judge.py --backtest          # 对 tier2 dump 回测，输出一致率矩阵
    python rules_judge.py --dump tier3_T1_review_dump.jsonl --out tier3_T1_verdicts.jsonl
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# ---------- 词表 ----------
FIN_WORDS = [
    "均线", "K线", "MACD", "KDJ", "BOLL", "量能", "成交量", "涨停", "跌停", "打板",
    "龙头", "筹码", "背离", "金叉", "死叉", "回撤", "止损", "止盈", "仓位", "趋势",
    "波段", "分时", "竞价", "可转债", "ETF", "期权", "期货", "量化", "回测", "策略",
    "因子", "模型", "参数", "函数", "变量", "代码", "脚本", "Python", "Excel", "公式",
    "指标", "API", "接口", "数据", "爬虫", "QMT", "通达信", "同花顺", "东方财富", "雪球",
    "主力", "散户", "大盘", "指数", "板块", "个股", "股票", "股价", "市盈率", "市净率",
    "财报", "估值", "分红", "复权", "缠论", "中枢", "背驰", "级别", "走势", "买卖点",
    "补仓", "空仓", "满仓", "加仓", "减仓", "抄底", "逃顶", "震荡", "突破", "支撑",
    "压力", "箱体", "缺口", "阳线", "阴线", "十字星", "换手", "量比", "市值", "流通",
    "解禁", "龙虎榜", "北向", "融资", "融券", "杠杆", "牛市", "熊市", "开户", "券商",
    "复盘", "选股", "形态", "底部", "顶部", "信号", "进场", "出场", "多单", "空单",
    "做多", "做空", "开仓", "平仓", "持仓", "胜率", "盈亏比", "回撤率", "年化", "收益",
    "风险", "本金", "证券", "A股", "美股", "港股", "交易", "买卖", "买点", "卖点",
    "SAR", "RSI", "CCI", "ROC", "VOL", "EMA", "SMA", "WMA", "布林", " candles",
]
FIN_RE = re.compile("|".join(re.escape(w) for w in FIN_WORDS), re.IGNORECASE)

CTA_WORDS = [
    "关注", "点赞", "订阅", "评论区", "领取", "私信", "后台", "小黄车", "小房车",
    "转发", "下期见", "不见不散", "扫码", "加我", "进群", "课程咨询", "点击下方",
    "主页", "粉丝群", "直播间", "蹲一个", "扣个", "评论区告诉我",
]
CTA_RE = re.compile("|".join(CTA_WORDS))

LYRIC_WORDS = [
    "不负年少", "风沙", "白茫茫", "寻找着", "岁岁月月", "亲爱的你", "爱过我",
    "思念", "流浪", "远方的朋友", "一生所爱", "陪你走过", "白了少年头",
]
LYRIC_RE = re.compile("|".join(LYRIC_WORDS))

CHICKEN_WORDS = [
    "人生", "命运", "坚持梦想", "心态决定", "感恩", "相信自己", "努力奋斗",
    "致自己", "人生就像", "岁月", "熬出头", "熬年头",
]
CHICKEN_RE = re.compile("|".join(CHICKEN_WORDS))

TIME_WORDS = re.compile(r"下周[一二三四五六日天]?|明天|今天|本周|昨日|昨天|点位|预测|推演|今晚|盘后预期")

HYPE_WORDS = re.compile(r"百分百|稳赚|必涨|翻倍|包赚|稳赚不赔|暴涨|涨停敢死队|绝对盈利")

GARBLE_FILL = re.compile(r"[啊哦呃嗯嘛呢吧哈呀咯哟欸]")

# ---------- 判定 ----------
def judge(claim: str, cls: str) -> tuple:
    """返回 (action, reason)。规则顺序即优先级，先命中先判。"""
    c = claim.strip()
    n = len(c)
    if n == 0:
        return ("reject", "empty")

    # --- 结构性乱码：重复字异常 ---
    if n >= 8:
        top = Counter(c).most_common(1)[0][1]
        if top / n > 0.30:
            return ("reject", "garbled")
    # 语气填充词密度（啊×N 重复）
    fill = len(GARBLE_FILL.findall(c))
    if n >= 10 and fill / n > 0.35:
        return ("reject", "garbled")

    has_fin = bool(FIN_RE.search(c))
    cta_hits = CTA_RE.findall(c)
    n_cta = len(set(cta_hits))

    # --- CTA/导流 ---
    if n_cta >= 3 and n < 200 and not has_fin:
        return ("reject", "promo")
    if n_cta >= 1 and n < 50 and not has_fin:
        return ("reject", "cta_only")
    if n_cta >= 1 and n < 30:
        return ("reject", "cta_only")

    # --- 歌词/鸡汤/域外 ---
    if not has_fin:
        if LYRIC_RE.search(c) and n < 80:
            return ("reject", "lyrics")
        if CHICKEN_RE.search(c) and n < 60:
            return ("reject", "off_domain")
        if n < 45 and not re.search(r"[0-9%．.]", c):
            # 无财经词+无数字+短文本：大概率闲聊/歌词
            return ("reject", "off_domain")

    # --- 夸大 ---
    if HYPE_WORDS.search(c):
        if n < 60:
            return ("reject", "hype")
        return ("revise", "hype")

    # --- 时效性 ---
    if TIME_WORDS.search(c) and n < 150:
        return ("revise", "time_sensitive")

    # --- 片段/单句 ---
    if n < 18:
        return ("revise", "single_line")
    sent_end = c.count("。") + c.count("？") + c.count("；") + c.count("！")
    if n < 60 and sent_end <= 1 and not re.search(r"[0-9]\.[0-9]|[0-9]%", c):
        return ("revise", "single_line")
    if n < 35 and sent_end <= 1 and not has_fin:
        return ("revise", "fragment")

    # --- 中英混杂异常（garbled_partial）---
    if n >= 30:
        letters = len(re.findall(r"[A-Za-z]", c))
        if letters / n > 0.5:
            return ("revise", "garbled_partial")

    return ("pass", "ok")


# ---------- 回测 ----------
def backtest():
    PREFIX = "learn_视频号学习_全部文案_20261108_"
    dump = [json.loads(l) for l in Path(r"C:/项目/_tmp_ci9/tier2_review_dump.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    manual = {}
    for l in Path(r"C:/项目/_tmp_ci9/tier2_review_verdicts.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            v = json.loads(l)
            manual[v["unit_id"]] = v["action"]
    # 孪生行：同 id 人工结论一致，直接用
    matrix = defaultdict(Counter)  # manual -> rule -> n
    detail_mismatch = []
    for r in dump:
        uid = r["unit_id"]
        if uid not in manual:
            continue
        m = manual[uid]
        rule_action, rule_reason = judge(r["claim"], r.get("cls", ""))
        matrix[m][rule_action] += 1
        if m != rule_action:
            detail_mismatch.append((m, rule_action, rule_reason, uid[len(PREFIX):], r["claim"][:60]))
    total = sum(sum(c.values()) for c in matrix.values())
    agree = sum(matrix[m][m] for m in matrix)
    print(f"回测样本: {total}  动作一致率: {agree/total*100:.1f}%")
    print(f"{'人工\\规则':<10}{'pass':>8}{'revise':>8}{'reject':>8}")
    for m in ("pass", "revise", "reject"):
        c = matrix[m]
        print(f"{m:<10}{c['pass']:>8}{c['revise']:>8}{c['reject']:>8}   (人工小计 {sum(c.values())})")
    # 关键指标：pass 误杀（人工 pass 被规则判非 pass）
    mp = matrix["pass"]
    false_kill = mp["revise"] + mp["reject"]
    print(f"\npass 误杀率: {false_kill}/{sum(mp.values())} = {false_kill/sum(mp.values())*100:.1f}%")
    print(f"reject 召回: {matrix['reject']['reject']}/{sum(matrix['reject'].values())}")
    print(f"revise 召回: {matrix['revise']['revise']}/{sum(matrix['revise'].values())}")
    print(f"\n不一致明细（前 25 条）:")
    for m, ra, rr, suf, text in detail_mismatch[:25]:
        print(f"  [{m}→{ra}/{rr}] {suf}  {text}")


def run_dump(dump_path: str, out_path: str):
    rows = [json.loads(l) for l in Path(dump_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    verdicts, seen = [], {}
    for r in rows:
        action, reason = judge(r.get("claim", ""), r.get("cls", ""))
        v = {"unit_id": r["unit_id"], "action": action, "reason": reason}
        seen[v["unit_id"]] = v  # 孪生去重
    for v in seen.values():
        verdicts.append(v)
    with Path(out_path).open("w", encoding="utf-8", newline="\n") as f:
        for v in verdicts:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    c = Counter(v["action"] for v in verdicts)
    print(f"dump 行: {len(rows)}  唯一 unit_id: {len(seen)}")
    print(f"判定分布: {dict(c)}  通过率 {c['pass']/len(seen)*100:.1f}%")
    print("理由码:", dict(Counter(v['reason'] for v in verdicts)))
    print(f"-> {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--dump")
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.backtest:
        backtest()
    elif args.dump and args.out:
        run_dump(args.dump, args.out)
    else:
        ap.error("需要 --backtest 或 --dump/--out")
