# -*- coding: utf-8 -*-
"""R3 失败必响巡检（联动层一期 T3）：计划任务「AI工作站执行失败巡检」动作体。

职责：每 10 分钟拉一次 8091 执行健康总线数据（force=True 新鲜值，独立进程
绕缓存）→ 逐任务判 fail / stale / fail_scan_hit → 节流检查（24h 同任务同
错误码不重复）→ ① subprocess 推飞书 P0 卡（send_feishu.py，outbox UTF-8
临时文件）② TaskStore.open 自动开 dev 部门 P0 单（P0 直入 doing）→ 写
last_run.json 供 N1 v1.9 第(9)项探「必响链自身失联」。

红线：
- 只读总线 + 只写工单账本（agent_platform org tasks）+ 只写节流状态，
  **不回写总线**，不写 8091 v2_alert 队列（写侧单源，避免第二本账）；
- 不走 tools/devtask.py（那是产码工单调度器，会建 worktree 污染面）；
- 自身异常 exit 1（fail-visible：N1 晨检靠 last_run.json 新鲜度 + 退出码
  探出必响链失联）；日常心跳不推送，只推异常；
- 零新增依赖（纯 stdlib）。

状态文件落点（2026-10-18 拍板约定）：automation_runs/execution_watchdog/
（C:\\aios junction 即仓内 automation_runs，经 repo 路径写 = 同一落点）。

用法:
    python tools/execution_watchdog.py               # 正常巡检
    python tools/execution_watchdog.py --dry-run     # 只打印将推送清单，零写副作用
    python tools/execution_watchdog.py --inject 任务名 rc   # 演练注入（当次把该任务 rc 覆写，不碰真任务）
"""

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# 复用 R1 采集器（一份轮询逻辑两个消费方）：portal 在 _REPO 下
sys.path.insert(0, str(_REPO / "portal"))
import api_execution  # noqa: E402

# TaskStore 开单底座（run_shadow.open_failed_task 同款先例）：
# engine path 注入后 from tasks import TaskStore
sys.path.insert(0, str(_REPO / "agent_platform" / "engine"))

#: 节流窗口（小时）：同 (任务名, error_code) 24h 内不重复推送/开单
THROTTLE_HOURS = 24
#: 状态目录（junction 语义：repo 路径 = C:\aios 路径）
STATE_DIR = _REPO / "automation_runs" / "execution_watchdog"
#: 飞书推送 CLI（subprocess 判例链：消息走 UTF-8 临时文件，不走命令行参数）
SEND_FEISHU = _REPO / "news_collector" / "send_feishu.py"
#: 仓库标准解释器（与 n1_check.ps1 L58 同源）；缺省回退当前解释器
PY = Path(r"C:\Program Files\Python311\python.exe")
if not PY.is_file():
    PY = Path(sys.executable)


# ── 节流状态 ──────────────────────────────────────────────────────────────

def _state_file(name):
    """单任务节流状态文件路径：<任务名>.json（中文任务名合法文件名）。"""
    return STATE_DIR / ("%s.json" % name)


def throttled(name, code, now=None):
    """节流判定：24h 内同 (任务名, error_code) 已响过 → True。

    error_code 变化立即再响（同任务换了死法是新事件）。状态文件缺失/坏
    JSON 视为未节流（宁重报勿漏报）。
    """
    now = now or datetime.now()
    try:
        with open(_state_file(name), "r", encoding="utf-8") as f:
            st = json.load(f)
        last = datetime.fromisoformat(str(st.get("last_alert_ts")))
        return (now - last) < timedelta(hours=THROTTLE_HOURS) \
            and str(st.get("error_code")) == str(code)
    except (OSError, ValueError):
        return False


def _mark_alerted(name, code, ticket_id, now=None):
    """写节流状态（推送+开单成功后调用）。"""
    now = now or datetime.now()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_state_file(name), "w", encoding="utf-8") as f:
        json.dump({"last_alert_ts": now.isoformat(timespec="seconds"),
                   "error_code": code, "ticket_id": ticket_id},
                  f, ensure_ascii=False, indent=1)


# ── 飞书推送（P0 卡） ─────────────────────────────────────────────────────

def push_feishu(text):
    """经 send_feishu.py 推送（outbox UTF-8 临时文件，api_misc L790 判例）。

    Returns:
        bool: SEND_OK 判定。失败不抛异常（由调用方决定 exit 语义）。
    """
    fd, tmp = tempfile.mkstemp(prefix="exwd_msg_", suffix=".txt")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        r = subprocess.run(
            [str(PY), "-X", "utf8", str(SEND_FEISHU), tmp],
            capture_output=True, timeout=120)
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        return "SEND_OK" in out
    except Exception as e:  # noqa: BLE001
        print("[exwd] 飞书推送异常: %s" % e)
        return False
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass


# ── 自动开单（org 工单账本） ──────────────────────────────────────────────

def open_ticket(name, body, today):
    """TaskStore.open 开 dev 部门 P0 单（P0 直入 doing）；同日同标题去重。

    标题带日期（run_shadow.open_failed_task 判例）→ ts.ls 全量标题比对即
    「同日同标题」自然成立；跨日新单由 24h 节流防刷屏。

    Returns:
        tuple[str | None, str]: (task_id 或 None, 去重/异常说明)。
    """
    title = "计划任务失败必响：%s（%s）" % (name, today)
    try:
        from tasks import TaskStore  # noqa: E402  engine path 已注入
        ts = TaskStore(_REPO / "agent_platform" / "data" / "org" / "tasks")
        dup = [t for t in ts.ls(dept="dev") if t["title"] == title]
        if dup:
            return dup[0]["task_id"], "当日同标题工单已存在，跳过开单"
        tid = ts.open("dev", title, body, "P0",
                      opened_by="system/execution-watchdog", p0=True)
        return tid, "已开单（P0 直入 doing）"
    except Exception as e:  # noqa: BLE001  开单 fail-open：不中断推送
        return None, "开单异常（不中断推送）: %s" % e


# ── 异常条目归并 ──────────────────────────────────────────────────────────

def _error_code_of(entry):
    """异常条目 → error_code（fail 用 rc 原值；stale/fail_scan 用固定键）。"""
    if entry["status"] == "fail":
        return entry.get("last_result")
    if entry["status"] == "stale":
        return "STALE"
    return "FAIL_SCAN"


def _anomalies(payload):
    """从总线载荷提取异常条目（enabled 且 fail/stale 或 fail_scan_hit）。"""
    out = []
    for e in payload.get("tasks", []):
        if e.get("status") in ("fail", "stale"):
            out.append(e)
        elif e.get("status") == "ok" and e.get("fail_scan_hit"):
            out.append(e)  # rc 面正常但自愈日志命中 FAILED:（自愈失败）
    return out


def _fmt_alert(entry, ticket_note):
    """P0 措辞模板（设计文档 T3）。

    断点7 收口（2026-10-18）：fail_scan 从 AgentWatchdog 自愈日志单点泛化
    为 manifest 通用口径（SEND_FAIL/EXIT=1/traceback/exit:1 等），告警措辞
    同步去「自愈 FAILED: 良性」专属措辞，改为 rc 面良性 + 日志命中的通用
    表述（具体命中的 pattern 见 manifest 该任务 fail_scan 条目）。
    """
    kind = {"fail": "LastTaskResult=%s 非0" % entry.get("last_result"),
            "stale": "超期未跑 stale_min=%s" % entry.get("stale_min")}.get(
                entry["status"],
                "fail_scan 日志命中失败标记（rc=%s rc 面良性，"
                "见 manifest fail_scan 口径）" % entry.get("last_result"))
    lines = [
        "🚨 [P0] 执行失败必响：%s" % entry["name"],
        "- 异常类型：%s" % kind,
        "- 最近运行：%s  · 节奏：%s" % (
            entry.get("last_run") or "—", entry.get("expected_cadence") or "—"),
        "- 证据：log_mtime=%s" % (entry.get("log_mtime") or "无日志面"),
        "- 处置：%s" % ticket_note,
        "- 同任务同错误码 24h 内不重复推送（节流）",
    ]
    return "\n".join(lines)


# ── 演练注入（T5 三哑火演练用，不碰真任务） ───────────────────────────────

def _apply_injections(payload, inject):
    """--inject 任务名 rc：当次把该任务 last_result/status 覆写为注入值。

    同步修正派生字段（审查收口 2026-10-18）：result_ok 随 rc 重算，
    fail_scan_hit 归零——否则注入良性 rc 后 status=ok 但残留的
    fail_scan_hit 仍会把该条目送进 _anomalies（FAIL_SCAN 误报），
    演练通道与真实哑火混流。
    """
    for name, rc in inject.items():
        for e in payload.get("tasks", []):
            if e["name"] == name:
                e["last_result"] = rc
                e["result_ok"] = rc in api_execution.BENIGN_RC
                e["fail_scan_hit"] = False
                e["status"] = "fail" if rc not in api_execution.BENIGN_RC \
                    else "ok"


