#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Ollama 守护启动器（tuned 配置）—— 2026-10-18 建立

背景（为什么需要这个脚本，别再用别的方式启动）
--------------------------------------------------
Ollama 在本机有一套 tuned 参数，原始载体是启动文件夹里的
`ollama-serve-tuned.bat`（2026-10-15 为 AMD Radeon 8060S iGPU 配的）：

    OLLAMA_FLASH_ATTENTION=1       # 大上下文性能
    OLLAMA_KV_CACHE_TYPE=q8_0      # 修 f16 KV cache 在大上下文下输出乱码
    OLLAMA_IGPU_ENABLE=1
    OLLAMA_CONTEXT_LENGTH=65536    # 上下文上限
    OLLAMA_KEEP_ALIVE=2h           # 模型驻留 2 小时，避免反复卸载重载
    OLLAMA_NUM_PARALLEL=2          # 并发 2，实测吞吐 1.53x

⚠️ 三个已踩过的坑，都是本脚本存在的原因：

1. **裸 `ollama serve` 不带这些变量 = 用默认配置（ctx=4096 / keep_alive=5m /
   KV=f16 / parallel=1）**。表面上服务是通的，但 run_shadow 的推理质量与
   稳定性都受影响，而且 `ollama ps` 里 ctx 显示 4096 才看得出来。
2. **`server.log` 是「Ollama 桌面 App」写的，CLI `serve` 的日志不进这个文件。**
   所以排查时读 `%LOCALAPPDATA%\\Ollama\\server.log` 会读到上一次 App 实例的
   旧 config 行，误判为「环境变量没生效」。要看真实 config，必须把 serve 的
   stdout 重定向到自己的文件。
3. **桌面 App 与 CLI serve 抢 11434 会互相踢**（日志出现
   `app_windows.go ... starting ollama server` + 请求 502）。所以启动前应先确认
   没有别的实例，必要时 taskkill 再起。

另一个必须知道的行为：**首次加载模型会超过 HTTP 客户端的常见超时**，
表现为 502 Bad Gateway（`waiting for llama-server to become available`），
但日志随后会显示模型其实已加载成功。所以「第一次请求 502」不等于配置错，
稍后重试通常就通。

用法
----
    python start_tuned_ollama.py            # 检查；不在则用 tuned 配置启动
    python start_tuned_ollama.py --status    # 只查状态 + 校验配置，不启动
    python start_tuned_ollama.py --restart   # 强制重启（会短暂中断推理）

退出码
------
默认模式 / --restart：0=已在跑或启动成功；1=启动失败；2=参数错误
--status（**当闸门用**）：
    0 = 服务在跑 且 配置已核验为 tuned（ctx 达 65536）
    1 = 服务在跑 但 配置不是 tuned（或端口不通）→ 需要 --restart
    2 = 无法判定（日志缺 config 行、也未取到 /api/ps）

为什么 --status 会返回 1/2 而不是恒 0
------------------------------------
本脚本 2026-10-18 首版把 --status 写成「打印完就 return 0」，等于校验器永远报健康。
实际踩到：当日实例是 ollama_watchdog.py 拉起的，日志写 `%LOCALAPPDATA%\Programs\Ollama\tuned.log`，
而首版只读自己的 `automation_runs\ollama_guard\serve_tuned.log`（该文件从未存在），
于是永远打印「无 serve 日志」并退 0 —— 与「配置正确」无法区分。故改为：
  · 多候选日志（自己的 → watchdog 的 tuned.log → App 的 server.log）
  · 按行内 `time=` 取**全局最新**的那条 config（server.log 是桌面 App 写的，
    常留着上一次实例的旧行，只读它会拿到过期值）
  · 再用 /api/ps 的 context_length 交叉验证（显式禁代理：会话沙箱注入 HTTP_PROXY 时，
    连 127.0.0.1 也会走代理，裸 urllib 会被代理返 502 而误判服务已死）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
import logging

HOST = "127.0.0.1"
PORT = 11434
EXE = (
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\t\AppData\Local"))
    / "Programs"
    / "Ollama"
    / "ollama.exe"
)
# 日志按定时任务产物统一约定落 automation_runs/<任务名>/（不随脚本位置漂移）
LOG_DIR = Path(
    os.environ.get("OLLAMA_GUARD_LOG_DIR", r"C:\aios\automation_runs\ollama_guard")
)
SERVE_LOG = LOG_DIR / "serve_tuned.log"
GUARD_LOG = LOG_DIR / "guard.log"

