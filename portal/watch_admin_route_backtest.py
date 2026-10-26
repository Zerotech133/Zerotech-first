# -*- coding: utf-8 -*-
"""盯盘只读管理页 —— 回测分面横向对比（只读路由）。

硬纪律（P1 特性，纯只读）：
  * 绝不写入任何文件、绝不发起网络请求、绝不调用 8091 / portal_api.py / 10.205.0.118。
  * 不触碰任何 BLOCK 项（飞书真实下发 / GAP-4 / 051 时序缺口 / 真实通知 / 交易）。
  * 不修改 watch_admin_server.py / watch_admin_page.html / watch_admin_snapshot.py。
  * fail-visible：数据源不可读时对应分块 verified=False + error_code，整体仍返回合法 dict。
  * 绝不编造：源数据缺 model/agent/combo 维度时，按该维度分面必须 verified=False +
    error_code="missing_dimension"，并附诚实说明；其余可做的分面正常 verified。
  * 输出不得含 NaN / Inf：非有限浮点规整为 None 后再返回（供 json.dumps 安全序列化）。

本模块由主服务（watch_admin_server.py）按如下契约动态加载：
  * API_ROUTES  = {"/api/watch-admin/backtest": backtest_api}
  * HTML_ROUTES = {"/watch-admin/backtest": BACKTEST_HTML}
其中 backtest_api(query: dict) -> dict 返回信封，含 last_backtest_facets 与 rotation_compare 两部分。
"""

from __future__ import annotations

import json
import math
import os
import statistics
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# 路径解析（相对本模块文件位置，避免依赖 cwd；并保留绝对兜底路径）
# --------------------------------------------------------------------------- #
_MODULE_DIR: str = os.path.dirname(os.path.abspath(__file__))
_QUANT_DIR: str = os.path.normpath(os.path.join(_MODULE_DIR, "..", "quant_backtest"))

LAST_BACKTEST_PATH: str = os.path.join(_QUANT_DIR, "last_backtest.json")
ROTATION_PATHS: Dict[str, str] = {
    "v6c": os.path.join(_QUANT_DIR, "chanlun_rotation_v6c_result.json"),
    "v6c2": os.path.join(_QUANT_DIR, "chanlun_rotation_v6c2_result.json"),
}

# --------------------------------------------------------------------------- #
# 维度常量（按契约）
# --------------------------------------------------------------------------- #
# last_backtest 可做的分面维度（仅这些字段存在）
FACET_DIMS: List[str] = ["strategy", "market", "mode"]
# last_backtest 真实数据缺失、绝不可编造的维度
MISSING_DIMS: List[str] = ["model", "agent", "combo"]
# rotation 横向对比关注的配置维度（按契约 C1–C4）
CONFIG_KEYS: List[str] = ["C1", "C2", "C3", "C4"]
# last_backtest 每行的数值指标键（用于分面均值）
RESULT_METRIC_KEYS: List[str] = [
    "sharpe",
    "total_return",
    "max_drawdown",
    "win_rate",
    "benchmark_return",
]


