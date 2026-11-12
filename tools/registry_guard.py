#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-02 注册表—桥文件一致性守卫。

要解决的问题
------------
`news_collector/selection_universe.json` 是 xq 队列并集的**权威声明**
（`xq_stock_timeline.load_queue` 只读它）。但各 `target_queue_*.json` 由
桥生成器（`run_houfeng_intel.py` 等）**每日重生成**，重生成时会把
`xq_include_candidates` / `xq_include_cohorts` 写回 `false` —— 与注册表的
`true` 长期漂移（判例 R27 G3）。

后果：`load_queue` 读 registry，功能**不受影响**；但桥文件里那句 `false`
会让后来的人（或另一条线）误判「扩源没开」，审计时要反复对账。

本守卫：**只读、不改文件**——发现漂移只报 ALERT + 给处置建议。为什么不自动
改：桥脚本每日重生成会再次覆盖，自动改等于每天打架；正解在桥生成器一侧
（写入 registry 值，或删冗余字段），归归属线处理。

用法
----
    python tools/registry_guard.py                 # audit：报告，exit 0
    python tools/registry_guard.py --enforce       # 有漂移/缺失则 exit 1
    python tools/registry_guard.py --json          # 机器可读（供日更自检消费）
    python tools/registry_guard.py --selftest      # 内置场景自检，不碰文件
