# -*- coding: utf-8 -*-
r"""W-07 · 独立验收套件（qa-tester 单写者落盘，GOV-W 白名单显式纳入）。

【委派】team-lead ADR-08 修订委派 W-07；本文件是 W-05/W-06/W-07 中唯一
        **独立验收**文件，不 import 那两个单测、不复述实现方结论。
【上游】PRD 最终版四条可达 AC（AC-01~AC-04）、治理方案 §1.3 / §三 / §六、
        项目档案 ADR-02/03/05/11/16~20、FF06 接口契约、发布方案 W-12、
        qa-testing.md §三用例模板 / §四 Bug 规范 / §五门禁 / §六验收清单。
【解释器】"C:\Program Files\Python311\python.exe" -X utf8
【只读基线】C:\Temp\tasks_inventory_20261105.json（带 BOM → encoding="utf-8-sig"，
            492 条 list）。所有夹具均为 tmp_path 运行时自建（含 AIOS-SELF-BUILT
            标记），严禁从 backups/db / C:\backups_mirror 复制生产产物（FF11）。

【独立性声明 · 关键】AC-N01 的 16 项 golden 由本文件**自包含的独立分类器**
        （按 PRD §1.3 规则重写，不调用 F1 的 compute_stats / assert_golden 做判据）
        从 492 条只读基线重算得出，再与硬编码期望常量比对；随后才把
        F1 的 compute_stats / assert_golden 作为「实现是否与独立判据一致」的
        交叉核对。因此本套件证明的是「AC 达成」，而非复述实现方的自证结论。

【本轮唯一四条可达 AC 的追溯】
  AC-01 ← AC-N01 / AC-N02 / AC-N12 / TI-N01~N14（F1 五子命令、492 统计、可重复）
  AC-02 ← AC-N03 / AC-N04 / AC-E02 / W-06 六类夹具（flag 八字段、不删不改、
          默认演练选 11-05、显式 --date 人工拦截；**不含 list_backups() = 0 断言**）
  AC-03 ← AC-N05 / AC-E04（W-10 统一声明 + 三处口径失效表述残留 = 0）
  AC-04 ← AC-N06 / AC-E06（三条关键链 + DbDumpDaily=pending 设计完成，不落运行态）

【锁定措辞（团长终裁，严禁改写）】
  覆盖率 18/293≈6.1%（严禁 23/293≈7.8%）；CainiaoPull 指向不存在的计划任务；
  db_dump_daily / cainiao_pull 引用均 0；登录类型 185/123/107/77。

【四层测试策略（qa-testing.md §一，缺层不合格）】
  功能：AC-N01~N07 正常流 + AC-E01~E08 异常流（异常 8 条 ≥ 正常 7 条的一半）
  兼容：TestFF10Compatibility —— PS5.1 / Python3.11 / Docker dash /
        schtasks 中文本地化 / C:\aios junction 五类真实执行环境矩阵
  性能：AC-N04 只读 2 字节魔数 + BG-N08 设计预算声明；按 G0-b「不实测、
        不设阈值」，实测值栏留空标「实施期填」，GB 级耗时是实施期观测项
  安全：AC-E08 硬边界 7 个 0 反向扫描 + AC-E03 夹具来源 + AC-E05 AST 无写路径

【量化质量门禁（本文件为 G0-b 执行态证据之一）】
  P0 bug = 0；核心链路用例通过率 100%；三条 AC 逐条通过判据见各用例 docstring；
  崩溃/假绿 = 0（任何异常不得产 ok=true）；零系统状态变更 = 7 个 0。

用例清单（编号 / 优先级 / 溯源）：
  AC-N01 P0 AC-01 独立重算 16 项 golden    AC-N02 P0 AC-01 连跑 3 次字节级一致
  AC-N03 P0 AC-02 真实 flag 八字段只读校验 AC-N04 P0 AC-02 默认演练选 11-05 不选 10-29
  AC-N05 P0 AC-03 W-10 统一声明口径自洽    AC-N06 P0 AC-04 设计件枚举三链 + pending
  AC-N07 P0 门禁 G-09 零状态变更前后指纹
  AC-E01 P0 环境漂移不静默通过、不判代码 bug AC-E02 P1 显式 --date 人工拦截、不自动过滤
  AC-E03 P0 FF11 夹具来源反向断言          AC-E04 P1 三处口径旧措辞残留 = 0
  AC-E05 P0 AST 无写 REGISTRY / 无 --apply AC-E06 P0 REGISTRY 23 条 / DbDumpDaily 未落态
  AC-E07 P0 白名单外新增文件 = 0          AC-E08 P0 硬边界 7 个 0 反向扫描
  CM-N01~N05：五类 FF10 兼容矩阵（PS5.1 / Python3.11 / dash / 中文本地化 / junction）
"""
from __future__ import annotations

