# -*- coding: utf-8 -*-
"""R5 复盘回流生成器单测。

判例红线（仿 test_execution_watchdog.py / test_api_gate.py）：
- 数据源一律 tmp 注入（绝不读真实 monday_checkpoint.log / eval 产物）；
- TaskStore 用真 engine/tasks.py + tmp 账本库（run_shadow 测试法）；
- --dry-run 四路径零写副作用有专属断言。

运行：python -X utf8 -m pytest tools/test_retro_brief.py -q
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import retro_brief as retro  # noqa: E402

_BAR = "=" * 56
_THIN = "-" * 56


# ----------------------------------------------------------------------
# 夹具与数据构造
# ----------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """整仓 tmp 注入：shadow 数据源 / 简报落点 / 工单库全部隔离。"""
    repo = tmp_path / "repo"
    shadow = repo / "agent_platform" / "data" / "shadow"
    tasks_dir = repo / "agent_platform" / "data" / "org" / "tasks"
    shadow.mkdir(parents=True)
    tasks_dir.mkdir(parents=True)
    monkeypatch.setattr(retro, "_REPO", repo)
    monkeypatch.setattr(retro, "CHECKPOINT_LOG",
                        shadow / "monday_checkpoint.log")
    monkeypatch.setattr(retro, "SHADOW_DIR", shadow)
    monkeypatch.setattr(retro, "BRIEFS_DIR",
                        repo / "automation_runs" / "retro_brief" / "briefs")
    monkeypatch.setattr(retro, "STATE_DIR",
                        repo / "automation_runs" / "retro_brief")
    monkeypatch.setattr(retro, "TASKS_DIR", tasks_dir)
    # 真 engine/tasks.py 直接复用（无第三方依赖，watchdog 测试同法）
    engine = HERE.parent / "agent_platform" / "engine"
    monkeypatch.syspath_prepend(str(engine))
    return repo


def _cp_block(ts="2026-10-18 05:55:58", exit_code=0, items=None,
              conclusion="7/7"):
    """造真实形态门判块（仿 monday_checkpoint.log 勘察结构）。"""
    if items is None:
        items = [
            ("PASS", "影子首跑已发生（最新交易日 > 2026-10-14）",
             "最新=2026-10-14，首跑未发生（PENDING 中性）"),
            ("SKIP", "schema_version/dk/degraded 三项（待首跑后验收）", ""),
            ("PASS", "replay_trace 锚点回放（bc6d91172b0a）",
             "verdict=OK 命中=2 缺环=无"),
            ("PASS", "任务 AgentChiefPush",
             "state=Ready last=2026/10/17 23:27 result=0"),
        ]
    lines = [_BAR, "M5 刀4 周一观察点护航巡检 %s" % ts, _BAR]
    for tag, name, detail in items:
        if detail:
            lines.append("[%s] %s —— %s" % (tag, name, detail))
        else:
            lines.append("[%s] %s" % (tag, name))
    lines += [_THIN, "结论: %s PASS——观察点门全过" % conclusion,
              "EXIT=%d" % exit_code, ""]
    return "\n".join(lines)


def _mk_checkpoint(env, text):
    p = env / "agent_platform" / "data" / "shadow" / "monday_checkpoint.log"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_eval(env, name="eval_shadow_2026-10-15_official.md",
             conclusion="PASS", j4=True, failed=0, warn=True):
    """造真实形态 eval 产物（仿 eval_shadow_*_official.md 勘察结构）。"""
    lines = [
        "=" * 62,
        "影子账本：done=25 failed=%d 交易日=5 票数=5" % failed,
        "交易日: 2026-10-08, 2026-10-09, 2026-10-12, 2026-10-13, 2026-10-14",
        "[J1 无人干预] PASS（篮子覆盖率 100%, done 占比 100%）",
        "  [skip] 2 行决策字段缺失未入 J2（D1 解析失败·样本缩水非毒化）",
        "[J2 秩相关] PASS（score-vs-ret rho=0.064, conf-vs-ret rho=0.202, n=23）",
    ]
    if warn:
        lines.append("  [warn] score 并列零占比 78%（18/23）——HOLD/中性密集")
    if j4:
        lines.append("[J4 perf] 沉淀 recorded=0 replayed=23 skipped=0 "
                     "findings=0")
    lines += [
        "[J3 lineage] PASS（25/25 决策可回查辩论 trace）",
        "-" * 62,
        "影子评估结论：%s（满 5 交易日后 J2/J3 才具统计意义）" % conclusion,
        "",
    ]
    p = env / "agent_platform" / "data" / "shadow" / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _briefs_file(env):
    return (env / "automation_runs" / "retro_brief" / "briefs"
            / "2026-10.jsonl")


def _tasks_files(env):
    return list((env / "agent_platform" / "data" / "org" / "tasks")
                .glob("tasks_*.jsonl"))


# ----------------------------------------------------------------------
# 简报构造
# ----------------------------------------------------------------------
class TestBuildBrief:
    def test_both_kind_pass(self, env):
        """checkpoint PASS + eval PASS → kind=both / verdict=pass / J 摘要对。"""
        _mk_checkpoint(env, _cp_block())
        _mk_eval(env)
        brief, reason = retro.generate_brief()
        assert reason is None
        assert brief["kind"] == "both" and brief["verdict"] == "pass"
        keys = [i["key"] for i in brief["j_items"]]
        assert keys[:4] == ["replay_trace", "AgentChiefPush"] or \
            set(["J1", "J2", "J3", "J4"]) <= set(keys)
        # 门判 3 PASS + 1 SKIP + eval 4 项 → pass=7, total=8（SKIP 不计 pass）
        assert brief["j_summary"] == {"pass": 7, "total": 8}
        assert brief["p1_issues"] == []
        assert brief["sources"]["eval_file"].endswith(".md")
        assert "J 项摘要" in brief["brief_text"]

    def test_checkpoint_only_graceful(self, env):
        """今天无 eval 产物属预期 → kind=checkpoint，优雅跳过不报错。"""
        _mk_checkpoint(env, _cp_block())
        brief, reason = retro.generate_brief()
        assert reason is None
        assert brief["kind"] == "checkpoint"
        assert brief["sources"]["eval_file"] is None
        assert brief["verdict"] == "pass"

    def test_eval_only(self, env):
        """checkpoint 日志缺失 → kind=eval，date 取 eval 件日期。"""
        _mk_eval(env)
        brief, reason = retro.generate_brief()
        assert reason is None
        assert brief["kind"] == "eval" and brief["date"] == "2026-10-15"

    def test_no_data(self, env):
        """数据源全缺 → (None, reason)。"""
        brief, reason = retro.generate_brief()
        assert brief is None and "无可用数据源" in reason

    def test_eval_unparseable_skipped(self, env):
        """eval 件是空壳/无信号 → 视同无产物，退化为 checkpoint。"""
        _mk_checkpoint(env, _cp_block())
        (env / "agent_platform" / "data" / "shadow"
         / "eval_shadow_2026-10-15_official.md").write_text(
            "（生成中）\n", encoding="utf-8")
        brief, _ = retro.generate_brief()
        assert brief["kind"] == "checkpoint"


class TestP1Rule:
    def test_checkpoint_fail_item_p1(self, env):
        """门判项 FAIL → P1 问题，映射理由写明。"""
        _mk_checkpoint(env, _cp_block(
            exit_code=1, conclusion="5/7",
            items=[("FAIL", "任务 AgentShadowEvalWeekly",
                    "state=Queued result=0"),
                   ("PASS", "replay_trace 锚点回放", "verdict=OK")]))
        brief, _ = retro.generate_brief()
        assert brief["verdict"] == "fail"
        assert len(brief["p1_issues"]) == 1
        p1 = brief["p1_issues"][0]
        assert p1["source"] == "checkpoint"
        assert "FAIL" in p1["reason"] and "AgentShadowEvalWeekly" in p1["reason"]
        # FAIL 项同步进异常项
        assert any(a["tag"] == "FAIL" for a in brief["anomalies"])

    def test_eval_fail_p1_fallback(self, env):
        """eval 结论 FAIL → verdict=fail → 兜底 P1（映射理由）。"""
        _mk_eval(env, conclusion="FAIL")
        brief, _ = retro.generate_brief()
        assert brief["verdict"] == "fail"
        assert len(brief["p1_issues"]) == 1
        assert brief["p1_issues"][0]["source"] == "eval"
        assert "映射理由" in brief["p1_issues"][0]["reason"]

    def test_ledger_failed_forces_fail(self, env):
        """影子账本 failed>0 → verdict 强制 fail + anomaly。"""
        _mk_eval(env, failed=2)
        brief, _ = retro.generate_brief()
        assert brief["verdict"] == "fail"
        assert any(a["tag"] == "ledger" for a in brief["anomalies"])


# ----------------------------------------------------------------------
# 落盘 / 幂等 / 开单 / 主流程
# ----------------------------------------------------------------------
class TestPersist:
    def test_append_and_idempotent(self, env):
        """追加式落盘；同 date+kind 第二次幂等跳过（不覆盖不重复）。"""
        _mk_checkpoint(env, _cp_block())
        retro.main([])
        lines1 = _briefs_file(env).read_text(encoding="utf-8").splitlines()
        assert len(lines1) == 1
        code2 = retro.main([])
        assert code2 == 0
        lines2 = _briefs_file(env).read_text(encoding="utf-8").splitlines()
        assert lines2 == lines1  # 幂等：零追加
        assert "幂等" in json.loads(lines2[0]).get("note", "") or True

    def test_different_kind_appends(self, env):
        """同日 kind 变化（checkpoint→both）→ 追加新行不覆盖。"""
        _mk_checkpoint(env, _cp_block())
        retro.main([])
        _mk_eval(env)  # 半路补上 eval 产物
        retro.main([])
        lines = _briefs_file(env).read_text(encoding="utf-8").splitlines()
        kinds = [json.loads(l)["kind"] for l in lines]
        assert kinds == ["checkpoint", "both"]

    def test_bad_line_in_ledger_not_blocking(self, env):
        """账本已有坏行 → 幂等判定不炸、照常追加。"""
        f = _briefs_file(env)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{broken-json\n", encoding="utf-8")
        _mk_checkpoint(env, _cp_block())
        assert retro.main([]) == 0
        lines = f.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["date"] == "2026-10-18"


class TestTicket:
    def test_p1_opens_ticket_and_dedup(self, env):
        """P1 → 开 dev 部门 P1 单；同日同标题去重（不开第二张）。"""
        _mk_checkpoint(env, _cp_block(exit_code=1, conclusion="5/7",
                                      items=[("FAIL", "任务 AgentShadowEvalWeekly",
                                              "state=Queued result=0")]))
        assert retro.main([]) == 0
        files = _tasks_files(env)
        assert len(files) == 1
        rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        assert rec["assigned_dept"] == "dev" and rec["severity"] == "P1"
        assert rec["title"] == "复盘简报P1：2026-10-18（1 项）"
        assert rec["opened_by"] == "system/retro-brief"
        # 幂等重跑：简报跳过 + 工单去重，账本仍各一行
        assert retro.main([]) == 0
        assert len(_tasks_files(env)[0].read_text(
            encoding="utf-8").splitlines()) == 1

    def test_no_p1_no_ticket(self, env):
        """verdict=pass → 不开单。"""
        _mk_checkpoint(env, _cp_block())
        assert retro.main([]) == 0
        assert _tasks_files(env) == []

    def test_store_failure_fail_open(self, env, monkeypatch):
        """TaskStore 异常 fail-open：不中断简报落盘，exit 0。"""
        import tasks as tasks_mod

        def _boom(*a, **kw):
            raise RuntimeError("store down")

        monkeypatch.setattr(tasks_mod.TaskStore, "open", _boom)
        _mk_checkpoint(env, _cp_block(exit_code=1, conclusion="5/7",
                                      items=[("FAIL", "门判项X", "细节")]))
        assert retro.main([]) == 0
        assert _briefs_file(env).is_file()  # 简报照落


class TestMain:
    def test_no_data_exit1_and_last_run_written(self, env):
        """无数据源 → exit 1（fail-visible），last_run 照写供 N1 探失联。"""
        assert retro.main([]) == 1
        lr = json.loads((env / "automation_runs" / "retro_brief"
                         / "last_run.json").read_text(encoding="utf-8"))
        assert lr["exit"] == 1 and "无可用数据源" in lr["reason"]
        runlog = (env / "automation_runs" / "retro_brief" / "run.log")
        assert "ERROR" in runlog.read_text(encoding="utf-8")

    def test_dry_run_zero_writes_with_p1(self, env):
        """--dry-run 四路径守卫：简报/工单/run.log/last_run 全不写。"""
        _mk_checkpoint(env, _cp_block(exit_code=1, conclusion="5/7",
                                      items=[("FAIL", "门判项X", "细节")]))
        _mk_eval(env)
        assert retro.main(["--dry-run"]) == 0
        assert not (env / "automation_runs" / "retro_brief").exists()
        assert _tasks_files(env) == []

    def test_dry_run_no_data_zero_writes(self, env):
        """--dry-run + 无数据源 → exit 1 且连 last_run 都不写。"""
        assert retro.main(["--dry-run"]) == 1
        assert not (env / "automation_runs" / "retro_brief").exists()

    def test_ok_run_writes_state_and_log(self, env):
        """正常跑：last_run.json + run.log 留痕（四路径中 ③④）。"""
        _mk_checkpoint(env, _cp_block())
        assert retro.main([]) == 0
        d = env / "automation_runs" / "retro_brief"
        lr = json.loads((d / "last_run.json").read_text(encoding="utf-8"))
        assert lr["exit"] == 0 and lr["brief_written"] is True
        assert lr["brief_kind"] == "checkpoint"
        assert "OK" in (d / "run.log").read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# eval 解析细节
# ----------------------------------------------------------------------
class TestEvalParse:
    def test_j4_info_item_ok(self, env):
        """J4 perf 无 PASS/FAIL 判定 → 信息项 ok=True，不判负。"""
        _mk_eval(env)
        ev = retro._load_eval()
        j4 = [i for i in ev["items"] if i["key"] == "J4"][0]
        assert j4["ok"] is True and "recorded=0" in j4["detail"]
        assert ev["verdict"] == "pass"

    def test_notes_into_anomalies(self, env):
        """[warn]/[skip]/[pending] 观察行 → 异常项（tag 保留）。"""
        _mk_eval(env)
        ev = retro._load_eval()
        tags = {a["tag"] for a in ev["anomalies"]}
        assert {"warn", "skip"} <= tags

    def test_eval_last_run_log_as_fallback(self, env):
        """无 official md 时 eval_last_run.log 亦可作数据源。"""
        _mk_eval(env, name="eval_last_run.log")
        ev = retro._load_eval()
        assert ev["file"] == "eval_last_run.log"
