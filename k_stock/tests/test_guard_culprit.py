# -*- coding: utf-8 -*-
"""K-STOCK-20261020-CULPRITFIX：守卫竞争者甄别三态测试。
纯注入进程表（mock dict），不真枚举系统进程（环境不可重现）。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))   # 仓库根（k_stock 命名空间包入口）

from k_stock import run_poc                   # noqa: E402

_GB = 1024 ** 3


def _proc(pid, ppid, name, mem=0, exe=""):
    return {"ProcessId": pid, "ParentProcessId": ppid, "Name": name,
            "ExecutablePath": exe, "WorkingSetSize": mem}


def test_ollama_child_llama_server_excluded():
    """llama-server 父=ollama.exe（T-Q8 实证场景 PID 36412←10392）→ 判 Ollama 自身忙，
    绝不写"关闭竞争者"。"""
    procs = [
        _proc(10392, 29900, "ollama.exe",
              exe=r"C:\Users\t\AppData\Local\Programs\Ollama\ollama.exe"),
        _proc(36412, 10392, "llama-server.exe", mem=int(8.08 * _GB),
              exe=r"C:\Users\t\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe"),
    ]
    note = run_poc._classify_llama_procs(procs)
    assert "Ollama 推理引擎自身忙" in note and "查消费方而非关服务" in note
    assert "竞争者" not in note and "关闭" not in note


def test_ollama_grandchild_via_chain():
    """隔代链（llama-server ← runner ← ollama.exe）同样命中父链甄别。"""
    procs = [
        _proc(100, 1, "ollama.exe"),
        _proc(200, 100, "ollama_runner.exe"),
        _proc(300, 200, "llama-server.exe", mem=int(9 * _GB)),
    ]
    note = run_poc._classify_llama_procs(procs)
    assert "Ollama 推理引擎自身忙" in note


def test_ollama_path_fallback_for_orphan():
    """父已退出（孤儿 runner，#18500 已知缺陷）但可执行路径在 Ollama 目录 →
    仍判服务本体，不误杀。"""
    procs = [_proc(36412, 99999, "llama-server.exe", mem=int(8 * _GB),
                   exe=r"C:\Users\t\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe")]
    assert "Ollama 推理引擎自身忙" in run_poc._classify_llama_procs(procs)
    procs2 = [_proc(36413, 0, "llama-server.exe", mem=int(8 * _GB),
                    exe=r"C:\Users\t\.ollama\models\blobs\llama-server.exe")]
    assert "Ollama 推理引擎自身忙" in run_poc._classify_llama_procs(procs2)


def test_independent_llama_server_is_competitor():
    """独立 python/llama-server 高占用（10-08 判例：路径不在 Ollama 目录）→
    保留原竞争者文案。"""
    procs = [
        _proc(10392, 1, "ollama.exe"),
        _proc(555, 1, "llama-server.exe", mem=int(8.22 * _GB),
              exe=r"D:\tools\llama.cpp\llama-server.exe"),
    ]
    note = run_poc._classify_llama_procs(procs)
    assert "竞争者定位" in note and "关闭或错峰" in note


def test_openprocess_denied_skips_without_crash():
    """权限降级：嫌疑进程 exe 不可读 + 父进程不在表内（OpenProcess 拒绝语义）→
    跳过不记竞争者、不 crash，落③未明。"""
    procs = [_proc(777, 888, "llama-server.exe", mem=int(8 * _GB), exe="")]
    note = run_poc._classify_llama_procs(procs)
    assert note == run_poc._CULPRIT_UNKNOWN
    assert "竞争者" not in note


def test_no_suspect_unknown_attribution():
    """无任何嫌疑进程 → ③负载来源未明。"""
    assert run_poc._classify_llama_procs([]) == run_poc._CULPRIT_UNKNOWN
    assert run_poc._classify_llama_procs(
        [_proc(10392, 1, "ollama.exe")]) == run_poc._CULPRIT_UNKNOWN


def test_ollama_chain_idle_load_elsewhere():
    """Ollama 链 llama-server 在册但已卸载（<1GB）→ 非负载来源，落③未明。"""
    procs = [
        _proc(10392, 1, "ollama.exe"),
        _proc(36412, 10392, "llama-server.exe", mem=263 * 1024 * 1024,
              exe=r"C:\Users\t\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe"),
    ]
    assert run_poc._classify_llama_procs(procs) == run_poc._CULPRIT_UNKNOWN


def test_bad_rows_and_cycle_tolerance():
    """脏行（PID 缺失/非数字）跳过；父进程环不挂死（链深上限 8）。"""
    cycle = [_proc(1, 2, "a.exe"), _proc(2, 1, "b.exe"),
             _proc(9, 1, "llama-server.exe", mem=int(8 * _GB), exe=r"D:\x\ls.exe")]
    assert "竞争者定位" in run_poc._classify_llama_procs(cycle)
    dirty = [{"ProcessId": None, "Name": "llama-server.exe"},
             _proc(9, 1, "llama-server.exe", mem=int(8 * _GB),
                   exe=r"D:\x\ls.exe")]
    assert "竞争者定位" in run_poc._classify_llama_procs(dirty)


def test_win_process_table_never_raises(monkeypatch):
    """枚举层防御：subprocess 任何异常 → 空表，不抛。"""
    import subprocess
    def _boom(*a, **k):
        raise OSError("sandbox denied")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert run_poc._win_process_table(("llama-server.exe",)) == []


def test_competitor_note_end_to_end(monkeypatch):
    """_competitor_note 注入进程表端到端：T-Q8 场景返回 Ollama 自身忙文案。"""
    procs = [
        _proc(10392, 29900, "ollama.exe"),
        _proc(36412, 10392, "llama-server.exe", mem=int(8.08 * _GB),
              exe=r"C:\Users\t\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe"),
    ]
    monkeypatch.setattr(run_poc, "_win_process_table", lambda names: procs)
    note = run_poc._competitor_note()
    assert "Ollama 推理引擎自身忙" in note and "竞争者" not in note
