# -*- coding: utf-8 -*-
"""P4 批7-PF：持仓/投委会域 Mixin（debate/council 页族 + portfolio 函数族）。
Handler 壳经基座多继承并入（禁 import *）。
"""
import glob
import logging
import json
import os
import re
import subprocess
import sys
import threading
import time

from pathlib import Path

# 跨平台文件锁（2026-10-26 服务器发布阻塞修复）：
# Windows 用 msvcrt.locking；POSIX（Linux 部署机）用 fcntl.flock，语义等价：
# 非阻塞拿锁失败抛 OSError（与 msvcrt.LK_NBLCK 一致），阻塞拿锁排队等待。
# 原实现模块级 import msvcrt 会让 portal_api 在 Linux 上冷启动即 ImportError。
try:
    import msvcrt as _msvcrt
except ImportError:  # POSIX
    _msvcrt = None
    import fcntl as _fcntl


def _os_lock(fd, nonblock=False):
    if _msvcrt is not None:
        _msvcrt.locking(fd, _msvcrt.LK_NBLCK if nonblock else _msvcrt.LK_LOCK, 1)
    else:
        _fcntl.flock(fd, _fcntl.LOCK_EX | (_fcntl.LOCK_NB if nonblock else 0))


def _os_unlock(fd):
    if _msvcrt is not None:
        try:
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
    else:
        _fcntl.flock(fd, _fcntl.LOCK_UN)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PAPER_DIR_ROOT = str(_REPO_ROOT / "k_stock" / "paper")
_PAPER_DATA_DIR_ROOT = str(_REPO_ROOT / "k_stock" / "data")
_PAPER_DB_ROOT = os.path.join(_PAPER_DATA_DIR_ROOT, "paper.db")
_PAPER_QD_LOCK_FILE = os.path.join(_PAPER_DATA_DIR_ROOT, ".double_knife.lock")
# 2026-10-25（EXEC-R6/P1-04）：portal_api.py 有 7 处按模块级属性访问
# api_portfolio._PAPER_DIR / _PAPER_DATA_DIR / _PAPER_DB（L3431/3541/3551/
# 3599/3619/3657/3692）。这三个名字此前只定义在类 _PortfolioAPI 内
# （本文件 L1627-1629），模块级不存在 → /api/portfolio_status、/api/plan_review、
# /api/review_minutes、/api/trader_roster 四个端点恒定 500 AttributeError
# （12:25:57 同秒 7 条 500 即此 fan-out；HEAD 基线即含缺陷，非今日 WIP）。
# 在模块级补同名别名，与类属性同源、零行为变化；改 7 处调用点也可以但触碰面更大。
_PAPER_DIR = _PAPER_DIR_ROOT
_PAPER_DATA_DIR = _PAPER_DATA_DIR_ROOT
_PAPER_DB = _PAPER_DB_ROOT


def _acquire_paper_qd_lock():
    os.makedirs(_PAPER_DATA_DIR_ROOT, exist_ok=True)
    lock = open(_PAPER_QD_LOCK_FILE, "a+b")
    try:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        _os_lock(lock.fileno(), nonblock=True)
    except OSError:
        lock.close()
        return None
    return lock


def _release_paper_qd_lock(lock):
    try:
        lock.seek(0)
        _os_unlock(lock.fileno())
    finally:
        lock.close()
from api_common import (
    _read_json,
    REPORTS_DIR,
    QBT_DIR,
    pill_nav,
    _council_pending,
    _PILL, NO_WINDOW, _screen_cache)

from api_portfolio_csv import (  # noqa: F401  A5FROZEN：portfolio 摘要/CSV/生效段拆出
    _pf_apply, _pf_csv, _pf_reason_cat, _pf_summary)  # noqa: F401


def _portfolio_market(qs):
    """GET /api/portfolio_market?q= 关键词
    K52-D 手动建仓标的搜索：subprocess 拉引擎全表（300s 缓存），
    之后内存按 code/name 过滤（q<2 字符返回空，前端防抖后调用）。"""
    from urllib.parse import parse_qs as _pqc
    q = ((_pqc(qs).get("q") or [""])[0] or "").strip().lower()
    if len(q) < 2:
        return {"ok": True, "n_total": 0, "items": []}
    now = time.time()
    if _mkt_tbl["d"] is None or now - _mkt_tbl["t"] > 300:
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--market", ""],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
            _mkt_tbl.update(t=now, d=json.loads((r.stdout or "[]").strip()))
        except Exception as e:
            return {"ok": False, "error": "行情快照加载失败：%s" % e}
    hits = [x for x in (_mkt_tbl["d"] or [])
            if q in (x.get("code") or "").lower()
            or q in (x.get("name") or "").lower()][:20]
    return {"ok": True, "n_total": len(_mkt_tbl["d"] or []), "items": hits}
def _debate_say(rec):
    """POST /api/debate/say {p, text}
    K74 老板插话：主持人（代分析师团）+ 三位操盘手并发回应（LLM
    直调，约 10-20s），追加到该场预演留痕 human 轮。"""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    simj = os.path.join(REPORTS_DIR, "council_sim_proposals.jsonl")
    try:
        with open(simj, encoding="utf-8") as f:
            props = [json.loads(x) for x in f if x.strip()]
    except Exception:
        return {"error": "尚无预演留痕，请先在 /sim 发起预演"}
    rev = props[::-1]
    try:
        i = int(rec.get("p"))
    except Exception:
        return {"error": "场次参数无效"}
    txt = str(rec.get("text") or "").strip()[:500]
    if not (0 <= i < len(rev)):
        return {"error": "场次不存在"}
    if not txt:
        return {"error": "发言不能为空"}
    r = rev[i]
    sys.path.insert(0, QBT_DIR)
    try:
        from council_sim import TRADER_SPECS
        from stock_council import _llm, _STANCE_RE
    except Exception as ex:
        return {"error": "引擎导入失败：%s" % str(ex)[:80]}
    vt, dr, fin = r.get("vote") or {}, r.get("directive") or {}, \
        r.get("final") or {}
    base = ("【标的】%s %s\n【分析师结论】票%s（加权 %+.1f），指令 %s，"
            "系数 ×%.2f\n【终裁】%s\n【老板（雇主）在辩论中插话】%s\n"
            "请以你的身份直接回应老板的观点：2-4 句中文，可以附和、可以"
            "坚持己见、可以提出折中，但要有依据。"
            % (r.get("code"), r.get("name") or "", vt.get("verdict"),
               vt.get("wsum") or 0, dr.get("act") or "—",
               (dr.get("sizing") or {}).get("k", 1.0),
               fin.get("verdict") or "—", txt))

    def _one(who, sysc):
        try:
            ans = (_llm([{"role": "system", "content": sysc},
                         {"role": "user", "content": base}])
                   .get("content") or "").strip()
        except Exception as ex:
            ans = "（回应失败：%s）" % str(ex)[:60]
        return {"who": who, "answer": ans}

    jobs = [("主持人（分析师团队）",
             "你是模拟投委会主持人，代表分析师团队回应老板的插话。"
             "诚实评估老板观点对结论的影响，不为迎合而改变立场。")]
    for rk, nm in (("aggr", "激进操盘手"), ("cons", "保守操盘手"),
                   ("neut", "中立操盘手")):
        jobs.append((nm, TRADER_SPECS[rk]["system"]))
    with ThreadPoolExecutor(max_workers=4) as ex:
        reps = list(ex.map(lambda j: _one(j[0], j[1]), jobs))
    # K75 插话后立场重测：操盘手回应自带 STANCE（人设指令），直接解析
    for rp in reps:
        if "操盘手" in rp["who"]:
            m = _STANCE_RE.search(rp["answer"])
            rp["stance3"] = (max(-2, min(2, int(m.group(1))))
                             if m else None)
    # K75 插话后终裁重测：老板的话是否改变终裁（预演层展示，不动真实链路）
    fin0 = r.get("final") or {}
    fin2 = {}
    try:
        u2 = ("【标的】%s %s\n【原分析师结论】票%s（%+.1f）→ 原终裁 %s\n"
              "【老板插话】%s\n【四方回应】%s\n"
              "请重审终裁：老板的观点是否成立？是否修正终裁立场或指令"
              "建议？2-4 句中文说明，最后单独一行 STANCE:n（-2..+2，"
              "插话后终裁立场）。"
              % (r.get("code"), r.get("name") or "",
                 vt.get("verdict"), vt.get("wsum") or 0,
                 fin0.get("verdict") or "—", txt,
                 "\n".join("· %s：%s" % (rp["who"],
                                         rp["answer"].replace("\n", " ")
                                         [:200]) for rp in reps)))
        a2 = (_llm([{"role": "system",
                     "content": ("你是模拟投委会终裁主持人，现在老板（雇主）"
                                 "插话后重审终裁。诚实评估：老板观点成立就"
                                 "吸收，不成立就维持并说明——不为迎合而改判，"
                                 "也不为姿态而固执。")},
                    {"role": "user", "content": u2}])
              .get("content") or "").strip()
        m2 = _STANCE_RE.search(a2)
        s2 = (max(-2, min(2, int(m2.group(1)))) if m2 else None)
        fin2 = {"stance": s2,
                "verdict": ("支持" if (s2 or 0) >= 1 else
                            "反对" if (s2 or 0) <= -1 else "中性"),
                "note": a2}
    except Exception as ex:
        fin2 = {"stance": None, "verdict": "重测失败", "note": str(ex)[:80]}
    # K118 写时合并：上方读全文件后经 LLM 并发（10-40s 窗口），期间
    # 重跑 append 的新行会被旧快照全量重写吞掉——改为独立锁文件内
    # 「重读最新 + 按 (code,d,ts) 锚点定位本场 + 追加本轮 + 整体写回」
    # （锁文件独立于数据文件：写回用 os.replace，数据文件本体不能持锁）
    new_human = {"text": txt, "ts": _t.strftime("%Y-%m-%d %H:%M:%S"),
                 "replies": reps, "final2": fin2}
    lk = open(simj + ".lock", "a+b")
    try:
        lk.seek(0)
        _os_lock(lk.fileno())
        try:
            with open(simj, encoding="utf-8") as f:
                fresh = [json.loads(x) for x in f if x.strip()]
            tgt = None
            for x in fresh:
                if (x.get("code") == r.get("code")
                        and x.get("d") == r.get("d")
                        and x.get("ts") == r.get("ts")):
                    tgt = x
                    break
            if tgt is None:
                return {"error": "该场已被覆盖（可能发生了重跑），"
                                 "请刷新列表后重试"}
            tgt.setdefault("human", []).append(new_human)
            tmp = simj + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for x in fresh:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            os.replace(tmp, simj)
        finally:
            lk.seek(0)
            _os_unlock(lk.fileno())
    finally:
        lk.close()
    return {"ok": True, "replies": reps}
def _debate_flow(r):
    """K74 辩论时间线：五幕 + 老板插话轮 → [(who, txt, tag, cls)]
    cls: host 分析师蓝 / trader 操盘手黄 / vote 绿 / final 红 / human 紫"""
    flow = []
    m0 = r.get("memo") or {}
    mtxt = ["%s：%s" % (lab, m0[k])
            for k, lab in (("thesis", "论点"), ("risks", "风险"),
                           ("if_wrong", "错了怎么办")) if m0.get(k)]
    if mtxt:
        flow.append(("主持人 · 分析师纪要", "\n".join(mtxt),
                     "信度 %s" % m0.get("conviction", "?"), "host"))
    vt = r.get("vote") or {}
    dr = r.get("directive") or {}
    flow.append(("加权投票", "六视角加权 %+.1f → 票%s；指令 %s（系数 ×%.2f）"
                 % (vt.get("wsum") or 0, vt.get("verdict"),
                    dr.get("act") or "—",
                    (dr.get("sizing") or {}).get("k", 1.0)),
                 vt.get("verdict") or "", "vote"))
    for v in r.get("traders") or []:
        if v.get("answer"):
            flow.append(("%s · 质询" % v.get("name"), v["answer"],
                         "%+d" % (v.get("stance") or 0), "trader"))
    rb = (r.get("rebuttal") or {}).get("answer")
    if rb:
        flow.append(("主持人 · 分析师回应", rb, "", "host"))
    for v in r.get("traders") or []:
        if v.get("answer2"):
            st2, st1 = v.get("stance2"), (v.get("stance") or 0)
            tag = ("%+d→%+d" % (st1, st2)
                   if st2 is not None and st2 != st1
                   else "%+d" % (st2 if st2 is not None else st1))
            flow.append(("%s · 再表态" % v.get("name"), v["answer2"],
                         tag, "trader"))
    fin = r.get("final") or {}
    if fin.get("note"):
        flow.append(("终裁", fin["note"], fin.get("verdict") or "", "final"))
    for h in r.get("human") or []:
        flow.append(("老板 · 插话", h.get("text") or "",
                     (h.get("ts") or "")[:16], "human"))
        for rp in h.get("replies") or []:
            st3 = rp.get("stance3")
            tag3 = ("%+d 插话后" % st3 if st3 else
                    "±0 插话后" if st3 == 0 else "")
            flow.append(("%s · 回应老板" % rp.get("who"),
                         rp.get("answer") or "", tag3,
                         "trader" if "操盘手" in (rp.get("who") or "")
                         else "host"))
        f2 = h.get("final2") or {}
        if f2.get("note"):
            flow.append(("终裁 · 插话后重审", f2["note"],
                         f2.get("verdict") or "", "final2"))
    return flow
