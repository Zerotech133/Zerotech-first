# -*- coding: utf-8 -*-
r"""W-05 · ops/task_inventory.py 单元测试（qa-tester 单写者落盘）。

【委派】team-lead ADR-08 修订委派 W-05；只新建本文件，不改任何既有文件。
【上游】PRD 最终版 AC-01（F-01 任务快照台账）、治理方案 §1.3 golden、
        全栈研发师《技术研发方案》§9.3 单测清单（F1 侧 8 条）、
        qa-testing.md §三用例模板 / §五质量门禁。
【解释器】"C:\Program Files\Python311\python.exe" -X utf8
【只读基线】C:\Temp\tasks_inventory_20261105.json（带 BOM → encoding="utf-8-sig"，
            492 条 list）。严禁从 backups/db 复制任何生产产物（FF11）。

四层覆盖映射（qa-testing.md §一）：
  功能：TI-N01~N14（正常流，等价类/边界值/场景法/状态迁移）
  兼容：TI-E01（utf-8 / mbcs(gbk) / BOM 双解码 + 中文本地化标签），
        W-07 TestFF10Compatibility 承载 PS5.1 / dash / junction 完整矩阵
  性能：BG-N08 声明 PROBE_BYTES/SHA256_CHUNK 设计预算（G0-b 不实测、不设阈值）；
        本文件 TI-N12 只锁「可重复运行字节级一致」，不跑真实压测
  安全：TI-E08（AST 静态扫描无写路径/无 --apply）、TI-E05（环境不可用不产半成品）、
        W-07 AC-E08 承载硬边界 7 个 0 反向扫描

用例清单（编号 / 优先级 / 溯源）：
  TI-N01 P0 AC-01 golden 16 项字节级一致       TI-N02 P0 AC-01 噪声 RC 排除（8 码参数化）
  TI-N03 P0 AC-01 1999 哨兵全变体（4 变体）    TI-N04 P0 AC-01 系统三前缀排除
  TI-N05 P0 AC-01 diff.removed 仅软删除        TI-N06 P0 AC-04 suggest_registry applied=false
  TI-N07 P0 AC-01 atomic_write 无半截 JSON     TI-N08 P1 AC-01 枚举 7 值
  TI-N09 P0 ADR-14 classify 顺序               TI-N10 P0 ADR-13 两套统计口径分离
  TI-N11 P0 §5.4 snapshot_id 排除生成侧元数据  TI-N12 P0 AC-01 连跑 3 次字节级一致
  TI-N13 P0 AC-01 覆盖率 18/293（禁 23/293）  TI-N14 P0 AC-01 五子命令 rc=0
  TI-E01 P0 FF10 中文本地化 / utf-8·mbcs 双解   TI-E02 P0 噪声 RC 与真实失败码混排
  TI-E03 P0 序列化失败不产半截 JSON            TI-E04 P0 0 任务不静默通过
  TI-E05 P0 权限/环境不可用 rc=2 不产半成品    TI-E06 P1 diff 新增/变更检测
  TI-E07 P0 Ready/Disabled 且 rc!=0 仍真实失败 TI-E08 P0 AST 无写 REGISTRY / 无 --apply
  TI-E09 P1 非法 RC 输入不抛异常               TI-E10 P1 基线缺失/坏 JSON 显式报错

异常流 10 条 ≥ 正常流 14 条的一半（7），满足 qa-testing.md 错误预防条款。
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import sys

import pytest

sys.path.insert(0, r"C:\aios\ops")
import task_inventory as ti  # noqa: E402

GOLDEN_PATH = r"C:\Temp\tasks_inventory_20261105.json"

# 独立期望常量：从治理方案 §1.3 抄录，不复用 F1 的 GOLDEN 常量做自证。
EXPECT = {
    "total": 492, "custom": 293, "system": 199,
    "Ready": 407, "Disabled": 76, "Running": 9,
    "Interactive": 185, "S4U": 123, "ServiceAccount": 107, "Group": 77,
    "custom_real_failed": 32, "custom_never_run": 24, "custom_disabled": 45,
    "ready_06_09": 76, "registry_entries": 23, "registry_matched_custom": 18,
}
NOISE_RC_NAMED = (267009, 267011, 2147943140, 1073807364)  # 规格点名的 4 码
SENTINEL_VARIANTS = (
    "1999-11-30", "1999/11/30",
    "1999-11-30 00:00:00", "1999/11/30 00:00:00",
)


def _load_golden():
    """只读基线；BOM 必须用 utf-8-sig 解码。"""
    with io.open(GOLDEN_PATH, "r", encoding="utf-8-sig") as f:
        rows = json.load(f)
    assert isinstance(rows, list), "基线应为 492 条 list"
    return rows


def _assert_golden_quiet(stats):
    """assert_golden 会打印 ord() 码点复核表，收拢 stdout 保持 -q 输出干净。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return ti.assert_golden(stats)


