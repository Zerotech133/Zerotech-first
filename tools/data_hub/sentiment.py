"""社区情绪日表：把帖子流压成 (code, trade_date) 一行，喂风控/因子。

**只做能证伪的计数指标，不做"情绪分"这类不可验证的复合分**：

| 指标 | 含义 | 为什么留它 |
|---|---|---|
| posts | 当日帖量 | 最稳的注意力代理 |
| authors | 独立作者数 | 区分"人多"与"一个人刷屏" |
| top_author_share | 单作者最多发帖占比 | 水军/机器人特征，>0.5 基本可疑 |
| comments / clicks | 评论数 / 点击数合计 | 互动强度 |
| heat | comments + clicks | 关注度总量 |
| bull / bear / neutral | 标题关键词命中数 | 粗粒度方向，词表可配 |
| bull_ratio | bull/(bull+bear) | **无命中时为 None，不填 0.5 假装中性** |

三条口径，写死在代码里而不是文档里：

1. **交易日归属**：非交易日的帖子归到**下一个交易日**（周末发的算周一），
   这样日表能直接和 K 线/px_d 对齐。日历不可用时退回自然日并标注 `cal=natural`。
2. **股吧的 bullish_bearish 字段是空的**（实测 85 帖里 80 个 0、5 个 NaN），
   所以多空必须自己从标题算——不要假设上游给了方向。
3. **雪球当前不可用**（2026-11-05 实测：无 cookie 直连 400，带 token 403
   IP Blacklisted）。雪球数据走 xq_pump，本模块只在 xq 表存在时合并热度，
   缺了不影响股吧口径，且会在返回里显式写 `sources` 说明实际用了什么。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import collect

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "data_hub_landing" / "sentiment_daily"

DEFAULT_BULL = ("看多", "加仓", "买入", "涨停", "突破", "新高", "利好", "反转", "抄底", "起飞")
DEFAULT_BEAR = ("看空", "减仓", "卖出", "跌停", "破位", "新低", "利空", "割肉", "套牢", "崩")


# ── 交易日归属 ────────────────────────────────────────────────────────
def _calendar():
    """延迟导入交易日历；不可用返回 None（降级为自然日，不阻断）。"""
    import sys

    qb = str(REPO / "quant_backtest")
    if qb not in sys.path:
        sys.path.insert(0, qb)
    try:
        import cn_trading_days as cal  # noqa: PLC0415

        return cal
    except Exception:  # noqa: BLE001  日历缺失不该阻断取数
        return None


def next_trading_day(d: date, cal=None, limit: int = 15) -> date:
    """≥ d 的第一个交易日（非交易日归到下一个交易日）。"""
    cal = cal or _calendar()
    if cal is None:
        return d
    if cal.is_trading_day(d):
        return d
    cur = d
    for _ in range(limit):
        cur = date.fromordinal(cur.toordinal() + 1)
        if cal.is_trading_day(cur):
            return cur
    return d


def trade_date_of(ts, cal=None):
    """时间戳 → 归属交易日（date）。非交易日归到下一交易日。"""
    if ts is None or pd.isna(ts):
        return None
    cal = cal or _calendar()
    return next_trading_day(pd.Timestamp(ts).date(), cal)


# ── 关键词方向 ────────────────────────────────────────────────────────
def keyword_score(title, bull= DEFAULT_BULL, bear= DEFAULT_BEAR) -> int:
    """标题 → 1 看多 / -1 看空 / 0 中性（同标题多空都中就判中性，不猜）。"""
    text = str(title or "")
    b = sum(1 for w in bull if w in text)
    s = sum(1 for w in bear if w in text)
    if b and s:
        return 0
    if b:
        return 1
    if s:
        return -1
    return 0


# ── 聚合 ──────────────────────────────────────────────────────────────
def aggregate(posts: pd.DataFrame, *, bull=DEFAULT_BULL, bear=DEFAULT_BEAR,
              cal=None, strict_cal: bool = True) -> pd.DataFrame:
    """帖子明细 → (code, trade_date) 日表。空输入返回空表（不抛）。"""
    cols = ["code", "trade_date", "posts", "authors", "top_author_share", "comments",
            "clicks", "heat", "bull", "bear", "neutral", "bull_ratio", "cal"]
    if posts is None or posts.empty:
        return pd.DataFrame(columns=cols)

    df = posts.copy()
    df["publish_time"] = pd.to_datetime(df["publish_time"], errors="coerce")
    df = df.dropna(subset=["publish_time"])
    if df.empty:
        return pd.DataFrame(columns=cols)
    if "code" not in df.columns:
        df["code"] = ""
    df = df.drop_duplicates(subset=["post_id"]) if "post_id" in df.columns else df

    cal_obj = cal if cal is not None else (_calendar() if strict_cal else None)
    if cal_obj is None:
        df["trade_date"] = df["publish_time"].dt.date
        cal_tag = "natural"
    else:
        uniq = {t.date() for t in df["publish_time"].dropna()}
        mapped = {d: next_trading_day(d, cal_obj) for d in uniq}
        df["trade_date"] = df["publish_time"].dt.date.map(mapped)
        cal_tag = "trading_day"

    df["dir"] = df["title"].map(lambda t: keyword_score(t, bull, bear)) if "title" in df.columns \
        else 0
    # 缺列要给 0 而不是让 pandas 抛：不同源（股吧/币安）字段不全
    for col in ("comments", "clicks"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0.0

    g = df.groupby(["code", "trade_date"], dropna=False)
    out = pd.DataFrame(
        {
            "posts": g.size(),
            "authors": g["user"].nunique() if "user" in df.columns else g.size(),
            "comments": g["comments"].sum(),
            "clicks": g["clicks"].sum(),
            "bull": g["dir"].apply(lambda s: int((s == 1).sum())),
            "bear": g["dir"].apply(lambda s: int((s == -1).sum())),
            "neutral": g["dir"].apply(lambda s: int((s == 0).sum())),
        }
    ).reset_index()
    if "user" in df.columns:
        # 顶号占比：单作者最多发帖数 / 当日帖量。水军/机器人特征，>0.5 基本可疑。
        shares = {}
        for key, sub in df.groupby(["code", "trade_date"], dropna=False):
            users = sub["user"].dropna()
            shares[key] = (
                round(float(users.value_counts().iloc[0]) / len(sub), 3) if len(users) else 0.0
            )
        out["top_author_share"] = [
            shares.get((c, d), 0.0) for c, d in zip(out["code"], out["trade_date"])
        ]
    else:
        out["top_author_share"] = None

    out["heat"] = out["comments"] + out["clicks"]
    denom = out["bull"] + out["bear"]
    out["bull_ratio"] = [
        round(b / d, 3) if d else None for b, d in zip(out["bull"], denom)
    ]
    out["cal"] = cal_tag
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    return out.sort_values(["code", "trade_date"]).reset_index(drop=True)[cols]


# ── 取数 + 落盘 ───────────────────────────────────────────────────────
def read_landing(adapter: str, dataset: str) -> pd.DataFrame:
    """读该数据集已落盘的全部分片；无则空表。"""
    d = collect.LANDING / adapter / dataset
    if not d.is_dir():
        return pd.DataFrame()
    parts = sorted(d.glob("part_*.parquet"))
    if not parts:
        return pd.DataFrame()
    frames = []
    for p in parts:
        try:
            frames.append(pd.read_parquet(p))
        except Exception:  # noqa: BLE001  单分片坏不拖垮全量
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_watchlist(path: str | Path | None = None) -> dict:
    """读 watchlist.yaml；缺失时返回内置最小名单（不抛）。"""
    default = {
        "codes": [{"code": "600519", "note": "茅台，帖子量大，做口径基准"}],
        "bull_words": list(DEFAULT_BULL),
        "bear_words": list(DEFAULT_BEAR),
    }
    p = Path(path) if path else HERE / "watchlist.yaml"
    if not p.exists():
        return default
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return default
    out = dict(default)
    out.update({k: v for k, v in data.items() if v})
    return out


def run(codes: list[str], *, fetch: bool = True, dry_run: bool = False,
        bull=DEFAULT_BULL, bear=DEFAULT_BEAR, pages: int = 2) -> dict:
    """取数 → 聚合 → 追加落盘。返回明细（也用于 CLI 输出与测试）。

    pages=2 是刻意的：单页约 80 帖，热门票一天就能顶满，帖量指标会被
    天花板压平。两页给到约 160 帖/日的余量，再热的票才需要调大。
    """
    result = {"codes": codes, "fetched": {}, "sources": {}, "rows": 0, "path": None,
              "written": False, "frames": {}}
    for code in codes:
        key = code
        if fetch:
            # 只取一次：collect 内部完成取数→三道闸→落盘→水位，这里只读它的结论
            try:
                res = collect.collect("guba", key, pages=pages)
                result["fetched"][key] = {
                    "status": res.get("status"),
                    "rows": res.get("rows"),
                    "watermark": res.get("watermark"),
                }
            except Exception as e:  # noqa: BLE001  单只失败不拖垮整批
                result["fetched"][key] = {"error": f"{type(e).__name__}: {e}"}
        hist = read_landing("guba", key)
        result["sources"][key] = {"guba_rows": int(len(hist))}
        if hist.empty:
            continue
        result["frames"][key] = aggregate(hist, bull=bull, bear=bear)

    frames = [f for f in result["frames"].values() if not f.empty]
    if not frames:
        return result
    daily = pd.concat(frames, ignore_index=True)
    result["rows"] = len(daily)
    if dry_run:
        result["preview"] = daily.to_dict("records")
        return result

    # 落盘：单文件全量覆盖，不追加分片。
    # 每次 run 都是从全部已落盘帖子重算，写成分片会让同一天出现多行
    # （09:45 那次的 46 帖 + 14:30 那次的 120 帖各占一行），下游必须先
    # 去重再取最新——这种"看起来有数据其实得先规整"的表没人会正确使用。
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "sentiment_daily.parquet"
    tmp = OUT / "sentiment_daily.tmp.parquet"
    daily.to_parquet(tmp, index=False)
    os.replace(tmp, target)
    man = OUT / "_manifest.json"
    hist = []
    if man.exists():
        try:
            prev = json.loads(man.read_text(encoding="utf-8"))
            hist = prev.get("writes") or []
        except Exception:  # noqa: BLE001
            hist = []
    hist.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "rows": len(daily),
            "codes": sorted(daily["code"].unique().tolist()),
            "date_min": str(daily["trade_date"].min().date()),
            "date_max": str(daily["trade_date"].max().date()),
            "cal": sorted(daily["cal"].unique().tolist()),
        }
    )
    man.write_text(
        json.dumps({"file": target.name, "writes": hist[-30:]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    result.update(path=str(target), written=True)
    return result


def read_daily(path: str | Path | None = None) -> pd.DataFrame:
    """读日表（单文件，无重复行）；不存在返回空表。"""
    p = Path(path) if path else OUT / "sentiment_daily.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def summary(daily: pd.DataFrame) -> str:
    """人读摘要：每只一行。"""
    if daily is None or daily.empty:
        return "（无数据）"
    lines = []
    for code, g in daily.groupby("code"):
        last = g.sort_values("trade_date").iloc[-1]
        lines.append(
            "%-8s %s 帖%-4d 作者%-3d 顶号占比%-5s 热度%-6d 多空 %d/%d%s"
            % (
                code,
                str(last["trade_date"].date()),
                last["posts"],
                last["authors"],
                ("%.2f" % last["top_author_share"]) if pd.notna(last["top_author_share"]) else "-",
                last["heat"],
                last["bull"],
                last["bear"],
                ("  多空比 %.2f" % last["bull_ratio"]) if last["bull_ratio"] is not None
                and pd.notna(last["bull_ratio"]) else "  多空比 -（标题无方向词命中）",
            )
        )
    return "\n".join(lines)
