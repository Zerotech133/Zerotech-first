# -*- coding: utf-8 -*-
r"""alert_fail 发送结果可观测化包装器（E1）。

背景三实伤：
    1. launcher 直调 quant_backtest/alert_fail.py 且 1>$null 2>$null，
       SEND 结果全黑洞——本包装器承接调用，结果落盘 logs/alert_send.log。
    2. alert_fail.py 写 outbox/pending_alert_<task>.txt 后发送成功也无人
       流转，16 个 pending 永久堆积——本包装器在发送成功后同目录原子改名
       为 .sent（保留供审计，本版不做自动删除）。
    3. alert_fail.py 按 utf-8 读 C:\Temp\*_err.log，而 PS 5.1 `2>` 重定向
       产物是 UTF-16LE，日志尾全是乱码——本包装器先把 err log BOM 嗅探
       解码、另存 UTF-8 副本再交给 alert_fail.py，不改其一行代码。

红线：quant_backtest 域零修改，仅 subprocess 调用；本文件只在
news_collector 域内活动。包装器自身任何异常都不外抛（告警观测层
不能反噬任务 rc），异常也记入 alert_send.log。
"""

import os
import subprocess
import sys
import tempfile
import time

from alert_io import append_ascii_log, sniff_encoding, sniff_read_text
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(HERE, "outbox")
SEND_LOG = os.path.join(HERE, "logs", "alert_send.log")
ALERT_FAIL_PY = os.path.abspath(
    os.path.join(HERE, "..", "quant_backtest", "alert_fail.py")
)


def pending_path(task):
    """alert_fail.py 的 pending 落点（与其同名同目录）。"""
    return os.path.join(OUTBOX, "pending_alert_%s.txt" % task)


