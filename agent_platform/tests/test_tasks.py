# -*- coding: utf-8 -*-
"""任务单系统自测（K-ORG-011 P2-1）：状态机全转移+RBAC越权+强制注记"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))
from tasks import PermissionDenied, TaskError, TaskStore  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tasks_test_"))
PASS = 0


def ok(name, cond=True):
    global PASS
    assert cond, name
    PASS += 1
    print("[PASS] %s" % name)


s = TaskStore(TMP / "org" / "tasks")

# 1. open + ls
tid = s.open("dev", "T1 prompt 立场强制校准", "四条靶点见基线报告", "P1", opened_by="A0", due="2026-10-16")
ok("open 返回 task_id", tid.startswith("T-"))
ok("ls 默认可见", len(s.ls()) == 1)
ok("ls 按 dept 过滤", len(s.ls(dept="invest")) == 0)

# 2. 全转移链：open→assigned→doing→blocked→doing→done
s.move(tid, "assigned", "A0")
s.move(tid, "doing", "dept/dev/DEV1")
s.move(tid, "blocked", "dept/dev/DEV1", note="等影子期满解冻")
s.move(tid, "doing", "dept/dev/DEV1", note="解冻")
try:
    s.move(tid, "done", "dept/dev/DEV1")  # 无 closure_note
    ok("done 强制 closure_note", False)
except TaskError:
    ok("done 强制 closure_note", True)
t = s.move(tid, "done", "dept/dev/DEV1", note="prompt 四条靶点已改，冒烟 10/10")
ok("全转移链至 done", t["status"] == "done")

# 3. 终态不可转移
try:
    s.move(tid, "doing", "A0")
    ok("终态拒绝转移", False)
except TaskError:
    ok("终态拒绝转移", True)

# 4. RBAC 越权：invest 成员动 dev 的单
tid2 = s.open("dev", "守卫移植", "llm.py 负载守卫", "P2", opened_by="A0")
try:
    s.move(tid2, "assigned", "dept/invest/ST1")
    ok("跨部门越权拒绝", False)
except PermissionDenied:
    ok("跨部门越权拒绝", True)
ok("本部门可流转", s.move(tid2, "assigned", "dept/dev/DEV1")["status"] == "assigned")

# 5. rejected 须写理由；cancelled
tid3 = s.open("trading", "实盘接单", "影子期无此职责", "P2", opened_by="A0")
try:
    s.move(tid3, "rejected", "dept/trading/TR1")
    ok("rejected 须写理由", False)
except TaskError:
    ok("rejected 须写理由", True)
ok("rejected 带理由", s.move(tid3, "rejected", "dept/trading/TR1",
                             note="影子冻结期无实盘职责")["status"] == "rejected")

# 6. P0 直达 doing
tid4 = s.open("dev", "Ollama 服务不可用处置", "failed 告警触发", "P0", opened_by="system/monitor", p0=True)
ok("P0 直入 doing", s.ls(status="doing")[0]["task_id"] == tid4)

# 7. 非法部门/严重度/不存在单
for fn, exc in (
    (lambda: s.open("hr", "x", "y"), TaskError),
    (lambda: s.open("dev", "x", "y", severity="P3"), TaskError),
    (lambda: s.move("T-20990101-999", "doing", "A0"), TaskError),
):
    try:
        fn()
        ok("参数校验", False)
    except exc:
        ok("参数校验（%s）" % exc.__name__, True)

# 8. append-only：文件行数 = 操作数（无覆盖写）
files = list((TMP / "org" / "tasks").glob("tasks_*.jsonl"))
nlines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
ok("append-only 行数一致（11 行=成功操作数，被拒操作不落行）" , nlines == 11)

shutil.rmtree(TMP, ignore_errors=True)
print("-" * 40)
print("TASKS ALL PASS（%d 断言）" % PASS)
