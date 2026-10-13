# -*- coding: utf-8 -*-
"""
adr.py — 架构决策记录（ADR）登记工具（K-ORG-041，落点 docs/adr/）

背景：ARCH1 人设自 K-ORG-018 起写有「ADR 登记：写 domain/arch_registry」，
但该路径从未存在、无工具支撑——纪律悬空（D3 影子评测 ARCH1=45 的病根，
040 报告结论②）。本工具把落点固化为 docs/adr/（入 git，可审计）：
  python tools/adr.py new --slug <短名> --agent ARCH1 --k <K编号> \
      --bg <背景一句> --dec <决策一句> --cost <代价一句> --review <复核期>
  python tools/adr.py list
  python tools/adr.py show <id>
每条 ADR = docs/adr/ADR-<序号3位>-<slug>.md，序号自增（扫目录取 max+1）。
"""
import argparse
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADR_DIR = os.path.join(ROOT, "docs", "adr")

TPL = """# ADR-{aid:03d} · {title}

- **日期**：{date}
- **登记岗**：{agent}
- **关联工单**：{k_id}
- **状态**：accepted（复核期 {review} 到期回访，QA1 按此复核）

## 背景
{bg}

## 决策
{dec}

## 代价（明确接受的坏处/限制）
{cost}

## 复核期
{review}——到期由 QA1 按本 ADR 回访；若推翻，新 ADR 引用本条编号并置本条 status=superseded。
"""


def _next_id():
    if not os.path.isdir(ADR_DIR):
        return 1
    nums = [int(m.group(1)) for f in os.listdir(ADR_DIR)
            for m in [re.match(r"ADR-(\d{3})-", f)] if m]
    return (max(nums) + 1) if nums else 1


def new(slug, agent, k_id, bg, dec, cost, review, title=None):
    os.makedirs(ADR_DIR, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-") or "untitled"
    aid = _next_id()
    date = datetime.date.today().isoformat()
    fn = "ADR-%03d-%s.md" % (aid, slug)
    path = os.path.join(ADR_DIR, fn)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(TPL.format(aid=aid, title=title or slug, date=date,
                           agent=agent, k_id=k_id, review=review,
                           bg=bg, dec=dec, cost=cost))
    print("ADR-%03d registered -> docs/adr/%s" % (aid, fn))
    return path


def list_all():
    if not os.path.isdir(ADR_DIR):
        print("(no ADRs)")
        return
    for f in sorted(os.listdir(ADR_DIR)):
        if f.startswith("ADR-") and f.endswith(".md"):
            head = open(os.path.join(ADR_DIR, f), encoding="utf-8").readline().strip()
            print(f, "|", head.lstrip("# "))


def show(aid_prefix):
    if not os.path.isdir(ADR_DIR):
        sys.exit("no ADR dir")
    hits = [f for f in os.listdir(ADR_DIR) if f.startswith("ADR-" + aid_prefix)]
    if len(hits) != 1:
        sys.exit("match %d files for %r" % (len(hits), aid_prefix))
    print(open(os.path.join(ADR_DIR, hits[0]), encoding="utf-8").read())


def main():
    p = argparse.ArgumentParser(description="ADR registry (docs/adr/)")
    sp = p.add_subparsers(dest="cmd", required=True)
    n = sp.add_parser("new")
    n.add_argument("--slug", required=True)
    n.add_argument("--title")
    n.add_argument("--agent", default="ARCH1")
    n.add_argument("--k", default="-")
    n.add_argument("--bg", required=True)
    n.add_argument("--dec", required=True)
    n.add_argument("--cost", required=True)
    n.add_argument("--review", required=True, help="如 2026-10-20")
    sp.add_parser("list")
    s = sp.add_parser("show")
    s.add_argument("id")
    a = p.parse_args()
    if a.cmd == "new":
        new(a.slug, a.agent, a.k, a.bg, a.dec, a.cost, a.review, a.title)
    elif a.cmd == "list":
        list_all()
    else:
        show(a.id)


if __name__ == "__main__":
    main()