"""
import argparse
import io
import json
import os
import sys

NC_DIRNAME = "news_collector"
REG_NAME = "selection_universe.json"


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_registry(root):
    p = os.path.join(root, NC_DIRNAME, REG_NAME)
    with io.open(p, "r", encoding="utf-8") as f:
        return p, json.load(f)


def evaluate(sources, get_bridge):
    """核心比对。get_bridge(fname) -> dict（抛 FileNotFoundError 表示桥缺失）。

    返回 findings: [{source, field, want, actual, state, file}]
    state: OK | DRIFT | ABSENT | MISSING
    """
    findings = []
    for key, src in sorted(sources.items()):
        fld = src.get("bridge_flag_field")
        if not fld:
            continue  # 该源没有独立桥开关（如 chanlun 恒并入），不参与比对
        fname = src.get("file")
        want = bool(src.get("xq_include"))
        base = {"source": key, "field": fld, "want": want, "file": fname}
        try:
            bridge = get_bridge(fname)
        except (FileNotFoundError, OSError):
            findings.append(dict(base, actual=None, state="MISSING"))
            continue
        if fld not in bridge:
            findings.append(dict(base, actual=None, state="ABSENT"))
            continue
        actual = bool(bridge.get(fld))
        findings.append(dict(base, actual=actual,
                             state=("OK" if actual == want else "DRIFT")))
    return findings


def report(findings, as_json=False):
    if as_json:
        print(json.dumps({
            "checked": len(findings),
            "drift": [f for f in findings if f["state"] == "DRIFT"],
            "absent": [f for f in findings if f["state"] == "ABSENT"],
            "missing": [f for f in findings if f["state"] == "MISSING"],
            "ok": [f for f in findings if f["state"] == "OK"],
        }, ensure_ascii=False, indent=2))
        return
    bad = [f for f in findings if f["state"] != "OK"]
    for f in bad:
        tag = {"DRIFT": "ALERT", "ABSENT": "ALERT", "MISSING": "ALERT"}[f["state"]]
        print("[%s] %s  %s" % (tag, f["source"], f["state"]))
        print("        registry.xq_include = %s （权威）" % f["want"])
        if f["state"] == "DRIFT":
            print("        %s:%s = %s （桥文件实际）" % (f["file"], f["field"], f["actual"]))
            print("        影响：load_queue 只读 registry，xq 并集不受影响；但桥文件每日重生成会")
            print("              持续写回相反值，形成「看起来关了、其实开着」的误导。")
            print("        处置（归桥生成器线）：①写入 registry 值；或 ②删除该冗余字段。")
        elif f["state"] == "ABSENT":
            print("        %s 无字段 %s" % (f["file"], f["field"]))
            print("        处置：确认桥生成器是否已改字段名；如已废弃请同步删 registry 的 bridge_flag_field。")
        else:
            print("        %s 不存在或不可读" % f["file"])
    n_bad = len(bad)
    print("[registry-guard] 汇总：检查 %d 源，问题 %d（漂移 %d / 缺字段 %d / 缺文件 %d），正常 %d"
          % (len(findings), n_bad,
             sum(1 for f in findings if f["state"] == "DRIFT"),
             sum(1 for f in findings if f["state"] == "ABSENT"),
             sum(1 for f in findings if f["state"] == "MISSING"),
             sum(1 for f in findings if f["state"] == "OK")))


def check_and_report(root=None, quiet_ok=False, enforce=False, as_json=False):
    """供外部调用（如 run_xq_daily.py 启动自检）。永不抛异常、永不阻断采集。

    返回 True=全部一致，False=存在问题（调用方自行决定是否告警）。
    """
    try:
        root = root or repo_root()
        _, reg = load_registry(root)
        nc = os.path.join(root, NC_DIRNAME)

        def get_bridge(fname):
            with io.open(os.path.join(nc, fname), "r", encoding="utf-8") as f:
                return json.load(f)

        findings = evaluate(reg.get("sources") or {}, get_bridge)
        problems = [f for f in findings if f["state"] != "OK"]
        if problems or not quiet_ok:
            report(findings, as_json=as_json)
        return not problems
    except Exception as e:  # 守卫自身故障绝不反噬调用方
        print("[registry-guard] WARN 自检失败: %s" % str(e)[:120])
        return True


def selftest():
    """内置场景：不读真实文件，只验检测逻辑。返回 0=全通过。"""
    print("[registry-guard] selftest 开始（不碰真实文件）")
    fails = []

    # 场景 A：registry=true / 桥=false → DRIFT
    srcA = {"s1": {"file": "b.json", "xq_include": True, "bridge_flag_field": "f"}}
    fA = evaluate(srcA, lambda n: {"f": False})
    okA = (len(fA) == 1 and fA[0]["state"] == "DRIFT")
    print("  A 真/假 → DRIFT : %s (%s)" % ("PASS" if okA else "FAIL",
                                          fA[0]["state"] if fA else "无"))
    if not okA:
        fails.append("A")

    # 场景 B：真/真 → OK
    fB = evaluate(srcA, lambda n: {"f": True})
    okB = (len(fB) == 1 and fB[0]["state"] == "OK")
    print("  B 真/真 → OK    : %s" % ("PASS" if okB else "FAIL"))
    if not okB:
        fails.append("B")

    # 场景 C：桥缺字段 → ABSENT
    fC = evaluate(srcA, lambda n: {"other": 1})
    okC = (len(fC) == 1 and fC[0]["state"] == "ABSENT")
    print("  C 缺字段 → ABSENT: %s" % ("PASS" if okC else "FAIL"))
    if not okC:
        fails.append("C")

    # 场景 D：桥文件不存在 → MISSING
    def _raise(n):
        raise FileNotFoundError(n)
    fD = evaluate(srcA, _raise)
    okD = (len(fD) == 1 and fD[0]["state"] == "MISSING")
    print("  D 缺文件 → MISSING: %s" % ("PASS" if okD else "FAIL"))
    if not okD:
        fails.append("D")

    # 场景 E：源无 bridge_flag_field → 跳过（不产生 finding）
    srcE = {"s2": {"file": "c.json", "xq_include": True}}
    fE = evaluate(srcE, lambda n: {"f": False})
    okE = (len(fE) == 0)
    print("  E 无关卡 → 跳过   : %s" % ("PASS" if okE else "FAIL"))
    if not okE:
        fails.append("E")

    if fails:
        print("[registry-guard] selftest FAILED: %s" % ",".join(fails))
        return 1
    print("[registry-guard] selftest 全部通过（5/5）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="注册表—桥文件一致性守卫（R-02）")
    ap.add_argument("--enforce", action="store_true", help="有漂移/缺失则 exit 1（默认 audit）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--selftest", action="store_true", help="内置场景自检，不碰真实文件")
    ap.add_argument("--quiet-ok", action="store_true", help="全部一致时不打印汇总")
    ap.add_argument("--root", default=None, help="仓库根（默认由脚本位置推导；测试时可指向影子根）")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if a.selftest:
        return selftest()

    root = os.path.abspath(a.root) if a.root else repo_root()
    reg_path, reg = load_registry(root)
    nc = os.path.join(root, NC_DIRNAME)

    def get_bridge(fname):
        with io.open(os.path.join(nc, fname), "r", encoding="utf-8") as f:
            return json.load(f)

    if not a.json:
        print("[registry-guard] 权威：%s" % os.path.relpath(reg_path, root))
    findings = evaluate(reg.get("sources") or {}, get_bridge)
    problems = [f for f in findings if f["state"] != "OK"]

    if problems or not a.quiet_ok:
        report(findings, as_json=a.json)

    if problems and a.enforce:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
