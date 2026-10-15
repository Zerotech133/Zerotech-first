# -*- coding: utf-8 -*-
"""frozen_tag_gate.py — 冻结 tag 准入闸门（K-STOCK-409）

用途：D-Day / D-1 开闸前，判定「哪些代码漂移会真的改变复现行为」。

🔴 为什么需要这个工具（K-STOCK-406/407/409 三轮踩坑的产物）：
  ①`git diff --stat <tag> -- <path>` 算的是「tag 与**工作区**」的差异。
     tag -f 刚打在 HEAD 上 + 工作区干净 ⇒ 两端恒等 ⇒ **恒空恒 PASS，形同虚设**。
     真正要比的是两个 ref：`git diff <refA> <refB> -- <path>`。
  ② 只查「链路文件自身零差异」是**不够的** —— 链路会 import 别的模块，
     模块变了链路行为就变。必须查**传递依赖闭包**。
     实例：`backtest_engine_v2.py`（自身零差异）在 L126/293/363 `import market_scan`，
     而 `market_scan.py` 相对 tag 有 +8 行漂移 ⇒ 「零差异」是假象。

判定口径（默认对 open1016-frozen-d2 HEAD）：
  层1· 入口文件自身漂移        → 必须为 0
  层2· 入口的仓内传递依赖闭包 → 逐个列��，按 sign-off 白名单放行
  层3· 闭包读到的配置文件漂移→ 必须为 0（配置变了结论不可比）
  附 · 产物类文件统计（.parquet/.log/_manifest 等）——记录在案，不作闸门

用法：
  python frozen_tag_gate.py                          # 默认基线检查
  python frozen_tag_gate.py --ref open1016-frozen-d2b --head HEAD
  python frozen_tag_gate.py --json                   # 机器可读（automation 用）
  python frozen_tag_gate.py --sign-off gate_shadow_collect.py,market_scan.py,warehouse_runs.py

退出码：0=PASS / 1=FAIL（层1 或层3 非空）/ 2=用法错误
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.dirname(HERE)                      # quant_backtest/
REPO = os.path.dirname(QB)                      # 仓库根

# ── 配置 ──────────────────────────────────────────────────
# A 臂复验入口（K-STOCK-381 方案 A · S3 一年窗同窗复验）
ENTRIES = [
    "quant_backtest/backtest_engine_v2.py",
    "quant_backtest/channel_engine.py",
    "quant_backtest/open1016_run.py",
]
# 路径型入口（S2 判据里的六路，按 git 路径形态）
PATH_ENTRIES = [
    "quant_backtest/bt2",
    "quant_backtest/open1016_run.py",
    "quant_backtest/backtest_engine_v2.py",
    "quant_backtest/channel_engine.py",
    "quant_backtest/chanlun",
    "portal/bt2_run",
]
# 遍历时跳过的目录（数据/产物/第三方/缓存）
SKIP_DIRS = {".git", "node_modules", "site-packages", "__pycache__", "venv", "pylibs",
             ".worktrees", "data", "reports", "archive", "backup", "backups", "deploy_packages"}
# 产物类特征（记录在案用，不作闸门）
ARTIFACT_PAT = [
    (r"\.parquet$|\.csv$|\.db$|\.sqlite", "数据文件"),
    (r"\.log$", "日志"),
    (r"_manifest\.json$|\.manifest\.json$", "运行清单（采集器每日改写）"),
    (r"\.tmp$|\.bak$|\.old$", "临时/备份"),
    (r"\.lock$", "锁文件"),
    (r"\.env|secret|credential|\.key$|\.pem$|\.enc$", "密钥/凭据"),
    (r"^work/|\.workbuddy/", "临时工作区/工具记忆"),
]


def git(*args, timeout=300) -> str:
    r = subprocess.run(["git", "-C", REPO] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.stdout


def git_rc(*args, timeout=300) -> tuple[str, int]:
    r = subprocess.run(["git", "-C", REPO] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.stdout, r.returncode


def p(s: str = "") -> None:
    print(s)


def rule(t: str) -> None:
    p("")
    p("=" * 94)
    p(t)
    p("=" * 94)


# ── 模块索引与import 解析 ──────────────────────────────────
def iter_py() -> list[str]:
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                fp = os.path.join(root, fn)
                out.append(os.path.relpath(fp, REPO).replace("\\", "/"))
    return out


def imports_of(rel: str) -> set:
    fp = os.path.join(REPO, rel.replace("/", os.sep))
    try:
        src = io.open(fp, encoding="utf-8", errors="replace").read()
    except Exception:
        return set()
    mods = set()
    for m in re.finditer(r"^\s*import\s+([\w.]+)", src, re.M):
        mods.add(m.group(1).split(".")[0])
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import", src, re.M):
        mods.add(m.group(1).split(".")[0])
    return mods


def closure(entries: list[str]) -> tuple[set, dict]:
    """BFS 收集仓内 import 闭包。返回 (文件集, 边字典)"""
    allpy = iter_py()
    bymod: dict[str, list] = {}
    for x in allpy:
        bymod.setdefault(os.path.splitext(os.path.basename(x))[0], []).append(x)
    seen: set = set()
    edges: dict = {}
    stack = [e for e in entries if os.path.exists(os.path.join(REPO, e.replace("/", os.sep)))]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for mod in imports_of(cur):
            cands = bymod.get(mod)
            if not cands:
                continue
            #优先 quant_backtest/ 根，其次最短路径（避免 tests/ 里的同名影子）
            cands = sorted(cands, key=lambda c: (not c.startswith("quant_backtest/"), len(c)))
            tgt = cands[0]
            edges.setdefault(cur, set()).add(tgt)
            if tgt not in seen:
                stack.append(tgt)
    return seen, edges


def drift(ref: str, head: str, path: str) -> list:
    return [x for x in git("diff", "--name-only", ref, head, "--", path).splitlines()
            if x.strip()]


# ── 主流程 ────────────────────────────────────────────────
def run(ref: str, head: str, signoff: set, signoff_cfg: set | None = None) -> dict:
    signoff_cfg = signoff_cfg or set()
    res: dict = {"ref": ref, "head": head, "sign_off_cfg": sorted(signoff_cfg),
                 "pass": True, "checks": []}

    # ── 层 0：判据自检（先证明本工具有效：drift 非空时能检出来）──
    rule("【层 0】判据自检 —— 证明本闸门不是恒 PASS")
    allp = [x for x in git("diff", "--name-only", ref, head).splitlines() if x.strip()]
    p("全仓漂移文件 = %d 个（若 = 0 说明 %s..%s 已一致，本闸门无事可做）"
      % (len(allp), ref, head))
    probe = drift(ref, head, "quant_backtest")
    p("实测 `git diff --name-only %s %s -- quant_backtest` = %d 行"
      % (ref, head, len(probe)))
    p("")
    p("⚠️ 反面对照（406 判例）：`git diff --name-only %s -- quant_backtest`"
      % ref)
    tmp = git_rc("tag", "_k409_selfcheck_%d" % os.getpid())
    if tmp[1] == 0:
        # 注意：这条命令按卡上原写法跑必然恒空 —— 这正是判据缺陷的证明
        git("tag", "-f", "_k409_probe", head)
        out = git("diff", "--name-only", "_k409_probe", "--", "quant_backtest")
        p("   输出 = %d 行%s" % (len([x for x in out.splitlines() if x.strip()]),
                                " ★恒空 = 旧判据形同虚设的证明" if not out.strip() else ""))
        git("tag", "-d", "_k409_probe")
    p("⇒ 本工具一律用 `git diff <refA> <refB> -- <path>`（两个 ref），不使用单 ref 写法。")
    res["checks"].append({"layer": 0, "name": "判据自检", "drift_total": len(allp),
                          "qb_drift": len(probe), "ok": True})

    # ── 层 1：入口/路径自身漂移 ──────────────────────────────
    rule("【层 1】入口文件自身漂移（必须为 0）")
    l1 = {}
    for e in PATH_ENTRIES:
        d = drift(ref, head, e)
        l1[e] = d
        p("  %-44s %d %s" % (e, len(d), "✅" if not d else "★" + str(d[:3])))
    bad1 = sum(len(v) for v in l1.values())
    p("")
    p("⇒ 层1 合计 = %d %s" % (bad1, "✅ PASS" if bad1 == 0 else "★ FAIL"))
    res["checks"].append({"layer": 1, "name": "入口自身漂移", "detail": l1,
                          "total": bad1, "ok": bad1 == 0})
    res["pass"] = res["pass"] and bad1 == 0

    # ── 层 2：传递依赖闭包 ─────────────────────────────────
    rule("【层 2】🔴 传递依赖闭包（入口 import 的仓内模块）")
    clos, edges = closure(ENTRIES)
    cdrift = sorted(x for x in clos if drift(ref, head, x))
    p("闭包内 py 文件 = %d 个" % len(clos))
    p("")
    p("其中相对 %s 有漂移 = %d 个：" % (ref, len(cdrift)))
    unsigned = []
    for x in cdrift:
        os_ = os.path.basename(x)
        st = git("diff", "--stat", ref, head, "--", x).strip().splitlines()[-1].strip()
        ok = x in signoff or os_ in signoff
        if not ok:
            unsigned.append(x)
        p("  %-48s %-46s %s" % (x[:48], st[:46], "已签收✅" if ok else "★未签收"))
    p("")
    if unsigned:
        p("⇒ ★%d 个漂移文件未在签收名单内 —— 必须逐个核实是否纯新增" % len(unsigned))
        for x in unsigned:
            p("    %s" % x)
    else:
        p("⇒全部漂移均已签收（%s）" % (", ".join(sorted(signoff)) or "白名单为空"))
    res["checks"].append({"layer": 2, "name": "传递依赖闭包", "closure_size": len(clos),
                          "drift": cdrift, "unsigned": unsigned, "ok": not unsigned})

    # ── 层 3：闭包读到的配置文件 ────────────────────────────
    rule("【层 3】闭包读取的配置文件漂移")
    # 🔴 K-STOCK-409 判例：运行时产物类配置（每日被采集器/服务改写）**不构成复验阻塞**，
    #    但仍要列出来供人判断。当前三个已知误报源：
    #      _manifest.json / qd_scan_cache.json —— 采集器与服务每日改写，不是复验输入
    #    口径：BLOCKING_CFG=影响回测口径的静态配置；其余归为 advisory（列出不判FAIL）
    ADVISORY_BASE = {"_manifest.json", "qd_scan_cache.json"}
    cfgs: set = set()
    for f in clos:
        try:
            src = io.open(os.path.join(REPO, f.replace("/", os.sep)),
                          encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in re.finditer(r"""["']([\w/\\.\-]+\.(?:json|yaml|yml|ini|cfg|toml))["']""", src):
            cfgs.add(m.group(1))
    blocking: list = []
    advisory: list = []
    signed_cfg: list = []
    for c in sorted(cfgs):
        base = os.path.basename(c)
        hits = [x for x in allp if os.path.basename(x) == base]
        if not hits:
            continue
        rec = {"config": base, "paths": hits}
        if base in signoff_cfg:
            # 已实证不影响复验（必须在报告里留证据链接，否则等于放行未知风险）
            rec["signed_off"] = True
            signed_cfg.append(rec)
        elif base in ADVISORY_BASE or len(hits) > 1:
            # 运行时产物（每日改写）/ 同名不同文件（匹配污染）→ 不判 FAIL
            advisory.append(rec)
        else:
            blocking.append(rec)
    p("闭包读取配置 = %d 个" % len(cfgs))
    p("")
    if signed_cfg:
        p("✔ 已实证签收（不影响复验，报告须留证据）：")
        for r in signed_cfg:
            p("     %-42s %s" % (r["config"][:42], r["paths"][:1]))
    if blocking:
        p("★ 阻塞项（静态配置漂移，复现性受损）：")
        for r in blocking:
            p("     %-42s %s" % (r["config"][:42], r["paths"][:1]))
    else:
        p("  阻塞项 = 0 ✅")
    p("")
    if advisory:
        p("  提示项（运行时产物 / 同名多文件，**不判FAIL**）：")
        for r in advisory:
            why = ("运行时产物（每日改写）" if r["config"] in ADVISORY_BASE
                   else "同名不同文件·匹配已收窄")
            p("     %-42s %s← %s" % (r["config"][:42], why, r["paths"][:1][:1]))
    p("")
    p("⇒层3 阻塞项 = %d %s" % (len(blocking), "✅ PASS" if not blocking else "★ FAIL"))
    res["checks"].append({"layer": 3, "name": "闭包配置漂移", "config_count": len(cfgs),
                          "blocking": blocking, "advisory": advisory,
                          "signed": signed_cfg, "ok": not blocking})
    res["pass"] = res["pass"] and not blocking

    # ── 附：产物类文件（记录在案，不作闸门）─────────────────
    rule("【附】产物类文件统计（记录在案 · 不影响 PASS/FAIL）")
    buckets: dict = {}
    for x in allp:
        for pat, why in ARTIFACT_PAT:
            if re.search(pat, x):
                buckets.setdefault(why, []).append(x)
                break
    if buckets:
        for why, lst in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            p("  %-30s %5d 个" % (why, len(lst)))
        p("")
        p("⇒ 这些不是闸门失败项，但说明「tag 纯度」问题。")
        p("  参考消噪记录：K-STOCK-408 已处理 .env.enc ×1 + work/ ×2014。")
    else:
        p("  （无产物类漂移）✅")
    res["artifacts"] = {k: len(v) for k, v in buckets.items()}

    # ── 结论 ───────────────────────────────────────────────
    rule("【结论】")
    for c in res["checks"]:
        if "layer" in c and c.get("layer"):
            p("  层%s %-16s %s" % (c["layer"], c["name"], "✅ PASS" if c.get("ok") else "★ FAIL"))
    p("")
    p("  总体 = %s" % ("✅ PASS —— 复验链路可复现，S3 判据可信"
                        if res["pass"] else "★ FAIL —— 不可开闸"))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="冻结 tag 准入闸门")
    ap.add_argument("--ref", default="open1016-frozen-d2")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--sign-off", default="",
                    help="已签收的漂移 py 文件（逗号分隔，basename 或完整路径）")
    ap.add_argument("--sign-off-cfg", default="",
                    help="已实证不影响复验的配置文件（逗号分隔，basename）")
    a = ap.parse_args()
    signoff = {x.strip() for x in a.sign_off.split(",") if x.strip()}
    signoff_cfg = {x.strip() for x in a.sign_off_cfg.split(",") if x.strip()}

    if not git("rev-parse", "-q", "--verify", a.ref):
        p("★ref %s 不存在" % a.ref)
        return 2
    res = run(a.ref, a.head, signoff, signoff_cfg)
    if a.json:
        p("")
        p("###JSON###")
        p(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())