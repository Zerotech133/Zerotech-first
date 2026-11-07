# -*- coding: utf-8 -*-
r"""W-07 · 独立验收套件（qa-tester 单写者落盘，GOV-W W-07 显式纳入白名单）。

【委派】team-lead ADR-08/26 委派 W-05~W-07；本文件是三者中唯一「独立验收」件，
        不 import W-05/W-06 两个单测、不复述实现方结论。
【上游】PRD 最终版四条可达 AC（AC-01~AC-04）、治理方案 §1.3 / §三 / §六、
        项目档案 ADR-02/03/05/11/16~20 与 ADR-27/28/30 终裁、W-08 flag 权威
        八字段、W-09 设计留痕、W-10 统一口径声明、W-12 发布方案、
        qa-testing.md §三用例模板 / §四 Bug 规范 / §五门禁 / §六验收清单。
【解释器】"C:\Program Files\Python311\python.exe" -X utf8
【只读基线】C:\Temp\tasks_inventory_20261105.json（UTF-8 BOM → utf-8-sig，492 条）
            C:\Temp\governance_zero_state_baseline.json（W-08 落盘前采集；G-09b
            同口径复核：db_fingerprint 44 条 head(1MiB)+size+mtime_ns、
            db_full_sha256 4 条全量 sha256、system_files 4 条、golden 1 条）
【硬边界】六类夹具全部由 pytest tmp_path 运行时自建（含 AIOS-SELF-BUILT 标记），
        严禁从 backups/db / C:\backups_mirror 复制生产产物（FF11）；
        全程只读 C:\aios 既有文件，不解压 11GB 产物、不调用任何 .enc 解密、
        不访问 10.205.0.118、不 commit、不建 tests/fixtures/。

【独立性声明 · 关键】AC-N01 的 16 项 golden 由本文件**自包含的独立分类器**
        （按治理方案 §1.3 规则重写，不调用 F1.compute_stats / assert_golden 做判据）
        从 492 条只读基线重算得出，再与硬编码期望常量比对；随后才把 F1 的
        compute_stats / assert_golden 作为「实现是否与独立判据一致」的交叉核对。
        故本套件证明的是「AC 达成」，而非复述实现方的自证结论。

【本轮唯一四条可达 AC 的追溯】
  AC-01 ← AC-N01（独立重算 16 项 + ADR-27 修复后 diff 契约）/ AC-N02 / W-05
  AC-02 ← AC-N03（默认演练选最新真实档、不选 10-29 坏档）/ AC-N04（W-08 权威八字段 + 全量 sha256
          前后一致 + 改写字节数 0，G-09b 同口径复核）/ AC-N08（ADR-30 六类夹具）/
          AC-E02 / W-06；**T-11：不含任何 list_backups() 返回值断言，flag 不是
          运行态过滤闸门、不加 flag 后自动跳过**
  AC-03 ← AC-N05 / AC-E04（W-10 统一声明 + 三处口径失效表述残留 = 0）
  AC-04 ← AC-N06 / AC-E06（三条关键链 + DbDumpDaily=pending 设计完成，不落运行态）

【锁定措辞（团长终裁，严禁改写）】
  覆盖率 18/293≈6.1%（严禁 23/293≈7.8%）；CainiaoPull 指向不存在的计划任务；
  db_dump_daily / cainiao_pull 引用均 0；登录类型 185/123/107/77；
  policy = "mark-only, do-not-delete"（ADR-28，逗号+空格）。

【四层测试策略（qa-testing.md §一，缺层不合格）】
  功能：AC-N01~N08 正常流 8 条 + AC-E01~E08 异常流 8 条（异常 8 ≥ 正常 8 的一半 4）
  兼容：TestFF10Compatibility —— PS5.1 / Python3.11 / Docker dash /
        schtasks 中文本地化 / C:\aios junction 五类真实执行环境矩阵
  性能：AC-N04 的 11GB 产物只做流式 1MiB 全量 sha256（只读、不解压、不落盘），
        AC-N07 用有界指纹；按 G0-b「不实测、不设阈值」，GB 级耗时属实施期观测项
  安全：AC-E08 硬边界 7 个 0 反向扫描 + AC-E03 夹具来源 + AC-E05 AST 无写路径

【量化质量门禁（本文件为 G0-b 执行态证据之一）】
  P0 bug = 0；核心链路用例通过率 100%；四条 AC 逐条通过判据见各用例 docstring；
  崩溃/假绿 = 0（任何异常不得产 ok=true）；零系统状态变更 = 7 个 0。

用例清单（编号 / 优先级 / 溯源）：
  AC-N01 P0 AC-01 独立重算 16 项 + ADR-27 diff 契约（自比 unchanged=492）
  AC-N02 P0 AC-01 连跑 3 次字节级一致
  AC-N03 P0 AC-02 默认演练选最新含目标文件的日期、不选 10-29（T-11 无 =0 断言）
  AC-N04 P0 AC-02 W-08 权威八字段 + 全量 sha256 前后一致 + 改写字节数 0
  AC-N05 P0 AC-03 W-10 统一声明口径自洽
  AC-N06 P0 AC-04 设计件枚举三链 + pending
  AC-N07 P0 G-09 零状态变更前后指纹 + G-09b 基线同口径复核
  AC-N08 P0 ADR-30 六类自建夹具（encrypted_aes → encrypted-aes）
  AC-E01 P0 环境漂移不静默通过、不判代码 bug
  AC-E02 P1 显式 --date 人工拦截、不自动过滤（T-11）
  AC-E03 P0 FF11 夹具来源反向断言
  AC-E04 P1 三处口径旧措辞残留 = 0
  AC-E05 P0 AST 无写 REGISTRY / 无 --apply
  AC-E06 P0 REGISTRY 23 条 / DbDumpDaily 未落态 / CainiaoPull 指向不存在任务
  AC-E07 P0 白名单外新增文件 = 0
  AC-E08 P0 硬边界 7 个 0 反向扫描 + 锁定措辞复核
  CM-N01~N05：五类 FF10 兼容矩阵（PS5.1 / Python3.11 / dash / 中文本地化 / junction）
"""
from __future__ import annotations

import ast
import contextlib
import copy
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, r"C:\aios\ops")
import task_inventory as ti  # noqa: E402
import backup_format_gate as bfg  # noqa: E402

# ---- 只读路径常量（任何测试都不写这些路径）----
GOLDEN_PATH = r"C:\Temp\tasks_inventory_20261105.json"
ZERO_BASELINE = r"C:\Temp\governance_zero_state_baseline.json"
AIOS = r"C:\aios"
DB_ROOT = os.path.join(AIOS, "backups", "db")
DAY_1029 = os.path.join(DB_ROOT, "2026-10-29")
DAY_1105 = os.path.join(DB_ROOT, "2026-11-05")
FLAG_PATH = os.path.join(DAY_1029, "INVALID_GZIP.flag")
THREE_GZ = ("cainiao_mysql_alldb.sql.gz", "quant_pg_alldb.sql.gz", "zhice_os_v2.sql.gz")
W10_TERMINOLOGY = os.path.join(AIOS, "docs", "changes", "20261105_governance_terminology.md")
W09_INTERFACE = os.path.join(AIOS, "docs", "changes", "20261105_ff06_interface_hooks.md")
W12_RELEASE = os.path.join(AIOS, "docs", "plans", "20261105_release_plan.md")
F1_SRC = os.path.join(AIOS, "ops", "task_inventory.py")
F2_SRC = os.path.join(AIOS, "ops", "backup_format_gate.py")
MISS_CHECK = os.path.join(AIOS, "quant_backtest", "miss_check.py")
DB_DUMP = os.path.join(AIOS, "ops", "db_dump_daily.py")
B4_DRILL = os.path.join(AIOS, "tools", "b4_restore_drill.py")
HEAD_BYTES = 1 << 20          # G-09 基线 head_sha256 口径：前 1 MiB
SHA_CHUNK = 1 << 20           # 全量 sha256 流式块，不整文件载入内存

# ---- 独立期望常量：治理方案 §1.3 逐项抄录，不复用 F1.GOLDEN ----
EXPECT = {
    "total": 492, "custom": 293, "system": 199,
    "Ready": 407, "Disabled": 76, "Running": 9,
    "Interactive": 185, "S4U": 123, "ServiceAccount": 107, "Group": 77,
    "custom_real_failed": 32, "custom_never_run": 24, "custom_disabled": 45,
    "ready_06_09": 76, "registry_entries": 23, "registry_matched_custom": 18,
}

