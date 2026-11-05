# -*- coding: utf-8 -*-
"""最小可用的"登记即取数"闭环：适配器 → 质量校验 → 落地 → 清单。

边界（重要）：
- 落盘到仓库根 `data_hub_landing/`，**不写 quant_backtest/data/**，不进 catalog。
  目的是证明取数链路通，而不是抢主仓的所有权；正式入仓要走受控采集框架。
- 适配器只做"取+归一"，不做业务加工。
- 落盘前必须过 check_frame：行数、日期列、主键唯一、空行。不过就拒绝落盘。

已实测可用的适配器（2026-11-05）：
  stooq          海外日线 CSV，无鉴权，直连 200
  eastmoney      A 股 K 线 JSON，直连 200（K 线域 push2his）
  sina           A 股实时快照，必须带 Referer
  guba           股吧帖子流（嵌入 JSON var article_list），必须带 Referer
  binance_square 币安广场帖子流，必须走本地代理；代理未开时报错退出（脚手架，
                 端点未实测——2026-11-05 实测时 7890 未监听）
社区数据的正式批量采集仍以既有管道为准：雪球走 xq_pump（5 类抽取 +
游标增量），news_collector 承担快讯。本文件适配器定位是探针级最小闭环。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, date
from pathlib import Path

import pandas as pd

from . import net

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
LANDING = REPO / "data_hub_landing"
STATE = HERE / "state"
WATERMARKS = STATE / "watermarks.json"


# ── 适配器：每个函数返回 (DataFrame, 来源 URL, 实际出口) ──────────────────
def fetch_stooq(symbol: str, *, since: str | None = None) -> tuple[pd.DataFrame, str, str]:
    """stooq 日线 CSV。symbol 形如 aapl.us / ^spx / usdcny。"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    res = net.fetch(url, policy="direct", timeout=(8.0, 30.0), source_id="collect:stooq")
    if not res.get("ok"):
        raise RuntimeError(f"stooq 取数失败: {res.get('error') or res.get('status')}")
    from io import StringIO

    df = pd.read_csv(StringIO(res["text"]))
    if df.empty or "Date" not in df.columns:
        raise RuntimeError(f"stooq 返回体非预期: {str(res.get('text'))[:120]}")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).rename(columns=str.lower)
    df.columns = [c.strip().lower() for c in df.columns]
    if since:
        df = df[df["date"] >= pd.Timestamp(since)]
    return df.sort_values("date").reset_index(drop=True), url, res.get("policy", "direct")