# config 行的候选来源（按可信度排序，但取「时间戳最新」的那条为准，不按文件顺序）
#   1) 本脚本自己写的 serve 日志（若本脚本启动过实例）
#   2) ollama_watchdog.py 定版的 tuned 日志（当前生产实例多由它拉起）
#   3) 桌面 App 写的 server.log —— 会残留旧实例的 config 行，只作兜底，命中时告警
_APPDATA_LOCAL = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\t\AppData\Local"))
CONFIG_LOG_CANDIDATES = [
    SERVE_LOG,
    _APPDATA_LOCAL / "Programs" / "Ollama" / "tuned.log",
    _APPDATA_LOCAL / "Ollama" / "server.log",
]

TUNED_ENV = {
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_IGPU_ENABLE": "1",
    # 2026-10-24 补第 7 项：OLLAMA_IGPU_ENABLE=1 单独存在时 iGPU **不会**进候选设备。
    # 本机用户级环境曾把 OLLAMA_VULKAN 显式设为 0（关掉 Vulkan 后端），
    # 于是"tuned 已生效、ctx 已 65536"却仍 100% CPU 推理——
    # `ollama ps` 的 PROCESSOR 列是唯一能看穿它的判据。
    # 2026-10-18 的勘误文档已把它列为 watchdog 额外设置的第 7 项，本启动器此前漏了。
    "OLLAMA_VULKAN": "1",
    "OLLAMA_CONTEXT_LENGTH": "16384",  # 2026-10-24 全仓收口（原 65536），与 watchdog 同值
    # 2026-10-24 收口：本启动器此前缺 MAX_LOADED_MODELS / DEBUG，且 KEEP_ALIVE 与
    # 看门狗不一致（2h vs 30m）——同一个服务换个入口拉起就是另一套参数，
    # 重启一次丢两项（当天实际丢了 MAX_LOADED_MODELS=3 与 DEBUG=1）。现全部对齐看门狗。
    # 2026-11-04 二次收口：08-24 那次只对齐了其中一个入口，启动文件夹的
    # ollama-serve-tuned.bat 仍是 65536/2/30m，于是"谁先抢到 11434 谁的配置生效"。
    # 本次把四处（用户级环境变量 / bat / ollama_watchdog.py / 本文件）统一为
    # 16384 / NUM_PARALLEL=1 / KEEP_ALIVE=2h / MAX_LOADED_MODELS=2。
    # 原 MAX_LOADED_MODELS=3 的依据是"35b+9b+轻量层三班"，35b 已于 11-02 物理
    # 删除，三班前提消失；现役 9b(5.9G)+bge-m3(1.16G) 双常驻即够，且与用户级一致。
    "OLLAMA_MAX_LOADED_MODELS": "2",
    # 原 30m 的依据是"35b 空闲常驻 22G 不划算"，35b 删除后该顾虑不成立，
    # 对齐用户级 2h，避免 watchdog / bat / 本启动器三档跳变。
    "OLLAMA_KEEP_ALIVE": "2h",
    # 2026-10-24 并行度 2 → 1：qwen35 架构明确不支持并行请求（服务日志 423 条
    # "model architecture does not currently support parallel requests"）。
    # 并行度 2 等于把 KV 槽翻倍、只占内存不提升吞吐，还会诱发 runner 重建。
    "OLLAMA_NUM_PARALLEL": "1",
    # DEBUG=1 除排障外还兼有"让 GPU discovery 稳定快速完成"的作用（10-16 判例）。
    "OLLAMA_DEBUG": "1",
}

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

# 判定 tuned 的「关键参数」（keep_alive 只影响重载延迟、不影响正确性，仅作信息项）
TUNED_CRITICAL = (
    "OLLAMA_CONTEXT_LENGTH",
    "OLLAMA_KV_CACHE_TYPE",
    "OLLAMA_FLASH_ATTENTION",
    "OLLAMA_NUM_PARALLEL",
    "OLLAMA_IGPU_ENABLE",
    "OLLAMA_VULKAN",  # 缺它则 iGPU 不生效（见上）
)
# ctx 达标线：默认 4096；tuned 历史 65536，2026-10-24 全仓收口后为 16384。
# 取 16384 与 TUNED_ENV 同值：低于它即判 NOT_TUNED（旧值 32768 会让新配置误报）
CTX_OK_FLOOR = 16384


def log(msg: str) -> None:
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with GUARD_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/start_tuned_ollama.py", exc)


