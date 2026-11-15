#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首个真实交易日观察窗 · 四读一键只读核验（三态 + 自我证伪）

背景
────
周一 09:36 的观察窗是「四读」：07:00 恢复链路 → 08:10 出票 / 09:35 盘前快照
→ 09:35 open log → 判据门 + 判据③差集 → paper_trade.log。清单散在文档与日志里，
手工执行容易漏项；更危险的是**周末假绿**：C:/Temp 里存在完整的周日 SKIP 落痕
（preopen 快照 + open rc=0），不设交易日闸门会把「周末没跑」误判成「观察窗全过」。

本脚本只做编排与三态汇总，不重写任何业务判据：判据门直接委托
quant_backtest/_audit_stage_a_verify.py --phase open（只读生产），其余判据基于
文件证据。全程不写生产文件、不动计划任务、不启服务、不 reload、不碰账本。

判据
────
D0  07:00 恢复链路       恢复日志四步完成、未补跑、恢复后 preopen_daily=Ready
D1  09:35 盘前快照       快照存在 / day 与目标日一致 / 字段齐 / sha256 可回查
D2  09:35 open log       当日 preopen snapshot rc=0 与 open rc=0 两行齐、无 ERR
D3  判据门               _audit_stage_a_verify.py 三态透传；rc=2/PENDING 不算通过
D4  快照自洽             快照 pending_sells_codes == 账户副本 pending_sells codes
D5  结构性盲区           判据③卖单完备性在 open 结清后不可复原，**恒 PENDING**
                        （只许靠 open 前快照人工核对，不许假装守住）

三态与退出码（与 _audit_stage_a_verify.py 同语义）
────
FAIL>0 → rc=1（停下读日志）／PENDING>0 → rc=2（未完成评估，不等于通过）
全 PASS → rc=0。D5 恒 PENDING，因此本脚本**永远不会宣告「全通过」**——这是
如实标注盲区，不是缺陷。交易日闸门命中时六条全 PENDING。

用法
────
  python tools/verify_monday_observe.py                     # 默认今天
  python tools/verify_monday_observe.py --day 20261116      # 指定交易日
  python tools/verify_monday_observe.py --self-test         # 自我证伪（只写临时目录）
  python tools/verify_monday_observe.py --temp-dir C:/Temp/_fixture

交易日闸门只用 weekday（周末），不接交易日历：节假日会判成「交易日」，
但那天不会有任何产物 → 判据自然落 PENDING，误判方向偏保守，不会假绿。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = REPO / "quant_backtest" / "_audit_stage_a_verify.py"
# 生产脚本（run_paper_trade.ps1 / paper_preopen_snapshot.py）把日志与盘前快照
# 硬编码写进 C:\Temp，与 %TEMP% 无关。曾误用 os.environ["TEMP"] 作证据根，
# 沙箱里指向别处 → D0/D1/D2 全报「证据不存在」的假 PENDING。此处必须写死，
# --self-test 里有断言锁住这个常量；--temp-dir 仅供隔离演练。
DEFAULT_TEMP = Path("C:/Temp")

PAPER_LOG_NAME = "paper_trade.log"
RESTORE_PREFIX = "preopen_restore_"
SNAP_PREFIX = "paper_preopen_"
STATES = ("PASS", "FAIL", "PENDING")

CHECK_IDS = (
    ("D0", "07:00 恢复链路"),
    ("D1", "09:35 盘前快照"),
    ("D2", "09:35 open log"),
    ("D3", "判据门 --phase open"),
    ("D4", "快照卖单集自洽"),
    ("D5", "判据③卖单完备性盲区"),
)


