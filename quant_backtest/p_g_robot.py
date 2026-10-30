# -*- coding: utf-8 -*-
"""P-G 飞书机器人查数（event consume 实时模式）：群 @app bot 中文问句 -> multi_agent 答案。

口径（2026-09-28 B 方案，从轮询模式切换）：
  · 背景：tre 环境 lark-cli 仅注入 user access token（无 app secret），
    strict_mode 下 bot 身份不可用。已设机器级 LARKSUITE_CLI_APP_SECRET，
    脚本启动时换 TAT（POST /auth/v3/tenant_access_token/internal），
    设全 4 个 bus daemon 环境变量后 bot 身份可用，event consume 走通。
  · 凭据（W2 出码，2026-10-04）：APP_ID/APP_SECRET 只从环境变量
    LARKSUITE_CLI_APP_ID / LARKSUITE_CLI_APP_SECRET 读取（机器级已配），
    缺失即 RuntimeError fail-closed；源码零凭据字面量。
  · 启动自愈（2026-10-07 自产线分支回移）：TAT 初始化 10×60s 重试，
    覆盖开机早期本机代理（127.0.0.1:7890）未就绪的 WinError 10061。
  · 事件流：lark-cli event consume im.message.receive_v1 --as bot 子进程
    stdout NDJSON 一行一条，blocking 读取。子进程崩溃 5s 重启。
  · 触发：event mentions 含 app bot open_id（ou_6bbb99fcfa7a5b1049f0cf19f64462ae，2026-10-07 以本 app 身份发 @ 实证回绑）
    的群消息 -> 提取问句（content 已预渲染为"@机器人名 问句"，去@名字）
  · 查数：单 worker 线程池异步调 multi_agent.answer(q)
    （Ollama 单卡并发 OOM 风险；multi_agent 自带当日缓存命中秒回）
  · 回复：lark-cli im +messages-send --chat-id <oc_xxx> --as bot
    （subprocess list 传参避 PowerShell 引号吞噬；4 环境变量已设）
  · 防滥用：message_id 幂等去重 + 每 sender_id 每 60s 限 1 次
  · TAT 刷新：每 100 分钟后台线程换一次（飞书 TAT 有效期 ~7200s）
  · 零框架纯 python，LLM 全本地（multi_agent 封装），不改信号/推送口径

用法：
  python p_g_robot.py --chat-id oc_xxx               # 默认 @bot 触发
  python p_g_robot.py --chat-id oc_xxx,oc_yyy        # 多群（白名单过滤）
  python p_g_robot.py --chat-id oc_xxx --dry         # 不真正回复（联调）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request
import logging
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 复用 send_feishu 的 lark-cli 定位
# K-PUSH-001 P0：旧机 Administrator 硬编码换机断链 → expanduser 动态推导
LARK_ROOT = os.path.join(
    os.path.expanduser("~"), ".trae-cn", "plugins",
    "trae-remote-official", "lark")

_APP_CREDS = None


def app_creds():
    """飞书应用凭据（fail-closed，进程内缓存一次）：只从环境变量读取。

    W2 凭据出码（2026-10-04）：原硬编码 APP_ID/APP_SECRET 已从源码
    清除（泄露面见 docs/planning/credential-inventory-2026-09-30.md #2，
    轮换走 commercial-remediation-runbook）。任一变量缺失/空白即抛
    RuntimeError("feishu_cred_missing:<VAR,...>")，绝不回退默认值。
    错误信息只含变量名，不含取值。
    """
    global _APP_CREDS
    if _APP_CREDS is None:
        app_id = os.environ.get("LARKSUITE_CLI_APP_ID", "").strip()
        app_secret = os.environ.get("LARKSUITE_CLI_APP_SECRET", "").strip()
        missing = [name for name, value in (
            ("LARKSUITE_CLI_APP_ID", app_id),
            ("LARKSUITE_CLI_APP_SECRET", app_secret)) if not value]
        if missing:
            raise RuntimeError("feishu_cred_missing:" + ",".join(missing))
        _APP_CREDS = (app_id, app_secret)
    return _APP_CREDS

BOT_OPEN_ID = os.environ.get("PG_BOT_OPEN_ID", "ou_0b743476bdd6657918407e86a1fd1a23")   # 默认=本 app（cli_aa469e12a8b85bcd）视角 bot open_id（2026-10-13 /bot/v3/info 实证回绑）；open_id 为 app 维度，换绑 app 时设 PG_BOT_OPEN_ID 覆盖，勿跨用他 app 所得 id；注意任务计划程序宿主缓存旧环境变量，故代码默认值必须同步换新

# 防滥用
RATE_LIMIT_S = 60      # 每 sender_id 每 N 秒 1 次
DEDUP_MAX = 500        # message_id LRU 上限
TAT_REFRESH_S = 6000   # TAT 刷新间隔（100 分钟，TAT 有效期 ~7200s）
CONSUME_RESTART_S = 5  # 子进程崩溃后重启等待

# 事件流
EVENT_KEY = "im.message.receive_v1"

# TAT 共享状态（_tat_lock 保护 _cur_tat）
_tat_lock = threading.Lock()
_cur_tat = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/p_g_robot.py", exc)

# 文件日志（SYSTEM 任务无控制台，stdout 丢失）
_LOG_FILE = r"C:\Temp\pg_robot_system.log"
_log_lock = threading.Lock()


def _log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with _log_lock:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/p_g_robot.py", exc)


def find_lark_cli():
    # K-STOCK-396 exe 解耦：仓库 bin/ 固定副本优先（插件目录升级/清除
    # 即断链，2026-10-13 巡检判例），插件目录扫描降为回退。
    _bin = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    if not os.path.isdir(LARK_ROOT):
        return None
    for d in sorted(os.listdir(LARK_ROOT), reverse=True):
        p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
        if os.path.isfile(p):
            return p
    return None


# ── TAT 管理 ───────────────────────────────────────────────
def _get_tat():
    """换 tenant_access_token。POST /auth/v3/tenant_access_token/internal。"""
    app_id, app_secret = app_creds()
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                  data=body, headers={"Content-Type": "application/json"})
    d = json.loads(safe_urlopen(req, timeout=15).read().decode("utf-8"))
    # 显式校验（不用 assert：python -O 下 assert 失效属 fail-open）
    if d.get("code") != 0:
        raise RuntimeError("tat_fail: %s"
                           % json.dumps(d, ensure_ascii=False)[:200])
    return d["tenant_access_token"]


def _refresh_tat_loop():
    """后台线程：每 TAT_REFRESH_S 秒刷新 TAT。"""
    global _cur_tat
    while True:
        try:
            tat = _get_tat()
            with _tat_lock:
                _cur_tat = tat
            _log("TAT refreshed len=%d" % len(tat))
        except Exception as e:
            _log("TAT refresh err: %s" % e)
        time.sleep(TAT_REFRESH_S)


def _get_cur_tat():
    with _tat_lock:
        return _cur_tat


def _bot_env():
    """event consume 子进程环境（4 环境变量全设，bot 身份）。"""
    app_id, app_secret = app_creds()
    env = dict(os.environ)
    env["LARKSUITE_CLI_APP_ID"] = app_id
    env["LARKSUITE_CLI_APP_SECRET"] = app_secret
    env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = _get_cur_tat() or ""
    env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = ""
    return env


# ── event consume 子进程 ────────────────────────────────────
def _start_consume(exe):
    """起 lark-cli event consume 子进程。返回 Popen。
    stdin=PIPE 必须显式 hold 住——lark-cli 把 stdin EOF 当退出信号
    （"wired for AI subprocess callers"），不 hold 会导致 consume 立即退出。"""
    env = _bot_env()
    return subprocess.Popen(
        [exe, "event", "consume", EVENT_KEY, "--as", "bot"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, encoding="utf-8", errors="replace", bufsize=1)


def _stderr_drain(p):
    """后台线程：持续读 stderr 防止管道缓冲填满阻塞 lark-cli。"""
    while True:
        try:
            line = p.stderr.readline()
            if not line:
                break
            line = line.strip()
            if line:
                _log("[stderr] %s" % line)
        except Exception:
            break


def _wait_ready(p, timeout=20):
    """等 stderr 出现 [event] ready marker。返回 True/False。
    ready 后启动 stderr drain 线程防管道阻塞。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = p.stderr.readline()
        if not line:
            return False
        line = line.strip()
        if line:
            _log("[stderr] %s" % line)
        if "[event] ready" in line:
            # 启动 stderr drain 线程，防止 stderr 管道缓冲填满阻塞 lark-cli
            threading.Thread(target=_stderr_drain, args=(p,), daemon=True).start()
            return True
        if line.startswith("{"):
            try:
                d = json.loads(line)
                if not d.get("ok"):
                    _log("consume init fail: %s" %
                         json.dumps(d, ensure_ascii=False)[:400])
                    return False
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/p_g_robot.py", exc)
    return False