def port_open(timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _parse_ts(line: str) -> str:
    """从日志行取 time= 的值（空串表示无时间戳，排序时视为最旧）。"""
    m = re.search(r"time=([^\s]+)", line)
    return m.group(1) if m else ""


def server_config():
    """扫描所有候选日志，取**全局最新**一条 server config 行的 env 值。

    返回 (cfg, src_path, ts_str)；找不到时返回 ({}, None, None)。

    为什么不按文件顺序取第一个：桌面 App 写的 server.log 常残留上一次实例的旧
    config 行，按顺序取会拿到过期值（这正是首版 --status 判不出来的原因之一）。
    """
    best = None  # (sort_key, cfg, path, ts)
    for idx, path in enumerate(CONFIG_LOG_CANDIDATES):
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # config 是**启动那一刻**写的一行，服务跑久后它早被挤出文件尾部
        # （tuned.log 20 分钟就到 1.1MB），所以头尾都要扫；中间是请求日志，无 config。
        lines = text.splitlines()
        if len(lines) > 8000:
            lines = lines[:4000] + lines[-4000:]
        for ln in lines:
            if "server config" not in ln:
                continue
            cfg = {}
            for key in TUNED_ENV:
                marker = key + ":"
                pos = ln.find(marker)
                if pos >= 0:
                    rest = ln[pos + len(marker) :]
                    cfg[key] = rest.split(" ")[0].split("]")[0]
            if not cfg:
                continue
            ts = _parse_ts(ln)
            key = (ts, -idx)  # 时间戳升序取最大；同戳时靠前的候选优先
            if best is None or key > best[0]:
                best = (key, cfg, path, ts)
    if best is None:
        return {}, None, None
    return best[1], best[2], best[3]


def api_ps(timeout: float = 6.0):
    """查 /api/ps。返回 models 列表；无法判定时返回 None（区别于 [] = 无模型加载）。

    **必须显式禁代理**：会话沙箱会注入 HTTP_PROXY，此时 urllib 连 127.0.0.1 也走代理，
    代理会返回 502 而不是 ConnectionRefused，从而把「服务活着」误判成「服务已死」。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        raw = opener.open("http://%s:%d/api/ps" % (HOST, PORT), timeout=timeout).read()
        return json.loads(raw).get("models", [])
    except Exception:
        return None


def tuned_deviations(cfg: dict) -> list:
    """返回关键参数的偏差列表；空列表 = 关键参数全达标。"""
    bad = []
    for key in TUNED_CRITICAL:
        want = TUNED_ENV[key]
        got = cfg.get(key)
        if got is None:
            bad.append("%s 缺失" % key)
            continue
        # 2026-10-24：Vulkan 与 FlashAttention 都是布尔量，日志里可能写 "true"、
        # 也可能写 "1"（取决于注入的是 bat 还是 launch 环境），必须归一比较。
        # 用严格相等会让"实际已开"的实例被判成偏差，进而误触发重启。
        if key in ("OLLAMA_FLASH_ATTENTION", "OLLAMA_VULKAN"):
            ok = str(got).lower() in ("true", "1")
        elif key == "OLLAMA_CONTEXT_LENGTH":
            try:
                ok = int(got) >= CTX_OK_FLOOR
            except Exception:
                ok = False
        else:
            ok = str(got) == want
        if not ok:
            bad.append("%s=%s(应 %s)" % (key, got, want))
    return bad


def ollama_procs() -> list[str]:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        )
        return [
            ln for ln in (r.stdout or "").splitlines() if "ollama.exe" in ln.lower()
        ]
    except Exception:
        return []


def start() -> bool:
    if not EXE.exists():
        log("FATAL 找不到 ollama.exe: %s" % EXE)
        return False

    env = os.environ.copy()
    env.update(TUNED_ENV)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = SERVE_LOG.open("ab")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [str(EXE), "serve"],
        env=env,
        stdout=fh,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    log("STARTED pid=%s (tuned env 已注入)" % proc.pid)

    deadline = time.time() + 60
    while time.time() < deadline:
        if port_open():
            log("PORT %d OPEN" % PORT)
            return True
        if proc.poll() is not None:
            log("FATAL 进程启动即退出 rc=%s（查 %s）" % (proc.returncode, SERVE_LOG))
            return False
        time.sleep(2)
    log("FATAL 60s 内端口未就绪")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ollama tuned 守护启动器")
    ap.add_argument("--status", action="store_true", help="只查状态，不启动")
    ap.add_argument("--restart", action="store_true", help="强制重启")
    args = ap.parse_args(argv)

    running = port_open()
    procs = ollama_procs()

    if args.status:
        log("STATUS port_open=%s processes=%d" % (running, len(procs)))
        cfg, src, ts = server_config()
        ps = api_ps() if running else None
        ps_ctx = None
        if ps:
            # 取已加载模型里**最大**的 ctx：客户端可按请求下发小 num_ctx（如分类链 2048），
            # 单个模型的小 ctx 不能证明服务端配置低
            ctxs = [
                m.get("context_length")
                for m in ps
                if isinstance(m.get("context_length"), int)
            ]
            if ctxs:
                ps_ctx = max(ctxs)

        if cfg:
            log("STATUS source=%s ts=%s" % (src.name if src else "?", ts or "?"))
            if src is not None and src.name == "server.log":
                log(
                    "WARN server.log 由桌面 App 写入，可能残留旧实例 config 行"
                    "（以 tuned.log 或 /api/ps 为准）"
                )
            log("STATUS config=" + json.dumps(cfg, ensure_ascii=False))
        else:
            log(
                "STATUS 候选日志中未找到 server config 行（候选: %s）"
                % ", ".join(p.name for p in CONFIG_LOG_CANDIDATES)
            )
        if ps_ctx is not None:
            log(
                "STATUS api_ps: 已加载 %d 个模型，最大 ctx=%d" % (len(ps or []), ps_ctx)
            )
        elif running and ps is None:
            log("STATUS api_ps 不可用（禁代理后仍失败）→ 真实 ctx 无法判定")

        # ---- 判定：这是本脚本当闸门用的关键，不能恒 0 ----
        # 优先级：/api/ps 的实时 ctx 是**真相**（服务此刻的真实配置）；
        #        config 日志行只说明「它是怎么被启动的」，可能是旧行或别的实例留下的。
        # 两者冲突时以实测为准，并告警——否则会误报 NOT_TUNED 让人去重启在跑的服务。
        if not running:
            log("VERDICT NOT_RUNNING（端口 %d 不通）→ 需要启动" % PORT)
            return 1

        if ps_ctx is not None:
            if ps_ctx >= CTX_OK_FLOOR:
                if cfg:
                    bad = tuned_deviations(cfg)
                    if bad:
                        log(
                            "WARN 日志行与实测冲突：%s；但 /api/ps ctx=%d 证明服务已 tuned，"
                            "该日志行应为旧实例残留，忽略" % ("; ".join(bad), ps_ctx)
                        )
                log(
                    "VERDICT TUNED（实测 /api/ps 最大 ctx=%d ≥ %d）"
                    % (ps_ctx, CTX_OK_FLOOR)
                )
                return 0
            log(
                "VERDICT NOT_TUNED（实测 /api/ps 最大 ctx=%d < %d，疑为默认 4096）"
                "→ 建议 --restart" % (ps_ctx, CTX_OK_FLOOR)
            )
            return 1

        # /api/ps 取不到（无模型加载或探针失败）→ 退回日志行判定
        if cfg:
            bad = tuned_deviations(cfg)
            if bad:
                log(
                    "VERDICT NOT_TUNED（仅据日志行）→ "
                    + "; ".join(bad)
                    + "（建议 --restart）"
                )
                return 1
            log("VERDICT TUNED（仅据日志行 %s，/api/ps 未取到 ctx）" % (ts or "?"))
            return 0
        log(
            "VERDICT UNKNOWN（无 config 行且无 /api/ps ctx）→ 建议 --restart 或查服务日志"
        )
        return 2

    if running and not args.restart:
        log("ALREADY RUNNING（不重启）")
        cfg, _, _ = server_config()
        bad = tuned_deviations(cfg) if cfg else []
        if bad:
            log(
                "WARN 当前实例关键参数不达标：%s；需要 tuned 请 --restart"
                % "; ".join(bad)
            )
        return 0

    if args.restart or not running:
        if procs:
            log("KILL 现有 ollama.exe（%d 个）" % len(procs))
            subprocess.run(
                ["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True, timeout=30
            )
            time.sleep(4)  # 给端口释放与 TIME_WAIT 收敛

    ok = start()
    if ok:
        cfg, src, _ = server_config()
        log(
            "DONE config=" + json.dumps(cfg, ensure_ascii=False)
            if cfg
            else "DONE（无 config 行）"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