# ── 三态账本 ────────────────────────────────────────────────────────
class Ledger:
    def __init__(self):
        self.items = []
        self.notes = []

    def add(self, cid, state, detail=""):
        assert state in STATES, state
        self.items.append((cid, state, detail))

    def note(self, text):
        self.notes.append(text)

    def counts(self):
        c = {s: 0 for s in STATES}
        for _, st, _ in self.items:
            c[st] += 1
        return c

    def state_of(self, cid):
        for c, st, _ in self.items:
            if c == cid:
                return st
        return None

    def report(self, title):
        print("\n" + "=" * 68)
        print(title)
        print("=" * 68)
        labels = dict(CHECK_IDS)
        for cid, st, detail in self.items:
            print("  [%-7s] %s %-22s %s" % (st, cid, labels.get(cid, ""), detail))
        for n in self.notes:
            print("  " + n)
        c = self.counts()
        print("-" * 68)
        print("  PASS=%d  FAIL=%d  PENDING=%d" % (c["PASS"], c["FAIL"], c["PENDING"]))
        if c["FAIL"]:
            print("  >>> 有 FAIL → 停下读日志，不许继续调参")
        elif c["PENDING"]:
            print("  >>> 未完成评估 —— PENDING 不等于通过，也不等于失败")
        else:
            print("  >>> 全部判据已评估且通过")
        return c


def _read_text(path):
    """读 utf-8-sig；调用方负责捕获异常并判 FAIL（不可静默吞）。"""
    return io.open(str(path), encoding="utf-8-sig", newline="").read()


def _iso(day):
    return "%s-%s-%s" % (day[:4], day[4:6], day[6:8])