# --------------------------------------------------------------------------- #
# 基础工具：容错 JSON 读取 + 非有限浮点规整
# --------------------------------------------------------------------------- #
def _read_json_file(path: str) -> Any:
    """读取 JSON 文件，对源数据中可能存在的 NaN/Infinity 做容错处理。

    源文件（如 last_backtest.json）偶尔写入了非标准 JSON 字面量 NaN，
    标准 json.loads 会失败。这里在解析前将 NaN/Infinity/-Infinity 替换为 null，
    保证读取健壮（fail-visible 而非崩溃）。
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw: str = fh.read()
    sanitized: str = (
        raw.replace("NaN", "null")
        .replace("Infinity", "null")
        .replace("-Infinity", "null")
    )
    return json.loads(sanitized)


def _sanitize(obj: Any) -> Any:
    """递归规整：将 dict/list 中的非有限浮点（NaN/Inf）替换为 None。

    保证最终返回给主服务的 dict 经 json.dumps 不会产生 NaN/Inf。
    """
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _is_finite_num(v: Any) -> bool:
    """判断是否为有限数值（剔除 None / NaN / Inf）。"""
    return isinstance(v, (int, float)) and not (
        isinstance(v, float) and (math.isnan(v) or math.isinf(v))
    )


def _mean(values: List[Any]) -> Optional[float]:
    """对有限数值列表求均值；无有效数值时返回 None（绝不编造）。"""
    nums: List[float] = [float(v) for v in values if _is_finite_num(v)]
    if not nums:
        return None
    return sum(nums) / len(nums)


# --------------------------------------------------------------------------- #
# 指标计算（来自 equity_*/trades_* 原始曲线 / 成交单，均只读）
# --------------------------------------------------------------------------- #
def _equity_curve(equity: Any) -> List[float]:
    """从 equity_<Ck>（日期->净值 的 dict）抽取按日期排序的净值序列。"""
    if not isinstance(equity, dict) or not equity:
        return []
    keys: List[str] = sorted(equity.keys())
    curve: List[float] = []
    for k in keys:
        v = equity[k]
        if _is_finite_num(v) and float(v) > 0:
            curve.append(float(v))
    return curve


def _compute_return(curve: List[float]) -> Optional[float]:
    """累计收益率 = 末值/首值 - 1。"""
    if len(curve) < 2:
        return None
    first, last = curve[0], curve[-1]
    if first <= 0:
        return None
    return last / first - 1.0


def _compute_annualized(curve: List[float]) -> Optional[float]:
    """按 252 交易日年化收益率。"""
    if len(curve) < 2:
        return None
    total = _compute_return(curve)
    if total is None:
        return None
    periods = len(curve) - 1
    if periods <= 0:
        return None
    factor = 252.0 / periods
    if total <= -1.0:
        return None
    return (1.0 + total) ** factor - 1.0


def _compute_max_drawdown(curve: List[float]) -> Optional[float]:
    """最大回撤（负值，0 表示无回撤）。"""
    if not curve:
        return None
    peak: float = curve[0]
    mdd: float = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return mdd


def _compute_sharpe(curve: List[float], periods_per_year: int = 252) -> Optional[float]:
    """由净值曲线日收益计算年化夏普（无风险利率取 0）。

    样本不足或波动为 0 时返回 None（绝不编造）。
    """
    if len(curve) < 3:
        return None
    rets: List[float] = []
    for i in range(1, len(curve)):
        a, b = curve[i - 1], curve[i]
        if a <= 0:
            continue
        rets.append(b / a - 1.0)
    if len(rets) < 2:
        return None
    mean = statistics.fmean(rets)
    stdev = statistics.pstdev(rets)
    if stdev == 0:
        return None
    return (mean / stdev) * math.sqrt(periods_per_year)


def _compute_win_rate(trades: Any) -> Optional[float]:
    """由 trades_<Ck> 成交单的 pnl 计算胜率（pnl>0 占比）。"""
    if not isinstance(trades, list) or not trades:
        return None
    pnls: List[float] = []
    for t in trades:
        if isinstance(t, dict) and _is_finite_num(t.get("pnl")):
            pnls.append(float(t["pnl"]))
    if not pnls:
        return None
    wins = sum(1 for p in pnls if p > 0)
    return wins / len(pnls)


# --------------------------------------------------------------------------- #
# 分块 (a)：last_backtest 分面聚合
# --------------------------------------------------------------------------- #
def _row_dim_value(row: Dict[str, Any], meta: Dict[str, Any], dim: str) -> Any:
    """取某行在给定维度上的取值：优先行内字段，回退到顶层 meta 字段。"""
    if dim in row and row[dim] is not None:
        return row[dim]
    if dim in meta and meta[dim] is not None:
        return meta[dim]
    return None


def _facet_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对一组结果行计算 count 与数值指标均值（均值缺失时为 None）。"""
    return {
        "count": len(rows),
        "mean_sharpe": _mean([r.get("sharpe") for r in rows]),
        "mean_total_return": _mean([r.get("total_return") for r in rows]),
        "mean_max_drawdown": _mean([r.get("max_drawdown") for r in rows]),
        "mean_win_rate": _mean([r.get("win_rate") for r in rows]),
        "mean_benchmark_return": _mean([r.get("benchmark_return") for r in rows]),
    }


