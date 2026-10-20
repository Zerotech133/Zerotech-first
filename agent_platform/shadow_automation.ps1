# Agent platform shadow automation (K-STOCK-004 blueprint v2 / K-ORG-001 sec.4)
# Mode 'run'  : weekdays 17:10 - run_shadow.py -> data/shadow/shadow_ledger.jsonl
#               (idempotent per day+symbol; failed rows -> P0 feishu alert inside)
# Mode 'push' : weekdays 17:35 - a0_chief.py --push (daily report to Feishu)
# Mode 'eval' : fridays  17:45 - eval_shadow.py -> data/shadow/eval_last_run.log
#               (three-criteria verdict J1/J2/J3; P2 automation, 10-17)
# ASCII-only by design (scheduled-task Chinese-path judgment: use C:\aios junction).
param([Parameter(Mandatory=$true)][ValidateSet('run','push','eval')][string]$Mode)
$ErrorActionPreference = 'Continue'
$here = 'C:\aios\agent_platform'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$py = 'C:\Program Files\Python311\python.exe'
if ($Mode -eq 'run') {
    # cmd /c byte-level redirect — 与 eval 分支同款形态。理由(2026-10-18 形态探针):
    # PS 5.1 的 `1>` 会把 native stdout 按 GBK 解码再以 UTF-16LE 重写, 实测复刻
    # 形态产出的日志前 3 字节 = FF FE(BOM), 中文全乱(`中文测试` -> `��-N�eKm`),
    # 退出码虽正确透传但日志失去排障价值。cmd /c 形态实测: 退出码 3 正常透传,
    # 日志首字节 E4 B8 AD = UTF-8「中」, 中文完好。
    # 决策依据不是探针本身而是**生产实证**: AgentShadowEvalWeekly 用同款 cmd /c
    # 真实跑过(LastTaskResult=0), eval_last_run.log 为纯 UTF-8 且中文正常;
    # 而 `1>` 形态在生产从未跑过(AgentShadowDaily LastTaskResult=267011 尚未运行)。
    # 注意: Arguments 不变(仍 -Mode run), 无需重注册计划任务, 触发时间面零影响。
    # 日志句柄自撞修复（2026-10-20；K-OPS-20261019-SHADOWFIX 根因收口，老田批准）:
    # 原形态外层 `> data\shadow\last_run.log` 以**覆盖写**持有该文件的写句柄
    # （共享模式仅 FILE_SHARE_READ，句柄全进程期不释放），而 run_shadow.py:427
    # 启动后又要以**追加**方式 open 同一文件 → 共享冲突，Win32 状态码
    # 32(ERROR_SHARING_VIOLATION)，Python 侧表现为 PermissionError [Errno 13]
    # —— 即 10-19 17:10 首跑 5 标的零落账的全部根因。10-20 之所以"成功"只是
    # Python 侧 catch 掉冲突并沿用既有 stdout/stderr（run_shadow.py:428-430），
    # 冲突一天都没消失 —— 本改法是**消除冲突**，不是续命。
    # 两条子改动必须同时生效（缺一即复发/双写）:
    #   1) 外层重定向改指**另一个**文件 last_run_wrapper.log → last_run.log
    #      转由 Python 侧单独持有，日志**单一写入者**（外层不再持其句柄）；
    #   2) 新目标改用**追加**语义 >> → 每日不再截断，历史事故 traceback 不被
    #      物理覆盖（现状 10-19 的 469 字节 traceback 已被 10-20 那次跑覆盖）。
    # 退出码语义不变：仍 exit $LASTEXITCODE（计划任务结果码不得失真）。
    $log = Join-Path $here 'data\shadow\last_run_wrapper.log'
    cmd /c "`"$py`" -X utf8 `"$here\run_shadow.py`" >> `"$log`" 2>&1"
    exit $LASTEXITCODE
}
elseif ($Mode -eq 'eval') {
    # cmd /c byte-level redirect: PS 5.1 native-stdout redirect mangles UTF-8
    # Chinese through GBK decode + UTF-16 rewrite (19:51 log mojibake verdict).
    $log = Join-Path $here 'data\shadow\eval_last_run.log'
    # M5-P2 demo guard: persist requires explicit --persist-root (default=deny)
    cmd /c "`"$py`" -X utf8 `"$here\eval_shadow.py`" --persist-root `"$here`" > `"$log`" 2>&1"
    exit $LASTEXITCODE
}
else {
    # cmd /c byte-level redirect — 与 run/eval 两分支同款形态(2026-10-18 形态探针):
    # PS 5.1 的 `1>` 把 native stdout 按 GBK 解码后以 UTF-16LE 重写(首 3 字节
    # FF FE), 中文全乱。本分支是唯一记录飞书 SEND_OK/SEND_FAIL 的日志面,
    # 乱码即失去排障价值。AgentChiefPush 从未运行过(LastTaskResult=267011),
    # 周一 17:35 是首跑, 不拿首跑去赌未验证形态。
    $log = Join-Path $here 'data\shadow\chief_last_run.log'
    cmd /c "`"$py`" -X utf8 `"$here\a0_chief.py`" --push > `"$log`" 2>&1"
    exit $LASTEXITCODE
}
