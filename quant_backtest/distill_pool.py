# -*- coding: utf-8 -*-
"""M5 蒸馏样本库构建器：规则弱标签层 + 影子 LLM 层 → 统一蒸馏池。

用途（INTEL_RD_PLAN Batch 3 "M5 蒸馏准备·样本即资产"）：
  notice 分类蒸馏的监督样本——本地 qwen3:8b 蒸馏 / 云端微调（Open Gateway），
  或 prompt 升级时的评测集。只攒后训，本构建器不涉及训练。

数据源（均只读，不写图/不写影子表）：
  ① datamart notice 全量 → event_graph._rule_judge 命中行
     （词表确定性弱标签，source=rule，conf=0.7；bear 类主要来源——
      影子层只判规则未命中行，bear 公告大多被词表拦截，影子层 bear 极稀缺）
  ② data/semantica/notice_llm.parquet 影子层 LLM 判定
     （source=shadow；conf=0 两连败兜底 fail 行剔除，不作监督标签）

合并语义：
  同 id 双源（G6-W1 词表反哺后：反哺前未命中的影子行现可被新词命中）
  → source=dual：bias 一致=互证（conf 取 LLM 侧）；不一致=分歧样本
    note=disagree 剔出训练池单列（"宁可不用不可错标"），保留观察。

落盘：data/semantica/distill_pool.parquet（幂等全量重建）。
字段：id/text/ts_code/ts/tag/bias/source/conf/model/judged_at/note
用法：python distill_pool.py [--dry]（--dry 只打印统计不落盘）
"""

import argparse
import hashlib
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SHADOW_PARQUET = os.path.join(HERE, "data", "semantica", "notice_llm.parquet")
OUT_PARQUET = os.path.join(HERE, "data", "semantica", "distill_pool.parquet")


def _build_rule_rows():
    """datamart notice 全量 → 规则命中行（id 与 event_graph._seed_notice 同构）。"""
    import datamart as dm
    from event_graph import TEXT_MAX, _an_id, _rule_judge

    df = dm.read("notice", limit=2000000)
    if df is None or df.empty:
        return pd.DataFrame()
    rows = []
    for r in df.to_dict("records"):
        code = str(r.get("ts_code") or "").strip()
        nd = str(r.get("notice_date") or "").strip()[:8]
        title = str(r.get("title") or "").strip()
        if not code or len(nd) != 8 or not title:
            continue
        bias, tag = _rule_judge(title)
        if not bias:
            continue
        an = _an_id(r.get("url"), title)
        eid = "ev:nt:%s:%s" % (
            an or hashlib.md5((nd + title).encode("utf-8"), usedforsecurity=False).hexdigest()[:12],
            code,
        )
        rows.append(
            dict(
                id=eid,
                text=title[:TEXT_MAX],
                ts_code=code,
                ts="%s-%s-%s 00:00" % (nd[:4], nd[4:6], nd[6:8]),
                tag=tag,
                bias=bias,
                source="rule",
                conf=0.7,
                model="rules_v2(G6-W1)",
                judged_at=None,
                note="",
            )
        )
    return pd.DataFrame(rows)


def build(dry=False):
    rdf = _build_rule_rows()
    sdf = pd.read_parquet(SHADOW_PARQUET)
    n_fail = int((sdf["conf"] == 0).sum())
    sdf = sdf[sdf["conf"] > 0].copy()  # fail 剔除（两连败不作标签）
    sdf["ts_code"] = sdf["id"].str.split(":").str[-1]
    sdf["source"] = "shadow"
    sdf["note"] = ""
    sdf = sdf[
        [
            "id",
            "text",
            "ts_code",
            "ts",
            "tag",
            "bias",
            "source",
            "conf",
            "model",
            "judged_at",
            "note",
        ]
    ]

    m = rdf.merge(sdf, on="id", how="outer", suffixes=("_r", "_s"), indicator=True)
    # 统一列：影子侧(_s)为底，规则侧(_r)补缺
    for c in ("text", "ts_code", "ts", "tag", "bias", "conf", "model", "judged_at"):
        m[c] = m[c + "_s"].where(m[c + "_s"].notna(), m[c + "_r"])
    m["source"] = "shadow"
    m.loc[m["_merge"] == "left_only", "source"] = "rule"

    both = m["_merge"] == "both"
    agree = both & (m["bias_r"] == m["bias_s"])
    dis = both & (m["bias_r"] != m["bias_s"])
    m["note"] = ""
    m.loc[agree, "source"] = "dual"
    m.loc[agree, "note"] = "互证(rule+llm)"  # conf 已取 LLM 侧
    m.loc[dis, "source"] = "dual"
    m.loc[dis, "note"] = "disagree"
    m.loc[dis, "conf"] = 0.0  # 分歧=不训（宁可不用不可错标）

    out = m[
        [
            "id",
            "text",
            "ts_code",
            "ts",
            "tag",
            "bias",
            "source",
            "conf",
            "model",
            "judged_at",
            "note",
        ]
    ]

    # 统计报告
    L = [
        "=== M5 distill_pool ===",
        "rule(词表命中) %d | shadow(LLM) %d | fail 剔除 %d"
        % (
            int((out["source"] == "rule").sum()),
            int((out["source"] == "shadow").sum()),
            n_fail,
        ),
        "dual: 互证 %d | 分歧(不训) %d" % (int(agree.sum()), int(dis.sum())),
        "total %d" % len(out),
    ]
    train = out[(out["note"] != "disagree")]
    L.append("--- 训练可用 bias ---")
    L.append(train["bias"].value_counts().to_string())
    L.append("--- 训练可用 tag top12 ---")
    L.append(train["tag"].value_counts().head(12).to_string())
    rpt = "\n".join(L)
    print(rpt)
    if not dry:
        out.to_parquet(OUT_PARQUET, index=False)
        print("saved:", OUT_PARQUET, len(out))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    build(getattr(ap.parse_args(), "dry", False))