def _build_last_backtest_facets(query: Dict[str, Any]) -> Dict[str, Any]:
    """构建 last_backtest 分面聚合。

    关键纪律：若请求按 model/agent/combo 分面（这些维度源数据不存在），
    必须 verified=False + error_code="missing_dimension"，绝不编造。
    """
    requested: Any = None
    for key in ("dimension", "facet", "dim"):
        if key in query and query[key] is not None:
            requested = str(query[key]).strip().lower()
            break

    # 请求了缺失维度 -> 直接 fail-visible，绝不编造分面
    if requested in MISSING_DIMS:
        return {
            "verified": False,
            "error_code": "missing_dimension",
            "note": "源数据无该维度，无法分面，绝不编造",
            "requested_dimension": requested,
            "available_dimensions": list(FACET_DIMS),
            "missing_dimensions": list(MISSING_DIMS),
            "facets": {},
        }

    # 文件不可读 -> verified=False + error_code，整体仍返回合法 dict
    if not os.path.exists(LAST_BACKTEST_PATH):
        return {
            "verified": False,
            "error_code": "source_missing",
            "note": "last_backtest.json 不存在或不可读",
            "source": LAST_BACKTEST_PATH,
            "requested_dimension": requested,
            "available_dimensions": list(FACET_DIMS),
            "missing_dimensions": list(MISSING_DIMS),
            "facets": {},
        }

    try:
        data: Dict[str, Any] = _read_json_file(LAST_BACKTEST_PATH)
    except Exception as exc:  # noqa: BLE001 - 读取异常需 fail-visible
        return {
            "verified": False,
            "error_code": "read_error",
            "note": "读取 last_backtest.json 失败: %s" % exc,
            "source": LAST_BACKTEST_PATH,
            "requested_dimension": requested,
            "available_dimensions": list(FACET_DIMS),
            "missing_dimensions": list(MISSING_DIMS),
            "facets": {},
        }

    meta: Dict[str, Any] = {
        "ts": data.get("ts"),
        "strategy": data.get("strategy"),
        "market": data.get("market"),
        "mode": data.get("mode"),
    }
    results: List[Dict[str, Any]] = data.get("results") or []
    if not isinstance(results, list):
        results = []

    # 按 strategy / market / mode 分面聚合（维度值缺失时回退到顶层 meta）
    facets: Dict[str, Any] = {}
    for dim in FACET_DIMS:
        buckets: Dict[Any, List[Dict[str, Any]]] = {}
        for row in results:
            if not isinstance(row, dict):
                continue
            val = _row_dim_value(row, meta, dim)
            key = val if val is not None else "__none__"
            buckets.setdefault(key, []).append(row)
        groups: List[Dict[str, Any]] = []
        for key, rows in buckets.items():
            grp = {"dimension": dim, "value": (None if key == "__none__" else key)}
            grp.update(_facet_group(rows))
            groups.append(grp)
        groups.sort(key=lambda g: (g["value"] is None, str(g["value"])))
        facets[dim] = groups

    return {
        "verified": True,
        "error_code": None,
        "note": None,
        "source": LAST_BACKTEST_PATH,
        "ts": meta.get("ts"),
        "strategy": meta.get("strategy"),
        "market": meta.get("market"),
        "mode": meta.get("mode"),
        "available_dimensions": list(FACET_DIMS),
        "missing_dimensions": list(MISSING_DIMS),
        "requested_dimension": requested,
        "result_count": len(results),
        "facets": facets,
    }


# --------------------------------------------------------------------------- #
# 分块 (b)：两个 chanlun_rotation 结果按 C1–C4 横向对比
# --------------------------------------------------------------------------- #
def _compute_config_metrics(data: Dict[str, Any], cfg: str) -> Dict[str, Any]:
    """对单个配置文件（C1..C4）抽取 equity_*/trades_* 并计算指标。

    优先级：equity 曲线 -> 计算 收益/年化/最大回撤/夏普；trades -> 计算胜率；
    缺失原始数据时回退到 Ck 摘要 dict（同源真实值），并逐指标标注来源；
    均不可得时为 None（绝不编造）。
    """
    summary: Any = data.get(cfg)
    if not isinstance(summary, dict):
        summary = {}

    equity: Any = data.get("equity_" + cfg)
    trades: Any = data.get("trades_" + cfg)
    curve: List[float] = _equity_curve(equity)

    total_return: Optional[float]
    total_return_src: str
    if curve:
        total_return = _compute_return(curve)
        total_return_src = "equity_curve"
    elif _is_finite_num(summary.get("total_return_pct")):
        total_return = float(summary["total_return_pct"]) / 100.0
        total_return_src = "summary"
    else:
        total_return = None
        total_return_src = "none"

    max_dd: Optional[float]
    max_dd_src: str
    if curve:
        max_dd = _compute_max_drawdown(curve)
        max_dd_src = "equity_curve"
    elif _is_finite_num(summary.get("max_drawdown_pct")):
        max_dd = float(summary["max_drawdown_pct"]) / 100.0
        max_dd_src = "summary"
    else:
        max_dd = None
        max_dd_src = "none"

    sharpe: Optional[float] = _compute_sharpe(curve) if curve else None
    sharpe_src: str = "equity_curve" if sharpe is not None else "none"

    annual: Optional[float]
    annual_src: str
    if curve:
        annual = _compute_annualized(curve)
        annual_src = "equity_curve"
    elif _is_finite_num(summary.get("annualized_pct")):
        annual = float(summary["annualized_pct"]) / 100.0
        annual_src = "summary"
    else:
        annual = None
        annual_src = "none"

    win_rate: Optional[float]
    win_src: str
    if isinstance(trades, list) and trades:
        win_rate = _compute_win_rate(trades)
        win_src = "trades" if win_rate is not None else "summary"
        if win_rate is None and _is_finite_num(summary.get("win_rate_pct")):
            win_rate = float(summary["win_rate_pct"]) / 100.0
            win_src = "summary"
    elif _is_finite_num(summary.get("win_rate_pct")):
        win_rate = float(summary["win_rate_pct"]) / 100.0
        win_src = "summary"
    else:
        win_rate = None
        win_src = "none"

    return {
        "label": summary.get("label"),
        "available": True,
        "total_return": total_return,
        "total_return_source": total_return_src,
        "annualized_return": annual,
        "annualized_source": annual_src,
        "max_drawdown": max_dd,
        "max_drawdown_source": max_dd_src,
        "sharpe": sharpe,
        "sharpe_source": sharpe_src,
        "win_rate": win_rate,
        "win_rate_source": win_src,
    }


