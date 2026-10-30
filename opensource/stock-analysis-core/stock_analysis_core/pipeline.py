# -*- coding: utf-8 -*-
"""流水线编排：适配器 → 特征 → 质量门 → 研判 → 渲染 → 落盘。"""
import json
import os
import time
from typing import Any, Dict, Optional

from . import __version__, features, gates, render, verdict
from .adapters.csv_source import CSVDataSource
from .types import Snapshot


class Pipeline:
    def __init__(self, data_source=None, policy: verdict.VerdictPolicy | None = None,
                 gate_config: gates.GateConfig | None = None):
        self.source = data_source
        self.policy = policy
        self.gate_config = gate_config

    def load(self, code: str, data_dir: Optional[str] = None) -> Snapshot:
        src = self.source or (CSVDataSource(data_dir) if data_dir else None)
        if src is None:
            raise ValueError("需要 data_source 或 data_dir")
        bars = src.bars(code)
        if not bars:
            raise ValueError("未找到 %s 的日线数据" % code)
        snap = Snapshot(code=code, name=code, as_of=bars[-1].date,
                        bars=bars, fundamentals=src.fundamentals(code),
                        valuation=src.valuation(code), events=src.events(code))
        return snap

    def run(self, code: str, data_dir: Optional[str] = None, out_dir: str = "./out") -> Dict[str, Any]:
        snap = self.load(code, data_dir)
        feat = features.compute(snap)
        checks = gates.run(snap, feat, self.gate_config)
        v = verdict.compute(snap, feat, self.policy)
        ctx = {"snapshot": snap, "features": feat, "verdict": v, "checks": checks, "version": __version__}
        md = render.render(ctx)
        g4 = gates.g4(md, {"verdict": v.__dict__})
        checks.append(g4)
        os.makedirs(os.path.join(out_dir, code), exist_ok=True)
        base = os.path.join(out_dir, code)
        with open(os.path.join(base, "report.md"), "w", encoding="utf-8") as f:
            f.write(md)
        result = {
            "code": code, "name": snap.name, "as_of": snap.as_of, "engine_version": __version__,
            "generated": time.strftime("%F %T"), "features": feat, "verdict": v.__dict__,
            "checks": [c.__dict__ for c in checks],
            "artifacts": {"report": os.path.join(base, "report.md")},
        }
        with open(os.path.join(base, "analysis.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        return result


def run(code: str, data_dir: str, out_dir: str = "./out") -> Dict[str, Any]:
    return Pipeline().run(code, data_dir, out_dir)
