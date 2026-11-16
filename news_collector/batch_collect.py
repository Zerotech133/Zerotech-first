# -*- coding: utf-8 -*-
"""批量补采驱动（可复用版，2026-11-16 判例沉淀）。

背景：2026-11-16 第二批补采用的临时脚本 `_wave2_collect.py` 暴露两处缺口——
taoguba 段只设「拦截页」熔断、没有「失败数」熔断（批 15 失败 4 只未停，
若遇软封会把「被限速」记成「源无数据」一路跑完）；且临时脚本跑完即删，
下次补采又要重写。本脚本把两段逻辑沉淀入库。

熔断判据（两段一致，guba 段先做对、taoguba 段补齐）：
  1. 失败数熔断：本批失败 > --fail-fuse（默认 3）→ 停该段。软封（返回正常
     模板但内容被限速掏空）不触发拦截页判据，只有失败数能兜住。
  2. 拦截熔断：出现拦截页 → 立即停该段（硬封明确信号）。
  3. 采集器 DONE 行自带 blocked=N（guba_pw）与 拦截页（taoguba）计数——
     2026-11-16 起两采集器均落 `_blocked.json` 失败账本，缺口工具可区分
     「被拦截」与「从未采过」。

用法：
  python batch_collect.py --codes-file zero.txt --sources guba,taoguba
  python batch_collect.py --codes 600519,002737 --sources guba
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG_F = os.path.join(HERE, "logs", "batch_collect.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_F), exist_ok=True)
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def batches(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def run_one(cmd, timeout=540):
    """stdin=DEVNULL：本机 subprocess 句柄错误的既有根治手段（HEAD fe6e035f2）。"""
    r = subprocess.run(cmd, cwd=HERE, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def parse_guba_done(out):
    """DONE ok=N fail=M blocked=K 新增=X 耗时=Y → (ok, fail, blocked)。
    兼容旧格式（无 blocked= 字段）按 0 计——旧字段缺即未见拦截页计数，
    熔断靠 fail 数兜底。"""
    for line in reversed(out.splitlines()):
        if line.startswith("DONE"):
            parts = {}
            for p in line.split()[1:]:
                if "=" in p:
                    k, _, v = p.partition("=")
                    try:
                        parts[k] = int(v)
                    except ValueError:
                        pass
            return (parts.get("ok", 0), parts.get("fail", 0),
                    parts.get("blocked", 0))
    return 0, 1, 0        # 无 DONE 行 = 整批异常，按全失败处理


def parse_tgb_done(out):
    """「完成: N 标的, 新增 X 条, 失败 Y, 拦截页 -/列表, ...」→ (ok, fail, blocked)。"""
    for line in reversed(out.splitlines()):
        if "完成:" in line:
            fail = 0
            blocked = 0
            for seg in line.split(","):
                seg = seg.strip()
                if seg.startswith("失败"):
                    try:
                        fail = int(seg.split()[1])
                    except (IndexError, ValueError):
                        fail = 1
                if seg.startswith("拦截页") and not seg.endswith("拦截页 -"):
                    blocked = 1
            n_target = 0
            try:
                n_target = int(line.split("完成:")[1].split("标的")[0].strip())
            except (IndexError, ValueError):
                pass
            return (max(n_target - fail, 0), fail, blocked)
    return 0, 1, 0


def run_segment(name, codes, batch_n, cmd_builder, parser, fuse, interval,
                seg_timeout):
    """跑一段。返回 (ok_total, fail_total, fused_reason)。"""
    ok = fail = 0
    fused = ""
    all_batches = list(batches(codes, batch_n))
    for bi, bt in enumerate(all_batches, 1):
        try:
            rc, out = run_one(cmd_builder(bt), seg_timeout)
        except subprocess.TimeoutExpired:
            ok += 0
            fail += len(bt)
            log("%s 批%d/%d 超时(>%ds) 全批计失败" % (name, bi, len(all_batches),
                                                    seg_timeout))
            if fail > fuse:
                fused = "失败数熔断(含超时)"
                break
            continue
        b_ok, b_fail, b_blocked = parser(out)
        ok += b_ok
        fail += b_fail
        log("%s 批%d/%d ok=%d fail=%d blocked=%d" % (name, bi, len(all_batches),
                                                    b_ok, b_fail, b_blocked))
        if b_blocked > 0:
            fused = "拦截页熔断"
            break
        if b_fail > fuse:
            fused = "失败数熔断(本批 fail=%d > %d)" % (b_fail, fuse)
            break
        time.sleep(interval)
    return ok, fail, fused


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量补采驱动：分批+双熔断+账本")
    ap.add_argument("--codes", default="", help="逗号分隔代码")
    ap.add_argument("--codes-file", default="", help="代码清单文件（每行一只，容忍 CRLF）")
    ap.add_argument("--sources", default="guba,taoguba",
                    help="段序即执行序，逗号分隔：guba / taoguba")
    ap.add_argument("--guba-batch", type=int, default=13)
    ap.add_argument("--tgb-batch", type=int, default=20)
    ap.add_argument("--fail-fuse", type=int, default=3,
                    help="单批失败数超过即熔断该段（默认 3）")
    ap.add_argument("--interval", type=float, default=8.0, help="批间隔秒")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=540, help="单批超时秒")
    a = ap.parse_args(argv)

    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    if a.codes_file:
        codes = [c.strip() for c in open(a.codes_file, encoding="utf-8")
                 if c.strip()]
    if not codes:
        print("[FATAL] 无代码清单（--codes / --codes-file 二选一）", file=sys.stderr)
        return 2
    bad = [c for c in codes if not (c.isdigit() and len(c) == 6)]
    if bad:
        # CRLF 判例：清单脏字符必须显形，不能静默当不适用
        print("[FATAL] 非法代码（须 6 位数字，收到 %d 只异常）: %s"
              % (len(bad), ",".join(bad[:8])), file=sys.stderr)
        return 2

    log("批量补采开始：%d 只，段序 %s，fail-fuse=%d"
        % (len(codes), a.sources, a.fail_fuse))
    segs = []
    for name in [s.strip() for s in a.sources.split(",") if s.strip()]:
        if name == "guba":
            segs.append(("guba", a.guba_batch,
                         lambda bt: [PY, "guba_pw.py", ",".join(bt),
                                     "--pages", str(a.pages),
                                     "--wait-ms", "8000"],
                         parse_guba_done))
        elif name == "taoguba":
            segs.append(("taoguba", a.tgb_batch,
                         lambda bt: [PY, "taoguba_collector.py", ",".join(bt),
                                     "--pages", str(a.pages)],
                         parse_tgb_done))
        else:
            print("[FATAL] 未知源段: %s" % name, file=sys.stderr)
            return 2

    summary = {}
    for name, bn, builder, parser in segs:
        ok, fail, fused = run_segment(name, codes, bn, builder, parser,
                                      a.fail_fuse, a.interval, a.timeout)
        summary[name] = (ok, fail, fused)
        log("%s 段结束 ok=%d fail=%d %s" % (name, ok, fail,
                                            ("熔断: " + fused) if fused else "未熔断"))
        if fused:
            log("后续段跳过（熔断传播，防加重）")
            break
        time.sleep(a.interval)
    log("批量补采结束 " + " | ".join(
        "%s ok=%d fail=%d%s" % (k, v[0], v[1],
                                " 熔断=" + v[2] if v[2] else "")
        for k, v in summary.items()))
    # 熔断=主动降速止损，退出码 0 但摘要里带熔断标记；段全失败才 rc=1
    all_fail = all(v[1] > v[0] for v in summary.values())
    return 1 if all_fail else 0


if __name__ == "__main__":
    sys.exit(main())
