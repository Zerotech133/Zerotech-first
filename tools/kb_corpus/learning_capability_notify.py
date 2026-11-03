# -*- coding: utf-8 -*-
"""学习能力图谱通知：把能力/战法/因子/军团调用摘要推送给相关军团（默认 dry-run）。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "portal/static/learning_capability.json"
OUT = REPO / "research/reports"


def build_text(d: dict) -> str:
    o = d.get("overall") or {}
    lines = [
        "【学习能力图谱已更新】",
        f"数据：{o.get('files', 0)} 文件 / {o.get('chars', 0):,} 字 / {o.get('units', 0)} 知识单元",
        f"可用：含领域词单元 {o.get('units_with_domain', 0)}；可审候选 {o.get('candidate_pool', 0)}；"
        f"视频号学习卡 {o.get('cards_merged_video', 0)}（总卡 {o.get('cards_total', 0)}）",
        "",
        "战法（可提炼）：",
    ]
    for t in (d.get("tactics") or [])[:8]:
        lines.append(f"· {t['name']}｜单元 {t['unit_support']}｜可计算性 {t['computability']}")
    gaps = [x["name"] for x in (d.get("data_capabilities") or [])
            if str(x.get("status", "")).startswith("**缺失")]
    ready_min = [x["name"] for x in (d.get("data_capabilities") or [])
                 if "分钟" in str(x.get("name")) and "已入库" in str(x.get("status"))]
    lines += ["", f"数据缺口：{'、'.join(gaps) or '无'}（不可行类）；"
              f"{'分钟K线已入库（stk_mins），' if ready_min else ''}其余日线级可立即实现。", "",
              "因子（细化）："]
    for f in (d.get("factors") or [])[:6]:
        lines.append(f"· {f['factor']}：{f['status']}")
    lines += ["", "军团调用：", ]
    for g in (d.get("legion_calls") or []):
        lines.append(f"· {g['legion']}：{g['call']}")
    lines += ["", "明细：http://127.0.0.1:8734/learning_projects.html", "（研究输出，不构成投资建议）"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真实发送飞书文本（默认 dry-run）")
    ap.add_argument("--text-only", action="store_true", help="只打印文本")
    args = ap.parse_args(argv)
    d = json.loads(DATA.read_text(encoding="utf-8"))
    text = build_text(d)
    if args.text_only or not args.send:
        print(text)
        if not args.send:
            print("\n[dry-run] 未发送；加 --send 真实推送")
        return 0
    sys.path.insert(0, str(REPO / "news_collector"))
    import send_feishu  # type: ignore
    res = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "kind": "learning:capability", "text": text, "result": send_feishu.send_text(text)}
    p = OUT / f"learning_capability_notify_{datetime.now().strftime('%Y%m%d')}.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["result"], ensure_ascii=False)[:400], "| receipt:", p.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
