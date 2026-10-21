#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对"同名但字段冲突"的组，去页面原文取三件事，判哪条记录写错了。

为什么必须核原文：
    清单能把冲突列出来，但"哪条对"只能看页面。抽样核过一组：
    页面写 `时间区间 2003.08-2023.5.30`，而档案里有一条记成「2003 至今」——
    对追新判断这是实质错误（以为还在更新，其实止于 2023 年中）。
    这类判断不能靠人点 18 次链接，程序取一次就够。

只读：GET 详情页，抽 `时间区间 / 更新频率 / 数据规模` 三个字段，不改任何数据。

用法：
    python alias_probe.py            # 核 B 类（内容冲突）全部
    python alias_probe.py --limit 2  # 先抽两组看抽取质量
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
GROUPS = BASE / "_alias_groups.json"
OUT_MD = BASE / "同名数据集页面核对.md"
OUT_JSON = BASE / "_alias_probe.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# 页面标签 -> 档案字段
LABELS = [("时间区间", "coverage"), ("更新频率", "frequency"), ("数据规模", "volume")]
ALL_LABELS = [lbl for lbl, _ in LABELS] + ["数据格式", "数据下载", "更新时间", "样例数据"]
# 页面里与字段同名区域混杂的导航/按钮文案，命中即认为不是值
NOISE = ("系列导航", "首页", "数据目录", "申请数据", "联系销售", "数据说明", "字段展示", "购买方")


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def fetch(url: str, attempts: int = 3) -> str | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
            with opener.open(req, timeout=25) as resp:
                return resp.read(900_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return None
        except Exception:  # noqa: BLE001
            pass
        if i < attempts - 1:
            time.sleep(2.0 * (i + 1))
    return None


def to_text(html: str) -> str:
    """整页先去标签成纯文本。

    不能边截断边去标签：600 字的窗口会从半个 `<svg` 标签中间切开，
    闭合的 `>` 落在窗口外，标签就删不掉，值里混进一串标记。
    一次性处理整页再用纯文本找标签，才不会有这种截断残留。
    """
    h = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S)
    h = re.sub(r"<style[^>]*>.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<svg[^>]*>.*?</svg>", " ", h, flags=re.S)
    h = re.sub(r"<[^>]*>", " ", h)          # 顺手清掉未闭合的残tag
    return re.sub(r"\s+", " ", h).strip()


FREQ_ENUM = ("实时", "日", "周", "月", "季度", "年度", "不定期", "按需")


def valid_value(field: str, val: str) -> bool:
    """按字段类型校验抽到的是不是"值"，不是就继续往后找。

    靠关键词拉黑会误杀：`年度 更新 申请数据试用` 里 `申请数据` 是按钮文案，
    一刀切会把 `年度` 这个真值也丢掉。按类型校验更准。
    """
    if len(val) < 2 or any(val.startswith(o) for o in ALL_LABELS) or any(k in val for k in NOISE):
        return False
    if field == "volume":
        return bool(re.search(r"[\d万亿条张个份]", val))
    if field == "coverage":
        return bool(re.search(r"\d|至今|未标注", val))
    if field == "frequency":
        # 必须是"以频率词开头且很短"，不能只要求"包含"：
        # 「日」是单字，正文里任何一个「日」都会让整段说明文通过校验，
        # 实测抓出过「最高可达每2分钟一次，为人口经济学…」这类正文当频率。
        head = val.strip()[:8]
        return len(val) <= 10 and any(head.startswith(w) for w in FREQ_ENUM)
    return True


def extract(text: str, label: str, field: str) -> str:
    """在纯文本里取标签后面的值。

    同一标签会出现多次：先是表头分组（后面紧跟另一个标签或骨架屏文案），
    带值那次后面才是内容。逐次尝试，跳过"值其实就是另一个标签"的情况。
    """
    pos = 0
    while True:
        i = text.find(label, pos)
        if i < 0:
            return ""
        pos = i + len(label)
        seg = text[pos: pos + 80]
        cut = len(seg)
        for other in ALL_LABELS + list(NOISE):
            j = seg.find(other)
            if 0 <= j < cut:
                cut = j
        val = seg[:cut].strip(" |·-—:")
        val = re.sub(r"\s*(更新|查看|详情|下载)\s*$", "", val).strip()
        if not valid_value(field, val):
            continue
        return val[:60]