# ── 回复消息 ──────────────────────────────────────────────
def _send_text(exe, chat_id, text, dry=False):
    """bot 身份发文本消息到 chat_id。subprocess list 传参避引号吞噬。"""
    if dry:
        _log("[dry] -> chat=%s: %s" % (chat_id, text[:120]))
        return True
    try:
        r = subprocess.run(
            [exe, "im", "+messages-send",
             "--chat-id", chat_id, "--text", text, "--as", "bot"],
            capture_output=True, text=True, encoding="utf-8",
            env=_bot_env(), timeout=30)
        try:
            ok = json.loads(r.stdout).get("ok") is True
        except Exception:
            ok = False
        if not ok:
            _log("SEND_FAIL out=%s err=%s" %
                 ((r.stdout or "")[:200], (r.stderr or "")[:200]))
        else:
            _log("SEND_OK chat=%s" % chat_id)
        return ok
    except Exception as e:
        _log("SEND_ERR %s" % e)
        return False


def _format_reply(d):
    """multi_agent 返回 -> 飞书回复文本。[AI] 前缀用于答案标识。"""
    ans = d.get("answer") or "（查数失败·无答案）"
    specs = [s["specialist"] for s in d.get("specialists") or []]
    lines = ["[AI] " + ans.strip(), ""]
    if specs:
        lines.append("来源：multi_agent（%s）" % " + ".join(specs))
    extra = []
    if d.get("cached"):
        extra.append("缓存命中")
    extra.append("耗时%ss" % d.get("elapsed_s", "?"))
    lines.append(" | ".join(extra))
    return "\n".join(lines)


