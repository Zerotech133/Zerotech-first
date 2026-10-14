#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GateDecision 账本：data/gate_decisions.jsonl 原子追加。

判定即账本行（方案 §三）：每次 decide 落一行 JSON；追加用
open(append)+flush+fsync，进程崩溃不丢已写行。账本只追加不改
历史行；前向回填（fwd）同 gate_shadow_collect.fwd 机制由周报
脚本另行落列/落文件，不与本账本耦合。
"""

import json
import os

PROJECT_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_LEDGER_FP = os.path.join(PROJECT_ROOT, "data",
                                 "gate_decisions.jsonl")


class Ledger:
    """jsonl 账本：append 原子追加；query 按 (market[, date]) 查询。"""

    def __init__(self, fp=None):
        self.fp = fp or DEFAULT_LEDGER_FP

    def append(self, decision):
        """追加一行；GateDecision 或 plain dict 均可。返回落账行。"""
        row = (decision.to_row() if hasattr(decision, "to_row")
               else dict(decision))
        d = os.path.dirname(self.fp)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())     # 崩溃不丢已写行
        return row

    def query(self, market, date=None):
        """按 market（必填）+ date（可选，YYYYMMDD）过滤账本行。"""
        out = []
        if not os.path.exists(self.fp):
            return out
        with open(self.fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue         # 损坏行跳过，不炸查询
                if str(r.get("market")) != str(market):
                    continue
                if date is not None and str(r.get("date")) != str(date):
                    continue
                out.append(r)
        return out

    def count(self):
        """账本总行数（损坏行不计）。"""
        return sum(1 for _ in self._iter_rows())

    def _iter_rows(self):
        if not os.path.exists(self.fp):
            return
        with open(self.fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