def _task(**kw):
    """构造可直接喂给 classify() 的最小 task dict（自定义路径）。"""
    d = {"name": "X", "path": r"\Custom\X", "state": "Ready",
         "last_rc": 0, "last_run_at": "2026-11-05T08:00:00",
         "next_run_at": "2026-11-06T08:00:00"}
    d.update(kw)
    return d


# --------------------------------------------------------------------------
# 正常流（N）
# --------------------------------------------------------------------------

def test_TI_N01_golden_stats_match_plan_1_3():
    """用例编号 TI-N01｜优先级 P0｜前置：只读 492 条基线｜步骤：build_snapshot→
    compute_stats→逐项比对 16 项 + assert_golden｜预期：16 项全中、assert_golden
    返回 (True, [])｜溯源：PRD AC-01 / 治理方案 §1.3。"""
    snap = ti.build_snapshot(_load_golden(), source="file", generated_at="FIXED")
    st = ti.compute_stats(snap)
    for k, v in EXPECT.items():
        got = st.get(k, st.get("by_state", {}).get(k, st.get("by_logon_type", {}).get(k)))
        assert got == v, "%s: got=%r expect=%r" % (k, got, v)
    ok, failures = _assert_golden_quiet(st)
    assert ok is True and failures == []


@pytest.mark.parametrize("rc", sorted(ti.SCHEDULER_NOISE_RC))
def test_TI_N02_noise_rc_excluded_from_real_failed(rc):
    """用例编号 TI-N02｜优先级 P0｜前置：自定义任务、rc=噪声码｜步骤：classify｜
    预期：scheduled_noise，绝不计入 real_failed｜溯源：AC-01 / ADR-13。
    备注：规格点名 4 码，源码 frozenset 实为 8 码（含 267008/267010/
    2147942402/2147946720），按源码全量参数化以锁死真实契约。"""
    assert rc in ti.SCHEDULER_NOISE_RC
    assert set(NOISE_RC_NAMED).issubset(set(ti.SCHEDULER_NOISE_RC))
    assert ti.classify(_task(last_rc=rc)) == "scheduled_noise"


@pytest.mark.parametrize("last", SENTINEL_VARIANTS)
def test_TI_N03_never_run_sentinels_all_variants(last):
    """用例编号 TI-N03｜优先级 P0｜前置：last_run_at 为 1999 家族变体、rc=3｜
    步骤：classify｜预期：never_run（哨兵优先于真实失败）｜溯源：AC-01。"""
    assert ti.classify(_task(last_run_at=last, last_rc=3)) == "never_run"


@pytest.mark.parametrize("prefix", ti.SYSTEM_PATH_PREFIXES)
def test_TI_N04_system_path_three_prefixes_excluded_from_custom(prefix):
    """用例编号 TI-N04｜优先级 P0｜前置：路径命中三前缀之一｜步骤：normalize_task｜
    预期：is_system=True、category=system，不进自定义分母｜溯源：AC-01 / ADR-13。"""
    t = ti.normalize_task({"Name": "Sys", "Path": prefix + "Job"})
    assert t["is_system"] is True
    assert t["category"] == "system"


def test_TI_N05_diff_removed_marks_soft_delete():
    """用例编号 TI-N05｜优先级 P0｜前置：base 含 P，cur 不含 P｜步骤：diff_snapshots｜
    预期：removed 仅带 deleted_at/quarantined 软删除标记，无物理删除路径｜
    溯源：AC-01 / 硬边界。"""
    base = {"tasks": [ti.normalize_task({"Name": "P", "Path": r"\P", "State": "Ready"})]}
    cur = {"tasks": []}
    d = ti.diff_snapshots(base, cur)
    assert d["summary"]["removed"] == 1
    r = d["removed"][0]
    assert r["path"] == r"\P" and r["deleted_at"] is None and r["quarantined"] is True
    # 反向：源码中不存在任何物理删除调用
    src = io.open(r"C:\aios\ops\task_inventory.py", encoding="utf-8").read()
    for forbidden in ("os.remove(", "os.unlink(", "shutil.rmtree(", "os.rmdir("):
        assert forbidden not in src, "diff 出现物理删除调用：%s" % forbidden


