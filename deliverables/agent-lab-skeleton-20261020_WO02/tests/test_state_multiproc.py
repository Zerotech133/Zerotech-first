"""工位DA · 田丰台账多进程写合并（WO-LEGION-S16-01）测试。

缺陷形态：``_persist_state`` 原先只有线程锁，两个进程注入同一
``state_path`` 时后写者整文件覆盖前写者——前写者落盘的记录无痕丢失。

覆盖点（对应工单要求）：
1. 两进程并发各落不同 issue，最终全部在盘（真 subprocess，进程级并发）；
2. 进程 A 落盘后，进程 B（构造时盘上尚无 A 记录、内存无 A）再写入
   不丢 A：盘上双在、B 内存回刷后双在、第三次加载可完整还原；
3. 合并可观测：合并发生时 ``LOG.warning`` 留痕（logger=roles.tian_feng）；
4. 单进程重复写幂等如旧：重复 ``_persist_state`` 文件字节不变、可解析；
5. ``state_path=None``：零 IO、零产物。

实现口径说明（以代码为准）：``issue_id`` 由各进程本地 ``issue_seq``
起算，跨进程不保证唯一（两进程同起点都会派 ISS-0001），故并集键用
模块自带的内容同一性原语 ``fingerprint``；盘上独有条目并入时若 id
与内存冲突则重派号（冲突以内存为准）。本文件对跨进程合并出的记录
一律按 subject/fingerprint 断言，不钉死 issue_id。

测试约定：
- 解释器 ``C:/Program Files/Python311/python.exe``（受管 venv 缺
  prometheus_client；子进程用 ``sys.executable`` 与主测试同解释器）；
- 零真实网络：子进程/主进程 bus_url 均指向不可达地址，本文件用例只
  走 intake（登记即落盘），不经总线投递；
- 状态文件全部落 ``tmp_path``，不写骨架内真实 ``data/``；
- 本文件所有业务数据均为「模拟」数据，不冒充真实事件。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from roles.rectification_line import SEVERITY_NORMAL
from roles.tian_feng import TianFeng

# 不可达地址：本文件用例只登记不上报，bus_url 仅占位。
UNREACHABLE_BUS = "http://127.0.0.1:1"

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 实例化会向默认注册表注册同名指标；
    同进程多次实例化前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_tianfeng(state_path=None) -> TianFeng:
    """构造归口席；state_path=None 时与既有纯内存调用完全一致。"""
    kw = {"report_to": "principal_laotian", "bus_url": UNREACHABLE_BUS,
          "metrics_port": 9102}
    if state_path is not None:
        return TianFeng(state_path=state_path, **kw)
    return TianFeng(**kw)


def intake_normal(tf: TianFeng, subject: str) -> dict:
    """登记一条一般整改问题单（模拟数据）；登记成功即落盘。"""
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_NORMAL,
        "evidence": {"note": f"模拟证据：{subject}（模拟数据）"},
    })
    assert issue["duplicate"] is False
    return issue


def board_subjects(tf: TianFeng) -> set:
    return {i["subject"] for i in tf.issue_board}


# ---------------- 子进程 worker（跨进程并发用） ----------------

_WORKER_SRC = '''
import json, sys, time
from pathlib import Path

repo_root, state_arg, subject, ready_flags, wait_flags = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
sys.path.insert(0, repo_root)

from roles.tian_feng import TianFeng  # noqa: E402  (sys.path 注入后)

state_path = Path(state_arg) if state_arg != "NONE" else None
tf = TianFeng(state_path=state_path, report_to="principal_laotian",
              bus_url="http://127.0.0.1:1", metrics_port=9102)

# 会合点：全部 worker 就绪后才同时开跑， maximise 真并发重叠窗口。
ready = [Path(p) for p in ready_flags.split("|")]
waiting = [Path(p) for p in wait_flags.split("|")]
for flag in ready:
    flag.write_text("1", encoding="utf-8")
deadline = time.time() + 60
while any(not f.exists() for f in waiting) and time.time() < deadline:
    time.sleep(0.01)

issue = tf.intake_issue({
    "source": "zhuge_liang", "platform": "quant_trading",
    "subject": subject, "severity": "normal",
    "evidence": {"note": f"模拟证据：{subject}（模拟数据）"},
})
print(json.dumps({"issue_id": issue["issue_id"], "subject": subject},
                 ensure_ascii=False))
'''


def _spawn_worker(work: Path, state_path: Path, subject: str,
                  ready_flags: str, wait_flags: str) -> subprocess.Popen:
    script = work / "_state_multiproc_worker.py"
    if not script.exists():
        script.write_text(_WORKER_SRC, encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(script), str(REPO_ROOT), str(state_path),
         subject, ready_flags, wait_flags],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")


# ---------------- 1) 两进程并发各落不同 issue，最终双在/多在 ----------------

def test_two_process_concurrent_distinct_issues_both_survive(tmp_path):
    """4 个真子进程并发登记不同 subject：盘上 4 条俱在、单号不撞、可还原。

    未实现跨进程合并时，后写者整文件覆盖前写者，必有条目丢失（红）。
    """
    work = tmp_path / "proc"
    work.mkdir()
    state_path = work / "tianfeng_state.json"
    n = 4
    subjects = [f"模拟并发问题 P{i}（模拟数据）" for i in range(n)]
    ready_flags = [work / f"ready_{i}.flag" for i in range(n)]

    procs = []
    for i in range(n):
        wait_flags = "|".join(str(f) for f in ready_flags)
        procs.append(_spawn_worker(work, state_path, subjects[i],
                                   str(ready_flags[i]), wait_flags))
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, f"worker 失败 rc={p.returncode}: {err}"
        outs.append(json.loads(out))

    data = json.loads(state_path.read_text(encoding="utf-8"))
    disk_subjects = {i["subject"] for i in data["issue_board"]}
    assert disk_subjects == set(subjects), (
        f"并发写入丢失条目: 盘上={disk_subjects} 期望={set(subjects)}")
    disk_ids = [i["issue_id"] for i in data["issue_board"]]
    assert len(set(disk_ids)) == n, f"issue_id 相撞: {disk_ids}"
    assert data["issue_seq"] >= n

    # 第三方新实例（等效重启）可完整还原
    tf_c = make_tianfeng(state_path)
    assert board_subjects(tf_c) == set(subjects)
    assert len({i["fingerprint"] for i in tf_c.issue_board}) == n


# ---------------- 2) B 写入不丢 A（顺序两进程 + 内存回刷） ----------------

def test_writer_b_merge_keeps_writer_a_records(tmp_path):
    """A 落盘后 B（构造早于 A 落盘、内存无 A）写入：A 的记录不丢。

    - 盘上 A/B 双在；B 内存回刷后双在（并回填指纹索引）；
    - 第三次加载（等效重启）还原全量。
    """
    work = tmp_path / "seq"
    work.mkdir()
    state_path = work / "tianfeng_state.json"
    subj_a = "模拟问题：A 席先落盘（模拟数据）"
    subj_b = "模拟问题：B 席后写入（模拟数据）"

    tf_a = make_tianfeng(state_path)                  # A：空盘起
    tf_b = make_tianfeng(state_path)                  # B：构造时盘上无 A
    assert tf_b.issue_board == []                     # B 内存确实无 A

    intake_normal(tf_a, subj_a)                       # A 落盘
    intake_normal(tf_b, subj_b)                       # B 写入（须合并）

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert {i["subject"] for i in data["issue_board"]} == {subj_a, subj_b}
    # 单号不撞（B 本地也派 ISS-0001 时须重派盘上独有条目）
    ids = [i["issue_id"] for i in data["issue_board"]]
    assert len(set(ids)) == 2

    # B 内存已回刷：双在 + 指纹索引回填
    assert board_subjects(tf_b) == {subj_a, subj_b}
    assert len(tf_b._fingerprint_index) == 2
    # B 的后续去重沿新账生效：同内容再登记判重，不新增
    dup = tf_b.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subj_a, "severity": SEVERITY_NORMAL,
        "evidence": {"note": "模拟证据（模拟数据）"},
    })
    assert dup["duplicate"] is True

    # 等效重启：新实例还原全量
    tf_c = make_tianfeng(state_path)
    assert board_subjects(tf_c) == {subj_a, subj_b}
    assert tf_c.data_gaps == []


# ---------------- 3) 合并可观测：LOG.warning 留痕 ----------------

def test_merge_emits_warning_log(tmp_path, caplog):
    """发生并入时 roles.tian_feng 打 LOG.warning（合并次数可观测）。"""
    state_path = tmp_path / "tianfeng_state.json"
    subj_a = "模拟问题：合并观测 A（模拟数据）"
    subj_b = "模拟问题：合并观测 B（模拟数据）"
    tf_a = make_tianfeng(state_path)
    tf_b = make_tianfeng(state_path)
    intake_normal(tf_a, subj_a)

    with caplog.at_level(logging.WARNING, logger="roles.tian_feng"):
        intake_normal(tf_b, subj_b)

    merge_logs = [r for r in caplog.records if "多进程写合并" in r.getMessage()]
    assert merge_logs, "并入盘上独有条目时未打 LOG.warning（合并不可观测）"
    assert any("issues=1" in r.getMessage() for r in merge_logs)

    # 无增量时（重复写同内存）不产生合并告警（先清 caplog：records 全测试累积）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="roles.tian_feng"):
        tf_b._persist_state()
    assert not [r for r in caplog.records if "多进程写合并" in r.getMessage()]


# ---------------- 4) 单进程重复写幂等如旧 ----------------

def test_single_process_repeated_persist_idempotent(tmp_path):
    """重复 _persist_state：文件字节不变、始终可解析，语义与旧版一致。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    intake_normal(tf, "模拟问题：幂等写 1（模拟数据）")
    before = state_path.read_text(encoding="utf-8")

    tf._persist_state()
    tf._persist_state()
    assert state_path.read_text(encoding="utf-8") == before

    data = json.loads(before)
    assert data["issue_seq"] == 1 and len(data["issue_board"]) == 1

    # 同内容重复登记：指纹判重、台账与盘面均不增长（如旧）
    dup = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟问题：幂等写 1（模拟数据）",
        "severity": SEVERITY_NORMAL, "evidence": {"note": "x"},
    })
    assert dup["duplicate"] is True
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(after["issue_board"]) == 1 and after["issue_seq"] == 1

    # 新内容照常追加并落盘
    intake_normal(tf, "模拟问题：幂等写 2（模拟数据）")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["issue_seq"] == 2 and len(data["issue_board"]) == 2
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------- 5) state_path=None：零 IO、零产物 ----------------

def test_state_path_none_zero_artifacts(tmp_path, monkeypatch):
    """零 IO 席位：不建文件、不建目录，多进程合并分支全不生效。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)                           # 若有落盘即会现形

    tf = make_tianfeng()
    assert tf.state_path is None and tf.wal_path is None
    intake_normal(tf, "模拟问题：零 IO 席位（模拟数据）")
    tf._persist_state()                               # 显式调用也应零 IO

    assert tf.data_gaps == []
    assert list(work.iterdir()) == []                 # 零落盘、零锁文件
    # 纯内存语义不变：同内容判重
    dup = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟问题：零 IO 席位（模拟数据）",
        "severity": SEVERITY_NORMAL, "evidence": {"note": "x"},
    })
    assert dup["duplicate"] is True
    assert list(work.iterdir()) == []