def fetch_eastmoney_kline(secid: str, *, limit: int = 5000, since: str | None = None) -> tuple[pd.DataFrame, str, str]:
    """东财日 K。secid 形如 1.600000（沪）/ 0.000001（深）。"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&end=20500101&lmt={limit}"
    )
    res = net.fetch(url, policy="direct", timeout=(8.0, 20.0), expect_json=True,
                    source_id="collect:eastmoney")
    if not res.get("ok"):
        raise RuntimeError(f"eastmoney 取数失败: {res.get('error') or res.get('status')}")
    klines = ((res.get("json") or {}).get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError("eastmoney 返回 klines 为空")
    rows = [line.split(",") for line in klines]
    cols = ["date", "open", "close", "high", "low", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols[: len(rows[0])])
    for c in df.columns:
        if c != "date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if since:
        df = df[df["date"] >= pd.Timestamp(since)]
    return df.sort_values("date").reset_index(drop=True), url, res.get("policy", "direct")


def fetch_sina_quote(codes: str) -> tuple[pd.DataFrame, str, str]:
    """新浪实时快照。codes 形如 sh600000,sz000001。必须带 Referer。"""
    url = f"https://hq.sinajs.cn/list={codes}"
    res = net.fetch(url, policy="direct", timeout=(6.0, 15.0),
                    headers={"Referer": "https://finance.sina.com.cn/"},
                    source_id="collect:sina")
    if not res.get("ok"):
        raise RuntimeError(f"sina 取数失败: {res.get('error') or res.get('status')}")
    from io import StringIO

    records = []
    for line in (res.get("text") or "").splitlines():
        if "=" not in line:
            continue
        head, body = line.split("=", 1)
        code = head.split("_")[-1]
        parts = body.strip().strip(';').strip('"').split(",")
        if len(parts) < 32:
            continue
        records.append(
            {
                "code": code,
                "name": parts[0],
                "open": float(parts[1] or 0),
                "prev_close": float(parts[2] or 0),
                "price": float(parts[3] or 0),
                "high": float(parts[4] or 0),
                "low": float(parts[5] or 0),
                "volume": float(parts[8] or 0),
                "amount": float(parts[9] or 0),
                "quote_time": f"{parts[30]} {parts[31]}",
            }
        )
    if not records:
        raise RuntimeError(f"sina 无可用记录: {str(res.get('text'))[:100]}")
    return pd.DataFrame(records), url, res.get("policy", "direct")


# 帖子流适配器：日期列/主键不同，且水位要保留时分秒（日粒度会重复落同一日帖子）
FEED_META = {
    "guba": {"date_col": "publish_time", "key_cols": ("post_id",), "wm_full": True},
    "binance_square": {"date_col": "publish_time", "key_cols": ("post_id",), "wm_full": True},
}


def _extract_balanced_json(text: str, marker: str) -> dict:
    """从 HTML 里抠 `var xxx={...}` 的完整 JSON（平衡括号，防正则截断）。"""
    i = text.find(marker)
    if i < 0:
        raise RuntimeError(f"页面无 {marker!r} 锚点")
    j = text.find("{", i)
    depth = 0
    end = None
    for k in range(j, len(text)):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end is None:
        raise RuntimeError(f"{marker!r} JSON 未闭合")
    return json.loads(text[j:end])


def fetch_guba(code: str, *, since: str | None = None, pages: int = 1) -> tuple[pd.DataFrame, str, str]:
    """股吧帖子流。code 形如 600519。必须带 Referer（不带 403/风控）。

    数据锚点：list 页内嵌 `var article_list={...}`（实测 80-85 帖/页，
    含 post_id/标题/作者/发布时间/评论数/点击数/多空倾向）。

    翻页（2026-11-05 实测）：第 1 页 `list,<code>.html`，第 n 页
    `list,<code>,f_<n>.html`，每页约 80 帖、按时间倒序。**单页不够用**：
    热门票一天 80+ 帖，只抓一页会把当日帖量顶到 84 的天花板，情绪指标
    就失真了，所以 pages 参数是真翻页而不是摆设。
    带 since 时早于水位的页直接停（按时间倒序，后面只会更旧）。
    """
    last_url = ""
    policy_used = "direct"
    frames = []
    for page in range(1, max(1, pages) + 1):
        url = f"https://guba.eastmoney.com/list,{code}.html" if page == 1 \
            else f"https://guba.eastmoney.com/list,{code},f_{page}.html"
        res = net.fetch(
            url,
            policy="direct",
            timeout=(6.0, 15.0),
            headers={"Referer": "https://guba.eastmoney.com/"},
            max_text=500000,
            source_id="collect:guba",
        )
        if not res.get("ok"):
            if page == 1:
                raise RuntimeError(f"guba 取数失败: {res.get('error') or res.get('status')}")
            break  # 后续页失败不至于丢已取到的首页
        last_url, policy_used = url, res.get("policy", "direct")
        payload = _extract_balanced_json(res["text"], "var article_list=")
        rows = []
        for it in payload.get("re") or []:
            rows.append(
                {
                    "post_id": str(it.get("post_id") or ""),
                    "code": code,
                    "bar_name": payload.get("bar_name") or it.get("stockbar_name"),
                    "title": it.get("post_title"),
                    "user": it.get("user_nickname"),
                    "publish_time": it.get("post_publish_time"),
                    "comments": it.get("post_comment_count"),
                    "clicks": it.get("post_click_count"),
                    "bullish_bearish": it.get("bullish_bearish"),
                    "post_type": it.get("post_type"),
                    "post_url": it.get("art_unique_url"),
                }
            )
        if not rows:
            break
        part = pd.DataFrame(rows)
        part["publish_time"] = pd.to_datetime(part["publish_time"], errors="coerce")
        frames.append(part)
        if since and part["publish_time"].min() < pd.Timestamp(since):
            break  # 本页已翻到水位以前，后面的页只会更旧
    if not frames:
        raise RuntimeError("guba 返回 0 帖（可能触发风控，检查 Referer）")
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["publish_time"]).drop_duplicates(subset=["post_id"])
    if since:
        df = df[df["publish_time"] >= pd.Timestamp(since)]
    return df.sort_values("publish_time").reset_index(drop=True), last_url, policy_used


def fetch_binance_square(dataset: str, *, since: str | None = None) -> tuple[pd.DataFrame, str, str]:
    """币安广场帖子流（脚手架，端点未实测：2026-11-05 代理 7890 未监听）。

    dataset 目前忽略（全站 feed）。必须走本地代理（直连被 RESET）。
    端点为公开抓取实践中的候选，首个返回可解析列表的胜出；
    代理未开时 net.fetch 直接报错，属预期失败。
    """
    candidates = [
        "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId=all&pageNo=1&pageSize=30",
        "https://www.binance.com/bapi/composite/v1/public/community/post/feed?page=1&pageSize=30",
    ]
    last_err = None
    for url in candidates:
        res = net.fetch(url, policy="local", timeout=(8.0, 20.0), expect_json=True,
                        source_id="collect:binance_square")
        if not res.get("ok"):
            last_err = f"{url} -> {res.get('error') or res.get('status')}"
            continue
        data = res.get("json") or {}
        items = data.get("data") or data.get("list") or data.get("articles") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("items") or []
        if not items:
            last_err = f"{url} -> 200 但无可解析列表"
            continue
        rows = []
        for it in items:
            rows.append(
                {
                    "post_id": str(it.get("id") or it.get("articleId") or ""),
                    "title": it.get("title"),
                    "author": (it.get("user") or {}).get("nickName") or it.get("author"),
                    "publish_time": it.get("publishDate") or it.get("createTime"),
                    "likes": it.get("likeCount"),
                    "comments": it.get("commentCount"),
                    "post_url": it.get("shareUrl") or it.get("url"),
                }
            )
        df = pd.DataFrame(rows)
        df["publish_time"] = pd.to_datetime(df["publish_time"], errors="coerce", format="mixed")
        df = df.dropna(subset=["publish_time"]).drop_duplicates(subset=["post_id"])
        if since:
            df = df[df["publish_time"] >= pd.Timestamp(since)]
        return df.sort_values("publish_time").reset_index(drop=True), url, "local"
    raise RuntimeError(f"binance_square 无可用端点（检查本地代理 7890）：{last_err}")


ADAPTERS = {
    "stooq": fetch_stooq,
    "eastmoney": fetch_eastmoney_kline,
    "sina": fetch_sina_quote,
    "guba": fetch_guba,
    "binance_square": fetch_binance_square,
}


# ── 质量校验：不过就不落盘 ────────────────────────────────────────────
def check_frame(df: pd.DataFrame, *, date_col: str = "date", key_cols: tuple[str, ...] = ()) -> dict:
    """返回 {'ok': bool, 'issues': [...]}。宁可拒绝落盘，也不留脏数据。"""
    issues: list[str] = []
    if df is None or df.empty:
        return {"ok": False, "issues": ["空表"]}
    if date_col in df.columns:
        if df[date_col].isna().any():
            issues.append(f"{date_col} 含无法解析的值 {int(df[date_col].isna().sum())} 行")
        elif not key_cols:
            # 有主键列的帖子流里时间重复是常态（多人同分钟发帖），主键去重即可
            dup = int(df[date_col].duplicated().sum())
            if dup:
                issues.append(f"{date_col} 重复 {dup} 行")
    for col in key_cols:
        if col not in df.columns:
            issues.append(f"缺主键列 {col}")
        elif df[col].isna().any():
            issues.append(f"{col} 含空值")
    all_null_rows = int(df.isna().all(axis=1).sum())
    if all_null_rows:
        issues.append(f"全空行 {all_null_rows} 行")
    return {"ok": not issues, "issues": issues}


def _watermarks() -> dict:
    try:
        return json.loads(WATERMARKS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _set_watermark(key: str, value: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    data = _watermarks()
    data[key] = {"last": value, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    WATERMARKS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def collect(adapter: str, dataset: str, *, since: str | None = None, dry_run: bool = False, **kw) -> dict:
    """取一个数据集：适配器 → 校验 → 落 parquet + 清单。"""
    if adapter not in ADAPTERS:
        raise ValueError(f"未知适配器 {adapter}，可选 {sorted(ADAPTERS)}")
    key = f"{adapter}:{dataset}"
    if since is None:
        wm = (_watermarks().get(key) or {}).get("last")
        since = wm  # 有水位就增量
    fn = ADAPTERS[adapter]
    df, url, policy = fn(dataset, since=since, **kw) if adapter != "sina" else fn(dataset, **kw)

    meta = FEED_META.get(adapter, {})
    date_col = meta.get("date_col") or ("quote_time" if adapter == "sina" else "date")
    if since and (df is None or df.empty):
        # 增量拉取常见结果：上游没有新行。这不是故障，不落盘也不算失败。
        return {
            "dataset": key,
            "rows": 0,
            "since": since,
            "status": "no_new",
            "check": {"ok": True, "issues": []},
        }
    verdict = check_frame(df, date_col=date_col, key_cols=meta.get("key_cols", ()))
    result = {
        "dataset": key,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "since": since,
        "url": url,
        "policy": policy,
        "check": verdict,
        "dry_run": dry_run,
    }
    if not verdict["ok"]:
        result["status"] = "rejected"
        return result

    if dry_run:
        result["status"] = "dry_run_ok"
        return result

    out_dir = LANDING / adapter / dataset.replace("/", "_").replace(".", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    idx = len(list(out_dir.glob("part_*.parquet")))
    target = out_dir / f"part_{idx:04d}.parquet"
    tmp = target.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, target)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    manifest_path = out_dir / "_manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manifest = {}
    manifest[target.name] = {
        "rows": int(len(df)),
        "sha256_16": digest,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_url": url,
        "policy": policy,
        "columns": list(df.columns),
        "since": since,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    last = df[date_col].max()
    wm_value = None
    try:
        if meta.get("wm_full"):
            wm_value = pd.Timestamp(last).isoformat(timespec="seconds")
        else:
            wm_value = pd.Timestamp(last).date().isoformat()
    except (TypeError, ValueError):
        wm_value = None
    if wm_value:
        _set_watermark(key, wm_value)
    result.update(
        status="written",
        path=str(target),
        sha256_16=digest,
        watermark=wm_value,
    )
    return result
