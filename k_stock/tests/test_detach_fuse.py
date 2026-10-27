# -*- coding: utf-8 -*-
"""K-STOCK 无进展熔断闸（P2-D4）测试。
monkeypatch 隔离 DATA/_today/FUSE_FILE/LOCK，不碰真 data/；
Popen/dereg_bootstrap 全 mock，不起任何子进程（规避沙箱连坐）。
注：LEDGER/LOG 已改 _ledger_path()/_log_path() 动态日期（02922778），
隔离改为 patch DATA 目录 + 固定 _today()。"""
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))   # 仓库根（k_stock 命名空间包入口）

from k_stock import detach_run                # noqa: E402


class _FakeProc:
    """poll() 立即返回 → main 的 6h 等待循环即刻 break。"""

    def poll(self):
        return 0


def _isolate(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(detach_run, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(detach_run, "_today", lambda: "20261021")
    monkeypatch.setattr(detach_run, "FUSE_FILE",
                        str(tmp_path / "data" / "poc_run_fuse.json"))
    monkeypatch.setattr(detach_run, "LOCK",
                        str(tmp_path / "data" / "poc_run.lock"))
    return Path(detach_run._ledger_path())


def _drive(monkeypatch, ledger_delta=0, dereg_calls=None, popen_calls=None):
    """mock 驱动 main()：ledger_delta>0 模拟 run_poc 本轮有产出（落账本行）。"""
    monkeypatch.setattr(detach_run, "ledger_complete", lambda: False)
    monkeypatch.setattr(detach_run, "run_poc_alive", lambda: False)
    monkeypatch.setattr(detach_run, "acquire_lock", lambda: True)
    monkeypatch.setattr(detach_run, "dereg_bootstrap",
                        lambda: dereg_calls.append(1))

    def fake_popen(cmd, **kw):
        popen_calls.append(cmd)
        if ledger_delta:
            with open(detach_run._ledger_path(), "a", encoding="utf-8") as f:
                for i in range(ledger_delta):
                    f.write(json.dumps({"ts": "t", "code": "%d.SH" % i,
                                        "mock": False}) + "\n")
        return _FakeProc()

    monkeypatch.setattr(detach_run.subprocess, "Popen", fake_popen)


def _fuse() -> dict:
    with open(detach_run.FUSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_progress_resets_counter(tmp_path, monkeypatch, capsys):
    """账本有增长 → 熔断计数清零（即使此前已累积 2 次无进展）。"""
    _isolate(tmp_path, monkeypatch)
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=1, dereg_calls=dereg, popen_calls=popen)
    _save_pre({"no_progress": 2})
    assert detach_run.main() == 0
    st = _fuse()
    assert st["no_progress"] == 0 and st["last_ts"]
    assert dereg == [] and len(popen) == 1 and "aborted" not in st


def test_no_progress_increments_and_persists(tmp_path, monkeypatch, capsys):
    """账本无增长 → 计数 +1 并持久化（含最后时间戳）；未达阈值不注销。"""
    lp = _isolate(tmp_path, monkeypatch)
    lp.write_text(json.dumps({"ts": "t", "code": "600519.SH"}) + "\n",
                  encoding="utf-8")
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=0, dereg_calls=dereg, popen_calls=popen)
    assert detach_run.main() == 0
    assert _fuse()["no_progress"] == 1
    assert detach_run.main() == 0
    st = _fuse()
    assert st["no_progress"] == 2 and st["last_ts"]
    assert dereg == [] and "aborted" not in st


def test_trips_on_third_no_progress(tmp_path, monkeypatch, capsys):
    """连续 3 次无进展 → 注销兜底任务 + 写 aborted 记录 + stderr WARN。"""
    lp = _isolate(tmp_path, monkeypatch)
    lp.write_text(json.dumps({"ts": "t", "code": "600519.SH"}) + "\n",
                  encoding="utf-8")
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=0, dereg_calls=dereg, popen_calls=popen)
    detach_run.main()
    detach_run.main()
    assert dereg == []                          # 前 2 轮不熔断
    detach_run.main()
    assert dereg == [1]                         # 第 3 轮注销兜底任务
    st = _fuse()
    assert st["aborted"] is True and st["aborted_ts"]
    assert st["no_progress"] == 3
    out = capsys.readouterr()
    assert "fuse tripped" in out.err
    assert len(popen) == 3                      # 3 轮都尝试过拉起


def test_aborted_guard_skips_spawn(tmp_path, monkeypatch, capsys):
    """aborted 残留（dereg 失败兜底）→ 不再拉起，直接确保注销退出。"""
    _isolate(tmp_path, monkeypatch)
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=1, dereg_calls=dereg, popen_calls=popen)
    _save_pre({"no_progress": 3, "aborted": True, "aborted_ts": "x"})
    assert detach_run.main() == 0
    assert popen == [] and dereg == [1]         # 不 spawn、补一次注销


def test_mock_and_bad_lines_are_not_progress(tmp_path, monkeypatch, capsys):
    """进度口径与 ledger_complete 同源：mock 行/坏行/空行不算进展。"""
    lp = _isolate(tmp_path, monkeypatch)
    lp.write_text(json.dumps({"ts": "t", "code": "A.SH"}) + "\n"
                  + json.dumps({"ts": "t", "code": "B.SH", "mock": True}) + "\n"
                  + "not-a-json-line\n\n"
                  + json.dumps({"ts": "t", "code": "C.SH", "mock": False}) + "\n",
                  encoding="utf-8")
    assert detach_run.ledger_progress() == 2


def test_corrupt_fuse_file_self_heals(tmp_path, monkeypatch, capsys):
    """熔断状态文件坏（半写/手改）→ 视作全新状态，主流程不崩。"""
    _isolate(tmp_path, monkeypatch)
    Path(detach_run.FUSE_FILE).write_text("{broken", encoding="utf-8")
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=0, dereg_calls=dereg, popen_calls=popen)
    assert detach_run.main() == 0
    assert _fuse()["no_progress"] == 1


def test_atomic_write_no_tmp_leftover(tmp_path, monkeypatch, capsys):
    """原子写落盘：目标文件完整可解析，无 .tmp 残留。"""
    _isolate(tmp_path, monkeypatch)
    dereg, popen = [], []
    _drive(monkeypatch, ledger_delta=0, dereg_calls=dereg, popen_calls=popen)
    detach_run.main()
    d = Path(detach_run.FUSE_FILE).parent
    assert not list(d.glob("*.tmp"))
    st = _fuse()                                # 目标文件 JSON 完整
    assert st["no_progress"] == 1


def _save_pre(state):
    with open(detach_run.FUSE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
