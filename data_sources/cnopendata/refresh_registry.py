#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量刷新 CnOpenData 档案：扫分类页，与 registry.json 对账，产出漂移报告。

设计要点
- 扫描范围**从档案自身反推**：registry 里每条详情 URL 形如
  /data/m/<leaf-category>/<slug>.html，取 <leaf-category> 去重即得待扫分类，
  这样档案扩了类目，刷新范围自动跟上，不用改脚本。
- 只读公开分类页，不登录、不调接口、不下载数据内容。
- **不静默删除**：页面上没再出现的条目只标 status=not_seen 并记日期，
  由人判断是下架还是页面改版，绝不直接删记录。

用法：
    python refresh_registry.py            # 扫描并出报告
    python refresh_registry.py --update   # 同时把 first_seen/last_seen/status 写回 registry
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
REPORT_JSON = BASE / "_refresh_report.json"
REPORT_MD = BASE / "刷新报告.md"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
HOST = "https://www.cnopendata.com"
DETAIL_RE = re.compile(r"/data/m/[A-Za-z0-9%&._+/-]+\.html")
# 卡片锚点：<a class="block h-full" href="/data/m/...">…</a>
CARD_RE = re.compile(
    r'<a[^>]+href="(/data/m/[A-Za-z0-9%&._+/-]+\.html)"[^>]*>(.*?)</a>', re.S
)
H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
API_RE = re.compile(r"(?:https?://www\.cnopendata\.com)?/api-store/[A-Za-z0-9%&._+/-]+")
# RSC 产品对象：{\"slug\":\"x\",\"name\":\"y\"}（HTML 里反斜杠已转义）
RSC_PRODUCT_RE = re.compile(r'"slug":"([^"]+)","name":"([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def clean_text(s: str) -> str:
    s = TAG_RE.sub(" ", s or "")
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
          .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", s).strip()


def fetch(url: str, retries: int = 2) -> str | None:
    """匿名 GET，显式禁用代理（本机代理会伪造响应），失败重试。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for i in range(retries + 1):
        try:
            with opener.open(req, timeout=25) as resp:
                raw = resp.read()
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            if i == retries:
                print(f"  !! 取页失败 {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(1.5)
    return None


def leaf_categories(urls: list[str]) -> list[str]:
    cats = set()
    for u in urls:
        m = re.search(r"/data/m/([^/]+)/", u)
        if m:
            cats.add(m.group(1))
    return sorted(cats)


def parse_cards(html: str) -> dict[str, dict]:
    """从分类页解析卡片：{url: {name, desc}}。

    早期版本按 href 位置就近取中文串，结果常抽到分类标签（如「债券数据」）。
    实际结构是每张卡 `<a href=…>` 内含 `<span>分类</span>` 与 `<h3>标题</h3>`，
    因此改为按锚点切块、块内取第一个 h3 作名称、第一个 p 作描述。
    """
    out: dict[str, dict] = {}
    for url, block in CARD_RE.findall(html):
        name = ""
        desc = ""
        m = H3_RE.search(block)
        if m:
            name = clean_text(m.group(1))
        if not name:
            # 没有 h3 时退化为块内非分类文本，仍标记需人工校
            m2 = re.search(r"text-lg[^>]*>(.*?)<", block, re.S)
            if m2:
                name = clean_text(m2.group(1))
        p = P_RE.search(block)
        if p:
            desc = clean_text(p.group(1))[:200]
        if url not in out or (name and not out[url]["name"]):
            out[url] = {"name": name, "desc": desc}
    return out


def slug_of(url: str) -> str:
    """URL 末段转 slug：去 .html、%20 与 %26 解码、非字母数字转 -、小写。"""
    tail = url.rstrip("/").split("/")[-1]
    tail = tail.replace(".html", "")
    tail = urllib.parse.unquote(tail)
    tail = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", tail)
    return re.sub(r"-{2,}", "-", tail).strip("-").lower()


def write_fragment(added: list[tuple[str, dict]], today: str,
                   group_by_cat: dict[str, str], label_by_cat: dict[str, str]) -> Path | None:
    """把新增条目写成 build_registry 可消费的分片。

    走分片而不是直接改 registry.json，是为了保住「registry 由分片合并而来」
    这条唯一入口——直接改产物会在下次 build 时被覆盖。
    """
    if not added:
        return None
    LAYER_BY_CAT = {
        "SSRD上市公司与资本市场": "core", "金融行业数据": "core",
        "新闻舆情与文本语料": "core", "API商店": "core",
        "因子与事件数据": "factor", "宏观与区域背景": "context",
    }
    records = []
    for url, info in sorted(added, key=lambda x: x[1]["category"]):
        records.append({
            "id": "cnod-" + slug_of(url),
            "layer": LAYER_BY_CAT.get(info["category"], "factor"),
            "category_group": group_by_cat.get(info["category"], "因子与事件数据"),
            "category": label_by_cat.get(info["category"], info["category"]),
            "name": info["name"] or slug_of(url),
            "url": url,
            "desc": (info["desc"] or "（刷新脚本自动收录，描述待补）")[:120],
            "coverage": "未标注", "volume": "未标注", "frequency": "未标注",
            "access_mode": "direct_sale",          # 分类页不标 OPEN，待逐条核
            "api_available": url.startswith(HOST + "/api-store/"),
            "tags": ["待分类"], "maps_to": ["量化/因子"],
            "priority": "P3",                       # 未分诊，一律 P3，等人工提级
            "auth_status": "未授权-需采购",
            "notes": f"增量刷新自动收录（{today}），名称取自分类页 h3，描述取自卡片首段；"
                     "获取方式与优先级**未经人工核实**，不得直接采购或接入。",
            "first_seen": today,
            "status": "new_untriaged",
        })
    out = BASE / "_fragments" / f"frag_refresh_{today.replace('-', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "shard": f"refresh-{today}", "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="把 first_seen/last_seen/status 写回 registry")
    ap.add_argument("--merge", action="store_true", help="把新增条目写成分片，交由 build_registry 合并")
    args = ap.parse_args()

    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    datasets = payload["datasets"]
    known = {d["url"]: d for d in datasets}
    today = tz_now().date().isoformat()

    # 分类 slug -> 分类组/标签，取自现有档案，保证新记录与原记录同组
    group_by_cat: dict[str, str] = {}
    label_by_cat: dict[str, str] = {}
    for d in datasets:
        m = re.search(r"/data/m/([^/]+)/", d.get("url", ""))
        if m:
            c = m.group(1)
            if c not in label_by_cat and d.get("category"):
                label_by_cat[c] = d["category"]
            if c not in group_by_cat and d.get("category_group"):
                group_by_cat[c] = d["category_group"]

    cats = leaf_categories([d["url"] for d in datasets if "/data/m/" in d["url"]])
    print(f"档案 {len(datasets)} 条；按 URL 反推待扫分类 {len(cats)} 个")

    seen: dict[str, dict] = {}  # url -> {category, name, desc}
    failed: list[str] = []
    for i, cat in enumerate(cats, 1):
        url = f"{HOST}/catalogs/{cat}"
        html = fetch(url)
        if html is None:
            failed.append(cat)
            continue
        cards = parse_cards(html)
        if not cards:
            # 兜底：连卡片结构都解析不出时，至少保住链接集合
            cards = {u: {"name": "", "desc": ""} for u in set(DETAIL_RE.findall(html))}
        print(f"  [{i}/{len(cats)}] {cat}: {len(cards)} 条")
        for u, info in cards.items():
            seen.setdefault(HOST + u, {"category": cat, **info})

    # API 商店单独扫（排除 .js/.css 等静态资源）
    api_html = fetch(f"{HOST}/api-store")
    if api_html:
        # 优先解析 RSC 里的产品对象（含 slug+name，最完整）；失败再退回 URL 匹配
        flat = api_html.replace('\\"', '"')
        products = RSC_PRODUCT_RE.findall(flat)
        apis: dict[str, str] = {}
        for slug, name in products:
            slug = slug.strip()
            if slug:
                apis["/api-store/" + slug] = clean_text(name)
        if not apis:
            for raw in API_RE.findall(api_html):
                path = raw.split("www.cnopendata.com", 1)[-1] if "www.cnopendata.com" in raw else raw
                slug = path.rstrip("/").rsplit("/", 1)[-1]
                if "." in slug or slug.startswith("page-") or not slug:
                    continue
                apis[path] = ""
        print(f"  [api-store]: {len(apis)} 个接口")
        for u, nm in sorted(apis.items()):
            seen.setdefault(HOST + u, {"category": "api-store", "name": nm, "desc": ""})
    else:
        failed.append("api-store")

    by_id = {d["id"]: d for d in datasets}
    added, cross_listed = [], []
    for u, v in seen.items():
        if u in known:
            continue
        # 同一数据集在多个类目下重复挂载：URL 不同但 id 相同，合并时会被并入既有条目
        rid = "cnod-" + slug_of(u)
        if rid in by_id:
            cross_listed.append({"url": u, "merged_into": rid, "existing_url": by_id[rid]["url"],
                                 "category": v["category"]})
        else:
            added.append((u, v))
    missing = [d for u, d in known.items() if u not in seen]

    report = {
        "checked_at": tz_now().isoformat(timespec="seconds"),
        "categories_scanned": len(cats),
        "failed_categories": failed,
        "registry_total": len(datasets),
        "seen_on_site": len(seen),
        "added": [{"url": u, "category": v["category"],
                   "name_guess": v["name"], "desc_guess": v["desc"]} for u, v in sorted(added)],
        "cross_listed": cross_listed,
        "not_seen": [{"id": d["id"], "name": d["name"], "url": d["url"], "category": d.get("category")} for d in missing],
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# CnOpenData 档案增量刷新报告", "",
        f"> 检查时间：{report['checked_at']}　｜　扫描分类 {len(cats)} 个（失败 {len(failed)}）",
        f"> 档案 {len(datasets)} 条　｜　站点可见 {len(seen)} 条　｜　**真新增 {len(added)}**"
        f"　｜　跨类目重复 {len(cross_listed)}　｜　**本期未见 {len(missing)}**",
        "",
        "> `本期未见` 只代表该 URL 没出现在对应分类页上，可能是下架、搬迁或页面改版，",
        "> **本脚本不删除任何记录**，需人工确认后再处置。",
        "",
    ]
    if cross_listed:
        lines += ["## 跨类目重复挂载（同一数据集的另一条路径；档案已收录该数据集，故不算新增）", ""]
    if added:
        lines += ["## 新增条目（站点有、档案无）", "", "| 分类 | 名称 | 链接 |", "|---|---|---|"]
        for u, v in sorted(added, key=lambda x: (x[1]["category"], x[1]["name"])):
            lines.append(f"| {v['category']} | {v['name'] or '（未提取到）'} | {u} |")
        lines.append("")
    if missing:
        lines += ["## 本期未见（档案有、站点未见）", "", "| id | 名称 | 分类 |", "|---|---|---|"]
        for d in missing:
            lines.append(f"| {d['id']} | {d['name']} | {d.get('category', '')} |")
        lines.append("")
    if failed:
        lines += ["## 取页失败", "", *[f"- {c}" for c in failed], ""]
    if not added and not missing and not failed:
        lines.append("**本期无漂移。**")
        lines.append("")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")

    if args.update:
        for d in datasets:
            d.setdefault("first_seen", d.get("_first_seen_hint", today))
            d["last_seen"] = today if d["url"] in seen else d.get("last_seen")
            d["status"] = "active" if d["url"] in seen else "not_seen"
        payload["meta"]["last_refresh"] = report["checked_at"]
        REGISTRY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写回 first_seen/last_seen/status")

    if args.merge:
        frag = write_fragment(added, today, group_by_cat, label_by_cat)
        if frag:
            print(f"已写分片 {frag.name}（{len(added)} 条），请跑 build_registry.py 合并")
        else:
            print("无新增，未写分片")

    print(f"\n新增 {len(added)} / 未见 {len(missing)} / 失败 {len(failed)}")
    print(f"   {REPORT_JSON}")
    print(f"   {REPORT_MD}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