# ── 查数 worker ────────────────────────────────────────────
def _worker(q, chat_id, exe, dry):
    """异步查数 + 回复。"""
    t0 = time.time()
    _log("query start: %s" % q[:80])
    try:
        from multi_agent import answer
        d = answer(q)
    except Exception as e:
        _log("query err: %s" % e)
        _send_text(exe, chat_id, "查数异常：%s" % str(e)[:200], dry=dry)
        return
    _log("query done in %.1fs" % (time.time() - t0))
    _send_text(exe, chat_id, _format_reply(d), dry=dry)


# ── 事件解析 ───────────────────────────────────────────────
_AT_PLACEHOLDER = re.compile(r"@_user_\d+\s*")            # 客户端 @本app机器人 脱敏占位
_AT_RAW_TAG = re.compile(r"<at\s+user_id=[^>]*>[^<]*</at>\s*")   # API 直发 @ 原文标签


def _strip_at(d):
    """content 去全部 @ 残留（mentions 名字 / @_user_N 占位 / <at> 原文），返回净问句文本。"""
    content = d.get("content") or ""
    for m in (d.get("mentions") or []):
        name = m.get("name")
        if name:
            content = content.replace("@" + name, "")
    content = _AT_PLACEHOLDER.sub("", content)
    content = _AT_RAW_TAG.sub("", content)
    return content


