# -*- coding: utf-8 -*-
"""F4 观点墙适配器：portal/data/todays_picks.json（+ 可选 pick_verdicts.json）
→ portal/data/wall.json，前端观点墙直接消费。

G1 自有产物接入（K-STOCK-122 §八挂账，v3 方案 §五缺口 2）：
  - linkage（板块联动）：真身=quant_backtest/data/linkage_shadow.jsonl
    （quant_backtest/linkage_collector.py 五信号影子采集，观察池=持仓+自选+
    共振 TopN，非全市场扫描）。产物存在 → 既有 linkage 卡 view 补实数
    （最近信号日+五信号命中计数）；picks_count 不动（个股级工具不产名单）。
  - jev（AI 复核门）：真身=jev-eval/review_data/triples.jsonl
    （jev-eval/signal_review_gate.py 五原子复核影子门，decision∈PASS/FOCUS/
    FOCUS_CN/WATCH/REJECT）。jev 是门不是选股策略师 → 新卡 id=jev，
    view=最近一次复核通过率+日期，top 留空（STRATEGIST_KEYS 同构）。
  两源均为可选源，语义与 verdicts/minutes 同款：缺文件/空产物 → 记
  provenance.missing 继续跑，坏 JSON → 非零退出；绝不编造。

需求真源：deliverables/2026-10-19_量化大屏v3方案_市场准入与策略师观点墙.md §三（F4 工位）。

契约（与前端 cockpit-v3 对齐，字段名不可自改）：
{
  "generated_at":  ISO8601 带时区（取 todays_picks.generated_at 转换，保证幂等）
  "scan_run_date": YYYYMMDD（todays_picks.scan_run_date 去连字符）
  "trade_basis":   todays_picks 原样
  "t1_gate":       todays_picks.t1_gate 原样对象
  "strategists": [{id,name,score,picks_count,top:[{ticker,name,score}],view}]
  "markets": {"A":active,"HK":planned,"US":planned,"CRYPTO":planned}
  "committee_line": 投研会一行结论（k_stock/paper/minutes.json 最新纪要「会议结论」行原文引用
                    → "MM-DD 盘前会｜结论"；文件缺失/无结论行 → 留空并记 missing，绝不编造）
  "provenance": {todays_picks_path, generated_from, missing, committee_source|null}
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
import collections
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TODAYS_PICKS = os.path.join(REPO_ROOT, "portal", "data", "todays_picks.json")
DEFAULT_VERDICTS = os.path.join(REPO_ROOT, "portal", "data", "pick_verdicts.json")
DEFAULT_MINUTES = os.path.join(REPO_ROOT, "k_stock", "paper", "minutes.json")
DEFAULT_LINKAGE_SHADOW = os.path.join(REPO_ROOT, "quant_backtest", "data",
                                      "linkage_shadow.jsonl")
DEFAULT_JEV_TRIPLES = os.path.join(REPO_ROOT, "jev-eval", "review_data",
                                   "triples.jsonl")
DEFAULT_OUT = os.path.join(REPO_ROOT, "portal", "data", "wall.json")

MARKETS = {"A": "active", "HK": "planned", "US": "planned", "CRYPTO": "planned"}
TOP_N = 3          # 每位策略师展示的 top 标的条数
KIND_LABEL = {"pre_open": "盘前会", "post_close": "收盘会"}
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


def extract_committee(minutes_path):
    """投研会一行真源：k_stock/paper/minutes.json 最新纪要的「会议结论」行。

    返回 (line, source_meta, missing_note)。引用原文绝不改写；
    缺文件/无 items/无结论行 → (None, None, 说明) 继续跑；
    文件存在但坏 JSON → SystemExit（与 verdicts 同语义，fail-visible）。
    """
    if not os.path.isfile(minutes_path or ""):
        return None, None, ("committee_line: minutes.json 未找到"
                            "（可选投研会纪要源）%s" % minutes_path)
    try:
        with open(minutes_path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (ValueError, OSError) as e:
        sys.stderr.write("[wall_adapter] minutes.json 解析失败: %s\n" % e)
        raise SystemExit(2)
    items = d.get("items") if isinstance(d, dict) else None
    if not items:
        return None, None, "committee_line: minutes.json 无 items（纪要为空）"
    latest = max(items, key=lambda m: str(m.get("created_at", "")))
    body = str(latest.get("body", ""))
    m = re.search(r"\*\*会议结论\*\*[：:](.+)", body)
    if not m:
        return None, None, ("committee_line: 最新纪要（%s）无「会议结论」行"
                            % latest.get("created_at", "?"))
    conclusion = m.group(1).strip()
    created = str(latest.get("created_at", ""))
    kind = str(latest.get("kind", ""))
    label = KIND_LABEL.get(kind, kind or "投研会")
    line = "%s %s｜%s" % (created[5:10].replace("-", "-"), label, conclusion)
    meta = {"source": os.path.abspath(minutes_path),
            "meeting_id": latest.get("id", ""),
            "meeting_at": created,
            "kind": kind,
            "title": str(latest.get("title", ""))}
    return line, meta, None


def load_jsonl(path, label="输入"):
    """读 JSONL 产物（可选源）：缺失 → None；坏行/非对象行 → fail-visible。

    与 verdicts/minutes 同语义：可选源缺失由调用方记 missing 继续跑，
    但存在而解析失败必须非零退出，禁静默吞坏产物。
    """
    if not os.path.isfile(path):
        return None
    recs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    _fail("%s 第 %d 行不是 JSON 对象：%s" % (label, i, path))
                recs.append(obj)
    except (OSError, ValueError) as e:
        _fail("%s 读取/解析失败 %s：%s" % (label, path, e))
    return recs


def linkage_shadow_view(recs, path):
    """linkage_shadow.jsonl → (view, source_meta, missing_note)。

    真身口径：linkage_collector.py 五信号影子采集（观察池=持仓+自选+共振
    TopN，非全市场扫描）。取最新 ref_date 的命中计数，只转述绝不编造；
    空产物/无 ref_date → (None, None, 说明)。
    """
    if not recs:
        return None, None, "linkage view: linkage_shadow.jsonl 无信号记录 %s" % path
    by_date = collections.defaultdict(list)
    for r in recs:
        d = str(r.get("ref_date") or "")
        if d:
            by_date[d].append(r)
    if not by_date:
        return None, None, "linkage view: linkage_shadow.jsonl 无 ref_date 字段 %s" % path
    latest = max(by_date)
    rows = by_date[latest]
    counts = collections.Counter(str(r.get("signal_name") or "?") for r in rows)
    # 信号名计数降序 → 名称升序（全确定性；五信号最多 5 类，全列）
    detail = "·".join("%s %d" % (n, c)
                      for n, c in sorted(counts.items(),
                                         key=lambda kv: (-kv[1], kv[0])))
    view = ("最近信号日 %s：五信号命中 %d 条（%s）；观察池口径（持仓+自选+共振 TopN），"
            "非全市场扫描" % (latest, len(rows), detail))
    meta = {"source": os.path.abspath(path), "ref_date": latest, "n_hits": len(rows)}
    return view, meta, None


def jev_gate_card(recs, path):
    """triples.jsonl → (jev 卡, source_meta, missing_note)。

    真身口径：jev-eval/signal_review_gate.py 五原子复核影子门。jev 是门
    不是选股策略师 → view=最近一次复核通过率+日期，top 留空不冒充选股；
    通过=非 REJECT（PASS/FOCUS/FOCUS_CN/WATCH 均为放行语义）。
    无已复核记录 → (None, None, 说明)。
    """
    if not recs:
        return None, None, "jev 卡: triples.jsonl 无记录 %s" % path
    reviewed_by_date = collections.defaultdict(list)
    for r in recs:
        if r.get("status") == "reviewed":
            d = str(r.get("pool_date") or "")
            if d:
                reviewed_by_date[d].append(r)
    if not reviewed_by_date:
        return None, None, "jev 卡: triples.jsonl 无已复核记录（status=reviewed）%s" % path
    latest = max(reviewed_by_date)
    rows = reviewed_by_date[latest]
    n = len(rows)
    rej = sum(1 for r in rows if r.get("decision") == "REJECT")
    passed = n - rej
    view = ("最近复核 %s：复核 %d 只，门通过 %d 只（通过率 %.1f%%）·否决 %d 只；"
            "影子·只标注不拦截" % (latest[5:10], n, passed, passed * 100.0 / n, rej))
    # 其后候选池存在但未复核 → 如实标注（绝不把未复核说成已复核）
    later = sorted({str(r.get("pool_date") or "") for r in recs
                    if str(r.get("pool_date") or "") > latest})
    if later:
        view += "；%s 起候选未复核" % later[0]
    card = {"id": "jev", "name": "Jev 信号复核门（AI 五原子）", "score": None,
            "picks_count": n, "top": [], "view": view}
    meta = {"source": os.path.abspath(path), "pool_date": latest,
            "n_reviewed": n, "n_rejected": rej}
    return card, meta, None


def _apply_linkage(strategists, recs, linkage_path, missing):
    """linkage 可选源接卡：产物存在 → 既有 linkage 卡 view 补实数。

    linkage 卡在 build_strategists 里已存在（n=0）→ 合并而非重复建卡；
    picks 无 linkage 策略时才追加新卡。缺产物/空产物 → 记 missing 继续跑。
    """
    if recs is None:
        missing.append("linkage view: linkage_shadow.jsonl 未找到"
                       "（可选联动源）%s" % linkage_path)
        return
    view, _lmeta, lmiss = linkage_shadow_view(recs, linkage_path)
    if lmiss:
        missing.append(lmiss)
    if not view:
        return
    card = next((c for c in strategists if c["id"] == "linkage"), None)
    if card is not None:
        card["view"] = view
    else:
        strategists.append({"id": "linkage", "name": "板块联动",
                            "score": None, "picks_count": 0,
                            "top": [], "view": view})


def _apply_jev(strategists, recs, jev_path, missing):
    """jev 可选源接卡：产物存在且有已复核记录 → 追加 jev 新卡。

    jev 是门不是选股策略师 → top 留空；无已复核记录 → 记 missing 继续跑。
    """
    if recs is None:
        missing.append("jev 卡: triples.jsonl 未找到"
                       "（可选复核门源）%s" % jev_path)
        return
    jcard, _jmeta, jmiss = jev_gate_card(recs, jev_path)
    if jmiss:
        missing.append(jmiss)
    if jcard is not None:
        strategists.append(jcard)


def build_wall(picks, verdicts, picks_path, verdicts_path, minutes_path=None,
               linkage_path=None, jev_path=None):
    missing = []
    # 策略师级 score：todays_picks/pick_verdicts 均无此口径 → null + 记缺失
    missing.append("strategist.score: 源文件无策略师级评分字段")
    missing.append("stock.score: 源文件无逐股评分字段")
    if not os.path.isfile(verdicts_path or ""):
        missing.append("pick_verdicts: 文件未找到（可选考核源）%s" % verdicts_path)
    if minutes_path is None:
        minutes_path = DEFAULT_MINUTES
    if linkage_path is None:
        linkage_path = DEFAULT_LINKAGE_SHADOW
    if jev_path is None:
        jev_path = DEFAULT_JEV_TRIPLES
    # 投研会一行：最新纪要「会议结论」原文引用（可选源，缺失留空记 missing）
    committee_line, committee_src, cmiss = extract_committee(minutes_path)
    if cmiss:
        missing.append(cmiss)
    strategists = build_strategists(picks, verdicts)
    # G1 自有产物（可选源，缺失/空产物 → 记 missing 继续跑，绝不编造）
    _apply_linkage(strategists, load_jsonl(linkage_path, label="linkage_shadow"),
                   linkage_path, missing)
    _apply_jev(strategists, load_jsonl(jev_path, label="jev_triples"),
               jev_path, missing)
    return {
        "generated_at": _iso8601_cn(picks["generated_at"]),
        "scan_run_date": str(picks["scan_run_date"]).replace("-", ""),
        "trade_basis": picks["trade_basis"],
        "t1_gate": picks["t1_gate"],
        "strategists": strategists,
        "markets": dict(MARKETS),
        "committee_line": committee_line or "",
        "provenance": {
            "todays_picks_path": os.path.abspath(picks_path),
            "generated_from": [os.path.abspath(p) for p in (picks_path, verdicts_path,
                               minutes_path, linkage_path, jev_path)
                               if p and os.path.isfile(p)],
            "committee_source": committee_src,
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
    ap.add_argument("--minutes", default=DEFAULT_MINUTES,
                    help="投研会纪要源（可选，默认 k_stock/paper/minutes.json）")
    ap.add_argument("--linkage", default=DEFAULT_LINKAGE_SHADOW,
                    help="linkage 影子采集源（可选，默认 "
                         "quant_backtest/data/linkage_shadow.jsonl）")
    ap.add_argument("--jev", default=DEFAULT_JEV_TRIPLES,
                    help="jev 复核门产物源（可选，默认 "
                         "jev-eval/review_data/triples.jsonl）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不落盘")
    args = ap.parse_args(argv)

    picks = load_todays_picks(args.todays_picks)
    verdicts = load_json(args.verdicts, required=False, label="pick_verdicts")
    if verdicts is not None and not isinstance(verdicts, dict):
        _fail("pick_verdicts 顶层不是对象：%s" % args.verdicts)
    wall = build_wall(picks, verdicts, args.todays_picks, args.verdicts,
                      args.minutes, args.linkage, args.jev)
    text = json.dumps(wall, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        sys.stdout.write(text)
        return
    atomic_write_json(args.out, wall)
    print("[wall_adapter] 已写出 %s（strategists=%d）"
          % (args.out, len(wall["strategists"])))


if __name__ == "__main__":
    main()
