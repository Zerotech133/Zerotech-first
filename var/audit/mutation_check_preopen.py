# -*- coding: utf-8 -*-
"""盘前链路修复的变异检验：把被测逻辑改回旧行为，断言必须失败。

安全约束：
  · 按字节读写，不碰换行格式（audit_preopen.py 是 LF，别被转成 CRLF）。
  · 替换前断言锚点 count==1，替换后断言新串存在。
  · 每个变异窗口只用几秒；恢复前校验磁盘内容仍是我写进去的变异版，
    若被并行会话改过就不覆盖，直接报错让人来判断。
  · finally 保证恢复，最后复跑全套测试确认回到全绿。
"""
import io
import os
import subprocess
import sys

ROOT = r"C:\项目\老田的AI助手"
PY = sys.executable
AUDIT = os.path.join(ROOT, "tools", "preopen", "audit_preopen.py")
RUN = os.path.join(ROOT, "tools", "preopen", "run_preopen.py")
TEST = os.path.join(ROOT, "tools", "tests", "test_preopen_gate_fixes.py")

MUTATIONS = [
    ("T+1 源回到硬失败", AUDIT,
     'elif sp["role"] in ("soft", "t_plus_1"):',
     'elif sp["role"] == "soft":',
     "test_t_plus_1_lag_degrades_not_blocks"),
    ("历史日差异回到无条件告警", AUDIT,
     "    if not is_target_day:\n        cc[\"note\"]",
     "    if False:\n        cc[\"note\"]",
     "test_stale_official_table_is_history_not_today_problem"),
    ("复用校验去掉数据日判断", RUN,
     '    if pv.get("data_day") != day:\n        return "产物数据日 %s 与目标日 %s 不符" % (pv.get("data_day"), day)\n',
     "",
     "test_reuse_rejection_blocks_stale_day"),
    ("行业空表回到无保护索引", os.path.join(ROOT, "tools", "preopen",
                                    "build_preopen_report.py"),
     '    if d.get("sector") and d["sector"]["top"] and d["sector"]["bottom"]:\n'
     '        s = d["sector"]\n        t = s["top"][0]',
     '    if d.get("sector"):\n        s = d["sector"]\n        t = s["top"][0]',
     "test_derive_survives_empty_sector"),

    # 收盘复盘 → 事件账：三条口径防线各做一次变异。
    ("观测挂回执行日（虚增流程账）", os.path.join(ROOT, "tools", "review",
                                        "attach_close_review.py"),
     '    signal_day = str(rev["data_day"])',
     '    signal_day = str(rev["exec_day"])',
     "test_attach_signal_day_is_data_day"),
    ("单日观测凑成三次", os.path.join(ROOT, "tools", "review",
                              "attach_close_review.py"),
     '        "independent_observations": 1,',
     '        "independent_observations": 3,',
     "test_attach_single_independent_observation"),
    ("秩相关退化成原始值相关", os.path.join(ROOT, "tools", "review",
                                  "attach_close_review.py"),
     '    return _pearson(_ranks(x), _ranks(y))',
     '    return _pearson(x, y)',
     "test_spearman_is_rank_not_raw"),
    ("降级观测混进主 quorum", os.path.join(ROOT, "tools", "review",
                                  "attach_close_review.py"),
     '        rho_scope, pattern, n_pool = "top15_only_fallback", PATTERN_TOP15_FALLBACK, len(rows)',
     '        rho_scope, pattern, n_pool = "top15_only_fallback", PATTERN, len(rows)',
     "test_fallback_id_differs"),
]


def run_pytest(node):
    cmd = [PY, "-m", "pytest", TEST + "::" + node, "-q"] if node else \
          [PY, "-m", "pytest", TEST, "-q"]
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=180)
    out = p.stdout.decode("utf-8", "replace")
    tail = "\n".join([x for x in out.splitlines() if x.strip()][-6:])
    return p.returncode, tail


def main():
    results = []
    for name, path, old, new, node in MUTATIONS:
        raw = open(path, "rb").read()
        # 本仓部分文件是 CRLF（Edit 工具写入所致），含换行的锚点必须自适应，
        # 否则 count=0 会被安全地 SKIP，却让人误以为变异已经做过。
        nl = b"\r\n" if b"\r\n" in raw else b"\n"
        ob = old.encode("utf-8").replace(b"\n", nl)
        nb = new.encode("utf-8").replace(b"\n", nl)
        if raw.count(ob) != 1:
            print("[SKIP] %s：锚点 count=%d（不是 1），拒绝变异"
                  % (name, raw.count(ob)))
            results.append((name, "SKIP"))
            continue
        mutated = raw.replace(ob, nb, 1)
        assert nb in mutated or new == "", "替换后新串不存在"
        try:
            with open(path, "wb") as f:
                f.write(mutated)
            rc, tail = run_pytest(node)
            killed = (rc != 0)
            print("[%s] %s：pytest rc=%d %s"
                  % ("KILLED" if killed else "SURVIVED", name, rc,
                     "" if killed else "← 断言没抓到旧行为，是假通过"))
            if not killed:
                print("       " + tail.replace("\n", " | "))
            results.append((name, "KILLED" if killed else "SURVIVED"))
        finally:
            cur = open(path, "rb").read()
            if cur != mutated:
                print("[ERROR] %s 在变异窗口内被其他进程改过，拒绝覆盖！" % path)
                raise SystemExit(9)
            with open(path, "wb") as f:
                f.write(raw)
            print("       已按字节恢复 %s" % os.path.basename(path))

    rc, tail = run_pytest(None)
    print("\n恢复后全套测试：rc=%d  %s" % (rc, tail.replace("\n", " | ")))
    survived = [n for n, v in results if v != "KILLED"]
    if survived or rc != 0:
        print("变异检验未通过：%s" % survived)
        return 2
    print("变异检验通过：%d/%d 个变异全部被断言抓住"
          % (len(results), len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