def _parse_event(line):
    """解析 event consume stdout 一行 NDJSON。
    返回 (问句, chat_id, message_id, sender_id, chat_type) 或 None。"""
    try:
        d = json.loads(line)
    except Exception:
        return None
    chat_type = d.get("chat_type") or ""
    chat_id = d.get("chat_id")
    msg_id = d.get("message_id")
    sender_id = d.get("sender_id")
    # 跳过 bot 自己发的（双保险，event 一般不触发 bot 发送方）
    if d.get("sender_type") == "bot":
        return None
    if chat_type == "p2p":
        # K-STOCK-299：私聊无需 @，content 即问句——此前被「mentions
        # 必含 bot」+「群白名单」双闸拦死，私聊从不触发。
        q = _strip_at(d).strip()
        if not q or q.startswith("[AI] "):
            return None
        return (q, chat_id, msg_id, sender_id, chat_type)
    # 群聊 @ 判定（K-STOCK-301：三形态兼容——lark-cli NDJSON 实测不透出
    # mentions，纯 mentions 判定对真实客户端消息永不命中，2026-10-13 实证）：
    #   1) mentions 含 BOT_OPEN_ID（透出时精确路径）
    #   2) content 含 @_user_N 占位（真实客户端 @本app机器人，Feishu 脱敏渲染，
    #      旧 bot 同款判据；@其他 app 机器人保留原文不会误中）
    #   3) content 含 <at user_id=BOT_OPEN_ID> 原文（API 直发 @，pg 命名空间）
    mentions = d.get("mentions") or []
    content = d.get("content") or ""
    if mentions:
        hit = any((m.get("id") == BOT_OPEN_ID) for m in mentions)
    else:
        hit = ("@_user_" in content
               or ('<at user_id=%s' % BOT_OPEN_ID) in content
               or ('<at user_id="%s"' % BOT_OPEN_ID) in content)
    if not hit:
        return None
    q = _strip_at(d).strip()
    # 过滤机器人自己发的回复（[AI] 前缀，双保险）
    if q.startswith("[AI] "):
        return None
    if not q:
        return None
    return (q, chat_id, msg_id, sender_id, chat_type)