def test_TI_N06_suggest_registry_applied_false_and_no_write():
    """用例编号 TI-N06｜优先级 P0｜前置：492 条快照｜步骤：suggest_registry｜
    预期：applied=False、expect 恒 None、needs_human_confirm=True、DbDumpDaily
    status=pending、CainiaoPull 冲突保留；miss_check.py 字节哈希不变｜
    溯源：AC-04 / ADR-03/05/11。"""
    reg = r"C:\aios\quant_backtest\miss_check.py"
    before = hashlib.sha256(io.open(reg, "rb").read()).hexdigest()
    snap = ti.build_snapshot(_load_golden(), source="file", generated_at="FIXED")
    out = ti.suggest_registry(snap)
    assert out["applied"] is False
    assert out["policy"].startswith("candidates-only")
    names = {c["name"]: c for c in out["candidates"]}
    assert set(names) == {"JevRestoreDrill", "BackupDaily", "ResticTrialDaily", "DbDumpDaily"}
    assert names["DbDumpDaily"]["status"] == "pending"
    assert all(c["expect"] is None and c["needs_human_confirm"] is True
               for c in out["candidates"])
    assert any(c["name"] == "CainiaoPull" for c in out["conflicts"])
    assert hashlib.sha256(io.open(reg, "rb").read()).hexdigest() == before


def test_TI_N07_atomic_write_no_partial_json(tmp_path):
    """用例编号 TI-N07｜优先级 P0｜前置：tmp 目录｜步骤：atomic_write_json 写含中文的
    完整对象｜预期：目标文件为完整合法 UTF-8 JSON，圆读一致，无 .tmp 残留｜
    溯源：AC-01 / qa-testing.md §三边界值。"""
    p = tmp_path / "snap.json"
    obj = {"a": 1, "中": "文", "tasks": [{"name": "x"}]}
    ti.atomic_write_json(str(p), obj)
    assert json.loads(p.read_text(encoding="utf-8")) == obj
    assert not list(tmp_path.glob(".task_inv_*.tmp")), "成功路径不得残留临时文件"


def test_TI_N08_state_machine_values_are_enum():
    """用例编号 TI-N08｜优先级 P1｜前置：无｜步骤：读 VALID_CATEGORIES｜
    预期：恰为 7 值枚举｜溯源：AC-01 分类契约。"""
    assert set(ti.VALID_CATEGORIES) == {
        "ready", "disabled", "running", "real_failed",
        "never_run", "scheduled_noise", "system",
    }
    assert len(ti.VALID_CATEGORIES) == 7


def test_TI_N09_classify_order_is_deterministic():
    """用例编号 TI-N09｜优先级 P0｜前置：构造各分支任务｜步骤：classify 逐跳｜
    预期：顺序 never_run→noise→system→real_failed→disabled→running→ready｜
    溯源：AC-01 / ADR-14（real_failed 必须早于 state 判定）。"""
    assert ti.classify(_task(last_run_at="1999-11-30 00:00:00", last_rc=3,
                             path=r"\ZeroBound\X")) == "never_run"   # 哨兵压过 system/noise
    assert ti.classify(_task(last_rc=267009, path=r"\Microsoft\X")) == "scheduled_noise"
    assert ti.classify(_task(last_rc=3, path=r"\Microsoft\X")) == "system"  # system 压过 rc!=0
    assert ti.classify(_task(last_rc=3)) == "real_failed"
    assert ti.classify(_task(state="Disabled", last_rc=0)) == "disabled"
    assert ti.classify(_task(state="Running", last_rc=0)) == "running"
    assert ti.classify(_task(state="Ready", last_rc=0)) == "ready"


def test_TI_N10_stats_two_calibers_separated():
    """用例编号 TI-N10｜优先级 P0｜前置：492 条基线｜步骤：compute_stats｜
    预期：by_state/custom_disabled 按原始 state；custom_real_failed/never_run
    按 category；两者分离且分别命中 golden｜溯源：AC-01 / ADR-13。"""
    st = ti.compute_stats(ti.build_snapshot(_load_golden(), source="file", generated_at="FIXED"))
    assert st["by_state"] == {"Ready": 407, "Disabled": 76, "Running": 9}
    assert st["custom_disabled"] == 45
    assert st["custom_real_failed"] == 32
    assert st["custom_never_run"] == 24
    assert st["by_logon_type"] == {
        "Interactive": 185, "S4U": 123, "Group": 77, "ServiceAccount": 107}


