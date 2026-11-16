# -*- coding: utf-8 -*-
"""宿主首页 · 三平台今日一句

产出两件东西：
  1) portal/data/platform_today.json —— 机读快照（生成物，已 gitignore）
  2) portal/home_v3.html —— 由 _templates/home_v3.src.html 渲染而成（生成物，已 gitignore）

设计判据：
1) 只聚合，不重算。每个数字都必须是某平台自己的产物里已有的值，逐字复制。
2) 一个平台读不到 -> 该平台记 unknown，其余照常出。整页不能因一家坏掉而空。
3) 日期一律带 asof，落后交易日数按 DAILY 分片文件名算，不用 mtime、不用 today。
4) 不写任何 verdict 之外的判断词。首页不替平台下结论。
5) 页面结构归模板（入库），数据归产物（不入库）—— 否则数据每天变、
   工作区每天 M，git 的变更信号就被噪声淹了。

三平台的数据真源：
  侯风    daily_report/build_houfeng_today.py 的 build()（复用，不重写逻辑）
  缠论    portal/observe_board.json
  中台    deliverables/quant/data/system_health.json

用法：python build_platform_today.py [--out <path>]
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # 老田的AI助手
PROJ = os.path.dirname(ROOT)                      # 项目
DAILY = os.path.join(ROOT, "quant_backtest", "data", "tushare", "daily")
QUANT = os.path.join(PROJ, "deliverables", "quant", "data")
OUT_DEFAULT = os.path.join(HERE, "data", "platform_today.json")


# ---------------------------------------------------------------- 基础设施
def trading_days() -> list:
    """交易日序列：取 daily 分片文件名里的日期（文件名即日期，不是 mtime）。"""
    out = []
    for f in glob.glob(os.path.join(DAILY, "d_*.parquet")):
        m = re.search(r"d_(\d{8})\.parquet$", os.path.basename(f))
        if m:
            out.append(m.group(1))
    return sorted(set(out))


def lag_of(asof: str, days: list) -> int | None:
    """asof 落后最新交易日几个交易日。asof 不在序列里返回 None（不猜）。"""
    if not asof or not days:
        return None
    if asof not in days:
        return None
    return len(days) - 1 - days.index(asof)


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def fmt_d(s: str) -> str:
    s = str(s or "")
    return f"{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else (s or "—")


def _load_houfeng_build():
    """按真实路径加载侯风生成器，复用它的 build()，不复制其内部逻辑。"""
    p = os.path.join(HERE, "daily_report", "build_houfeng_today.py")
    if not os.path.exists(p):
        return None
    spec = importlib.util.spec_from_file_location("_hf_today_for_host", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 三平台
def houfeng(days: list) -> dict:
    base = {
        "id": "houfeng",
        "name": "侯风",
        "role": "选票 · 对外产品层",
        "url": "/houfeng/HOUFENG_TODAY.html",
        "url_note": "今日状态页",
    }
    mod = _load_houfeng_build()
    if mod is None:
        return dict(base, state="unknown", verdict="读取器缺失",
                    numbers=[], data_date=None, lag=None,
                    detail="daily_report/build_houfeng_today.py 不存在")
    try:
        d = mod.build()
    except Exception as e:                                    # noqa: BLE001
        return dict(base, state="unknown", verdict="读取失败",
                    numbers=[], data_date=None, lag=None,
                    detail=f"{type(e).__name__}: {e}")

    w = d.get("watch") or {}
    ver = d.get("verdict")
    if ver is None:
        state, verdict = "unknown", "今日状态未采集"
    elif ver == "PICK":
        state, verdict = "pick", "今日出票"
    elif ver == "CANDIDATES-ONLY":
        state, verdict = "watch", "今日不出票 · 有候选"
    elif ver == "WATCH-ONLY":
        state, verdict = "watch", "今日无票 · 仅观察池"
    elif ver == "NO-PICK":
        state, verdict = "none", "今日无票"
    else:
        state, verdict = "unknown", f"未识别的结论：{ver}"

    return dict(
        base,
        state=state,
        verdict=verdict,
        data_date=d.get("asof"),
        lag=lag_of(d.get("asof"), days),
        numbers=[
            {"label": "可买批次", "v": str(d.get("picks") or 0)},
            # build() 的 cand 是 dict（asof/files/n/conc），取 n；旧版是 int，两种都容错
            {"label": "候选篮", "v": str((d.get("cand") or {}).get("n", 0)
                                        if isinstance(d.get("cand"), dict)
                                        else (d.get("cand") or 0))},
            {"label": "观察池", "v": str(w.get("n") or 0)},
        ],
        detail=(f"最后批次 {fmt_d(d.get('last_pick_date'))}"
                if d.get("last_pick_date") else "从未产出批次"),
    )


def chanlun(days: list) -> dict:
    base = {
        "id": "chanlun",
        "name": "缠论",
        "role": "结构池 · 内部研究板",
        "url": "/chanlun_board.html",
        "url_note": "结构池与模拟仓",
    }
    d = read_json(os.path.join(HERE, "observe_board.json"))
    if not d:
        return dict(base, state="unknown", verdict="状态文件读不到",
                    numbers=[], data_date=None, lag=None,
                    detail="portal/observe_board.json 不存在或损坏")
    r = d.get("regime") or {}
    c = d.get("counts") or {}
    asof = d.get("asof")
    return dict(
        base,
        state="ok",
        verdict=f"大盘 {r.get('grade', '?')} · {r.get('gate', '?')}",
        data_date=asof,
        lag=lag_of(asof, days),
        numbers=[
            {"label": "结构池", "v": str(d.get("n") or 0)},
            {"label": "A类", "v": str(c.get("a") or 0)},
            {"label": "六条件全过", "v": str(c.get("full") or 0)},
        ],
        detail=f"20日动量 {r.get('mom20_pct', '?')}%",
    )


def qengine(days: list) -> dict:
    base = {
        "id": "qengine",
        "name": "策略中台",
        "role": "判据与产物 · 内部研究台",
        "url": "",                 # 8080 上暂无出口，见 detail；不填假链接
        "url_note": "本地 8790 未对外挂载",
    }
    d = read_json(os.path.join(QUANT, "system_health.json"))
    if not d:
        return dict(base, state="unknown", verdict="健康台账读不到",
                    numbers=[], data_date=None, lag=None,
                    detail="deliverables/quant/data/system_health.json 不存在")
    ss = d.get("source_summary") or {}
    gs = d.get("gap_summary") or {}
    asof = d.get("latest_trade_date")
    bad = (ss.get("stale") or 0) + (ss.get("unknown") or 0)
    return dict(
        base,
        state="ok" if not bad else "warn",
        verdict=("数据源全部正常" if not bad else f"数据源 {bad} 项异常"),
        data_date=asof,
        lag=lag_of(asof, days),
        numbers=[
            {"label": "数据源正常", "v": f"{ss.get('ok', 0)}/{sum(ss.get(k, 0) or 0 for k in ('ok', 'lag', 'stale', 'unknown'))}"},
            {"label": "P0 缺口", "v": str(gs.get("P0") or 0)},
            {"label": "P1 缺口", "v": str(gs.get("P1") or 0)},
        ],
        detail=f"体检于 {str(d.get('collected_at') or '')[5:16].replace('T', ' ')}",
    )


def build() -> dict:
    days = trading_days()
    rows = []
    for fn in (houfeng, chanlun, qengine):
        try:
            rows.append(fn(days))
        except Exception as e:                                # noqa: BLE001
            rows.append({"id": fn.__name__, "name": fn.__name__, "role": "",
                         "url": "", "state": "unknown", "verdict": "读取异常",
                         "numbers": [], "data_date": None, "lag": None,
                         "detail": f"{type(e).__name__}: {e}"})
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": days[-1] if days else None,
        "asof_text": fmt_d(days[-1]) if days else "—",
        "n_trading_days": len(days),
        "platforms": rows,
    }


# ---------------------------------------------------------------- 渲染首页
# 模板 / 产物分离（2026-11-16）：
#   数据内联在页面里（该页既有约定：不依赖外部数据桥），而数据每天变 ——
#   如果页面本身入库，工作区就会每天显示 M，`git add -A` 会顺带把数据噪声带走。
#   所以：模板 _templates/home_v3.src.html 入库（页面结构有版本历史），
#   产物 portal/home_v3.html 是生成物、不入库（.gitignore）。
#   要改页面结构改模板；直接改产物会在下次生成时被覆盖。
HOME_SRC = os.path.join(HERE, "_templates", "home_v3.src.html")
HOME_OUT = os.path.join(HERE, "home_v3.html")
PLAT_RE = re.compile(r'(<script id="plat-today" type="application/json">)(.*?)(</script>)', re.S)


def render_home(d: dict, strict: bool = True) -> tuple[bool, str]:
    """模板 -> 产物。模板只读，产物只写。

    写后读回校验：块能解析、平台数为 3、BOM 与总字节结构未被破坏。

    strict=False 仅供变异检验注入刻意异常的数据（空平台、多余平台）。
    生产调用方必须保持默认 True。
    """
    assert os.path.abspath(HOME_SRC) != os.path.abspath(HOME_OUT), "模板与产物不能是同一文件"
    if not os.path.exists(HOME_SRC):
        return False, "模板 _templates/home_v3.src.html 不存在（产物未重建，原产物保持不动）"
    raw = open(HOME_SRC, "rb").read()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    n = len(PLAT_RE.findall(text))
    if n != 1:
        return False, f"模板锚点应唯一，实际 {n} 处"
    body = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    body = body.replace("</", "<\\/")          # 防止提前闭合 script
    new = PLAT_RE.sub(lambda m: m.group(1) + body + m.group(3), text, count=1)
    out = new.encode("utf-8")
    open(HOME_OUT, "wb").write((b"\xef\xbb\xbf" + out) if has_bom else out)

    # 读回校验（对产物，不是对模板）
    back = open(HOME_OUT, "rb").read()
    assert (back[:3] == b"\xef\xbb\xbf") == has_bom, "BOM 状态被改动"
    bt = back.decode("utf-8-sig")
    m = PLAT_RE.search(bt)
    if not m:
        return False, "写入后锚点丢失"
    got = json.loads(m.group(2).replace("<\\/", "</"))
    if strict:
        if len(got.get("platforms") or []) != 3:
            return False, f"回读平台数 {len(got.get('platforms') or [])} != 3"
        if got.get("asof") != d.get("asof"):
            return False, f"回读 asof {got.get('asof')} != {d.get('asof')}"
    return True, f"内联 {len(body)}B 已注入（{os.path.basename(HOME_OUT)}，共 {len(back)}B）"


def main() -> int:
    out = OUT_DEFAULT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    d = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    # 自检：三个平台必须齐、每个都必须有 state 与 data_date 字段（值可为 None）
    assert len(d["platforms"]) == 3, d
    for p in d["platforms"]:
        assert "state" in p and "data_date" in p and "verdict" in p, p

    ok, msg = render_home(d)
    print(f"OK {out}  asof={d['asof']}  "
          + "  ".join(f"{p['id']}={p['state']}/lag={p['lag']}" for p in d["platforms"]))
    print(f"{'HOME ' + msg if ok else 'HOME-FAIL ' + msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
