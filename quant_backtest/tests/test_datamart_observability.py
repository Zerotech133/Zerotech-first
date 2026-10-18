# -*- coding: utf-8 -*-
"""S4 开闸① 可观测性测试（2026-10-18）：datamart_read/datamart_ready 三态。

背景：C:/envmain/python.exe 缺 pyarrow → datamart 读层异常被逐层吞掉 →
datamart_read 返回 ok=True 且 n=0（无告警）→ 影子统计前向收益恒 None，
永远停在「样本积累中」。本测试用 mock/monkeypatch 模拟「引擎缺失」「读
失败」「空结果」三态，断言 WARN 诊断可见 + 返回态字段正确。
零真实 pyarrow/数据库/网络：datamart 一律注入 fake 模块，DataFrame 用
stub 顶替（只需 __len__/to_dict）。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_tools  # noqa: E402


# ── stub：替身 datamart 模块与 DataFrame ─────────────────────
class _FakeDF:
    """最小 DataFrame 替身：datamart_read 只用 len() 与 to_dict("records")。"""

    def __init__(self, records):
        self._r = list(records)

    def __len__(self):
        return len(self._r)

    def to_dict(self, kind):
        return list(self._r)


class _FakeDM:
    """datamart 模块替身：read/ready/explain 可编排异常与返回。"""

    def __init__(self, rows=None, read_exc=None, ready=True, ready_exc=None,
                 explain="fake-explain"):
        self.rows = rows if rows is not None else []
        self.read_exc = read_exc
        self.ready_flag = ready
        self.ready_exc = ready_exc
        self.explain_ret = explain

    def read(self, table, **kw):
        if self.read_exc is not None:
            raise self.read_exc
        return _FakeDF(self.rows)

    def ready(self, table):
        if self.ready_exc is not None:
            raise self.ready_exc
        return self.ready_flag

    def explain(self, table):
        return self.explain_ret


@pytest.fixture
def engine():
    """固定引擎探测结果，绕开真实 pyarrow import（进程内缓存短路）。"""
    def _set(v):
        agent_tools._PQ_ENGINE = v
        return v
    yield _set
    agent_tools._PQ_ENGINE = None


@pytest.fixture
def fake_dm(monkeypatch):
    """把 fake datamart 模块注入 sys.modules（import datamart 即命中）。"""
    def _set(dm):
        monkeypatch.setitem(sys.modules, "datamart", dm)
        return dm
    yield _set


# ── datamart_read：三态 + 正常态 ─────────────────────────────
def test_read_happy_path(engine, fake_dm, capsys):
    """有数据：n/rows 语义不变，新增 engine/empty_reason=None，无 WARN。"""
    engine("pyarrow")
    fake_dm(_FakeDM(rows=[{"ts_code": "000001.SZ", "close": 10.0},
                          {"ts_code": "000001.SZ", "close": 10.5}]))
    r = agent_tools.call("datamart_read", table="ts_daily")
    assert r["ok"] is True
    d = r["data"]
    assert d["n"] == 2 and len(d["rows"]) == 2
    assert d["engine"] == "pyarrow"
    assert d["empty_reason"] is None
    assert "[datamart_read]" not in capsys.readouterr().err


def test_read_engine_missing(engine, fake_dm, capsys):
    """引擎缺失：WARN 可见 + empty_reason=no_parquet_engine（fail-visible）。"""
    engine("none")
    fake_dm(_FakeDM())                      # 现实形态：缺引擎 → 读层吞异常回空表
    r = agent_tools.call("datamart_read", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is True                  # 不改读取语义：仍 fail-open
    d = r["data"]
    assert d["n"] == 0 and d["rows"] == []
    assert d["empty_reason"] == "no_parquet_engine"
    assert "[datamart_read] WARN" in err
    assert "no_parquet_engine" in err


def test_read_read_failure(engine, fake_dm, capsys):
    """读失败：WARN 可见 + ok=False + empty_reason=read_error:<Exc>。"""
    engine("pyarrow")
    fake_dm(_FakeDM(read_exc=RuntimeError("boom")))
    r = agent_tools.call("datamart_read", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is False
    assert "boom" in r["err"]
    assert r["n"] == 0 and r["rows"] == []
    assert r["empty_reason"] == "read_error:RuntimeError"
    assert "[datamart_read] WARN" in err
    assert "boom" in err


def test_read_import_failure(engine, fake_dm, capsys):
    """datamart 模块导入失败：WARN 可见 + empty_reason=datamart_import_error。"""
    engine("pyarrow")
    monkey_mod = fake_dm(None)              # sys.modules["datamart"]=None → ImportError
    assert monkey_mod is None
    r = agent_tools.call("datamart_read", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is False
    assert r["empty_reason"] == "datamart_import_error"
    assert "[datamart_read] WARN" in err


def test_read_empty_table_not_ready(engine, fake_dm, capsys):
    """空结果 + 表未就绪：WARN 区分 table_not_ready。"""
    engine("pyarrow")
    fake_dm(_FakeDM(ready=False))
    r = agent_tools.call("datamart_read", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is True
    d = r["data"]
    assert d["n"] == 0
    assert d["empty_reason"] == "table_not_ready"
    assert "[datamart_read] WARN" in err
    assert "table_not_ready" in err


def test_read_empty_genuine(engine, fake_dm, capsys):
    """空结果 + 表就绪（如窗口内无数据）：WARN 标 empty，不误报环境坏。"""
    engine("pyarrow")
    fake_dm(_FakeDM(ready=True))
    r = agent_tools.call("datamart_read", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is True
    assert r["data"]["empty_reason"] == "empty"
    assert "[datamart_read] WARN" in err


def test_read_consumer_shape_compat(engine, fake_dm):
    """消费方兼容：council_shadow/shadow_collect 的取数惯用法不受影响。"""
    engine("pyarrow")
    fake_dm(_FakeDM(rows=[{"trade_date": "2026-10-17", "close": 9.9}]))
    r = agent_tools.call("datamart_read", table="ts_daily")
    d2 = r.get("data") if isinstance(r.get("data"), dict) else r
    rows = d2.get("rows") or []
    assert rows and rows[0]["close"] == 9.9


# ── datamart_ready：引擎自检 ─────────────────────────────────
def test_ready_engine_missing(engine, fake_dm, capsys):
    """引擎缺失：明确不可用态 ready=False + reason=no_parquet_engine。"""
    engine("none")
    fake_dm(_FakeDM(ready=True))            # 表文件在但环境坏：不得误报可用
    r = agent_tools.call("datamart_ready", table="ts_daily")
    err = capsys.readouterr().err
    assert r["ok"] is True
    d = r["data"]
    assert d["ready"] is False
    assert d["engine"] is None
    assert d["reason"] == "no_parquet_engine"
    assert d["explain"] is None
    assert "[datamart_ready] WARN" in err


def test_ready_ok(engine, fake_dm, capsys):
    """引擎在 + 表就绪：ready/explain 语义不变，附加 engine 字段。"""
    engine("pyarrow")
    fake_dm(_FakeDM(ready=True))
    r = agent_tools.call("datamart_ready", table="ts_daily")
    assert "[datamart_ready]" not in capsys.readouterr().err
    d = r["data"]
    assert d["ready"] is True
    assert d["engine"] == "pyarrow"
    assert d["explain"] == "fake-explain"


def test_ready_not_ready(engine, fake_dm, capsys):
    """引擎在 + 表未就绪：ready=False（常规语义）。"""
    engine("pyarrow")
    fake_dm(_FakeDM(ready=False))
    r = agent_tools.call("datamart_ready", table="ts_daily")
    d = r["data"]
    assert d["ready"] is False
    assert d["explain"] is None
    assert "reason" not in d or d.get("reason") is None


def test_ready_dm_error(engine, fake_dm, capsys):
    """datamart 导入/探测异常：ready=False + reason=datamart_error:<Exc>。"""
    engine("pyarrow")
    fake_dm(None)                           # sys.modules None → ImportError
    r = agent_tools.call("datamart_ready", table="ts_daily")
    err = capsys.readouterr().err
    assert r["data"]["ready"] is False
    assert r["data"]["reason"].startswith("datamart_error:")
    assert "ModuleNotFoundError" in r["data"]["reason"]
    assert "[datamart_ready] WARN" in err


# ── 引擎探测函数本身（不依赖真实 pyarrow：直接读缓存语义） ──
def test_engine_probe_cache_semantics(engine):
    """探测缓存语义：非 None 时直接复用，不触发真实 import。"""
    engine("pyarrow")
    assert agent_tools._parquet_engine() == "pyarrow"
    engine("none")
    assert agent_tools._parquet_engine() == "none"