# ── 主流程 ────────────────────────────────────────────────────────────────

def _parse_inject(argv):
    """解析 --inject <任务名> <rc>（可多次）。"""
    inject = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--inject":
            if len(argv) < i + 3:
                raise SystemExit("--inject 需要 <任务名> <整数rc> 两个参数")
            try:
                inject[argv[i + 1]] = int(argv[i + 2])
            except ValueError:
                raise SystemExit("--inject 需要 <任务名> <整数rc> 两个参数")
            i += 3
        else:
            i += 1
    return inject


def main(argv=None):
    """巡检主入口。返回退出码：0=正常（含有条异常已响）、1=自身异常。

    --dry-run：全路径零写副作用——不写 last_run.json、不写节流状态、
    不推送、不开单（QA 风险①收口 2026-10-18）。

    自证：
        python -X utf8 tools/execution_watchdog.py --dry-run
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    inject = _parse_inject(argv)
    now = datetime.now()
    ran_at = now.isoformat(timespec="seconds")
    alerted, skipped = [], []
    exit_code = 0

    try:
        payload = api_execution.build_execution_health(force=True)
        _apply_injections(payload, inject)
        # dry-run 全路径零写副作用（QA 风险①收口）：心跳/节流状态一律不落盘，
        # 否则会覆写真实巡检留痕、并把 N1 v1.9 心跳喂成假新鲜。
        if not payload.get("ps_ok"):
            # 总线轮询失败 = 巡检失能 → exit 1（N1 v1.9 探出），
            # last_run.json 照写（fail-visible 链路最末端也要可见）。
            print("[exwd] 总线轮询失败: %s" % payload.get("ps_error"))
            alerted.append({"name": "(总线)", "error": payload.get("ps_error"),
                            "pushed": False, "ticket": None})
            if not dry:
                _write_last_run(ran_at, 1, alerted, skipped)
            return 1
        if not payload.get("manifest_ok"):
            print("[exwd] manifest 异常: %s" % payload.get("manifest_error"))
            if not dry:
                _write_last_run(ran_at, 1, alerted, skipped)
            return 1

        today = now.strftime("%Y-%m-%d")
        for entry in _anomalies(payload):
            name = entry["name"]
            code = _error_code_of(entry)
            if throttled(name, code, now):
                print("[exwd] 节流跳过：%s code=%s（24h 内已响）" % (name, code))
                skipped.append({"name": name, "error_code": code})
                continue
            if dry:
                print("[dry-run] 将推送/开单：%s code=%s status=%s"
                      % (name, code, entry["status"]))
                alerted.append({"name": name, "error_code": code,
                                "pushed": None, "ticket": None})
                continue
            # ① 自动开单（fail-open：异常不中断推送）
            body = _fmt_alert(entry, "见飞书 P0 卡（开单随附）")
            tid, note = open_ticket(name, body, today)
            # ② 推飞书 P0 卡
            text = _fmt_alert(entry, "%s：%s" % (
                note, tid or "开单失败请人工补单"))
            pushed = push_feishu(text)
            print("[exwd] %s code=%s → pushed=%s ticket=%s (%s)"
                  % (name, code, pushed, tid, note))
            if pushed:
                _mark_alerted(name, code, tid, now)
            else:
                # 推送失败不写节流状态（下次巡检重试响），但 exit 1 可见
                exit_code = 1
            alerted.append({"name": name, "error_code": code,
                            "pushed": pushed, "ticket": tid})
        if not dry:
            _write_last_run(ran_at, exit_code, alerted, skipped)
        else:
            print("[dry-run] 心跳/节流状态未写（零副作用），本轮汇总："
                  "将响 %d · 节流跳过 %d" % (len(alerted), len(skipped)))
        return exit_code
    except Exception as e:  # noqa: BLE001  自身异常 → exit 1（N1 可探）
        print("[exwd] 巡检自身异常: %s" % e)
        try:
            if not dry:
                _write_last_run(ran_at, 1, [{"error": str(e)}], skipped)
        except OSError:
            pass
        return 1


def _write_last_run(ran_at, exit_code, alerted, skipped):
    """写 last_run.json（N1 v1.9 第(9)项新鲜度检查的数据面）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "last_run.json", "w", encoding="utf-8") as f:
        json.dump({"ran_at": ran_at, "exit": exit_code,
                   "alerted": alerted, "skipped_throttle": skipped},
                  f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