# ── 主循环 ─────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="P-G 飞书机器人查数（event consume 实时模式）")
    ap.add_argument("--chat-id", required=True,
                    help="监控的 chat_id（逗号分隔多个，作为白名单过滤）")
    ap.add_argument("--dry", action="store_true",
                    help="不真正回复，只打印解析+答案（联调用）")
    ap.add_argument("--selftest", action="store_true",
                    help="ready 后自己发 @bot 消息测试事件投递（诊断用）")
    a = ap.parse_args()

    exe = find_lark_cli()
    if not exe:
        print("ERROR lark-cli.exe not found under %s" % LARK_ROOT)
        sys.exit(1)

    chat_ids = a.chat_id or os.environ.get("PG_ROBOT_CHAT_IDS", "")   # CLI --chat-id 优先，回落环境变量（2026-10-07 env 化）
    chat_whitelist = set(c.strip() for c in chat_ids.split(",") if c.strip())

    _log("lark-cli: %s" % exe)
    _log("chat_whitelist: %s" % chat_whitelist)
    _log("BOT_OPEN_ID: %s" % BOT_OPEN_ID)
    _log("dry=%s" % a.dry)

    # 1. 换 TAT（同步，启动必须成功）
    #    开机早期本机代理（127.0.0.1:7890）未就绪会 WinError 10061，
    #    60s 间隔重试自愈（10 次窗口覆盖代理客户端自启）
    try:
        global _cur_tat
        for i in range(10):
            try:
                _cur_tat = _get_tat()
                break
            except Exception as e:
                if i == 9:
                    raise
                _log("TAT init retry %d/10: %s" % (i + 1, e))
                time.sleep(60)
        _log("TAT init ok len=%d" % len(_cur_tat))
    except Exception as e:
        _log("FATAL: TAT init fail: %s" % e)
        sys.exit(1)

    # 2. 起 TAT 刷新线程
    threading.Thread(target=_refresh_tat_loop, daemon=True).start()

    # 3. 查数线程池
    pool = ThreadPoolExecutor(max_workers=1)  # 单 worker 防 Ollama OOM

    # 4. 防滥用状态
    seen = OrderedDict()    # message_id -> True（LRU 幂等去重）
    last_ask = {}           # sender_id -> last ts

    # 5. event consume 主循环（崩溃自动重启）
    _log("event consume loop started (Ctrl+C to exit)")
    while True:
        try:
            p = _start_consume(exe)
            _log("consume pid=%d spawned" % p.pid)
            ready = _wait_ready(p, timeout=20)
            if not ready:
                _log("consume not ready, restart in %ds" % CONSUME_RESTART_S)
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
                time.sleep(CONSUME_RESTART_S)
                continue
            _log("consume ready, reading events...")
            # selftest: ready 后 3 秒自己发 @bot 消息（和 pg_ab_v2.py 完全一样的方式）
            if a.selftest:
                def _selftest():
                    time.sleep(3)
                    text = '<at user_id="%s">P-Gbot</at> selftest_%d' % (BOT_OPEN_ID, int(time.time()))
                    _log("[selftest] sending: %s" % text)
                    try:
                        # 不传 env，继承当前进程环境（和 pg_ab_v2.py 一样，--as user 需要 user token）
                        r = subprocess.run(
                            [exe, "im", "+messages-send",
                             "--chat-id", list(chat_whitelist)[0],
                             "--text", text, "--as", "user"],
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=30)
                        _log("[selftest] send: %s" % (r.stdout or "")[:200])
                    except Exception as e:
                        _log("[selftest] send err: %s" % e)
                threading.Thread(target=_selftest, daemon=True).start()
            # 主读循环
            while True:
                line = p.stdout.readline()
                if not line:
                    # 子进程退出（崩溃/被杀）
                    _log("consume stdout EOF, exit_code=%s" % p.poll())
                    break
                line = line.strip()
                if not line:
                    continue
                _log("RAW_EVENT: %s" % line[:2000])
                parsed = _parse_event(line)
                if not parsed:
                    _log("parse_skip (not @bot/p2p or filtered)")
                    continue
                q, cid, msg_id, sender, ctype = parsed
                # 白名单过滤（K-STOCK-299：私聊放行——单用户部署，
                # bot 只可能被主人/家庭成员私聊，无滥用面）
                if ctype != "p2p" and cid not in chat_whitelist:
                    _log("skip chat not in whitelist: %s" % cid)
                    continue
                # 幂等去重
                if msg_id in seen:
                    continue
                seen[msg_id] = True
                while len(seen) > DEDUP_MAX:
                    seen.popitem(last=False)
                # 限流
                now = time.time()
                last = last_ask.get(sender, 0)
                if now - last < RATE_LIMIT_S:
                    wait = int(RATE_LIMIT_S - (now - last))
                    _log("rate limit: sender=%s wait=%ds" % (sender, wait))
                    _send_text(exe, cid,
                               "查询太频繁，请 %d 秒后再试" % wait, dry=a.dry)
                    continue
                last_ask[sender] = now
                _log("@trigger q=%r chat=%s from=%s" %
                     (q[:60], cid, sender))
                pool.submit(_worker, q, cid, exe, a.dry)
            # 子进程退出后等 5s 重启
            try:
                if p.stdin and not p.stdin.closed:
                    p.stdin.close()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/p_g_robot.py", exc)
            try:
                p.wait(timeout=2)
            except Exception:
                p.kill()
            _log("consume crashed, restart in %ds" % CONSUME_RESTART_S)
            time.sleep(CONSUME_RESTART_S)
        except KeyboardInterrupt:
            _log("interrupted, shutting down...")
            break
        except Exception as e:
            _log("loop err: %s" % e)
            time.sleep(CONSUME_RESTART_S)

    pool.shutdown(wait=False)
    _log("bye")


if __name__ == "__main__":
    main()