def norm(value: str) -> tuple:
    s = re.sub(r"\s+", "", str(value or ""))
    if not s:
        return ("empty",)
    if s.startswith("未标注"):
        return ("unlabeled",)
    digits = re.findall(r"\d+", s)
    if digits:
        return ("num", tuple(int(d) for d in digits))
    return ("txt", s.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    if not GROUPS.is_file():
        print("缺 _alias_groups.json，先跑 alias_report.py")
        return 2
    groups = json.loads(GROUPS.read_text(encoding="utf-8"))["b_content_conflict"]
    by_id = {d["id"]: d for d in json.loads(REGISTRY.read_text(encoding="utf-8"))["datasets"]}
    if args.limit:
        groups = groups[: args.limit]

    print(f"待核 {len(groups)} 组（每组 2 个页面，间隔 {args.delay}s）")
    results = []
    for i, g in enumerate(groups, 1):
        canon, alias = by_id.get(g["canonical"]), by_id.get(g["alias"])
        if not canon or not alias:
            continue
        row = {"name": g["name"], "canonical": canon["id"], "alias": alias["id"], "pages": {}}
        for role, rec in (("canonical", canon), ("alias", alias)):
            html = fetch(rec["url"])
            page = {}
            if html:
                text = to_text(html)
                for label, field in LABELS:
                    page[field] = extract(text, label, field)
            row["pages"][role] = {"url": rec["url"], "page": page,
                                  "record": {f: rec.get(f) for _, f in LABELS},
                                  "ok": bool(html)}
            time.sleep(args.delay)
        results.append(row)
        print(f"  [{i}/{len(groups)}] {g['name']}")
        for role in ("canonical", "alias"):
            p = row["pages"][role]
            if not p["ok"]:
                print(f"      {role}: 取页失败")
                continue
            print(f"      {role}: 页面 {p['page']}")

    # 判定：页面对该记录是"一致/不一致/页面未写/取页失败"
    def verdict(page_val: str, rec_val: str) -> str:
        if not page_val:
            return "页面未写该字段"
        if not str(rec_val or "").strip():
            return "档案未填"
        a, b = norm(page_val), norm(rec_val)
        if a == b:
            return "一致"
        # 页面常把字段写成「年度 更新」「截至2024.11」这类带修饰的形态，
        # 一方包含另一方即视为同一事实，避免修饰词造成假冲突。
        pa = re.sub(r"\s+", "", str(page_val))
        ra = re.sub(r"\s+", "", str(rec_val))
        if pa and ra and (ra in pa or pa in ra):
            return "一致"
        return "不一致"

    for row in results:
        for role in ("canonical", "alias"):
            p = row["pages"][role]
            p["verdict"] = {f: verdict(p["page"].get(f, ""), p["record"].get(f, "")) for _, f in LABELS}

    now = tz_now()
    lines = ["# 同名数据集页面核对（内容冲突组）", "",
             f"> 核对时间：{now.isoformat(timespec='seconds')}　｜　组数 {len(results)}"
             f"　｜　方法：直接取详情页 `时间区间 / 更新频率 / 数据规模` 三个字段原文", "",
             "> 判定口径：页面数字序列与档案一致即「一致」；不一致说明记录与页面事实矛盾。",
             "> 页面本身没写该字段时记「页面未写该字段」，不当成记录错误。", ""]
    lines += ["## 结论汇总", "", "| 组 | 正本判定 | 别名判定 |", "|---|---|---|"]
    for row in results:
        def brief(role: str) -> str:
            v = row["pages"][role]["verdict"]
            bad = [f for f, r in v.items() if r == "不一致"]
            if not row["pages"][role]["ok"]:
                return "取页失败"
            return "、".join(bad) + " 不一致" if bad else "与页面一致"
        lines.append(f"| {row['name']} | {brief('canonical')} | {brief('alias')} |")
    lines.append("")

    for row in results:
        lines += [f"## {row['name']}", ""]
        lines += ["| 角色 | 字段 | 档案记载 | 页面原文 | 判定 |", "|---|---|---|---|---|"]
        for role, label in (("canonical", "正本"), ("alias", "别名")):
            p = row["pages"][role]
            if not p["ok"]:
                lines.append(f"| {label} | — | — | — | 取页失败：{p['url']} |")
                continue
            for _, field in LABELS:
                fname = dict((f, lb) for lb, f in LABELS)[field]
                lines.append(f"| {label} | {fname} | {str(p['record'].get(field) or '（空）')}"
                             f" | {p['page'].get(field) or '（未取到）'} | {p['verdict'][field]} |")
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"checked_at": now.isoformat(timespec="seconds"),
                                    "rows": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    bad = sum(1 for r in results for role in ("canonical", "alias")
              for v in r["pages"][role]["verdict"].values() if v == "不一致")
    print(f"\n结果：{len(results)} 组，与页面矛盾的字段共 {bad} 处")
    print(f"报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