def _build_rotation_compare() -> Dict[str, Any]:
    """构建两个 chanlun_rotation 结果按 C1–C4 的横向对比。

    文件不可读时对应部分 verified=False + error_code，整体仍返回合法 dict。
    """
    loaded: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}
    for key, path in ROTATION_PATHS.items():
        if not os.path.exists(path):
            errors[key] = "source_missing"
            continue
        try:
            loaded[key] = _read_json_file(path)
        except Exception as exc:  # noqa: BLE001 - 读取异常需 fail-visible
            errors[key] = "read_error:%s" % exc

    rows: List[Dict[str, Any]] = []
    for cfg in CONFIG_KEYS:
        row: Dict[str, Any] = {"config": cfg}
        for fk in ROTATION_PATHS.keys():
            if fk not in loaded:
                row[fk] = {
                    "available": False,
                    "error_code": errors.get(fk, "source_missing"),
                }
            else:
                row[fk] = _compute_config_metrics(loaded[fk], cfg)
        rows.append(row)

    verified: bool = len(errors) == 0
    if verified:
        error_code: Optional[str] = None
        note: Optional[str] = None
    else:
        error_code = "source_missing"
        note = "部分源文件不可读: " + ", ".join(sorted(errors.keys()))

    return {
        "verified": verified,
        "error_code": error_code,
        "note": note,
        "files": {k: v for k, v in ROTATION_PATHS.items()},
        "configs": list(CONFIG_KEYS),
        "metric_keys": [
            "total_return",
            "annualized_return",
            "max_drawdown",
            "sharpe",
            "win_rate",
        ],
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def backtest_api(query: Dict[str, Any]) -> Dict[str, Any]:
    """回测分面横向对比 API 处理函数。

    Args:
        query: 请求参数 dict（如 {"dimension": "market"}）。
               允许 dimension/facet/dim 指定 last_backtest 分面维度；
               若指定 model/agent/combo 则返回 missing_dimension（绝不编造）。

    Returns:
        信封 dict，含 last_backtest_facets 与 rotation_compare 两部分；
        所有非有限浮点已规整为 None，可直接 json.dumps。
    """
    if not isinstance(query, dict):
        query = {}

    last_facets: Dict[str, Any] = _build_last_backtest_facets(query)
    rotation: Dict[str, Any] = _build_rotation_compare()

    envelope: Dict[str, Any] = {
        "last_backtest_facets": last_facets,
        "rotation_compare": rotation,
        "meta": {
            "feature": "watch-admin-backtest",
            "read_only": True,
            "data_dir": _QUANT_DIR,
        },
    }
    return _sanitize(envelope)


# --------------------------------------------------------------------------- #
# 只读 HTML 页面（深海军蓝风格，禁顶部胶囊导航）
# --------------------------------------------------------------------------- #
BACKTEST_HTML: str = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>回测分面横向对比 · 盯盘只读</title>
<style>
  :root{
    --navy:#0e1b2a; --navy2:#13263a; --cyan:#27c9c9; --amber:#e0a23c;
    --line:#1f3a52; --txt:#cfe3ee; --muted:#7e98ab; --red:#e06a6a; --green:#5fcf9b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--navy);color:var(--txt);
       font-family:"Microsoft YaHei",system-ui,sans-serif;}
  header{padding:18px 24px;border-bottom:1px solid var(--line);
         background:linear-gradient(180deg,var(--navy2),var(--navy));}
  header h1{margin:0;font-size:20px;color:var(--cyan);letter-spacing:.5px;}
  header .sub{margin-top:4px;color:var(--muted);font-size:12px;}
  main{padding:20px 24px;max-width:1180px;margin:0 auto;}
  section{margin-bottom:28px;}
  h2{font-size:15px;color:var(--amber);border-left:3px solid var(--amber);
     padding-left:8px;margin:0 0 12px;}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
         border:1px solid var(--line);margin-left:6px;}
  .ok{color:var(--green);border-color:var(--green);}
  .bad{color:var(--red);border-color:var(--red);background:rgba(224,106,106,.08);}
  .unverified{color:var(--amber);border-color:var(--amber);
              background:rgba(224,162,60,.10);font-weight:bold;}
  .meta{color:var(--muted);font-size:12px;margin-bottom:10px;}
  table{width:100%;border-collapse:collapse;font-size:13px;
        background:var(--navy2);border:1px solid var(--line);}
  th,td{padding:8px 10px;border:1px solid var(--line);text-align:right;}
  th:first-child,td:first-child{text-align:left;}
  thead th{color:var(--cyan);background:rgba(39,201,201,.08);}
  .pos{color:var(--green);} .neg{color:var(--red);} .na{color:var(--muted);}
  .note{font-size:12px;color:var(--muted);margin:6px 0 0;}
  .src-tag{font-size:10px;color:var(--muted);margin-left:3px;}
  .err{padding:16px;border:1px solid var(--red);color:var(--red);
       background:rgba(224,106,106,.06);border-radius:6px;}
  .legend{font-size:11px;color:var(--muted);margin-top:6px;}
  code{color:var(--amber);}
