# -*- coding: utf-8 -*-
"""monthly_report.py（W37 月报生成器）测试集——三段勾稽 / 缺账本 INCONCLUSIVE /
禁止编造字段 / 月一日守卫 / 数字反查钩子。

账实口径（paperclip/app.py 实读）：budget_ledger delta 扣减为负
（D1 pre-hold / D2 meter / D3a settle-extra 经 _try_spend_conn），
退入为正（settle-refund / abort-refund 经 _credit_conn）。
"""

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))          # recheck 内 import check_report_isolation 用


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mr = _load("monthly_report")

MONTH = "2026-10"

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  delta_cents INTEGER NOT NULL,
  reason TEXT NOT NULL,
  wo_id TEXT,
  balance_after INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  wo_id TEXT,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
"""


def _audit_chain_rows() -> list[tuple]:
    """与 app.py audit_append 同构的一行有效审计链（真实夹具：每笔账本行
    必伴审计行，审计链空表在生成器按疑被删改降 DEGRADED——语义正确）。"""
    genesis = "0" * 64
    ts = f"{MONTH}-05 10:00:00"
    mat = f"1|{ts}|approver|wo.state_change|wo-1|{{}}|{genesis}"
    h = hashlib.sha256(mat.encode("utf-8")).hexdigest()
    return [(1, ts, "approver", "wo.state_change", "wo-1", "{}", genesis, h)]


def _make_db(root: Path, ledger_rows: list[tuple], audit_rows: list[tuple] | None = None) -> Path:
    dbf = root / "digital-legion" / "paperclip" / "paperclip.db"
    dbf.parent.mkdir(parents=True, exist_ok=True)
    if dbf.exists():          # 同夹具内重建（如断链对照用例），整库重置
        dbf.unlink()
    conn = sqlite3.connect(dbf)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO budget_ledger (account_id, delta_cents, reason, wo_id, "
        "balance_after, created_at) VALUES (?,?,?,?,?,?)", ledger_rows)
    for seq, ts, actor, evt, wo_id, payload, prev, h in (audit_rows if audit_rows is not None
                                                         else _audit_chain_rows()):
        conn.execute(
            "INSERT INTO audit_event (seq, ts, actor, event_type, wo_id, payload, "
            "prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
            (seq, ts, actor, evt, wo_id, payload, prev, h))
    conn.commit()
    conn.close()
    return dbf


def _valid_month_rows() -> list[tuple]:
    """wo-1 done：D1=-500 D2=-200 settle-refund=+100 → 净成本 600；
    wo-2 rejected：D1=-300 abort-refund=+300 → 恒等式成立。余额链逐行咬合。"""
    return [
        (1, -500, "wo:wo-1:pre-hold", "wo-1", -500, f"{MONTH}-05 10:00:00"),
        (1, -200, "wo:wo-1:meter", "wo-1", -700, f"{MONTH}-06 10:00:00"),
        (1, 100, "wo:wo-1:settle-refund", "wo-1", -600, f"{MONTH}-07 10:00:00"),
        (1, -300, "wo:wo-2:pre-hold", "wo-2", -900, f"{MONTH}-08 10:00:00"),
        (1, 300, "wo:wo-2:abort-refund", "wo-2", -600, f"{MONTH}-09 10:00:00"),
    ]


def _make_git_repo(root: Path) -> None:
    """tmp 仓根 git init + docs/legion 一件入库（③盘点段有据）。"""
    (root / "docs" / "legion").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "legion" / "判例件.md").write_text("判例正文", encoding="utf-8")

    def g(*args):
        # 不用 `git -C`（CentOS7 git 1.8.3.1 不支持），用 cwd= 保持跨平台/跨 git 版本可跑。
        subprocess.run(["git", *args], check=True,
                       capture_output=True, cwd=str(root),
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "GIT_AUTHOR_DATE": "2026-10-10T12:00:00 +0800",
                            "GIT_COMMITTER_DATE": "2026-10-10T12:00:00 +0800",
                            "PATH": __import__("os").environ.get("PATH", ""),
                            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                            "HOME": str(root)})
    g("init")
    g("add", "docs/legion/判例件.md")
    g("commit", "-m", "test: 判例入库")


# ---------------------------------------------------------------- ②成本勾稽

def test_cost_ok_done_and_rejected_identities(tmp_path):
    """三段勾稽①：done 单 D1+D2-refund=净成本；rejected 单 D1=|D3b|，全 PASS。"""
    _make_db(tmp_path, _valid_month_rows())
    txt, state = mr.cost_section(tmp_path, MONTH)
    assert state == "OK"
    assert "wo-1：D1=500 D2=200 extra=0 refund=100 abort=0 净额=600 -> PASS" in txt
    assert "wo-2：D1=300 D2=0 extra=0 refund=0 abort=300 净额=0 -> PASS" in txt
    assert "余额链 FAIL" not in txt


def test_cost_degraded_on_balance_chain_break(tmp_path):
    """余额链断点（balance_after 与前行差额≠delta）→ DEGRADED 双列。"""
    rows = _valid_month_rows()
    rows[2] = (1, 100, "wo:wo-1:settle-refund", "wo-1", -999, f"{MONTH}-07 10:00:00")
    _make_db(tmp_path, rows)
    txt, state = mr.cost_section(tmp_path, MONTH)
    assert state == "DEGRADED"
    assert "余额链 FAIL" in txt


def test_cost_degraded_on_rejected_identity_fail(tmp_path):
    """rejected 单 D1≠|D3b|（全退不足）→ 恒等式 FAIL，段 DEGRADED。"""
    rows = [(1, -300, "wo:wo-x:pre-hold", "wo-x", -300, f"{MONTH}-05 10:00:00"),
            (1, 200, "wo:wo-x:abort-refund", "wo-x", -100, f"{MONTH}-06 10:00:00")]
    _make_db(tmp_path, rows)
    txt, state = mr.cost_section(tmp_path, MONTH)
    assert state == "DEGRADED"
    assert "wo-x" in txt and "FAIL" in txt
    assert "rejected 恒等式不成立" in txt


def test_cost_inconclusive_when_db_missing(tmp_path):
    """缺账本：INCONCLUSIVE 账本缺失，不编文不报 OK。"""
    txt, state = mr.cost_section(tmp_path, MONTH)
    assert state == "INCONCLUSIVE"
    assert "账本缺失" in txt


def test_cost_inconclusive_on_zero_flow_month(tmp_path):
    """当月零流水：INCONCLUSIVE 零流水月，不是 OK（判例 6 空集=假绿）。"""
    _make_db(tmp_path, [(1, -100, "wo:w:pre-hold", "w", -100, "2026-09-01 10:00:00")])
    txt, state = mr.cost_section(tmp_path, MONTH)
    assert state == "INCONCLUSIVE"
    assert "零流水月" in txt


def test_audit_chain_recompute(tmp_path):
    """审计哈希链本地复算旁证：正确链 PASS，断链 FAIL。"""
    dbf = _make_db(tmp_path, _valid_month_rows())
    conn = mr._open_ro(dbf)
    r = mr.verify_audit_chain(conn)
    conn.close()
    assert r["verdict"] == "PASS"
    # 断链：prev_hash 咬合破坏
    rows = _audit_chain_rows()
    rows[0] = (1, rows[0][1], rows[0][2], rows[0][3], rows[0][4],
               rows[0][5], "f" * 64, rows[0][7])
    _make_db(tmp_path, _valid_month_rows(), audit_rows=rows)
    conn = mr._open_ro(dbf)
    r2 = mr.verify_audit_chain(conn)
    conn.close()
    assert r2["verdict"] == "FAIL"


# ---------------------------------------------------------------- ③④段与整报

def test_build_report_all_sections_ok(tmp_path):
    """三段齐备（成本库+git 仓+org/hr）→ 报告级 OK，①概览逐段状态如实。"""
    _make_db(tmp_path, _valid_month_rows())
    _make_git_repo(tmp_path)
    org = tmp_path / "agent_platform" / "registry"
    org.mkdir(parents=True)
    (org / "org.json").write_text(json.dumps({
        "chief": {"agent_id": "A0"},
        "departments": [{"dept": "dev", "agents": ["T1", "T2"]}],
    }, ensure_ascii=False), encoding="utf-8")
    hr = tmp_path / "agent_platform" / "hr"
    hr.mkdir(parents=True)
    for n in ("A0", "T1", "T2", "GHOST"):
        (hr / n).mkdir()

    text = mr.build_report(tmp_path, MONTH)
    assert "报告级三态: OK" in text
    assert "- 成本勾稽段：OK" in text
    assert "- 施工盘点段：OK" in text
    assert "- 编制对账段：OK" in text
    # ④ 差集如实列示 + 541b1690 口径引用
    assert "hr 有 org 无（幽灵目录）：GHOST" in text
    assert "org 有 hr 无（登记缺 profile）：（空）" in text
    assert "541b1690" in text
    # 成本段不出「成本分析/趋势解读」——解读是账本没有的字段
    assert "成本分析" not in text
    assert "趋势解读" not in text


def test_build_report_missing_all_inconclusive(tmp_path):
    """三源全缺：各段 INCONCLUSIVE，报告级最差态，缺件如实不编造。"""
    text = mr.build_report(tmp_path, MONTH)
    assert "报告级三态: INCONCLUSIVE" in text
    assert "账本缺失" in text
    for w in ("建议", "预计", "据悉"):
        assert w not in text


def test_every_section_has_anchor_and_state(tmp_path):
    """每段锚行带 锚: 与 态:，无出处段禁止存在（W38 查1 口径）。"""
    import re
    text = mr.build_report(tmp_path, MONTH)
    lines = text.splitlines()
    sec_idx = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert len(sec_idx) == 5          # 固定五段
    for i in sec_idx:
        body = []
        for ln in lines[i + 1:]:
            if ln.startswith("## "):
                break
            body.append(ln)
        anchor = next((ln for ln in body if ln.startswith("> ")), "")
        assert "锚:" in anchor, f"段缺锚行: {lines[i]}"
        assert re.search(r"态:\s*(OK|INCONCLUSIVE|DEGRADED)", anchor), \
            f"锚行缺三态: {lines[i]}"


# ---------------------------------------------------------------- 反查钩子

def test_recheck_passes_on_fresh_report(tmp_path):
    """recheck 复算钩子：新出报告全过（无 violations）。"""
    _make_db(tmp_path, _valid_month_rows())
    _make_git_repo(tmp_path)
    org = tmp_path / "agent_platform" / "registry"
    org.mkdir(parents=True)
    (org / "org.json").write_text(json.dumps({
        "chief": {"agent_id": "A0"},
        "departments": [{"dept": "dev", "agents": ["T1"]}],
    }, ensure_ascii=False), encoding="utf-8")
    hr = tmp_path / "agent_platform" / "hr"
    hr.mkdir(parents=True)
    for n in ("A0", "T1"):
        (hr / n).mkdir()
    text = mr.build_report(tmp_path, MONTH)
    r = mr.recheck(text, tmp_path)
    assert r["violations"] == [], r["violations"]
    assert r["checked"] > 0


def test_recheck_bites_doctored_number(tmp_path):
    """禁止编造字段（W38 查3）：篡改报告数字必须被复算咬出。"""
    _make_db(tmp_path, _valid_month_rows())
    _make_git_repo(tmp_path)
    org = tmp_path / "agent_platform" / "registry"
    org.mkdir(parents=True)
    (org / "org.json").write_text(json.dumps({
        "chief": {"agent_id": "A0"},
        "departments": [{"dept": "dev", "agents": ["T1"]}],
    }, ensure_ascii=False), encoding="utf-8")
    hr = tmp_path / "agent_platform" / "hr"
    hr.mkdir(parents=True)
    for n in ("A0", "T1"):
        (hr / n).mkdir()
    text = mr.build_report(tmp_path, MONTH)
    doctored = text.replace("D1=500 D2=200", "D1=501 D2=200")
    assert doctored != text
    r = mr.recheck(doctored, tmp_path)
    assert any("wo-1" in v for v in r["violations"])


# ---------------------------------------------------------------- 月一日守卫

def test_month_guard_skip_non_first_day():
    """无 --month 且非月一日：SystemExit(0) 自跳过，不写文件。"""
    with pytest.raises(SystemExit) as ei:
        mr.month_window(None, today=date(2026, 10, 19))
    assert ei.value.code == 0


def test_month_default_projects_last_complete_month():
    """月一日调用：默认投影最近完整自然月（上月）。"""
    assert mr.month_window(None, today=date(2026, 11, 1)) == "2026-10"
    assert mr.month_window(None, today=date(2027, 1, 1)) == "2026-12"


def test_month_explicit_bypasses_guard_and_validates_format():
    assert mr.month_window("2026-10") == "2026-10"
    with pytest.raises(SystemExit):
        mr.month_window("2026-10-19")     # 格式非法显式拒绝