# ── 证据源（self-test 时可整体替换） ────────────────────────────────
class Evidence:
    def __init__(self, root=None, verify_result=None):
        self.root = Path(root) if root else DEFAULT_TEMP
        self._verify_result = verify_result  # (rc, stdout) 注入；None = 真调子进程

    def restore_text(self):
        files = sorted(self.root.glob(RESTORE_PREFIX + "*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return _read_text(files[0]) if files else None

    def snap_path(self, day):
        return self.root / (SNAP_PREFIX + day + ".json")

    def snap_account_path(self, day):
        return self.root / (SNAP_PREFIX + day + "_account.json")

    def paper_log_text(self):
        p = self.root / PAPER_LOG_NAME
        return _read_text(p) if p.is_file() else None

    def verify(self, day):
        if self._verify_result is not None:
            return self._verify_result
        if not VERIFY_SCRIPT.is_file():
            return (127, "")
        r = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--day", day, "--phase", "open"],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace")
        return (r.returncode, (r.stdout or "") + (r.stderr or ""))


# ── 判据 ────────────────────────────────────────────────────────────
def _d0(led, ev):
    try:
        txt = ev.restore_text()
    except Exception as exc:
        led.add("D0", "FAIL", "恢复日志读取失败 %s" % exc)
        return
    if txt is None:
        led.add("D0", "PENDING", "恢复日志尚未产生（时点未到）")
    elif "ALERT 检测到补跑" in txt:
        led.add("D0", "FAIL", "恢复脚本检出补跑 —— 08:10 可能用滞后名单出票")
    elif "STEP3 OK 未发生补跑" not in txt:
        led.add("D0", "FAIL", "缺 STEP3，恢复脚本可能中途退出（静默失效）")
    elif not re.search(r"AFTER state=Ready", txt):
        led.add("D0", "FAIL", "恢复后 preopen_daily 不是 Ready")
    else:
        led.add("D0", "PASS", "恢复四步完成 / 未补跑 / 任务已 Ready")


def _d1(led, ev, day):
    p = ev.snap_path(day)
    if not p.is_file():
        led.add("D1", "PENDING", "盘前快照 %s 尚未产生" % p.name)
        return
    try:
        d = json.loads(_read_text(p))
    except Exception as exc:
        led.add("D1", "FAIL", "快照解析失败 %s" % exc)
        return
    if str(d.get("day")) != day:
        led.add("D1", "FAIL", "快照 day=%r 与目标 %s 不符（拿到旧快照）"
                % (d.get("day"), day))
        return
    miss = [k for k in ("pending_buys_codes", "pending_sells_codes",
                        "account_sha256", "counts") if k not in d]
    if miss:
        led.add("D1", "FAIL", "快照缺关键字段 %s" % miss)
        return
    ap = ev.snap_account_path(day)
    if not ap.is_file():
        led.add("D1", "PENDING", "账户副本缺失，sha256 无法回查")
        return
    h = hashlib.sha256(ap.read_bytes()).hexdigest()
    if h != d.get("account_sha256"):
        led.add("D1", "FAIL", "account_sha256 与账户副本不符（快照不可回查）")
        return
    led.add("D1", "PASS", "day 一致 / 字段齐 / sha256 可回查（buy %d, sell %d）"
            % (len(d.get("pending_buys_codes") or []),
               len(d.get("pending_sells_codes") or [])))


def _d2(led, ev, day):
    try:
        txt = ev.paper_log_text()
    except Exception as exc:
        led.add("D2", "FAIL", "paper_trade.log 读取失败 %s" % exc)
        return
    if txt is None:
        led.add("D2", "PENDING", "paper_trade.log 不存在")
        return
    iso = _iso(day)
    lines = [l.lstrip("\ufeff").strip() for l in txt.splitlines()
             if l.lstrip("\ufeff").startswith("paper ") and iso in l]
    if not lines:
        led.add("D2", "PENDING", "当日无 open log 落痕（时点未到）")
        return
    bad = [l for l in lines
           if re.search(r"rc=(?!0\b)\d+", l) or "ERR" in l or "FAIL" in l]
    if bad:
        led.add("D2", "FAIL", "当日落痕含非零 rc / ERR：%s" % bad[:2])
        return
    has_snap = any("preopen snapshot rc=0" in l for l in lines)
    has_open = any("open rc=0" in l for l in lines)
    if not (has_snap and has_open):
        led.add("D2", "PENDING", "缺 preopen snapshot 或 open 之一（落痕不完整）")
        return
    led.add("D2", "PASS", "当日 preopen snapshot rc=0 + open rc=0 齐")


def _d3(led, ev, day):
    try:
        rc, out = ev.verify(day)
    except Exception as exc:
        led.add("D3", "FAIL", "判据门子进程异常 %s" % exc)
        return
    m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)\s+PENDING=(\d+)", out)
    if not m:
        led.add("D3", "FAIL", "判据门无三态汇总输出（rc=%s）" % rc)
        return
    p, f, q = (int(x) for x in m.groups())
    if f > 0 or rc == 1:
        led.add("D3", "FAIL", "判据门 FAIL=%d rc=%s → 停跑读日志" % (f, rc))
    elif rc == 2 or q > 0:
        led.add("D3", "PENDING",
                "判据门未完成评估（PASS=%d PENDING=%d rc=%s）→ 不等于通过"
                % (p, q, rc))
    elif rc == 0 and p > 0:
        led.add("D3", "PASS", "判据门全部评估且通过（PASS=%d rc=0）" % p)
    else:
        led.add("D3", "FAIL", "异常组合 rc=%s PASS=%d PENDING=%d" % (rc, p, q))


def _d4(led, ev, day):
    sp, ap = ev.snap_path(day), ev.snap_account_path(day)
    if not (sp.is_file() and ap.is_file()):
        led.add("D4", "PENDING", "快照或账户副本缺失")
        return
    try:
        s = json.loads(_read_text(sp))
        a = json.loads(_read_text(ap))
    except Exception as exc:
        led.add("D4", "FAIL", "解析失败 %s" % exc)
        return
    snap_codes = set(s.get("pending_sells_codes") or [])
    acct_codes = {x.get("code") for x in (a.get("pending_sells") or [])
                  if isinstance(x, dict) and x.get("code")}
    if snap_codes != acct_codes:
        led.add("D4", "FAIL", "快照卖单集与账户副本不一致 diff=%s"
                % sorted(snap_codes ^ acct_codes)[:6])
        return
    led.add("D4", "PASS", "快照卖单集与账户副本自洽（%d 笔）" % len(snap_codes))


