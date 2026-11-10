# -*- coding: utf-8 -*-
"""候风每日标准作业 · 唯一入口（HOUFENG DAILY RUNNER）

设计原则（老田 2026-11-09 定）：
  1. 唯一口径 —— 只认 M2_FROZEN.json，不提供池切换、不提供口径降级；
  2. 不迭代 —— 参数一律从冻结文件读，本文件不写任何策略默认值；
  3. 响亮失败 —— 引擎漂移 / 数据不全 / 产物异常 一律非零退出，绝不"少一点也能出"；
  4. 状态化输出 —— 闸门关闭是合法状态（只观察），不是故障；但绝不因此改口径。

每天只做一件事：
    python daily_run.py                 # 信号日=数据最新交易日
    python daily_run.py --signal 20261106

退出码：
    0  正常（含闸门关闭只出观察名单）
    2  参数/环境错误（含子进程超时）
    3  引擎偏离冻结版（md5 不符）—— 最高优先级，先解决再谈选股
    4  数据不全 / 信号日不新鲜
    5  产物异常（picks 数与闸门口径矛盾）
    6  选股子进程执行失败

2026-11-09 修订（只修实现缺陷，未动任何策略口径）：
  · check_data 原读 data/ashare/daily.parquet —— 该文件不存在，真实数据是
    5816 个分股票 parquet。原实现使数据门禁恒定 exit 4，且 --signal 缺省
    自动取交易日同样走该路径（exit 2）。改为扫分文件 trade_date 列（9s）。
  · MIN_COVER 原含 moneyflow / daily_basic —— 分文件无这两类列，且候风引擎
    （load_wide → build_matrices）根本不读它们，属抄错的依赖清单。移出。
  · form 值全为字符串，bool("False") 恒真；改 _as_bool 显式解析。
  · 增加信号日新鲜度校验：拒绝用过期数据当"今天"出票。
  · 增加运行日志落盘、失败落盘、与上一交易日 diff、rejected 提醒、超时捕获。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
FROZEN = os.path.join(HERE, "M2_FROZEN.json")
PICK_LIVE = os.path.join(HERE, "paper", "_pick_live.py")
OUT_DIR = os.path.join(HERE, "daily")
LOG_DIR = os.path.join(OUT_DIR, "logs")
ENGINE = os.path.join(ROOT, "qpzm_backtest.py")
PSIM = os.path.join(ROOT, "portfolio_sim.py")
DATA_DIR = os.path.join(ROOT, "data", "ashare")
NAMES = os.path.join(DATA_DIR, "_names.json")

# 数据完整性下限：信号日必须有足够覆盖，否则当日选股无意义。
# 只有 daily 一项：候风引擎 load_wide 只读分股票 parquet 的
# open/high/low/close/pre_close/volume，不读 moneyflow / daily_basic。
MIN_COVER = {"daily": 5000}

# 信号日距今天超过此自然日数 → 判定数据断供（覆盖国庆/春节最长假 9 天）
MAX_STALE_DAYS = 10


# ────────────────────────── 基础工具 ──────────────────────────

def md5_8(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _as_bool(v, default=None):
    """显式布尔解析。M2_FROZEN 的 form 值全是字符串，
    bool("False") 为 True 会造成"看着关了其实还开着"。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "on", "是"):
        return True
    if s in ("false", "0", "no", "n", "off", "否"):
        return False
    if default is not None:
        return default
    raise ValueError("无法解析为布尔：%r" % (v,))


def parse_fingerprint(text, label):
    """从 'qpzm_backtest.py（逐笔锚，md5 f6782e33，...）' 里取 8 位 md5。"""
    if not text:
        return None
    m = re.search(r"md5\s+([0-9a-fA-F]{8})", text)
    if not m:
        m = re.search(r"\b([0-9a-f]{8})\b", text)
    return m.group(1).lower() if m else None


def ymd_to_date(s):
    s = str(int(s))
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])


def days_between(d_old, d_new):
    """两个 YYYYMMDD 之间相差的自然日数。"""
    def _t(x):
        x = str(int(x))
        return time.mktime(time.strptime(x, "%Y%m%d"))
    return int(round((_t(d_new) - _t(d_old)) / 86400.0))


