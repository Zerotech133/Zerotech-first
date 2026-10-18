# -*- coding: utf-8 -*-
"""Hermes8787-KillSvc Gate 0 前置逻辑层单测（R7）。

覆盖：
  - 七条白名单逐条正反例（>= 14 例）
  - 三态判定 3 例
  - 防抖六条 6 例（每条一个用例，内含正反断言）
  - 演练脚本 killsvc_drill.ps1 PowerShell 解析器语法校验

零真实探测、零进程操作、零提权。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import killsvc_guard as g  # noqa: E402


# ---------------------------------------------------------------------------
# 构造工厂
# ---------------------------------------------------------------------------

def make_target(**over):
    """真 8787 特征样本（构造 dict，非真探测；§1/§2.2.2/§2.2.3 口径）。"""
    t = {
        "pid": 49536,
        "process_name": "pythonw.exe",
        "command_line": r"pythonw.exe C:\项目\老田的AI助手\Hermes\hermes-webui-next\server.py --port 8787",
        "session_id": 0,
        "owner": "SYSTEM",
        "port": 8787,
        "listening": True,
        "probe_failures": 3,
    }
    t.update(over)
    return t


def make_request(**over):
    r = {
        "requester": "guard30_chain",
        "nonce": "n-20261018-0001",
        "issued_at": time.time(),
        "target": make_target(),
        "reason": "state_b_http_probe_3_fail",
        "audit_log_path": g.KILL_SVC_LOG_PATH,
    }
    r.update(over)
    return r


def check_ok(req, **kw):
    return g.check(req, **kw).allow


def deny_rule(req, **kw):
    v = g.check(req, **kw)
    assert not v.allow, "应拒绝却放行: %s" % v
    return v.rule


# ---------------------------------------------------------------------------
# 七条白名单逐条正反例
# ---------------------------------------------------------------------------

# --- W1 请求者身份 ---
def test_w1_allow_whitelisted_requesters():
    for who in ("guard30_chain", "n1_selfheal", "admin_manual"):
        assert check_ok(make_request(requester=who)), who


def test_w1_deny_unknown_requester():
    assert deny_rule(make_request(requester="random_script")) == "W1"


def test_w1_deny_missing_requester():
    req = make_request()
    del req["requester"]
    assert deny_rule(req) == "W1"


# --- W2 目标进程特征三重匹配 ---
def test_w2_allow_true_8787_signature():
    assert check_ok(make_request())


def test_w2_allow_python_host_rollback_base():
    # §1 实测宿主为 python.exe；命令行取回滚基 hermes-webui\server.py 亦应放行
    t = make_target(process_name="python.exe",
                    command_line=r"python.exe C:\项目\老田的AI助手\Hermes\hermes-webui\server.py")
    assert check_ok(make_request(target=t))


def test_w2_deny_cmdline_mismatch():
    # §4.2 反向断言形态：端口对但命令行不含 server.py
    t = make_target(command_line=r"python.exe -m http.server 8787")
    assert deny_rule(make_request(target=t)) == "W2"


def test_w2_deny_wrong_port():
    t = make_target(port=18787)
    assert deny_rule(make_request(target=t)) == "W2"


def test_w2_deny_foreign_process_name():
    t = make_target(process_name="cmd.exe")
    assert deny_rule(make_request(target=t)) == "W2"


def test_w2_deny_missing_target():
    assert deny_rule(make_request(target=None)) == "W2"


# --- W3 请求频率防抖冷却窗 ---
def test_w3_allow_after_cooldown():
    now = time.time()
    assert check_ok(make_request(), now=now, last_trigger_ts=now - g.COOLDOWN_SECONDS)


def test_w3_deny_within_cooldown():
    now = time.time()
    assert deny_rule(make_request(), now=now, last_trigger_ts=now - 60) == "W3"


# --- W4 参数面=零参数强制 ---
def test_w4_allow_clean_schema():
    assert check_ok(make_request())


def test_w4_deny_args_key():
    # 模拟携带 taskkill 参数的注入面请求
    assert deny_rule(make_request(args=["/F", "/PID", "49536"])) == "W4"


def test_w4_deny_unknown_key():
    assert deny_rule(make_request(command="taskkill /F /PID 49536")) == "W4"


# --- W5 时间窗（PT5M）---
def test_w5_allow_fresh_request():
    assert check_ok(make_request(), now=time.time())


def test_w5_deny_stale_request():
    now = time.time()
    assert deny_rule(make_request(issued_at=now - 400), now=now) == "W5"


def test_w5_deny_missing_issued_at():
    req = make_request()
    del req["issued_at"]
    assert deny_rule(req) == "W5"


# --- W6 指纹/nonce 防重放 ---
def test_w6_allow_fresh_nonce():
    assert check_ok(make_request(nonce="n-abc-1"), seen_nonces={"n-xyz-0"})


def test_w6_deny_replayed_nonce():
    assert deny_rule(make_request(nonce="n-abc-1"), seen_nonces={"n-abc-1"}) == "W6"


def test_w6_deny_empty_nonce():
    assert deny_rule(make_request(nonce="")) == "W6"


# --- W7 审计落账要求 ---
def test_w7_allow_with_reason_and_fixed_log():
    assert check_ok(make_request())


def test_w7_deny_wrong_log_path():
    assert deny_rule(make_request(audit_log_path=r"C:\Temp\my.log")) == "W7"


def test_w7_deny_empty_reason():
    assert deny_rule(make_request(reason="  ")) == "W7"


# ---------------------------------------------------------------------------
# 三态判定（§2.1 单任务覆盖三态）
# ---------------------------------------------------------------------------

def test_tri_state_not_found():
    assert g.tri_state({"listening": False, "pid": None}) == g.TRI_NOTFOUND
    assert g.tri_state({}) == g.TRI_NOTFOUND
    assert g.tri_state(None) == g.TRI_NOTFOUND


def test_tri_state_normal_user_session():
    t = make_target(session_id=1, owner=r"DESKTOP-ARF447U\t", probe_failures=0)
    assert g.tri_state(t) == g.TRI_NORMAL


def test_tri_state_zombie_system_session():
    t = make_target(session_id=0, owner="SYSTEM", probe_failures=3)
    assert g.tri_state(t) == g.TRI_ZOMBIE


# ---------------------------------------------------------------------------
# 防抖六条（§3.2 逐条）
# ---------------------------------------------------------------------------

def test_d1_both_conditions_required():
    # 双条件齐备才判僵死；TCP 通但 HTTP 不响应（探活失败）同样计入
    assert g.d1_zombie_declared(make_target(probe_failures=3)) is True
    # 仅进程存在探活正常 -> 不判僵死
    assert g.d1_zombie_declared(make_target(probe_failures=0)) is False
    # 半死（失败 2 次）未达阈值 -> 不判僵死
    assert g.d1_zombie_declared(make_target(probe_failures=2)) is False


def test_d2_single_condition_no_action():
    # 仅探测失败但端口无监听 -> 态 A，走 Guard30，不触发 KillSvc
    assert g.d2_no_kill_single_condition({"listening": False, "pid": None, "probe_failures": 3}) is True
    # 仅进程存在探活正常 -> 健康态零动作
    assert g.d2_no_kill_single_condition(make_target(probe_failures=0)) is True
    # 双条件齐备 -> 不属于零动作区
    assert g.d2_no_kill_single_condition(make_target(probe_failures=3)) is False


def test_d3_pre_kill_identity_recheck():
    assert g.d3_pre_kill_identity_ok(make_target()) is True
    assert g.d3_pre_kill_identity_ok(make_target(command_line=r"python.exe -m http.server 8787")) is False


def test_d4_cooldown_window():
    now = time.time()
    assert g.d4_cooldown_ok(now, None) is True
    assert g.d4_cooldown_ok(now, now - g.COOLDOWN_SECONDS - 1) is True
    assert g.d4_cooldown_ok(now, now - 60) is False


def test_d5_task_bounded_pt5m():
    assert g.d5_task_bounded(60) is True
    assert g.d5_task_bounded(g.TIME_WINDOW_SECONDS) is True
    assert g.d5_task_bounded(360) is False              # 超过 PT5M
    assert g.d5_task_bounded(60, concurrent_instances=2) is False  # IgnoreNew：并发堆积不放行


def test_d6_guard30_cadence():
    now = time.time()
    assert g.d6_guard30_cadence_ok(now, None) is True
    assert g.d6_guard30_cadence_ok(now, now - g.GUARD30_CADENCE_SECONDS) is True
    assert g.d6_guard30_cadence_ok(now, now - 600) is False  # 30min 伴生周期未错开


# ---------------------------------------------------------------------------
# 整链放行 + 演练脚本语法校验
# ---------------------------------------------------------------------------

def test_full_chain_allow():
    now = time.time()
    v = g.check(make_request(), now=now, last_trigger_ts=now - 3600,
                seen_nonces=set())
    assert v.allow and v.rule == "ALLOW"


def test_drill_ps1_parses_clean():
    """演练脚本须通过 PowerShell AST 解析器语法校验（node --check 不适用于 ps1）。"""
    ps1 = str(TOOLS / "killsvc_drill.ps1")
    assert Path(ps1).is_file()
    # 中文路径经命令行传参会被 GBK 控制台弄乱，故经环境变量传入
    inner = (
        "$t=$null;$e=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:KILLSVC_PS1,[ref]$t,[ref]$e)|Out-Null;"
        "if($e -and $e.Count -gt 0){$e|ForEach-Object{Write-Output $_.Message};exit 1}else{exit 0}"
    )
    env = dict(os.environ, KILLSVC_PS1=ps1)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", inner],
        capture_output=True, text=True, errors="replace", timeout=120, env=env,
    )
    assert r.returncode == 0, "killsvc_drill.ps1 语法错误:\n%s\n%s" % (r.stdout, r.stderr)
