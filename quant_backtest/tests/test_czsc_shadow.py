# -*- coding: utf-8 -*-
"""CZSC 影子旁路管道单测（K-ORG-202）。

分两层（与双模式架构对齐）：
- 编排层（主环境可跑，零 czsc 依赖）：名单提取 / 四道守卫降级 / 非阻塞退出码 /
  顶层无 czsc import（隔离核心的静态保证）。
- worker 层（skipif venv 缺失）：真实票端到端——首遍落账、二遍幂等零新增、
  schema 校验、生产三账本 MD5 前后全等（影子零触碰断言）。

影子铁律：czsc_ledger.jsonl 永不进 verdict/L3/L4；本测试对生产三账本只读。
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import czsc_shadow as cs

VENV_EXISTS = os.path.exists(cs.VENV_PY)
LEDGER_EXISTS = os.path.exists(cs.LEDGER_PATH)

CL_DIR = os.path.join(cs.BASE, "data", "chanlun_learn")
PROD_LEDGERS = [cs.LEDGER_PATH,
                os.path.join(CL_DIR, "verify.jsonl"),
                os.path.join(CL_DIR, "attribution.jsonl")]

FEAT_KEYS = {"power", "slope", "rsq", "angle", "accel", "zz_rel"}
FIRED_KEYS = {"日线_D1_三买辅助V230228", "日线_D1_表里关系V230101",
              "日线_D1_涨跌停V230331", "日线_D1B_BUY1V221126"}


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_ledger(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ── 编排层（主环境，无 venv 依赖） ─────────────────────────────


def test_module_import_never_loads_czsc():
    """隔离核心：import czsc_shadow 顶层不得触发 czsc 加载（干净子进程验证）。"""
    code = ("import sys, json; sys.path.insert(0, r'%s');"
            "import czsc_shadow;"
            "print(json.dumps({'czsc_loaded': 'czsc' in sys.modules}))" % cs.BASE)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=120)
    assert r.returncode == 0, (r.stderr or "")[-300:]
    assert json.loads(r.stdout.strip().splitlines()[-1])["czsc_loaded"] is False


def test_extract_codes_latest_day_dedup(tmp_path):
    p = tmp_path / "ledger.jsonl"
    _write_ledger(p, [
        {"found_day": "2026-10-16", "code": "600519.SH"},   # 旧日，不取
        {"found_day": "2026-10-17", "code": "000001.SZ"},
        {"found_day": "2026-10-17", "code": "000001.SZ"},   # 重复，去重
        {"found_day": "2026-10-17", "code": "600519.SH"},
        {"found_day": "2026-10-17"},                         # 无 code，忽略
    ])
    day, codes = cs.extract_codes(str(p))
    assert day == "2026-10-17"
    assert codes == ["000001_SZ", "600519_SH"]


def test_extract_codes_empty_found_day(tmp_path):
    """全行无 found_day 时不抛异常，返回空名单（编排器据此 skip）。"""
    p = tmp_path / "ledger.jsonl"
    _write_ledger(p, [{"code": "000001.SZ"}, {}])
    day, codes = cs.extract_codes(str(p))
    assert day is None
    assert codes == []


def test_orchestrate_skips_when_venv_missing(tmp_path):
    led = tmp_path / "ledger.jsonl"
    _write_ledger(led, [{"found_day": "2026-10-17", "code": "000001.SZ"}])
    out = tmp_path / "out.jsonl"
    status, detail = cs.orchestrate(venv_py=str(tmp_path / "nope" / "python.exe"),
                                    ledger_path=str(led), out_path=str(out),
                                    ashare_dir=str(tmp_path))
    assert status == "skip_no_venv"
    assert "venv" in detail
    assert not out.exists()  # 降级路径不产生任何产物


def test_orchestrate_skips_when_ledger_missing(tmp_path):
    status, _ = cs.orchestrate(venv_py=sys.executable,  # 真实存在，越过 venv 守卫
                               ledger_path=str(tmp_path / "absent.jsonl"),
                               out_path=str(tmp_path / "out.jsonl"),
                               ashare_dir=str(tmp_path))
    assert status == "skip_no_ledger"


def test_orchestrate_skips_when_ledger_stale(tmp_path):
    led = tmp_path / "ledger.jsonl"
    _write_ledger(led, [{"found_day": "2026-10-17", "code": "000001.SZ"}])
    old = time.time() - 2 * 86400
    os.utime(led, (old, old))  # mtime 设为两天前
    status, detail = cs.orchestrate(venv_py=sys.executable,
                                    ledger_path=str(led),
                                    out_path=str(tmp_path / "out.jsonl"),
                                    ashare_dir=str(tmp_path))
    assert status == "skip_stale_ledger"
    assert "mtime" in detail


def test_orchestrate_skips_when_universe_empty(tmp_path):
    led = tmp_path / "ledger.jsonl"
    _write_ledger(led, [{"code": "000001.SZ"}])  # 无 found_day → 名单空
    status, _ = cs.orchestrate(venv_py=sys.executable, ledger_path=str(led),
                               out_path=str(tmp_path / "out.jsonl"),
                               ashare_dir=str(tmp_path))
    assert status == "skip_empty_universe"


def test_main_orchestrate_never_blocks(tmp_path, monkeypatch):
    """影子链非阻塞：任何 status（含 error_worker）退出码恒为 0。"""
    monkeypatch.setattr(cs, "orchestrate",
                        lambda **kw: ("error_worker", "boom"))
    assert cs.main_orchestrate() == 0
    monkeypatch.setattr(cs, "orchestrate",
                        lambda **kw: ("ran", '{"written": 3}'))
    assert cs.main_orchestrate() == 0


# ── worker 层（venv 存在时实跑，主环境自动跳过） ────────────────

def _run_worker(codes_file, out, meta="pytest"):
    cmd = [cs.VENV_PY, os.path.abspath(cs.__file__), "--worker",
           "--codes", str(codes_file), "--out", str(out),
           "--ashare-dir", cs.ASHARE_DIR, "--meta", meta]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)


@pytest.mark.skipif(not VENV_EXISTS, reason="venv_czsc 不存在（主环境零 czsc 依赖）")
@pytest.mark.skipif(not LEDGER_EXISTS, reason="生产 ledger 不存在")
def test_worker_end_to_end_idempotent_and_prod_untouched(tmp_path):
    """端到端：真实名单前 2 票——首遍落账 + schema + 二遍幂等零新增 +
    生产三账本 MD5 前后全等（影子零触碰）。产物全部落在 tmp_path。"""
    _, codes = cs.extract_codes(cs.LEDGER_PATH)
    avail = [c for c in codes
             if os.path.exists(os.path.join(cs.ASHARE_DIR, c + ".parquet"))][:2]
    if not avail:
        pytest.skip("名单票无可用 ashare parquet")

    codes_file = tmp_path / "codes.json"
    codes_file.write_text(json.dumps(avail), encoding="utf-8")
    out = tmp_path / "czsc_ledger.jsonl"
    md5_before = {p: _md5(p) for p in PROD_LEDGERS}

    r1 = _run_worker(codes_file, out)
    assert r1.returncode == 0, (r1.stderr or r1.stdout)[-300:]
    d1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert d1["written"] == len(avail), d1   # 首遍全量落账
    assert d1["errored"] == 0, d1            # 真实票不应异常

    rows = [json.loads(l) for l in open(out, encoding="utf-8")]
    assert len(rows) == len(avail)
    for r in rows:
        assert r["schema_v"] == cs.SCHEMA_V
        assert r["meta"] == "pytest"
        assert set(r["feats"]) == FEAT_KEYS
        assert set(r["fired"]) == FIRED_KEYS
        assert all(v is None or isinstance(v, float) for v in r["feats"].values())
        assert all(isinstance(v, bool) for v in r["fired"].values())
        assert isinstance(r["bi_n"], int) and r["bi_n"] > 0
        assert isinstance(r["zs_n"], int)
        assert r["code"] in avail and len(r["dt"]) == 8

    r2 = _run_worker(codes_file, out)
    assert r2.returncode == 0, (r2.stderr or r2.stdout)[-300:]
    d2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert d2["written"] == 0, d2            # 二遍幂等：零新增
    assert d2["errored"] == 0, d2
    assert d2["skipped_existing"] == d1["written"], d2
    assert len(open(out, encoding="utf-8").readlines()) == len(rows)  # append-only 无追加

    md5_after = {p: _md5(p) for p in PROD_LEDGERS}
    assert md5_after == md5_before           # 生产三账本逐字节零触碰
