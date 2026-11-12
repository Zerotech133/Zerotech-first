# -*- coding: utf-8 -*-
"""收盘复盘：今天开盘用的那份票，收盘后到底怎么样。

为什么必须有这一步
------------------
盘前链路只生产「预期」，不生产「结果」。没有 15:40 这一步，每天只有过程、
没有成绩：选得对不对、评分有没有区分度、超额是正还是负，全靠事后手工复盘
（2026-11-12 就是这么干的，一次花掉半小时，且不可累积）。

三个必须写死的判据，否则这份报告会变成自我安慰
----------------------------------------------
① **日期必须对上**。行情快照的日期字段 != 执行日 → 直接阻断。不复权、不推断、
   不「大概就是今天」。用错日期的行情算出来的收益是「看起来对」的那一类错误，
   比报错危险得多。
② **同时给绝对值和超额**。只报绝对收益，会把 beta 当 alpha（11-12 组合 −1.27%，
   看着很糟，同池基准 −1.46%，其实超额 +0.19pct）；只报超额，会掩盖风格暴露。
③ **声明口径**。本报告是「1 日 / 开盘买收盘 / 等权 / 未扣费」的**当日体检**，
   不是记账口径（记账是 40 日 / T+1 开盘 / 双边 0.002 / 批级日等权）。
   两个口径不能互相代言，报告里必须写明这一点。

行情从哪来
----------
本地 tdx/ts_daily 收盘后当天通常还没有（日更 04:26），所以默认走外网新浪快照。
本地若已有当日数据则优先用本地（同一份口径、可离线复算）。

跨源校验（2026-11-12 老田批的口径）
----------------------------------
sina 是主源，腾讯是备用源，两件事分开：
· **failover**：sina 全失败 → 腾讯顶上。此时只有一个源有值，不存在「信谁」，
  标 single_source，照常出报告。
· **cross-check**：两源都有值 → 逐只比收盘价。相对差 > 0.5% 判该日冲突，
  原值全落 `quotes/qc_<day>.json`。
· **冲突日不进 40 日累计分母**（真剔天数，不是记 0）。不取平均、不挑一个 ——
  选边会引入方向性偏差；本项目评的是链路，缺一天样本远便宜于错一天样本。
· 阈值 0.5% 的依据：2026-11-12 实测 sina/腾讯/东财 对 82 只票逐分不差
  （max 0.0000%），放到 0.5% 仍过不去 → 超阈指向真异常而非算法差异。
· 本地复权日线**不参与**比对：与实时源不复权价口径不同，比出来是自己造的假冲突。
· 备用源不可用只记 cross_unavailable，绝不让主路径失败 —— 救不了不能拖下水。

用法
----
    python tools/review/close_review.py                # 复盘今天
    python tools/review/close_review.py --date 20261112
    python tools/review/close_review.py --no-ledger    # 只出报告，不写累计台账

退出码：0 OK / 1 OK但带告警（当日行情两源冲突，已排除出分母）/ 2 阻断 / 3 脚本异常
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import statistics as st
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PREOPEN = os.path.join(os.path.dirname(HERE), "preopen")
sys.path.insert(0, PREOPEN)
import audit_preopen as audit  # noqa: E402

ROOT = r"C:\项目\老田的AI助手"
DELIV = r"C:\项目\deliverables"
LEDGER_DIR = os.path.join(ROOT, "var", "preopen", "close_review")
LEDGER = os.path.join(LEDGER_DIR, "ledger.jsonl")
QUOTE_DIR = os.path.join(LEDGER_DIR, "quotes")   # 当日行情原始输入（证据，非缓存）
TARGET_DAYS = 40          # 攒够这么多个交易日，这条链路才谈得上「被评价过」
PY = r"C:\Program Files\Python311\python.exe"    # 计划任务所用的同一个解释器
STATUS = os.path.join(PREOPEN, "preopen_status.py")
SINA_RETRY = 3            # 主源单批最多试几次（同源重试）
TENCENT_RETRY = 2         # 备用源只试 2 次，且超时更短 —— 它挂了不能拖住主路径
TENCENT_TIMEOUT = 8
CONFLICT_PCT = 0.5        # 两源对同一只票收盘价的相对差阈值（%）
# 为什么是 0.5%：2026-11-12 实测 sina/腾讯/东财 对 8 只票逐分不差（max 0.0000%），
# 阈值放到 0.5% 仍然过不去，说明超阈指向的是真异常（某源未更新/除权/字段漂移），
# 不是两家算法差异。这条闸门顺带当「上游改字段顺序」的探针：字段错位会让整批票
# 系统性偏移，一次比对就能抓住（今天我自己就误把 sina 的 f[2]=昨收 当收盘，造出
# 6.8% 的假冲突，正是靠三源一比才发现是探针写错、不是数据错）。

# 「科技硬件」的归类口径写死在代码里，不每天各算各的。
# 存在的意义：2026-11-12 提出「53% 集中在该板块要不要中性化」，而当时的判断
# 靠的是临时脚本里一个现写的集合 —— 那种数字明天就复现不出来。
# 这里只**记录**暴露度，不参与选股、不改口径；台账攒够天数才谈得上要不要动它。
TECH_HW = {"半导体", "元器件", "通信设备", "IT设备", "软件服务",
           "电子元件", "光学光电子", "消费电子"}

INDEX = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
         ("sh000688", "科创50"), ("sh000300", "沪深300"), ("sh000852", "中证1000")]


def die(msg: str) -> "None":
    """阻断：打印原因并以 2 退出。

    不用 raise SystemExit("字符串") —— 那会退成 rc=1，与脚本异常撞码，
    自动化就分不清「逻辑上不该出报告」和「代码炸了」。
    """
    print(msg)
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------
def load_basket(exec_day: str) -> dict:
    """今天开盘到底用了哪份票。篮子优先，没有篮子才按交易日历反推。"""
    bp = os.path.join(DELIV, "preopen_basket_%s.json" % exec_day)
    if os.path.exists(bp):
        b = json.load(io.open(bp, encoding="utf-8"))
        b["_source"] = "basket"
        return b
    cal = audit.trade_cal()
    data_day = cal[cal.index(exec_day) - 1] if exec_day in cal and cal.index(exec_day) > 0 else None
    pp = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json" % data_day) if data_day else None
    if not (pp and os.path.exists(pp)):
        die("[FATAL] 既无 %s，也找不到可反推的选票产物 —— 今天没出票，无从复盘"
            % os.path.basename(bp))
    return {"exec_day": exec_day, "data_day": data_day, "pick_json": pp,
            "reused": None, "picker_run_at": None, "_source": "derived"}


def load_picks(b: dict) -> tuple[dict, list[dict], list[dict]]:
    d = json.load(io.open(b["pick_json"], encoding="utf-8"))
    top = d.get("top15_adj") or []
    pool = d.get("full_rated") or []
    if not top:
        die("[FATAL] 选票产物 top15_adj 为空：%s" % b["pick_json"])
    return d, top, pool


# ---------------------------------------------------------------------------
# 行情
# ---------------------------------------------------------------------------
def sina_code(ts: str) -> str:
    c, mk = ts.split(".")
    return ("sh" if mk.upper() == "SH" else "sz") + c


def quotes_local(exec_day: str, codes: list[str]) -> dict:
    """本地日线。只有同步已经把当日落下来时才有 —— 15:40 通常没有，是备用路。"""
    try:
        df = audit.load_day("tdx/ts_daily", exec_day,
                            ["ts_code", "open", "close", "pre_close"])
    except Exception:
        return {}
    if df is None or not len(df):
        return {}
    df = df[df["ts_code"].isin(codes)]
    out = {}
    for r in df.itertuples():
        out[r.ts_code] = {"open": float(r.open), "last": float(r.close),
                          "pre": float(r.pre_close), "date": exec_day, "src": "local"}
    return out


def _fetch_sina(batch: list[str], tries: int = SINA_RETRY) -> dict:
    """单批行情，失败重试。

    sina 偶发挂起或返回空体是常态，一次失败就让整份复盘不出（rc=2）代价太大。
    这里只做同源重试；跨源的两件事在 get_quotes 里分开处理：
      · failover —— sina 全挂时由腾讯顶上，此时只有一个源有值，不产生「信谁」问题；
      · cross-check —— 两源都有值时逐只比对，超阈值即判该日冲突。
    混在一起写会把「可靠性」和「口径」搅成一团，说不清哪一步该谁负责。
    """
    url = "https://hq.sinajs.cn/list=" + ",".join(batch)
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    raw = None
    ok = False
    for i in range(max(1, tries)):
        try:
            raw = op.open(req, timeout=20).read().decode("gbk", "ignore")
            if "var hq_str_" in raw:
                ok = True
                break
            # 有响应但无行情：sina 偶尔回空体或错误页。这不算成功，要继续重试 ——
            # 否则 raw 非空会让下面的抛出条件失效，把「取不到」伪装成「取到 0 只」。
            last = RuntimeError("响应无可解析行情（%d 字节）" % len(raw))
        except Exception as e:          # noqa: BLE001 - 网络层异常种类多，统一重试
            last = e
        if i + 1 < tries:
            time.sleep(1.0 * (i + 1))    # 1s / 2s 退避，别做无休止重试
    if not ok:
        raise last if last else RuntimeError("行情请求失败（重试 %d 次）" % tries)
    out = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("var hq_str_"):
            continue
        key = line.split("hq_str_")[1].split("=")[0]
        body = line.split('"')[1] if '"' in line else ""
        f = body.split(",")
        if len(f) < 32 or not f[3]:
            continue
        try:
            out[key] = {"open": float(f[1]), "pre": float(f[2]), "last": float(f[3]),
                        "date": (f[30] or "").replace("-", ""), "tm": f[31], "src": "sina"}
        except ValueError:
            continue
    return out


def quotes_sina(keys: list[str]) -> dict:
    out = {}
    for i in range(0, len(keys), 30):
        try:
            out.update(_fetch_sina(keys[i:i + 30]))
        except Exception as e:
            die("[FATAL] 行情取不到（%s: %s，已重试 %d 次）—— 今天这份复盘没有数据基础，"
                "不出报告比出一份没数的报告强" % (type(e).__name__, e, SINA_RETRY))
        time.sleep(0.35)
    return out


def _fetch_tencent(batch: list[str], tries: int = TENCENT_RETRY) -> dict:
    """备用源单批行情。字段位是实测 dump 出来的（2026-11-12）：
    f[1]=名称 f[2]=6位代码 f[3]=现价 f[4]=昨收 f[5]=今开 f[30]=yyyyMMddHHmmss。

    注意 sina 与腾讯的字段位**并不相同**：sina 的 f[2] 是昨收、f[3] 才是收盘，
    腾讯的 f[3] 就是现价。照抄下标会得到「整批票系统性偏移」—— 两源比对能抓住
    这类错，单源看不出任何异常。
    """
    url = "https://qt.gtimg.cn/q=" + ",".join(batch)
    last = None
    for i in range(max(1, tries)):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = op.open(req, timeout=TENCENT_TIMEOUT).read().decode("gbk", "ignore")
            if "~" not in raw:
                last = RuntimeError("腾讯响应无可解析行情（%d 字节）" % len(raw))
            else:
                out = {}
                for chunk in raw.split(";"):
                    if "~" not in chunk:
                        continue
                    f = chunk.split("~")
                    if len(f) < 33 or not f[3]:
                        continue
                    try:
                        key = chunk.split("v_")[1].split("=")[0]
                        out[key] = {"open": float(f[5]), "pre": float(f[4]),
                                    "last": float(f[3]), "date": (f[30] or "")[:8],
                                    "tm": (f[30] or "")[8:], "src": "tencent"}
                    except (ValueError, IndexError):
                        continue
                if not out:
                    last = RuntimeError("腾讯返回 0 只可解析行情")
                else:
                    return out
        except Exception as e:          # noqa: BLE001 - 网络层异常种类多，统一重试
            last = e
        if i + 1 < tries:
            time.sleep(0.8)
    raise last if last else RuntimeError("腾讯行情请求失败（重试 %d 次）" % tries)


def quotes_tencent_safe(keys: list[str]) -> tuple[dict, str | None]:
    """备用源取数，**永不抛**。

    它的角色是「能救就救」，不能把主路径拖下水：某批失败就停下，返回已拿到的
    部分和错误串，由调用方决定是 failover 还是只记一条「校验不可用」。
    """
    out, err = {}, None
    for i in range(0, len(keys), 30):
        try:
            out.update(_fetch_tencent(keys[i:i + 30]))
        except Exception as e:          # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, e)
            break
    return out, err


def cross_check(primary: dict, alt: dict, codes: list[str], exec_day: str) -> dict:
    """逐只比对两源收盘价，返回校验结论（只判断，不改数据）。"""
    det, bad = {}, []
    for c in codes:
        k = sina_code(c)
        a, b2 = primary.get(k), alt.get(k)
        if not a or not b2 or not a.get("last") or not b2.get("last"):
            continue
        if (b2.get("date") or "") != exec_day:
            continue                    # 备用源日期不符 → 不是今天的数据，比了没意义
        d = (b2["last"] / a["last"] - 1.0) * 100.0
        det[c] = {"a": a["last"], "b": b2["last"], "diff_pct": round(d, 4)}
        if abs(d) > CONFLICT_PCT:
            bad.append(c)
    mx = max((abs(v["diff_pct"]) for v in det.values()), default=None)
    status = "no_overlap" if not det else ("conflict" if bad else "agree")
    return {"status": status, "threshold_pct": CONFLICT_PCT,
            "n_compared": len(det), "max_diff_pct": mx,
            "conflict_codes": bad, "alt_source": "tencent", "details": det}


def save_qc(exec_day: str, cross: dict) -> dict:
    """校验证据单独落盘，不塞进 q_<day>.json。

    因为 q 文件的 sha256 是「同一执行日行情是否变过」的指纹：把跨源比对结果混进去，
    以后每次改校验逻辑都会让指纹变一次，真正的「行情变了」就被噪声淹没。
    证据分文件，指纹只认行情本身。
    """
    os.makedirs(QUOTE_DIR, exist_ok=True)
    p = os.path.join(QUOTE_DIR, "qc_%s.json" % exec_day)
    body = dict(cross, schema="quote_crosscheck/v1", exec_day=exec_day,
                checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(body, ensure_ascii=False, indent=1))
    return {"path": "var/preopen/close_review/quotes/qc_%s.json" % exec_day,
            "status": cross.get("status"),
            "max_diff_pct": cross.get("max_diff_pct"),
            "conflict_codes": cross.get("conflict_codes") or [],
            "n_compared": cross.get("n_compared"),
            "threshold_pct": cross.get("threshold_pct")}


def get_quotes(b: dict, codes: list[str], exec_day: str) -> tuple[dict, str, dict]:
    """取当日行情，返回 (行情, 来源, 校验结论)。

    三层顺序：本地日线 → sina → 腾讯(failover)。本地是复权序列，只在覆盖 ≥80%
    时采用，且**不做跨源比对** —— 复权与不复权比出来的是自己造的假冲突。
    """
    q = quotes_local(exec_day, codes)
    if len(q) >= max(1, int(0.8 * len(codes))):
        return q, "local", {"status": "skip_local", "threshold_pct": CONFLICT_PCT,
                            "n_compared": 0, "max_diff_pct": None, "conflict_codes": [],
                            "note": "本地复权日线，与实时源不复权口径不同，不做跨源比对"}

    keys = [sina_code(c) for c in codes]
    sq, sina_err = {}, None
    try:
        sq = quotes_sina(keys)
    except SystemExit as e:             # quotes_sina 取不到就 die()，这里接住做 failover
        sina_err = "sina 已重试 %d 次仍无数据（rc=%s）" % (SINA_RETRY, e.code)

    if sina_err:
        tq, terr = quotes_tencent_safe(keys)
        if not tq:
            die("[FATAL] 主源与备用源都没取到行情（%s / 腾讯: %s）—— 今天这份复盘"
                "没有数据基础，不出报告比出一份没数的报告强"
                % (sina_err, terr or "无数据"))
        q = {}
        for c in codes:
            v = tq.get(sina_code(c))
            if v:
                q[c] = v
        print("[WARN] 主源 sina 取数失败，已由备用源腾讯顶上（单源，无交叉校验）：%s"
              % sina_err)
        return q, "tencent_failover", {
            "status": "single_source", "threshold_pct": CONFLICT_PCT,
            "n_compared": 0, "max_diff_pct": None, "conflict_codes": [],
            "sina_err": sina_err, "alt_err": terr,
            "note": "主源全失败，只有备用源有值，不存在冲突可比"}

    tq, terr = quotes_tencent_safe(keys)
    if tq:
        cross = cross_check(sq, tq, codes, exec_day)
    else:
        cross = {"status": "cross_unavailable", "threshold_pct": CONFLICT_PCT,
                 "n_compared": 0, "max_diff_pct": None, "conflict_codes": [],
                 "alt_err": terr,
                 "note": "备用源不可用，当日只有单源，未做交叉校验"}

    if not q:
        q = {}
        for c in codes:
            v = sq.get(sina_code(c))
            if v:
                q[c] = v
        return q, "sina", cross
    # 本地只有一部分 → 用外网补齐，逐只标注来源
    for c in codes:
        if c in q:
            continue
        v = sq.get(sina_code(c))
        if v:
            q[c] = v
    # 补进来的才参与判冲突：本地复权值不能因为和实时源不一致就判冲突
    keep = [c for c in (cross.get("conflict_codes") or [])
            if (q.get(c) or {}).get("src") == "sina"]
    cross["conflict_codes"] = keep
    if cross.get("n_compared"):
        cross["status"] = "conflict" if keep else "agree"
    return q, "mixed", cross


def index_quotes() -> dict:
    """指数单独取。

    坑：股票行情以 ts_code 为键（000001.SZ），指数以 sina 代码为键（sh000001），
    两者混在一个字典里查，指数会永远查不到 —— 而且不报错，只是指数表空着。
    「空表不报错」是本平台反复踩的那一类静默失败，所以这里显式分开取、单独返回。
    """
    try:
        sq = quotes_sina([k for k, _ in INDEX])
    except SystemExit:
        return {}
    out = {}
    for k, nm in INDEX:
        v = sq.get(k)
        if v:
            out[nm] = pct(v.get("last"), v.get("pre"))
    return out


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def pct(a: float, b: float):
    if not b or a is None or b is None:
        return None
    return (a / b - 1.0) * 100.0


def check_quote_dates(q: dict, exec_day: str) -> list[str]:
    """返回与执行日不符的行情日期（空列表 = 全部对上）。

    单独抽成函数是为了能直接喂构造数据验证：这条闸门挡的是「用昨天的行情算今天
    的成绩」，算出来的数字看着完全正常 —— 属于必须靠断言守、不能靠人眼守的一类。
    """
    return sorted({v.get("date") for v in q.values()
                   if v.get("date") and v["date"] != exec_day})


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def rows_for(items: list[dict], q: dict, exec_day: str) -> list[dict]:
    out = []
    for it in items:
        code = it["ts_code"]
        x = q.get(code)
        if not x or not x.get("open") or not x.get("last"):
            out.append({"code": code, "name": it.get("name"),
                        "score": it.get("score"), "miss": True})
            continue
        out.append({"code": code, "name": it.get("name"), "ind": it.get("industry"),
                    "score": it.get("score"), "open": x["open"], "close": x["last"],
                    "pre": x["pre"],
                    "gap": pct(x["open"], x["pre"]),
                    "o2c": pct(x["last"], x["open"]),
                    "c2c": pct(x["last"], x["pre"]),
                    "src": x.get("src"), "quote_date": x.get("date"),
                    "miss": False})
    return out


def industry_exposure(rows: list[dict]) -> dict:
    """组合的行业集中度（只记录，不改口径）。

    HHI = Σ(行业权重²)，1/HHI 可读作「等效行业数」。等权 15 只、10 个行业时
    HHI 大致 0.13，等效约 7.7 个行业 —— 比「53% 集中在科技硬件」这句话更适合
    做跨日比较：它能随天数累积成序列，而一句占比不能。
    """
    ok = [r for r in rows if not r["miss"] and r.get("ind")]
    if not ok:
        return {}
    n = len(ok)
    c = Counter(r["ind"] for r in ok)
    return {"n": n, "n_industries": len(c),
            "hhi": round(sum((v / n) ** 2 for v in c.values()), 4),
            "tech_n": sum(v for k, v in c.items() if k in TECH_HW),
            "tech_share": round(100.0 * sum(v for k, v in c.items() if k in TECH_HW) / n, 1),
            "top3": [[k, v] for k, v in c.most_common(3)]}


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["miss"]]
    o2c = [r["o2c"] for r in ok if r["o2c"] is not None]
    c2c = [r["c2c"] for r in ok if r["c2c"] is not None]
    gap = [r["gap"] for r in ok if r["gap"] is not None]
    return {"n": len(ok), "n_miss": len(rows) - len(ok),
            "miss_codes": [r["code"] for r in rows if r["miss"]],
            "o2c_mean": mean(o2c), "o2c_med": med(o2c), "n_o2c": len(o2c),
            "c2c_mean": mean(c2c), "c2c_med": med(c2c), "n_c2c": len(c2c),
            "gap_mean": mean(gap),
            "win_o2c": sum(1 for x in o2c if x > 0), "win_c2c": sum(1 for x in c2c if x > 0)}


# ---------------------------------------------------------------------------
# 台账（40 日评价靠它，不靠每天的报告）
# ---------------------------------------------------------------------------
def read_ledger() -> list[dict]:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with io.open(LEDGER, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def ledger_digest(exec_day: str) -> dict:
    """按执行日去重（同日重跑取最后一条），算累计与 40 日进度。

    冲突日不进分母 —— 这是口径，不是优化：既然已声明「两源差 >阈值 的日子不可信」，
    就必须真的把它从天数里剔掉，而不是统计时按 0 计入。否则「40 日」里掺进一堆
    自己都不信的日子，进度条就在撒谎，而且这种谎话没人会去核对。
    """
    recs = {}
    for r in read_ledger():
        d = r.get("exec_day")
        if d:
            recs[d] = r
    days = sorted(recs)
    bad = [d for d in days if recs[d].get("quote_conflict")]
    ok_days = [d for d in days if not recs[d].get("quote_conflict")]
    ex = [recs[d].get("excess_o2c") for d in ok_days
          if recs[d].get("excess_o2c") is not None]
    return {"n_days": len(ok_days), "target": TARGET_DAYS,
            "progress": "%d/%d" % (len(ok_days), TARGET_DAYS),
            "cum_excess": sum(ex) if ex else None,
            "mean_excess": (sum(ex) / len(ex)) if ex else None,
            "day_win": sum(1 for x in ex if x > 0), "n_ex": len(ex),
            "n_conflict_days": len(bad), "conflict_days": bad, "n_rows": len(days),
            "first_day": ok_days[0] if ok_days else None,
            "last_day": ok_days[-1] if ok_days else None,
            "includes_today": exec_day in recs}


def chain_state(exec_day: str) -> dict:
    """把四态体检结果嵌进复盘产物。

    复盘是这条链路的最后一环。如果链路状态只在手跑 preopen_status 时才看得见，
    那又回到「不知道跑没跑」的老毛病 —— 让每天的复盘产物自己带着它，
    翻一份文件就知道今天走到哪一步、哪一步是空的。

    用子进程而不是 import：status 是个 CLI 工具，import 会把它和本脚本的
    sys.path / 模块级副作用缠在一起；子进程还能吃掉它的崩溃（返回 error 字段
    而不是让整份复盘跟着挂）。
    """
    try:
        r = subprocess.run([PY, "-X", "utf8", STATUS, "--date", exec_day,
                            "--json", "--assume-review"],
                           capture_output=True, text=True, timeout=120, cwd=PREOPEN)
        d = json.loads(r.stdout or "{}")
        if not d.get("verdict"):
            return {"error": "status 无 verdict", "rc": r.returncode,
                    "stderr": (r.stderr or "")[-300:]}
        la, pk, rp = (d.get("launch") or {}), (d.get("pick") or {}), (d.get("report") or {})
        return {"verdict": d["verdict"], "status_rc": r.returncode,
                "started": bool(la.get("found")), "n_start": la.get("n_start"),
                "first_started_at": la.get("first_started_at"),
                "picked": bool(pk.get("exists")), "n_codes": pk.get("n_codes"),
                "reused": pk.get("reused"), "picker_run_at": pk.get("picker_run_at"),
                "pick_source": (d.get("paths") or {}).get("pick_source"),
                "reported": bool(rp.get("exists")),
                "review_assumed": d.get("review_assumed", False)}
    except Exception as e:      # noqa: BLE001 - 体检失败不该拖垮复盘本身
        return {"error": "%s: %s" % (type(e).__name__, e)}


def append_ledger(rec: dict) -> None:
    """台账每天一行，同日重跑就地覆盖。

    早先是纯追加：同一天每跑一次就多一行，于是「文件里有几行」与「攒了几天」
    脱钩 —— 今天测试跑了 4 次，账上就有 4 行 20261112，看上去像攒了 4 天。
    digest 虽然按 exec_day 去重算得对，但物理文件本身在说另一套话，
    而人第一眼看到的是文件。
    """
    os.makedirs(LEDGER_DIR, exist_ok=True)
    rows = [r for r in read_ledger() if r.get("exec_day") != rec.get("exec_day")]
    rows.append(rec)
    rows.sort(key=lambda r: str(r.get("exec_day") or ""))
    with io.open(LEDGER, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def save_evidence(exec_day: str, q: dict, qsrc: str) -> dict:
    """把「本次用来算收益的行情」原样落盘，并给一个内容指纹。

    没有这一步，结论就只能信不能验：次日本地日线同步到位后，同一天会算出
    略微不同的数字（复权、停牌标记、源差异），而没有原始输入就无法区分
    「是数据变了」还是「是代码变了」。审计口径下这是硬伤 —— 报告的每一个
    百分比都必须能由某份留存输入原样复算出来。
    """
    os.makedirs(QUOTE_DIR, exist_ok=True)
    p = os.path.join(QUOTE_DIR, "q_%s.json" % exec_day)
    body = {"exec_day": exec_day, "quote_source": qsrc,
            "quotes": {k: q[k] for k in sorted(q)}}
    h = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()
    prev = None
    if os.path.exists(p):
        try:
            prev = json.loads(io.open(p, encoding="utf-8").read())
        except ValueError:
            prev = None
    prev_h = (prev or {}).get("sha256")
    ev = {"path": "var/preopen/close_review/quotes/q_%s.json" % exec_day,
          "sha256": h, "n": len(q), "written": False,
          "source_changed": bool(prev_h and prev_h != h)}
    if prev_h == h:
        return ev          # 同日重跑、输入一致 → 不重写，避免制造无意义改动
    if prev_h and prev_h != h:
        # 同一执行日的行情变了：留旧版并响亮告警。静默覆盖等于毁掉复核线索。
        io.open(p + ".prev", "w", encoding="utf-8").write(
            json.dumps(prev, ensure_ascii=False, indent=1))
        print("[WARN] 行情证据内容变了（%s → %s，旧版存 q_%s.json.prev）—— "
              "同一执行日的行情源给出了不同数字，本次数字与上次不可直接比较"
              % (prev_h[:12], h[:12], exec_day))
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(dict(body, schema="quote_evidence/v1", sha256=h,
                        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        prev_sha256=prev_h), ensure_ascii=False, indent=1))
    ev["written"] = True
    return ev


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _f(x, fmt="%+.2f%%"):
    return "-" if x is None else fmt % x


def _hms(ts) -> str:
    return (str(ts) or "")[11:19] or "-"


def _cross_label(cc: dict) -> str:
    """把校验结论翻成人话。分开写是因为「一致/冲突/单源/没比」四种状态
    在报告里必须一眼能分辨 —— 把「没比」写成「一致」是这类校验最常见的谎。"""
    cc = cc or {}
    st, n, mx = cc.get("status"), cc.get("n_compared") or 0, cc.get("max_diff_pct")
    if st == "agree":
        return "✔ 双源一致（sina vs 腾讯，%d 只，max %+.4f%%）" % (n, mx or 0.0)
    if st == "conflict":
        return ("✘ **双源冲突 %d/%d 只，max %s%%**（阈值 %s%%）"
                % (len(cc.get("conflict_codes") or []), n, mx, cc.get("threshold_pct")))
    if st == "single_source":
        return "单源（sina 失败，腾讯 failover）—— 无冲突可比"
    if st == "cross_unavailable":
        return "单源（备用源不可用，未做交叉校验）"
    if st == "skip_local":
        return "未比对（本地复权口径，与实时源不同口径）"
    if st == "no_overlap":
        return "无可比对票"
    return st or "-"


def render_md(b: dict, top_s: dict, pool_s: dict, idx: dict,
              rows: list[dict], dg: dict, qsrc: str, cross: dict, expo: dict,
              ev: dict, chain: dict) -> str:
    ex_o2c = (top_s["o2c_mean"] - pool_s["o2c_mean"]
              if top_s["o2c_mean"] is not None and pool_s["o2c_mean"] is not None else None)
    L = []
    A = L.append
    A("# 收盘复盘 · %s" % b["exec_day"])
    A("")
    A("> **口径**：1 日 / 开盘买 · 收盘卖 / 等权 / 未扣费。这是**当日体检**，"
      "不是记账口径（记账 = 40 日 / T+1 开盘 / 双边 0.002 / 批级日等权）。"
      "两者不能互相代言。")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 执行日 | %s |" % b["exec_day"])
    A("| 数据日 | %s |" % (b.get("data_day") or "-"))
    A("| 选票计算于 | %s |" % (b.get("picker_run_at") or "未知（产物未自述）"))
    A("| 选票来源 | %s |" % ("复用（本次未重算）" if b.get("reused") is True else
                            "重算" if b.get("reused") is False else "未记录"))
    A("| 篮子 | %s |" % ("preopen_basket_%s.json" % b["exec_day"]
                          if b.get("_source") == "basket" else "按交易日历反推（非当时实际所用）"))
    A("| 行情来源 | %s |" % qsrc)
    A("| 行情校验 | %s |" % _cross_label(cross))
    A("| 行情证据 | `%s` · sha256 `%s` · %d 只 |"
      % (ev["path"].split("/")[-1], (ev.get("sha256") or "")[:12], ev.get("n") or 0))
    if ev.get("source_changed"):
        A("")
        A("> ⚠️ **本次行情与同日上一次留存不一致**（sha256 变了，旧版留在 "
          "`q_%s.json.prev`）。两次数字不可直接比较，先查是数据源差异还是行情修正。"
          % b["exec_day"])
    if (cross or {}).get("status") == "conflict":
        A("")
        A("> ⛔ **当日行情两源冲突，本日不进 40 日累计分母。** 冲突票：%s。"
          "三源原值已落 `qc_%s.json`。这里不取平均、也不挑一个 —— 选边会引入方向性"
          "偏差，而本项目的目的是评价链路，**缺一天样本远比错一天样本便宜**。"
          % ("、".join(cross.get("conflict_codes") or []), b["exec_day"]))
    A("")
    A("## 一、结果")
    A("")
    A("| 口径 | 均值 | 中位 | 胜率 | 样本 |")
    A("|---|---|---|---|---|")
    A("| 开盘买 → 收盘卖 | **%s** | %s | %d/%d | %d |"
      % (_f(top_s["o2c_mean"]), _f(top_s["o2c_med"]), top_s["win_o2c"],
         top_s["n_o2c"], top_s["n"]))
    A("| 昨收 → 今收 | %s | %s | %d/%d | %d |"
      % (_f(top_s["c2c_mean"]), _f(top_s["c2c_med"]), top_s["win_c2c"],
         top_s["n_c2c"], top_s["n"]))
    A("| 跳空（昨收→今开） | %s | - | - | %d |" % (_f(top_s["gap_mean"]), top_s["n"]))
    if top_s["n_miss"]:
        A("")
        A("**缺行情 %d 只**（停牌或取数失败，未计入均值）：%s"
          % (top_s["n_miss"], "、".join(top_s["miss_codes"])))
    A("")
    A("## 二、超额（对同池等权）")
    A("")
    A("同池基准 = 当日参与打分的 %d 只等权，与组合同一天、同一入口径。" % pool_s["n"])
    A("")
    A("| | 组合 | 同池 | 超额 |")
    A("|---|---|---|---|")
    A("| 开盘→收盘 | %s | %s | **%s** |"
      % (_f(top_s["o2c_mean"]), _f(pool_s["o2c_mean"]), _f(ex_o2c)))
    A("| 昨收→今收 | %s | %s | %s |"
      % (_f(top_s["c2c_mean"]), _f(pool_s["c2c_mean"]),
         _f(None if top_s["c2c_mean"] is None or pool_s["c2c_mean"] is None
            else top_s["c2c_mean"] - pool_s["c2c_mean"])))
    A("")
    A("## 三、市场（判断亏损是 beta 还是 alpha）")
    A("")
    A("| 指数 | 涨跌 |")
    A("|---|---|")
    for nm, v in idx.items():
        A("| %s | %s |" % (nm, _f(v)))
    A("")
    A("## 四、逐只（按开盘→收盘排序）")
    A("")
    A("| # | 代码 | 名称 | 行业 | 评分 | 开盘 | 收盘 | 跳空 | 开→收 | 日涨 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    ok = [r for r in rows if not r["miss"]]
    for i, r in enumerate(sorted(ok, key=lambda x: -(x["o2c"] if x["o2c"] is not None else -99)), 1):
        A("| %d | %s | %s | %s | %.3f | %.2f | %.2f | %s | **%s** | %s |"
          % (i, r["code"], r["name"] or "-", r["ind"] or "-", float(r["score"] or 0),
             r["open"], r["close"], _f(r["gap"]), _f(r["o2c"]), _f(r["c2c"])))
    A("")
    A("## 五、行业暴露（只记录，不参与选股）")
    A("")
    if expo:
        A("| 项 | 值 |")
        A("|---|---|")
        A("| 入选覆盖行业 | %d 个 / %d 只 |" % (expo["n_industries"], expo["n"]))
        A("| HHI | %.4f（等效约 %.1f 个行业）|" % (expo["hhi"], 1.0 / expo["hhi"]))
        A("| 科技硬件 | %d 只，占 %.1f%% |" % (expo["tech_n"], expo["tech_share"]))
        A("| 前三大行业 | %s |"
          % "、".join("%s %d" % (k, v) for k, v in expo["top3"]))
        A("")
        A("> 这一节**不参与选股**，只把每天的集中度记进台账。理由是集中度要不要"
          "中性化，是口径变更，得先有 20~40 日的「暴露—结果」序列才能判；"
          "单日集中既可能是风险也可能是当天最强的板块，凭一天定不了。")
    else:
        A("（行业字段缺失，本节跳过）")
    A("")
    A("## 六、累计进度")
    A("")
    A("已记 %s 个交易日（目标 %d）。累计超额 %s，日均 %s，正超额 %d/%d 日。"
      % (dg["progress"], dg["target"], _f(dg["cum_excess"]), _f(dg["mean_excess"]),
         dg["day_win"], dg["n_ex"]))
    A("")
    if dg.get("n_conflict_days"):
        A("> 另有 %d 个交易日因行情两源冲突**已排除在分母之外**：%s"
          % (dg["n_conflict_days"], "、".join(dg["conflict_days"])))
        A("")
    if dg["n_days"] < dg["target"]:
        A("> 样本不足（%s），上面的日均与胜率**还不足以下结论**。"
          "这条链路要攒够 %d 个交易日才算被评价过 —— 单日数字只用来发现异常，不用来定性。"
          % (dg["progress"], dg["target"]))
    else:
        A("> 样本已达 %s，日均与胜率具备参考性；但与记账口径（40 日/T+1 开盘/双边成本）"
          "仍不是同一件事，不要把这里的数字当净收益。" % dg["progress"])
    A("")
    A("## 七、当天链路（四态）")
    A("")
    if chain.get("error"):
        A("体检没跑成：`%s` —— 复盘本身不受影响，但链路状态未知。" % chain["error"])
    else:
        A("| 环节 | 状态 | 证据 |")
        A("|---|---|---|")
        A("| 启动 | %s | 落痕 %s 次%s |"
          % ("✔" if chain.get("started") else "✘", chain.get("n_start") or 0,
             "，首 %s" % _hms(chain["first_started_at"])
             if chain.get("first_started_at") else ""))
        A("| 算票 | %s | %s 只，算于 %s（%s） |"
          % ("✔" if chain.get("picked") else "✘", chain.get("n_codes") or 0,
             chain.get("picker_run_at") or "-",
             "复用" if chain.get("reused") is True
             else "重算" if chain.get("reused") is False else "来源未记"))
        A("| 简报 | %s | preopen_report_%s.html |"
          % ("✔" if chain.get("reported") else "✘", b["exec_day"]))
        A("| 复盘 | ✔ | 本文件 |")
        A("")
        A("判定：`%s`。这一列的价值在于**失败也能被看见** —— 链路断在哪一环，"
          "不用再翻落痕和日志去拼。" % chain.get("verdict"))
    A("")
    A("---")
    A("生成 %s · close_review · 数据基准 %s"
      % (datetime.now().strftime("%Y-%m-%d %H:%M"), b["exec_day"]))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="收盘复盘：当日选票成绩")
    ap.add_argument("--date", default=None, help="执行日 YYYYMMDD，缺省今天")
    ap.add_argument("--no-ledger", action="store_true", help="不写累计台账")
    a = ap.parse_args()

    exec_day = a.date or datetime.now().strftime("%Y%m%d")
    cal = audit.trade_cal()
    if exec_day not in cal:
        print("[SKIP] %s 非交易日" % exec_day)
        return 0

    b = load_basket(exec_day)
    d, top, pool = load_picks(b)
    if not b.get("picker_run_at"):
        b["picker_run_at"] = d.get("picker_run_at")
    b.setdefault("reused", None)
    print("执行日 %s | 数据日 %s | 选票 %s | 篮子来源 %s"
          % (exec_day, b.get("data_day"), d.get("picker_run_at") or "未自述", b["_source"]))

    codes = [x["ts_code"] for x in top] + [x["ts_code"] for x in pool]
    codes = list(dict.fromkeys(codes))
    q, qsrc, cross = get_quotes(b, codes, exec_day)

    # ① 日期闸门：拿错日期的行情算收益，是「看起来对」的错误，必须硬拦
    bad = check_quote_dates(q, exec_day)
    if bad:
        die("[FATAL] 行情日期 %s != 执行日 %s —— 拒绝用错日期的行情出复盘"
            % (",".join(bad), exec_day))
    if len(q) < max(1, int(0.5 * len(codes))):
        die("[FATAL] 只取到 %d/%d 只行情，样本不足，不出复盘" % (len(q), len(codes)))

    # 先留证据、再算数。顺序反了就会出现「报告出了、输入没了」的不可复核产物。
    ev = save_evidence(exec_day, q, qsrc)
    # 校验证据单独一份：不并进 q 文件，免得改了校验逻辑就把行情指纹一起改掉
    qc = save_qc(exec_day, cross)
    conflicted = (cross.get("status") == "conflict")

    rows = rows_for(top, q, exec_day)
    pool_rows = rows_for(pool, q, exec_day)
    top_s = summarize(rows)
    pool_s = summarize(pool_rows)

    idx = index_quotes()
    if not idx:
        print("[WARN] 指数取不到 —— 报告里市场一栏会空着（不影响个股成绩）")

    expo = industry_exposure(rows)

    # 链路状态必须在写盘之前取：写完再取，[4] 复盘 这一项是自指的
    # （判它存在要读这个文件，而这个文件正在生成）。--assume-review 破环。
    chain = chain_state(exec_day)

    rec = {"schema": "close_review/v1", "exec_day": exec_day,
           "data_day": b.get("data_day"), "quote_source": qsrc,
           "quote_check": cross, "quote_conflict": conflicted, "qc": qc,
           "picker_run_at": b.get("picker_run_at"), "reused": b.get("reused"),
           "n": top_s["n"], "n_miss": top_s["n_miss"],
           "top_o2c": top_s["o2c_mean"], "top_c2c": top_s["c2c_mean"],
           "pool_o2c": pool_s["o2c_mean"], "pool_c2c": pool_s["c2c_mean"],
           "excess_o2c": (top_s["o2c_mean"] - pool_s["o2c_mean"]
                          if top_s["o2c_mean"] is not None and pool_s["o2c_mean"] is not None
                          else None),
           "win_o2c": top_s["win_o2c"], "rows": rows,
           "exposure": expo, "evidence": ev, "chain_state": chain,
           "index": idx, "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # 先入账、再算摘要、最后渲染。
    # 反过来做（先算摘要再入账）会让报告里的累计永远慢一天 —— 首日甚至显示 0/40，
    # 这正是「产物看着正常、数字其实是旧的」那一类错误。
    if not a.no_ledger:
        append_ledger({k: rec[k] for k in
                       ("schema", "exec_day", "data_day", "quote_source", "quote_check",
                        "quote_conflict", "picker_run_at",
                        "reused", "n", "n_miss", "top_o2c", "top_c2c", "pool_o2c",
                        "pool_c2c", "excess_o2c", "win_o2c", "exposure",
                        "evidence", "written_at")})

    dg = ledger_digest(exec_day)
    md = render_md(b, top_s, pool_s, idx, rows, dg, qsrc, cross, expo, ev, chain)

    md_path = os.path.join(DELIV, "close_review_%s.md" % exec_day)
    js_path = os.path.join(DELIV, "close_review_%s.json" % exec_day)
    io.open(md_path, "w", encoding="utf-8").write(md)
    io.open(js_path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))

    print("组合 开→收 %s（%d 只，胜 %d/%d）| 同池 %s | 超额 %s"
          % (_f(top_s["o2c_mean"]), top_s["n"], top_s["win_o2c"], top_s["n_o2c"],
             _f(pool_s["o2c_mean"]),
             _f(None if top_s["o2c_mean"] is None or pool_s["o2c_mean"] is None
                else top_s["o2c_mean"] - pool_s["o2c_mean"])))
    print("累计 %s 日（目标 %d），日均超额 %s" % (dg["progress"], dg["target"],
                                          _f(dg["mean_excess"])))
    print("行情校验 %s" % _cross_label(cross))
    if conflicted:
        print("[WARN] 两源冲突 %d 只（max %s%%）→ 本日不进 40 日累计分母，"
              "原值见 %s；业务 rc 置 1（产物已出，任务计划程序仍按 0 收）"
              % (len(cross.get("conflict_codes") or []), cross.get("max_diff_pct"),
                 qc["path"]))
    if chain.get("error"):
        print("[WARN] 链路体检没跑成：%s（复盘本身已出）" % chain["error"])
    else:
        print("链路 %s | 启动 %s | 算票 %s | 简报 %s | 复盘 本份"
              % (chain.get("verdict"),
                 ("✔%d次" % (chain.get("n_start") or 0)) if chain.get("started") else "✘",
                 ("✔%s只" % (chain.get("n_codes") or 0)) if chain.get("picked") else "✘",
                 "✔" if chain.get("reported") else "✘"))
    print("WROTE %s" % md_path)
    print("WROTE %s" % js_path)
    # 业务码：0 = 干净出报告；1 = 出了但带告警（今日行情两源冲突，已排除出分母）。
    # 不要用 2 —— 2 的语义是「阻断」，冲突日报告是完整可用的，只是不入账。
    return 1 if conflicted else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("[FATAL] 收盘复盘异常：%s: %s" % (type(e).__name__, e))
        sys.exit(3)
