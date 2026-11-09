# -*- coding: utf-8 -*-
"""数据仓 HTTP stats 与 catalog/registry 口径对齐。

2026-11-06 R9 追记（链中态识别）：
  `KgDailySync` 计划任务每日 06:20 触发 `start_kg_sync.ps1`（22 步链）。**第一步**
  `build_catalog.py` 会重写 `quant_backtest/data/catalog.json`（updated 时间戳与 sha256
  必然改变），而 `warehouse_registry.json` 的 `catalog_sha256` 只在**链尾**
  `registry_align.py --apply` 时才更新。两者之间隔着 20 多分钟的整条链。

  → 每天约 06:24～06:48 这段，两者**必然**失配。本测试原本会在该窗口内必红，
    是一条「必然自愈」的假红（2026-11-06 06:24 全量回归即撞上，06:48:47 链尾自愈）。

  处理：sha 断言不再无条件红，先读 `C:\\Temp\\kg_sync_task.log` 判定今日链路状态 ——
    · 今日有 `catalog_build exit=0`、无 `registry_align ... exit=0`、且链未收尾 → **skip**（链中态）
    · 链已收尾（`kg_sync OK` / `kg_sync WARN registry_align`）却仍不齐 → **真红**
    · 今日无任何链记录 / 无日志文件 → 按 catalog.updated 距今 < CHAIN_WINDOW_HOURS 兜底 skip，
      超窗口仍不齐 → 真红（保留「registry 长期不重建」的原守卫能力）
  其余断言（version / tables / physical / display / updated）不受影响，任何时刻都生效。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import api_dw_registry as dw  # noqa: E402

# 2026-11-04 棘轮加固：catalog_version 每次 registry_align 都会递增（v38→v39…），
# 硬编码会让无关的数据注册批次阻断所有合并窗口；改为与 live catalog.json 动态比对。
# v39 语义（2026-11-04 06:24 重建）：catalog.tables = 已登记/展示表（256），
# registry.physical_count = 数据根物理表（304，含未登记物理产物）→ 不再要求两者相等，
# 改用 registry.catalog_sha256 与 live catalog.json sha256 比对（更严格的"同源"证明）。
CATALOG = ROOT / "quant_backtest" / "data" / "catalog.json"

KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
CHAIN_WINDOW_HOURS = 3


def _kg_chain_state(today: str | None = None, log_path: Path | None = None) -> str:
    """今日 KgDailySync 链路状态（只读日志文本，不猜时间）。

    返回：
      aligned             今日已有 registry_align --apply 成功记录
      in_chain            今日 catalog 已重建，但链尾对齐还没执行、链也没收尾 → 链中态
      chain_done_unaligned 链已收尾（OK/WARN）却无对齐成功记录 → 真问题
      no_record           日志文件在，但今日无记录
      no_log              日志文件不存在（异地/CI）
    """
    today = today or datetime.date.today().isoformat()
    p = Path(log_path) if log_path is not None else KG_LOG
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "no_log"
    mine = [ln for ln in text.splitlines() if today in ln]
    if not mine:
        return "no_record"
    joined = "\n".join(mine)
    has_catalog = "catalog_build exit=0" in joined
    has_align_ok = bool(re.search(r"registry_align .*action=apply .*exit=0", joined))
    # 收尾行三种形态：成功 / 旧格式告警（11-05 前）/ R9 起的 STALE 标记
    has_end = (("kg_sync OK" in joined)
               or ("kg_sync WARN registry_align" in joined)
               or ("kg_sync STALE" in joined))
    if has_align_ok:
        return "aligned"
    if has_catalog and not has_end:
        return "in_chain"
    if has_catalog and has_end:
        return "chain_done_unaligned"
    return "no_record"


def _catalog_age_hours(live: dict) -> float | None:
    """catalog.updated 距现在的小时数（无法解析则 None）。"""
    raw = live.get("updated")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    return (datetime.datetime.now() - ts).total_seconds() / 3600.0


def test_catalog_alignment_uses_current_catalog_and_registry():
    a = dw._catalog_alignment()
    live = json.loads(CATALOG.read_text(encoding="utf-8"))
    sha = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert a["catalog_version"] == live.get("version"), (
        "HTTP stats 的 catalog_version 与 live catalog.json 不一致")
    assert a["catalog_tables"] == len(live.get("tables") or {}), (
        "HTTP stats 的 catalog_tables 与 live catalog.json 表数不一致")
    assert a["catalog_tables"] >= 200
    assert a["registry_physical"] >= a["catalog_tables"], (
        "registry 物理表数应 >= catalog 已登记表数（v39 口径）")
    assert a["registry_display"] > 0
    assert a["catalog_updated"]

    if a.get("registry_catalog_sha256") != sha:
        state = _kg_chain_state()
        age = _catalog_age_hours(live)
        if state == "in_chain":
            pytest.skip(
                "KgDailySync 链中态：catalog 已于 %s 升版为 v%s（sha %s），"
                "链尾 registry_align --apply 尚未执行（registry 当前 sha %s）；"
                "链尾跑完即自愈，见 reports/impl_round9_six_items_closeout_20261106.md §5.3"
                % (live.get("updated"), live.get("version"), sha[:12],
                   str(a.get("registry_catalog_sha256"))[:12]))
        if state in ("no_log", "no_record") and age is not None and age < CHAIN_WINDOW_HOURS:
            pytest.skip(
                "无今日链日志（%s），但 catalog 于 %.1fh 前刚更新 → 按链中窗口兜底跳过；"
                "若超 %dh 仍不齐将判真实失配" % (state, age, CHAIN_WINDOW_HOURS))
        raise AssertionError(
            "warehouse_registry 非由当前 catalog.json 重建（catalog_sha256 不一致）\n"
            "  live catalog : v%s / %s / sha %s\n"
            "  registry     : sha %s\n"
            "  链状态       : %s（aligned/chain_done_unaligned 属真问题）\n"
            "  修复         : python tools/datawarehouse/registry_align.py --apply --json"
            % (live.get("version"), live.get("updated"), sha,
               a.get("registry_catalog_sha256"), state))


# ---------------------------------------------------------------- 链中态识别自测
def test_chain_state_helper_reads_in_chain(tmp_path):
    """今日有 catalog_build、无对齐、无收尾 → in_chain（应 skip 而非红）。"""
    log = tmp_path / "kg.log"
    log.write_text(
        "kg_sync OK 2026-11-05T06:42:48\n"
        "catalog_build exit=0 2026-11-06T06:24:32\n"
        "evt_build exit=0 2026-11-06T06:33:52\n",
        encoding="utf-8")
    assert _kg_chain_state("2026-11-06", log) == "in_chain"


def test_chain_state_helper_reads_aligned(tmp_path):
    log = tmp_path / "kg.log"
    log.write_text(
        "catalog_build exit=0 2026-11-06T06:24:32\n"
        "registry_align date=2026-11-06T06:48:47+08:00 action=apply catalog=v43 "
        "sha=753b48ffc73b621c physical=306 display=258 rebuild=ok exit=0\n"
        "registry_align exit=0 2026-11-06T06:48:47\n"
        "kg_sync OK 2026-11-06T06:48:47\n",
        encoding="utf-8")
    assert _kg_chain_state("2026-11-06", log) == "aligned"


def test_chain_state_helper_flags_done_but_unaligned(tmp_path):
    """链已收尾（WARN）但没有对齐成功 → chain_done_unaligned，必须真红。

    11-05 实况：`kg_sync WARN registry_align (data chain already done) rc=1`，
    直到 07:10 人工 apply 才补上。此分支就是为抓这类「链跑完了、registry 还是旧的」。
    """
    log = tmp_path / "kg.log"
    log.write_text(
        "catalog_build exit=0 2026-11-05T06:24:32\n"
        "registry_align exit=1 2026-11-05T06:42:40\n"
        "kg_sync WARN registry_align (data chain already done) rc=1\n"
        "kg_sync OK 2026-11-05T06:42:48\n",
        encoding="utf-8")
    assert _kg_chain_state("2026-11-05", log) == "chain_done_unaligned"


def test_chain_state_helper_flags_stale_format(tmp_path):
    """R9 起链尾失败改写成 `kg_sync STALE ...`，判定必须同样识别为「链已收尾」。"""
    log = tmp_path / "kg.log"
    log.write_text(
        "catalog_build exit=0 2026-11-06T06:24:32\n"
        "registry_align attempt=1/2 exit=1 2026-11-06T06:42:40\n"
        "registry_align attempt=2/2 exit=1 2026-11-06T06:43:40\n"
        "kg_sync STALE registry_align rc=1 after 2 attempts at 2026-11-06T06:43:40: "
        "warehouse_registry may be outdated; run tools/datawarehouse/registry_align.py "
        "--apply manually\n"
        "kg_sync OK 2026-11-06T06:43:40\n",
        encoding="utf-8")
    assert _kg_chain_state("2026-11-06", log) == "chain_done_unaligned"


def test_chain_state_helper_missing_log(tmp_path):
    assert _kg_chain_state("2026-11-06", tmp_path / "nope.log") == "no_log"
    log = tmp_path / "kg.log"
    log.write_text("catalog_build exit=0 2026-11-01T06:24:32\n", encoding="utf-8")
    assert _kg_chain_state("2026-11-06", log) == "no_record"


def test_mismatch_in_chain_skips_instead_of_failing(monkeypatch):
    """链中态下 sha 不匹配必须 skip，不得 red（今日链尾随后会自愈）。"""
    live = json.loads(CATALOG.read_text(encoding="utf-8"))
    fake = {
        "catalog_version": live.get("version"),
        "catalog_tables": len(live.get("tables") or {}),
        "catalog_updated": live.get("updated"),
        "registry_physical": 306,
        "registry_display": 258,
        "registry_catalog_sha256": "0" * 64,   # 必不匹配
    }
    monkeypatch.setattr(dw, "_catalog_alignment", lambda: fake)
    monkeypatch.setattr(sys.modules[__name__], "_kg_chain_state", lambda *a, **k: "in_chain")
    with pytest.raises(pytest.skip.Exception):
        test_catalog_alignment_uses_current_catalog_and_registry()


def test_mismatch_after_chain_done_raises(monkeypatch):
    """链已收尾却仍不齐 → 必须抛 AssertionError（11-05 rc=1 那类真问题的守卫）。"""
    live = json.loads(CATALOG.read_text(encoding="utf-8"))
    fake = {
        "catalog_version": live.get("version"),
        "catalog_tables": len(live.get("tables") or {}),
        "catalog_updated": live.get("updated"),
        "registry_physical": 306,
        "registry_display": 258,
        "registry_catalog_sha256": "0" * 64,
    }
    monkeypatch.setattr(dw, "_catalog_alignment", lambda: fake)
    monkeypatch.setattr(sys.modules[__name__], "_kg_chain_state",
                        lambda *a, **k: "chain_done_unaligned")
    with pytest.raises(AssertionError, match="非由当前 catalog.json 重建"):
        test_catalog_alignment_uses_current_catalog_and_registry()