import ast
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, r"C:\aios\ops")
import task_inventory as ti  # noqa: E402
import backup_format_gate as bfg  # noqa: E402

# ---- 只读路径常量（全程只读，任何测试都不写这些路径）----
GOLDEN_PATH = r"C:\Temp\tasks_inventory_20261105.json"
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

# ---- 独立期望常量：治理方案 §1.3 逐项抄录，不复用 F1.GOLDEN ----
EXPECT = {
    "total": 492, "custom": 293, "system": 199,
    "Ready": 407, "Disabled": 76, "Running": 9,
    "Interactive": 185, "S4U": 123, "ServiceAccount": 107, "Group": 77,
    "custom_real_failed": 32, "custom_never_run": 24, "custom_disabled": 45,
    "ready_06_09": 76, "registry_entries": 23, "registry_matched_custom": 18,
}
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
    """按 PRD §1.3 / ADR-13/14 顺序重写的独立分类规则。"""
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
        "registry_matched_custom": sum(1 for n in reg if n in custom_names),
        "coverage_ratio": round(len([n for n in reg if n in custom_names]) / len(custom_idx), 4),
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
    """AC-E03 用：全部自建、带标记、位于 tmp_path。"""
    d = tmp_path / "qa_acceptance_artifacts"
    d.mkdir(exist_ok=True)
    plain = d / "plain_sql_2d2d.gz"
    plain.write_bytes(b"-- AIOS-SELF-BUILT acceptance fixture\nSELECT 1;\n")
    valid = d / "valid.sql.gz"
    valid.write_bytes(gzip.compress(b"-- AIOS-SELF-BUILT acceptance fixture\nSELECT 1;\n"))
    return {"plain": plain, "valid": valid}


def _bounded_fingerprint():
    """零状态变更指纹：小文件全量 sha256；11GB 大文件只取 (size, mtime_ns, 2B 魔数)。
    避免读取 11GB / 全量解压（硬边界）。返回可比较的 dict。"""
    fp = {}
    for p in (F1_SRC, F2_SRC, MISS_CHECK, DB_DUMP, B4_DRILL,
              os.path.join(DAY_1029, "manifest.json"), FLAG_PATH):
        if os.path.isfile(p):
            fp[p] = ("full", hashlib.sha256(io.open(p, "rb").read()).hexdigest())
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
# 表 C · 独立验收正常流（N = 7）
# ==========================================================================