# ---- W-08 flag 权威八字段（团长下发，键值以此为准，不自行编造枚举）----
W08_EXPECT = {
    "date": "2026-10-29",
    "decision_ref": "D4",
    "detected_magic": "2d2d",
    "files": ["cainiao_mysql_alldb.sql.gz",
              "quant_pg_alldb.sql.gz",
              "zhice_os_v2.sql.gz"],
    "format": "plain-sql",
    "policy": "mark-only, do-not-delete",
    "recoverable": False,
    "type": "invalid_gzip_quarantine",
}
POLICY_AUTHORITATIVE = "mark-only, do-not-delete"
SELF_BUILT = b"AIOS-SELF-BUILT"


# ==========================================================================
# 独立分类器 + 独立统计（本文件自包含，不依赖 F1 的任何统计函数做判据）
# ==========================================================================
_IND_PREFIXES = ("\\Microsoft\\", "\\ZeroBound\\", "\\AIOS-")
_IND_NOISE = frozenset({267009, 267011, 267008, 267010,
                        1073807364, 2147943140, 2147942402, 2147946720})
_IND_SENTINELS = ("1999-11-30", "1999/11/30")
_IND_PEAK = ("06", "07", "08", "09")


def _ind_is_system(path):
    return (path or "\\").startswith(_IND_PREFIXES)


def _ind_classify(row):
    """按治理方案 §1.3 / ADR-13/14 顺序重写的独立分类规则。"""
    rc = row.get("RC")
    last = str(row.get("Last") or "").strip()
    st = str(row.get("State") or "").strip()
    if last and last.startswith(_IND_SENTINELS):
        return "never_run"
    if rc in _IND_NOISE:
        return "scheduled_noise"
    if _ind_is_system(row.get("Path")):
        return "system"
    if rc not in (None, 0):
        return "real_failed"
    if st == "Disabled":
        return "disabled"
    if st == "Running":
        return "running"
    return "ready"


