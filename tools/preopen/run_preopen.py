# -*- coding: utf-8 -*-
"""盘前一键：校对 → 选票 → 出报告 → 记账。后峰盘前 Agent 的唯一入口。

老田 2026-11-12 的要求是「每天盘前都要做这件事」。那么它就必须是**一条命令**，
不是一串手工步骤 —— 步骤一多，缺一步没人发现，出来的报告就是半成品。

顺序按依赖排，前一步不通过就不往下走（fail-fast）：

  1. 数据校对  tools/preopen/audit_preopen.py
     verdict=fail → 立即停，不发报告。宁可今天没有报告，也不发一份基于坏数据的报告。
  2. 精选 15 只  _preopen_pick15_v2_adj.py --pkg <最新包> --asof <行情最新日>
     参数自动推：包取 strategy_center_inbox 最大日期目录；asof 取个股日线最新日。
  3. 简报 H5  tools/preopen/build_preopen_report.py
     读 2 的产物 + 台账，产出可转发的自包含 HTML。
  4. 记账 + 长建议  tools/preopen/event_log.py / evolve.py
     把今天这件事写进事件账（含意外与候选教训），并检查是否已够格立假设。
  5. 进化台账  evolve.py --digest
     出一份人能读的台账：今天观察到什么、累计到哪、还差多少够格。
     第 4 步不影响 rc —— 记账失败不该让今天的报告作废，但要在日志里留痕。

退出码：0 全绿 / 1 出了东西但有告警 / 2 阻断（未出报告）
            3 选票脚本失败（数据可能没问题，是脚本或依赖的问题）

用法：
  python tools/preopen/run_preopen.py
  python tools/preopen/run_preopen.py --skip-pick      # 只重出报告，不重跑选票
  python tools/preopen/run_preopen.py --json-out x.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\项目\老田的AI助手"
PY = sys.executable
PICK_DIR = r"C:\项目\zerobound-kg\factor_proto"
PICK = os.path.join(PICK_DIR, "_preopen_pick15_v2_adj.py")
# 排序影子对照（只记录，不参与出票）。与 PICK 同目录、同解释器、同一份共识包。
SHADOW = os.path.join(PICK_DIR, "shadow_rank.py")
# 实测 12s（82 只候选）。给 240s 是留 20 倍余量：它要读全市场日线，
# 万一磁盘被同步任务占满，慢到 3 分钟是可能的；但没有上限就等于让一个
# 观测步骤能拖死出票链路。
SHADOW_TIMEOUT = 240
PKG_ROOT = os.path.join(PICK_DIR, "strategy_center_inbox")
DELIV = r"C:\项目\deliverables"

# P0 启动落痕（2026-11-12）：由 preopen_task.ps1 第一行可执行代码写入。
# 这里只读、不写 —— 落痕必须独立于业务代码，业务死掉也照样有。
LAUNCH = r"C:\Temp\preopen_launch.jsonl"

# 精排口径版本。必须与 zerobound-kg/factor_proto 下两处保持一致：
#   _preopen_pick15_v2_adj.py 产物里的 chain_policy_v
#   fg_daily_chain.py 里的 REFINE_POLICY_V
# 改动任何会改变名单的口径（过滤规则、复权、打分）三处一起 +1，否则旧产物
# 会被当成当前口径复用。tools/tests/test_preopen_gate_fixes.py 会断言这一点。
# v4（2026-11-13）：pick15 新增偏离度硬否决（ratio > 1.20 剔除），会改变名单。
# 这是**真实的口径升级**，不是笔误 —— 11-12 夜那次才是笔误（当时只改运行器、
# 没动 rate()，版本号却写成 4，会让所有合法产物被复用校验永久阻断）。
#
# 三处必须同步改。只改产出方（这次漏的就是这一半）会让 --skip-pick 被自己的
# 版本号挡住：产物写 v4、这边期望 v3 → 「产物口径 v4 与当前 v3 不符」→ 阻断。
# 只改这一边则更糟：旧口径产物会被当成当前口径放行。方向不同，但都叫静默失效。
POLICY_V = 4


def reuse_rejection(pv: dict, day: str, policy_v: int = POLICY_V) -> str | None:
    """复用选票前的阻断判定：返回 None 才可复用。

    抽成纯函数是为了能单测 —— 11-12 08:27 的事故就是「只看文件存在」，
    而修复如果只写在 main() 的分支里，改回去也没人会发现。
    """
    if pv.get("data_day") != day:
        return "产物数据日 %s 与目标日 %s 不符" % (pv.get("data_day"), day)
    if pv.get("chain_policy_v") != policy_v:
        return "产物口径 v%s 与当前 v%s 不符" % (pv.get("chain_policy_v"), policy_v)
    return None

sys.path.insert(0, HERE)
import audit_preopen as audit  # noqa: E402


def read_launch(day: str) -> dict:
    """读某日的启动落痕。读不到就如实说「无记录」，不推断也不补默认值。

    为什么要有这个函数：「任务是否启动」和「是否算票」是两件事。
    2026-11-12 的判例是 08:10 触发、日志却在 08:27 才创建 —— 没有落痕时，
    「08:10 没启动」与「启动了但在写第一行日志前就死了」在证据上完全等价。
    """
    rec = {"source": LAUNCH, "n_start": 0, "started_at": None, "first_started_at": None,
           "ended": None, "last_kind": None, "dur_sec": None, "pids": [],
           "skip_pick": None, "found": False}
    if not os.path.exists(LAUNCH):
        return rec
    try:
        fh = io.open(LAUNCH, encoding="utf-8-sig", errors="replace")
    except OSError:
        return rec
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("day") or "") != day:
                continue
            rec["found"] = True
            if r.get("kind") == "start":
                rec["n_start"] += 1
                rec["started_at"] = r.get("ts")          # 最后一条 start
                if rec["first_started_at"] is None:
                    rec["first_started_at"] = r.get("ts")
                if r.get("pid") not in rec["pids"]:
                    rec["pids"].append(r.get("pid"))
                rec["skip_pick"] = r.get("skip_pick")
            rec["last_kind"] = r.get("kind")
            if r.get("kind") == "end":
                rec["ended"] = r.get("ts")
                rec["dur_sec"] = r.get("dur_sec")
    return rec


def pick_provenance(path: str) -> dict:
    """选票产物的「什么时候算的」。

    优先读产物自述的 picker_run_at；老产物没有这个字段就退回文件 mtime，
    但必须标成 source="mtime" —— 两者可信度不同，混为一谈就等于没标。
    """
    if not os.path.exists(path):
        return {"exists": False, "picker_run_at": None, "picker_run_at_source": "absent"}
    mtime = datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return {"exists": True, "picker_run_at": None,
                "picker_run_at_source": "unreadable", "mtime": mtime}
    run_at = d.get("picker_run_at")
    return {"exists": True,
            "picker_run_at": run_at or mtime,
            "picker_run_at_source": "artifact" if run_at else "mtime",
            "mtime": mtime,
            "chain_policy_v": d.get("chain_policy_v"),
            "n_picks": len(d.get("top15_adj") or []),
            "data_day": d.get("refine_date")}



def latest_pkg() -> str | None:
    if not os.path.isdir(PKG_ROOT):
        return None
    ds = sorted(d for d in os.listdir(PKG_ROOT)
                if os.path.isdir(os.path.join(PKG_ROOT, d)) and d.isdigit()
                and os.path.exists(os.path.join(PKG_ROOT, d, "push_package.json")))
    return ds[-1] if ds else None


def _pkg_from_pick(path: str) -> str | None:
    """从选票产物里取回它当时用的共识包名。

    `--skip-pick` 不会重推 latest_pkg()，但影子排序必须用**同一份**包：
    此刻再调 latest_pkg() 可能已经多出一个新包，那对照就换成了另一批候选，
    差异里混进了「换了包」这个因素，而它不属于本次要检验的「换排序」。

    所以只认产物自述，取不到就跳过 —— 跳过是诚实的，「拿个最新的顶上」
    会让两套排序的候选集不同，却仍然被当成同一个对照。
    """
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return None
    p = ((d.get("picker_args") or {}).get("pkg") or "")
    name = os.path.basename(os.path.normpath(p)) if p else ""
    # 只认 8 位纯数字目录名。别的形态（空、相对路径、拼错的盘符）一律当取不到，
    # 而不是硬塞给 shadow_rank 让它自己报「包不存在」—— 那会变成一条假告警。
    return name if name.isdigit() else None


def shadow_step(pkg: str | None, day: str, pick_json: str, runner=None) -> dict:
    """排序影子：跑一次对照，返回要记进 run json 的那条记录。

    抽成函数（而不是写在 main 里）的理由和 reuse_rejection 一样：写在 main 的
    分支里就只有「真跑一次全流程」才能验证，而那会覆盖当天产物、追加台账 ——
    为了测一个观测步骤而改动生产数据，代价比 bug 本身还大。

    **绝不抛异常、绝不改 rc**。runner 可注入，测试时传假的。
    """
    r = runner or (lambda cmd, cwd=None, timeout=None: run(cmd, cwd=cwd, timeout=timeout))
    rec: dict = {"step": "shadow_rank"}
    pkg_s = pkg or _pkg_from_pick(pick_json)
    if not pkg_s:
        rec.update({"skipped": True,
                    "reason": "拿不到共识包名（复用路径且产物无 picker_args.pkg）"})
        return rec
    try:
        rc_s, log_s = r([PY, "-X", "utf8", SHADOW, "--pkg", pkg_s, "--asof", day],
                        cwd=PICK_DIR, timeout=SHADOW_TIMEOUT)
    except Exception as e:                      # noqa: BLE001 —— 观测步骤不许炸主流程
        rec.update({"rc": -1, "pkg": pkg_s, "asof": day,
                    "error": "%s: %s" % (type(e).__name__, e)})
        return rec
    rec.update({"rc": rc_s, "pkg": pkg_s, "asof": day,
                "out": "\n".join((log_s or "").strip().splitlines()[:4])})
    return rec


def run(cmd: list[str], cwd: str | None = None,
        timeout: int | None = None) -> tuple[int, str]:
    """跑子进程，返回 (rc, 输出)。

    timeout 默认 None（沿用旧行为）—— 这是刻意的：给已有调用加超时会改变它们
    的失败语义，而它们不在本次改动范围内。只有明确需要上限的新调用才传。
    超时按 rc=124 返回（shell 惯例），并把原因写进输出，免得「超时」被读成「崩溃」。
    """
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") if isinstance(e.stdout, str)
               else (e.stdout or b"").decode("utf-8", "replace"))
        err = ((e.stderr or "") if isinstance(e.stderr, str)
               else (e.stderr or b"").decode("utf-8", "replace"))
        return 124, out + err + "\n[timeout] 超过 %ss 未结束，已中止" % timeout


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前一键：校对→选票→出报告")
    ap.add_argument("--date", default=None, help="数据日，缺省取个股日线最新日")
    ap.add_argument("--skip-pick", action="store_true", help="跳过选票，只重出报告")
    ap.add_argument("--json-out", default=os.path.join(DELIV, "preopen_run_latest.json"),
                    help="运行结论落盘路径（默认与报告同目录）")
    a = ap.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: dict = {"started": stamp, "steps": [], "rc": 0}

    asof, _ = audit.max_date("tdx/ts_daily", "trade_date")
    day = a.date or asof
    # 执行日 = 数据日之后的下一个交易日。台账/简报文件名都用它（老田早上翻的是
    # 「今天这份」，写成 11-11 会让他以为是昨天那份）。提前到这里算，是因为
    # 选票篮子的落盘名也要用它 —— 篮子回答的是「今天开盘用哪份票」。
    cal = audit.trade_cal()
    later = [x for x in cal if x > day]
    exec_day = later[0] if later else day

    out["data_day"] = day
    out["exec_day"] = exec_day
    launch = read_launch(exec_day)
    out["launch"] = launch
    print("=" * 62)
    print("后峰盘前 · %s" % stamp)
    print("数据日 %s   执行日 %s" % (day, exec_day))
    if launch["found"]:
        print("启动落痕 %s 起 %d 次%s"
              % (launch["first_started_at"], launch["n_start"],
                 "，已 end" if launch["ended"] else "，**无 end —— 有启动没跑完**"))
    else:
        print("启动落痕 无记录 —— 不是计划任务拉起，或落痕写入前就失败了")
    print("=" * 62)

    # ── 1. 校对 ─────────────────────────────────────────────────────────
    au = audit.audit(day)
    out["audit"] = {"verdict": au["verdict"], "problems": au["problems"],
                    "warns": au["warns"]}
    print("\n[1/6] 数据校对 → %s" % au["verdict"])
    for p in au["problems"]:
        print("      × %s" % p)
    for w in au["warns"]:
        print("      ! %s" % w)
    out["steps"].append({"step": "audit", "verdict": au["verdict"]})
    if au["verdict"] == "fail":
        print("\n阻断：数据不通过，不出报告。")
        out["rc"] = 2
        _dump(out, a.json_out)
        return 2

    # ── 2. 选票 ─────────────────────────────────────────────────────────
    pick_json = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json" % day)
    reused = bool(a.skip_pick and os.path.exists(pick_json))
    # 必须在分支外初始化：复用路径不会给 pkg 赋值，第 6 步要读它。
    # 放在 else 里当局部变量，会让「复用」那条路在末尾炸 NameError ——
    # 而 NameError 发生在报告已出之后，看起来像「跑完了」，实际最后一步没跑。
    pkg: str | None = None
    if reused:
        pv0 = pick_provenance(pick_json)
        # 复用前必须证明「这份票属于今天、且属于当前口径」。只看文件存在
        # 就复用，是 11-12 08:27 那次事故的根因：拿 11-11 的 json 出了一份
        # 抬头写 11-12 的报告，外观与当天新算完全一致。溯源要能阻断，
        # 不能只是打印一行让人自己看。
        reason = reuse_rejection(pv0, day)
        if reason:
            print("\n阻断：复用的选票不可用 —— %s" % reason)
            print("      请去掉 --skip-pick 重算，不要拿旧票出今天的报告。")
            out["steps"].append({"step": "pick", "error": "stale_reuse",
                                 "reason": reason})
            out["rc"] = 3
            _dump(out, a.json_out)
            return 3
        print("\n[2/6] 选票 → 复用 %s（未重算）" % os.path.basename(pick_json))
        print("      这份票算于 %s（来源 %s）"
              % (pv0.get("picker_run_at"), pv0.get("picker_run_at_source")))
        print("      ⚠ 名单不是本次运行算的 —— 简报会带 reused=true，别和当天新算看成一样")
        out["steps"].append({"step": "pick", "skipped": True, "reused": True,
                             "pick_json": pick_json})
    else:
        pkg = latest_pkg()
        if not pkg:
            print("\n阻断：找不到共识池推送包（strategy_center_inbox）")
            out["steps"].append({"step": "pick", "error": "no package"})
            out["rc"] = 3
            _dump(out, a.json_out)
            return 3
        print("\n[2/6] 选票 → pkg=%s asof=%s" % (pkg, day))
        rc, log = run([PY, "-X", "utf8", PICK, "--pkg", pkg, "--asof", day], cwd=PICK_DIR)
        tail = "\n".join(log.strip().splitlines()[-6:])
        print("      rc=%d\n%s" % (rc, "\n".join("      " + l for l in tail.splitlines())))
        out["steps"].append({"step": "pick", "rc": rc, "pkg": pkg, "tail": tail,
                             "reused": False, "pick_json": pick_json})
        if rc != 0:
            out["rc"] = 3
            _dump(out, a.json_out)
            return 3

    # 选票溯源（P1）：无论算的还是复用的，都从产物里读「到底什么时候算的」。
    # 老产物没有 picker_run_at，退回 mtime 并标 source=mtime —— 不标就等于冒充。
    pvp = pick_provenance(pick_json)
    out["pick_provenance"] = dict(pvp, reused=reused)

    # 校对与选票对龙虎榜是两套视角：校对本读本地分片，选票会向源端补拉。
    # 11-12 回执因此出现过「能力降级」与「V1b 实际生效」并存的假告警。
    # 以选票产物的判定为准改写校对结论，免得报告把已解决的问题再报一遍。
    try:
        _tl = (json.load(io.open(pick_json, encoding="utf-8"))
               .get("toplist_filter") or {})
    except (ValueError, OSError):
        _tl = {}
    if _tl.get("status") == "OK":
        if any("龙虎榜" in w for w in out["audit"]["warns"]):
            out["audit"]["warns"] = [w for w in out["audit"]["warns"]
                                     if "龙虎榜" not in w]
            out["audit"]["warns"].append(
                "龙虎榜：本地分片缺目标日，选票阶段已源端补拉，V1b 生效（来源 %s）"
                % (_tl.get("source") or "未标"))
    elif _tl.get("status") not in (None, "OK"):
        out["audit"]["warns"].append(
            "龙虎榜：选票产物判定 %s，上榜过滤未生效" % _tl.get("status"))
    print("      选票溯源：picker_run_at=%s（%s）reused=%s"
          % (pvp.get("picker_run_at"), pvp.get("picker_run_at_source"), reused))

    # 选票篮子：回答「今天开盘到底用哪份票」，与「基于哪天数据算的」分开存。
    # 2026-11-12 的坑正是这两件事被混成一件：08:27 复用 11-11 的 json，
    # 简报却打 exec_day=20261112，外观与当天新算完全一致。
    codes: list[str] = []
    try:
        _pj = json.load(io.open(pick_json, encoding="utf-8"))
        codes = [x["ts_code"] for x in (_pj.get("top15_adj") or [])]
    except (ValueError, OSError, KeyError) as e:
        print("      ⚠ 选票产物读不出名单（%s）—— 篮子仍落盘，codes 为空" % e)
    basket = {"schema": "preopen_basket/v1", "exec_day": exec_day, "data_day": day,
              "pick_json": pick_json, "reused": reused,
              "picker_run_at": pvp.get("picker_run_at"),
              "picker_run_at_source": pvp.get("picker_run_at_source"),
              "chain_policy_v": pvp.get("chain_policy_v"),
              "run_started": stamp, "launch_started": launch.get("started_at"),
              "codes": codes, "n_codes": len(codes)}
    basket_path = os.path.join(DELIV, "preopen_basket_%s.json" % exec_day)
    io.open(basket_path, "w", encoding="utf-8").write(
        json.dumps(basket, ensure_ascii=False, indent=1))
    out["basket"] = {"path": basket_path, "n_codes": len(codes), "reused": reused}
    print("      篮子 %s（%d 只）" % (os.path.basename(basket_path), len(codes)))

    # ── 3. 报告 ─────────────────────────────────────────────────────────
    # 先落一次盘再出报告：简报要读本次运行的选票溯源（picker_run_at / reused），
    # 读不到就退化成「来源未确认」。落盘顺序即数据依赖顺序。
    _dump(out, a.json_out)
    print("\n[3/6] 简报 →")
    rc, log = run([PY, "-X", "utf8", os.path.join(HERE, "build_preopen_report.py"),
                   "--date", day, "--run-json", a.json_out])
    print("      " + log.strip().splitlines()[-1] if log.strip() else "      (无输出)")
    out["steps"].append({"step": "report", "rc": rc, "out": log.strip()})

    out["rc"] = 1 if (au["warns"] or rc != 0) else 0
    if rc != 0:
        out["rc"] = 3

    # ── 4. 记账 + 长建议 ────────────────────────────────────────────────
    # 事件化必须读本次运行产物，所以先落一次盘再调 —— 否则 event_log
    # 读到的是上一次的结果，记的就不是今天这件事。
    _dump(out, a.json_out)
    print("\n[4/6] 记账 →")
    rc_e, log_e = run([PY, "-X", "utf8", os.path.join(HERE, "event_log.py"),
                       "--from-run", "--run-json", a.json_out])
    tail_e = "\n".join(log_e.strip().splitlines()[:8])
    print("\n".join("      " + l for l in tail_e.splitlines()) if tail_e
          else "      (无输出)")
    out["steps"].append({"step": "event", "rc": rc_e, "out": tail_e})

    rc_p, log_p = run([PY, "-X", "utf8", os.path.join(HERE, "evolve.py"), "--propose"])
    tail_p = "\n".join(log_p.strip().splitlines()[:12])
    print("\n".join("      " + l for l in tail_p.splitlines()) if tail_p
          else "      (无输出)")
    out["steps"].append({"step": "evolve", "rc": rc_p, "out": tail_p})

    # ── 5. 进化台账（给人看的那一面）──────────────────────────────────
    # 前四步都是给机器和报告用的。这一步是唯一一个"给老田翻一眼"的产物：
    # 今天观察到什么、累计到哪了、还差多少才够格提建议。
    # 失败不影响主流程 —— 台账是输出，不是关卡。
    print("\n[5/6] 进化台账 →")
    # 文件名用执行日，不用数据日 —— 老田早上翻的是「今天这份台账」，
    # 写成 11-11 会让他以为是昨天那份（报告顶栏同一个坑，已经踩过一次）。
    # exec_day 在函数开头已算好（篮子落盘要用），此处复用，不再重复推导。
    md_out = os.path.join(DELIV, "preopen_evolve_%s.md" % exec_day)
    html_out = os.path.join(DELIV, "preopen_evolve_%s.html" % exec_day)
    rc_d, log_d = run([PY, "-X", "utf8", os.path.join(HERE, "evolve.py"),
                       "--digest", "--md-out", md_out, "--html-out", html_out])
    tail_d = "\n".join(log_d.strip().splitlines()[-2:]) if log_d.strip() else "(无输出)"
    print("\n".join("      " + l for l in tail_d.splitlines()))
    out["steps"].append({"step": "digest", "rc": rc_d,
                         "md": md_out, "html": html_out, "out": tail_d})

    # ── 6. 排序影子（只记录，不参与出票）───────────────────────────────
    # 为什么挂在盘前链上、而不是单开一个计划任务：
    #   它需要的两样输入 —— 共识包名与精排日 —— 只有这里现成有。另开任务就得
    #   再推一遍「最新包是哪个 / 数据日是哪天」，两处推导必然漂移，于是「影子
    #   和实盘用的是同一份输入」这个前提就没了，而整个对照的意义就建立在它上面。
    #
    # 为什么放在最后：前面任何一步失败都已 return，走到这里说明报告、篮子、
    #   台账全部落盘。影子整步崩掉，损失的只是「今天的对照没记上」。
    #
    # 为什么不改 rc：它是观测，不是关卡。同族于第 5 步 —— 计划任务的退出码
    #   回答的是「跑完了吗」，把观测失败报成任务失败，会让人去修一个不存在的问题。
    #
    # 为什么 pkg 要取「产物自述」而不是重推 latest_pkg()：复用路径下后者可能
    #   已经多出一个新包，那样两套排序的候选集就不同了，差异里混进「换了包」。
    print("\n[6/6] 排序影子 →")
    rec_s = shadow_step(pkg, day, pick_json)
    if rec_s.get("skipped"):
        print("      跳过：%s" % rec_s["reason"])
    elif rec_s.get("error"):
        print("      ⚠ 影子未入账：%s —— 不影响今天的票" % rec_s["error"])
    else:
        for l in (rec_s.get("out") or "(无输出)").splitlines():
            print("      " + l)
        if rec_s.get("rc") != 0:
            print("      ⚠ 影子未入账（rc=%s）—— 不影响今天的票" % rec_s.get("rc"))
    out["steps"].append(rec_s)

    _dump(out, a.json_out)
    print("\n完成：rc=%d（0 全绿 / 1 有告警 / 2 阻断 / 3 脚本失败）" % out["rc"])
    return out["rc"]


def _dump(out: dict, path: str | None) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    io.open(path, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