def _council_simrun(rec):
    """POST /api/council/simrun {op:"start"|"status"}
    K69 模拟投委会预演：后台跑 council_sim.py（LLM 六视角辩论，每票
    约几十秒），start 立即返回不阻塞；status 轮询进程+日志尾部。"""
    op = str(rec.get("op") or "start").strip().lower()
    if op == "status":
        p = _SIMRUN.get("p")
        running = bool(p and p.poll() is None)
        tail = ""
        try:
            if os.path.exists(_SIMRUN["log"]):
                with open(_SIMRUN["log"], "rb") as f:
                    f.seek(0, 2)
                    sz = f.tell()
                    f.seek(max(0, sz - 3000))
                    tail = f.read().decode("utf-8", "replace").strip()[-800:]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        return {"ok": True, "running": running, "tail": tail}
    if op != "start":
        return {"ok": False, "error": "op 需为 start/status"}
    p = _SIMRUN.get("p")
    if p and p.poll() is None:
        return {"ok": False, "error": "预演进行中，请稍候刷新"}
    try:
        with open(_SIMRUN["log"], "ab") as lf:
            lf.write(("\n===== %s sim council =====\n"
                      % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
            _SIMRUN["p"] = subprocess.Popen(
                [sys.executable, "-X", "utf8", "council_sim.py"],
                stdout=lf, stderr=lf, cwd=QBT_DIR, creationflags=NO_WINDOW)
        return {"ok": True, "started": True, "pid": _SIMRUN["p"].pid}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
def _council_confirm(rec):
    """POST /api/council/confirm {sid, code, op:"confirm"|"dismiss"}
    K64d 半自动执行链（预警票）+ K67 系数建议票，人工确认后执行——
      · 平仓预警 → portfolio.py --close sid.code（全平）
      · 减仓预警 → --adjust sid.code,trim,50（减半）
      · 加仓建议（k≥1.1 激进同向）→ --adjust sid.code,add,pct
      · 减仓建议（k≤0.9 保守同向/偏空）→ --adjust sid.code,trim,pct
        （pct=|k-1|×100，入队时算好存票面）
    dismiss=忽略并出队。执行/忽略均从 pending 移除；铁律：无确认不动仓。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    op = str(rec.get("op") or "").strip().lower()
    if op not in ("confirm", "dismiss"):
        return {"ok": False, "error": "op 需为 confirm/dismiss"}
    key = "%s|%s" % (sid, code)
    with _pf_lock:
        pend = _council_pending()
        item = pend.get(key)
        if not item:
            return {"ok": False, "error": "队列无此票：%s" % key}
        act = item.get("act") or ""
        res = {"ok": True, "op": op, "sid": sid, "code": code, "act": act}
        if op == "confirm":
            if act == "平仓预警":
                cmd = ["--close", "%s.%s" % (sid, code)]
            elif act in ("减仓预警", "减仓建议", "加仓建议"):
                try:
                    pct = int(item.get("pct")
                              or (50 if act == "减仓预警" else 10))
                except (TypeError, ValueError):
                    pct = 50 if act == "减仓预警" else 10
                pct = max(1, min(100, pct))
                cmd = ["--adjust", "%s.%s,%s,%d"
                       % (sid, code,
                          "add" if act == "加仓建议" else "trim", pct)]
            else:
                return {"ok": False, "error": "非可执行票：%s" % act}
            try:
                r = subprocess.run(
                    [sys.executable, "-X", "utf8", "portfolio.py"] + cmd,
                    cwd=QBT_DIR, capture_output=True, text=True,
                    timeout=120, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "执行超时（120s），票留队列"}
            out = (r.stdout or "").strip()
            bad = r.returncode != 0 or any(w in out for w in
                                           ("无可平", "不存在", "不足",
                                            "无可调"))
            if bad:
                return {"ok": False, "error": out[-200:] or "rc=%d"
                        % r.returncode}
            res["run_tail"] = out.splitlines()[-2:]
            res["account"] = _pf_summary(sid)
            _screen_cache["t"] = 0.0
        pend.pop(key, None)
        try:
            with open(os.path.join(REPORTS_DIR, "council_pending.json"),
                      "w", encoding="utf-8") as f:
                json.dump(pend, f, ensure_ascii=False, indent=1)
        except Exception as ex:
            res["warn"] = "队列回写失败：%s" % str(ex)[:80]
        return res
def _portfolio_open(rec):
    """POST /api/portfolio_open {sid, code, amount?}
    K51-D 手动建仓：跨策略快照寻价按当前价成交；用户意志优先（不受
    闸门/熔断/起始日/拉黑限制），仅守现金与 max_pos；amount 缺省=等权。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    amount = rec.get("amount")
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        if not code:
            return {"ok": False, "error": "code 不能为空"}
        if amount is not None:
            try:
                amount = round(float(amount), 2)
            except (TypeError, ValueError):
                return {"ok": False, "error": "amount 需为数值（元）"}
            if not 1_000 <= amount <= 10_000_000:
                return {"ok": False, "error": "amount 越界（1千~1千万）"}
        arg = "%s.%s" % (sid, code)
        if amount:
            arg += ",%d" % int(amount)
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--open", arg],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "建仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or "OPEN 手动" not in out:
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code,
                "run_tail": out.splitlines()[-2:], "account": _pf_summary(sid)}
def _portfolio_adjust(rec):
    """POST /api/portfolio_adjust {sid, code, act:"add"|"trim", pct}
    K51-A 手动加/减仓：add=按现价加仓（规模=持仓市值×pct%）、
    trim=卖出持仓数量 pct%（≥100 全平）。subprocess 调引擎 CLI。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    act = str(rec.get("act") or "").strip().lower()
    try:
        pct = float(rec.get("pct"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "pct 需为数值（%）"}
    if act not in ("add", "trim"):
        return {"ok": False, "error": "act 需为 add/trim"}
    if not 1 <= pct <= 100:
        return {"ok": False, "error": "pct 越界（1~100%）"}
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        if not any(p.get("code") == code for p in
                   (led["accounts"][sid].get("open") or [])):
            return {"ok": False, "error": "该账户无此持仓：%s" % code}
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--adjust",
                 "%s.%s,%s,%g" % (sid, code, act, pct)],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "加减仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or ("不足" in out or "无可调" in out):
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code, "act": act, "pct": pct,
                "run_tail": out.splitlines()[-2:], "account": _pf_summary(sid)}
def _portfolio_close(rec):
    """POST /api/portfolio_close {sid, code?}
    K50-D 手动平仓：code 为空/all=清仓整个账户，否则平单只。
    按源快照最新价成交，reason=手动平仓（不挡该标的日后重建与否由
    rebuild 语义决定：手动平仓无 rebuild → 该标的不再自动重建）。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        arg = sid if (not code or code.lower() == "all") else "%s.%s" % (sid, code)
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--close", arg],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "平仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or "无可平" in out or "不存在" in out:
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code or None,
                "run_tail": out.splitlines()[-3:], "account": _pf_summary(sid)}
def _portfolio_undo():
    """POST /api/portfolio_undo：弹栈最后一笔 API 配置变更并恢复
    （capital/start_date 回旧值，资金走平滑注资/撤资；连续撤销沿历史回退，
    历史弹空即止，无 redo）。"""
    with _pf_lock:
        cfg_fp = os.path.join(QBT_DIR, "data", "portfolio_config.json")
        cfg = _read_json(cfg_fp)
        hist = cfg.get("_history") or []
        if not hist:
            return {"ok": False, "error": "无可撤销的配置变更"}
        last = hist.pop()
        sid = last.get("sid") or ""
        ac_cfg = (cfg.get("accounts") or {}).get(sid)
        if not ac_cfg:
            return {"ok": False, "error": "历史条目账户 %s 已不存在" % sid}
        restored = []
        # K50 修复：只恢复并显示「值实际变化」的字段；
        # 快照值为 None 表示当时账户级未设置 → 删键回退全局默认
        if last.get("capital") is not None and \
                ac_cfg.get("capital") != last["capital"]:
            ac_cfg["capital"] = int(last["capital"])
            restored.append("资金=%d万" % (ac_cfg["capital"] // 10000))
        if last.get("start_date") is not None and \
                (ac_cfg.get("start_date") or "") != last["start_date"]:
            ac_cfg["start_date"] = last["start_date"]
            restored.append("起始日=%s" % (last["start_date"] or "(立即)"))
        for k, lab in (("stop_loss_pct", "止损"), ("take_profit_pct", "止盈"),
                       ("max_drawdown_pct", "熔断")):
            if last.get(k) is None:
                if k in ac_cfg:
                    del ac_cfg[k]
                    restored.append("%s=(默认)" % lab)
            elif ac_cfg.get(k) != last[k]:
                ac_cfg[k] = float(last[k])
                restored.append("%s=%.1f%%" % (lab, ac_cfg[k]))
        if last.get("max_pos") is not None and \
                ac_cfg.get("max_pos") != last["max_pos"]:
            ac_cfg["max_pos"] = int(last["max_pos"])
            restored.append("持仓上限=%d" % ac_cfg["max_pos"])
        if not restored:
            restored.append("（快照与当前一致）")
        cfg["_history"] = hist
        tmp = cfg_fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cfg_fp)
        tail, err = _pf_apply(sid)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "sid": sid, "restored": restored,
                "undo_ts": last.get("ts", ""), "n_left": len(hist),
                "run_tail": tail, "account": _pf_summary(sid)}
def _portfolio_set(rec):
    """POST /api/portfolio_set {sid, capital?, start_date?}
    写 data/portfolio_config.json 后立即跑 daily_run（同日幂等）：
    capital 变更平滑注资/撤资即时生效；start_date 账户级建仓时间闸门。"""
    sid = str(rec.get("sid") or "")
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        cfg_fp = os.path.join(QBT_DIR, "data", "portfolio_config.json")
        cfg = _read_json(cfg_fp)
        ac_cfg = (cfg.setdefault("accounts", {}).setdefault(
            sid, {"enabled": True, "capital": 1_000_000, "max_pos": 20}))
        # K46 撤销历史：先取变更前快照（入栈在确认 changed 后）
        prev = {"sid": sid, "capital": ac_cfg.get("capital"),
                "start_date": ac_cfg.get("start_date") or "",
                "stop_loss_pct": ac_cfg.get("stop_loss_pct"),
                "take_profit_pct": ac_cfg.get("take_profit_pct"),
                "max_pos": ac_cfg.get("max_pos"),
                "max_drawdown_pct": ac_cfg.get("max_drawdown_pct")}
        changed = []
        if rec.get("capital") is not None:
            try:
                cap = int(rec["capital"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "capital 需为整数（元）"}
            if not 10_000 <= cap <= 100_000_000:
                return {"ok": False, "error": "capital 越界（1万~1亿）"}
            if cap != ac_cfg.get("capital"):
                ac_cfg["capital"] = cap
                changed.append("capital=%d" % cap)
        if rec.get("start_date") is not None:
            sd = str(rec["start_date"]).strip()
            if sd and (len(sd) != 10 or sd[4] != "-" or sd[7] != "-"
                       or not sd.replace("-", "").isdigit()):
                return {"ok": False, "error": "start_date 需为 YYYY-MM-DD 或空"}
            if sd != (ac_cfg.get("start_date") or ""):
                ac_cfg["start_date"] = sd
                changed.append("start_date=%s" % (sd or "(立即)"))
        # K50 止盈止损阈值（%相对成本价；0=停用；账户级优先全局回退）
        for k, lab in (("stop_loss_pct", "止损"), ("take_profit_pct", "止盈")):
            if rec.get(k) is not None:
                try:
                    v = round(float(rec[k]), 1)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "%s 需为数值（%%）" % lab}
                if not 0 <= v <= 50:
                    return {"ok": False, "error": "%s 越界（0~50%%，0=停用）" % lab}
                if v != ac_cfg.get(k):
                    ac_cfg[k] = v
                    changed.append("%s=%.1f%%" % (lab, v))
        # K51-C 回撤熔断阈值（0=停用；账户级优先全局回退）
        if rec.get("max_drawdown_pct") is not None:
            try:
                v = round(float(rec["max_drawdown_pct"]), 1)
            except (TypeError, ValueError):
                return {"ok": False, "error": "熔断需为数值（%）"}
            if not 0 <= v <= 50:
                return {"ok": False, "error": "熔断越界（0~50%，0=停用）"}
            if v != ac_cfg.get("max_drawdown_pct"):
                ac_cfg["max_drawdown_pct"] = v
                changed.append("熔断=%.1f%%" % v)
        # K51-A 持仓数上限
        if rec.get("max_pos") is not None:
            try:
                v = int(rec["max_pos"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "持仓上限需为整数"}
            if not 1 <= v <= 50:
                return {"ok": False, "error": "持仓上限越界（1~50）"}
            if v != ac_cfg.get("max_pos"):
                ac_cfg["max_pos"] = v
                changed.append("持仓上限=%d" % v)
        if not changed:
            return {"ok": True, "changed": [], "note": "无变更",
                    "account": _pf_summary(sid)}
        # K46 撤销历史：入栈变更前快照（仅记 API 变更；CLI --set 不入史）
        cfg.setdefault("_history", []).append(dict(
            ts=time.strftime("%Y-%m-%d %H:%M:%S"), **prev))
        cfg["_history"] = cfg["_history"][-20:]
        tmp = cfg_fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cfg_fp)
        tail, err = _pf_apply(sid)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "sid": sid, "changed": changed,
                "run_tail": tail, "account": _pf_summary(sid)}
