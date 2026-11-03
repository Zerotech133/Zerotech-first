# -*- coding: utf-8 -*-
"""G6 影子层：notice 公告 LLM 受控分类（qwen3:8b 本地 Ollama）。

背景（INTEL_RD_PLAN G6）：公告分类=event_graph.RULES 14 组关键词先命中先赢，
未命中 → tag=None → 图内 group="notice" 基线组 → H1 排除基线 → H1 样本仅 96
条（覆盖面不足）。本脚本对未命中公告做 LLM 结构化分类，**影子先行**：
  · 结果只落 data/semantica/notice_llm.parquet，不改 events.jsonl、
    不改 event_graph 规则、不写图——H1/DRIVE 口径零影响
  · 标签受控枚举：RULES 14 组原 tag（与 DRIVE group 对齐）+ 新增
    资产重组/对外投资/股权转让（bull，规则漏网的实质主题）+ 中性披露
    （neutral 兜底，程序性文件/例行披露——宁可中性不可错判）
  · --eval N：规则命中行分层抽样 LLM 复判，输出规则↔LLM 一致率
    （互检；达标后才谈接入 _seed_notice）

G6b 接入口径（2026-09-30 用户拍板方案①；2026-10-07 门闸改口径：
分歧人工抽检正确率→回填污染率≤5% 定案，含方向保护）：--backfill
把影子判定按 id 回填 events.jsonl——只补 bias=None 的 notice 行（规则
命中优先不动），LLM conf>=0.7 且非中性才回填；标题含反向词却判 bull
（终止/失败/取消/撤回）判定可疑不回填。下游 kg_build_p3_evt→
kg_build_m2 的 group=coalesce(tag,kind) 自动把新组（资产重组/对外投资/
股权转让）带进 DRIVE 实质组，kg_build_m2/kg_distill 零改动。回填只动
bias/tag/conf/reason，text 不变 → 向量矩阵无需重建。

用法：
  python kg_notice_llm.py --limit 200    # 小样试跑（含质量抽检输出）
  python kg_notice_llm.py --eval 500     # 规则命中行一致性评估
  python kg_notice_llm.py                # 全量未命中分类（断点续跑）
  python kg_notice_llm.py --backfill     # 判定新行 + 按 id 回填 events.jsonl
                                        # （G6b 正式接入：每日链 evt_build 后跑，
                                        #  Ollama 不可用则熔断跳过判定 exit 0 不阻断）
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVT_JSONL = os.path.join(HERE, "data", "semantica", "events.jsonl")
OUT_PARQUET = os.path.join(HERE, "data", "semantica", "notice_llm.parquet")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
LLM_CONF_MIN = 0.7  # 回填阈值（全量实证实质主题 conf 均 0.9+）
FAIL_STREAK_MAX = 10  # 连续失败熔断（Ollama 中途挂掉防雪崩）
# 方向保护（K260 定案，2026-10-07 人工判定 30 条发现）：标题含反向词
# 却判 bull 是 LLM 方向反转（如「终止收购」判成资产重组，样本 1/30），
# 回填时跳过——宁可保持未命中也不错误打进 bull 组
REVERSE_WORDS = ("终止", "失败", "取消", "撤回")

# 标签枚举：RULES 14 组（tag 名与 event_graph 完全一致，保证将来可无缝
# 接入 DRIVE group）+ 3 个规则漏网实质组 + 中性兜底
TAG_BIAS = {
    # bull（8 组，同 RULES）
    "业绩预增": "bull",
    "中标合同": "bull",
    "回购增持": "bull",
    "分红派息": "bull",
    "股权激励": "bull",
    "政府补助": "bull",
    "资产收购": "bull",
    "合作协议": "bull",
    # bear（6 组，同 RULES）
    "业绩预亏": "bear",
    "终止重组": "bear",
    "监管处罚": "bear",
    "诉讼冻结": "bear",
    "退市风险": "bear",
    "股东减持": "bear",
    # 新增（规则漏网的实质主题）
    "资产重组": "bull",  # 重组进展/筹划/停牌重组（非终止）
    "对外投资": "bull",  # 设立子公司/对外投资/增资扩产
    "股权转让": "bull",  # 股权挂牌转让/受让（A股重组预期语境）
    # 中性兜底
    "中性披露": "neutral",
}
# 模型偶发英文标签 → 归一化（只认 neutral；裸 bull/bear 无主题信息
# 不进 DRIVE group，走 fail 兜底）
TAG_ALIAS = {"neutral": "中性披露"}
PROMPT = (
    "你是A股公告分类器。对下面公告标题选唯一主题标签。\n"
    '标签枚举（|后为方向；"tag"的值必须是枚举中的中文标签原文）：\n'
    "bull：业绩预增 中标合同 回购增持 分红派息 股权激励 政府补助 "
    "资产收购 合作协议 资产重组 对外投资 股权转让\n"
    "bear：业绩预亏 终止重组 监管处罚 诉讼冻结 退市风险 股东减持\n"
    "neutral：中性披露（股东会文件/法律意见书/例行进展/现金管理/"
    "募集资金的常规操作等无实质价格信息的）\n"
    "判断要点：①新的计划/预披露/首次公告（减持预披露、新增诉讼、"
    "首次回购、重组筹划）属该主题实质标签；期限届满/实施完毕/实施情况"
    '/例行进展类选中性披露。②重组/收购/投资/转让等主题的"进展公告"'
    '仍属该主题标签，不是中性。③"股份质押延期购回/质押式回购"是质押'
    '管理非股份回购，选中性披露。④标题模糊或信息不足才选"中性披露"'
    "并给低conf；不确定时宁可选中性。\n"
    '只输出JSON，不要任何其他文字：{"tag":"中文标签","conf":0到1的小数}\n'
    "公告标题：%s"
)


def load_events():
    """events.jsonl → (未命中行, 规则命中行)。"""
    unhit, hit = [], []
    with open(EVT_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("kind") != "notice":
                continue
            row = {
                "id": e.get("id"),
                "text": e.get("text") or "",
                "ts": e.get("ts"),
                "tag_rule": e.get("tag"),
            }
            (hit if e.get("tag") else unhit).append(row)
    return unhit, hit


def judge(title, timeout=30):
    """单条 → {tag, bias, conf} 或 None（解析失败重试一次）。"""
    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": PROMPT % title[:120]}],
        "options": {"temperature": 0.1, "num_predict": 60},
    }
    for _ in range(2):
        try:
            r = requests.post(OLLAMA, json=body, timeout=timeout)
            r.raise_for_status()
            m = re.search(r"\{[^{}]*\}", r.json()["message"]["content"])
            if not m:
                continue
            d = json.loads(m.group(0))
            tag = str(d.get("tag", "")).strip()
            tag = TAG_ALIAS.get(tag, tag)
            if tag not in TAG_BIAS:
                continue
            conf = min(1.0, max(0.0, float(d.get("conf", 0) or 0)))
            return {"tag": tag, "bias": TAG_BIAS[tag], "conf": round(conf, 2)}
        except Exception:
            time.sleep(1.0)
    return None


def load_done():
    if not os.path.exists(OUT_PARQUET):
        return pd.DataFrame(), set()
    df = pd.read_parquet(OUT_PARQUET)
    return df, set(df["id"])


def save(rows):
    df_new = pd.DataFrame(rows)
    df_old, _ = load_done()
    df = pd.concat([df_old, df_new], ignore_index=True) if not df_old.empty else df_new
    df.drop_duplicates(subset=["id"], keep="last", inplace=True)
    df.reset_index(drop=True).to_parquet(OUT_PARQUET, index=False)
    return len(df)


def run(rows, label, batch_flush=200):
    """跑批：跳过已判，每 batch_flush 条落盘一次（断点续跑粒度）。

    连续 FAIL_STREAK_MAX 条失败 → 熔断返回（Ollama 挂掉防雪崩；
    已判部分已落盘，断点续跑下日自动补）。
    """
    _, done = load_done()
    todo = [r for r in rows if r["id"] not in done]
    print("%s: 待判 %d（已判 %d 跳过）" % (label, len(todo), len(done)))
    buf, n_fail, t0, fail_streak = [], 0, time.time(), 0
    for i, r in enumerate(todo, 1):
        res = judge(r["text"])
        if res is None:
            n_fail += 1
            fail_streak += 1
            if fail_streak >= FAIL_STREAK_MAX:
                print(
                    "  熔断：连续 %d 条失败（Ollama 可能挂了），中止判定" % fail_streak,
                    flush=True,
                )
                break
            res = {"tag": "中性披露", "bias": "neutral", "conf": 0.0}
        else:
            fail_streak = 0
        buf.append(
            {
                "id": r["id"],
                "text": r["text"],
                "ts": r["ts"],
                "tag": res["tag"],
                "bias": res["bias"],
                "conf": res["conf"],
                "model": MODEL,
                "judged_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        if i % 10 == 0:
            el = time.time() - t0
            print(
                "  %d/%d (%.1fs, %.2fs/条, fail=%d)"
                % (i, len(todo), el, el / i, n_fail),
                flush=True,
            )
        if len(buf) >= batch_flush:
            save(buf)
            buf = []
        time.sleep(0.3)
    if buf:
        save(buf)
    print("%s 完成: fail=%d 落盘 %s" % (label, n_fail, OUT_PARQUET))


def ollama_ok():
    """Ollama 健康预检（/api/tags 短超时）。"""
    try:
        r = requests.get(OLLAMA.replace("/api/chat", "/api/tags"), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def backfill_events():
    """影子判定按 id 回填 events.jsonl（G6b 正式接入）。

    只补 kind=notice 且 bias 非 bull/bear 的行（规则命中优先不动）；
    LLM 判定为实质主题且 conf>=LLM_CONF_MIN 才回填。text 不变 →
    向量矩阵无需重建。原子写回，幂等（已有 bias 的行永远不碰）。
    """
    from event_graph import _atomic_jsonl  # 复用原子写（无副作用 import）

    df, _ = load_done()
    if df.empty:
        print("回填: 影子表为空，跳过")
        return
    llm = {}
    for r in df.to_dict("records"):
        if r["tag"] == "中性披露" or float(r["conf"]) < LLM_CONF_MIN:
            continue
        # 方向保护：LLM 判 bull 但标题含反向词（终止/失败/取消/撤回），
        # 判定可疑（可能把「终止收购」误判为资产重组），不参与回填
        if r["bias"] == "bull" and any(
            w in (r.get("text") or "") for w in REVERSE_WORDS
        ):
            continue
        llm[r["id"]] = r
    rows, n_fill = [], 0
    with open(EVT_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if (
                e.get("kind") == "notice"
                and e.get("bias") not in ("bull", "bear")
                and e.get("id") in llm
            ):
                r = llm[e["id"]]
                e["bias"] = r["bias"]
                e["tag"] = r["tag"]
                e["conf"] = float(r["conf"])
                e["reason"] = "llm:%s" % r.get("model", MODEL)
                n_fill += 1
            rows.append(e)
    if n_fill:
        _atomic_jsonl(EVT_JSONL, rows)
    print(
        "回填: %d 条实质主题（LLM conf>=%.1f，规则优先未动）" % (n_fill, LLM_CONF_MIN)
    )
    return n_fill


def dist_report(df, title):
    print("\n%s —— 标签分布:" % title)
    vc = df["tag"].value_counts()
    for tag, n in vc.items():
        print(
            "  %-8s %6d (%.1f%%)  conf均值 %.2f"
            % (tag, n, 100 * n / len(df), df.loc[df["tag"] == tag, "conf"].mean())
        )
    print("bias:", df["bias"].value_counts().to_dict())


def sample_print(df, n=15, seed=7):
    rnd = random.Random(seed)
    idx = rnd.sample(range(len(df)), min(n, len(df)))
    print("随机抽检 %d 条:" % len(idx))
    for i in idx:
        r = df.iloc[i]
        print("  [%s|%.2f] %s" % (r["tag"], r["conf"], r["text"][:58]))


def eval_rules(hit, n):
    """规则命中行分层抽样 → LLM 复判 → 一致率。"""
    df_rule = pd.DataFrame(hit)
    parts = []
    for tag, g in df_rule.groupby("tag_rule"):
        parts.append(
            g.sample(
                min(len(g), max(1, round(n * len(g) / len(df_rule)))), random_state=7
            )
        )
    sample = pd.concat(parts, ignore_index=True)
    print("评估样本 %d 条（规则命中分层）" % len(sample))
    rows = []
    for i, r in enumerate(sample.to_dict("records"), 1):
        res = judge(r["text"])
        if res is None:
            res = {"tag": "中性披露", "bias": "neutral", "conf": 0.0}
        rows.append(
            {
                "id": r["id"],
                "text": r["text"],
                "ts": r["ts"],
                "tag_rule": r["tag_rule"],
                "tag": res["tag"],
                "bias": res["bias"],
                "conf": res["conf"],
            }
        )
        if i % 10 == 0:
            print("  %d/%d" % (i, len(sample)), flush=True)
        time.sleep(0.3)
    ev = pd.DataFrame(rows)
    ev["bias_rule"] = ev["tag_rule"].map(TAG_BIAS)
    same_tag = (ev["tag"] == ev["tag_rule"]).mean()
    same_bias = (ev["bias"] == ev["bias_rule"]).mean()
    print(
        "\n规则↔LLM 一致率: tag %.1f%% / bias %.1f%%（n=%d）"
        % (100 * same_tag, 100 * same_bias, len(ev))
    )
    print("混淆 top10（规则→LLM）:")
    mix = ev.groupby(["tag_rule", "tag"]).size().sort_values(ascending=False)
    for (tr, tl), n in mix.head(10).items():
        mark = "=" if tr == tl else "≠"
        print("  %s %s→%s: %d" % (mark, tr, tl, n))
    low_conf_wrong = ev[(ev["bias"] != ev["bias_rule"]) & (ev["conf"] < 0.5)]
    print("低conf(<0.5)且方向不一致: %d 条（LLM 自知不确定）" % len(low_conf_wrong))
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只判前 N 条（小样）")
    ap.add_argument("--eval", type=int, default=0, help="规则命中行抽样 N 条一致性评估")
    ap.add_argument(
        "--backfill",
        action="store_true",
        help="判定新行 + 按 id 回填 events.jsonl（每日链模式）",
    )
    args = ap.parse_args()

    if args.backfill:
        # 每日链模式：Ollama 挂了不阻断（exit 0），只做已判行回填
        # [2026-11-03 B] 分批补采：--limit >0 时本轮只判前 N 条，批次完成即 rc0；
        # 未完成积压由后续轮次续跑（events.jsonl 已判行自动跳过）
        if ollama_ok():
            unhit, _ = load_events()
            limit = max(0, int(args.limit or 0))
            if limit:
                before = len(unhit)
                _, done = load_done()
                unhit = [r for r in unhit if r["id"] not in done]
                unjudged = len(unhit)
                unhit = unhit[:limit]
                print("分批补采：总待判 %d，未判 %d，本轮上限 %d，本轮处理 %d"
                      % (before, unjudged, limit, len(unhit)))
            run(unhit, "G6b 判定")
        else:
            print("Ollama 不可达，跳过判定（断点续跑下日自动补）")
        backfill_events()
        return

    unhit, hit = load_events()
    print("notice 未命中 %d / 规则命中 %d（events.jsonl）" % (len(unhit), len(hit)))

    if args.eval:
        ev = eval_rules(hit, args.eval)
        ev.to_parquet(OUT_PARQUET.replace(".parquet", "_eval.parquet"), index=False)
        return

    rows = unhit[: args.limit] if args.limit else unhit
    run(rows, "G6 影子分类")
    _, done = load_done()
    df = pd.read_parquet(OUT_PARQUET)
    dist_report(df, "全量已判 %d" % len(df))
    sample_print(df)


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "notice_llm", main)