class Tee(object):
    """stdout 同时落盘。长跑脚本必须有日志文件，否则事后无从复盘。"""

    def __init__(self, path):
        self.fp = open(path, "w", encoding="utf-8")

    def write(self, s):
        sys.__stdout__.write(s)
        self.fp.write(s)
        self.fp.flush()

    def flush(self):
        sys.__stdout__.flush()
        self.fp.flush()

    def close(self):
        try:
            self.fp.close()
        except Exception:
            pass


def write_fail(signal, code, stage, errs, extra=None):
    """失败落盘：exit 3/4/5/6 时留下痕迹，次日可追溯「昨天为什么没出票」。"""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, "_FAIL_%s_%s.json" % (signal or "auto",
                                                        time.strftime("%Y%m%d_%H%M%S")))
        body = {
            "schema": "houfeng.daily.fail/v1",
            "signal_date": signal,
            "exit_code": code,
            "stage": stage,
            "errors": errs,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if extra:
            body.update(extra)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        print("[fail] 已落盘 %s" % p, flush=True)
    except Exception as e:  # 落盘失败不掩盖主错误
        print("[fail] 落盘失败（不影响退出码）：%s" % e, flush=True)


# ────────────────────────── 门禁一：冻结标准 ──────────────────────────

def load_frozen():
    with open(FROZEN, encoding="utf-8") as f:
        return json.load(f)


def check_frozen(fr):
    """返回 (ok, errors, params)。指纹不符 → 直接拒绝，不降级。"""
    form = fr.get("form") or {}
    errs = []
    eng_want = parse_fingerprint(form.get("engine"), "engine")
    psim_want = parse_fingerprint(form.get("portfolio_sim"), "portfolio_sim")
    eng_got = md5_8(ENGINE) if os.path.exists(ENGINE) else "missing"
    psim_got = md5_8(PSIM) if os.path.exists(PSIM) else "missing"

    if eng_want != eng_got:
        errs.append("qpzm_backtest.py  期望 %s / 实际 %s" % (eng_want, eng_got))
    if psim_want != psim_got:
        errs.append("portfolio_sim.py  期望 %s / 实际 %s" % (psim_want, psim_got))

    params = {
        "top_n": int(form.get("top_n")),
        "hold": int(form.get("hold")),
        "cost": float(form.get("cost_roundtrip")),
        "max_weight": float(form.get("max_weight")),
        "capital": float(form.get("capital_ref")),
        "drop_p1": _as_bool(form.get("drop_p1"), False),
        "rank": form.get("rank"),
        "weight_scheme": form.get("weight_scheme"),
        "lot_fill": form.get("lot_fill"),
        "limit_mode": form.get("limit_mode"),
    }
    return (not errs), errs, params


def check_rejected(fr):
    """已否决项只读提醒：防止「换个日子再试一次」。"""
    rej = fr.get("rejected") or []
    if not rej:
        return
    print("[gate] 已否决项 %d 条（不得重试）：" % len(rej), flush=True)
    for r in rej:
        print("   ✗ %s → %s" % (str(r.get("item"))[:40], r.get("verdict", "?")), flush=True)


# ────────────────────────── 门禁二：数据 ──────────────────────────

def scan_data(signal=None):
    """扫 data/ashare 分股票 parquet 的 trade_date 列。

    返回 dict：
      coverage   信号日有 bar 的股票数
      last_date  全库最新交易日
      n_files    参与统计的文件数
      lagging    最后日期落后于 last_date 的股票数与样例（停牌/退市）
      elapsed    耗时秒
    """
    import pyarrow.parquet as pq

    t0 = time.time()
    files = [f for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.parquet")))
             if not os.path.basename(f).startswith("_")]
    coverage = 0
    last_date = 0
    lagging = []
    for f in files:
        try:
            col = pq.read_table(f, columns=["trade_date"]).column("trade_date")
        except Exception:
            continue
        vals = [int(x) for x in col.to_pylist() if x is not None]
        if not vals:
            continue
        m = max(vals)
        if m > last_date:
            last_date = m
        if signal is not None and signal in vals:
            coverage += 1
        elif signal is None:
            coverage += 1
        if m < last_date and len(lagging) < 10:
            lagging.append(os.path.basename(f).replace("_", ".").replace(".parquet", ""))
    return {
        "coverage": coverage if signal is not None else len(files),
        "last_date": last_date,
        "n_files": len(files),
        "lagging_sample": lagging,
        "elapsed": round(time.time() - t0, 1),
    }


def check_data(signal, allow_stale=False):
    """信号日数据覆盖 + 新鲜度。缺就停，绝不拿次日的补。

    返回 (ok, errs, info)。
    """
    if not os.path.isdir(DATA_DIR):
        return False, ["找不到数据目录 %s" % DATA_DIR], {}
    try:
        info = scan_data(signal)
    except Exception as e:
        return False, ["扫描数据目录失败：%s" % e], {}

    errs = []
    if info["last_date"] and signal > info["last_date"]:
        errs.append("信号日 %d 晚于数据最新交易日 %d（当日数据尚未落地）"
                    % (signal, info["last_date"]))
    if info["coverage"] < MIN_COVER["daily"]:
        errs.append("daily 覆盖 %d < %d" % (info["coverage"], MIN_COVER["daily"]))

    # 新鲜度：不指定 --signal 时必须用最新交易日；落后太多判定断供
    if not allow_stale:
        stale = days_between(info["last_date"], int(time.strftime("%Y%m%d")))
        info["stale_days"] = stale
        if stale > MAX_STALE_DAYS:
            errs.append("数据最新交易日 %d 距今天 %d 天（>%d），疑似上游断供"
                        % (info["last_date"], stale, MAX_STALE_DAYS))
    return (not errs), errs, info


def check_names(signal):
    """名称表陈旧只告警不阻断：新股名缺失只影响显示，不影响选股。"""
    if not os.path.exists(NAMES):
        print("[warn] 缺 _names.json，选股清单 name 将为空", flush=True)
        return
    age = days_between(int(time.strftime("%Y%m%d", time.localtime(os.path.getmtime(NAMES)))),
                       int(time.strftime("%Y%m%d")))
    if age >= 7:
        print("[warn] _names.json 已 %d 天未更新，新股/改名可能显示为空" % age, flush=True)


# ────────────────────────── 执行 ──────────────────────────

def run_pick(signal, params, timeout=1800):
    cmd = [
        sys.executable, PICK_LIVE,
        "--signal", str(signal),
        "--capital", str(params["capital"]),
        "--top-n", str(params["top_n"]),
        "--max-weight", str(params["max_weight"]),
        "--cost", str(params["cost"]),
    ]
    cmd += ["--drop-p1"] if params["drop_p1"] else ["--no-drop-p1"]
    # 2026-11-10 审计修复：lot_fill/limit_mode 必须随冻结口径下传，
    # 原实现靠子脚本默认值，冻结口径一旦改动就会静默漂移。
    cmd += ["--lot-fill", str(params["lot_fill"]),
            "--limit-mode", str(params["limit_mode"])]
    out = os.path.join(OUT_DIR, "_raw_%d.json" % signal)
    cmd += ["--out", out]
    t0 = time.time()
    print("[daily] 调用选股：%s" % " ".join(cmd), flush=True)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        print("[daily] 选股超时（%ds）" % timeout, flush=True)
        return None, out, "timeout"
    tail = (p.stdout or "").strip().splitlines()[-8:]
    for ln in tail:
        print("  | " + ln, flush=True)
    if p.returncode != 0:
        print("[daily] 选股失败 rc=%d" % p.returncode, flush=True)
        print((p.stderr or "")[-1500:], flush=True)
        return None, out, "rc=%d" % p.returncode
    print("[daily] 选股完成，耗时 %.1fs" % (time.time() - t0), flush=True)
    with open(out, encoding="utf-8") as f:
        return json.load(f), out, None


def verify(payload, params):
    """产物自洽性：闸门关闭却给出买入清单 = 口径被破坏。"""
    errs = []
    meta = payload.get("meta") or {}
    picks = payload.get("picks") or []
    if meta.get("env_safe") is False and picks:
        errs.append("env_safe=false 但产出了 %d 只买入标的（闸门被绕过）" % len(picks))
    if len(picks) > params["top_n"]:
        errs.append("picks %d 超过 top_n %d" % (len(picks), params["top_n"]))
    if int(meta.get("hold") or -1) != params["hold"]:
        errs.append("hold=%s 与冻结 %d 不符" % (meta.get("hold"), params["hold"]))
    if abs(float(meta.get("cost_roundtrip", -1)) - params["cost"]) > 1e-9:
        errs.append("cost=%s 与冻结 %s 不符" % (meta.get("cost_roundtrip"), params["cost"]))
    if _as_bool(meta.get("drop_p1"), None) != params["drop_p1"]:
        errs.append("drop_p1=%s 与冻结 %s 不符" % (meta.get("drop_p1"), params["drop_p1"]))
    if meta.get("lot_fill") != params["lot_fill"]:
        errs.append("lot_fill=%s 与冻结 %s 不符" % (meta.get("lot_fill"), params["lot_fill"]))
    if meta.get("limit_mode") != params["limit_mode"]:
        errs.append("limit_mode=%s 与冻结 %s 不符" % (meta.get("limit_mode"), params["limit_mode"]))
    return errs


def diff_prev(signal, picks):
    """与上一份产物比标的进出。SOP 第九节要求「差异」，缺了这条汇报不完整。"""
    prev = []
    for f in sorted(glob.glob(os.path.join(OUT_DIR, "*.json"))):
        base = os.path.basename(f)
        if base.startswith("_") or not base[:-5].isdigit():
            continue
        d = int(base[:-5])
        if d < signal:
            prev.append((d, f))
    if not prev:
        return {"prev_signal": None, "entered": [], "exited": [],
                "note": "无上一份产物可比"}
    pd_, pf = prev[-1]
    try:
        old = set(x["code"] for x in (json.load(open(pf, encoding="utf-8")).get("picks") or []))
    except Exception:
        old = set()
    new = set(x["code"] for x in picks)
    return {
        "prev_signal": pd_,
        "entered": sorted(new - old),
        "exited": sorted(old - new),
        "kept": len(new & old),
    }


# ────────────── 产品页一致性断言（只读，不进门禁链，老田 2026-11-10 批） ──────────────

HOUFENG_PAGES = [
    "HOUFENG_STRATEGY_CARD.html", "HOUFENG_BROCHURE.html",
    "HOUFENG_ARENA.html", "HOUFENG_ARENA_36M.html",
    "HOUFENG_HOLDGRID.html", "HOUFENG_PICK_20261106.html",
    "HOUFENG_PLAYBOOK.html", "HOUFENG_DIVIDEND.html",
    "M2_DASHBOARD.html",
]

# 数据页 fetch 的数据包（2026-11-10 实锤漂移：固化只带了 HTML 没带数据，
# 线上 7 个数据页全部加载失败）——与页面同表断言。
HOUFENG_DATA = [
    "houfeng_snapshot.json", "houfeng_playbook.json",
    "paper_ledger_replay_12m.jsonl", "houfeng_arena.json",
    "houfeng_arena_36m.json", "houfeng_holdgrid.json",
    "houfeng_pick_20261106.json", "houfeng_dividend.json",
    "hf_tokens.css",  # 2026-11-10 P1：候风产品族单一事实源，随页断言
]


def check_houfeng_pages():
    """portal/houfeng/ 是对外产品真身，dashboard/ 是构建归档。
    逐页 md5 断言，不一致只报警不阻断（退出码不受影响）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(here, "dashboard")
    dst_dir = os.path.join(os.path.dirname(os.path.dirname(here)),
                           "portal", "houfeng")
    result = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "truth": dst_dir, "archive": src_dir, "pages": {}, "drift": []}
    for name in HOUFENG_PAGES + HOUFENG_DATA:
        s, d = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if not (os.path.isfile(s) and os.path.isfile(d)):
            result["pages"][name] = "MISSING"
            result["drift"].append(name)
            continue
        ok = (md5_8(s) == md5_8(d))
        result["pages"][name] = ("OK" if ok else "DRIFT")
        if not ok:
            result["drift"].append(name)
    try:
        out = os.path.join(OUT_DIR, "houfeng_pages_check.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[pages] 状态文件写失败（不影响选股）：%s" % e)
    if result["drift"]:
        print("[pages] ⚠ 产品页/数据包漂移 %d/%d：%s"
              % (len(result["drift"]),
                 len(HOUFENG_PAGES) + len(HOUFENG_DATA),
                 ", ".join(result["drift"])))
        print("[pages]   真身 portal/houfeng/ 与归档 dashboard/ 不一致，"
              "确认来源后重新复制，禁止带着漂移对外。")
    else:
        print("[pages] 产品页一致性 OK：%d 页 + %d 数据包与归档逐位一致"
              % (len(HOUFENG_PAGES), len(HOUFENG_DATA)))
    return result


# ────────────────────────── 主流程 ──────────────────────────

def main():
    global FROZEN, OUT_DIR, LOG_DIR
    ap = argparse.ArgumentParser(description="候风每日标准作业（唯一入口）")
    ap.add_argument("--signal", type=int, default=None,
                    help="信号日 YYYYMMDD，缺省取数据最新交易日")
    ap.add_argument("--check-only", action="store_true", help="只跑门禁，不选股")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--allow-stale", action="store_true",
                    help="历史回看时跳过新鲜度校验（仅 --signal 指定历史日生效）")
    ap.add_argument("--frozen", default=FROZEN, help="冻结文件路径（排障用）")
    ap.add_argument("--out-dir", default=OUT_DIR, help="产物目录（排障/演练用）")
    args = ap.parse_args()

    FROZEN = args.frozen
    OUT_DIR = args.out_dir
    LOG_DIR = os.path.join(OUT_DIR, "logs")

    os.makedirs(LOG_DIR, exist_ok=True)
    tee = Tee(os.path.join(LOG_DIR, "%s.log" % time.strftime("%Y%m%d_%H%M%S")))
    sys.stdout = tee
    try:
        return _main(args)
    finally:
        sys.stdout = sys.__stdout__
        tee.close()


def _main(args):
    stage = "init"
    signal = args.signal

    print("=" * 66)
    print("候风每日标准作业  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 66)

    if not os.path.exists(FROZEN):
        print("[FATAL] 缺少冻结文件 %s" % FROZEN)
        write_fail(signal, 2, "frozen", ["缺少冻结文件"])
        return 2
    fr = load_frozen()
    print("[gate] 冻结状态：%s" % fr.get("status", "?"))
    print("[gate] 冻结时间：%s  by %s" % (fr.get("frozen_at", "?"), fr.get("frozen_by", "?")))

    stage = "frozen"
    ok, errs, params = check_frozen(fr)
    if not ok:
        print("\n[FATAL] 引擎偏离冻结版，拒绝选股（退出码 3）", flush=True)
        for e in errs:
            print("  ✗ %s" % e, flush=True)
        print("\n  这不是「今天没信号」，是「现在跑的不是 M2.1 冻结版」。", flush=True)
        print("  处置二选一：", flush=True)
        print("    a) 回滚工作区到冻结提交：git checkout HEAD -- quant_backtest/qpzm_backtest.py quant_backtest/portfolio_sim.py", flush=True)
        print("    b) 确认当前版本并重新冻结：更新 M2_FROZEN.json 的 md5 与 version_note（须老田签字）", flush=True)
        write_fail(signal, 3, "frozen", errs)
        return 3

    print("[gate] 引擎指纹 OK：qpzm_backtest %s / portfolio_sim %s"
          % (md5_8(ENGINE), md5_8(PSIM)))
    print("[gate] 冻结参数：top_n=%d hold=%d cost=%.4f max_w=%.2f capital=%.0f "
          "drop_p1=%s lot_fill=%s limit_mode=%s"
          % (params["top_n"], params["hold"], params["cost"],
             params["max_weight"], params["capital"], params["drop_p1"],
             params["lot_fill"], params["limit_mode"]))
    check_rejected(fr)

    stage = "data"
    if signal is None:
        try:
            info = scan_data(None)
            signal = int(info["last_date"])
        except Exception as e:
            print("[FATAL] 无法自动取最新交易日：%s" % e)
            write_fail(None, 2, "data", [str(e)])
            return 2
        print("[gate] signal 未指定 → 数据最新交易日 %d" % signal)
        allow_stale = False
    else:
        allow_stale = args.allow_stale

    dok, derr, dinfo = check_data(signal, allow_stale=allow_stale)
    if not dok:
        print("\n[FATAL] 信号日数据不全/不新鲜，拒绝选股（退出码 4）", flush=True)
        for e in derr:
            print("  ✗ %s" % e, flush=True)
        print("  不切换口径、不借用次日数据——缺数据的日子就是不出票。", flush=True)
        write_fail(signal, 4, "data", derr, {"data_info": dinfo})
        return 4
    print("[gate] 数据覆盖 OK：%s" % json.dumps(
        {k: dinfo[k] for k in ("coverage", "last_date", "n_files", "elapsed")},
        ensure_ascii=False))
    if dinfo.get("lagging_sample"):
        print("[gate] 落后股样例（停牌/退市，%d 只）%s"
              % (len(dinfo["lagging_sample"]), dinfo["lagging_sample"][:5]), flush=True)
    check_names(signal)

    if args.check_only:
        print("\n[done] check-only，门禁全部通过")
        return 0

    stage = "pick"
    os.makedirs(OUT_DIR, exist_ok=True)
    payload, raw_path, why = run_pick(signal, params, timeout=args.timeout)
    if payload is None:
        print("\n[FATAL] 选股子进程未产出（退出码 6）：%s" % why, flush=True)
        write_fail(signal, 6, "pick", [why])
        return 6

    stage = "verify"
    verrs = verify(payload, params)
    if verrs:
        print("\n[FATAL] 产物与冻结口径矛盾（退出码 5）", flush=True)
        for e in verrs:
            print("  ✗ %s" % e, flush=True)
        write_fail(signal, 5, "verify", verrs)
        return 5

    meta = payload["meta"]
    picks = payload["picks"]
    dif = diff_prev(signal, picks)
    std = {
        "schema": "houfeng.daily/v1",
        "signal_date": signal,
        "generated_at": meta["generated_at"],
        "frozen": {
            "status": fr.get("status"),
            "frozen_at": fr.get("frozen_at"),
            "engine_md5": md5_8(ENGINE),
            "portfolio_sim_md5": md5_8(PSIM),
            "frozen_file_md5": md5_8(FROZEN),
        },
        "form": params,
        "env": {
            "safe": meta.get("env_safe"),
            "action": ("开闸·可开新仓" if meta.get("env_safe") else "关闸·只观察不开新仓"),
        },
        "funnel": {
            "n_cand_raw": meta.get("n_cand_raw"),
            "n_p1_drop": meta.get("n_p1_drop"),
            "n_cand": meta.get("n_cand"),
            "n_selected": meta.get("n_selected"),
            "n_lot_skip": meta.get("n_lot_skip"),
        },
        "money": {
            "capital": meta.get("capital"),
            "invested": meta.get("invested"),
            "cash_left": meta.get("cash_left"),
            "utilization_pct": meta.get("utilization_pct"),
        },
        "picks": picks,
        "diff_prev": dif,
        "data": {k: dinfo.get(k) for k in ("coverage", "last_date", "n_files")},
        "semantics": "T 日收盘信号 → T+1 开盘买入 → 持有 %d 交易日收盘卖出" % params["hold"],
        "caveat": meta.get("caveat"),
    }
    out = os.path.join(OUT_DIR, "%d.json" % signal)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(std, f, ensure_ascii=False, indent=1, allow_nan=False)

    print("\n" + "-" * 66)
    print("信号日 %d · %s" % (signal, std["env"]["action"]))
    print("漏斗：候选 %s → drop_p1 剔 %s → 剩 %s → 选 %s"
          % (std["funnel"]["n_cand_raw"], std["funnel"]["n_p1_drop"],
             std["funnel"]["n_cand"], std["funnel"]["n_selected"]))
    print("资金：投入 %.0f / 现金 %.0f / 利用率 %.1f%%"
          % (std["money"]["invested"] or 0, std["money"]["cash_left"] or 0,
             std["money"]["utilization_pct"] or 0))
    if dif.get("prev_signal"):
        print("较 %d：进 %d / 出 %d / 留 %d"
              % (dif["prev_signal"], len(dif["entered"]),
                 len(dif["exited"]), dif.get("kept", 0)))
        if dif["entered"]:
            print("  进：%s" % ", ".join(dif["entered"][:8]))
        if dif["exited"]:
            print("  出：%s" % ", ".join(dif["exited"][:8]))
    else:
        print("差异：%s" % dif.get("note"))
    print("产物：%s" % out)
    try:
        check_houfeng_pages()
    except Exception as e:
        print("[pages] 产品页断言异常（不影响选股，照常退出 0）：%s" % e)
    print("-" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
