#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trace_critic.py — 轨迹 Critic（K-ORG-080/N8）

对 shadow/coder_traces/ 下的产码轨迹用本地 qwen3.5:9b（经 8080 网关，判例：
Ollama 直连 11434 不走；35b 已证伪勿用）做三维度打分：
  ① 增量纪律：变更面与单目标是否匹配（小问题大改=扣分）
  ② 契约变更声明：改签名/路由/公开接口是否有声明痕迹
  ③ 收尾调用核对：新增方法是否有收尾证据
     （判例 K-STOCK-141：批7切割丢 _council_page 尾行 self._html(body)
       → 服务端零异常 + 客户端裸 socket）
三维度任一 ≤4 分 → verdict=REVIEW（升级 QA1 深审清单）。
边界：打分只做升级触发器，不直接裁决（裁决权在 QA1/田丰）。

用法：
  python tools/trace_critic.py                # 最新 30 条轨迹打分
  python tools/trace_critic.py --tid K-ORG-038a
  python tools/trace_critic.py --limit 5
  python tools/trace_critic.py --report report.md

fail-open 纪律：网关不可达 → 提示后退出 0（不炸不重试风暴）；
每条间隔 0.5s 防打爆；全程只读轨迹，报告只写 --report 路径或 stdout。
"""
import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

# K-ORG-080/N8：轨迹目录与 agent_coder.py:440 TRACE_DIR 同源
TRACE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "shadow", "coder_traces")
GATEWAY = "http://127.0.0.1:8080/ollama/api/chat"   # 必须经网关（判例：11434 直连不走）
GATEWAY_PING = "http://127.0.0.1:8080/ollama/api/tags"  # 轻量探活（chat 冷启动>10s，勿用 ping）
MODEL = "qwen3.5:9b"                                # 35b 已证伪，勿用
REVIEW_THRESHOLD = 4        # 任一维度 ≤4 → REVIEW
INTERVAL = 0.5              # 每条打分间隔，防打爆
MAX_EVENTS_IN_PROMPT = 40   # 喂给 9b 的事件数上限
MAX_DETAIL = 120            # 单事件 detail 截断

# QA1 结果字段的候选 kind（勘察：当前轨迹 kind ∈ generate/edit/lint/smoke/
# write/commit/fail，无 QA1 字段 → 重合率暂不可统计，080 判据允许渐进）
QA_RESULT_KINDS = {"qa_verdict", "qa1_verdict", "qa_return", "verdict", "qa1"}

SYS_PROMPT = (
    "你是产码轨迹质检员（K-ORG-080/N8 trace_critic）。对一条编码代理轨迹"
    "打三个维度分（0-10 整数）：\n"
    "1. 增量纪律：变更面是否与单目标匹配，小问题大改/无关文件多改=扣分；\n"
    "2. 契约变更声明：改动函数签名/路由/公开接口时轨迹中是否有声明痕迹，"
    "无声明=扣分；\n"
    "3. 收尾调用核对：新增方法是否有收尾证据（smoke PASS/commit），"
    "有 write 无 smoke/commit=扣分（判例 K-STOCK-141 尾行丢失事故）。\n"
    '只输出 JSON：{"tid":"...","scores":{"增量":0-10,"契约":0-10,"收尾":0-10},'
    '"verdict":"PASS或REVIEW","reason":"一句话"}'
)


def extract_json(text):
    """K-ORG-038 判例：模型爱先写散文再上围栏——先 strip ``` 围栏，
    再找首个 {...}（花括号配对）解析。失败返回 None（调用方记 UNPARSED 不炸）。"""
    body = re.sub(r"```[a-zA-Z]*\s*", "", text).replace("```", "")
    start = body.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start:i + 1])
                except (ValueError, TypeError):
                    return None
    return None


def load_trace(path):
    """解析单条轨迹 JSONL → (tid, 事件列表)。坏行跳过并计数。
    单文件形状（勘察结论）：每行 {ts,k_id,kind,detail}，
    kind ∈ generate/edit/lint/smoke/write/commit/fail。"""
    tid = os.path.splitext(os.path.basename(path))[0]
    events, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                events.append({"ts": rec.get("ts", ""),
                               "kind": rec.get("kind", "?"),
                               "detail": str(rec.get("detail", ""))})
            except (ValueError, TypeError):
                bad += 1
    return tid, events, bad


def summarize(tid, events):
    """本地统计 + 构造喂给 9b 的事件流摘要（只读轨迹，不改任何生产文件）。"""
    files, smoke_pass, fail_cnt = [], False, 0
    for e in events:
        k, d = e["kind"], e["detail"]
        if k in ("write", "add"):
            m = re.match(r"([\w\\/.-]+)", d)
            if m and m.group(1) not in files:
                files.append(m.group(1))
        if k == "smoke" and d.startswith("PASS"):
            smoke_pass = True
        if k == "fail" or d.startswith("round") and "blocked" in d:
            fail_cnt += 1
    lines = ["%s %s %s" % (e["ts"], e["kind"], e["detail"][:MAX_DETAIL])
             for e in events[-MAX_EVENTS_IN_PROMPT:]]
    return {"tid": tid, "n_events": len(events), "files": files,
            "smoke_pass": smoke_pass, "fails": fail_cnt,
            "stream": "\n".join(lines)}


def call_ollama(prompt, timeout):
    """照抄 tools/agent_coder.py:365 ollama 通道配方（经 8080 网关）。"""
    est = (len(SYS_PROMPT) + len(prompt)) // 2 + 2048   # 063 判例：动态 num_ctx
    num_ctx = max(6144, ((est + 1023) // 1024) * 1024)
    payload = {"model": MODEL, "stream": False, "think": False,
               "format": "json",
               "options": {"num_ctx": num_ctx, "temperature": 0.3},
               "messages": [{"role": "system", "content": SYS_PROMPT},
                            {"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        GATEWAY, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with safe_urlopen(req, timeout=timeout) as resp:
        d = json.load(resp)
    return (d.get("message") or {}).get("content", "")


def score_one(summary, timeout):
    """单轨迹三维度打分。解析失败 → verdict=UNPARSED，保留 9b 原始回复前
    200 字供调 prompt（080 验收判据②）。"""
    raw = call_ollama("轨迹 %s（共%d事件，文件：%s）事件流：\n%s"
                      % (summary["tid"], summary["n_events"],
                         ",".join(summary["files"]) or "无", summary["stream"]),
                      timeout)
    obj = extract_json(raw)
    if not obj or not isinstance(obj.get("scores"), dict):
        return {"tid": summary["tid"], "scores": None, "verdict": "UNPARSED",
                "reason": "9b 输出无法解析为 JSON", "raw": raw[:200]}
    sc = obj["scores"]
    scores = {}
    for dim in ("增量", "契约", "收尾"):
        try:
            scores[dim] = max(0, min(10, int(sc.get(dim, 0))))
        except (ValueError, TypeError):
            return {"tid": summary["tid"], "scores": None,
                    "verdict": "UNPARSED",
                    "reason": "维度分缺失/非整数", "raw": raw[:200]}
    # 升级规则：任一维度 ≤4 → REVIEW（只做升级触发器，裁决权在 QA1/田丰）
    verdict = "REVIEW" if min(scores.values()) <= REVIEW_THRESHOLD else "PASS"
    return {"tid": summary["tid"], "scores": scores, "verdict": verdict,
            "reason": str(obj.get("reason", ""))[:120], "raw": raw[:200]}


def collect_traces(limit):
    """按 mtime 倒序收集全部轨迹文件，取最新 limit 条。"""
    paths = sorted(glob.glob(os.path.join(TRACE_DIR, "*", "*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    return paths[:limit]


def find_qa1_results(all_events):
    """重合率统计：找 QA1 实际退回记录。勘察确认当前轨迹无此类字段 →
    返回空 dict，调用方报告「QA1 结果字段缺失，重合率暂不可统计」。"""
    out = {}
    for tid, events in all_events.items():
        for e in events:
            if e["kind"] in QA_RESULT_KINDS or \
               ("退回" in e["detail"] and e["kind"].startswith("qa")):
                out.setdefault(tid, []).append(e["detail"][:80])
    return out


def overlap_rate(results, qa1):
    """Critic REVIEW 与 QA1 退回的重合率（QA1 字段存在时才有意义）。"""
    if not qa1:
        return None
    both = sum(1 for tid in qa1 if results.get(tid, {}).get("verdict") == "REVIEW")
    return {"qa1_returned": len(qa1), "critic_review": both,
            "rate": round(both / len(qa1) * 100, 1)}


def main():
    ap = argparse.ArgumentParser(description="K-ORG-080/N8 轨迹 Critic")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--tid", help="单轨迹 k_id")
    ap.add_argument("--report", help="输出 markdown 报告路径")
    ap.add_argument("--timeout", type=int, default=60, help="单条打分超时秒")
    args = ap.parse_args()

    # fail-open：网关不可达 → 提示退出 0（不炸不重试风暴）
    # 探活用 api/tags（轻量）；chat 冷启动慢，不拿它探活
    try:
        safe_urlopen(GATEWAY_PING, timeout=10).read(64)
    except Exception as e:
        print("[trace_critic] Ollama 网关 %s 不可达（%s）——跳过本轮打分，退出 0"
              % (GATEWAY_PING, str(e)[:120]))
        return 0

    if args.tid:
        hits = [p for p in collect_traces(9999)
                if os.path.splitext(os.path.basename(p))[0] == args.tid]
        if not hits:
            print("[trace_critic] 未找到轨迹 %s" % args.tid)
            return 0
        paths = hits
    else:
        paths = collect_traces(args.limit)
    if not paths:
        print("[trace_critic] %s 下无轨迹" % TRACE_DIR)
        return 0

    all_events, results, bad_lines = {}, {}, 0
    t0 = time.time()
    for i, p in enumerate(paths):
        tid, events, bad = load_trace(p)
        bad_lines += bad
        all_events[tid] = events
        if not events:
            results[tid] = {"tid": tid, "verdict": "UNPARSED",
                            "scores": None, "reason": "空轨迹", "raw": ""}
            continue
        try:
            results[tid] = score_one(summarize(tid, events), args.timeout)
        except Exception as e:   # fail-open：单条炸不拖垮整批
            results[tid] = {"tid": tid, "verdict": "UNPARSED", "scores": None,
                            "reason": "调用异常: %s" % str(e)[:100], "raw": ""}
        print("[%d/%d] %s -> %s" % (i + 1, len(paths), tid,
                                    results[tid]["verdict"]))
        if args.tid:  # 单轨迹模式：打完整 JSON 明细（验收判据②）
            print(json.dumps(results[tid], ensure_ascii=False, indent=2))
        if i < len(paths) - 1:
            time.sleep(INTERVAL)
    elapsed = time.time() - t0

    qa1 = find_qa1_results(all_events)
    ov = overlap_rate(results, qa1)
    dist = {}
    for r in results.values():
        dist[r["verdict"]] = dist.get(r["verdict"], 0) + 1
    reviews = sorted(r["tid"] for r in results.values()
                     if r["verdict"] == "REVIEW")
    print("\n== K-ORG-080/N8 轨迹 Critic 汇总 ==")
    print("分布: %s | 解析失败行: %d | 总耗时: %.1fs" %
          (dist, bad_lines, elapsed))
    if reviews:
        print("升级 QA1 深审清单: %s" % ", ".join(reviews))
    if ov is None:
        print("QA1 结果字段缺失，重合率暂不可统计（080 判据允许渐进）")
    else:
        print("重合率: %s" % ov)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write("# 轨迹 Critic 回放报告（K-ORG-080/N8）\n\n")
            f.write("- 时间: %s | 轨迹数: %d | 总耗时: %.1fs\n"
                    % (time.strftime("%Y-%m-%d %H:%M"), len(paths), elapsed))
            f.write("- verdict 分布: %s | JSONL 坏行: %d\n\n" % (dist, bad_lines))
            f.write("## 打分明细\n\n| tid | 增量 | 契约 | 收尾 | verdict | "
                    "reason |\n|---|---|---|---|---|---|\n")
            for tid in results:
                r = results[tid]
                s = r.get("scores")
                cells = ("%d/%d/%d" % (s["增量"], s["契约"], s["收尾"])
                         if s else "-")
                f.write("| %s | %s | %s | %s |\n" % (tid, cells,
                        r["verdict"], r["reason"].replace("|", "\\|")))
            f.write("\n## 升级 QA1 深审清单（REVIEW）\n\n")
            f.write("\n".join("- " + t for t in reviews) or "- 无")
            f.write("\n\n## UNPARSED 原始回复（前 200 字，供调 prompt）\n\n")
            for tid, r in results.items():
                if r["verdict"] == "UNPARSED":
                    f.write("- %s: `%s`\n" % (tid, r["raw"][:200]))
            f.write("\n## QA1 重合率\n\n")
            f.write("- %s\n" % ("统计: %s" % ov if ov else
                    "QA1 结果字段缺失，重合率暂不可统计（080 判据允许渐进）"))
        print("报告已写入 %s" % args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
