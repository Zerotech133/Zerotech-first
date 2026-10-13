# -*- coding: utf-8 -*-
"""K-STOCK-337 · 回测跑批子进程 watchdog（静默超时杀树+自动重试）。

背景（335 号观察项）：
    _327_paired.py 跑三臂时，chan_base 子进程曾在「全市场数据装载 3%」处
    无声中断——stdout 无 traceback、无错误输出、进程退出 rc=0（疑似解释器级
    异常终止），引擎未写产物 json。单独重跑 34s 成功，偶发、机制未知。
    常规 subprocess.run 无法防"进程不退出也不吐日志"的挂死，也无法重试。

方案：
    run_guarded() 用 Popen 启子进程（stdout/stderr 落同一日志文件），尾随
    监测日志文件字节数活性：
    - 若 quiet_secs 秒内日志无新增字节且进程未退出 → 判定静默挂死；
    - taskkill /F /T（经 python subprocess 调，bash 会吞 / 开关）杀整棵树；
    - 自动重试一次（attempts 默认 2）；仍失败则返回哨兵 rc，交上层按原
      FAIL 判定（rc!=0 或产物缺失）收口。

用法：
    from _run_guard import run_guarded, KILLED_RC
    rc, used = run_guarded([PY, "-u", engine, "--d0", d0, ...],
                           log_fh=log, cwd=QBT, label=jid)

注意：
    - Windows 环境；守护日志行同样写入 log_fh（与子进程输出同流，可追溯）。
    - 不改变上层幂等/守卫语义：本模块只负责"跑+盯+杀+重试"，产物存在性
      判定仍由调用方完成。
    - os.fstat 直接读共享句柄的文件尾，无需子进程 flush（python -u 无缓冲）。
"""
import os
import subprocess
import time

KILLED_RC = -137          # 被 watchdog 杀树的哨兵退出码（沿用 SIGKILL 语义）
DEFAULT_QUIET_SECS = 300.0  # 静默超时默认 300s（335 实测全市场装载远短于此）


def _kill_tree(pid):
    """杀整棵进程树。taskkill 必须经 python subprocess 调（bash 会吞 / 开关）。"""
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        # taskkill 输出为 GBK（中文系统），bytes 捕获+容错解码，防 reader 线程炸
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        return r.returncode, out, err
    except OSError as e:
        return 1, "", "taskkill spawn error: %s" % e


def run_guarded(cmd, log_fh, cwd=None, quiet_secs=DEFAULT_QUIET_SECS,
                poll_secs=2.0, label="", attempts=2):
    """带 watchdog 的子进程执行。

    参数：
        cmd        子进程命令行（list，含解释器）。
        log_fh     已打开的日志文件对象（子进程 stdout/stderr 与守护日志共用）。
        cwd        子进程工作目录。
        quiet_secs 日志无新增字节判挂死的阈值秒数（默认 300，可参数化）。
        poll_secs  活性轮询间隔秒数。
        label      守护日志行前缀（一般是 job_id）。
        attempts   总尝试次数（默认 2 = 首跑 + 自动重试一次）。

    返回：
        (rc, used_attempts)
        rc = 最后一次子进程退出码；若最终一次是被 watchdog 杀掉则为 KILLED_RC。
    """
    tag = "[%s run_guard]" % label if label else "[run_guard]"
    fd = log_fh.fileno()
    rc, used = None, 0
    killed_final = False
    for attempt in range(1, attempts + 1):
        used = attempt
        log_fh.write("%s attempt %d/%d start: %s\n"
                     % (tag, attempt, attempts, " ".join(cmd)))
        log_fh.flush()
        t0 = time.time()
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                                cwd=cwd)
        last_size = os.fstat(fd).st_size
        last_progress = time.time()
        killed = False
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            time.sleep(poll_secs)
            size = os.fstat(fd).st_size
            if size > last_size:
                last_size = size
                last_progress = time.time()
            elif time.time() - last_progress >= quiet_secs:
                silent = time.time() - last_progress
                tk_rc, tk_out, tk_err = _kill_tree(proc.pid)
                if tk_rc != 0:
                    # K-STOCK-338: taskkill 失败通常是超时判定与子进程自行退出撞上
                    # （竞态窗口：poll 之后 sleep 期间进程退出·taskkill 报"进程不存在"）
                    # ——补杀一次再复核。
                    tk_rc, tk_out, tk_err = _kill_tree(proc.pid)
                    if proc.poll() is not None:
                        # 子进程其实已自行退出：不吞真实 rc、不误判为被杀。
                        # 真实退出码透传上层（不重试·保持既有语义）。
                        rc = proc.returncode
                        log_fh.write(
                            "%s attempt %d 杀灭窗口内子进程已自行退出 rc=%s"
                            " -> 真实 rc 透传上层·不重试 (tk_rc=%s %s%s)\n"
                            % (tag, attempt, rc, tk_rc, tk_out, tk_err))
                        log_fh.flush()
                        return rc, used
                if tk_rc != 0 and proc.poll() is None:
                    # K-STOCK-338: 补杀仍失败且子进程仍挂死——绝不重试（重试会
                    # 造成新旧两份引擎并发跑·并发写同一产物 json = 数据污染），
                    # 返回哨兵交上层 FAIL 收口；挂死进程留给人工 taskkill。
                    log_fh.write(
                        "%s WATCHDOG 杀灭失败且子进程仍存活 pid=%s tk_rc=%s %s%s"
                        " -> 放弃重试防并发双跑·需人工 taskkill /F /T /PID %s\n"
                        % (tag, proc.pid, tk_rc, tk_out, tk_err, proc.pid))
                    log_fh.flush()
                    rc = KILLED_RC
                    killed = True
                    break
                proc.wait()
                log_fh.write(
                    "%s WATCHDOG 触发: attempt %d 静默 %.0fs(阈值 %.0fs) 无日志增长"
                    " -> taskkill /F /T pid=%s tk_rc=%s %s%s (elapsed %.0fs)\n"
                    % (tag, attempt, silent, quiet_secs, proc.pid, tk_rc,
                       tk_out, tk_err, time.time() - t0))
                log_fh.flush()
                killed = True
                break
        if not killed:
            # 正常退出：rc=0 直接过；rc!=0 是子进程自己的失败语义，立即透传
            # 给上层原 FAIL 判定收口，不重试（保持既有语义）。
            log_fh.write("%s attempt %d/%d exit rc=%s (%.0fs)%s\n"
                         % (tag, attempt, attempts, rc, time.time() - t0,
                            "" if rc == 0 else " (非守护杀灭·透传上层·不重试)"))
            log_fh.flush()
            return rc, used
        if killed:
            rc = KILLED_RC
        log_fh.write("%s attempt %d/%d FAIL rc=%s (%.0fs)%s\n"
                     % (tag, attempt, attempts, rc, time.time() - t0,
                        " -> 自动重试" if attempt < attempts else ""))
        log_fh.flush()
    return rc, used
