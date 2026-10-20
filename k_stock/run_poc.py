# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 日终 runner

用法：
  python k_stock/run_poc.py 600519.SH 贵州茅台 [--mock] [--out data/poc_YYYYMMDD.json]
  python k_stock/run_poc.py --batch data/watchlist.txt   # 批量（每行 "代码 名称"）

产出：data/poc_<日期>.json（五节点结构化输出+trace+degraded，lineage 可追溯）
另逐只追加 data/poc_results_<日期>.jsonl（每只五节点一跑完即落盘，宿主中途被掐
不清零；重启读该账本跳过已完成标的，--force 全量重跑。K-STOCK-20261020-INCREMENT）
S1 判据挂钩：连续 5 个交易日无人干预跑通；picks 次日收益秩相关>0（对照脚本 S2 挂）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from k_stock.kstock.llm import LLMClient
from k_stock.kstock.orchestrator import Orchestrator, load_team

TEAM = HERE / "config" / "team_poc.json"
OUT_DIR = HERE / "data"
GUARD_LOG = HERE / "data" / "guard_aborts.jsonl"   # 守卫中止结构化留痕（append jsonl）
HEARTBEAT = HERE / "data" / "last_run.json"        # 跑批心跳（mtime 即判"今天跑没跑"）


def _append_guard_log(record: dict) -> None:
    """守卫中止落盘（K-STOCK-20261019-GUARDLOG）。
    O_APPEND + 单次 os.write：小体积追加在单写者语义下即原子，多进程并发最多
    交错行不会撕裂行（与 llm_cost jsonl 判例同风格）。
    落盘失败静默降级——日志失败绝不改变主流程退出码（exit 3 语义保持不变）。"""
    try:
        GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(GUARD_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


def _write_heartbeat(rc: int, elapsed_s: float, summary: dict) -> None:
    """跑批心跳：正常完成/异常退出（含守卫 exit 3）均落 data/last_run.json，
    「今天跑没跑」看文件 mtime 即判，零产物不再无法区分未触发/被拦截。
    tmp+os.replace 原子换血，落盘失败静默。"""
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "rc": rc, "elapsed_s": round(elapsed_s, 1), "summary": summary}
        tmp = HEARTBEAT.with_name(HEARTBEAT.name + ".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(HEARTBEAT))
    except Exception:
        pass


def _results_log_path() -> Path:
    """当日逐只落盘账本路径（K-STOCK-20261020-INCREMENT）。"""
    return OUT_DIR / f"poc_results_{datetime.now():%Y%m%d}.jsonl"


def _append_result_log(record: dict) -> bool:
    """逐只落盘（K-STOCK-20261020-INCREMENT）：每只标的五节点循环一完成即追加
    当日 poc_results_<YYYYMMDD>.jsonl，宿主中途硬杀（T-O7 定因 90min 超时回收
    进程树）已完成的不再清零。O_APPEND + 单次 os.write，与 _append_guard_log
    同判例。失败 WARN 不中断主流程、不改变退出码语义。"""
    try:
        path = _results_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except Exception as e:
        print(f"[WARN] 逐只落盘失败（主流程继续，不影响退出码）: {e}", flush=True)
        return False


def _load_done_codes(path: Path) -> dict[str, dict]:
    """读当日逐只账本，返回 {code: 记录}（同 code 多行取最后一条，后写为准）。
    账本只记五节点跑完的（FAIL 不记），故「code 在账本」即「已有完整结果」。
    坏行跳过、读失败视为无历史全量重跑——续跑判定宁放过不误跳。"""
    done: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                code = str(rec.get("code") or "").strip()
                if code:
                    done[code] = rec
    except FileNotFoundError:
        pass                                  # 当日首跑，无历史
    except OSError as e:
        print(f"[resume][WARN] 当日账本不可读（{e}），按无历史全量重跑", flush=True)
    return done


def run_one(stock: str, context: str = "", mock: bool = False, auto: bool = False) -> dict:
    nodes = load_team(TEAM)
    if auto:
        from k_stock.kstock.datafeed import build_context
        ctx = build_context(stock.split()[0])  # "代码 名称" 取代码
        context = (f"[技术面]\n{ctx['tech']}\n[新闻情绪]\n{ctx['news']}\n"
                   f"[基本面]\n{ctx['funda']}")
    client = LLMClient(mock=mock)
    orch = Orchestrator(nodes, client.chat)
    result = orch.run(stock=stock, context=context)
    result["stock"] = stock
    result["asof"] = datetime.now().isoformat(timespec="seconds")
    result["mock"] = mock
    result["auto_context"] = auto
    return result


# Ollama 服务链进程名（父链甄别用）：llama-server.exe 是 ollama.exe fork 的
# llama.cpp 推理引擎子进程，与第三方私起 llama-server 同名同名姓。
_OLLAMA_CHAIN_NAMES = {"ollama.exe", "ollama_app.exe", "ollama_llama_server.exe"}
_CULPRIT_UNKNOWN = "负载来源未明，热态超阈，建议查 /api/ps 与消费方日志。"


def _win_process_table(names: tuple[str, ...]) -> list[dict]:
    """PowerShell Get-CimInstance 取进程表（风格贴 ops/launch_push_admin.py 判例）。
    枚举失败（沙箱拦截/权限不足/超时）一律返回空表，绝不抛异常——守卫留痕是
    旁路诊断，不允许影响主流程。单条结果 ConvertTo-Json 会退化为对象，需归一。"""
    try:
        import subprocess
        flt = " OR ".join(f"Name='{n}'" for n in names)
        ps = (f"Get-CimInstance Win32_Process -Filter \"{flt}\" | "
              "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,"
              "WorkingSetSize | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        data = json.loads(r.stdout.decode("utf-8", errors="replace") or "[]")
        if isinstance(data, dict):
            data = [data]
        return [p for p in data if isinstance(p, dict)]
    except Exception:
        return []


def _is_ollama_path(exe: str) -> bool:
    """可执行路径是否落在 Ollama 安装/模型目录（\\.ollama\\ 或 \\Ollama\\）。
    只看 ExecutablePath，不看 CommandLine——第三方进程命令行可能引用 Ollama
    模型 blob，路径不构成归属证据。"""
    low = (exe or "").lower()
    return "\\.ollama" in low or "\\ollama" in low


def _classify_llama_procs(procs: list[dict]) -> str:
    """竞争者甄别（纯函数，注入进程表便于单测；K-STOCK-20261020-CULPRITFIX）。
    背景：07:18 守卫把 Ollama 亲儿子 llama-server（8.08GB 驻留）当竞争者留痕，
    若按注记字面"关闭竞争者"会误杀 Ollama 本体——三态修正：
    ① 明确竞争者（非 Ollama 链满载 llama-server）→ 保留原"关闭或错峰"文案；
    ② Ollama 自身链占用（父链命中 ollama.exe，或路径在 Ollama 目录）→
       "Ollama 推理引擎自身忙，建议查消费方而非关服务"；
    ③ 无明确占用（无嫌疑进程/链不可判/Ollama 链已 idle）→ 未明文案。
    守卫探针自身触发的推理归属无需单独判定：探针经 11434 由 ollama.exe 派发，
    其引擎子进程父链必命中 ollama.exe，已被②覆盖。
    权限降级纪律：父链解析失败/父进程不可见（已退出或 OpenProcess 拒绝）的
    嫌疑进程跳过、不计竞争者（宁漏勿误杀），绝不 crash。"""
    by_pid: dict = {}
    suspects: list = []
    for p in procs:
        try:
            by_pid[int(p.get("ProcessId"))] = p
        except (TypeError, ValueError):
            continue
        if "llama-server" in str(p.get("Name") or "").lower():
            suspects.append(p)
    for s in suspects:
        try:
            mem_gb = int(s.get("WorkingSetSize") or 0) / 1024 ** 3
        except (TypeError, ValueError):
            mem_gb = 0.0
        chain_is_ollama = False
        cur = s
        for _ in range(8):                        # 链深上限，防父环
            try:
                parent = by_pid.get(int(cur.get("ParentProcessId") or 0))
            except (TypeError, ValueError):
                break                             # 父 PID 不可读 → 停止上溯
            if parent is None:
                break                             # 父不在表内（已退出/不可见）
            if str(parent.get("Name") or "").lower() in _OLLAMA_CHAIN_NAMES:
                chain_is_ollama = True
                break
            cur = parent
        exe = str(s.get("ExecutablePath") or "")
        if chain_is_ollama or _is_ollama_path(exe):
            if mem_gb > 1.0:                      # ② Ollama 自身链满载驻留
                return (f"Ollama 自身链占用：llama-server.exe 满载 {mem_gb:.2f}GB"
                        f"（Ollama 推理引擎子进程，模型驻留推理态）——Ollama 推理引擎"
                        f"自身忙（可能被下游任务如 m3/分类轮占满），建议查消费方而非关服务。")
            continue                              # Ollama 链但已卸载（idle）→ 非负载来源
        if exe:                                   # 路径可读且非 Ollama 目录 → ① 明确竞争者
            if mem_gb > 1.0:
                return (f"竞争者定位：llama-server.exe 满载 {mem_gb:.2f}GB（推理态，"
                        f"与 Ollama 踩踏主因）——请确认归属后关闭或错峰。")
            return (f"竞争者在册但 idle（{mem_gb * 1024:.0f}MB，已卸载）——负载另有来源。")
        # exe 不可读 + 父链不可判 → 跳过该进程（不计竞争者，防误杀）
    return _CULPRIT_UNKNOWN                       # ③ 无明确占用


def _competitor_note() -> str:
    """中止归因：甄别竞争推理服务三态（详见 _classify_llama_procs）。
    10-08 判例背景：第三方私起 llama-server（监听 54199，非 K259 的 8030 入口）
    满载 8.22GB 时与 Ollama 互相踩踏，idle 时自动卸载回 263MB——该检测保留，
    但 Ollama 服务链自身进程不再误判为竞争者。"""
    return _classify_llama_procs(_win_process_table(
        ("llama-server.exe", *sorted(_OLLAMA_CHAIN_NAMES))))


def _glm_probe(timeout_s: int = 30) -> tuple[bool, str]:
    """GLM 主链可达性探测（K-STOCK-20261019-GUARDDECOUPLE）。
    判定口径与 llm.py LLMClient 主链一致：GLM_API_KEY/ZHIPU_API_KEY 在位
    （llm.py 同名逻辑）+ coding 端点一次 max_tokens=8 最小 chat 往返；
    KSTOCK_GLM_BASE/KSTOCK_GLM_MODEL 覆盖与 llm.py 默认值对齐。
    返回 (可达, 说明)，不抛异常——探测失败折算为 (False, 原因)。"""
    key = (os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY") or "").strip()
    if not key:
        return False, "GLM_API_KEY 未配置（与 llm.py 主链判定一致）"
    try:
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = os.environ.get(
            "KSTOCK_GLM_BASE", "https://open.bigmodel.cn/api/coding/paas/v4").rstrip("/")
        model = os.environ.get("KSTOCK_GLM_MODEL", "glm-5.3-flash")
        body = json.dumps({"model": model, "stream": False, "max_tokens": 8,
                           "messages": [{"role": "user", "content": "回复两个字：正常"}]}).encode("utf-8")
        req = urllib.request.Request(
            base + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key}, method="POST")
        d = json.loads(op.open(req, timeout=timeout_s).read())
        _ = d["choices"][0]["message"]["content"]
        return True, f"{model} ok"
    except Exception as e:
        return False, str(e)[:200]


def load_guard(threshold_s: float = 6.0, retries: int = 2, cooldown_s: float = 180.0) -> tuple[float, float]:
    """两段式负载守卫（2026-10-08 修正）：
    第 1 探针 = 冷启动预热（Ollama keep_alive 默认 5m 过期即卸载模型，重载 70s+，不计判定）；
    第 2 探针 = 热态真实推理速度，超阈值判定负载挤压 → 冷却 cooldown_s 后重探，
    最多 retries 次（负载是波动的：18:05 实测 18.0s→18:15 回落；第三方 llama-server
    等竞争者有 idle 自动卸载机制，满载→冷却→回落后即可放行）。全重试仍超 → 由调用方中止。
    阈值 6s（标定曲线，10-08 实证）：热态 2s→5/5 全绿 | 8.8s→3/5（T2/N1 崩）|
    10.8s→1/5（T1/N1 合法 JSON 前缀截断=生成流中途夭折）。S1 判据要有效数据，
    宁缺毋滥；空闲基线 ~2s，6s 有 3 倍余量。
    返回 (冷耗时, 最终热耗时)。守卫顺带完成预热，一举两得。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _probe() -> float:
        t0 = time.time()
        body = json.dumps({"model": "qwen3.5:9b", "stream": False, "keep_alive": 1800,
                           "messages": [{"role": "user", "content": "回复两个字：正常"}]}).encode()
        d = json.loads(op.open(urllib.request.Request(
            "http://127.0.0.1:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}), timeout=300).read())
        _ = d["message"]["content"]
        return time.time() - t0

    t_cold = _probe()                       # 预热，不计判定
    t_warm = _probe()
    attempt = 0
    while t_warm > threshold_s and attempt < retries:
        attempt += 1
        print(f"[guard] 热态 {t_warm:.1f}s 超阈（第 {attempt}/{retries} 次）——"
              f"冷却 {cooldown_s:.0f}s 后重探（等竞争者 idle 卸载）…", flush=True)
        time.sleep(cooldown_s)
        t_warm = _probe()
    return t_cold, t_warm


def main():
    t0 = time.time()
    rc = 0
    summary: dict = {}
    try:
        _run_targets(summary)
    except SystemExit as e:                       # 守卫 exit 3 / argparse exit 2 均留心跳
        rc = e.code if isinstance(e.code, int) else 1
        raise
    except BaseException:
        rc = 1
        raise
    finally:
        _write_heartbeat(rc, time.time() - t0, summary)


def _run_targets(summary: dict) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stock", nargs="?", help="如 600519.SH 贵州茅台（加引号）")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--auto", action="store_true", help="三路 context 自动接入（D0 正式）")
    ap.add_argument("--batch", help="清单文件，每行 '代码 名称' 或纯代码")
    ap.add_argument("--watchlist", type=int, metavar="N",
                    help="自动取监控池成交额前 N 只（与 --auto 配套）")
    ap.add_argument("--out", help="输出 json 路径（默认 data/poc_<时间戳>.json）")
    ap.add_argument("--no-guard", action="store_true", help="跳过负载守卫")
    ap.add_argument("--force", action="store_true",
                    help="忽略当日逐只账本已有结果，全量重跑（默认幂等续跑）")
    args = ap.parse_args()

    targets: list[str] = []
    if args.watchlist:
        from k_stock.kstock.datafeed import watchlist_from_quotes
        targets = watchlist_from_quotes(args.watchlist)
    elif args.batch:
        for line in Path(args.batch).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    elif args.stock:
        targets.append(args.stock)
    else:
        ap.error("需要 stock / --batch / --watchlist 之一")

    # 幂等续跑（K-STOCK-20261020-INCREMENT）：启动即读当日账本，已有完整结果的
    # 标的跳过（--force 全量重跑）。全部已完成时连守卫探测也一并跳过——
    # 两段式探测+冷却固定吃 ~13.5 分钟预算，纯续跑不碰 LLM 没必要付。
    log_path = _results_log_path()
    done = {} if args.force else _load_done_codes(log_path)
    if done:
        print(f"[resume] 当日账本 {log_path.name} 已有 {len(done)}/{len(targets)} 只"
              f"完整结果，续跑只补缺（--force 全量重跑）", flush=True)

    if not args.mock and not args.no_guard and len(done) < len(targets):
        # K-STOCK-20261019-GUARDDECOUPLE：守卫与主链解耦。主链为 GLM 官方 API
        # （k_stock/kstock/llm.py），Ollama 仅是 fallback——默认路径下 Ollama 侧
        # 信号（不可达/热态超阈）降级为 WARN 放行，仅 GLM 主链不可达才中止；
        # 显式 KSTOCK_FORCE_OLLAMA=1 恢复硬守卫（Ollama 不可达/超阈即 exit 3），
        # 用于未来主链切回 Ollama 的场景。
        force_ollama = os.environ.get("KSTOCK_FORCE_OLLAMA", "") == "1"
        try:
            t_cold, t_warm = load_guard()
            print(f"[guard] 冷启动 {t_cold:.1f}s（预热，不计判定）| 热态推理 {t_warm:.1f}s"
                  f"（含冷却重探，最多 2 次×180s）", flush=True)
            if t_warm > 6.0:
                culprit = _competitor_note()
                if force_ollama:
                    print(f"[guard] 热态超 6s（含重试）——系统负载挤压推理"
                          f"（标定：2s 全绿/8.8s 半崩/10.8s 仅 1 节点存活），中止。{culprit}"
                          f"建议换时段（清晨 07:05 自动化窗口）或关闭竞争推理服务后重试。", flush=True)
                    _append_guard_log({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "phase": "probe_hot", "latency_s": round(t_warm, 2),
                        "t_cold_s": round(t_cold, 2), "threshold_s": 6.0,
                        "reason": "热态超6s（含冷却重试）中止", "culprit": culprit,
                        "ollama_alive": True, "watchlist_size": len(targets),
                    })
                    summary.update({"stage": "guard", "phase": "probe_hot"})
                    sys.exit(3)
                print(f"[guard][WARN] Ollama 热态超 6s（含重试）。{culprit}"
                      f"主链为 GLM——降级放行，不中止"
                      f"（KSTOCK_FORCE_OLLAMA=1 可恢复硬守卫）。", flush=True)
                _append_guard_log({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": "probe_hot_degraded", "latency_s": round(t_warm, 2),
                    "t_cold_s": round(t_cold, 2), "threshold_s": 6.0,
                    "reason": "ollama_hot_over_threshold_degraded", "culprit": culprit,
                    "ollama_alive": True, "watchlist_size": len(targets),
                })
        except Exception as e:
            if force_ollama:
                print(f"[guard] Ollama 不可达: {e}", flush=True)
                _append_guard_log({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": "probe_unreachable", "latency_s": None,
                    "reason": f"Ollama 不可达: {e}", "ollama_alive": False,
                    "watchlist_size": len(targets),
                })
                summary.update({"stage": "guard", "phase": "probe_unreachable"})
                sys.exit(3)
            # 默认路径降级：Ollama 缺席不再中止整批，先验 GLM 主链再放行
            print(f"[guard][WARN] Ollama 不可达（{e}）——主链为 GLM，验证主链后放行…", flush=True)
            glm_ok, glm_note = _glm_probe()
            _append_guard_log({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "phase": "probe_unreachable_degraded", "latency_s": None,
                "reason": "ollama_unreachable_degraded", "detail": str(e)[:200],
                "ollama_alive": False, "glm_ok": glm_ok, "glm_note": glm_note,
                "watchlist_size": len(targets),
            })
            if not glm_ok:
                print(f"[guard] GLM 主链亦不可达（{glm_note}）——中止整批"
                      f"（S1 宁缺毋滥，防全链 LLMUnavailable 级联）。", flush=True)
                _append_guard_log({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": "glm_unreachable", "latency_s": None,
                    "reason": f"GLM 主链不可达: {glm_note}", "watchlist_size": len(targets),
                })
                summary.update({"stage": "guard", "phase": "glm_unreachable"})
                sys.exit(3)
            print(f"[guard] GLM 主链可达（{glm_note}）——放行，继续 GLM 主链。", flush=True)

    OUT_DIR.mkdir(exist_ok=True)
    results = []
    for stock in targets:
        code = stock.split()[0]
        if code in done:
            r = done[code]                    # 续跑：账本记录原样回填 results，
            print(f"[skip] {code} 今日账本已有完整结果——跳过"
                  f"（--force 可强制重跑）", flush=True)   # 整盘 json 仍含全部标的
            results.append(r)
            continue
        print(f"[run] {stock} ...", flush=True)
        try:
            r = run_one(stock, mock=args.mock, auto=args.auto)
            d1 = r["outputs"].get("D1", {})
            print(f"  -> D1: {d1.get('action')} conf={d1.get('confidence')} "
                  f"degraded={r['degraded'] or '无'}", flush=True)
            # 逐只落盘：五节点一跑完立即追加，被掐不清零；FAIL 不记（续跑重试）
            rec = dict(r)
            rec["ts"] = datetime.now().isoformat(timespec="seconds")
            rec["code"] = code
            _append_result_log(rec)
        except Exception as e:
            r = {"stock": stock, "error": str(e)[:300],
                 "asof": datetime.now().isoformat(timespec="seconds")}
            print(f"  -> FAIL: {r['error']}", flush=True)
        results.append(r)

    ok = sum(1 for r in results if "error" not in r)
    degraded = sum(1 for r in results if r.get("degraded"))
    summary.update({"targets": len(targets), "ok": ok, "fail": len(results) - ok,
                    "degraded": degraded, "mock": args.mock})
    out = Path(args.out) if args.out else OUT_DIR / f"poc_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved ->", out)


if __name__ == "__main__":
    main()
