# -*- coding: utf-8 -*-
"""F4 观点墙适配器：portal/data/todays_picks.json（+ 可选 pick_verdicts.json）
→ portal/data/wall.json，前端观点墙直接消费。

需求真源：deliverables/2026-10-19_量化大屏v3方案_市场准入与策略师观点墙.md §三（F4 工位）。

契约（与前端 cockpit-v3 对齐，字段名不可自改）：
{
  "generated_at":  ISO8601 带时区（取 todays_picks.generated_at 转换，保证幂等）
  "scan_run_date": YYYYMMDD（todays_picks.scan_run_date 去连字符）
  "trade_basis":   todays_picks 原样
  "t1_gate":       todays_picks.t1_gate 原样对象
  "strategists": [{id,name,score,picks_count,top:[{ticker,name,score}],view}]
  "markets": {"A":active,"HK":planned,"US":planned,"CRYPTO":planned}
  "committee_line": 投研会一行结论（review_minutes/analyst/trader 未编制 → 留空）
  "provenance": {todays_picks_path, generated_from, missing}
}

规则：
- 零业务计算、单一数据源、缺源标灰：任何字段取不到就 null/空并记入 provenance.missing，绝不编造；
- 幂等：同输入两次运行输出字节一致（generated_at 源自输入文件而非墙钟）；
- fail-visible：todays_picks 缺失/坏 JSON/结构不符 → stderr + 非零退出，禁静默出空墙；
  pick_verdicts 为可选补充源：缺失记入 missing 继续跑，存在但解析失败 → 报错退出；
- 原子写：同目录 .tmp → os.replace（判例 tools/sync_pipeline_to_strategies.py）；
- 只读输入文件，绝不修改。
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TODAYS_PICKS = os.path.join(REPO_ROOT, "portal", "data", "todays_picks.json")
DEFAULT_VERDICTS = os.path.join(REPO_ROOT, "portal", "data", "pick_verdicts.json")
DEFAULT_OUT = os.path.join(REPO_ROOT, "portal", "data", "wall.json")

MARKETS = {"A": "active", "HK": "planned", "US": "planned", "CRYPTO": "planned"}
TOP_N = 3          # 每位策略师展示的 top 标的条数
# 墙契约顶层键（顺序即输出顺序）
WALL_KEYS = ["generated_at", "scan_run_date", "trade_basis", "t1_gate",
             "strategists", "markets", "committee_line", "provenance"]
STRATEGIST_KEYS = ["id", "name", "score", "picks_count", "top", "view"]


def _fail(msg):
    """fail-visible：stderr 说明 + 非零退出，禁静默跳过。"""
    print("[wall_adapter] 错误：%s" % msg, file=sys.stderr)
    sys.exit(1)


def load_json(path, required=True, label="输入"):
    """读 JSON；required 且缺失 → fail；解析失败一律 fail。"""
    if not os.path.isfile(path):
        if required:
            _fail("%s 不存在：%s" % (label, path))
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        _fail("%s 读取/解析失败 %s：%s" % (label, path, e))


def load_todays_picks(path):
    """读 todays_picks 并校验必需键与结构，任何缺失 → fail-visible。"""
    data = load_json(path, required=True, label="todays_picks")
    if not isinstance(data, dict):
        _fail("todays_picks 顶层不是对象：%s" % path)
    for key in ("generated_at", "scan_run_date", "trade_basis",
                "strategies", "t1_gate", "stocks"):
        if key not in data:
            _fail("todays_picks 缺必需键 %r：%s" % (key, path))
    if not isinstance(data["strategies"], dict) or not data["strategies"]:
        _fail("todays_picks.strategies 不是非空字典：%s" % path)
    if not isinstance(data["stocks"], list):
        _fail("todays_picks.stocks 不是列表：%s" % path)
    return data


def _iso8601_cn(naive_str):
    """'2026-10-18 22:56:38' → '2026-10-18T22:56:38+08:00'（源数据为本地北京时间）。"""
    return naive_str.replace(" ", "T", 1) + "+08:00"


def _view_for(sid, meta):
    """从真实字段组装一句话观点，绝不编造。"""
    parts = []
    n = meta.get("n")
    if isinstance(n, int):
        parts.append("今日 %d 只" % n)
    if sid == "strong" and isinstance(meta.get("lead"), int):
        parts.append("领涨 %d · 观察 %d" % (meta["lead"], meta.get("observe") or 0))
    env = meta.get("env_gate")
    if isinstance(env, dict) and env.get("state"):
        parts.append("环境闸门：%s" % env["state"])
    view = "；".join(parts)
    return view if view else (meta.get("note") or "")


def build_strategists(picks, verdicts):
    """strategies × stocks → 策略师卡片列表（保持 todays_picks 键序）。"""
    stocks = picks["stocks"]
    by_source = {}
    for row in stocks:
        for src in row.get("sources") or []:
            by_source.setdefault(src.get("s"), []).append(row)

    by_strategy_perf = {}
    if isinstance(verdicts, dict):
        by_strategy_perf = (verdicts.get("summary") or {}).get("by_strategy") or {}

    cards = []
    for sid, meta in picks["strategies"].items():
        if not isinstance(meta, dict):
            continue
        n = meta.get("n")
        picks_count = n if isinstance(n, int) else 0
        # top 标的：按 n_hit 降序 → pct_chg 降序 → code 升序（全确定性）
        rows = by_source.get(sid, [])
        rows.sort(key=lambda r: (-(r.get("n_hit") or 0),
                                 -(r.get("pct_chg") or 0),
                                 str(r.get("code") or "")))
        top = [{"ticker": r.get("code") or "",
                "name": r.get("name") or "",
                "score": None}          # 源数据无逐股评分 → null（前端灰标）
               for r in rows[:TOP_N]]
        view = _view_for(sid, meta)
        perf = by_strategy_perf.get(sid)
        if isinstance(perf, dict):
            w1 = perf.get("1") or {}
            if isinstance(w1.get("hit"), (int, float)):
                view += "；考核 1日 hit %.1f%% avg %s%%" % (
                    w1["hit"], w1.get("avg"))
        cards.append({
            "id": sid,
            "name": meta.get("label") or sid,
            "score": None,              # 源数据无策略师级评分 → null（前端灰标）
            "picks_count": picks_count,
            "top": top,
            "view": view,
        })
    return cards


def build_wall(picks, verdicts, picks_path, verdicts_path):
    missing = []
    # 策略师级 score：todays_picks/pick_verdicts 均无此口径 → null + 记缺失
    missing.append("strategist.score: 源文件无策略师级评分字段")
    missing.append("stock.score: 源文件无逐股评分字段")
    if not os.path.isfile(verdicts_path or ""):
        missing.append("pick_verdicts: 文件未找到（可选考核源）%s" % verdicts_path)
    # 投研会一行：review_minutes/analyst_latest/trader_latest 尚未编制（K-STOCK-122 M5）
    missing.append("committee_line: review_minutes/analyst_latest/trader_latest 未编制（K-STOCK-122 M5 挂账）")
    return {
        "generated_at": _iso8601_cn(picks["generated_at"]),
        "scan_run_date": str(picks["scan_run_date"]).replace("-", ""),
        "trade_basis": picks["trade_basis"],
        "t1_gate": picks["t1_gate"],
        "strategists": build_strategists(picks, verdicts),
        "markets": dict(MARKETS),
        "committee_line": "",
        "provenance": {
            "todays_picks_path": os.path.abspath(picks_path),
            "generated_from": [os.path.abspath(p) for p in (picks_path, verdicts_path)
                               if p and os.path.isfile(p)],
            "missing": missing,
        },
    }


def atomic_write_json(path, data):
    """原子写：同目录 .tmp → os.replace，失败清理 tmp。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="F4：todays_picks (+pick_verdicts) → portal/data/wall.json 观点墙")
    ap.add_argument("--todays-picks", default=DEFAULT_TODAYS_PICKS)
    ap.add_argument("--verdicts", default=DEFAULT_VERDICTS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="只打印不落盘")
    args = ap.parse_args(argv)

    picks = load_todays_picks(args.todays_picks)
    verdicts = load_json(args.verdicts, required=False, label="pick_verdicts")
    if verdicts is not None and not isinstance(verdicts, dict):
        _fail("pick_verdicts 顶层不是对象：%s" % args.verdicts)
    wall = build_wall(picks, verdicts, args.todays_picks, args.verdicts)
    text = json.dumps(wall, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        sys.stdout.write(text)
        return
    atomic_write_json(args.out, wall)
    print("[wall_adapter] 已写出 %s（strategists=%d）"
          % (args.out, len(wall["strategists"])))


if __name__ == "__main__":
    main()
