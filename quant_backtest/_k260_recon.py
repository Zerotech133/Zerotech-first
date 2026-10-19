# -*- coding: utf-8 -*-
"""K260 对账：parquet 候选 id 与 events.jsonl notice id 交集检查（只读）。"""
import json
import pandas as pd

PQ = r"C:\项目\老田的AI助手\quant_backtest\data\semantica\notice_llm.parquet"
EVT = r"C:\项目\老田的AI助手\quant_backtest\data\semantica\events.jsonl"

df = pd.read_parquet(PQ)
all_pq = set(df["id"])
cand_ids = set(df[(df["tag"] != "中性披露") & (df["conf"] >= 0.7)]["id"])

rows = []
with open(EVT, encoding="utf-8") as f:
    for line in f:
        rows.append(json.loads(line))
notice_ids = set(e["id"] for e in rows if e.get("kind") == "notice")
all_ids = set(e["id"] for e in rows)

print("parquet 全量 id 数:", len(all_pq))
print("notice 行 id 数:", len(notice_ids))
print("parquet 样本 id:", sorted(all_pq)[:5])
print("notice 样本 id:", sorted(notice_ids)[:5])
print("parquet∩notice:", len(all_pq & notice_ids))
print("候选在 notice 中:", len(cand_ids & notice_ids))
print("候选在事件全量中:", len(cand_ids & all_ids))
