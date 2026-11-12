# -*- coding: utf-8 -*-
"""11-13 反哺：真实链路上验证 --skip-pick 复用校验（不是只测纯函数）。

两次运行：
  A 正常复用：当前产物 data_day=目标日、口径 v3 → 必须放行（rc=1，有告警）
  B 旧票复用：把昨天的产物冒充今天 → 必须 rc=3 阻断，且 steps 里留 stale_reuse

B 会临时替换产物文件，finally 按字节恢复并复核 data_day，
绝不把「旧票」留在生产路径上。日期字面量一律码点构造。
"""
import io
import json
import os
import shutil
import subprocess
import sys

ROOT = r"C:\项目\老田的AI助手"
DELIV = r"C:\项目\deliverables"
RUN = os.path.join(ROOT, "tools", "preopen", "run_preopen.py")
PICK_OK = os.path.join(DELIV, "preopen_pick15_v2_adj_check_20261112.json")
PICK_OLD = os.path.join(DELIV, "preopen_pick15_v2_adj_check_20261111.json")
BACKUP = os.path.join(ROOT, "var", "audit", "_bak_pick15_20261112.json")
OUT_A = os.path.join(ROOT, "var", "audit", "_reuse_ok.json")
OUT_B = os.path.join(ROOT, "var", "audit", "_reuse_blocked.json")
D = lambda *c: "".join(chr(c) for c in c)
DAY = D(50, 48, 50, 54, 49, 49, 49, 50)


def run(out):
    cmd = [sys.executable, "-X", "utf8", RUN, "--date", DAY,
           "--skip-pick", "--json-out", out]
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=420)
    txt = p.stdout.decode("utf-8", "replace")
    doc = {}
    if os.path.exists(out):
        doc = json.load(io.open(out, encoding="utf-8"))
    return p.returncode, doc, txt


def main():
    print("目标日码点复核:", [ord(c) for c in DAY], "->", DAY)
    rc_a, a, _ = run(OUT_A)
    reused = (a.get("pick_provenance") or {}).get("reused")
    print("\n[A 正常复用] exit=%d  rc=%s  reused=%s"
          % (rc_a, a.get("rc"), reused))
    ok_a = (a.get("rc") == 1 and reused is True)

    shutil.copy2(PICK_OK, BACKUP)
    try:
        shutil.copy2(PICK_OLD, PICK_OK)
        rc_b, b, txt_b = run(OUT_B)
        steps = b.get("steps") or []
        stale = any(s.get("error") == "stale_reuse" for s in steps)
        print("[B 旧票复用] exit=%d  rc=%s  stale_reuse=%s"
              % (rc_b, b.get("rc"), stale))
        for ln in txt_b.splitlines():
            if "阻断" in ln or "请去掉" in ln:
                print("   ", ln.strip())
        ok_b = (b.get("rc") == 3 and stale)
    finally:
        shutil.copy2(BACKUP, PICK_OK)
        d = json.load(io.open(PICK_OK, encoding="utf-8"))
        print("[恢复] data_day=%s  chain_policy_v=%s  top15=%d"
              % (d.get("data_day"), d.get("chain_policy_v"),
                 len(d.get("top15_adj") or [])))
        assert d.get("data_day") == DAY, "恢复失败：产物仍是被调包的旧票"

    print("\n结论：正常复用%s，旧票阻断%s"
          % ("放行 OK" if ok_a else "异常", "生效 OK" if ok_b else "失效"))
    return 0 if (ok_a and ok_b) else 2


if __name__ == "__main__":
    raise SystemExit(main())