def _council_holdings(cs, opens):
    """K63 持仓管理明细：proposals 全量按 (sid,code) 取最新立案留痕
    ∩ 当前持仓 code 集，返回按指令严重度排序的明细行（不缓存，量小；
    props 行数=立案票数级，远小于 watch 表）。"""
    if not (cs or {}).get("d") or not opens:
        return []
    hmap = {}
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_proposals.jsonl"),
                  encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                k = (r.get("sid"), r.get("code"))
                if k in opens:
                    hmap[k] = r          # 后读覆盖 = 最新一夜
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
    order = {"平仓预警": 0, "减仓预警": 1, "收紧盯盘": 2, "持有观察": 3}
    hold = [{"sid": k[0], "code": k[1], "name": r.get("name"),
             "verdict": (r.get("memo") or {}).get("verdict"),
             "act": (r.get("directive") or {}).get("act") or "—",
             "style": r.get("style") or "稳健",
             "net": (r.get("directive") or {}).get("net_stance"),
             "k": ((r.get("directive") or {}).get("sizing") or {}).get("k"),
             "vote": ((r.get("vote") or {}).get("verdict")),
             "d": r.get("d")} for k, r in hmap.items()]
    hold.sort(key=lambda x: (order.get(x["act"], 9), str(x["code"])))
    return hold
# K84 辩论室「你的插话已重测」横幅的时效窗口（秒）：
# 插话后该秒数内进详情页显示横幅；可用环境变量 DEBATE_RECENT_SEC 覆盖
DEBATE_RECENT_SEC = int(os.environ.get("DEBATE_RECENT_SEC") or 600)

_mkt_tbl = {"t": 0.0, "d": None}
_SIMRUN = {"p": None,
           "log": os.path.join(r"C:\Temp", "portal_council_sim.log")}
# ── K45 策略卡「模拟建仓」配置端点：capital/start_date 即时生效 ──
_pf_lock = threading.Lock()


# ══ Handler 方法（经 MRO 并入基座）══

