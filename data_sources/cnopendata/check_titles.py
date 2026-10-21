#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档案页面内容一致性校验：确认 URL 打开的**是它自称的那个数据集**。

为什么状态码不够：
    check_links.py 只探 HTTP 状态码。它能抓出 404（如工商那条被"归一化"成父路径的记录），
    但抓不出这种情况——URL 返回 200，页面却是**另一个数据集**。
    手工改地址、上游换 slug、复制粘贴串行，都会产生这种"能打开但张冠李戴"的记录，
    而状态码永远是 200。只有比对页面标题才能发现。

判据（为什么用「精确相等」而不是「相似度过阈值」）：
    详情页 <title> 形如「数据集名 | CnOpenData」。实测 30/30 条归一化后与档案名**完全相等**。
    而档案里名称最相近的一对是「中国上市公司股票复权行情数据」与
    「中国上市公司股票未复权行情数据」，相似度 **0.966** —— 任何比例阈值都挡不住这种错配，
    偏偏复权/未复权互换在量化上是要命的（收益序列完全不同）。
    所以判据定为精确相等：不等即报，按相似度分三档给人排序看。
      exact    归一化后完全一致
      near     >=0.80，极可能是兄弟数据集链错（复权/未复权这类）或标题微调
      mismatch  <0.80，指向了不相干的数据集

用法：
    python check_titles.py                 # 只查未分诊的自动收录条目
    python check_titles.py --all           # 全量
    python check_titles.py --limit 30      # 抽样
    python check_titles.py --self-test     # 变异自检：故意错配必须被判为不一致
    python check_titles.py --calibrate     # 看相似度分布，用于定阈值
"""
from __future__ import annotations

import argparse
import difflib
import html as htmlmod
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
OUT = BASE / "_title_health.json"
MD = BASE / "标题校验报告.md"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
# 分档线：>=NEAR 视为「疑似兄弟数据集链错」，低于则视为指向不相干页面。
# 注意这不是判定线——判定线是「归一化后是否完全相等」。
NEAR = 0.80


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def norm(text: str) -> str:
    """归一化：去站名、去标点空白、统一大小写。只保留中英文数字。"""
    s = htmlmod.unescape(text or "")
    s = re.sub(r"\s*[|｜]\s*CnOpenData.*$", "", s, flags=re.I)
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", s)
    return s.lower()


def sim(name: str, title: str) -> float:
    """名称与标题的相似度，**仅用于排序**，不作为判定依据。"""
    a, b = norm(name), norm(title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def classify(name: str, title: str) -> tuple[str, float]:
    """判定：精确相等才算对上。不等就报，用相似度分档帮人排优先级。"""
    a, b = norm(name), norm(title)
    if not b:
        return "no_title", 0.0
    if a == b:
        return "exact", 1.0
    score = difflib.SequenceMatcher(None, a, b).ratio()
    return ("near" if score >= NEAR else "mismatch"), round(score, 3)


def fetch_title(url: str, attempts: int = 3) -> tuple[int | None, str, str]:
    """取页面标题。5xx 与超时重试（该站高频请求下会偶发 525/超时）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last: tuple[int | None, str, str] = (None, "", "未执行")
    for i in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        try:
            with opener.open(req, timeout=25) as resp:
                body = resp.read(200_000).decode("utf-8", "replace")
                m = TITLE_RE.search(body)
                title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
                return resp.status, title, resp.geturl()
        except urllib.error.HTTPError as exc:
            last = (exc.code, "", url)
            if exc.code < 500:
                return last
        except Exception as exc:  # noqa: BLE001
            last = (None, "", str(exc)[:80])
        if i < attempts - 1:
            time.sleep(2.0 * (i + 1))
    return last


