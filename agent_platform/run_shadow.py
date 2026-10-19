# -*- coding: utf-8 -*-
"""5 日影子运行账本（K-STOCK-004 蓝图 v2 判据的数据采集端）

每日 17:10 计划任务触发（周一~五，避开 03:00 备份 / 15:30 SubPushEod 窗口）：
  股票篮子逐票跑 live 全链路 → 决策快照写 data/shadow/shadow_ledger.jsonl
  lineage：run_id 可回查 data/checkpoints.db 的辩论 trace 与节点 checkpoint。

设计纪律：
  - 幂等：同日同票已入账则跳过（防手动重跑污染 5 日样本）
  - 健壮：单票失败记 failed 行，不中断整篮（无人干预要求）
  - 落文件：stdout/stderr 由计划任务重定向 data/shadow/last_run.log（长跑不依赖管道）
  - 隔离：`--root <dir>` 或 `SHADOW_DATA_ROOT` 把**全部存储**（账本/checkpoints/
    memory/幂等库/p0_alert_state/org tasks）重定向到该根；配合 `--rehearsal`
    （或 SHADOW_REHEARSAL=1）时数据根若仍解析为真仓 data/ → **拒绝执行 exit 2**，
    且禁用飞书推送。用于周一前对 run 路径做零污染预演。
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from checkpoints import Checkpointer  # noqa: E402
from idem_hook import DualTrackHook  # noqa: E402  M0 P4 双轨幂等
from interpreter import Interpreter, Team  # noqa: E402
from llm import OllamaLLM  # noqa: E402
from memory import MemoryStore  # noqa: E402
from tools import make_context, quote_snapshot, kline_daily  # noqa: E402
from regime_roster import dk_snapshot, enrich_row, bars_to_df  # noqa: E402  M4-P3 双刀阵容
from strategy_runtime import run_strategy as _rt_run_strategy  # noqa: E402  拍板01 唯一口径

BASKET = ["600519", "000858", "601318", "300750", "600036"]  # 茅台/五粮液/平安/宁德/招行


def _cli_opt(flag, argv=None):
    """取 CLI 选项值（flag value 形态）；argv 缺省 sys.argv[1:]。
    pytest 的 argv 不含本脚本私有 flag，故 import 期解析不会误伤测试。"""
    a = list(sys.argv[1:] if argv is None else argv)
    if flag in a:
        i = a.index(flag)
        if i + 1 < len(a):
            return a[i + 1]
    return None


_CLI_ROOT = _cli_opt("--root")
if _CLI_ROOT:
    os.environ.setdefault("SHADOW_DATA_ROOT", _CLI_ROOT)
if "--rehearsal" in sys.argv:
    os.environ.setdefault("SHADOW_REHEARSAL", "1")

# ---- 数据根注入（M5 缺口收口：run 路径此前无隔离根，无法预演）------------
# 默认真仓 data/；设 SHADOW_DATA_ROOT 或 --root <dir> 即整体隔离。
# 纪律：**所有存储路径都从 LEDGER 反推（_data_root()）**，不另立并列常量——
#   否则 monkeypatch LEDGER 的测试面与隔离阀之间会出现「只隔离一半」的裂缝
#   （判例重演：闭包定值绕过 setattr patch 致 26 红）。
REHEARSAL = os.environ.get("SHADOW_REHEARSAL") == "1"
DATA_ROOT = Path(os.environ.get("SHADOW_DATA_ROOT") or (BASE / "data"))
LEDGER = DATA_ROOT / "shadow" / "shadow_ledger.jsonl"
# K-ORG-129 / T-20261008-003（B8：全篮子 HOLD 使 J2 失效）：score 基数表——
# 枚举档 × confidence 连续落账（见 parse_decision），HOLD=0 中性锚不动；
# WATCH 新档 -0.5：L4 存疑观察＝弱负证据，低于 HOLD、高于 SELL，不再与 HOLD 同分
REC_SCORE = {"STRONG_BUY": 2.0, "BUY": 1.0, "HOLD": 0.0, "WATCH": -0.5,
             "SELL": -1.0, "STRONG_SELL": -2.0}
# 运行期单策略产出超时（§5.2 过渡缓解）：M3 受理沙箱挡不住运行期死循环数据，
# 17:10 首跑挂死风险兜底——超时/异常单策略 fail-open 跳过，其余照常落账。
# 时长三通道（任务 #112）：传参 timeout_s > 环境变量 SHADOW_PROPOSE_TIMEOUT_S
# （导入期解析一次；未设/非法/非正值回退默认，>0 合法浮点才生效）> 默认 10.0。
def _env_propose_timeout(default):
    """SHADOW_PROPOSE_TIMEOUT_S → float；未设/坏值回退 default。"""
    raw = os.environ.get("SHADOW_PROPOSE_TIMEOUT_S")
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v > 0 else default


PROPOSE_TIMEOUT_S = _env_propose_timeout(10.0)
# failed 即时告警（K-STOCK-013）：复用已验证推送链；P0 语义=不等 17:35 日报
PUSH_SCRIPT = BASE.parent / "news_collector" / "send_feishu.py"
# ★判例对齐：p0_alert_state 不立并列常量（读侧 95 行走 _data_root() 运行时反推，
# 写侧曾用模块级 ALERT_STATE——monkeypatch/隔离场景读写分裂「只隔离一半」）。
# 统一：读写都经 _data_root()/shadow/p0_alert_state.json。


def _data_root():
    """当前账本所属数据根——运行时读 LEDGER 模块属性，monkeypatch 后整体重定向。
    存储路径一律经此反推，保证「账本在哪，附属存储就在哪」。"""
    return Path(LEDGER).resolve().parent.parent




def push_failed_alert(failed_rows, today, parse_fail_rows=None):
    """篮子跑完有 failed 时立即推飞书简讯；同日只推一次（节流）。任何异常不中断主流程。
    K-STOCK-338：parse_fail_rows（done 但决策解析失败）并入同条告警——failed 是 P0、
    parse 失败是样本缺失（模型偶发不守格式），后者此前完全静默。"""
    if not failed_rows and not parse_fail_rows:
        return
    if REHEARSAL:
        # 预演阀：飞书推送是**真实外部副作用**，预演一律不发（只报本地统计）
        print("[alert] 预演模式：跳过飞书推送（failed=%d parse_fail=%d）"
              % (len(failed_rows), len(parse_fail_rows or [])))
        return
    try:
        alert_state = _data_root() / "shadow" / "p0_alert_state.json"
        st = json.loads(alert_state.read_text(encoding="utf-8")) if alert_state.exists() else {}
        if st.get("date") == today and st.get("pushed"):
            print("[alert] 今日 failed 告警已推过（节流），跳过")
            return
        lines = ["**[P0] 影子运行 %d 笔失败（需处置）**" % len(failed_rows)]
        for r in failed_rows:
            lines.append("- %s：%s" % (r["symbol"], r.get("error", "?")[:100]))
        if parse_fail_rows:
            lines.append("**[P1] %d 笔 done 但 D1 决策解析失败（评估样本缺失·不阻塞）**"
                         % len(parse_fail_rows))
            for r in parse_fail_rows:
                lines.append("- %s：latency %.0fs" % (r["symbol"], r.get("latency_s", 0)))
        tmp = _data_root() / "shadow" / "_p0_alert_tmp.md"
        tmp.write_text("\n".join(lines), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(PUSH_SCRIPT), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = (r.stdout or r.stderr or "").strip()
        ok = r.returncode == 0 and "SEND_OK" in out
        print("[alert] failed 即时告警推送：%s" % (out.splitlines()[-1] if out else "无输出"))
        tmp.unlink(missing_ok=True)
        if ok:
            alert_state = _data_root() / "shadow" / "p0_alert_state.json"
            alert_state.write_text(json.dumps({"date": today, "pushed": True}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[alert] 告警推送异常（不中断主流程）：%s" % e)


def _balanced_objects(text):
    r"""花括号计数法提取 text 中所有配平的 JSON 对象块（K-ORG-137）。

    旧实现 `\{[^{}]*recommendation[^{}]*\}` 禁止对象内出现花括号——D1
    rationale 含嵌套 { } 时失配 → parse_ok=false 样本静默缩水。计数法
    逐块配平，支持嵌套，语义不变（仍只认含 recommendation 的对象）。"""
    objs = []
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:  # 配平才收录；未配平（截断输出）跳过
            objs.append(text[i:j + 1])
        i = j + 1
    return objs


def parse_decision(text):
    """从 D1 输出提取 JSON 决策；失败返回 None（评估时计为解析失败样本）"""
    try:
        for block in _balanced_objects(text):
            j = json.loads(block)
            if "recommendation" not in j:
                continue  # 非决策对象（如解释段里的 JSON 引用）跳过
            # K-ORG-129：score 连续化＝枚举基数 × confidence——
            # 此前纯枚举（HOLD 恒 0、18/25 并列零）致 J2 Spearman 无分辨（B8）；
            # HOLD 基数 0 保持中性（0×c 仍 0），confidence 解析失败按 0 容错不毒化
            base = REC_SCORE.get(str(j.get("recommendation", "")).upper(), 0.0)
            try:
                conf = float(j.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            j["score"] = round(base * conf, 3)
            return j
    except json.JSONDecodeError:
        return None
    return None


def _propose_with_timeout(registry, strategy_id, context, timeout_s=None):
    """单策略产出超时包裹（设计稿风险表 §5.2 过渡缓解：运行期防拖死）。

    唯一口径（拍板 01，2026-10-18）：超时包裹机制已收敛到
    engine/strategy_runtime.run_strategy（裸 daemon Thread + 结果盒，
    禁 ThreadPoolExecutor——其 worker 3.9+ 非 daemon，解释器退出会
    join 全部 worker，死循环策略会把进程挂在 exit 上，与「防拖死」
    目标自相矛盾）。本函数收缩为对该隔离门的薄封装，仅做影子域
    语义转换：

    - fail-visible → fail-open：隔离门返回 timeout/error/rejected 的
      result dict，此处转 stderr 一行诊断 + None（跳过该策略、不中断
      影子主链，对齐本文件 done 分支既有风格）；调用方 _collect_verdicts
      据此计数。
    - registry.run 抛异常（含 StrategyError）：隔离门结果盒已捕获转
      error 分支，fail-open 行为不变。
    - timeout_s 缺省读模块级 PROPOSE_TIMEOUT_S（None 哨兵而非默认参数
      绑定，保证 monkeypatch 常量对无参调用生效）。
    - Python 线程无法强杀：超时后 worker 继续后台跑，随进程退出回收
      （daemon）；受理时 M3 已挡一轮，运行期长尾用「跳过」兜底。
    """
    if timeout_s is None:
        timeout_s = PROPOSE_TIMEOUT_S
    r = _rt_run_strategy(registry, strategy_id, context, timeout_s=timeout_s)
    status = r["status"]
    if status == "success":
        return r.get("verdict")
    if status == "timeout":
        print("[strategy] %s propose 超时（>%.1fs），fail-open 跳过"
              % (strategy_id, timeout_s), file=sys.stderr)
    else:  # error / rejected：隔离门 error 字段含 strategy_id 前缀诊断
        print("[strategy] %s propose 异常 fail-open 跳过：%s"
              % (strategy_id, str(r.get("error"))[:120]), file=sys.stderr)
    return None


def _skip_record(strategy_id, result):
    """隔离门非 success 结果 → 落账 skip 记录（任务 #112：SKIPPED_* + reason）。

    reason 取隔离门 error 字段（含 strategy_id 前缀诊断，超时形如
    「strategy_id=x 执行超时（N.NNNs；后台线程无法强杀）」），截 200 字符。"""
    status = result.get("status")
    if status == "timeout":
        st = "SKIPPED_TIMEOUT"
    else:  # error / rejected
        st = "SKIPPED_ERROR"
    return {"strategy_id": strategy_id, "status": st,
            "reason": str(result.get("error") or status)[:200]}


def _collect_verdicts_with_skips(registry, context, timeout_s=None):
    """done 分支逐策略产出（任务 #112 扩展）：返回 (vs, skips)。

    - vs：成功策略的 Verdict 列表（与 _collect_verdicts 口径一致）；
    - skips：被跳过策略的落账记录，元素形如
      {"strategy_id", "status": "SKIPPED_TIMEOUT"|"SKIPPED_ERROR", "reason"}，
      由调用方挂到影子账本行 strategy_skipped 字段；
    - 主链继续：单策略超时/异常只产生一条 skip 记录，绝不中断循环。
    timeout_s 缺省读模块级 PROPOSE_TIMEOUT_S（None 哨兵，调用期解析）。"""
    if timeout_s is None:
        timeout_s = PROPOSE_TIMEOUT_S
    vs, skips = [], []
    for sid in registry.list_ids():
        r = _rt_run_strategy(registry, sid, context, timeout_s=timeout_s)
        if r["status"] == "success":
            vs.append(r.get("verdict"))
            continue
        skips.append(_skip_record(sid, r))
        if r["status"] == "timeout":
            print("[strategy] %s propose 超时（>%.1fs），fail-open 跳过"
                  % (sid, timeout_s), file=sys.stderr)
        else:  # error / rejected：隔离门 error 字段含 strategy_id 前缀诊断
            print("[strategy] %s propose 异常 fail-open 跳过：%s"
                  % (sid, str(r.get("error"))[:120]), file=sys.stderr)
    if skips:
        print("[warn] strategy 域跳过 %d 个策略（超时/异常 fail-open，影子主链不受影响）"
              % len(skips))
    return vs, skips


def _collect_verdicts(registry, context, timeout_s=None):
    """done 分支逐策略产出：超时/异常的策略跳过并计数，其余照常入 vs 列表。
    （兼容入口：等价 _collect_verdicts_with_skips(...)[0]，签名/返回不变。）"""
    vs, _skips = _collect_verdicts_with_skips(registry, context, timeout_s=timeout_s)
    return vs


def build_ledger_row(today, symbol, name, run_id, price_at_decision, dec,
                     parse_ok, source_status, latency_s, ts=None):
    """落账行构造（M5 刀 1：影子账本 schema 收口）。

    - schema_version="m5.v1" 起新行携带；存量 20 行无标记=v0 冻结不重写
      （K-ORG-129 连续化两代并存，旧行随样本积累自然滑出评估窗口）；
    - degraded 扩义＝数据源异常 ∪ 决策解析失败——此前 parse_ok=false 行
      degraded=False（10-17 实测唯一 false 案例即此缺口），a0_chief 日报
      P1 计数与 morning_check 对解析失败行失明；
    - dec 为 parse_decision 结果（解析失败时 {}）。
    """
    if ts is None:
        ts = time.time()
    src_bad = any(v != "normal" for v in (source_status or {}).values())
    return {
        "date": today, "ts": ts, "symbol": symbol,
        "name": name, "status": "done",
        "run_id": run_id, "price_at_decision": price_at_decision,
        "recommendation": dec.get("recommendation"),
        "confidence": dec.get("confidence"),
        "score": dec.get("score"), "target_range": dec.get("target_price_range"),
        "parse_ok": bool(parse_ok),
        "degraded": (not parse_ok) or src_bad,
        "schema_version": "m5.v1",
        "latency_s": latency_s,
    }


def already_done(today, symbol):
    if not LEDGER.exists():
        return False
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("date") == today and r.get("symbol") == symbol and r.get("status") == "done":
                return True
        except json.JSONDecodeError:
            continue
    return False


def open_failed_task(failed_rows, today, store=None):
    """failed 自动开单给研发部（K-ORG-013 / P2-3）：P0 直入 doing；同日同因节流。异常不中断主流程。
    store 参数供测试注入临时库（默认真库）。"""
    if not failed_rows:
        return None
    try:
        from tasks import TaskStore  # noqa: E402  engine path 已在顶部注入
        ts = store or TaskStore(_data_root() / "org" / "tasks")
        title = "影子运行 failed 处置（%s）" % today
        dup = [t for t in ts.ls(dept="dev") if t["title"] == title]
        if dup:
            print("[task] 当日 failed 开单已存在（%s），跳过" % dup[0]["task_id"])
            return dup[0]["task_id"]
        body = "\n".join("- %s：%s" % (r["symbol"], r.get("error", "?")[:120])
                         for r in failed_rows)
        tid = ts.open("dev", title, body, "P0",
                      opened_by="system/monitor", p0=True)
        print("[task] failed 自动开单：%s（P0 直入 doing，待研发部处置）" % tid)
        return tid
    except Exception as e:  # noqa: BLE001
        print("[task] 自动开单异常（不中断主流程）：%s" % e)
        return None


def _assert_not_real_root(data_root):
    """预演默认拒绝阀：REHEARSAL 下数据根解析为真仓 data/ 时直接拒绝执行。

    判例：opt-out 型安全阀不是 fail-safe。演示污染已复发三次，凡是「先跑再说」
    的默认路径都会在某次手滑里落到真仓——所以预演必须显式给隔离根，
    且给错了要在**写任何字节之前**撞墙。"""
    if not REHEARSAL:
        return True
    if Path(data_root).resolve() == (BASE / "data").resolve():
        print("[FATAL] 预演模式拒绝写真实数据根 %s —— "
              "请设 SHADOW_DATA_ROOT 或 --root <隔离目录>" % data_root)
        return False
    return True


# ---- 日志/账本追加打开：有界重试（2026-10-19 判例修复）--------------------
# 周一 17:10 首跑实证：open(last_run.log, "a") 撞瞬时独占锁（嫌疑
# watch-daemon/提权进程/杀毒软件毫秒级握持，锁定者不可追认）→
# PermissionError [Errno 13] → 5 标的零落账（未落账≠假绿）。锁通常瞬时
# 释放，故加有界重试+退避；无锁时首次即成功，行为与裸 open 零差异。
#: 最大尝试次数（含首次）
LOG_OPEN_MAX_ATTEMPTS = 5
#: 逐次退避秒数（长度 = MAX_ATTEMPTS - 1；总等待 ≤7.5s，不拖 17:10 窗口）
LOG_OPEN_BACKOFF_S = (0.5, 1.0, 2.0, 4.0)


class _LogOpenError(OSError):
    """日志/账本追加打开重试耗尽（fail-visible 诊断载体；OSError 子类）。

    禁静默跳过：跳过=零落账假绿，正是 10-19 事故形态（裸 traceback、
    无重试、无诊断）。本异常携带路径/尝试次数/末次 errno 供排障。
    """


def _open_log_append(path, max_attempts=None, backoff=None, sleep=None):
    """有界重试打开追加写文件（last_run.log / shadow_ledger.jsonl 同类打开点）。

    - 仅对 OSError 系重试（Windows 瞬时独占锁表现为 PermissionError
      [Errno 13]，是 OSError 子类）；其余异常（如 ValueError）原样上抛，
      重试无意义；
    - 退避序列默认 0.5/1/2/4s 共 5 次；sleep 可注入（测试零等待）；
    - 耗尽抛 _LogOpenError——调用方必须 fail-visible 退出，禁静默跳过；
    - 无锁时首次即成功：与裸 open(path, "a", encoding="utf-8") 零差异。
    """
    attempts = LOG_OPEN_MAX_ATTEMPTS if max_attempts is None else int(max_attempts)
    waits = LOG_OPEN_BACKOFF_S if backoff is None else tuple(backoff)
    nap = time.sleep if sleep is None else sleep
    last_err = None
    for i in range(attempts):
        try:
            return open(path, "a", encoding="utf-8")
        except OSError as e:
            last_err = e
            if i + 1 >= attempts:
                break
            delay = waits[i] if i < len(waits) else (waits[-1] if waits else 0.0)
            print("[log] %s 打开被占（errno=%s），%.1fs 后重试 %d/%d"
                  % (path, getattr(e, "errno", None), delay, i + 2, attempts),
                  file=sys.stderr)
            nap(delay)
    raise _LogOpenError("%s 追加打开重试 %d 次仍失败（末次 %r）——疑似瞬时独占锁"
                        "未释放，fail-visible 中止" % (path, attempts, last_err))


def main():
    data_root = _data_root()
    if not _assert_not_real_root(data_root):
        return 2
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not sys.stdout.isatty():
        # 计划任务无终端：输出自重定向落文件（长跑纪律，不依赖管道）
        log_path = LEDGER.parent / "last_run.log"
        try:
            log_f = _open_log_append(log_path)
        except _LogOpenError as e:
            # fail-visible（10-19 判据）：日志打开失败禁静默跳过——跳过=零落账
            # 假绿。此时 sys.stderr 尚未被替换（仍是计划任务经 cmd /c 2>&1
            # 捕获的真 stderr，落 last_run.log），诊断可见；退出码 3 经
            # shadow_automation.ps1 透传计划任务历史。
            print("[FATAL] 影子运行日志打开失败，本次中止（未落任何账，非假绿）：%s"
                  "——处置：查 watch-daemon/提权进程/杀毒软件是否瞬时握持 %s；"
                  "锁定释放后下一触发窗（工作日 17:10）自动重试"
                  % (e, log_path), file=sys.stderr)
            return 3
        sys.stdout = log_f
        sys.stderr = log_f
        print("===== shadow run start %s =====" % datetime.now())
    if REHEARSAL:
        print("[rehearsal] 隔离模式 data_root=%s（真仓 data/ 零写入；飞书推送已禁）"
              % data_root)
    today = datetime.now().strftime("%Y-%m-%d")
    team = Team(BASE / "registry" / "team_invest.json")
    grants = {a["agent_id"]: a["memory_grants"] for a in team.agents}
    memory = MemoryStore(data_root / "memory", grants_by_agent=grants, enforce=True)
    cp = Checkpointer(data_root / "checkpoints.db")
    llm = OllamaLLM()
    interp = Interpreter(team, llm, memory, cp)

    summary = []
    failed_rows = []
    parse_fail_rows = []  # K-STOCK-338：done 但决策解析失败（评估样本缺失，此前静默）
    # M0 P4 双轨幂等：date+symbol+done JSONL 仍是权威；并行写统一账本
    # （scope=shadow_daily，死信窗 1h——单票耗时分钟级，超 1h 视为进程已死）。
    hook = DualTrackHook(
        "shadow_daily", stale_seconds=3600,
        # 真仓保持原行为（None→默认库/IDEMPOTENCY_DB）；隔离时才改指本根
        db_path=(None if data_root == (BASE / "data").resolve()
                 else data_root / "idempotency" / "idempotency.db"))
    for symbol in BASKET:
        if already_done(today, symbol):
            print("[skip] %s 今日已入账" % symbol)
            continue
        claim = hook.claim("daily_decision", "%s|%s" % (today, symbol),
                           {"date": today, "symbol": symbol, "strategy": "basket_v2"})
        attempt_id = claim.get("attempt_id", "")
        if claim["outcome"] == "completed":
            print("[skip] %s 幂等账本已完成（result_ref=%s）"
                  % (symbol, claim.get("result_ref", "")))
            continue
        if claim["outcome"] == "in_progress":
            print("[skip] %s 幂等账本执行中（另一进程在跑）" % symbol)
            continue
        if claim["outcome"] == "conflict":
            print("[warn] %s 幂等账本冲突（跳过）：%s"
                  % (symbol, claim.get("conflict_reason", "?")))
            continue
        if claim["outcome"] == "unavailable":
            print("[idem] 账本不可用（fail-open 继续旧链）：%s"
                  % claim.get("error", "?"))
        row = None
        for attempt in (1, 2):  # 脚本内重试一次（Ollama 偶发超时；幂等不受影响，done 才跳过）
            try:
                data_ctx = make_context(symbol)
                t0 = time.time()
                result = interp.run(symbol, data_ctx=data_ctx)
                dec = parse_decision(result["decision"])
                parse_ok = dec is not None  # K-STOCK-338：解析失败显式记录，不再静默吞成 {}
                dec = dec or {}
                price = quote_snapshot(symbol)["price"]
                row = build_ledger_row(
                    today, symbol, data_ctx.get("name", ""), result["run_id"],
                    price, dec, parse_ok, result["status"],
                    round(time.time() - t0, 1))  # M5 刀 1：schema_version+degraded 扩义
                if not parse_ok:
                    print("[warn] %s D1 决策解析失败（输出无 recommendation JSON·已入账 parse_ok=false）"
                          % symbol)
                # M4-P3 双刀阵容观测（fail-open：dk 异常不影响影子主链）
                bars = None
                try:
                    bars = kline_daily(symbol, 30)
                    snap = dk_snapshot(bars_to_df(bars))
                except Exception as _dke:  # noqa: BLE001
                    snap = {"dk_error": str(_dke)[:120]}
                row = enrich_row(row, snap)
                # M6 strategy 域挂接（K-ORG-167，fail-open：策略产出为影子
                # 并行参考，主链零依赖；root=DATA_ROOT 尊重隔离根预演阀）
                if bars:
                    try:
                        from shadow_strategies import build_default_registry
                        from strategy_registry import record_verdicts
                        reg = build_default_registry()
                        # 军团 Codex bridge 装配（2026-10-18 拍板 M2）：
                        # processed/ 已注册插件并入影子 run，fail-open
                        try:
                            sys.path.insert(0, str(BASE / "adapters"))
                            from codex_bridge import load_registered
                            _n, _s = load_registered(reg)
                            if _n or _s:
                                print("[strategy] codex 桥接插件 +%d (skip %d)"
                                      % (_n, _s))
                        except Exception as _ce:  # noqa: BLE001
                            print("[warn] codex 桥接装载跳过（首批三策略不受影响）：%s"
                                  % str(_ce)[:120])
                        sctx = {"symbol": symbol,
                                "closes": [float(b[2]) for b in bars
                                           if len(b) > 2 and b[2] is not None]}
                        # §5.2 过渡缓解：逐策略超时包裹（超时/异常 fail-open
                        # 跳过该策略，不拖垮其余；落账 schema/verdict_key 不变）
                        vs, skipped_recs = _collect_verdicts_with_skips(reg, sctx)
                        srep = record_verdicts(vs, root=DATA_ROOT,
                                               trade_date=today,
                                               run_id=result["run_id"])
                        print("[strategy] %s 产出 %d 条 recorded=%d replayed=%d"
                              % (symbol, len(vs), srep["recorded"],
                                 srep["replayed"]))
                        # 任务 #112：超时/异常跳过的策略落账进影子行（带 reason）；
                        # 无 skip 时不加键（m5.v1 超集兼容，附加可选字段）
                        if skipped_recs:
                            row["strategy_skipped"] = skipped_recs
                    except Exception as _se:  # noqa: BLE001
                        print("[warn] strategy 域降级（影子主链不受影响）：%s"
                              % str(_se)[:120])
                break
            except Exception as e:  # noqa: BLE001  无人干预要求：失败入账不中断
                row = {"date": today, "ts": time.time(), "symbol": symbol,
                       "status": "failed", "error": str(e)[:200]}
                if attempt == 1:
                    print("[retry] %s 首次失败，3s 后重试：%s" % (symbol, str(e)[:100]))
                    time.sleep(3)
        try:
            # 同类打开点（与 last_run.log 同目录同锁况）：账本 append 也走
            # 有界重试；打开失败禁静默跳过该票——跳过=样本静默缺失（假绿）。
            with _open_log_append(LEDGER) as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except _LogOpenError as e:
            # fail-visible：本票未落账、整轮中止。已入账票不受影响（逐票
            # append+close）；双轨幂等该 attempt 不回写 completed，死信窗
            # （1h）后下次触发可重跑本票，done 票按幂等跳过。
            print("[FATAL] 影子账本打开失败，%s 未落账，本次运行中止：%s"
                  "——已入账票保留；剩余标的由下一触发窗（工作日 17:10）自动补齐"
                  % (symbol, e), file=sys.stderr)
            return 3
        if attempt_id:  # 双轨回写：done→completed；failed→允许下次死信后重试
            hook.complete(attempt_id, ok=(row["status"] == "done"),
                          result_ref=str(row.get("run_id", "")),
                          error=str(row.get("error", ""))[:200])
        print("[%s] %s %s %s" % (row["status"], symbol, row.get("recommendation", ""),
                                 row.get("run_id", row.get("error", ""))))
        summary.append(row["status"])
        if row["status"] == "failed":
            failed_rows.append(row)
        elif row.get("parse_ok") is False:
            parse_fail_rows.append(row)

    print("shadow day %s done: %s" % (today, dict((s, summary.count(s)) for s in set(summary))))
    if parse_fail_rows:
        print("parse_fail: %d（%s）" % (len(parse_fail_rows),
                                        ",".join(r["symbol"] for r in parse_fail_rows)))
    push_failed_alert(failed_rows, today, parse_fail_rows)
    open_failed_task(failed_rows, today)


if __name__ == "__main__":
    sys.exit(main() or 0)