class Handler:

    def _debate_page(self, qs):
        """K74 模拟辩论室：五幕辩论回放 + 老板插话（人类参与轮）。"""
        import html as _h
        from urllib.parse import parse_qs as _pql
        e = _h.escape
        qp = _pql(qs or "")
        SIMJ = os.path.join(REPORTS_DIR, "council_sim_proposals.jsonl")
        props = []
        try:
            with open(SIMJ, encoding="utf-8") as f:
                props = [json.loads(x) for x in f if x.strip()]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        # K91 同日同标的去重：jsonl 是 append-only，同场重跑产生多行
        # ——旧行占据列表卡片并虚高「场数」（600663 一日 4 行 → 显示
        # 4 张卡，点旧卡看不到插话反馈）。按 (code,d) 取最新行用于
        # 列表/详情显示；K96 统计口径改用全量行（插话跨重跑累计，
        # 重跑不吞掉旧场插话），历史行留在文件里不删
        allp = props
        _seen = {}
        for x in props:
            _seen[(x.get("code"), x.get("d"))] = x
        props = list(_seen.values())
        rev = props[::-1]
        SID_LABEL = {"qpzm": "青萍之末", "9dim": "九维评分",
                     "sig4": "四策略信号", "chanlun": "缠论结构",
                     "manual": "手动"}
        pv = (qp.get("p") or [""])[0]
        if pv.isdigit() and int(pv) < len(rev):
            # ── 单场：五幕时间线 + 插话框 ──
            idx = int(pv)
            r = rev[idx]
            flow = _debate_flow(r)
            hs = r.get("human") or []
            tvs = r.get("traders") or []

            def _stance_chart(rr):
                """K76 立场演化轨迹图：质询→再表态→插话N后，
                三操盘手实线 + 终裁虚线，变化点白圈高亮+增量标注。"""
                if not tvs:
                    return ""
                cols = ["质询", "再表态"] + \
                    ["插话%d后" % (i + 1) for i in range(len(hs))]
                ncol = len(cols)
                W, H = max(340, 90 + ncol * 96), 250
                L, Rm, T, B = 64, 26, 18, 46
                pw, ph = W - L - Rm, H - T - B

                def _y(v):
                    return T + ph * (2 - v) / 4.0

                def _x(i):
                    return L + pw * i / float(max(1, ncol - 1))

                # K79 最新插话轮即时反馈：DEBATE_RECENT_SEC（默认 10 分钟）
                # 内的插话触发结论横幅；最新列永远亮框突出（含脉冲光圈），
                # 与旧轮次拉开视觉差
                last_col = ncol - 1 if hs else -1
                recent = False
                if hs:
                    try:
                        recent = (time.time() - time.mktime(
                            time.strptime(hs[-1].get("ts") or "",
                                          "%Y-%m-%d %H:%M:%S"))) \
                            < DEBATE_RECENT_SEC
                    except Exception:
                        recent = False

                gl = "".join(
                    "<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' "
                    "stroke='#30363d'%s/><text x='%d' y='%.1f' "
                    "fill='#8b949e' font-size='10' text-anchor='end'>"
                    "%+d</text>"
                    % (L, _y(v), W - Rm, _y(v),
                       " stroke-dasharray='4 3'" if v == 0 else "",
                       L - 6, _y(v) + 3, v)
                    for v in (2, 1, 0, -1, -2))
                cl = "".join(
                    "<text x='%.1f' y='%d' fill='%s' font-size='%s' "
                    "text-anchor='middle'%s%s>%s%s</text>"
                    % (_x(i), H - 26,
                       "#d2a8ff" if i == last_col else
                       ("#58a6ff" if i >= 2 else "#8b949e"),
                       "12.5" if i == last_col else "11",
                       " font-weight='600'" if i >= 2 else "",
                       " style='cursor:help'" if i >= 2 else "",
                       e(c + "·最新") if i == last_col else e(c),
                       ("<title>老板：%s</title>"
                        % e((hs[i - 2].get("text") or "")[:120]))
                       if i >= 2 and i - 2 < len(hs) else "")
                    for i, c in enumerate(cols))
                # K78 插话列竖向高亮带：默认可见的「此列为插话后重测」标记
                # K79 最新列加亮（.16）+ 紫虚线亮框，与旧轮次拉开视觉差
                bw = min(38.0, pw / max(1, ncol - 1) * 0.42)
                band = "".join(
                    "<rect x='%.1f' y='%d' width='%.1f' height='%d' "
                    "fill='%s'/>%s"
                    "<text x='%.1f' y='%d' font-size='11' "
                    "text-anchor='middle' style='cursor:help' "
                    "fill='#c58af9'>💬<title>老板插话后立场重测列"
                    "</title></text>"
                    % (_x(i) - bw, T, bw * 2, ph,
                       "rgba(197,138,249,.16)" if i == last_col
                       else "rgba(197,138,249,.08)",
                       ("<rect x='%.1f' y='%d' width='%.1f' height='%d' "
                        "fill='none' stroke='#c58af9' stroke-width='1' "
                        "stroke-dasharray='4 3' rx='5'/>"
                        % (_x(i) - bw, T, bw * 2, ph))
                       if i == last_col else "",
                       _x(i), T + 12)
                    for i in range(2, ncol))
                COLS = {"激进": "#f28b82", "保守": "#8ab4f8",
                        "中立": "#fdd663"}
                lines, dots, rings = [], [], []
                for v in tvs:
                    nm = v.get("name") or "?"
                    color = COLS.get(nm[:2], "#8b949e")
                    s0 = v.get("stance") or 0
                    s2 = v.get("stance2")
                    pts = [(s0, True),
                           (s2 if s2 is not None else s0, s2 is not None)]
                    for h in hs:
                        st3 = None
                        for rp in h.get("replies") or []:
                            if rp.get("who") == nm:
                                st3 = rp.get("stance3")
                        pts.append((st3 if st3 is not None
                                    else pts[-1][0], st3 is not None))
                    lines.append(
                        "<polyline points='%s' fill='none' stroke='%s' "
                        "stroke-width='2'/>"
                        % (" ".join("%.1f,%.1f" % (_x(i), _y(p[0]))
                                    for i, p in enumerate(pts)), color))
                    for i, p in enumerate(pts):
                        val, tested = p
                        prev = pts[i - 1][0] if i else val
                        moved = i > 0 and val != prev
                        if not tested:
                            dots.append(
                                "<circle cx='%.1f' cy='%.1f' r='3.5' "
                                "fill='#161b22' stroke='#8b949e' "
                                "stroke-width='1.2' style='cursor:help'>"
                                "<title>%s · %s："
                                "该轮未重测</title></circle>"
                                % (_x(i), _y(val), e(nm), e(cols[i])))
                            continue
                        tip = "%s · %s：%+d" % (nm, cols[i], val)
                        if moved:
                            src = "老板插话" if i >= 2 else "分析师回应"
                            tip += "（%s %+d→%+d）" % (src, prev, val)
                            dots.append(
                                "<text x='%.1f' y='%.1f' fill='%s' "
                                "font-size='9'>%+d</text>"
                                % (_x(i) + 6, _y(val) - 5, color,
                                   val - prev))
                            if i >= 2:
                                dots.append(
                                    "<text x='%.1f' y='%.1f' fill='#c58af9' "
                                    "font-size='8'>←插话</text>"
                                    % (_x(i) + 6, _y(val) + 12))
                        dots.append(
                            "<circle cx='%.1f' cy='%.1f' r='%d' fill='%s'"
                            "%s style='cursor:help'><title>%s</title>"
                            "</circle>"
                            % (_x(i), _y(val), 5 if moved else 3.5, color,
                               " stroke='#fff' stroke-width='1.5'"
                               if moved else "", e(tip)))
                        if i == last_col and tested:
                            rings.append(
                                "<circle cx='%.1f' cy='%.1f' r='9' fill="
                                "'none' stroke='#c58af9' stroke-width='1.5' "
                                "style='animation:k79p 1.8s ease-in-out "
                                "infinite'><title>最新插话后重测点"
                                "</title></circle>" % (_x(i), _y(val)))
                        if moved and i >= 2:  # K81 因果连线：💬→变化点
                            dots.append(
                                "<path d='M%.1f,%d Q%.1f,%.1f %.1f,%.1f' "
                                "fill='none' stroke='#c58af9' "
                                "stroke-width='1' stroke-dasharray='2 2' "
                                "opacity='.7' style='cursor:help'>"
                                "<title>老板这句话改变了%s的立场"
                                "（%+d→%+d）</title></path>"
                                "<polygon points='%.1f,%.1f %.1f,%.1f "
                                "%.1f,%.1f' fill='#c58af9' opacity='.7'/>"
                                % (_x(i), T + 20, _x(i) + 24,
                                   (T + 20 + _y(val)) / 2.0,
                                   _x(i) + 7, _y(val) - 10,
                                   e(nm), prev, val,
                                   _x(i) + 7, _y(val) - 2,
                                   _x(i) + 3.5, _y(val) - 10,
                                   _x(i) + 10.5, _y(val) - 10))
                fin = rr.get("final") or {}
                fpts = ([(fin.get("stance"), True)]
                        if fin.get("stance") is not None else [])
                # K79 修复 K76 列错位：每轮插话强制占一个槽位（无 final2
                # =该轮未重审，平移空心点），终裁虚线与列严格对齐
                for h in hs:
                    f2 = h.get("final2") or {}
                    if f2.get("stance") is not None:
                        fpts.append((f2.get("stance"), True))
                    else:
                        fpts.append((fpts[-1][0] if fpts else 0, False))
                if fpts:
                    lines.append(
                        "<polyline points='%s' fill='none' stroke='#d2a8ff' "
                        "stroke-width='2' stroke-dasharray='6 3'/>"
                        % (" ".join("%.1f,%.1f" % (_x(1 + i), _y(p[0]))
                                    for i, p in enumerate(fpts))))
                    for i, p in enumerate(fpts):
                        val, tested = p
                        prev = fpts[i - 1][0] if i else val
                        moved = i > 0 and val != prev
                        tip = "终裁 · %s：%+d" % (cols[1 + i], val)
                        if moved:
                            tip += "（老板插话 %+d→%+d）" % (prev, val)
                        dots.append(
                            "<circle cx='%.1f' cy='%.1f' r='%d' fill='%s' "
                            "stroke='%s'%s style='cursor:help'>"
                            "<title>%s</title></circle>"
                            % (_x(1 + i), _y(val), 5 if moved else 3.5,
                               "#d2a8ff" if tested else "#161b22",
                               "#fff" if tested else "#8b949e",
                               " stroke-width='1.5'" if moved else "",
                               e(tip)))
                        if tested and 1 + i == last_col:
                            rings.append(
                                "<circle cx='%.1f' cy='%.1f' r='9' fill="
                                "'none' stroke='#d2a8ff' stroke-width='1.5' "
                                "style='animation:k79p 1.8s ease-in-out "
                                "infinite'><title>终裁 · 最新插话后重审"
                                "</title></circle>"
                                % (_x(1 + i), _y(val)))
                        if moved and i >= 1:  # K81 终裁改判因果连线
                            dots.append(
                                "<path d='M%.1f,%d Q%.1f,%.1f %.1f,%.1f' "
                                "fill='none' stroke='#d2a8ff' "
                                "stroke-width='1' stroke-dasharray='2 2' "
                                "opacity='.7' style='cursor:help'>"
                                "<title>老板这句话改变了终裁立场"
                                "（%+d→%+d）</title></path>"
                                "<polygon points='%.1f,%.1f %.1f,%.1f "
                                "%.1f,%.1f' fill='#d2a8ff' opacity='.7'/>"
                                % (_x(1 + i), T + 20, _x(1 + i) - 24,
                                   (T + 20 + _y(val)) / 2.0,
                                   _x(1 + i) - 7, _y(val) - 10,
                                   prev, val,
                                   _x(1 + i) - 7, _y(val) - 2,
                                   _x(1 + i) - 10.5, _y(val) - 10,
                                   _x(1 + i) - 3.5, _y(val) - 10))
                legend = "".join(
                    "<span style='color:%s;font-size:11.5px;"
                    "margin-right:12px'>● %s</span>" % (c, n)
                    for n, c in list(COLS.items())
                    + [("终裁", "#d2a8ff")]) + \
                    "<span style='color:var(--gray-5);font-size:11.5px;" \
                    "margin-left:4px'>○ 未重测</span>"
                # K78 老板插话原文引文：默认可见，不依赖悬停
                quotes = "".join(
                    "<div style='color:var(--gray-5);font-size:11.5px;"
                    "margin-top:3px'>💬 插话%d（%s）「%s」</div>"
                    % (i + 1, (h.get("ts") or "")[5:16],
                       e((h.get("text") or "")[:60]))
                    for i, h in enumerate(hs))
                # K80 数值矩阵：重测结果数字直读（变化格紫粗、未测○、
                # 最新列浅紫底）——不用目测 Y 轴也能读出每轮重测值
                mth = ("padding:2px 9px;font-weight:600;color:#8b949e;"
                       "font-size:11px;text-align:center")
                mtd = ("padding:2px 9px;text-align:center;"
                       "font-size:12px;color:#c9d1d9")

                def _mtd(cell, j):
                    return ("<td style='%s%s'>%s</td>"
                            % (mtd,
                               ";background:rgba(197,138,249,.10)"
                               if j == last_col else "", cell))

                mrows = []
                for v in tvs:
                    nm2 = (v.get("name") or "")[:2]
                    cur = (v.get("stance2") if v.get("stance2") is not None
                           else (v.get("stance") or 0))
                    cells = ["%+d" % (v.get("stance") or 0), "%+d" % cur]
                    for h in hs:
                        st3 = None
                        for rp in h.get("replies") or []:
                            if rp.get("who") == v.get("name"):
                                st3 = rp.get("stance3")
                        if st3 is None:
                            cells.append("<span style='color:var(--gray-5)'>○"
                                         "</span>")
                        else:
                            cells.append(
                                ("<b style='color:#c58af9'>%+d</b>" % st3)
                                if st3 != cur else "%+d" % st3)
                            cur = st3
                    mrows.append(
                        "<tr><td style='%s;font-weight:600'>%s</td>%s</tr>"
                        % (mtd, e(nm2),
                           "".join(_mtd(c, j)
                                   for j, c in enumerate(cells))))
                pf = fin.get("stance")
                fcells = ["<span style='color:var(--gray-5)'>—</span>",
                          ("%+d" % pf) if pf is not None
                          else "<span style='color:var(--gray-5)'>—</span>"]
                for h in hs:
                    f2 = h.get("final2") or {}
                    fs = f2.get("stance")
                    if fs is None:
                        fcells.append("<span style='color:var(--gray-5)'>○"
                                      "</span>")
                    else:
                        fcells.append(
                            ("<b style='color:#d2a8ff'>%+d</b>" % fs)
                            if fs != pf else "%+d" % fs)
                        pf = fs
                matrix = ("<div style='margin-top:10px;overflow-x:auto'>"
                          "<table style='border-collapse:collapse;"
                          "border:1px solid var(--gray-4)'>"
                          "<tr><th style='%s'></th>%s</tr>%s"
                          "<tr><td style='%s;color:#d2a8ff;font-weight:600'"
                          ">终裁</td>%s</tr></table></div>"
                          % (mth,
                             "".join("<th style='%s'>%s</th>"
                                     % (mth, e(c)) for c in cols),
                             "".join(mrows), mtd,
                             "".join(_mtd(c, j)
                                     for j, c in enumerate(fcells))))
                # K79 即时反馈横幅：插话 10 分钟内，图顶直接给重测结论
                banner = ""
                if hs and recent:
                    pv = {v.get("name"):
                          (v.get("stance2") if v.get("stance2") is not None
                           else (v.get("stance") or 0)) for v in tvs}
                    for h in hs[:-1]:
                        for rp in h.get("replies") or []:
                            nm2 = rp.get("who") or ""
                            if "操盘手" in nm2 and \
                                    rp.get("stance3") is not None:
                                pv[nm2] = rp.get("stance3")
                    parts = []
                    for v in tvs:
                        nm2 = v.get("name")
                        st3 = None
                        for rp in hs[-1].get("replies") or []:
                            if rp.get("who") == nm2:
                                st3 = rp.get("stance3")
                        if st3 is None:
                            parts.append("%s 未重测" % nm2[:2])
                        elif st3 != pv.get(nm2):
                            parts.append(
                                "<b style='color:#c58af9'>%s %+d→%+d</b>"
                                % (nm2[:2], pv.get(nm2) or 0, st3))
                        else:
                            parts.append("%s 维持%+d" % (nm2[:2], st3))
                    f2v = ((hs[-1].get("final2") or {}).get("verdict")
                           or "—")
                    banner = ("<div style='margin:8px 0;padding:8px 12px;"
                              "background:rgba(197,138,249,.10);border:"
                              "1px solid #c58af9;border-radius:8px;"
                              "font-size:13px'>🎤 你的插话已重测：%s｜"
                              "终裁 %s</div>"
                              % ("、".join(parts), f2v))
                return ("<div class='sec'><h2>📈 立场演化轨迹"
                        "（插话重测可视化）</h2>%s"
                        "<svg width='%d' height='%d' style='background:"
                        "rgba(255,255,255,.03);border-radius:8px'>"
                        "<style>@keyframes k79p{0%%,100%%"
                        "{stroke-opacity:.15}50%%{stroke-opacity:.9}}"
                        "</style>%s%s%s%s%s%s"
                        "</svg><div style='margin-top:6px'>%s"
                        "<span style='color:var(--gray-5);font-size:11px;"
                        "margin-left:10px'>白圈=立场变化点</span>"
                        "<span style='color:#c58af9;font-size:11px;"
                        "margin-left:10px'>💬列=老板插话后重测</span>"
                        "</div>%s%s</div>"
                        % (banner, W, H, gl, band, cl, "".join(lines),
                           "".join(rings), "".join(dots), legend, quotes,
                           matrix))

            def _say_effect(rr):
                """K76 插话效果摘要：每轮说服了谁（方向）+ 终裁判定链。"""
                if not hs:
                    return ""
                prev = {v.get("name"):
                        (v.get("stance2") if v.get("stance2") is not None
                         else (v.get("stance") or 0)) for v in tvs}
                fin0 = (rr.get("final") or {}).get("verdict")
                last_fin = fin0
                out = []
                for i, h in enumerate(hs):
                    moved = []
                    for rp in h.get("replies") or []:
                        nm = rp.get("who") or ""
                        if "操盘手" not in nm:
                            continue
                        st3 = rp.get("stance3")
                        if st3 is not None and st3 != prev.get(nm):
                            moved.append("%s %+d→%+d" % (nm[:2],
                                                         prev.get(nm) or 0,
                                                         st3))
                            prev[nm] = st3
                    f2 = (h.get("final2") or {}).get("verdict")
                    judge = ("改判 %s→%s" % (last_fin, f2)
                             if f2 and f2 != last_fin
                             else "维持%s" % (f2 or "—"))
                    last_fin = f2 or last_fin
                    out.append("插话%d（%s）：说服 %s｜终裁%s"
                               % (i + 1, (h.get("ts") or "")[5:16],
                                  "、".join(moved) if moved else "无人",
                                  judge))
                return ("<div class='sec'><h2>🎯 插话效果</h2>%s</div>"
                        % "".join("<div style='padding:3px 0;font-size:"
                                   "13px'>%s</div>" % e(s) for s in out))

            LINE = {"host": "#8ab4f8", "trader": "#fdd663", "vote": "#7ee787",
                    "final": "#f28b82", "human": "#c58af9",
                    "final2": "#d2a8ff"}
            NAME = {"host": "🏛", "trader": "⚔", "vote": "🗳", "final": "⚖",
                    "human": "👤", "final2": "⚖✎"}
            cards = "".join(
                "<div style='margin:10px 0;padding:10px 14px;background:"
                "rgba(255,255,255,.04);border-left:3px solid %s;"
                "border-radius:6px'><div style='margin-bottom:2px'>"
                "<b style='font-size:13.5px'>%s %s</b>%s</div>"
                "<div style='white-space:pre-wrap;font-size:13px;"
                "line-height:1.75'>%s</div></div>"
                % (LINE.get(c, "#8b949e"), NAME.get(c, ""), e(who),
                   (" <span class='bdg %s'>%s</span>"
                    % ("g" if t.startswith(("+", "支持", "高"))
                       else ("r" if t.startswith(("-", "反对", "低"))
                             else "n"), e(t))) if t else "",
                   e(txt))
                for who, txt, t, c in flow)
            fin = r.get("final") or {}
            saybox = ("<div class='sec' id='saybox'><h2>🎤 老板插话</h2>"
                      "<div class='dim' style='margin:0 0 8px;font-size:12.5px'>"
                      "以雇主身份加入本场辩论——主持人（分析师团队）与三位"
                      "操盘手将各自回应你（约 10-20 秒）。</div>"
                      "<textarea id='sayta' rows='3' style='width:98%%;"
                      "background:var(--gray-0);color:var(--gray-7);border:1px solid "
                      "var(--gray-4);border-radius:6px;padding:8px;font-size:13px'"
                      " placeholder='例：我认为量能不足，等回踩 10 日线再谈"
                      "进场；或直接质询某位操盘手…'></textarea>"
                      "<div style='margin-top:6px'><button onclick=_say(this) "
                      "style='%s;cursor:pointer'>▶ 发言</button></div></div>"
                      % _PILL)
            sjs = ("<script>var P=%d;function _say(btn){var ta=document."
                   "getElementById('sayta');var t=ta.value.trim();"
                   "if(!t){alert('说点什么');return}btn.disabled=true;"
                   "btn.textContent='辩论中…';"
                   "fetch('/api/debate/say',{method:'POST',headers:{'Content-"
                   "Type':'application/json'},body:JSON.stringify({p:P,text:"
                   "t})}).then(function(r){return r.json()}).then(function(j)"
                   "{if(j.ok){location.reload()}else{alert(j.error||'失败');"
                   "btn.disabled=false;btn.textContent='▶ 发言'}}).catch("
                   "function(){alert('网络错误');btn.disabled=false;"
                   "btn.textContent='▶ 发言'})}</script>" % idx)
            vt = r.get("vote") or {}
            content = ("<h1>🎤 模拟辩论室 · %s %s</h1>"
                       "<div class='dim' style='margin:2px 0 10px'>%s 预演"
                       "（来源 %s）· 分析师票%s（%+.1f）→ 终裁 <b>%s</b>"
                       "　<a href='/sim' style='color:var(--blu-500)'>← 返回锦标赛"
                       "</a></div>%s%s%s%s"
                       % (e(r.get("code") or ""), e(r.get("name") or ""),
                          r.get("d") or "",
                          e(SID_LABEL.get(r.get("sid"), r.get("sid") or "—")),
                          vt.get("verdict"), vt.get("wsum") or 0,
                          e(fin.get("verdict") or "—"),
                          _stance_chart(r), _say_effect(r), cards, saybox))
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>模拟辩论室 · %s</title>"
                    % e(r.get("code") or "")
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("debate") + content + sjs + "</body></html>")
            self._html(body)
            return
        # ── 列表页：场次卡片 ──
        def _lchips(x):
            # K81 列表页跟随：插话后 stance3 紫段（与 /sim _srow 同源逻辑）
            hs = x.get("human") or []
            st3map = {}
            for h in hs:
                for rp in h.get("replies") or []:
                    w = rp.get("who") or ""
                    if "操盘手" in w and rp.get("stance3") is not None:
                        st3map[w] = rp.get("stance3")
            out = ""
            for v in (x.get("traders") or []):
                st = v.get("stance") or 0
                st2 = v.get("stance2")
                lab = "%s%+d" % ((v.get("name") or "")[:2], st)
                if st2 is not None and st2 != st:
                    lab += "→%+d" % st2
                seg = ""
                cur = st2 if st2 is not None else st
                st3 = st3map.get(v.get("name") or "")
                if st3 is not None and st3 != cur:
                    seg = "<b style='color:#c58af9'>→%+d</b>" % st3
                    cur = st3
                out += ("<span class='bdg %s'>%s%s</span> "
                        % ("g" if cur > 0
                           else ("r" if cur < 0 else "n"),
                           e(lab), seg))
            return out or "<span class='dim'>—</span>"

        rows = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td><td>%s</td>"
            "<td>%s（w%+.1f）</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (x.get("d"), e(x.get("code") or ""), e(x.get("code") or ""),
               e(x.get("name") or ""),
               e(SID_LABEL.get(x.get("sid"), x.get("sid") or "—")),
               (x.get("vote") or {}).get("verdict"),
               (x.get("vote") or {}).get("wsum") or 0,
               _lchips(x),
               e((x.get("final") or {}).get("verdict") or "—"),
               ("<a href='/debate?p=%d' style='color:var(--blu-500)'>🎤%s "
                "进场辩论</a>"
                % (i, ("×%d" % len(x.get("human") or []))
                   if x.get("human") else "")))
            for i, x in enumerate(rev[:30]))
        if not rows:
            rows = ("<tr><td colspan=7 class='dim'>尚无预演场次——先到 "
                    "<a href='/sim' style='color:var(--blu-500)'>/sim</a> 点「预演"
                    "当日候选票」</td></tr>")
        # ── K85/K96 常驻统计：老板插话 → 终裁改判率 + 操盘手撬动率
        # （全量行累计：插话跨重跑累计；场级数字按 (code,d) 去重）
        # 口径：有效重测轮=该轮有 final2 且原终裁在；改判轮=final2
        # verdict ≠ 原终裁；操盘手撬动人次=重测立场（stance3）偏离其
        # 链式当前立场（初始=stance2，每轮重测后滚动）的人次，
        # 主持人回复 stance3=None 自动排除
        n_r = n_r2 = n_chg = 0
        n_tr3 = n_tr_move = 0
        _hk = set()  # 有插话的场
        _ck = set()  # 被撬动（终裁改判）的场
        for x in allp:
            hs = x.get("human") or []
            if not hs:
                continue
            _hk.add((x.get("code"), x.get("d")))
            fv = (x.get("final") or {}).get("verdict")
            sess_chg = False
            tcur = {t.get("name"): t.get("stance2")
                    for t in x.get("traders") or []}
            for h in hs:
                n_r += 1
                for rp in h.get("replies") or []:
                    s3 = rp.get("stance3")
                    nm = rp.get("who")
                    if s3 is None or nm not in tcur:
                        continue
                    cur = tcur[nm]
                    if cur is not None:
                        n_tr3 += 1
                        if s3 != cur:
                            n_tr_move += 1
                    tcur[nm] = s3
                f2 = (h.get("final2") or {}).get("verdict")
                if not f2 or not fv:
                    continue
                n_r2 += 1
                if f2 != fv:
                    n_chg += 1
                    sess_chg = True
            if sess_chg:
                _ck.add((x.get("code"), x.get("d")))
        n_h = len(_hk)
        n_sess_chg = len(_ck)
        rate = ("%.0f%%" % (100.0 * n_chg / n_r2)) if n_r2 else "—"
        trate = ("%.0f%%" % (100.0 * n_tr_move / n_tr3)) if n_tr3 else "—"
        hstat = ("<div class='dim' style='margin:2px 0 10px'>"
                 "📊 插话统计：%d 场预演 / %d 场有插话 · 插话 %d 轮"
                 "（有效重测 %d）· 终裁改判 "
                 "<b style='color:#c58af9'>%d 轮</b>（撬动 %d 场）· "
                 "改判率 <b style='color:#c58af9'>%s</b> · "
                 "操盘手撬动 <b style='color:#c58af9'>%d/%d 人次</b>"
                 "（<b style='color:#c58af9'>%s</b>）</div>"
                 % (len(props), n_h, n_r, n_r2, n_chg, n_sess_chg,
                    rate, n_tr_move, n_tr3, trate))
        content = ("<h1>🎤 模拟辩论室</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K74 分析师"
                   "与操盘手的五幕辩论场（预演留痕回放）+ <b>老板插话</b>"
                   "——以雇主身份入场，主持人与三位操盘手实时回应你。</div>"
                   "%s"
                   "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                   "<th>来源</th><th>分析师票</th><th>操盘手（含演化）</th>"
                   "<th>终裁</th><th>进场</th></tr>%s</table>"
                   % (hstat, rows))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>模拟辩论室</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("debate") + content + "</body></html>")
        self._html(body)
    def _council_page(self, qs):
        """K55-C4 投委会留痕页：三张 jsonl 浏览+检索 + 影子统计/转正门槛
        + 记忆使用统计。服务端渲染，只读 reports 不写任何文件。"""
        import html as _h
        from urllib.parse import parse_qs as _pq
        e = _h.escape
        qp = _pq(qs)
        q = ((qp.get("q") or [""])[0] or "").strip().lower()

        def _jl(name):
            out = []
            try:
                with open(os.path.join(REPORTS_DIR, name),
                          encoding="utf-8") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
            return out

        props = _jl("council_proposals.jsonl")
        watch = [r for r in _jl("council_watch.jsonl")
                 if r.get("type") == "watch"]
        revs = _jl("council_reviews.jsonl")
        stats = {}
        try:
            with open(os.path.join(REPORTS_DIR, "council_shadow_stats.json"),
                      encoding="utf-8") as f:
                stats = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)

        def hit(r):
            return (not q) or q in str(r.get("code") or "").lower() \
                or q in str(r.get("name") or "").lower()

        def _vcol(v):
            if v == "支持":
                return "<span class='bdg g'>支持</span>"
            if v == "反对":
                return "<span class='bdg r'>反对</span>"
            return "<span class='bdg n'>%s</span>" % e(str(v or "中性"))

        def _pc(x):
            return ("%+.2f%%" % x) if x is not None else "—"

        def _pw(x):
            return ("%.1f%%" % x) if x is not None else "—"

        g, corr = stats.get("gate") or {}, stats.get("corr")
        gs = g.get("status") or "暂无统计（等待投委会运行）"
        gb = "g" if gs.startswith("显著") else \
            "n" if gs.startswith("不显著") else "y"
        cards = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>"
                 "<div class='sstat'><span class='st'>立案记录</span>"
                 "<span class='sv'>%d</span><span class='su'>去重 %d 票</span></div>"
                 "<div class='sstat'><span class='st'>前向收益样本</span>"
                 "<span class='sv'>%s</span><span class='su'>转正门槛 30 票</span></div>"
                 "<div class='sstat'><span class='st'>分歧度-r20</span>"
                 "<span class='sv'>%s</span><span class='su'>%s</span></div>"
                 "<div class='sstat'><span class='st'>影子状态</span>"
                 "<span class='sv'>%s</span><span class='su'>只留痕不拦截</span></div>"
                 "</div>"
                 % (stats.get("n_prop_total") or 0, stats.get("n_uniq") or 0,
                    "%d/30" % (g.get("n") or 0),
                    ("r=%.2f t=%.1f" % (corr["r"], corr["t"])) if corr else "n/a",
                    ("n=%d（Pearson）" % corr["n"]) if corr else "样本积累中",
                    e(gs).replace("显著·建议转正评估（人工拍板）",
                                  "显著·建议转正评估")))
        method = ("<div class='sec'><h2>转正门槛（口径透明）</h2>"
                  "<div style='font-size:13.5px;line-height:1.8'>"
                  "影子期并行不打扰产线；累计 <b>≥30 票</b>对照「分歧度-前向收益」"
                  "相关性显著（|t|≥2 近似 5%%）才建议转正评估，人工拍板；"
                  "转正后仍只获<b>建仓 skip 建议权</b>，资金动作人工确认铁律不变。"
                  "前向收益 rN = 建仓后第 N 个交易日收盘 / 建仓日收盘 - 1"
                  "（N=5/20，假日停牌自然顺延累积）。n&lt;30 只记录不评判"
                  "（K33 影子纪律）。状态：<span class='bdg %s'>%s</span></div>"
                  "</div>" % (gb, e(gs)))
        bd_rows = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (x["bucket"], x["n"], _pw(x.get("win5")),
               _pc(x.get("r5")), _pc(x.get("r20")))
            for x in (stats.get("by_dissent") or []))
        tbl_d = ("<div class='sec'><h2>分歧度分桶 × 前向收益</h2>"
                 "<table class='sticky'><tr><th>分歧度</th><th>n</th>"
                 "<th>r5 胜率</th><th>r5 均值</th><th>r20 均值</th></tr>%s"
                 "</table></div>" % bd_rows)
        bv_rows = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_vcol(x["verdict"]), x["n"], _pw(x.get("win5")),
               _pc(x.get("r5")), _pc(x.get("r20")))
            for x in (stats.get("by_verdict") or []))
        tbl_v = ("<div class='sec'><h2>备忘录结论 × 前向收益</h2>"
                 "<table class='sticky'><tr><th>结论</th><th>n</th>"
                 "<th>r5 胜率</th><th>r5 均值</th><th>r20 均值</th></tr>%s"
                 "</table></div>" % bv_rows)
        mem = stats.get("mem") or {}
        cited = "".join("<li>%s <span class='bdg y'>×%d</span></li>" % (e(t), n)
                        for t, n in (mem.get("cited_top") or [])) or \
            ("<li class='dim'>尚无引用——复盘会产出经验后自动检索注入立案会"
             "（月度按「被引用×事后有效」淘汰）</li>")
        mem_sec = ("<div class='sec'><h2>记忆池（%d 条经验 · 检索注入立案会）</h2>"
                   "<ul style='margin:4px 0 0 18px;line-height:2'>%s</ul></div>"
                   % (mem.get("pool") or 0, cited))
        samp = {"%s|%s|%s" % (s.get("sid"), s.get("code"), s.get("d")): s
                for s in (stats.get("samples") or [])}

        def _tk(code, name):
            tc = str(code or "")
            if tc.endswith((".SH", ".SZ", ".BJ")):
                return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc),
                                                          e(name or ""))
            return "%s %s" % (e(tc), e(name or ""))

        def _dcol(p):   # K62 影子管理指令 + 风格桶徽章
            dr = p.get("directive") or {}
            act, sty = dr.get("act") or "", p.get("style") or ""
            if not act and not sty:
                return "<span class='dim'>—</span>"
            cls = "r" if act in ("平仓预警", "减仓预警") else \
                "y" if act == "收紧盯盘" else "g"
            s = ("<span class='bdg %s'>%s</span>" % (cls, e(act))) if act else ""
            if sty:
                s += " <span class='bdg n'>%s桶</span>" % e(sty)
            return s

        def _kcol(p):   # K67 票/系数列：投票结论 + 仓位系数（放/缩着色）
            vt = p.get("vote") or {}
            k = ((p.get("directive") or {}).get("sizing") or {}).get("k")
            v = vt.get("verdict")
            if v is None and k is None:
                return "<span class='dim'>—</span>"
            kk = k if k is not None else 1.0
            cls = "up" if kk > 1.0 else ("dn" if kk < 1.0 else "")
            return "%s<span class='%s'>×%.1f</span>" % (
                ("票%s " % e(v)) if v else "", cls, kk)

        fp = [x for x in props if hit(x)][::-1][:50]
        rp = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td>"
            "<td class='%s'>%s</td>"
            "<td>%s</td><td>%s</td></tr>"
            % (p.get("d"), _tk(p.get("code"), p.get("name")),
               e(p.get("sid") or ""),
               p.get("dissent") if p.get("dissent") is not None else "—",
               _vcol((p.get("memo") or {}).get("verdict")),
               _kcol(p),
               _dcol(p),
               e(str((p.get("memo") or {}).get("conviction") or "—")),
               "up" if ((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                                p.get("d"))) or {}
                         ).get("r5") or 0) >= 0 else "dn",
               _pc((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                           p.get("d"))) or {}).get("r5")),
               "up" if ((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                                p.get("d"))) or {}
                         ).get("r20") or 0) >= 0 else "dn",
               _pc((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                           p.get("d"))) or {}).get("r20")),
               e(str((p.get("memo") or {}).get("thesis") or "")[:70]),
               e(str(((p.get("views") or {}).get("bear") or {})
                     .get("answer") or "")[:70]))
            for p in fp) or \
            "<tr><td colspan=12 class='dim'>暂无立案留痕</td></tr>"
        tbl_p = ("<div class='sec'><h2>🏛 立案备忘录（%d 条 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>日期</th><th>标的</th>"
                 "<th>账户</th><th>分歧</th><th>结论</th><th>票/系数</th>"
                 "<th>指令/桶</th>"
                 "<th>信心</th>"
                 "<th>r5</th><th>r20</th><th>核心逻辑</th><th>空头要点</th>"
                 "</tr>%s</table></div>" % (len([x for x in props if hit(x)]),
                                            rp))
        fw = [x for x in watch if hit(x)][::-1][:50]
        rw = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td></tr>"
            % (w.get("d"), _tk(w.get("code"), w.get("name")),
               "<span class='bdg n'>%s</span>" % e(w.get("style") or "稳健"),
               "<span class='bdg r'>需注意</span>"
               if (w.get("judge") or {}).get("level") == "需注意"
               else "<span class='bdg n'>无影响</span>",
               e(str((w.get("judge") or {}).get("bias") or "?")),
               e(str((w.get("judge") or {}).get("view") or "")[:90]))
            for w in fw) or \
            "<tr><td colspan=6 class='dim'>暂无盯盘留痕</td></tr>"
        tbl_w = ("<div class='sec'><h2>👁 盯盘留痕（%d 条 LLM 解读 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>日期</th><th>标的</th>"
                 "<th>桶</th><th>级别</th><th>偏性</th><th>解读</th></tr>%s"
                 "</table></div>"
                 % (len([x for x in watch if hit(x)]), rw))
        fr = [x for x in revs if hit(x)][::-1][:50]
        rr = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (r.get("d"), _tk(r.get("code"), r.get("name")),
               e(r.get("sid") or ""),
               "up" if (r.get("ret_pct") or 0) >= 0 else "dn",
               _pc(r.get("ret_pct")),
               "<span class='bdg r'>对</span>"
               if r.get("bear_right") == "对" else
               "<span class='bdg g'>错</span>"
               if r.get("bear_right") == "错" else
               "<span class='bdg n'>%s</span>" % e(str(r.get("bear_right")
                                                       or "—")),
               e(str(r.get("attribution") or "—")),
               e(str(r.get("retrospect") or "")[:70]),
               e(str(((r.get("lessons") or [{}])[0].get("text")
                      or ""))[:70] if r.get("lessons") else "—"))
            for r in fr) or \
            "<tr><td colspan=8 class='dim'>暂无平仓复盘留痕</td></tr>"
        tbl_r = ("<div class='sec'><h2>🧾 平仓复盘（%d 条 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>平仓日</th><th>标的</th>"
                 "<th>账户</th><th>盈亏</th><th>空头</th><th>归因</th>"
                 "<th>回看</th><th>首条经验</th></tr>%s</table></div>"
                 % (len([x for x in revs if hit(x)]), rr))
        form = ("<form method='get' action='/council' "
                "style='display:flex;gap:6px;margin:4px 0 14px;flex-wrap:wrap;"
                "align-items:center'>"
                "<input name='q' value='%s' "
                "placeholder='按代码/名称检索三张留痕表' "
                "style='padding:7px 10px;border:1px solid var(--gray-4);"
                "border-radius:8px;background:var(--gray-0);color:var(--gray-7);"
                "font-size:13px;min-width:260px'>"
                "<button style='%s;cursor:pointer'>检索</button>%s</form>"
                % (e(q), _PILL,
                   (" <a href='/council' style='%s'>清除</a>" % _PILL)
                   if q else ""))
        # K64d 半自动执行链：待确认预警队列（人工确认→执行，忽略→出队）
        pend = _council_pending()

        def _prow(it):
            def _b(op, txt):
                return ("<button onclick=\"_cfn('%s','%s','%s',this)\" "
                        "style='%s;cursor:pointer'>%s</button>"
                        % (e(it.get("sid") or ""), e(it.get("code") or ""),
                           op, _PILL, txt))
            vt = it.get("vote") or {}
            act = it.get("act") or "?"
            k = it.get("k") or (it.get("sizing") or {}).get("k") or 1.0
            bc = "r" if act in ("平仓预警", "减仓预警") else \
                ("g" if act == "加仓建议" else "y")   # K67 建议票配色
            alab = act + (
                (" %+d%%" % it.get("pct"))
                if act == "加仓建议" and it.get("pct")
                else (" -%d%%" % it.get("pct"))
                if act == "减仓建议" and it.get("pct")
                else (" %d%%" % it.get("pct"))
                if act == "减仓预警" and it.get("pct") else "")
            return ("<tr><td>%s</td><td>%s</td><td>%s</td>"
                    "<td><span class='bdg %s'>%s</span></td><td>%s</td>"
                    "<td>%s</td><td style='white-space:nowrap'>%s %s</td>"
                    "</tr>"
                    % (e(it.get("d") or "—"),
                       _tk(it.get("code"), it.get("name")),
                       e(it.get("sid") or ""), bc, e(alab),
                       ("×%.1f" % k) if k else "—",
                       ("%s %+.1f" % (vt.get("verdict") or "—",
                                      vt.get("wsum") or 0))
                       if vt.get("wsum") is not None else "—",
                       _b("confirm", "✔ 确认执行"),
                       _b("dismiss", "✖ 忽略")))

        if pend:
            sec_p = ("<div class='sec'><h2>⚡ 待确认执行（%d 条 · K64d 预警"
                     " + K67 系数建议）</h2><div class='dim' style='margin:"
                     "0 0 8px;font-size:12.5px'><b>人工确认后才动仓</b>："
                     "平仓预警→全平 · 减仓预警→减半 · 加仓建议（k≥1.1 激进"
                     "同向）→add %+d%% · 减仓建议（k≤0.9 保守同向/偏空）→"
                     "trim 同比，pct=|k-1|×100；忽略=出队不动仓。</div>"
                     "<table class='sticky'><tr><th>预警日</th>"
                     "<th>标的</th><th>账户</th><th>指令</th>"
                     "<th>仓位系数</th><th>投票</th><th>操作</th></tr>%s"
                     "</table></div>"
                     % (len(pend),
                        next((x.get("pct") or 10 for x in pend.values()
                              if x.get("act") == "加仓建议"), 10),
                        "".join(_prow(x) for x in
                                sorted(pend.values(),
                                       key=lambda x: x.get("queued_at")
                                       or ""))))
        else:
            sec_p = ""
        content = ("<h1>🏛 多 Agent 投委会 · 影子留痕</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K55 六视角"
                   "（多头/空头/激进/保守/技术/情绪）→ 主持人备忘录 · 四会工作流"
                   "（立案/盯盘/平仓/复盘）· 三张表 append-only · LLM 永不碰交易 · "
                   "资金动作人工确认 · 统计更新 %s · "
                   "<a href='/sim' style='color:var(--blu-500)'>♠ 辩论结果交三操盘手"
                   "六本账模拟 →</a></div>%s%s%s%s%s%s%s%s%s%s"
                   % (e(stats.get("updated") or "—"), form, sec_p, cards,
                      method, tbl_d, tbl_v, mem_sec, tbl_p, tbl_w, tbl_r))
        cjs = ("<script>function _cfn(sid,code,op,btn){"
               "if(btn)btn.disabled=true;"
               "var m=op==='confirm'?"
               "'确认执行该预警？（将真实改动模拟盘账本：平仓/减仓）':"
               "'忽略该预警并出队？';"
               "if(!window.confirm(m)){if(btn)btn.disabled=false;return;}"
               "fetch('/api/council/confirm',{method:'POST',"
               "headers:{'Content-Type':'application/json'},"
               "body:JSON.stringify({sid:sid,code:code,op:op})})"
               ".then(function(r){return r.json();})"
               ".then(function(j){if(j.ok){alert('已'+(op==='confirm'?"
               "'执行':'忽略')+'：'+(j.act||''));location.reload();}"
               "else{alert('失败：'+(j.error||''));"
               "if(btn)btn.disabled=false;}})"
               ".catch(function(e){alert('网络错误：'+e);"
               "if(btn)btn.disabled=false;});}</script>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>投委会 · 影子留痕</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("council") + content + cjs + "</body></html>")
        self._html(body)   # K-STOCK-141 修复：批7搬运切割丢失收尾行（handler 算完 body 不发响应即 return → /council 空连接断开）
    _PAPER_QD_LOCK = threading.Lock()   # 进程内快速拒绝重复点击
    _PAPER_DIR = _PAPER_DIR_ROOT
    _PAPER_DATA_DIR = _PAPER_DATA_DIR_ROOT
    _PAPER_DB = _PAPER_DB_ROOT
    # ---------------- K-STOCK 模拟仓写路径（v0.2 平台化 2026-10-09） ----------------
    # 设计：portal_api 保持纯标准库——引擎调用一律 subprocess CLI（py311 绝对
    # 路径，带 pandas；与 07:05 automation 同一口径）。每次下单一个干净子进程，
    # 门户进程零 pandas / 零第二写连接 / 零共享账本状态。鉴权：端点已入
    # _MUTATION_POSTS（本机放行；局域网与脚本须 X-Portal-Token，同全站口径）。
    _PAPER_PY = sys.executable

    def _api_paper_stop(self, raw):
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        stock = str(rec.get("stock") or "").strip().upper()
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", stock):
            self._json({"ok": False,
                        "error": "股票代码格式：6位数字.SH/.SZ/.BJ"}, 400)
            return
        if not rec.get("clear") and rec.get("stop_price") is None:
            self._json({"ok": False, "error": "需要 stop_price 或 clear=true"}, 400)
            return
        argv = ["order", "--side", "STOP", "--stock", stock]
        if rec.get("clear"):
            argv.append("--clear")
        else:
            argv += ["--stop-price", str(rec["stop_price"])]
        try:
            r = self._paper_run_json(
                [self._PAPER_PY, os.path.join(self._PAPER_DIR, "cli.py")] + argv)
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        self._json(r, 200 if r.get("ok") else 400)
    def _api_paper_order(self, raw):
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mode = str(rec.get("mode") or "manual").strip()
        try:
            if mode == "double_knife":
                # 真幂等：查账本今天是否已有双刀落单（文件存在≠已执行，
                # dry-run 也产 picks 文件）；并发防重：进程内互斥锁。
                if not self._PAPER_QD_LOCK.acquire(blocking=False):
                    self._json({"ok": False,
                                "error": "双刀执行进行中，请勿重复提交"}, 409)
                    return
                _qd_file_lock = _acquire_paper_qd_lock()
                if _qd_file_lock is None:
                    self._PAPER_QD_LOCK.release()
                    self._json({"ok": False,
                                "error": "双刀执行进行中，请勿重复提交"}, 409)
                    return
                try:
                    import sqlite3 as _sq3
                    con = _sq3.connect(
                        f"file:{self._PAPER_DB}?mode=ro", uri=True)
                    try:
                        n = con.execute(
                            "SELECT COUNT(*) FROM orders "
                            "WHERE ref LIKE 'poc_qd_%' AND account_id='main' AND ts LIKE ?",
                            (time.strftime("%Y-%m-%d") + "%",)).fetchone()[0]
                    finally:
                        con.close()
                    if n:
                        self._json({"ok": False,
                                    "error": "今日双刀信号已执行过（账本已有落单）"}, 409)
                        return
                    r = self._paper_run_json(
                        [self._PAPER_PY,
                         os.path.join(self._PAPER_DIR, "qd_adapter.py"),
                         "--json"], timeout=60)
                finally:
                    _release_paper_qd_lock(_qd_file_lock)
                    self._PAPER_QD_LOCK.release()
            else:
                side = str(rec.get("side") or "").upper()
                stock = str(rec.get("stock") or "").strip().upper()
                if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", stock):
                    self._json({"ok": False,
                                "error": "股票代码格式：6位数字.SH/.SZ/.BJ"}, 400)
                    return
                if side == "BUY":
                    qty = rec.get("qty")
                    if not isinstance(qty, int) or qty <= 0 or qty % 100:
                        self._json({"ok": False,
                                    "error": "qty 须为 100 的整数倍股数"}, 400)
                        return
                elif side == "SELL":
                    if not rec.get("all") and not rec.get("qty"):
                        self._json({"ok": False,
                                    "error": "SELL 需要 qty 或 all=true"}, 400)
                        return
                else:
                    self._json({"ok": False, "error": "side 须为 BUY/SELL"}, 400)
                    return
                argv = ["order", "--side", side, "--stock", stock]
                if side == "SELL" and rec.get("all"):
                    argv.append("--all")
                elif rec.get("qty"):
                    argv += ["--qty", str(rec["qty"])]
                if rec.get("price"):
                    argv += ["--price", str(rec["price"])]
                if rec.get("stop_price"):
                    argv += ["--stop-price", str(rec["stop_price"])]
                r = self._paper_run_json(
                    [self._PAPER_PY, os.path.join(self._PAPER_DIR, "cli.py")] + argv)
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "引擎子进程执行超时"}, 504)
            return
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        self._json(r, 200 if r.get("ok") else 400)
    def _paper_desk(self):
        """交易台动态块：AI / 双刀最近决策产物（纯标准库 glob+json 只读）。"""
        out = {}
        d = r"C:\项目\老田的AI助手\k_stock\data"
        try:
            qd = sorted(glob.glob(os.path.join(d, "poc_qd_*.json")))
            try:  # 与下单防重同口径：查账本（文件存在≠已执行）
                import sqlite3 as _sq3
                con = _sq3.connect(
                    f"file:{self._PAPER_DB}?mode=ro", uri=True)
                try:
                    out["qd_ran_today"] = bool(con.execute(
                        "SELECT COUNT(*) FROM orders "
                        "WHERE ref LIKE 'poc_qd_%' AND account_id='main' AND ts LIKE ?",
                        (time.strftime("%Y-%m-%d") + "%",)).fetchone()[0])
                finally:
                    con.close()
            except Exception:
                out["qd_ran_today"] = False
            if qd:
                f = qd[-1]
                m = {"file": os.path.basename(f),
                     "ts": time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(os.path.getmtime(f)))}
                try:
                    with open(f, encoding="utf-8") as fh:
                        items = json.load(fh)
                    m["signals"] = [{"stock": it.get("stock"),
                                     "name": (it.get("_meta") or {}).get("name"),
                                     "close": (it.get("_meta") or {}).get("close")}
                                    for it in items]
                except Exception:
                    m["signals"] = []
                out["qd_last_run"] = m
            ai = [x for x in glob.glob(os.path.join(d, "poc_*.json"))
                  if not os.path.basename(x).startswith("poc_qd_")]
            if ai:
                f = max(ai, key=os.path.getmtime)
                out["ai_last_run"] = {
                    "file": os.path.basename(f),
                    "ts": time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(os.path.getmtime(f)))}
        except Exception as e:  # desk 是增强信息，失败不拖垮 status
            out["error"] = repr(e)
        return out
    def _paper_run_json(self, cmd, timeout=35):
        """跑子进程并解析 stdout 尾部 JSON（CLI --json 模式单行输出）。

        🔴 中文 Windows 坑：子进程管道默认按系统 locale（GBK）编码 stdout，
        门户侧按 UTF-8 解码必花 → 强制子进程 PYTHONIOENCODING=utf-8。
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        if out:
            try:
                return json.loads(out.splitlines()[-1])
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        return {"ok": False,
                "error": err.splitlines()[-1] if err else (out[-200:] or f"exit={p.returncode}")}
    def _sim_page(self, qs):
        """K68 三操盘手模拟锦标赛页：六本账（3 操盘手 × 2 盘算）真实日K
        买卖点战绩榜，对照买入持有基准。        只读 reports 两个 json。"""
        import html as _h

        def e(x):  # K-STOCK-057 防 float/None 穿透（json 字段类型不稳，escape 只吃 str）
            if x is None:
                return ""
            if not isinstance(x, str):
                x = str(x)
            return _h.escape(x)

        ev, sim = {}, {}
        try:
            with open(os.path.join(REPORTS_DIR, "trader_sim_eval.json"),
                      encoding="utf-8") as f:
                ev = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        try:
            with open(os.path.join(REPORTS_DIR, "trader_sim.json"),
                      encoding="utf-8") as f:
                sim = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        BOOKS = ["激进|积极", "激进|保守", "保守|积极",
                 "保守|保守", "中立|积极", "中立|保守"]
        # K70 策略来源标签（与 stock_council.SID_DESC 同步）
        SID_LABEL = {"qpzm": "青萍之末", "9dim": "九维评分",
                     "sig4": "四策略信号", "chanlun": "缠论结构",
                     "manual": "手动"}

        def _sl(sid):
            return SID_LABEL.get(sid or "", sid or "—")

        def _pc(x):
            return ("%+.2f" % x) if x is not None else "—"

        def _cell(r, bk):
            x = (r.get("books") or {}).get(bk)
            if not x:
                return "<td class='dim'>—</td>"
            tip = "%s · 出场 %s · 仓位 ×%s" % (
                "/".join(x.get("reason") or ["—"]),
                x.get("exit_d") or "—",
                x.get("size_eff") or "—")
            return ("<td class='%s' title='%s'>%s</td>"
                    % ("up" if (x.get("contrib") or 0) >= 0 else "dn",
                       e(tip), _pc(x.get("contrib"))))

        # 六本账汇总卡
        ag = ev.get("books") or {}
        cards = "".join(
            "<div class='sstat'><span class='st'>%s</span>"
            "<span class='sv'>%s</span><span class='su'>n=%d 胜率 %.0f%% "
            "α %+.2f</span></div>"
            % (e(bk), _pc((ag.get(bk) or {}).get("avg_contrib")),
               (ag.get(bk) or {}).get("n") or 0,
               (ag.get(bk) or {}).get("winrate") or 0.0,
               (ag.get(bk) or {}).get("avg_alpha") or 0.0)
            for bk in BOOKS)
        cards = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>%s</div>" % cards)
        # K70 策略来源 × 操盘手 交叉表（复盘归因：哪个策略的票在哪本账好）
        sids = ev.get("sids") or {}
        srs = "".join(
            "<tr><td><span title='%s'>%s</span></td><td>%d</td>%s"
            "<td class='%s'>%+.2f</td><td><span class='bdg n'>%s</span>"
            "</td></tr>"
            % (e(sid), e((s.get("label") or _sl(sid))),
               s.get("n") or 0,
               "".join("<td class='%s'>%s</td>"
                       % ("up" if (s.get(bk) or 0) >= 0 else "dn",
                          _pc(s.get(bk)) if s.get(bk) is not None else
                          "<span class='dim'>—</span>")
                       for bk in BOOKS),
               "up" if (s.get("total") or 0) >= 0 else "dn",
               s.get("total") or 0.0, e(s.get("best_bk") or "—"))
            for sid, s in sorted(sids.items(),
                                 key=lambda kv: -(kv[1].get("total")
                                                  or 0))) or \
            ("<tr><td colspan=10 class='dim'>暂无策略来源汇总——入场后"
             "自动按策略分组</td></tr>")
        tbl_sid = ("<div class='sec'><h2>📡 策略来源 × 操盘手（复盘归因 · "
                   "哪个策略的票交给哪位操盘手最赚）</h2>"
                   "<table class='sticky'><tr><th>策略来源</th><th>票数</th>"
                   "<th>激进·积极</th><th>激进·保守</th><th>保守·积极</th>"
                   "<th>保守·保守</th><th>中立·积极</th><th>中立·保守</th>"
                   "<th>合计贡献</th><th>最佳账</th></tr>%s</table>"
                   "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                   "行=选股策略（青萍之末/缠论/九维…），列=六本账累计贡献度"
                   "——留痕全链带来源标签，供复盘归因与 AI 再训练取数。</div>"
                   "</div>" % srs)
        # 战绩明细（新→旧）
        rows = sorted(ev.get("rows") or [],
                      key=lambda r: r.get("entry_d") or "", reverse=True)[:60]
        trs = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
            "<td title='%s'>%s</td><td>%s@%s</td>%s<td class='%s'>%s</td>"
            "<td><span class='bdg n'>%s</span></td></tr>"
            % (e(str(r.get("pkey") or "").split("|")[-1]),
               e(r.get("code") or ""), e(r.get("code") or ""),
               e(r.get("name") or ""), e(r.get("sid") or ""),
               e(r.get("sid_label") or _sl(r.get("sid"))),
               e(r.get("entry_d") or "—"), e(r.get("entry") or "—"),
               "".join(_cell(r, bk) for bk in BOOKS),
               "up" if (r.get("baseline") or 0) >= 0 else "dn",
               _pc(r.get("baseline")), e(r.get("best") or "—"))
            for r in rows) or \
            ("<tr><td colspan=12 class='dim'>暂无入场——立案票在 pending 等"
             "日K，或投票反对未接单</td></tr>")
        tbl = ("<div class='sec'><h2>🏆 战绩榜（每票 × 六本账贡献度 · "
               "对照买入持有基准 · 新→旧）</h2>"
               "<table class='sticky'><tr><th>立案日</th><th>标的</th>"
               "<th>策略来源</th><th>入场</th><th>激进·积极</th>"
               "<th>激进·保守</th><th>保守·积极</th><th>保守·保守</th>"
               "<th>中立·积极</th><th>中立·保守</th><th>基准</th>"
               "<th>最佳</th></tr>%s</table>"
               "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
               "单元格=贡献度（收益率×仓位系数×出仓比例），悬停看离场理由"
               "与实际仓位；基准=同窗买入持有。</div></div>" % trs)
        # 持仓明细（六本账在仓）
        pos_rows = []
        for bk in BOOKS:
            for pk, p in ((sim.get("books") or {}).get(bk)
                          or {}).get("pos", {}).items():
                pos_rows.append((bk, p))
        pos_rows.sort(key=lambda x: x[1].get("entry_d") or "", reverse=True)
        pr = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
            "<td title='%s'>%s</td><td>%s@%s</td><td>×%.2f</td>"
            "<td>%s（%s）</td><td>%s</td><td>%d/%d</td><td>%s</td></tr>"
            % (e(bk), e(p.get("code") or ""), e(p.get("code") or ""),
               e(p.get("name") or ""), e(p.get("sid") or ""),
               _sl(p.get("sid")),
               e(p.get("entry_d") or ""), e(p.get("entry") or ""),
               p.get("size_eff") or 0,
               e(p.get("stop") or "—"), e(p.get("stop_rule") or "—"),
               e(str(p.get("tp") or "—")), p.get("n_days") or 0,
               p.get("max_days") or 0, e(p.get("last_d") or "—"))
            for bk, p in pos_rows) or \
            "<tr><td colspan=9 class='dim'>六本账当前空仓</td></tr>"
        tbl_pos = ("<div class='sec'><h2>📒 在仓明细（%d 笔 · 止损线观点线"
                   "优先）</h2><table class='sticky'><tr><th>账本</th>"
                   "<th>标的</th><th>来源</th><th>入场</th><th>仓位</th>"
                   "<th>止损</th><th>止盈</th><th>天数</th><th>最新K</th>"
                   "</tr>%s</table></div>" % (len(pos_rows), pr))
        # 待入场 + 跳过
        pend = ev.get("pending") or {}
        tpr = "".join(
            "<tr><td>%s</td><td>%s %s</td><td title='%s'>%s</td>"
            "<td>×%.1f</td><td>%+d</td>"
            "<td><span class='bdg y'>等日K</span></td></tr>"
            % (e(t.get("d") or "—"), e(t.get("code") or ""),
               e(t.get("name") or ""), e(t.get("sid") or ""),
               _sl(t.get("sid")), t.get("k") or 1.0, t.get("net") or 0)
            for t in sorted(pend.values(),
                            key=lambda x: x.get("d") or "", reverse=True)) or \
            "<tr><td colspan=6 class='dim'>无待入场</td></tr>"
        skp = ev.get("skipped") or {}
        tsk = "".join(
            "<tr><td>%s</td><td>%s %s</td><td class='dim'>%s</td></tr>"
            % (e(s.get("d") or "—"), e(s.get("code") or ""),
               e(s.get("name") or ""), e(s.get("why") or ""))
            for s in sorted(skp.values(),
                            key=lambda x: x.get("d") or "", reverse=True))
        sec_p = ("<div class='sec'><h2>⏳ 待入场（%d 票）</h2>"
                 "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                 "<th>账户</th><th>系数</th><th>净多空</th><th>状态</th>"
                 "</tr>%s</table></div>" % (len(pend), tpr))
        if skp:
            sec_p += ("<div class='sec'><h2>🚫 未接单（%d 票）</h2>"
                      "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                      "<th>原因</th></tr>%s</table></div>"
                      % (len(skp), tsk))
        method = ("<div class='sec'><h2>管线与口径（用户愿景 · 全留痕可回放）"
                  "</h2><div style='font-size:13.5px;line-height:1.9'>"
                  "选股策略（青萍之末/缠论/九维…）出票（<b>来源标签 sid"
                  "+中文名全链贯穿</b>：辩论留痕/模拟留痕/六本账/买卖点均"
                  "可追溯，供复盘归因与 AI 再训练取数）→ 投委会六视角辩论"
                  "（基本"
                  "面在前）→ 结果交三位操盘手：<b>激进</b>（同向放大 ×1.2）/"
                  "<b>保守</b>（同向缩小 ×0.8）/<b>中立</b>（×1.0，K66 口径"
                  "执行层落地）× 两种盘算：<b>积极</b>（满基数 · TP 半止盈+"
                  "追踪 8%% · 40日）/<b>保守</b>（半基数 · TP 全出落袋 · 20日）"
                  "→ 模拟建仓（辩论日次一交易日开盘，反对票不接单）→ 真实日K"
                  "做买卖点（止损跳空按开盘/盘中按止损价；止损线=观点线 ML/RL "
                  "优先、无效兜底激进-6%%/保守-8%%/中立按桶）→ 每票×每账评价"
                  "贡献度，对照买入持有基准。<b>铁律：只写 reports 两个 json"
                  "，绝不碰真实账本。</b>夜链 23:50 随投委会自动推进，统计"
                  "更新 %s。</div></div>" % e(ev.get("ts") or "—"))
        # K69 模拟投委会（预演层）：预演留痕 + 真实对照打分
        score = {}
        try:
            with open(os.path.join(REPORTS_DIR, "council_sim_score.json"),
                      encoding="utf-8") as f:
                score = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        sim_props = []
        try:
            with open(os.path.join(REPORTS_DIR,
                                   "council_sim_proposals.jsonl"),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        sim_props.append(json.loads(line))
                    except Exception:
                        continue
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        cmp_ = {"%s|%s|%s" % (x.get("sid"), x.get("code"), x.get("d")): x
                for x in (score.get("rows") or [])}

        def _vc(v):
            return {"支持": "<span class='bdg g'>支持</span>",
                    "反对": "<span class='bdg r'>反对</span>"}.get(
                v, "<span class='bdg n'>%s</span>" % e(str(v or "中性")))

        sim_cards = ("<div style='display:grid;grid-template-columns:repeat("
                     "auto-fit,minmax(150px,1fr));gap:10px'>"
                     "<div class='sstat'><span class='st'>预演票数</span>"
                     "<span class='sv'>%d</span><span class='su'>独立留痕"
                     " sim=True</span></div>"
                     "<div class='sstat'><span class='st'>真实对齐</span>"
                     "<span class='sv'>%s</span><span class='su'>按账·票·日"
                     " 三键对齐</span></div>"
                     "<div class='sstat'><span class='st'>verdict 一致率"
                     "</span><span class='sv'>%s</span><span class='su'>"
                     "预演vs真实结论</span></div>"
                     "<div class='sstat'><span class='st'>平均|Δ系数|</span>"
                     "<span class='sv'>%s</span><span class='su'>K66 仓位系数"
                     "偏差</span></div></div>"
                     % (len(sim_props), score.get("n_pairs") or 0,
                        ("%.0f%%" % score["verdict_agree_rate"])
                        if score.get("verdict_agree_rate") is not None else "—",
                        ("%.2f" % score["avg_abs_dk"])
                        if score.get("avg_abs_dk") is not None else "—"))

        def _srow(i, x):
            key = "%s|%s|%s" % (x.get("sid"), x.get("code"), x.get("d"))
            c = cmp_.get(key) or {}
            vt = x.get("vote") or {}
            dr = x.get("directive") or {}
            k = (dr.get("sizing") or {}).get("k")
            k = 1.0 if k is None else k
            ra = c.get("role_agree") or {}
            n_agree = sum(1 for v in ra.values() if v)
            if c:
                real = "%s <span class='%s'>Δ%+.2f</span>" % (
                    _vc(c.get("real_verdict")),
                    "up" if (c.get("d_k") or 0) >= 0 else "dn",
                    c.get("d_k") or 0)
                roles = "%d/6" % n_agree
            else:
                real = "<span class='dim'>待真实会</span>"
                roles = "<span class='dim'>—</span>"
            tvs = ""
            # K80 插话后立场重测跟随到预演表：取各操盘手最新 stance3
            hs = x.get("human") or []
            st3map = {}
            for h in hs:
                for rp in h.get("replies") or []:
                    w = rp.get("who") or ""
                    if "操盘手" in w and rp.get("stance3") is not None:
                        st3map[w] = rp.get("stance3")
            for v in (x.get("traders") or []):
                st = v.get("stance") or 0
                st2 = v.get("stance2")
                lab = "%s%+d" % ((v.get("name") or "")[:2], st)
                if st2 is not None and st2 != st:      # K73 立场演化高亮
                    lab += "→%+d" % st2
                seg = ""
                st3 = st3map.get(v.get("name") or "")
                cur = st2 if st2 is not None else st
                if st3 is not None and st3 != cur:     # K80 插话后段（紫）
                    seg = "<b style='color:#c58af9'>→%+d</b>" % st3
                    cur = st3
                tvs += ("<span class='bdg %s' title='%s'>%s%s</span> "
                        % ("g" if cur > 0
                           else ("r" if cur < 0 else "n"),
                           e((v.get("answer") or "")[:120].replace("\n",
                                                                   " ")),
                           e(lab), seg))
            if not tvs:
                tvs = "<span class='dim'>—</span>"
            fin = x.get("final") or {}
            fv = fin.get("verdict")
            fbadge = (("<span class='bdg %s' title='%s'>终裁·%s</span>"
                       % ("g" if fv == "支持" else
                          ("r" if fv == "反对" else "n"),
                          e((fin.get("note") or "")[:200]
                            .replace("\n", " ")), e(fv)))
                      if fv else "<span class='dim'>—</span>")
            # K80 插话后终裁重审跟随（verdict 变了才亮紫）
            if hs:
                f2v = (hs[-1].get("final2") or {}).get("verdict")
                if f2v and f2v != fv:
                    fbadge += (" <span class='bdg n' style='border-color:"
                               "#c58af9;color:#c58af9' title='老板插话后"
                               "终裁重审'>插话后·%s</span>" % e(f2v))
            # K73 完整辩论流水（会议记录式，行下方展开）
            flow = []
            m0 = (x.get("memo") or {})
            mtxt = []
            for _k, _lab in (("thesis", "论点"), ("risks", "风险"),
                             ("if_wrong", "错了怎么办")):
                if m0.get(_k):
                    mtxt.append("%s：%s" % (_lab, m0[_k]))
            if mtxt:
                flow.append(("主持人·分析师纪要", "\n".join(mtxt),
                             "信度 %s" % m0.get("conviction", "?")))
            for v in (x.get("traders") or []):
                if v.get("answer"):
                    flow.append(("%s·质询" % v.get("name"),
                                 v["answer"], "%+d" % (v.get("stance") or 0)))
            rb = (x.get("rebuttal") or {}).get("answer") or ""
            if rb:
                flow.append(("主持人·分析师回应", rb, ""))
            for v in (x.get("traders") or []):
                if v.get("answer2"):
                    flow.append(("%s·再表态" % v.get("name"), v["answer2"],
                                 "%+d" % (v.get("stance2")
                                          if v.get("stance2") is not None
                                          else (v.get("stance") or 0))))
            if fin.get("note"):
                flow.append(("终裁", fin["note"],
                             fin.get("verdict") or ""))
            dhtml = ""
            if flow:
                dhtml = ("<tr class='drow'><td colspan=10 "
                         "style='padding:0;border-top:none'>"
                         "<details style='margin:0 0 2px 8px'><summary "
                         "style='cursor:pointer;color:#8ab4f8;font-size:"
                         "12px'>📜 完整辩论流水（%d 轮发言）</summary>"
                         "<div style='max-height:420px;overflow:auto;"
                         "font-size:12.5px;line-height:1.7'>%s</div>"
                         "</details></td></tr>"
                         % (len(flow), "".join(
                             "<div style='margin:4px 0;padding:6px 10px;"
                             "background:rgba(255,255,255,.04);"
                             "border-left:2px solid %s'><b>%s</b>%s"
                             "<div style='white-space:pre-wrap'>%s</div>"
                             "</div>"
                             % ("#f28b82" if "终裁" in who
                                else ("#fdd663" if "操盘手" in who
                                      else "#8ab4f8"),
                                e(who),
                                (" <span class='bdg n'>%s</span>" % e(tag))
                                if tag else "",
                                e(txt))
                             for who, txt, tag in flow)))
            return ("<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a> "
                    "<a href='/debate?p=%d' title='进场辩论/插话'"
                    " style='font-size:11.5px'>🎤%s</a></td>"
                    "<td>%s</td><td>%s（w%+.1f）</td>"
                    "<td><span class='bdg n'>%s</span></td>"
                    "<td class='%s'>×%.2f</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td></tr>%s"
                    % (x.get("d"), e(x.get("code") or ""),
                       e(x.get("code") or ""), e(x.get("name") or ""), i,
                       ("×%d" % len(hs)) if hs else "",
                       e(x.get("sid") or ""), _vc(vt.get("verdict")),
                       vt.get("wsum") or 0, e(dr.get("act") or "—"),
                       "up" if k > 1.0 else ("dn" if k < 1.0 else ""),
                       k, tvs, fbadge, real, roles, dhtml))

        sp_rows = "".join(_srow(i, x)
                          for i, x in enumerate(sim_props[::-1][:20])) or \
            ("<tr><td colspan=10 class='dim'>尚无预演——点右上按钮对当日候选"
             "票先辩一遍</td></tr>")
        sec_sim = ("<div class='sec'><h2>🎭 模拟投委会 · 预演层"
                   "<button onclick='_simrun(this)' style='%s;cursor:pointer;"
                   "margin-left:12px;font-size:12.5px'>▶ 预演当日候选票"
                   "</button></h2>"
                   "<div class='dim' style='margin:0 0 8px;font-size:12.5px'>"
                   "真实投委会（夜链 23:50）<b>开会前</b>三幕预演：①六视角"
                   "分析师辩论（LLM+投票+K66 系数，零口径漂移）②<b>操盘手"
                   "质询</b>——激进/保守/中立三位 AI 操盘手带着自己账本处境"
                   "入场交锋 ③<b>终裁</b>——主持人综合两方给终版立场；独立"
                   "留痕不进真实链路，真实会后自动对照打分。</div>%s"
                   "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                   "<th>账户</th><th>预演票</th><th>指令</th><th>预演系数"
                   "</th><th>操盘手质询</th><th>终裁</th><th>真实对照</th>"
                   "<th>视角一致</th></tr>%s</table>"
                   "</div>" % (_PILL, sim_cards, sp_rows))
        sjs = ("<script>function _simrun(btn){btn.disabled=true;"
               "fetch('/api/council/simrun',{method:'POST',"
               "headers:{'Content-Type':'application/json'},body:'{}'})"
               ".then(function(r){return r.json();})"
               ".then(function(j){if(j.ok){alert('预演已启动：后台六视角"
               "辩论（每票约几十秒），稍后刷新本页看结果');}"
               "else{alert('失败：'+(j.error||''));btn.disabled=false;}})"
               ".catch(function(e){alert('网络错误：'+e);"
               "btn.disabled=false;});}</script>")
        # K71 取舍决策（仓位固定·有取有舍·事后观察）
        dcs = (ev.get("decisions") or {})
        dagg = dcs.get("agg") or {}
        tips = dcs.get("tips") or []
        sw = sum(a.get("swap_n") or 0 for a in dagg.values())
        swok = sum(a.get("swap_ok") or 0 for a in dagg.values())
        sk = sum(a.get("skip_n") or 0 for a in dagg.values())
        skok = sum(a.get("skip_ok") or 0 for a in dagg.values())

        def _rate(ok, n):
            return ("%.0f%%" % (100.0 * ok / n)) if n else "—"

        dcards = ("<div style='display:grid;grid-template-columns:repeat("
                  "auto-fit,minmax(170px,1fr));gap:10px'>"
                  "<div class='sstat'><span class='st'>换仓决策</span>"
                  "<span class='sv'>%d 笔</span><span class='su'>正确率 %s"
                  "（新票＞被挤票假想）</span></div>"
                  "<div class='sstat'><span class='st'>放弃决策</span>"
                  "<span class='sv'>%d 笔</span><span class='su'>正确率 %s"
                  "（假想收益≤0）</span></div>"
                  "<div class='sstat'><span class='st'>仓位约束</span>"
                  "<span class='sv'>≤100%%</span><span class='su'>每账固定"
                  "资金池·单票≤100%%</span></div></div>"
                  % (sw, _rate(swok, sw), sk, _rate(skok, sk)))

        def _drow(x):
            act = x.get("action")
            if act == "换仓":
                detail = ("%s → <a href='/kg?c=%s'>%s</a>（让位 @%s，"
                          "%+.2f%%）"
                          % (e(x.get("victim_code") or ""),
                             e(x.get("victim_code") or ""),
                             e(x.get("victim_name") or ""),
                             e(x.get("victim_exit") or "—"),
                             x.get("victim_ret") or 0))
                after = ("假想 %+.2f%% vs 新票 %+.2f%%"
                         % (x.get("victim_hyp") or 0,
                            x.get("new_ret") or 0)
                         if x.get("victim_hyp") is not None
                         else "观察中")
            else:
                detail = "%s（余仓 %.2f · %s）" % (
                    e(x.get("note") or ""), x.get("free") or 0,
                    e(x.get("size_eff") or 0))
                after = ("假想 %+.2f%%" % x.get("new_hyp")
                         if x.get("new_hyp") is not None else "观察中")
            c = x.get("correct")
            badge = ("<span class='bdg g'>✓ 对</span>" if c
                     else ("<span class='bdg r'>✗ 错</span>"
                           if c is False
                           else "<span class='bdg n'>观察中</span>"))
            return ("<tr><td>%s</td><td>%s</td><td><span class='bdg %s'>"
                    "%s</span></td>"
                    "<td><a href='/kg?c=%s'>%s %s</a></td>"
                    "<td>%s</td><td class='dim' style='font-size:12px'>%s"
                    "</td><td>%s</td></tr>"
                    % (x.get("d"), e(x.get("bk") or ""),
                       "y" if act == "换仓" else "n", e(act or ""),
                       e(x.get("new_code") or ""), e(x.get("new_code") or ""),
                       e(x.get("new_name") or ""), detail, after, badge))

        dtrs = "".join(_drow(x) for x in (dcs.get("rows") or [])[:30]) or \
            ("<tr><td colspan=7 class='dim'>暂无取舍决策——每账仓位 ≤100%%，"
             "满仓来新票才触发换仓/放弃</td></tr>")
        tip_html = ("".join("<div style='margin-top:6px'>⚠ %s</div>"
                            % e(t) for t in tips)
                    if tips else "")
        sec_dec = ("<div class='sec'><h2>⚔️ 取舍决策 · 事后观察（仓位固定"
                   "→有取有舍→评对错→供进化）</h2>%s%s"
                   "<table class='sticky' style='margin-top:10px'>"
                   "<tr><th>日期</th><th>账本</th><th>动作</th><th>新票</th>"
                   "<th>取舍</th><th>事后对照</th><th>对错</th></tr>%s"
                   "</table>"
                   "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                   "每本账一个固定资金池（总仓位 ≤100%%，单票 ≤100%%）：满仓"
                   "来新票时——<b>激进</b>挤掉最弱持仓（浮亏最深）换仓；"
                   "<b>保守</b>不挤仓放弃新票；<b>中立</b>只挤「过半时间窗"
                   "且浮亏」者。事后自动对照：换仓对=新票实际收益＞被挤票"
                   "假想收益；放弃对=新票假想收益≤0。正确率连续偏低会给"
                   "出进化方向提示（≥5 样本才评）。</div>%s</div>"
                   % (dcards, tip_html, dtrs, ""))
        # K88 预演六本账：预演票喂独立账，vs_real=预演质量用钱度量
        evb = {}
        try:
            with open(os.path.join(REPORTS_DIR,
                                   "trader_sim_simbooks_eval.json"),
                      encoding="utf-8") as f:
                evb = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_portfolio.py", exc)
        sec_preset = ""
        if evb:
            vs = evb.get("vs_real") or {}
            vb = vs.get("books") or {}

            def _bc(bk, key):
                x = (vb.get(bk) or {}).get(key) or 0.0
                return ("<td class='%s'>%+.2f</td>"
                        % ("up" if x >= 0 else "dn", x))
            btrs = "".join(
                "<tr><td>%s</td><td>%d</td>%s%s%s</tr>"
                % (e(bk), (vb.get(bk) or {}).get("n") or 0,
                   _bc(bk, "sim"), _bc(bk, "real"), _bc(bk, "diff"))
                for bk in BOOKS)
            vd = vs.get("verdict") or {}
            _so = "；".join(vs.get("sim_only") or [])
            _ro = "；".join(vs.get("real_only") or [])
            sec_preset = (
                "<div class='sec'><h2>🎬 预演六本账 · vs 真实账（预演质量"
                "的真金白银度量）</h2>"
                "<div class='dim' style='font-size:12.5px;margin:4px 0 8px'>"
                "预演票（模拟投委会留痕）喂独立六账：同样操盘规则、独立"
                "文件对（trader_sim_simbooks*.json），与真实账零干扰；预演"
                "早真会一步建仓——同票贡献差=领先一步的价值；裁决一致率低"
                "=预演与真会漂移大，喂账成绩仅供参考。数据 %s</div>"
                "<table class='sticky'><tr><th>账本</th><th>同票数</th>"
                "<th>预演均贡献</th><th>真实均贡献</th><th>差值</th></tr>%s"
                "</table>"
                "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                "预演票 %d · 真实票 %d · 同票 %d · 裁决一致率 %.0f%%（%d/%d）"
                "%s%s · 预演待入场 %d 票</div></div>"
                % (e(evb.get("ts") or "—"), btrs,
                   vs.get("n_sim") or 0, vs.get("n_real") or 0,
                   vs.get("n_both") or 0, vd.get("pct") or 0.0,
                   vd.get("agree") or 0, vd.get("n") or 0,
                   (" ｜预演独接：%s" % e(_so)) if _so else "",
                   (" ｜真实独接：%s" % e(_ro)) if _ro else "",
                   len(evb.get("pending") or {})))
        content = ("<h1>♠ 三操盘手模拟锦标赛 · 六本账</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K68 影子"
                   "模拟层：选股策略出票（来源标签全链贯穿）→ 分析师团队"
                   "六视角辩论 → 激进/保守/中立 × 积极/保守 六本账 → 真实"
                   "日K买卖点 → 战绩评价（贡献度+基准α）</div>"
                   "%s%s%s%s%s%s%s%s%s"
                   % (cards, tbl_sid, sec_dec, sec_preset, sec_sim, sec_p,
                      tbl, tbl_pos, method))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>操盘手锦标赛 · 六本账</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("sim") + content + sjs + "</body></html>")
        self._html(body)
        self._html(body)