def run_checks(led, ev, day, *, trading_day):
    if not trading_day:
        for cid, label in CHECK_IDS:
            led.add(cid, "PENDING", "非交易日闸门：观察窗不适用，不读旧产物")
        led.note("⚠️ 交易日闸门命中（weekday>=5）：周末 SKIP 落痕不构成通过")
        return
    _d0(led, ev)
    _d1(led, ev, day)
    _d2(led, ev, day)
    _d3(led, ev, day)
    _d4(led, ev, day)
    # 结构性盲区：不因数据齐而转 PASS；这条恒在，故本脚本永不宣告「全通过」。
    led.add("D5", "PENDING",
            "结构性盲区：open 结清后挂单不可复原，只能靠 open 前快照人工核对；"
            "本脚本不宣告守住")


# ── 自我证伪 ────────────────────────────────────────────────────────
def _write_fixture(root, day, *, bad=False, verify_result=None):
    """构造「周一已产出」的完整证据。日期一律 date() 构造，不写字面量。"""
    iso = _iso(day)
    account = {
        "pending_buys": [{"code": "A%d.SZ" % i} for i in range(3)],
        "pending_sells": [{"code": "S%d.SH" % i} for i in range(2)],
        "positions": {},
    }
    ap = root / (SNAP_PREFIX + day + "_account.json")
    ap.write_text(json.dumps(account, ensure_ascii=False), encoding="utf-8")
    sells = ["S%d.SH" % i for i in range(2)]
    if bad:
        sells = ["S9.SH"]  # 与账户副本不一致 → D4 FAIL
    snap = {
        "day": day if not bad else "19990101",  # 旧快照 → D1 FAIL
        "pending_buys_codes": ["A%d.SZ" % i for i in range(3)],
        "pending_sells_codes": sells,
        "account_sha256": hashlib.sha256(ap.read_bytes()).hexdigest(),
        "counts": {"positions": 0, "pending_buys": 3, "pending_sells": 2},
        "taken_at": iso + "T09:35:01",
    }
    (root / (SNAP_PREFIX + day + ".json")).write_text(
        json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    restore = [
        "[%s 07:00:00] BEFORE state=Disabled lastRun=1999" % iso,
        "[%s 07:00:01] STEP1 startWhenAvailable -> False" % iso,
        "[%s 07:00:04] STEP2 enabled" % iso,
        ("[%s 07:00:04] ALERT 检测到补跑（lastRun 已推进）" % iso) if bad
        else ("[%s 07:00:04] STEP3 OK 未发生补跑" % iso),
        "[%s 07:00:06] STEP4 startWhenAvailable -> True" % iso,
        "[%s 07:00:08] AFTER state=Ready lastRun=1999" % iso,
    ]
    (root / (RESTORE_PREFIX + "selftest.log")).write_text(
        "\n".join(restore), encoding="utf-8")

    logs = ["paper preopen snapshot rc=0 %sT09:35:01" % iso,
            ("paper open rc=1 %sT09:35:02" % iso) if bad
            else ("paper open rc=0 %sT09:35:02" % iso)]
    (root / PAPER_LOG_NAME).write_text("\n".join(logs), encoding="utf-8")

    vr = verify_result or ((1, "  PASS=3  FAIL=2  PENDING=0  (phase=open)") if bad
                          else (0, "  PASS=5  FAIL=0  PENDING=0  (phase=open)"))
    return Evidence(root=root, verify_result=vr)


def _self_test():
    """三条路径都必须真实执行：好周一 / 坏周一 / 周末（防死代码与假绿）。"""
    monday = _dt.date(2026, 11, 16)   # 不写字面量日期串
    sunday = _dt.date(2026, 11, 15)
    assert monday.weekday() < 5 and sunday.weekday() >= 5, "fixture 日期选错"
    # 锁住证据根：%TEMP% 与生产 C:\Temp 不是同一目录，改回环境变量会造假 PENDING
    assert DEFAULT_TEMP.as_posix().lower().rstrip("/") == "c:/temp", \
        ("证据根必须是生产硬编码的 C:/Temp", DEFAULT_TEMP)
    results = {}

    with tempfile.TemporaryDirectory(prefix="observe_good_") as d:
        led = Ledger()
        run_checks(led, _write_fixture(Path(d), monday.strftime("%Y%m%d")),
                   monday.strftime("%Y%m%d"), trading_day=True)
        results["good"] = led
        c = led.counts()
        print("[self-test/good]  %s" % {k: v for k, v in c.items()})
        for cid in ("D0", "D1", "D2", "D3", "D4"):
            assert led.state_of(cid) == "PASS", ("good 应 PASS", cid,
                                                 led.state_of(cid))
        assert led.state_of("D5") == "PENDING", "盲区必须恒 PENDING"
        assert c["FAIL"] == 0, "good 不该有 FAIL"

    with tempfile.TemporaryDirectory(prefix="observe_bad_") as d:
        led = Ledger()
        run_checks(led, _write_fixture(Path(d), monday.strftime("%Y%m%d"), bad=True),
                   monday.strftime("%Y%m%d"), trading_day=True)
        results["bad"] = led
        c = led.counts()
        print("[self-test/bad]   %s" % {k: v for k, v in c.items()})
        assert c["FAIL"] >= 4, ("判据恒真/抓不住坏证据", c)

    # 周末：证据全用「好」的，闸门必须把它们全部挡成 PENDING（防假绿）
    with tempfile.TemporaryDirectory(prefix="observe_weekend_") as d:
        led = Ledger()
        run_checks(led, _write_fixture(Path(d), sunday.strftime("%Y%m%d")),
                   sunday.strftime("%Y%m%d"), trading_day=False)
        c = led.counts()
        print("[self-test/weekend] %s" % {k: v for k, v in c.items()})
        assert c["PASS"] == 0 and c["FAIL"] == 0 and c["PENDING"] == len(CHECK_IDS), \
            ("周末闸门未挡住旧产物 → 会假绿", c)

    print("\nSELF-TEST OK：好周一全评估 / 坏周一 FAIL=%d / 周末全 PENDING"
          % results["bad"].counts()["FAIL"])
    return 0


# ── 入口 ────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="首个真实交易日观察窗 · 四读一键只读核验（三态）")
    ap.add_argument("--day", default=None,
                    help="目标交易日 YYYYMMDD，默认今天；周末自动走非交易日闸门")
    ap.add_argument("--self-test", action="store_true",
                    help="自我证伪：好/坏/周末三条路径，只写临时目录")
    ap.add_argument("--temp-dir", default=None,
                    help="证据根目录（默认取 %%TEMP%%；演练可指隔离 fixture）")
    args = ap.parse_args(argv)

    if args.self_test:
        try:
            return _self_test()
        except AssertionError as exc:
            print("SELF-TEST BROKEN: %s" % (exc,))
            return 3

    day = args.day or _dt.date.today().strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", day):
        print("ERROR: --day 必须是 8 位数字 YYYYMMDD，收到 %r" % day)
        return 1
    d = _dt.datetime.strptime(day, "%Y%m%d").date()
    trading = d.weekday() < 5

    ev = Evidence(root=Path(args.temp_dir) if args.temp_dir else DEFAULT_TEMP)
    led = Ledger()
    run_checks(led, ev, day, trading_day=trading)
    c = led.report("观察窗只读核验 · day=%s（%s）"
                   % (day, "交易日" if trading else "非交易日"))
    if c["FAIL"]:
        return 1
    if c["PENDING"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