def hardest_pair(datasets: list[dict]) -> tuple[dict, dict] | None:
    """找名称最相近的两条**不同名**记录——错配风险最高的那一对。

    不写死具体记录：档案会演进，写死等于给未来埋假绿。
    """
    uniq: dict[str, dict] = {}
    for d in datasets:
        k = norm(d.get("name", ""))
        if k:
            uniq.setdefault(k, d)
    items = list(uniq.items())
    bucket: dict[str, list[tuple[str, dict]]] = {}
    for k, d in items:
        bucket.setdefault(k[:3], []).append((k, d))
    best: tuple[float, dict, dict] | None = None
    for group in bucket.values():
        for (k1, d1), (k2, d2) in __import__("itertools").combinations(group, 2):
            r = difflib.SequenceMatcher(None, k1, k2).ratio()
            if best is None or r > best[0]:
                best = (r, d1, d2)
    if best is None:
        return None
    return best[1], best[2]


def self_test() -> int:
    """变异自检：拿**最相近的一对**互相错配，必须被判为不一致。

    以前用两个随便挑的记录做自检，错配相似度只有 0.32，随便什么阈值都能过，
    等于没测。现在改用最脏的输入（复权/未复权这类，相似度 0.96+）。
    """
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    pair = hardest_pair(payload["datasets"])
    if pair is None:
        print("自检失败：档案里找不到两条不同名记录")
        return 1
    a, b = pair
    code_a, title_a, _ = fetch_title(a["url"])
    code_b, title_b, _ = fetch_title(b["url"])
    print(f"A: {a['name']}\n   -> {title_a!r} ({code_a})")
    print(f"B: {b['name']}\n   -> {title_b!r} ({code_b})")
    if not title_a or not title_b:
        print("自检失败：取不到标题，无法验证")
        return 1
    v_same, s_same = classify(a["name"], title_a)
    v_cross, s_cross = classify(a["name"], title_b)
    print(f"正配 a.name vs a.title = {v_same} ({s_same})   应 = exact")
    print(f"错配 a.name vs b.title = {v_cross} ({s_cross})   应 != exact")
    if v_same == "exact" and v_cross != "exact":
        print("自检 PASS：最脏输入也能判出，判别力成立")
        return 0
    print("自检 FAIL：无法区分正配与最相近的错配")
    return 1


