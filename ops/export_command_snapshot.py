# -*- coding: utf-8 -*-
"""导出指挥沙盘静态快照（WO-LEGION-S30）。

本机执行沙箱会回收常驻进程，服务活不过几分钟。本脚本把**当前真实快照**
（org.json / hr / tasks.json / schedules / 进化层统计）直接内嵌进 map.html
模板，产出一个**自包含 HTML**：双击即可看，无需起服务。

用法：
  python ops/export_command_snapshot.py                 # 输出到 deliverables/prototypes/
  python ops/export_command_snapshot.py --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_AP = _REPO / "agent_platform"
if str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

from legion_command.snapshot import build_snapshot  # noqa: E402


def evolution_view() -> dict:
    """与 /api/evolution 同源：实验卡清单 + 五域统计（只读）。"""
    exp_dir = _AP / "data" / "experiments"
    cards = []
    if exp_dir.exists():
        for p in sorted(exp_dir.glob("*.json"))[-20:]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            cards.append({"card_id": d.get("card_id"),
                          "hypothesis": str(d.get("hypothesis", ""))[:60],
                          "status": d.get("status")})
    lib = Path.home() / ".hermes" / "profiles" / "zhaoyun" / "experience"
    rows = []
    idx = lib / "_index.jsonl"
    if idx.exists():
        for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    domains = {d: {"entries": sum(1 for r in rows if r.get("domain") == d),
                   "readonly": d == "riskguard"}
               for d in ("stock_screen", "execution", "tooling", "riskguard", "supervision")}
    return {"experiments": cards, "experience_domains": domains,
            "entries_total": len(rows)}


def export(out: Path) -> Path:
    tpl = (_AP / "legion_command" / "static" / "map.html").read_text(encoding="utf-8")
    snap = build_snapshot()
    evo = evolution_view()
    payload = ("<script>window.__SNAP__ = %s; window.__EVO__ = %s;</script>\n</body>"
               % (json.dumps(snap, ensure_ascii=False), json.dumps(evo, ensure_ascii=False)))
    html = tpl.replace("</body>", payload, 1)
    stamp = time.strftime("%Y%m%d_%H%M")
    out = out if out else (_REPO / "deliverables" / "prototypes"
                           / ("legion_sandbox_live_%s.html" % stamp))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    p = export(args.out)
    s = build_snapshot()
    print("exported:", p)
    print("snapshot: %d 营 / %d 员 / %d 在途 / unmapped=%d"
          % (s["summary"]["departments"], s["summary"]["agents"],
             s["summary"]["doing"], len(s["activity"]["unmapped"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