def normalize_err_log(log_path):
    """把非 UTF-8 的 err log 另存 UTF-8 副本，返回可安全交给
    alert_fail.py（内部 utf-8 读取）的路径；UTF-8 原样返回，缺失返回 ''。"""
    if not log_path or not os.path.isfile(log_path):
        return ""
    enc = sniff_encoding(log_path)
    if enc == "utf-8":
        return log_path
    text = sniff_read_text(log_path)
    os.makedirs(OUTBOX, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUTBOX, prefix="norm_err_", suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return tmp


def parse_send_result(stdout, returncode):
    """从 alert_fail.py stdout 判定发送结果，返回 (ok, reason)。"""
    out = (stdout or "").strip()
    if "SEND_OK" in out:
        return True, "SEND_OK"
    for line in out.splitlines():
        if "SEND_FAIL" in line:
            return False, line.strip()[:120]
        if "alert send error" in line:
            return False, line.strip()[:120]
    return False, "no_send_marker rc=%d out=%s" % (returncode, out[:60])


def _log_field(value, limit=240):
    """把结构化字段压成单行，避免 ``|``/换行破坏日志解析。"""
    text = str(value if value is not None else "")
    text = text.replace("|", "/").replace("\r", " ").replace("\n", " ")
    return text[:limit] or "-"


def exception_class(task_rc, ok, reason, wrapper_error=False, rename_error=False):
    """根据任务返回码和发送链结果给出稳定的异常分类。"""
    if wrapper_error:
        return "wrapper_exception"
    if rename_error:
        return "pending_rename_failure"
    try:
        task_failed = int(str(task_rc)) != 0
    except (TypeError, ValueError):
        task_failed = str(task_rc or "?") not in ("0", "?")
    if task_failed:
        return "task_failure"
    if ok:
        return "none"
    if "SEND_FAIL" in (reason or "") or "alert send error" in (reason or ""):
        return "send_failure"
    return "send_unknown"


def log_send(
    task,
    channel,
    ok,
    reason,
    task_rc="?",
    log_path="",
    result_summary="",
    exception_kind="none",
):
    """单行 ASCII 状态日志，保留旧字段并补齐任务三联证据。"""
    append_ascii_log(
        SEND_LOG,
        "%s | task=%s | channel=%s | result=%s | reason=%s"
        " | task_rc=%s | log_path=%s | result_summary=%s"
        " | exception_class=%s"
        % (
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            _log_field(task, 80),
            _log_field(channel, 40),
            "OK" if ok else "FAIL",
            _log_field(reason),
            _log_field(task_rc, 32),
            _log_field(log_path, 260),
            _log_field(result_summary or reason),
            _log_field(exception_kind, 48),
        ),
    )


def handle_send_result(
    pending,
    task,
    ok,
    reason,
    task_rc="?",
    log_path="",
    result_summary="",
    exception_kind="none",
):
    """发送结果落盘 + 成功时 pending→.sent 流转。返回动作标记：
    sent / sent_existing（.sent 已存在走时间戳兜底）/ no_pending / kept。"""
    log_send(
        task,
        "feishu",
        ok,
        reason,
        task_rc=task_rc,
        log_path=log_path,
        result_summary=result_summary,
        exception_kind=exception_kind,
    )
    if not ok:
        return "kept"
    if not os.path.isfile(pending):
        return "no_pending"
    dst = pending + ".sent"
    if os.path.exists(dst):  # Windows rename 不覆盖：时间戳兜底保审计
        dst = "%s.sent.%s" % (pending, time.strftime("%Y%m%d%H%M%S"))
    os.rename(pending, dst)  # 同目录改名，原子
    return "sent_existing" if ".sent." in os.path.basename(dst) else "sent"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    task = argv[0] if len(argv) > 0 else "unknown"
    code = argv[1] if len(argv) > 1 else "?"
    raw_log = argv[2] if len(argv) > 2 else ""
    norm_log = ""
    norm_is_temp = False  # P3-a：提前初始化，normalize 抛异常时 finally 不 NameError
    wrapper_error = False
    try:
        norm_log = normalize_err_log(raw_log)
        # P3-a 2026-11-12：只清理自建规范化副本；原 err 日志恰为 UTF-8 时
        # normalize 原样返回原路径，此前 finally-unlink 会把原始证据一并删掉
        # （11-12 kg_kg_evt_sync_err.log 消失判例）。回滚：恢复
        # C:\项目\backups\alert_fail_wrap.py.bak-20261112-P123
        norm_is_temp = bool(norm_log) and (norm_log != raw_log)
        r = subprocess.run(
            [sys.executable, "-X", "utf8", ALERT_FAIL_PY, task, code, norm_log],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (r.stdout or "") + (
            ("\n" + r.stderr.strip()) if (r.stderr or "").strip() else ""
        )
        ok, reason = parse_send_result(out, r.returncode)
    except Exception as e:  # 观测层自身故障也要留痕
        ok, reason = False, "wrap_err " + str(e)[:100]
        wrapper_error = True
    finally:
        if norm_is_temp and os.path.isfile(norm_log):
            try:
                os.unlink(norm_log)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/alert_fail_wrap.py", exc)
    try:
        action = handle_send_result(
            pending_path(task),
            task,
            ok,
            reason,
            task_rc=code,
            log_path=raw_log,
            result_summary=reason,
            exception_kind=exception_class(
                code, ok, reason, wrapper_error=wrapper_error
            ),
        )
    except Exception as e:  # 改名失败不吞发送事实
        log_send(
            task,
            "feishu",
            ok,
            reason + " rename_err " + str(e)[:60],
            task_rc=code,
            log_path=raw_log,
            result_summary=reason,
            exception_kind=exception_class(code, ok, reason, rename_error=True),
        )
        action = "rename_err"
    print(
        "ALERT_WRAP_%s action=%s reason=%s"
        % ("OK" if ok else "FAIL", action, reason[:80])
    )
    return 0  # 观测层永远不反噬调用方 rc


if __name__ == "__main__":
    sys.exit(main())