def write_md(report: dict, path: Path) -> None:
    """人读版报告。与 JSON 同源，避免两套口径。"""
    rows = report["rows"]
    exact = [r for r in rows if r["verdict"] == "exact"]
    near = report["near"]
    mism = report["mismatch"]
    bad = report["non_200"]
    lines = [
        "# CnOpenData 档案内容一致性校验报告", "",
        f"> 校验时间：{report['checked_at']}　｜　范围：{report['scope']}"
        f"（{report['checked']} 条）　｜　判据：{report['criterion']}", "",
        f"> 精确对上 **{len(exact)}**　｜　疑似兄弟链错 **{len(near)}**"
        f"　｜　明显不符 **{len(mism)}**　｜　取不到标题 {len(report['no_title'])}"
        f"　｜　非 200 {len(bad)}", "",
        "> 为什么不用相似度阈值：档案里最相近的一对是「中国上市公司股票复权行情数据」与",
        "> 「中国上市公司股票未复权行情数据」，相似度 **0.966**，任何阈值都挡不住这种错配，",
        "> 而两者互换在量化上是要命的。故判据取「归一化后精确相等」。", "",
    ]
    if not near and not mism and not bad:
        lines += ["## 结论", "", "全部条目打开的页面标题与档案名称一致，无链错。", ""]
    if near:
        lines += ["## 疑似兄弟数据集链错（相似度 ≥ 0.80，优先核对）", "",
                  "| 相似度 | 档案名称 | 实际页面标题 | 链接 |", "|---|---|---|---|"]
        for r in near:
            lines.append(f"| {r['score']:.3f} | {r['name']} | {r['title']} | {r['url']} |")
        lines.append("")
    if mism:
        lines += ["## 明显不符（指向了不相干页面）", "",
                  "| 相似度 | 档案名称 | 实际页面标题 | 链接 |", "|---|---|---|---|"]
        for r in mism:
            lines.append(f"| {r['score']:.3f} | {r['name']} | {r['title']} | {r['url']} |")
        lines.append("")
    if bad:
        lines += ["## 非 200", "", "| 状态 | 档案名称 | 链接 |", "|---|---|---|"]
        for r in bad:
            lines.append(f"| {r['http']} | {r['name']} | {r['url']} |")
        lines.append("")
    lines += ["## 复跑方式", "",
              "```",
              "python check_titles.py --self-test          # 先证明判据有判别力",
              "python check_titles.py --all                # 全量，约 11 分钟",
              "python check_titles.py --report-only        # 从已有 JSON 重出本报告",
              "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--calibrate", action="store_true", help="只打印相似度分布，不落报告")
    ap.add_argument("--report-only", action="store_true", help="从已有 _title_health.json 重出报告，不联网")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.report_only:
        if not OUT.is_file():
            print(f"没有历史结果 {OUT}，无法重出报告")
            return 2
        report = json.loads(OUT.read_text(encoding="utf-8"))
        write_md(report, MD)
        print(f"已从 {OUT.name} 重出报告：{MD}")
        return 0

    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    targets = payload["datasets"]
    if not args.all:
        targets = [d for d in targets if d.get("status") == "new_untriaged"]
    if args.limit:
        targets = targets[: args.limit]

    print(f"待校验 {len(targets)} 条（间隔 {args.delay}s，判据=归一化后精确相等）")
    rows = []
    for i, d in enumerate(targets, 1):
        code, title, final = fetch_title(d["url"])
        verdict, score = classify(d["name"], title) if code == 200 else ("http_" + str(code), 0.0)
        rows.append({"id": d["id"], "name": d["name"], "url": d["url"],
                     "http": code, "title": title, "final_url": final,
                     "verdict": verdict, "score": score, "priority": d.get("priority")})
        if i % 20 == 0:
            print(f"  {i}/{len(targets)}  异常累计 {sum(1 for r in rows if r['verdict'] != 'exact')}")
        time.sleep(args.delay)

    if args.calibrate:
        for r in sorted(rows, key=lambda x: x["score"]):
            print(f"  {r['verdict']:10s} {r['score']:.3f}  {r['name'][:30]:32s} <= {r['title'][:38]}")
        return 0

    near = [r for r in rows if r["verdict"] == "near"]
    mism = [r for r in rows if r["verdict"] == "mismatch"]
    no_title = [r for r in rows if r["verdict"] == "no_title"]
    bad = [r for r in rows if str(r["verdict"]).startswith("http_") or r["verdict"] == "error"]
    exact = [r for r in rows if r["verdict"] == "exact"]

    report = {
        "checked_at": tz_now().isoformat(timespec="seconds"),
        "scope": "all" if args.all else "new_untriaged",
        "criterion": "归一化后精确相等",
        "checked": len(targets),
        "exact": len(exact),
        "near": sorted(near, key=lambda x: -x["score"]),
        "mismatch": sorted(mism, key=lambda x: -x["score"]),
        "no_title": no_title,
        "non_200": bad,
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(report, MD)

    print(f"\n结果：校验 {len(targets)} / 精确对上 {len(exact)} / 疑似兄弟链错 {len(near)}"
          f" / 明显不符 {len(mism)} / 无标题 {len(no_title)} / 非200 {len(bad)}")
    for label, group in (("疑似兄弟链错(near)", report["near"]), ("明显不符(mismatch)", report["mismatch"])):
        if group:
            print(f"\n-- {label} --")
        for r in group[:20]:
            print(f"  {r['score']:.3f}  {r['name']}\n         页面标题：{r['title'] or '（空）'}\n         {r['url']}")
    print(f"\n报告：{OUT}")
    return 0 if not near and not mism and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