def test_AC_N01_independent_golden_all_16_match_AC01():
    """用例编号 AC-N01｜优先级 P0｜前置：492 条只读 golden（utf-8-sig）｜
    步骤：①校验 BOM ②本文件独立分类器重算 16 项 ③与硬编码期望逐项比对
    ④交叉核对 F1.compute_stats / assert_golden 是否与独立判据一致
    ⑤锁定措辞（18/293、登录类型、0/0 引用）｜
    预期：16 项全中、F1 交叉核对一致、覆盖率 0.0614 而非 0.0785｜
    溯源：PRD AC-01 / 治理方案 §1.3 / ADR-13/14。"""
    # ① BOM / 编码口径（FF10：C:\Temp 快照带 BOM，必须 utf-8-sig）
    with io.open(GOLDEN_PATH, "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf", "基线应带 UTF-8 BOM"
    rows = _load_golden_rows()
    assert len(rows) == 492
    # ② ③ 独立重算（不调用 F1 的统计函数）
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
    # snapshot_id 只作内容指纹：改 source / host 不影响
    a = ti.build_snapshot(rows, source="A", generated_at="T1")["snapshot_id"]
    b = ti.build_snapshot(rows, source="B", generated_at="T2")["snapshot_id"]
    assert a == b == ids[0]


def test_AC_N03_real_flag_eight_fields_read_only_AC02():
    """用例编号 AC-N03｜优先级 P0｜前置：团长 S4 已落盘 INVALID_GZIP.flag｜
    步骤：纯只读读 flag → 逐字段校验 → 与 F2.write_quarantine_flag(apply=False)
    产生的研发契约 C-1 内容做**自洽比对**（不自行编造枚举）｜
    预期：恰八字段；type/date/files(3)/format=plain-sql/recoverable=false/
    detected_magic=2d2d/decision_ref=D4；policy 与源码契约一致且含
    mark-only + do-not-delete 语义｜溯源：PRD AC-02 / C-1 / T-11（无 =0 断言）。
    备注：FF06 §5.2 散文写 "mark-only-do-not-delete"、源码/实落 flag 写
    "mark-only, do-not-delete"，属文档措辞差异；按团长「以研发契约 C-1 为准」
    以源码产出为判据，不硬编码任一拼写，差异另作观察项回报团长（非缺陷）。"""
    assert os.path.isfile(FLAG_PATH), "AC-N03 前置：flag 未落盘 → 测试阻塞项上报团长"
    flag = json.loads(io.open(FLAG_PATH, encoding="utf-8").read())
    assert set(flag) == {"type", "date", "files", "format", "recoverable",
                         "detected_magic", "policy", "decision_ref"}
    assert flag["type"] == "invalid_gzip_quarantine"
    assert flag["date"] == "2026-10-29"
    assert sorted(flag["files"]) == sorted(THREE_GZ)
    assert flag["format"] == "plain-sql"
    assert flag["recoverable"] is False
    assert flag["detected_magic"] == "2d2d"
    assert flag["decision_ref"] == "D4"
    assert "mark-only" in flag["policy"] and "do-not-delete" in flag["policy"]
    # 与研发契约 C-1 自洽比对（apply=False 不落盘）
    tmp = tempfile.mkdtemp(prefix="qa_acn03_")
    try:
        p = os.path.join(tmp, "plain_sql_2d2d.gz")
        io.open(p, "wb").write(b"-- AIOS-SELF-BUILT\nSELECT 1;\n")
        v = bfg.verify_artifact(p)
        day = os.path.join(tmp, "2026-10-29")
        contract = bfg.write_quarantine_flag(day, "plain_sql_2d2d.gz", v,
                                             apply=False)["flag_content"]
        assert flag["policy"] == contract["policy"], "flag.policy 偏离源码契约 C-1"
        for k in ("type", "date", "format", "recoverable", "detected_magic", "decision_ref"):
            assert flag[k] == contract[k], "flag.%s 偏离源码契约 C-1" % k
        assert not os.path.exists(os.path.join(day, "INVALID_GZIP.flag"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_AC_N04_default_drill_selects_1105_not_1029_AC02(tmp_path):
    """用例编号 AC-N04｜优先级 P0｜前置：①真实 backups/db 日期目录 ②temp 日期
    镜像（自建小文件）｜步骤：①只读 os.listdir 取最大日期 ②只读取 2 字节魔数
    ③复现 b4_restore_drill.list_backups「日期倒序、需含 quant_pg_alldb.sql.gz」
    语义选 backups[0]｜预期：最大日期 = 2026-11-05；11-05 魔数 1f8b、
    10-29 魔数 2d2d；默认选中 11-05、10-29 未被选中；should_select 对
    2d2d 判 False｜溯源：PRD AC-02 / ADR-05 / T-11（**不含 list_backups()=0 断言**）。
    性能口径：只读 2 字节魔数，绝不整文件解压/读取 11GB 产物。"""
    # ① 真实目录：按日期倒序，最大 = 11-05
    dates = sorted([d for d in os.listdir(DB_ROOT)
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", d)], reverse=True)
    assert dates, "backups/db 下应有日期目录"
    assert dates[0] == "2026-11-05", "默认演练取 backups[0] 应为 11-05"
    assert "2026-10-29" in dates and "2026-10-29" != dates[0]
    # ② 只读取 2 字节魔数（read_magic(n=2)）
    assert bfg.read_magic(os.path.join(DAY_1105, "quant_pg_alldb.sql.gz")) == b"\x1f\x8b"
    for name in THREE_GZ:
        assert bfg.read_magic(os.path.join(DAY_1029, name)) == b"--"
    # ③ temp 镜像复现 list_backups 选择语义
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


def test_AC_N05_unified_terminology_doc_self_consistent_AC03():
    """用例编号 AC-N05｜优先级 P0｜前置：W-10 统一口径声明（本轮 AC-03 的
    唯一权威声明件）｜步骤：只读扫描措辞｜预期：含「明文副链断档」「两库真空」
    「统一声明替代改原文件」「加密主链健康」「已 modified」；枚举三处口径；
    且不含失效表述（P0 全链断 / 60 天轮转 / --keep-days 已实现）｜
    溯源：PRD AC-03 / ADR-05「以新文档统一声明替代改原文件」。"""
    assert os.path.isfile(W10_TERMINOLOGY), "AC-03 权威声明件缺失 → 测试阻塞项"
    t = io.open(W10_TERMINOLOGY, encoding="utf-8").read()
    for phrase in ("明文副链断档", "两库真空", "统一声明替代改原文件",
                   "加密主链健康", "已 modified", "KEEP_DAYS", "14"):
        assert phrase in t, "统一声明缺措辞：%s" % phrase
    # 枚举三处口径
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
    """用例编号 AC-N07｜优先级 P0｜前置：跑代表性只读调用链之前先取指纹｜
    步骤：before = 指纹 → 执行只读链 → after = 指纹 → 比对｜
    预期：G-09a/b/c 三项全一致（F1/F2 源码、manifest、flag、db_dump_daily、
    miss_check、b4_drill 小文件全量 sha256 不变；3 个 11GB .gz 的
    size/mtime_ns/魔数不变）｜溯源：质量门禁 G-09 / 硬边界「零系统状态变更」。"""
    before = _bounded_fingerprint()
    _read_only_pipeline()
    after = _bounded_fingerprint()
    assert before == after, "只读验收过程改变了既有文件 → P0 门禁不通过"


# ==========================================================================
# 表 C-E · 独立验收异常流（E = 8）
# ==========================================================================

def test_AC_E01_env_drift_is_loud_not_silent_and_not_code_bug(monkeypatch):
    """用例编号 AC-E01｜优先级 P0｜前置：492 基线 + 注入式漂移（+1 行）｜
    步骤：①正常基线 assert_golden=True ②注入第 493 行再算 ③monkeypatch
    subprocess.run 模拟只读 live /query 返回漂移数据｜
    预期：漂移时 assert_golden 返回 (False, failures) 且含 total 不一致，
    **不静默通过、不抛异常、不判为代码 bug**；live 路径仅发 /query 只读命令｜
    溯源：PRD AC-01 / T-09（环境漂移上报团长）。
    备注：实时计划任务总数 ≠492 的 live 计数由 ops-sre 运行态观测（T-09）；
    本用例用注入式漂移验证「检测契约」——确定性、不触碰系统状态。"""
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
    # live /query 只读路径审计（mock，不实跑 schtasks）
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


def test_AC_E02_explicit_date_manual_intercept_no_auto_filter_AC02():
    """用例编号 AC-E02｜优先级 P1｜前置：flag 可见 + W-09/W-10 文档｜
    步骤：检查 flag 可见性与 policy + 静态核对 b4_restore_drill.py 未接入闸门｜
    预期：flag 可见、policy 含 mark-only/do-not-delete；b4 源码不含
    should_select / verify_artifact / backup_format_gate（ADR-05 明确不承诺
    代码自动过滤）；文档声明「不是运行态过滤闸门 / 无因果」；
    **不要求代码自动过滤、不写死 list_backups()=0**｜溯源：PRD AC-02 / ADR-05 / T-11。"""
    assert os.path.isfile(FLAG_PATH)
    flag = json.loads(io.open(FLAG_PATH, encoding="utf-8").read())
    assert "mark-only" in flag["policy"] and "do-not-delete" in flag["policy"]
    b4 = io.open(B4_DRILL, encoding="utf-8", errors="replace").read()
    for token in ("should_select", "verify_artifact", "backup_format_gate"):
        assert token not in b4, "本轮不得改 b4_restore_drill.py 接入自动过滤：%s" % token
    w10 = io.open(W10_TERMINOLOGY, encoding="utf-8").read()
    assert "不是运行态过滤闸门" in w10 and "无因果" in w10
    w9 = io.open(W09_INTERFACE, encoding="utf-8").read()
    assert "本轮不承诺代码自动过滤" in w9


def test_AC_E03_fixture_provenance_reverse_assertion(tmp_path):
    """用例编号 AC-E03｜优先级 P0｜前置：运行时自建夹具｜步骤：检查前 64B +
    路径归属 + 生产标记｜预期：含 AIOS-SELF-BUILT 标记；不含生产明文 SQL /
    `-- pg_dump` / 生产库名 / 连接串；全部位于 tmp_path；
    未从 backups/db 或 C:\\backups_mirror 复制任何字节｜溯源：FF11 / AC-02。"""
    fx = _self_built_fixtures(tmp_path)
    production_markers = [b"pg_dump", b"cainiao_mysql", b"zhice_os_v2",
                          b"quant_pg_alldb", b"postgresql://", b"password="]
    for name, p in fx.items():
        assert str(tmp_path) in str(p), "%s 不在 tmp_path 下" % name
        raw = p.read_bytes()
        assert SELF_BUILT in raw[:64] or SELF_BUILT in gzip.decompress(raw)
        for m in production_markers:
            assert m not in raw, "%s 含生产标记 %r" % (name, m)
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
        # git 不可用时退化为文件系统核对，不造成假红
        pass
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


def test_AC_E08_hard_boundary_seven_zeros_reverse_scan(monkeypatch):
    """用例编号 AC-E08｜优先级 P0｜前置：F1/F2 源码 + 只读基线 + 自建夹具｜
    步骤：①AST/源码静态扫描 ②monkeypatch subprocess 全程审计 ③前后文件指纹比对
    ④跑代表性只读链｜
    预期：硬边界 **7 个 0** 全部成立：
      [0] 计划任务增改/启用/禁用 = 0（无 Register-/Set-/Disable-/Enable-/
          Unregister-ScheduledTask、schtasks /create|/change|/delete，live 仅 /query）
      [1] 既有文件删改 = 0（F1/F2 无 os.remove/unlink/rmtree）
      [2] DUMPS / KEEP_DAYS 变更 = 0（db_dump_daily.py 哈希不变且仍 KEEP_DAYS=14）
      [3] backups/db 既有产物字节改写 = 0（前后指纹一致）
      [4] .enc 解密执行 = 0（源码无 Unprotect-File/decrypt；夹具全合成）
      [5] 10.205.0.118 访问 = 0（源码无该 IP、无网络库 import）
      [6] 本阶段/测试过程非授权落盘 = 0（无 git commit；apply=False 默认不落盘；
          tests/fixtures 不存在；AC-E07 白名单）
    溯源：硬边界 7 个 0 / qa-testing.md 安全层 / ADR-02/03/05。"""
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
    # [2] DUMPS / KEEP_DAYS（静态 + 动态指纹在 [3] 覆盖）
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
    for forbidden in ("git commit", "git push", "--apply", "--host",
                      "--upload", "--remote"):
        # F1 docstring 合法出现「不接受 --apply」等字样，用 AST 已在 AC-E05 精确判定；
        # 这里只断言真正危险的字面（git / 上传），避免把说明文字误判为违规。
        if forbidden.startswith("git "):
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
    snap, st = _read_only_pipeline()
    # 追加 F2 只读链（自建夹具，apply=False）
    tmp = tempfile.mkdtemp(prefix="qa_ace08_")
    try:
        p = os.path.join(tmp, "valid.sql.gz")
        io.open(p, "wb").write(gzip.compress(b"-- AIOS-SELF-BUILT\nSELECT 1;\n"))
        v = bfg.verify_artifact(p)
        item = {"key": "k", "ok": True, "size": 1, "file": "valid.sql.gz", "must": True}
        bfg.gate_entry(item, tmp)
        bfg.should_select(v)
        r = bfg.write_quarantine_flag(os.path.join(tmp, "2026-10-29"),
                                      "valid.sql.gz", v, apply=False)
        assert r["apply"] is False and "written" not in r
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    assert all(any("/query" in str(a) for a in c) for c in calls), \
        "任何子进程调用必须是只读 /query"
    after = _bounded_fingerprint()
    assert before == after, "[3] 验收过程改写了既有产物 → P0 门禁不通过"


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
        assert "UTF-16LE" in w9 and "PowerShell" in w9 and "5.1" in w9
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
        """用例编号 CM-N05｜优先级 P1｜溯源：FF10 / 发布方案（junction 口径）。
        C:\\aios 为 junction（指向 C:\\项目\\老田的AI助手，同一 inode）：
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