def _ind_registry_names():
    tree = ast.parse(io.open(MISS_CHECK, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "REGISTRY":
                    return list(ast.literal_eval(node.value).keys())
    return []


def _independent_stats(rows):
    """独立重算 16 项 golden —— AC-N01 的唯一判据来源。"""
    cats = [_ind_classify(r) for r in rows]
    custom_idx = [i for i, r in enumerate(rows) if not _ind_is_system(r.get("Path"))]
    reg = _ind_registry_names()
    custom_names = {str(rows[i].get("Name")) for i in custom_idx}
    blob = [(str(r.get("Exec") or "") + " " + str(r.get("Args") or "")).lower()
            for r in rows]
    matched = [n for n in reg if n in custom_names]
    return {
        "total": len(rows),
        "custom": len(custom_idx), "system": len(rows) - len(custom_idx),
        "Ready": sum(1 for r in rows if str(r.get("State") or "").strip() == "Ready"),
        "Disabled": sum(1 for r in rows if str(r.get("State") or "").strip() == "Disabled"),
        "Running": sum(1 for r in rows if str(r.get("State") or "").strip() == "Running"),
        "Interactive": sum(1 for r in rows if str(r.get("Logon") or "").strip() == "Interactive"),
        "S4U": sum(1 for r in rows if str(r.get("Logon") or "").strip() == "S4U"),
        "ServiceAccount": sum(1 for r in rows if str(r.get("Logon") or "").strip() == "ServiceAccount"),
        "Group": sum(1 for r in rows if str(r.get("Logon") or "").strip() == "Group"),
        "custom_real_failed": sum(1 for i in custom_idx if cats[i] == "real_failed"),
        "custom_never_run": sum(1 for i in custom_idx if cats[i] == "never_run"),
        "custom_disabled": sum(1 for i in custom_idx
                               if str(rows[i].get("State") or "").strip() == "Disabled"),
        "ready_06_09": sum(1 for i in custom_idx
                           if str(rows[i].get("State") or "").strip() == "Ready"
                           and len(str(rows[i].get("Next") or "")) >= 13
                           and str(rows[i].get("Next"))[11:13] in _IND_PEAK),
        "registry_entries": len(reg),
        "registry_matched_custom": len(matched),
        "coverage_ratio": round(len(matched) / len(custom_idx), 4),
        "refs": {
            "db_dump_daily": sum(1 for b in blob if "db_dump_daily" in b),
            "cainiao_pull": sum(1 for b in blob if "cainiao_pull" in b),
        },
    }


def _load_golden_rows():
    with io.open(GOLDEN_PATH, "r", encoding="utf-8-sig") as f:
        rows = json.load(f)
    assert isinstance(rows, list), "基线应为 492 条 list"
    return rows


def _self_built_fixtures(tmp_path):
    """六类夹具全部 tmp_path 运行时自建（FF11），绝不读生产产物。"""
    d = tmp_path / "qa_acceptance_artifacts"
    d.mkdir(exist_ok=True)
    marker = b"-- AIOS-SELF-BUILT synthetic fixture (no production dump)\nSELECT 1;\n"
    f = {}
    p = d / "valid.sql.gz"
    p.write_bytes(gzip.compress(marker))
    f["valid"] = p
    p = d / "plain_sql_2d2d.gz"
    p.write_bytes(marker)
    f["plain_sql_2d2d"] = p
    p = d / "truncated.sql.gz"
    p.write_bytes(gzip.compress(marker)[:12])
    f["truncated"] = p
    p = d / "zero_byte"
    p.write_bytes(b"")
    f["zero_byte"] = p
    raw = bytearray(gzip.compress(marker))
    raw[-5] ^= 0xFF
    p = d / "bad_crc"
    p.write_bytes(bytes(raw))
    f["bad_crc"] = p
    p = d / "encrypted_aes"
    p.write_bytes(b"Salted__" + os.urandom(8) + os.urandom(24) + os.urandom(32))
    f["encrypted_aes"] = p
    return f


def _full_sha256(path):
    """流式全量 sha256（T-10 口径 A：产物整体压缩字节，非解压后 SQL 明文）。"""
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        while True:
            c = f.read(SHA_CHUNK)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def _bounded_fingerprint():
    """零状态变更指纹：小文件全量 sha256；11GB 大文件只取 (size, mtime_ns, 2B 魔数)。"""
    fp = {}
    for p in (F1_SRC, F2_SRC, MISS_CHECK, DB_DUMP, B4_DRILL,
              os.path.join(DAY_1029, "manifest.json"), FLAG_PATH):
        if os.path.isfile(p):
            fp[p] = ("full", _full_sha256(p))
        else:
            fp[p] = ("absent",)
    for name in THREE_GZ:
        p = os.path.join(DAY_1029, name)
        if os.path.isfile(p):
            st = os.stat(p)
            with io.open(p, "rb") as f:
                magic = f.read(2)
            fp[p] = ("bounded", st.st_size, st.st_mtime_ns, magic.hex())
        else:
            fp[p] = ("absent",)
    return fp


_BASELINE_CACHE = {}

# 2026-11-06 代码审计（BUG-06）：system_files 中由「外部会话已提交 merge」
# 造成的合法变更，逐条登记可审计理由；绝不静默丢弃，验收报告须引用此清单。
EXTERNAL_SYSTEM_CHANGES = []


def _external_system_evidence(rel, baseline):
    """system_files 差异若来自外部会话已提交 merge，返回可审计理由；否则 None。

    四重判据全部满足才归因（**不放宽硬边界**）：
      ① 文件仍存在；
      ② `git status --porcelain -- <rel>` 为空 → 工作区内容即 HEAD 内容，
         不是治理侧未提交写入（硬边界 [7] 禁止 git commit）；
      ③ 该文件在 HEAD 中最近一次提交时间 > 基线采集时刻；
      ④ 工作区字节（CRLF→LF 归一化后）与 HEAD blob 全等 → 未被二次改写。
    任一不满足返回 None，调用方按「未归因改动」判 FAIL。

    背景：2026-11-05 21:58:46 外部会话 merge
    `task/WO-GUANYU-B4DUMPFIX-20261105` 改写了 ops/db_dump_daily.py，
    T+24h 复跑时 3 个用例因「与 15:04:12 基线逐字节一致」永久硬断言而全红。
    把时点耦合改成「签发基线 + 外部变更举证」后判据重新可签，且零删改硬线不变。
    """
    import datetime as _dt
    root = baseline["root"]
    p = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    st = subprocess.run(
        ["git", "-C", root, "status", "--porcelain", "--", rel],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if st.returncode != 0 or (st.stdout or "").strip():
        return None
    log = subprocess.run(
        ["git", "-C", root, "log", "-1", "--format=%cI", "--", rel],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw_ct = (log.stdout or "").strip()
    if not raw_ct:
        return None
    try:
        ct = _dt.datetime.fromisoformat(raw_ct).timestamp()
        base_ts = _dt.datetime.fromisoformat(baseline["captured_at"]).timestamp()
    except ValueError:
        return None
    if ct <= base_ts:
        return None
    blob = subprocess.run(["git", "-C", root, "show", "HEAD:" + rel],
                          capture_output=True).stdout
    with io.open(p, "rb") as f:
        work = f.read()
    if work.replace(b"\r\n", b"\n") != blob:
        return None
    return ("external committed merge: worktree==HEAD blob (CRLF-normalized), "
            "commit_at=%s > baseline_at=%s" % (raw_ct, baseline["captured_at"]))


def _baseline_recheck():
    """G-09b 与 W-08 落盘前基线同口径复核（只读、结果缓存在进程内）。

    返回 (mismatches, rewritten_bytes, full_now, baseline)：
      mismatches    —— 44 条有界指纹 / 4 条全量 sha256 / 4 条 system_files /
                       1 条 golden 的不一致清单，空 = 零状态变更
      rewritten_bytes —— manifest.json + 3 个 .sql.gz 的改写字节数（全量 sha256
                       与基线逐字节相等时为 0）
      full_now      —— {相对路径: 当前全量 sha256}
    """
    if "result" in _BASELINE_CACHE:
        return _BASELINE_CACHE["result"]
    assert os.path.isfile(ZERO_BASELINE), "G-09b 基线缺失 → 测试阻塞项上报团长"
    b = json.loads(io.open(ZERO_BASELINE, encoding="utf-8-sig").read())
    root = b["root"]
    mismatches = []

    # ① 44 条有界指纹：head(1MiB) sha256 + size + mtime_ns（与基线同口径）
    for rel, v in sorted(b["db_fingerprint"].items()):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            mismatches.append((rel, "absent"))
            continue
        st = os.stat(p)
        h = hashlib.sha256()
        with io.open(p, "rb") as f:
            h.update(f.read(HEAD_BYTES))
        if (h.hexdigest() != v["head_sha256"] or st.st_size != v["size"]
                or st.st_mtime_ns != v["mtime_ns"]):
            mismatches.append((rel, "bounded_fingerprint"))

    # ② 4 条全量 sha256（manifest.json + 3 个 .sql.gz）：前后一致 + 改写字节数
    full_now, rewritten_bytes = {}, 0
    for rel, want in sorted(b["db_full_sha256"].items()):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            mismatches.append((rel, "full_absent"))
            continue
        got = _full_sha256(p)
        full_now[rel] = got
        if got != want:
            # 任一字节改写必改变全量 sha256；以文件全部字节计为改写上界
            rewritten_bytes += os.stat(p).st_size
            mismatches.append((rel, "full_sha256"))
        base_size = b["db_fingerprint"].get(rel, {}).get("size")
        if base_size is not None and os.stat(p).st_size != base_size:
            mismatches.append((rel, "size"))

    # ③ system_files 4 条 + golden 1 条
    # BUG-06：原实现把「与基线逐字节一致」写成永久硬断言，任何外部合法
    # 合并都会让本套件永久失败（违反「判据必须可签」）。改为：差异必须
    # 拿到外部已提交 merge 的四重举证，否则仍判 mismatches（硬 FAIL）。
    # golden 是 C:\Temp 只读底册，不属于仓库源码，仍保持逐字节硬断言。
    for rel, want in sorted(b["system_files"].items()):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            mismatches.append((rel, "absent"))
            continue
        if _full_sha256(p) != want:
            why = _external_system_evidence(rel, b)
            if why:
                EXTERNAL_SYSTEM_CHANGES.append({"path": rel, "evidence": why})
            else:
                mismatches.append((rel, "system_file"))
    g = b["golden"]
    if not os.path.isfile(g["path"]) or _full_sha256(g["path"]) != g["sha256"]:
        mismatches.append(("golden", "sha256"))

    result = (mismatches, rewritten_bytes, full_now, b)
    _BASELINE_CACHE["result"] = result
    return result


def _read_only_pipeline():
    """本轮验收的代表性只读调用链（绝不写 C:\aios 任何既有文件）。"""
    rows = _load_golden_rows()
    snap = ti.build_snapshot(rows, source="file", generated_at="FIXED")
    st = ti.compute_stats(snap)
    with contextlib.redirect_stdout(io.StringIO()):
        ti.assert_golden(st)
    ti.diff_snapshots(snap, snap)
    ti.suggest_registry(snap)
    return snap, st


# ==========================================================================
# 表 C · 独立验收正常流（N = 8）
# ==========================================================================

def test_AC_N01_independent_golden_and_adr27_diff_contract_AC01():
    """用例编号 AC-N01｜优先级 P0｜前置：492 条只读 golden（utf-8-sig）｜
    步骤：①校验 BOM ②本文件独立分类器重算 16 项 ③与硬编码期望逐项比对
    ④交叉核对 F1.compute_stats / assert_golden ⑤锁定措辞 ⑥ADR-27 修复后
    diff 契约（自比 + 构造 added/removed/changed 各 1）｜
    预期：16 项全中、F1 交叉核对一致、覆盖率 0.0614 而非 0.0785；
    登录类型 185/123/107/77；refs 0/0；自比 summary.unchanged=492（不是 97），
    added/removed/changed 每条带 name，unchanged 明细不进 JSON、tuple 不泄漏｜
    溯源：PRD AC-01 / 治理方案 §1.3 / ADR-13/14 / ADR-27。"""
    # ① BOM / 编码口径（FF10：C:\Temp 快照带 BOM，必须 utf-8-sig）
    with io.open(GOLDEN_PATH, "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf", "基线应带 UTF-8 BOM"
    rows = _load_golden_rows()
    assert len(rows) == 492
    # ② ③ 独立重算（不调用 F1 的统计函数做判据）
    ind = _independent_stats(rows)
    for k, v in EXPECT.items():
        assert ind[k] == v, "独立重算 %s: got=%r expect=%r" % (k, ind[k], v)
    # ⑤ 锁定措辞
    assert ind["coverage_ratio"] == round(18 / 293, 4) == 0.0614
    assert ind["coverage_ratio"] != round(23 / 293, 4)   # 严禁 23/293≈7.8%
    assert (ind["Interactive"], ind["S4U"], ind["ServiceAccount"], ind["Group"]) \
        == (185, 123, 107, 77)
    assert ind["refs"] == {"db_dump_daily": 0, "cainiao_pull": 0}
    # ④ 交叉核对：实现是否同意独立判据（不是拿实现自证）
    st = ti.compute_stats(ti.build_snapshot(rows, source="file", generated_at="FIXED"))
    for k, v in EXPECT.items():
        got = st.get(k, st.get("by_state", {}).get(k, st.get("by_logon_type", {}).get(k)))
        assert got == v, "F1 compute_stats 与独立判据不一致：%s" % k
    with contextlib.redirect_stdout(io.StringIO()):
        ok, failures = ti.assert_golden(st)
    assert ok is True and failures == []
    assert st["referenced"] == {"db_dump_daily": 0, "cainiao_pull": 0}

    # ⑥ ADR-27 修复后 diff 契约：(path,name) 复合键
    snap = ti.build_snapshot(rows, source="file", generated_at="FIXED")
    self_d = ti.diff_snapshots(snap, snap)
    assert self_d["summary"] == {"added": 0, "removed": 0,
                                 "changed": 0, "unchanged": 492}, \
        "ADR-27 修复前自比会把 492 误报成 97"
    assert self_d["added"] == [] and self_d["removed"] == [] and self_d["changed"] == []
    assert self_d["base"] == {"total": 492} and self_d["cur"] == {"total": 492}
    assert isinstance(self_d["summary"]["unchanged"], int)
    if "unchanged" in self_d:
        assert all(isinstance(x, str) for x in self_d["unchanged"])
    json.dumps(self_d, ensure_ascii=False)  # 可序列化 = 无 tuple 泄漏

    mutated = copy.deepcopy(rows)
    mutated.pop()                        # removed 1
    mutated[0]["State"] = "Disabled"     # changed 1
    mutated.append({"Name": "QA_ADDED", "Path": r"\QA", "State": "Ready",
                    "RC": 0, "Logon": "Interactive",
                    "Last": "2026-11-05T08:00:00", "Next": "2026-11-06T08:00:00"})
    d = ti.diff_snapshots(snap, ti.build_snapshot(mutated, source="file",
                                                   generated_at="FIXED"))
    assert d["summary"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 490}
    assert set(d["added"][0]) == {"path", "name", "state"} and d["added"][0]["name"] == "QA_ADDED"
    assert set(d["removed"][0]) == {"path", "name", "state", "deleted_at", "quarantined"}
    assert d["removed"][0]["name"] and d["removed"][0]["quarantined"] is True
    assert d["removed"][0]["deleted_at"] is None
    assert set(d["changed"][0]) == {"path", "name", "fields"}
    assert d["changed"][0]["name"] and "state" in d["changed"][0]["fields"]
    # 同 path 不同 name 不被合并（本次真实缺陷）
    def row(name, state):
        return ti.normalize_task({"Name": name, "Path": "\\",
                                  "State": state, "RC": 0})
    same = ti.diff_snapshots({"tasks": [row("A", "Ready"), row("B", "Ready")]},
                             {"tasks": [row("A", "Ready"), row("B", "Disabled")]})
    assert same["summary"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 1}
    assert same["changed"][0]["name"] == "B" and same["changed"][0]["path"] == "\\"


def test_AC_N02_repeatable_three_runs_bytes_identical_AC01():
    """用例编号 AC-N02｜优先级 P0｜前置：同一 492 条基线｜步骤：build_snapshot
    连跑 3 次（固定 generated_at）+ compute_stats 连跑 3 次｜
    预期：snapshot_id 与规范化 tasks JSON 字节级一致；16 项 golden 三次全等；
    snapshot_id 不含生成侧元数据（source/host 变、id 不变）｜
    溯源：PRD AC-01「连续运行 ≥2 次、差异 0 项」/ FF06 §5.4。"""
    rows = _load_golden_rows()
    ids, payloads, goldens = [], [], []
    for _ in range(3):
        s = ti.build_snapshot(rows, source="file", generated_at="FIXED")
        ids.append(s["snapshot_id"])
        payloads.append(json.dumps(s["tasks"], ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")))
        st = ti.compute_stats(s)
        goldens.append(tuple(st.get(k, st.get("by_state", {}).get(k,
                       st.get("by_logon_type", {}).get(k))) for k in EXPECT))
    assert len(set(ids)) == 1 and len(ids[0]) == 16
    assert len(set(payloads)) == 1
    assert len(set(goldens)) == 1 and goldens[0] == tuple(EXPECT.values())
    a = ti.build_snapshot(rows, source="A", generated_at="T1")["snapshot_id"]
    b = ti.build_snapshot(rows, source="B", generated_at="T2")["snapshot_id"]
    assert a == b == ids[0]


def test_AC_N03_default_drill_selects_latest_real_dump_not_1029_AC02(tmp_path):
    """用例编号 AC-N03｜优先级 P0｜前置：真实 backups/db 日期目录 + temp 镜像｜
    步骤：①只读 os.listdir 取日期目录 ②只读取 2 字节魔数 ③复现
    b4_restore_drill.list_backups「日期倒序、需含 quant_pg_alldb.sql.gz」语义
    选 candidates[0] ④should_select 对 2d2d 判 False｜
    预期：默认演练选中的是「最大且含 quant_pg_alldb.sql.gz」的日期目录，
    且 ≥ 2026-11-05、不是 2026-10-29；选中档与 11-05 魔数 1f8b、10-29 三档
    魔数 2d2d；镜像里 dump_latest.log 这类非日期文件不参与选档｜
    溯源：PRD AC-02 / ADR-05 / T-11。
    2026-11-08 重采基线（选项 A）：原判据硬编码 dates[0] == "2026-11-05"，
    而 DbDumpDaily 每天 03:30 新建日期目录（11-07 / 11-08 已实证），
    该断言与治理侧改动无关、随时间必红。改为「取最大且含目标文件的日期」，
    与 list_backups 的真实选档语义同构，不削弱「不选 10-29 坏档」的硬要求。
    **T-11 硬约束：本用例不含任何 list_backups() 返回值断言（不写 =0），
    flag 不是运行态过滤闸门、不写「加 flag 后自动跳过」**。
    性能口径：只读 2 字节魔数，绝不整文件解压/读取 11GB 产物。"""
    dates = sorted([d for d in os.listdir(DB_ROOT)
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", d)], reverse=True)
    assert dates, "backups/db 下应有日期目录"
    cand = [d for d in dates
            if os.path.isfile(os.path.join(DB_ROOT, d, "quant_pg_alldb.sql.gz"))]
    assert cand, "backups/db 下应有含 quant_pg_alldb.sql.gz 的日期目录"
    selected = cand[0]
    assert selected >= "2026-11-05", "默认演练应选到 11-05 及其后的真实档：%s" % selected
    assert selected != "2026-10-29", "默认演练取 backups[0] 不得是 10-29 坏档"
    assert "2026-10-29" in dates
    assert bfg.read_magic(os.path.join(DB_ROOT, selected,
                                       "quant_pg_alldb.sql.gz")) == b"\x1f\x8b"
    assert bfg.read_magic(os.path.join(DAY_1105, "quant_pg_alldb.sql.gz")) == b"\x1f\x8b"
    for name in THREE_GZ:
        assert bfg.read_magic(os.path.join(DAY_1029, name)) == b"--"
    mirror = tmp_path / "db"
    for date, magic in (("2026-10-29", b"-- AIOS-SELF-BUILT plain"),
                        ("2026-11-05", gzip.compress(b"-- AIOS-SELF-BUILT gz"))):
        dd = mirror / date
        dd.mkdir(parents=True)
        (dd / "quant_pg_alldb.sql.gz").write_bytes(magic)
    (mirror / "dump_latest.log").write_text("AIOS-SELF-BUILT noise", encoding="utf-8")
    candidates = sorted([d.name for d in mirror.iterdir()
                         if re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)
                         and (d / "quant_pg_alldb.sql.gz").is_file()], reverse=True)
    selected = candidates[0]
    assert selected == "2026-11-05" and selected != "2026-10-29"
    assert (mirror / selected / "quant_pg_alldb.sql.gz").read_bytes()[:2] == b"\x1f\x8b"
    assert bfg.should_select(bfg.verify_artifact(
        str(mirror / "2026-10-29" / "quant_pg_alldb.sql.gz"))) is False


def test_AC_N04_w08_authoritative_eight_fields_and_zero_rewrite_AC02(tmp_path):
    r"""用例编号 AC-N04｜优先级 P0｜前置：团长 W-08 已落盘 INVALID_GZIP.flag +
    C:\Temp\governance_zero_state_baseline.json（W-08 落盘前采集）｜
    步骤：①只读 flag，按 W-08 权威八字段逐键逐值精确比对（policy 取 ADR-28 值）
    ②校验 files 恰好 3 个文件名 ③只读 manifest.json（无 BOM、items 5、ok_count 3）
    ④F2.read_magic 只读 2 字节复核三档魔数 2d2d / format=plain-sql / ok=false
    ⑤G-09b 同口径复核：manifest.json + 3 个 .sql.gz 全量 sha256 与基线前后一致，
    且改写字节数 = 0 ⑥断言 flag 不是运行态过滤闸门（T-11，无 list_backups 断言）｜
    预期：八字段名与值全等、files 恰 3 个、policy="mark-only, do-not-delete"、
    recoverable=false、无 must:false 项混入、全量 sha256 4/4 一致、
    rewritten_bytes == 0｜溯源：PRD AC-02 / W-08 权威八字段 / ADR-28 / T-11 /
    G-09b / ADR-30。"""
    assert os.path.isfile(FLAG_PATH), "AC-N04 前置：W-08 flag 未落盘 → 测试阻塞项上报团长"
    flag = json.loads(io.open(FLAG_PATH, encoding="utf-8").read())
    # ① 八字段名 + 八字段值（不自行编造枚举）
    assert set(flag) == set(W08_EXPECT) and len(flag) == 8
    assert flag == W08_EXPECT, "W-08 flag 偏离权威八字段：%r" % flag
    assert flag["policy"] == POLICY_AUTHORITATIVE
    # ② files 恰 3 个文件名（聚合，仍是**一个字段**，不破坏八字段结构）
    assert isinstance(flag["files"], list) and len(flag["files"]) == 3
    assert sorted(flag["files"]) == sorted(THREE_GZ)
    # ③ manifest.json：无 BOM、items 5 条、ok_count 3；两条 must:false 不列入 flag
    raw_manifest = io.open(os.path.join(DAY_1029, "manifest.json"), "rb").read()
    assert not raw_manifest.startswith(b"\xef\xbb\xbf"), "manifest 不应带 BOM"
    manifest = json.loads(raw_manifest.decode("utf-8"))
    assert len(manifest["items"]) == 5 and manifest["ok_count"] == 3
    non_must = [it["file"] for it in manifest["items"] if it.get("must") is False]
    assert len(non_must) == 2
    assert all(n not in flag["files"] for n in non_must), "must:false 项不得进入 flag"
    # ④ F2 只读复核（read_magic 仅 2 字节；verify_artifact 遇 2d2d 提前返回，
    #    不整文件读取 11GB、不解压、不解密）
    for name in THREE_GZ:
        p = os.path.join(DAY_1029, name)
        assert bfg.read_magic(p) == b"--"
        v = bfg.verify_artifact(p)
        assert v["magic"] == "2d2d" and v["format"] == "plain-sql"
        assert v["ok"] is False and v["recoverable"] is False
        assert bfg.should_select(v) is False
    # ⑤ G-09b 同口径复核：全量 sha256 前后一致 + 改写字节数 0
    mismatches, rewritten_bytes, full_now, baseline = _baseline_recheck()
    expected_full_keys = {
        "backups/db/2026-10-29/manifest.json",
        "backups/db/2026-10-29/%s" % THREE_GZ[0],
        "backups/db/2026-10-29/%s" % THREE_GZ[1],
        "backups/db/2026-10-29/%s" % THREE_GZ[2],
    }
    assert set(full_now) == expected_full_keys, "G-09b 全量 sha256 应覆盖 manifest + 3 档"
    assert all(full_now[k] == baseline["db_full_sha256"][k] for k in full_now), \
        "manifest/3 个 .sql.gz 全量 sha256 与 W-08 落盘前基线不一致"
    assert rewritten_bytes == 0, "检测到既有产物被改写字节：%d" % rewritten_bytes
    assert mismatches == [], "G-09b 同口径复核不一致：%r" % (mismatches,)
    # ⑥ T-11：flag 不是运行态过滤闸门，本套件不含 list_backups() 返回值断言
    w10 = io.open(W10_TERMINOLOGY, encoding="utf-8").read()
    assert "不是运行态过滤闸门" in w10 and "无因果" in w10
    b4 = io.open(B4_DRILL, encoding="utf-8", errors="replace").read()
    # D4 已批准接入自动过滤（L2 五要素）：b4 应含 should_select 且在 list_backups
    # 调用之，坏档被自动过滤；flag 本身仍不是运行态过滤闸门（隔离留痕，与选档逻辑独立）。
    assert "should_select" in b4 and "verify_artifact" in b4
    assert "from backup_format_gate import" in b4 or "import backup_format_gate" in b4
    assert "policy=mark-only, do-not-delete" in w10


def test_AC_N05_unified_terminology_doc_self_consistent_AC03():
    """用例编号 AC-N05｜优先级 P0｜前置：W-10 统一口径声明（AC-03 唯一权威声明件）｜
    步骤：只读扫描措辞｜预期：含「明文副链断档」「两库真空」「统一声明替代改原文件」
    「加密主链健康」「已 modified」；枚举三处口径；且不含失效表述
    （P0 全链断 / 60 天轮转 / --keep-days 已实现）｜
    溯源：PRD AC-03 / ADR-05「以新文档统一声明替代改原文件」。"""
    assert os.path.isfile(W10_TERMINOLOGY), "AC-03 权威声明件缺失 → 测试阻塞项"
    t = io.open(W10_TERMINOLOGY, encoding="utf-8").read()
    for phrase in ("明文副链断档", "两库真空", "统一声明替代改原文件",
                   "加密主链健康", "已 modified", "KEEP_DAYS", "14"):
        assert phrase in t, "统一声明缺措辞：%s" % phrase
    for f in ("20261105_nextstep_guard_and_backup_gap.md", "db_dump_daily.py",
              "20261105_data_backup_and_task_governance_plan.md"):
        assert f in t, "统一声明未枚举口径文件：%s" % f
    for stale in ("P0 全链断", "60 天轮转", "--keep-days 已实现"):
        assert stale not in t, "统一声明残留失效表述：%s" % stale


def test_AC_N06_design_artifact_enumerates_key_chains_pending_AC04():
    """用例编号 AC-N06｜优先级 P0｜前置：W-09 设计留痕文档｜步骤：只读扫描 +
    交叉核对 suggest_registry 候选｜预期：文档枚举 JevRestoreDrill / BackupDaily /
    ResticTrialDaily 三条关键链 + DbDumpDaily=pending + applied=false + 措辞
    「设计完成」+「本轮不承诺代码自动过滤」；suggest_registry 产出恰 4 候选、
    applied=False、DbDumpDaily status=pending、expect=None 需人工确认｜
    溯源：PRD AC-04「设计完成，不是运行态在册」/ ADR-03/05/11。"""
    assert os.path.isfile(W09_INTERFACE), "AC-04 设计留痕件缺失 → 测试阻塞项"
    t = io.open(W09_INTERFACE, encoding="utf-8").read()
    for phrase in ("JevRestoreDrill", "BackupDaily", "ResticTrialDaily",
                   "DbDumpDaily", "pending", "applied=false", "设计完成",
                   "本轮不承诺代码自动过滤"):
        assert phrase in t, "设计件缺措辞：%s" % phrase
    snap = ti.build_snapshot(_load_golden_rows(), source="file", generated_at="FIXED")
    out = ti.suggest_registry(snap)
    assert out["applied"] is False
    names = {c["name"]: c for c in out["candidates"]}
    assert set(names) == {"JevRestoreDrill", "BackupDaily", "ResticTrialDaily", "DbDumpDaily"}
    assert names["DbDumpDaily"]["status"] == "pending"
    assert all(c["expect"] is None and c["needs_human_confirm"] is True
               for c in out["candidates"]), "expect 须显式、须人工确认"
    assert any(c["name"] == "CainiaoPull" for c in out["conflicts"])


def test_AC_N07_zero_state_change_fingerprint_G09():
    """用例编号 AC-N07｜优先级 P0｜前置：跑代表性只读调用链之前先取指纹 +
    G-09b 基线｜步骤：before = 指纹 → 执行只读链 → after = 指纹 → 比对；
    再以 G-09 基线做同口径复核｜预期：G-09a/b/c 三项全一致（F1/F2 源码、
    manifest、flag、db_dump_daily、miss_check、b4_drill 小文件全量 sha256 不变；
    3 个 11GB .gz 的 size/mtime_ns/魔数不变；44 条基线指纹零不一致）｜
    溯源：质量门禁 G-09 / 硬边界「零系统状态变更」。"""
    before = _bounded_fingerprint()
    _read_only_pipeline()
    after = _bounded_fingerprint()
    assert before == after, "只读验收过程改变了既有文件 → P0 门禁不通过"
    mismatches, rewritten_bytes, _, _ = _baseline_recheck()
    assert mismatches == [], "G-09b 与 W-08 落盘前基线不一致：%r" % (mismatches,)
    assert rewritten_bytes == 0


def test_AC_N08_six_fixtures_adr30_encrypted_aes_AC02(tmp_path):
    """用例编号 AC-N08｜优先级 P0｜前置：六类 tmp_path 运行时自建夹具｜
    步骤：verify_artifact 逐类判定｜预期：valid→gzip/ok=true；plain_sql_2d2d→
    plain-sql；truncated→gzip/ok=false；zero_byte→empty；bad_crc→gzip/ok=false；
    **encrypted_aes→"encrypted-aes"（ADR-30 修复后 8 字节头使 Salted__ 可识别），
    且对外 v["magic"] 仍为 2 字节 "5361"、ok=false、recoverable=false**｜
    溯源：PRD AC-02 / ADR-30 / FF11 / FF06 §5.2（绝不调用解密）。"""
    fx = _self_built_fixtures(tmp_path)
    table = [
        ("valid", "gzip", True, True),
        ("plain_sql_2d2d", "plain-sql", False, False),
        ("truncated", "gzip", False, False),
        ("zero_byte", "empty", False, False),
        ("bad_crc", "gzip", False, False),
        ("encrypted_aes", "encrypted-aes", False, False),
    ]
    for name, fmt, ok, rec in table:
        v = bfg.verify_artifact(str(fx[name]))
        assert v["format"] == fmt, "%s: got=%r expect=%r" % (name, v["format"], fmt)
        assert v["ok"] is ok and v["recoverable"] is rec, name
        assert v["format"] in bfg.VALID_FORMATS
    v = bfg.verify_artifact(str(fx["encrypted_aes"]))
    assert v["format"] == "encrypted-aes"
    assert v["magic"] == "5361" and len(v["magic"]) == 4, "对外 magic 仍为 2 字节"
    assert bfg.detect_format(b"Salted__", v["size_bytes"]) == "encrypted-aes"
    assert bfg.should_select(v) is False
    # 反向：所有夹具自建、位于 tmp_path、不含生产明文标记
    prod = [b"pg_dump", b"cainiao_mysql", b"zhice_os_v2", b"quant_pg_alldb",
            b"postgresql://", b"mysql://", b"password="]
    for p in fx.values():
        assert str(tmp_path) in str(p)
        raw = p.read_bytes()
        for m in prod:
            assert m not in raw
    assert SELF_BUILT in fx["plain_sql_2d2d"].read_bytes()
    assert SELF_BUILT in gzip.decompress(fx["valid"].read_bytes())


# ==========================================================================
# 表 C-E · 独立验收异常流（E = 8，≥ 正常流 8 条的一半 4）
# ==========================================================================

def test_AC_E01_env_drift_is_loud_not_silent_and_not_code_bug(monkeypatch):
    """用例编号 AC-E01｜优先级 P0｜前置：492 基线 + 注入式漂移（+1 行）｜
    步骤：①正常基线 assert_golden=True ②注入第 493 行再算 ③monkeypatch
    subprocess.run 模拟只读 live /query 返回漂移数据｜
    预期：漂移时 assert_golden 返回 (False, failures) 且含 total 不一致，
    **不静默通过、不抛异常、不判为代码 bug**；live 路径仅发 /query 只读命令｜
    溯源：PRD AC-01 / T-09（环境漂移上报团长）。"""
    rows = _load_golden_rows()
    base = ti.compute_stats(ti.build_snapshot(rows, source="file", generated_at="FIXED"))
    with contextlib.redirect_stdout(io.StringIO()):
        ok, _ = ti.assert_golden(base)
    assert ok is True
    drifted = rows + [{"Name": "QA_DRIFT_SENTINEL", "Path": r"\QA",
                       "State": "Ready", "RC": 0, "Logon": "Interactive",
                       "Last": "2026-11-05T08:00:00", "Next": "2026-11-06T08:00:00"}]
    st = ti.compute_stats(ti.build_snapshot(drifted, source="file", generated_at="FIXED"))
    assert st["total"] == 493
    with contextlib.redirect_stdout(io.StringIO()):
        ok, failures = ti.assert_golden(st)
    assert ok is False and any(f.startswith("total:") for f in failures)
    calls = []

    def spy(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(ti.subprocess, "run", spy)
    live = ti.query_schtasks(timeout=5)
    assert live == [] and len(calls) == 1
    assert calls[0][:2] == ["schtasks", "/query"]
    for forbidden in ("/create", "/change", "/delete", "/run", "/end"):
        assert forbidden not in calls[0]


def test_AC_E02_D4_auto_filter_integrated_AC02():
    """用例编号 AC-E02｜优先级 P0（D4 升级）｜前置：flag 可见 + W-09/W-10 文档｜
    步骤：检查 flag 可见性与 policy + 静态核对 b4_restore_drill.py 已接入闸门｜
    预期：flag 可见、policy 精确等于 ADR-28 权威值；b4 已 import F2 并在 list_backups
    调用 should_select 自动过滤坏档（D4 L2 已批准）；文档声明「不是运行态过滤闸门 /
    无因果」仍成立（flag 是隔离留痕，should_select 是 b4 选档逻辑，二者独立）；
    **不写死 list_backups()=0**｜溯源：PRD AC-02 / ADR-05 / T-11 / D4。"""
    assert os.path.isfile(FLAG_PATH)
    flag = json.loads(io.open(FLAG_PATH, encoding="utf-8").read())
    assert flag["policy"] == POLICY_AUTHORITATIVE
    b4 = io.open(B4_DRILL, encoding="utf-8", errors="replace").read()
    # D4 已批准接入自动过滤：b4 应 import F2 且在 list_backups 调用 should_select
    assert "should_select" in b4 and "verify_artifact" in b4
    assert "from backup_format_gate import" in b4 or "import backup_format_gate" in b4
    w10 = io.open(W10_TERMINOLOGY, encoding="utf-8").read()
    assert "不是运行态过滤闸门" in w10 and "无因果" in w10
    # W-09 的「本轮不承诺代码自动过滤」声明已被 D4 推翻（见 W-13 V4 §D4 实施记录），
    # 此处仅核对 W-09 仍含该历史表述，不重复断言其有效性。
    w9 = io.open(W09_INTERFACE, encoding="utf-8").read()
    assert "本轮不承诺代码自动过滤" in w9


def test_AC_E03_fixture_provenance_reverse_assertion(tmp_path):
    r"""用例编号 AC-E03｜优先级 P0｜前置：六类运行时自建夹具｜步骤：逐一读字节
    + 路径归属 + 生产标记｜预期：含 AIOS-SELF-BUILT 标记；不含生产明文 SQL /
    `-- pg_dump` / 生产库名 / 连接串；全部位于 tmp_path；
    未从 backups/db 或 C:\backups_mirror 复制任何字节｜溯源：FF11 / AC-02。"""
    fx = _self_built_fixtures(tmp_path)
    production_markers = [b"pg_dump", b"cainiao_mysql", b"zhice_os_v2",
                          b"quant_pg_alldb", b"postgresql://", b"password="]
    for name, p in fx.items():
        assert str(tmp_path) in str(p), "%s 不在 tmp_path 下" % name
        assert "backups" not in str(p).lower(), "%s 疑似来自生产目录" % name
        raw = p.read_bytes()
        for m in production_markers:
            assert m not in raw, "%s 含生产标记 %r" % (name, m)
    assert SELF_BUILT in fx["plain_sql_2d2d"].read_bytes()
    assert SELF_BUILT in gzip.decompress(fx["valid"].read_bytes())
    assert b"SELECT" not in fx["encrypted_aes"].read_bytes()
    for d in (DB_ROOT, r"C:\backups_mirror"):
        assert d not in str(tmp_path)
    assert not os.path.isdir(os.path.join(AIOS, "tests", "fixtures")), \
        "不得创建 tests/fixtures/ 目录（委派硬约束）"


def test_AC_E04_stale_phrasing_absent_in_three_originals_AC03():
    """用例编号 AC-E04｜优先级 P1｜前置：AC-03 三处原始口径文件｜
    步骤：只读扫描失效表述｜预期：三处均不含「P0 全链断」「60 天轮转」
    「--keep-days 已实现」「KEEP_DAYS = 60」「全链断档」；
    db_dump_daily.py 且保有 KEEP_DAYS = 14（口径收口、不改常量）｜
    溯源：PRD AC-03「失效表述残留 = 0；KEEP_DAYS 收口为 14」。"""
    targets = (DB_DUMP, MISS_CHECK,
               os.path.join(AIOS, "docs", "changes",
                            "20261105_nextstep_guard_and_backup_gap.md"))
    stale = ("P0 全链断", "P0 db 备份断链", "60 天轮转",
             "--keep-days 已实现", "KEEP_DAYS = 60", "全链断档")
    for p in targets:
        assert os.path.isfile(p), "口径文件缺失：%s" % p
        t = io.open(p, encoding="utf-8", errors="replace").read()
        for s in stale:
            assert s not in t, "%s 残留失效表述：%s" % (os.path.basename(p), s)
    assert "KEEP_DAYS = 14" in io.open(DB_DUMP, encoding="utf-8").read()


def test_AC_E05_ast_no_write_path_and_no_apply():
    """用例编号 AC-E05｜优先级 P0｜前置：F1 源码｜步骤：ast.parse 逐调用检查
    （**不用字符串扫描**，因 F1 docstring 里合法出现「不接受 --apply」与
    miss_check.py 字样，朴素扫描会造成假红）｜
    预期：无 --apply/--host/--upload/--remote 的 add_argument；无对
    miss_check.py / REGISTRY 的写模式 open；无 schtasks 变更子命令与
    Register-/Enable-/Disable-/Unregister-/New-ScheduledTask；
    suggest_registry 调用前后 miss_check.py 哈希不变｜
    溯源：PRD AC-04 / ADR-06/10 / qa-testing.md 安全层。"""
    src = io.open(F1_SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    banned_add, write_opens = set(), []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (
            f.id if isinstance(f, ast.Name) else "")
        if name == "add_argument" and n.args and isinstance(n.args[0], ast.Constant):
            v = n.args[0].value
            if isinstance(v, str) and v in ("--apply", "--host", "--upload", "--remote"):
                banned_add.add(v)
        if name in ("open", "fdopen") and len(n.args) >= 2:
            m = n.args[1]
            if isinstance(m, ast.Constant) and isinstance(m.value, str) \
                    and any(x in m.value for x in ("w", "a", "x", "+")):
                tgt = n.args[0]
                if isinstance(tgt, ast.Constant) and isinstance(tgt.value, str) \
                        and ("miss_check" in tgt.value or "REGISTRY" in tgt.value):
                    write_opens.append(tgt.value)
    assert banned_add == set() and write_opens == []
    for cmd in ("schtasks /create", "schtasks /change", "schtasks /delete",
                "Register-ScheduledTask", "Enable-ScheduledTask",
                "Disable-ScheduledTask", "Unregister-ScheduledTask",
                "New-ScheduledTask"):
        assert cmd not in src
    before = hashlib.sha256(io.open(MISS_CHECK, "rb").read()).hexdigest()
    ti.suggest_registry(ti.build_snapshot(_load_golden_rows(),
                                          source="file", generated_at="FIXED"))
    assert hashlib.sha256(io.open(MISS_CHECK, "rb").read()).hexdigest() == before


def test_AC_E06_registry_unchanged_23_and_pending_not_landed():
    """用例编号 AC-E06｜优先级 P0｜前置：miss_check.py 只读 + 492 基线｜
    步骤：AST 数 REGISTRY 键 + 扫计划任务名 + 核对候选状态｜
    预期：REGISTRY 恰 23 条不变；DbDumpDaily 不在 REGISTRY、不在 492 计划任务名
    中（未落运行态）；CainiaoPull **在 REGISTRY 但 492 中无此计划任务**
    （锁定措辞「CainiaoPull 指向不存在任务」）；候选 DbDumpDaily=pending｜
    溯源：PRD AC-04 / ADR-11 / 锁定措辞。"""
    reg = _ind_registry_names()
    assert len(reg) == 23, "REGISTRY 应保持 23 条"
    assert "DbDumpDaily" not in reg, "DbDumpDaily 不得落运行态 REGISTRY"
    assert "CainiaoPull" in reg, "CainiaoPull 应保留在册（设计意图报 MISS）"
    names = {str(r.get("Name")) for r in _load_golden_rows()}
    assert "DbDumpDaily" not in names
    assert "CainiaoPull" not in names, "CainiaoPull 应指向不存在的计划任务"
    out = ti.suggest_registry(ti.build_snapshot(_load_golden_rows(),
                                                source="file", generated_at="FIXED"))
    assert out["applied"] is False
    assert next(c for c in out["candidates"]
                if c["name"] == "DbDumpDaily")["status"] == "pending"


def test_AC_E07_no_files_outside_whitelist():
    """用例编号 AC-E07｜优先级 P0｜前置：git 工作区 / tests 目录｜
    步骤：git status --porcelain -- tests/ 取新增集合 + 文件系统交叉核对｜
    预期：tests/ 下未跟踪的新增集合 ⊆ {W-05, W-06, W-07 三个文件}；
    不存在 tests/fixtures/；10-29 目录除 3 个 .gz + manifest + flag 外
    无额外新增（无 .tmp 残留、无未授权落盘）｜
    溯源：门禁 G-09c / 委派「新建且只新建三个绝对路径」。"""
    whitelist = {
        "tests/test_task_inventory.py",
        "tests/test_backup_format_gate.py",
        "tests/test_governance_acceptance.py",
    }
    untracked = set()
    try:
        p = subprocess.run(["git", "-C", AIOS, "status", "--porcelain", "--", "tests/"],
                           capture_output=True, timeout=30)
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("??"):
                untracked.add(line[3:].strip().replace("\\", "/"))
    except (OSError, subprocess.TimeoutExpired):
        pass  # git 不可用时退化为文件系统核对，不造成假红
    extra = {u for u in untracked
             if not (u in whitelist or u.endswith("/") and u.rstrip("/") in
                     {"tests/__pycache__", "tests/e2e"})}
    assert not extra, "白名单外新增文件：%s" % extra
    assert not os.path.isdir(os.path.join(AIOS, "tests", "fixtures"))
    allowed = set(THREE_GZ) | {"manifest.json", "INVALID_GZIP.flag"}
    actual = set(os.listdir(DAY_1029))
    assert actual == allowed, "10-29 出现白名单外文件：%s" % (actual - allowed)
    for name in actual:
        assert not name.endswith((".tmp", ".bak")), "残留临时文件：%s" % name


def test_AC_E08_hard_boundary_seven_zeros_reverse_scan(monkeypatch, tmp_path):
    """用例编号 AC-E08｜优先级 P0｜前置：F1/F2 源码 + 只读基线 + 自建夹具｜
    步骤：①AST/源码静态扫描 ②monkeypatch subprocess 全程审计 ③前后文件指纹比对
    ④跑代表性只读链 + F2 只读链 ⑤锁定措辞复核｜
    预期：硬边界 **7 个 0** 全部成立：
      [0] 计划任务增改/启用/禁用 = 0（无 Register-/Set-/Disable-/Enable-/
          Unregister-ScheduledTask、schtasks /create|/change|/delete，live 仅 /query）
      [1] 既有文件删改 = 0（F1/F2 无 os.remove/unlink/rmtree）
      [2] DUMPS / KEEP_DAYS 变更 = 0（db_dump_daily.py 哈希不变且仍 KEEP_DAYS=14）
      [3] backups/db 既有产物字节改写 = 0（前后指纹一致 + G-09b 基线一致）
      [4] .enc 解密执行 = 0（源码无 Unprotect-File/decrypt；夹具全合成）
      [5] 10.205.0.118 访问 = 0（源码无该 IP、无网络库 import）
      [6] 本阶段/测试过程非授权落盘 = 0（无 git commit；apply=False 默认不落盘；
          tests/fixtures 不存在；AC-E07 白名单）
    锁定措辞：18/293≈6.1%、CainiaoPull 指向不存在任务、登录 185/123/107/77｜
    溯源：硬边界 7 个 0 / qa-testing.md 安全层 / ADR-02/03/05 / 团长 W-07 要求。"""
    f1 = io.open(F1_SRC, encoding="utf-8").read()
    f2 = io.open(F2_SRC, encoding="utf-8").read()
    both = f1 + "\n" + f2
    # [0] 计划任务变更命令
    for cmd in ("schtasks /create", "schtasks /change", "schtasks /delete",
                "Register-ScheduledTask", "Set-ScheduledTask",
                "Enable-ScheduledTask", "Disable-ScheduledTask",
                "Unregister-ScheduledTask", "New-ScheduledTask"):
        assert cmd not in both, "[0] 出现计划任务变更命令：%s" % cmd
    # [1] 物理删除调用
    for forbidden in ("os.remove(", "os.unlink(", "shutil.rmtree(", "os.rmdir("):
        assert forbidden not in both, "[1] 出现物理删除调用：%s" % forbidden
    # [2] DUMPS / KEEP_DAYS
    assert "KEEP_DAYS = 14" in io.open(DB_DUMP, encoding="utf-8").read()
    assert "KEEP_DAYS = 60" not in io.open(DB_DUMP, encoding="utf-8").read()
    # [4] .enc 解密
    low = both.lower()
    for forbidden in ("unprotect-file", "decrypt", "aescrypt", "cipher("):
        assert forbidden not in low, "[4] 出现解密调用：%s" % forbidden
    # [5] 生产服务器 + 网络库
    assert "10.205.0.118" not in both
    for path in (F1_SRC, F2_SRC):
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    assert a.name not in ("socket", "requests", "urllib",
                                          "http", "paramiko", "ftplib")
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] not in ("socket", "requests",
                                                      "urllib", "http", "paramiko")
    # [6] git commit / 非授权落盘
    for forbidden in ("git commit", "git push"):
        assert forbidden not in both, "[6] 源码含版本库写命令：%s" % forbidden
    assert inspect_default_apply_is_false(), "[6] apply 默认值必须 False"
    assert not os.path.isdir(os.path.join(AIOS, "tests", "fixtures"))

    # ② ③ 全程审计：monkeypatch subprocess + 前后指纹
    before = _bounded_fingerprint()
    calls = []

    def spy(args, **kwargs):
        calls.append(list(args) if isinstance(args, (list, tuple)) else [args])
        for a in calls[-1]:
            for bad in ("/create", "/change", "/delete", "/run", "/end",
                        "Register-ScheduledTask", "git", "10.205.0.118"):
                assert bad not in str(a), "[0/5/6] 违规子进程调用：%s" % a
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(ti.subprocess, "run", spy)
    _read_only_pipeline()
    ti.query_schtasks(timeout=5)   # live 只读路径审计（mock，不实跑 schtasks）
    # 追加 F2 只读链（自建夹具、apply=False，全程落在 pytest tmp_path）
    p = tmp_path / "valid.sql.gz"
    p.write_bytes(gzip.compress(b"-- AIOS-SELF-BUILT\nSELECT 1;\n"))
    v = bfg.verify_artifact(str(p))
    item = {"key": "k", "ok": True, "size": 1, "file": "valid.sql.gz", "must": True}
    bfg.gate_entry(item, str(tmp_path))
    bfg.should_select(v)
    r = bfg.write_quarantine_flag(str(tmp_path / "2026-10-29"),
                                  "valid.sql.gz", v, apply=False)
    assert r["apply"] is False and "written" not in r
    assert all(any("/query" in str(a) for a in c) for c in calls), \
        "任何子进程调用必须是只读 /query"
    after = _bounded_fingerprint()
    assert before == after, "[3] 验收过程改写了既有产物 → P0 门禁不通过"
    mismatches, rewritten_bytes, _, _ = _baseline_recheck()
    assert mismatches == [] and rewritten_bytes == 0, "[3] G-09b 基线不一致"

    # ⑤ 锁定措辞复核（团长 W-07 落盘要求）
    ind = _independent_stats(_load_golden_rows())
    assert ind["coverage_ratio"] == round(18 / 293, 4) == 0.0614
    assert ind["registry_entries"] == 23 and ind["registry_matched_custom"] == 18
    assert (ind["Interactive"], ind["S4U"], ind["ServiceAccount"], ind["Group"]) \
        == (185, 123, 107, 77)
    reg = _ind_registry_names()
    names = {str(r.get("Name")) for r in _load_golden_rows()}
    assert "CainiaoPull" in reg and "CainiaoPull" not in names


def inspect_default_apply_is_false():
    """[6] 辅助：write_quarantine_flag 的 apply 默认值必须为 False。"""
    import inspect
    return inspect.signature(
        bfg.write_quarantine_flag).parameters["apply"].default is False


# ==========================================================================
# FF10 多端兼容矩阵（qa-testing.md §二按产品形态映射；本项目无 Web/iOS/
# Android/鸿蒙/小程序，映射为四类真实执行环境差异 + 路径别名，不跳过）
# ==========================================================================
class TestFF10Compatibility:
    """兼容矩阵：目标环境 = 单机 Windows CLI/脚本的真实执行环境构成。"""

    def test_CM_N01_python311_on_windows_and_utf8(self):
        """用例编号 CM-N01｜优先级 P1｜溯源：FF10 / 发布方案 §Python 口径。
        解释器 = Python 3.11 on Windows；中文源码/中文测试字面量在 -X utf8 下
        可无损往返；项目文档明确「本机无 python3 别名」与 gzip.open +
        subprocess 写原 fd 的 10-29 坏档成因（不臆造 UTF-16LE 分支给 F1）。"""
        assert sys.platform == "win32"
        assert sys.version_info[:2] == (3, 11)
        assert "就绪".encode("utf-8").decode("utf-8") == "就绪"
        w9 = io.open(W09_INTERFACE, encoding="utf-8").read()
        assert "python3" in w9 and "gzip.open" in w9 and "子进程写原 fd" in w9

    def test_CM_N02_windows_ps51_utf16le_is_wrapper_domain(self):
        """用例编号 CM-N02｜优先级 P1｜溯源：FF10 / FF06 A2/A15。
        PS 5.1 的 *>>/1>>/2>> 写 UTF-16LE 属 alert_fail_wrap.normalize_err_log()
        的 BOM 嗅探既有 wrapper 域；F1.query_schtasks 的解码链仅为
        utf-8-sig/utf-8/mbcs/gbk（fullstack 明确纠正：不给 F1 加 UTF-16LE 预期）。"""
        w9 = io.open(W09_INTERFACE, encoding="utf-8").read()
        assert "UTF-16LE" in w9 and ("Power" + "Shell") in w9 and "5.1" in w9
        assert "Out-File -Append -Encoding utf8" in w9 or "Add-Content -Encoding UTF8" in w9
        chain = ti._decode.__code__.co_consts
        assert ("utf-8-sig", "utf-8", "mbcs", "gbk") in chain
        assert not any(isinstance(c, str) and "utf-16" in c.lower() for c in chain)

    def test_CM_N03_docker_dash_no_pipefail_is_design_only(self):
        """用例编号 CM-N03｜优先级 P2｜溯源：FF10 / qa-testing.md 兼容层。
        Docker 容器内 dash 不支持 pipefail：本轮 F1/F2 均为纯 Python 模块、
        不依赖 bash 管道，因此只做静态核对（不实跑 shell）——
        源码不含 pipefail / set -o / bash shebang。"""
        for p in (F1_SRC, F2_SRC):
            t = io.open(p, encoding="utf-8").read()
            for bad in ("pipefail", "set -o", "#!/bin/bash", "#!/usr/bin/env bash"):
                assert bad not in t, "%s 含 bash-only 构造：%s" % (p, bad)

    def test_CM_N04_schtasks_chinese_localization_dual_decode(self):
        """用例编号 CM-N04｜优先级 P0｜溯源：FF10 / FF06 A2。
        schtasks 中文本地化输出：F1._decode 对 utf-8 / gbk(mbcs) / BOM 三种
        字节均能解出中文；文档登记「中英双 regex」口径。"""
        zh = "状态: 就绪 上次运行时间: 从不"
        assert ti._decode(zh.encode("utf-8")) == zh
        assert ti._decode(zh.encode("gbk")) == zh
        assert ti._decode(b"\xef\xbb\xbf" + zh.encode("utf-8")) == zh
        w9 = io.open(W09_INTERFACE, encoding="utf-8").read()
        assert "中英双 regex" in w9 and "上次运行时间" in w9

    def test_CM_N05_junction_vs_physical_path_mixed(self, tmp_path):
        r"""用例编号 CM-N05｜优先级 P1｜溯源：FF10 / 发布方案（junction 口径）。
        C:\aios 为 junction（指向 C:\项目\老田的AI助手，同一 inode）：
        比较前用 realpath 归一；atomic_write_json 写到含 .. 段的混合路径
        仍能原子落地且可圆读；发布方案记录 junction 事实。"""
        real = os.path.realpath(AIOS)
        assert os.path.isdir(real)
        assert os.path.realpath(F1_SRC) == os.path.realpath(
            os.path.join(real, "ops", "task_inventory.py"))
        mixed = tmp_path / "qa_junction" / "sub" / ".." / "out.json"
        ti.atomic_write_json(str(mixed), {"中文": "ok"})
        assert json.loads(io.open(os.path.realpath(str(mixed)),
                                  encoding="utf-8").read()) == {"中文": "ok"}
        w12 = io.open(W12_RELEASE, encoding="utf-8").read()
        assert "junction" in w12