</style>
</head>
<body>
<header>
  <h1>回测分面横向对比</h1>
  <div class="sub">盯盘只读管理页 · 纯只读快照 · 数据源：quant_backtest · 绝不编造</div>
</header>
<main>
  <div id="status"></div>

  <section>
    <h2>last_backtest 分面聚合 <span id="lb-badge" class="badge"></span></h2>
    <div id="lb-body"></div>
  </section>

  <section>
    <h2>chanlun_rotation 横向对比（C1–C4） <span id="rc-badge" class="badge"></span></h2>
    <div id="rc-body"></div>
  </section>
</main>

<script>
function fmt(v, pct){
  if(v===null||v===undefined) return '<span class="na">—</span>';
  var s = (pct? (v*100).toFixed(2)+'%' : v.toFixed(3));
  if(v>0) return '<span class="pos">'+s+'</span>';
  if(v<0) return '<span class="neg">'+s+'</span>';
  return s;
}
function setBadge(el, part){
  if(part.verified===true){ el.className='badge ok'; el.textContent='VERIFIED'; }
  else if(part.error_code==='missing_dimension'){
    el.className='badge unverified'; el.textContent='SOURCE_UNVERIFIED（missing_dimension）';
  } else { el.className='badge bad'; el.textContent='SOURCE_UNVERIFIED（'+(part.error_code||'error')+'）'; }
}
function renderLastBacktest(d){
  var el = document.getElementById('lb-body');
  if(d.verified!==true){
    var msg = '<div class="err">分面不可用：'+(d.note||d.error_code||'未知错误')+'</div>';
    msg += '<div class="note">缺维度：'+((d.missing_dimensions||[]).join(' / ')||'无')+
           ' —— <span class="unverified" style="padding:1px 6px;border:1px solid var(--amber);border-radius:8px;">SOURCE_UNVERIFIED（missing_dimension）</span></div>';
    el.innerHTML = msg; return;
  }
  var html = '';
  html += '<div class="meta">ts='+(d.ts||'-')+' · strategy='+(d.strategy||'-')+
          ' · market='+(d.market||'-')+' · mode='+(d.mode||'-')+
          ' · 结果数='+(d.result_count||0)+'</div>';
  html += '<div class="note">可做分面维度：'+((d.available_dimensions||[]).join(' / '))+
          ' ；缺失维度（绝不编造）：'+((d.missing_dimensions||[]).join(' / '))+
          ' <span class="unverified" style="padding:1px 6px;border:1px solid var(--amber);border-radius:8px;">SOURCE_UNVERIFIED（missing_dimension）</span></div>';
  var facets = d.facets||{};
  ['strategy','market','mode'].forEach(function(dim){
    var groups = facets[dim]||[];
    html += '<h3 style="color:var(--cyan);font-size:13px;margin:14px 0 6px;">按 '+dim+' 分面（'+groups.length+' 组）</h3>';
    if(!groups.length){ html += '<div class="note">无分面数据</div>'; return; }
    html += '<table><thead><tr><th>'+dim+'</th><th>count</th><th>均值 sharpe</th>'+
            '<th>均值 收益</th><th>均值 最大回撤</th><th>均值 胜率</th><th>均值 基准收益</th></tr></thead><tbody>';
    groups.forEach(function(g){
      html += '<tr><td>'+(g.value===null?'<span class="na">null</span>':g.value)+'</td>'+
              '<td>'+g.count+'</td>'+
              '<td>'+fmt(g.mean_sharpe)+'</td>'+
              '<td>'+fmt(g.mean_total_return,true)+'</td>'+
              '<td>'+fmt(g.mean_max_drawdown,true)+'</td>'+
              '<td>'+fmt(g.mean_win_rate,true)+'</td>'+
              '<td>'+fmt(g.mean_benchmark_return,true)+'</td></tr>';
    });
    html += '</tbody></table>';
  });
  el.innerHTML = html;
}
function renderRotation(d){
  var el = document.getElementById('rc-body');
  if(d.verified!==true){
    el.innerHTML = '<div class="err">横向对比不可用：'+(d.note||d.error_code||'未知错误')+'</div>';
    return;
  }
  var rows = d.rows||[];
  var html = '<table><thead><tr>'+
    '<th>配置</th><th>标签(v6c)</th>'+
    '<th>总收益 v6c</th><th>夏普 v6c</th><th>最大回撤 v6c</th><th>胜率 v6c</th>'+
    '<th>总收益 v6c2</th><th>夏普 v6c2</th><th>最大回撤 v6c2</th><th>胜率 v6c2</th>'+
    '</tr></thead><tbody>';
  rows.forEach(function(r){
    var a = r.v6c||{}, b = r.v6c2||{};
    function cell(m, key, pct){
      if(!m || m.available!==true) return '<td class="na">—</td>';
      var src = m[key+'_source']||'';
      var tag = src==='equity_curve'?'<span class="src-tag">[曲线]</span>'
               : src==='trades'?'<span class="src-tag">[成交]</span>'
               : src==='summary'?'<span class="src-tag">[摘要]</span>':'';
      return '<td>'+fmt(m[key],pct)+tag+'</td>';
    }
    html += '<tr><td>'+r.config+'</td><td style="text-align:left;color:var(--muted)">'+
            (a.label||b.label||'-')+'</td>'+
            cell(a,'total_return',true)+cell(a,'sharpe',false)+cell(a,'max_drawdown',true)+cell(a,'win_rate',true)+
            cell(b,'total_return',true)+cell(b,'sharpe',false)+cell(b,'max_drawdown',true)+cell(b,'win_rate',true)+
            '</tr>';
  });
  html += '</tbody></table>';
  html += '<div class="legend">标注： [曲线]=由 equity_&lt;Ck&gt; 净值曲线计算；[成交]=由 trades_&lt;Ck&gt; 成交单计算；[摘要]=源文件 Ck 摘要字段（真实值）。缺原始数据时对应指标为 <span class="na">—</span>（绝不编造）。</div>';
  el.innerHTML = html;
}
function load(){
  fetch('/api/watch-admin/backtest')
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(d){
      var lb = d.last_backtest_facets||{};
      var rc = d.rotation_compare||{};
      setBadge(document.getElementById('lb-badge'), lb);
      setBadge(document.getElementById('rc-badge'), rc);
      renderLastBacktest(lb);
      renderRotation(rc);
      document.getElementById('status').innerHTML = '';
    })
    .catch(function(e){
      document.getElementById('status').innerHTML =
        '<div class="err">快照获取失败：'+(e.message||e)+'</div>';
    });
}
load();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# 动态加载契约
# --------------------------------------------------------------------------- #
API_ROUTES: Dict[str, Any] = {
    "/api/watch-admin/backtest": backtest_api,
}

HTML_ROUTES: Dict[str, str] = {
    "/watch-admin/backtest": BACKTEST_HTML,
}