def test_TI_N11_snapshot_id_excludes_generation_metadata(monkeypatch):
    """用例编号 TI-N11｜优先级 P0｜前置：同一 492 条｜步骤：改 source/generated_at/
    COMPUTERNAME 各生成一次｜预期：snapshot_id 全等且为 16 位 hex（仅内容指纹）｜
    溯源：FF06 §5.4 snapshot_id 不变式。"""
    rows = _load_golden()
    a = ti.build_snapshot(rows, source="A", generated_at="T1")["snapshot_id"]
    b = ti.build_snapshot(rows, source="B", generated_at="T2")["snapshot_id"]
    monkeypatch.setenv("COMPUTERNAME", "HOST_QA_X")
    c = ti.build_snapshot(rows, source="A", generated_at="T1")["snapshot_id"]
    assert a == b == c and len(a) == 16 and int(a, 16) >= 0


def test_TI_N12_repeatable_three_runs_bytes_identical():
    """用例编号 TI-N12｜优先级 P0｜前置：同一只读基线｜步骤：连跑 3 次
    build_snapshot（固定 generated_at）｜预期：snapshot_id 与 tasks 规范化 JSON
    字节级一致；compute_stats 的 16 项 golden 逐次相同｜溯源：AC-01 可重复运行。"""
    rows = _load_golden()
    ids, payloads, goldens = [], [], []
    for _ in range(3):
        s = ti.build_snapshot(rows, source="file", generated_at="FIXED")
        ids.append(s["snapshot_id"])
        payloads.append(json.dumps(s["tasks"], ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")))
        st = ti.compute_stats(s)
        goldens.append(tuple(st.get(k, st.get("by_state", {}).get(k,
                       st.get("by_logon_type", {}).get(k))) for k in EXPECT))
    assert len(set(ids)) == 1 and len(set(payloads)) == 1
    assert len(set(goldens)) == 1 and goldens[0] == tuple(EXPECT.values())


def test_TI_N13_registry_coverage_18_of_293_not_23():
    """用例编号 TI-N13｜优先级 P0｜前置：492 条基线｜步骤：compute_stats｜
    预期：23 条 REGISTRY 命中 18 个自定义任务，覆盖率 18/293≈6.1%（0.0614），
    严禁写成 23/293≈7.8%；db_dump_daily/cainiao_pull 引用均 0｜溯源：AC-01 锁定措辞。"""
    st = ti.compute_stats(ti.build_snapshot(_load_golden(), source="file", generated_at="FIXED"))
    assert st["registry_entries"] == 23
    assert st["registry_matched_custom"] == 18
    assert st["custom"] == 293
    assert st["registry_coverage_ratio"] == round(18 / 293, 4) == 0.0614
    assert st["registry_coverage_ratio"] != round(23 / 293, 4)
    assert st["referenced"] == {"db_dump_daily": 0, "cainiao_pull": 0}


@pytest.mark.parametrize("argv,expect_rc", [
    (["export", "--source", "file", "--file", GOLDEN_PATH, "--out", "{out}"], 0),
    (["snapshot", "--source", "file", "--file", GOLDEN_PATH, "--out", "{out}"], 0),
    (["diff", "--base", GOLDEN_PATH, "--cur", GOLDEN_PATH, "--out", "{out}"], 0),
    (["stats", "--snap", GOLDEN_PATH, "--assert-golden"], 0),
    (["suggest-registry", "--snap", GOLDEN_PATH, "--out", "{out}"], 0),
])
def test_TI_N14_cli_five_subcommands_rc0(tmp_path, argv, expect_rc):
    """用例编号 TI-N14｜优先级 P0｜前置：注入式 file 模式｜步骤：五个只读 CLI
    子命令各跑一次｜预期：rc=0；带 --out 的落 tmp 且为合法 JSON｜
    溯源：AC-01 五子命令。"""
    out = str(tmp_path / "out.json")
    args = [a.replace("{out}", out) for a in argv]
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ti.main(args)
    assert rc == expect_rc
    if "--out" in args:
        assert json.loads(io.open(out, encoding="utf-8").read())


# --------------------------------------------------------------------------
# 异常流（E）
# --------------------------------------------------------------------------

def test_TI_E01_decode_utf8_mbcs_gbk_bom_and_chinese_labels():
    """用例编号 TI-E01｜优先级 P0｜前置：构造 utf-8 / gbk(mbcs) / BOM 三种字节，
    含中文本地化标签｜步骤：_decode｜预期：三种均能解出中文，BOM 被剥离；
    该分支即 FF10「schtasks 中文本地化输出 + utf-8/mbcs 双解码」的可执行映射｜
    溯源：AC-01 / FF10 / qa-testing.md 兼容层。"""
    zh = "状态: 就绪 上次运行时间: 从不"
    assert ti._decode(zh.encode("utf-8")) == zh
    assert ti._decode(zh.encode("gbk")) == zh          # mbcs 在本机 = gbk
    assert ti._decode(b"\xef\xbb\xbf" + zh.encode("utf-8")) == zh
    assert ti._decode("任务名".encode("utf-8")) == "任务名"
    # 契约：解码链恰为 utf-8-sig/utf-8/mbcs/gbk，不臆造 UTF-16LE 分支
    assert ("utf-8-sig", "utf-8", "mbcs", "gbk") in ti._decode.__code__.co_consts


def test_TI_E02_noise_rc_and_real_failed_mixed():
    """用例编号 TI-E02｜优先级 P0｜前置：8 个噪声码 + 3 个真实失败码 + 1 哨兵
    混排｜步骤：normalize_task→classify 计数｜预期：scheduled_noise=8、
    real_failed=3、never_run=1，互不串味｜溯源：AC-01 / ADR-13。"""
    rows = []
    for rc in sorted(ti.SCHEDULER_NOISE_RC):
        rows.append({"Name": "N%d" % rc, "Path": r"\N", "State": "Ready", "RC": rc})
    for rc in (1, 2, 3):
        rows.append({"Name": "R%d" % rc, "Path": r"\R", "State": "Ready", "RC": rc})
    rows.append({"Name": "S", "Path": r"\S", "State": "Ready",
                 "RC": 3, "Last": "1999/11/30 00:00:00"})
    cats = [ti.normalize_task(r)["category"] for r in rows]
    assert cats.count("scheduled_noise") == 8
    assert cats.count("real_failed") == 3
    assert cats.count("never_run") == 1


def test_TI_E03_serialization_fault_produces_no_partial_json(tmp_path):
    """用例编号 TI-E03｜优先级 P0｜前置：不可序列化对象｜步骤：atomic_write_json
    抛异常后检查目标｜预期：目标文件不存在（无半截 JSON），异常显式上抛｜
    溯源：AC-01 / qa-testing.md §三异常流「JSON 半截写入」。"""
    p = tmp_path / "bad.json"
    with pytest.raises(Exception):
        with contextlib.redirect_stderr(io.StringIO()):
            ti.atomic_write_json(str(p), {"x": object()})
    assert not p.exists(), "序列化失败不得留下半截目标文件"


def test_TI_E04_empty_snapshot_does_not_silently_pass(tmp_path):
    """用例编号 TI-E04｜优先级 P0｜前置：0 任务快照｜步骤：compute_stats +
    assert_golden + CLI stats --assert-golden｜预期：ratio=0.0 不除零、
    assert_golden 返回 ok=False（不静默通过）、CLI rc=1（数据校验失败）｜
    溯源：AC-01 / 退出码映射 B 册（rc=1 数据校验失败）。"""
    s = ti.build_snapshot([], source="empty", generated_at="FIXED")
    st = ti.compute_stats(s)
    assert st["total"] == 0 and st["custom"] == 0
    assert st["registry_coverage_ratio"] == 0.0
    ok, failures = _assert_golden_quiet(st)
    assert ok is False and len(failures) >= 15
    p = str(tmp_path / "qa_empty_snap.json")   # 全程落在 pytest 管理的 tmp_path
    ti.atomic_write_json(p, s)
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ti.main(["stats", "--snap", p, "--assert-golden"])
    assert rc == 1


@pytest.mark.parametrize("argv", [
    ["export", "--source", "file"],                     # 缺 --file
    ["diff", "--base", r"C:\no_such_base.json", "--cur", GOLDEN_PATH],
    ["stats", "--snap", r"C:\no_such_snap.json"],
])
def test_TI_E05_cli_env_unavailable_rc2_no_half_product(tmp_path, argv):
    """用例编号 TI-E05｜优先级 P0｜前置：权限/环境不可用或基线缺失｜步骤：调用
    main｜预期：rc=2（环境不可用），不产半成品目标文件｜溯源：AC-01 / 退出码
    映射 B 册（rc=2 EMPTY_SNAPSHOT / 环境不可用）。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = ti.main(argv)
    assert rc == 2
    assert not list(tmp_path.glob("*.json")), "环境不可用不得产半成品"


def test_TI_E06_diff_added_and_changed_detection():
    """用例编号 TI-E06｜优先级 P1｜前置：base 2 任务，cur 保留 1 改 1 加 1｜
    步骤：diff_snapshots｜预期：added=1 / changed=1 / unchanged=1 / removed=0｜
    溯源：AC-01 / qa-testing.md 场景法。"""
    def row(name, state, rc):
        return ti.normalize_task({"Name": name, "Path": "\\" + name,
                                  "State": state, "RC": rc})
    base = {"tasks": [row("A", "Ready", 0), row("B", "Ready", 0)]}
    cur = {"tasks": [row("A", "Ready", 0), row("B", "Disabled", 3), row("C", "Ready", 0)]}
    d = ti.diff_snapshots(base, cur)
    assert d["summary"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 1}
    assert d["changed"][0]["path"] == r"\B"
    assert "state" in d["changed"][0]["fields"] and "last_rc" in d["changed"][0]["fields"]


@pytest.mark.parametrize("state,rc", [("Ready", 3), ("Disabled", 2), ("Running", 1)])
def test_TI_E07_real_failed_precedes_state(state, rc):
    """用例编号 TI-E07｜优先级 P0｜前置：Ready/Disabled/Running 且 rc!=0｜
    步骤：classify｜预期：一律 real_failed（不被状态机吞掉）｜溯源：AC-01 / ADR-14。"""
    assert ti.classify(_task(state=state, last_rc=rc)) == "real_failed"


def test_TI_E08_ast_no_write_path_and_no_apply_option():
    """用例编号 TI-E08｜优先级 P0｜前置：F1 源码｜步骤：AST 解析（避免把
    docstring 里「不接受 --apply」「miss_check.py」的说明文字误判为写路径）｜
    预期：无 --apply/--host/--upload/--remote 的 add_argument；无对
    miss_check.py/REGISTRY 的写模式 open；无 schtasks 变更子命令｜
    溯源：AC-04 / ADR-06/10 / qa-testing.md 安全层。"""
    src = io.open(r"C:\aios\ops\task_inventory.py", encoding="utf-8").read()
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
            mode = n.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                    and any(m in mode.value for m in ("w", "a", "x", "+")):
                tgt = n.args[0]
                if isinstance(tgt, ast.Constant) and isinstance(tgt.value, str) \
                        and ("miss_check" in tgt.value or "REGISTRY" in tgt.value):
                    write_opens.append(tgt.value)
    assert banned_add == set(), "出现禁用的写参数：%s" % banned_add
    assert write_opens == [], "出现写 REGISTRY 的路径：%s" % write_opens
    for cmd in ("schtasks /create", "schtasks /change", "schtasks /delete",
                "Register-ScheduledTask", "Enable-ScheduledTask",
                "Disable-ScheduledTask", "New-ScheduledTask"):
        assert cmd not in src


@pytest.mark.parametrize("raw,want", [("3", 3), ("0", 0), ("", None),
                                       (None, None), ("abc", None), ("12x", None)])
def test_TI_E09_normalize_bad_rc_never_raises(raw, want):
    """用例编号 TI-E09｜优先级 P1｜前置：非法/空/缺失 RC 输入｜步骤：
    normalize_task｜预期：鲁棒收敛，不抛异常，rc 归位｜溯源：AC-01 边界值。"""
    t = ti.normalize_task({"Name": "X", "RC": raw})
    assert t["last_rc"] == want


def test_TI_E10_missing_or_bad_baseline_raises_loudly(tmp_path):
    """用例编号 TI-E10｜优先级 P1｜前置：基线文件缺失 / 坏 JSON｜步骤：
    _load_rows 等价路径｜预期：显式报错而非返回 492 或空集静默通过｜
    溯源：AC-01「基线缺失→NO_BASELINE 不误报 492 新增」。"""
    missing = str(tmp_path / "no.json")
    with pytest.raises((OSError, ValueError)):
        ti._load_rows(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ti._load_rows(str(bad))
    # 正常形态：{"tasks":[...]} 亦可被接受（增强鲁棒性）
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"tasks": [{"Name": "X"}]}), encoding="utf-8-sig")
    assert len(ti._load_rows(str(good))) == 1
