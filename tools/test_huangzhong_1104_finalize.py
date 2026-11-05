# -*- coding: utf-8 -*-
"""黄忠 11-04 终版回执生成器 单测（tmp 夹具；不碰真实 deliverables/work/）。

覆盖：
  · 正常生成：九段 + 三口径覆盖率 + 签字表 + 人工确认搬运；终版模板不生效声明；
  · 输入缺失 fail-visible：显式 --summary/--receipt 不存在 → rc2；自动发现全缺 → rc2；
  · 缺 incidents 人工确认 → 生成但标注「待人工确认」；
  · 已存在不覆盖（除非 --force）；
  · runner 汇总 JSON 输入 / 缺 risk_coverage 行 → 风险加权口径缺失标注；
  · receipt 草案解析失败 → 六条表回退汇总口径并给出待补提示；
  · --json CLI 结构 + tmp 输出路径；--no-write / 不写 work/；--now 非法 rc2。
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone.utc  # 测试用固定时钟（tz 不参与语义）
NOW = datetime(2026, 11, 1, 7, 30, tzinfo=TZ)
NOW_ISO = NOW.isoformat(timespec="seconds")


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_1104_finalize",
        _TOOLS / "huangzhong_1104_finalize.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 夹具内容

SUMMARY_MD = """# 黄忠 11-04 复核执行汇总（2026-11-01T05:09:12+08:00）

> 工具：`tools/huangzhong_1104_run.py` v1（只读编排）｜状态：**ok**
> 说明：本汇总只搬运子工具结果，不改变任何判定；Go/No-Go 由老田 L3 决策。

## 一、执行总览

| 步骤 | rc | 状态 | 关键结果 |
|---|---|---|---|
| preflight | 0 | ✓ | ready=True blockers=0 warnings=0 mature_at_1030=2 |
| review | 1 | ✓ | 六条 5/6（overall=False） |
| weekly | 0 | ✓ | runs=86 merge=1591 coverage=0.054 |
| receipt | 0 | ✓ | draft=C:\\项目\\deliverables\\{receipt_name} |
| risk_coverage | 0 | ✓ | 风险加权=0.0456 等权=0.0478 |
| corpus | 0 | ✓ | total=— legacy=6 drill=2 |
| peer_verify | 0 | ✓ | status=ok agreement=— |
| evidence_lint | 0 | ✓ | 完整率=1.0 scanned=78 |
| incidents | 0 | ✓ | exists=True rows=0 |
| health | 0 | ✓ | status=ok |
| perf_signal | 0 | ✓ | status=ok |
| security_signal | 0 | ✓ | status=ok |

## 二、异常与建议

- 全部步骤通过；可进入人工核对（六条/三口径覆盖率/④满窗）与 Go/No-Go。
"""

RECEIPT_MD = """# 黄忠观察期复核回执（草案）· 2026-11-04

> **状态：草案（非最终回执）**——本文件由 `tools/huangzhong_1104_receipt.py` 自动拼装。
> 工单：`WO-HZ-RECEIPT-20261031` ｜ 生成：2026-11-01T05:38:17+08:00 ｜ 目标复核：2026-11-04T10:00:00+08:00 ｜ 口径：认证标准 v2 + runbook v6 §十

## 一、复核时间与环境

- 生成时间：`2026-11-01T05:38:17+08:00`（Asia/Shanghai）
- 目标复核时刻：`2026-11-04T10:00:00+08:00`

## 二、六条判据

**overall_pass = False（5/6 ✓）**；仅六条全 ✓ 才 overall_pass=true。

| # | 判据 | 状态 | 实测 | 证据 |
|---|---|---|---|---|
| ① | PASS 验收样本量 | ✓ 达标 | 77/10 单 | work/huangzhong/acceptance_*.json |
| ② | 受控拒收演练 | ✓ 达标 | 2 次（REJECT=1 OBSERVE=1） | drills 台账 |
| ③ | 误拒绝率 <5% | ✓ 达标 | 逐单复核 0 推翻（清单 1 条） | reject_review*.json |
| ④ | 漏放 = 0 | tracking 跟踪中 | 满窗 0/77 单（77 单跟踪中） | incidents.jsonl |
| ⑤ | 证据链完整率 100% + 年龄 ≤48h | ✓ 达标 | 完整率 100.0%（71/71） | evidence 段 |
| ⑥ | 赵云抽查 3/3 一致 | ✓ 达标 | 3/3 一致 | audit_sample_3.json |

- KPI（review）：runs=87 / merge_done=1595 / 覆盖率=5.45% / 窗口=7 天

## 三、覆盖率（当前 / 11-04 投影 / unique 补充）

| 口径 | 值 | 判读 |
|---|---|---|
| 主口径 runs / merge_done | 87 / 1595 = **5.45%** | 未达标/不可判定（门槛 ≥10.00%） |
| 11-04 投影（趋势工具） | runs≈239.70 / merge≈1562 = **15.35%**；unique≈**7.17%** | 预计达标 |
| unique-commit 补充 | unique_commit=77 / **unique_coverage=4.83%** | 防注水；仅补充说明 |

## 四、④ 满窗核对（漏放=0 前置）

- now 满窗：**0 单**；跟踪中：**77 单**；11-04 10:00 预计满窗：**2 单**
- 最早满窗：`2026-11-04T07:18:01+08:00`；下一满窗：`2026-11-04T07:18:01+08:00`
- ④ 当前状态：`tracking`（tracking = 时间未满窗，属预期，不阻塞 ②③⑤⑥）

| 预计满窗 commit | 验收单 | generated_at | window_end |
|---|---|---|---|
| 725b8801 | acceptance_725b8801.json | 2026-10-28T07:18:01+08:00 | 2026-11-04T07:18:01+08:00 |
| 88091a92 | acceptance_88091a92.json | 2026-10-28T07:47:03+08:00 | 2026-11-04T07:47:03+08:00 |

## 五、事故台账状态

- 来源：tools/huangzhong_incidents.py verify
- 台账文件：`work/huangzhong/incidents.jsonl` ｜ exists=True ｜ 行数=0 ｜ 坏行=0 ｜ issues=0
- **人工确认（老田，2026-10-31 14:42）**：**无已知事故**；记录 `work/huangzhong/incidents_confirm_20261031.md`

## 六、Go/No-Go 建议（含 L3 提示）

**建议：NO-GO**（code=`NO_GO`）

- 结论：六条未全 ✓（5/6）：按缺口补证后重跑复核与本草稿；本次不接 merge 前置（Go 需老田 L3 单独批准）
- 决策来源：tools/huangzhong_go_no_go.py
- 依据：
  - 六条仅 5/6 ✓，未达 GO 前置
  - 仅时间项待满窗：④ 当前满窗样本 0 单、跟踪 77 单
- **L3 提示**：即使建议 GO，接入「merge 前置·观察不阻断」仍需**老田 L3 单独批准**；本草案**不构成批准**。

## 七、证据路径清单

- review 六条报告：`work/huangzhong/review_20261101_053817.json`
- preflight 报告：`C:\\项目\\deliverables\\2026-11-04_黄忠复核preflight_20261101_053817.md`
- 验收单台账：`work/huangzhong/acceptance_*.json`

## 八、已知风险与待人工项

**数据/工具告警（fail-visible）**

- review：portal/data/dev_events.jsonl 坏行 1 行已跳过

**待人工项（草案不代替）**

1. 老田 Go/No-Go 决策 + 签字（L3；本草案不构成批准）；
2. 赵云抽查 3/3 一致性确认；

## 九、签署与生效（草案非最终）

| 角色 | 职责 | 签字位 |
|---|---|---|
| 黄忠（验收守门官） | 自证六条与证据链 | DSH-Lead 代执行（2026-11-04） |
| 赵云（平台质量门/审计） | 抽查 3/3 一致 | （待签） |
| 老田 | Go/No-Go 最终决策（L3） | （待签） |
"""

CONFIRM_MD = """# ④ 事故人工确认记录（2026-10-31）

- 确认人：老田
- 确认时间：2026-10-31 14:42
- 依据：work/huangzhong/incidents_scan_20261031.md（窗口扫描 1,988 事件、生产事故类=0）
- 台账：work/huangzhong/incidents.jsonl（已建立，0 条 = 无记录）
- 结论：**无已知事故**；授权写入 11-04 观察期复核回执的 ④ 人工核对/签字位。
- 备注：11-04 满窗时仍按 runbook 7.3 再核对一次。

---
作者：DSH-Lead｜状态：已确认
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    dlv = tmp_path / "deliverables"
    dlv.mkdir(parents=True)
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True)
    out = tmp_path / "out"
    mod = _load()
    # 隔离真实 deliverables：默认发现目录指向 tmp
    monkeypatch.setattr(mod, "DEFAULT_OUT_DIR", dlv)
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    return {"mod": mod, "dlv": dlv, "work": work, "out": out}


def write_inputs(env, *, summary: bool = True, receipt: bool = True,
                 confirm: bool = True, receipt_name: str | None = None,
                 summary_text: str | None = None,
                 receipt_text: str | None = None):
    dlv, work = env["dlv"], env["work"]
    receipt_name = receipt_name or "2026-11-04_黄忠观察期复核回执_草案_20261101_050913.md"
    if receipt:
        (dlv / receipt_name).write_text(receipt_text or RECEIPT_MD,
                                        encoding="utf-8")
    if summary:
        (dlv / "2026-11-04_黄忠复核执行汇总_20261101_050912.md").write_text(
            (summary_text or SUMMARY_MD).replace("{receipt_name}", receipt_name),
            encoding="utf-8")
    if confirm:
        (work / "incidents_confirm_20261031.md").write_text(
            CONFIRM_MD, encoding="utf-8")
    return receipt_name


def _run(mod, env, **kwargs):
    kwargs.setdefault("out_dir", str(env["out"]))
    kwargs.setdefault("work_dir", str(env["work"]))
    kwargs.setdefault("now", NOW)
    return mod.run(**kwargs)


def _work_snapshot(work: Path) -> dict:
    return {str(p.relative_to(work)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(work.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ 用例

def test_full_generation_nine_sections_signature_and_three_coverage(env):
    mod = env["mod"]
    write_inputs(env)
    before = _work_snapshot(env["work"])
    rep = _run(mod, env)

    assert rep["ok"] is True
    assert rep["written"] is True
    final = Path(rep["final_path"])
    assert final.is_file()
    assert final.name == "2026-11-04_黄忠观察期复核回执.md"
    text = final.read_text(encoding="utf-8")

    # 九段齐全
    for header in ("## 一、复核时间与环境", "## 二、六条判据（认证标准 v2）",
                   "## 三、三口径覆盖率", "## 四、④ 满窗核对",
                   "## 五、事故台账 + 人工确认",
                   "## 六、Go/No-Go 建议（含 L3 提示）",
                   "## 七、证据路径清单", "## 八、待人工项",
                   "## 九、签字表"):
        assert header in text
    # 终版模板未生效声明
    assert "终版模板，未签字前不生效" in text or "未签字，不生效" in text
    # 六条表
    for item_id in ("①", "②", "③", "④", "⑤", "⑥"):
        assert "| %s |" % item_id in text
    assert "overall_pass = False（5/6 ✓）" in text
    # 三口径覆盖率
    assert "5.45%" in text                  # runs/merge 主口径
    assert "4.83%" in text                  # unique
    assert "4.56%" in text                  # 风险加权（来自汇总）
    assert "等权 4.78%" in text or "等权 4.78%" in text.replace(" ", "")
    # ④ 满窗
    assert "预计满窗：**2 单**" in text
    assert "725b8801" in text
    # 事故台账 + 人工确认
    assert "无已知事故" in text
    assert "老田" in text and "2026-10-31 14:42" in text
    # L3 提示
    assert "L3" in text
    # 签字表三行
    for role in ("黄忠（验收守门官）", "赵云（平台质量门/审计）", "老田"):
        assert role in text
    assert text.count("（待签）") >= 2
    # 不写 work/
    assert _work_snapshot(env["work"]) == before
    # JSON 摘要
    assert rep["six_overall"]["passed"] == 5
    assert rep["three_coverage"]["run_merge"]["pct"] == pytest.approx(0.0545)
    assert rep["three_coverage"]["unique"]["pct"] == pytest.approx(0.0483)
    assert rep["three_coverage"]["risk_weighted"]["pct"] == pytest.approx(0.0456)


def test_explicit_missing_summary_rc2(env):
    mod = env["mod"]
    write_inputs(env)
    rep = _run(mod, env, summary_path=str(env["dlv"] / "nope.md"))
    assert rep["ok"] is False
    assert "汇总文件不存在" in (rep["error"] or "")
    assert rep["written"] is False


def test_explicit_missing_receipt_rc2(env):
    mod = env["mod"]
    write_inputs(env)
    rep = _run(mod, env, receipt_path=str(env["dlv"] / "nope.md"))
    assert rep["ok"] is False
    assert "receipt 草案不存在" in (rep["error"] or "")


def test_auto_discovery_both_missing_rc2(env):
    mod = env["mod"]
    rep = _run(mod, env)
    assert rep["ok"] is False
    assert "数据缺失" in (rep["error"] or "")


def test_missing_confirm_fail_visible_but_generates(env):
    mod = env["mod"]
    write_inputs(env, confirm=False)
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["written"] is True
    text = Path(rep["final_path"]).read_text(encoding="utf-8")
    assert "未找到 incidents 人工确认文件" in text
    assert "待人工确认" in text
    warnings = "\n".join(rep["warnings"])
    assert "人工确认" in warnings


def test_existing_not_overwritten_unless_force(env):
    mod = env["mod"]
    write_inputs(env)
    rep1 = _run(mod, env)
    assert rep1["ok"] is True
    final = Path(rep1["final_path"])
    original = final.read_text(encoding="utf-8")
    # 第二次：已存在 → rc2 且不覆盖
    rep2 = _run(mod, env)
    assert rep2["ok"] is False
    assert "已存在" in (rep2["error"] or "")
    assert final.read_text(encoding="utf-8") == original
    # --force → 覆盖成功
    rep3 = _run(mod, env, force=True)
    assert rep3["ok"] is True
    assert rep3["written"] is True
    assert rep3["force"] is True


def test_summary_json_input(env):
    mod = env["mod"]
    write_inputs(env, summary=False)
    summary_json = env["dlv"] / "2026-11-04_黄忠复核执行汇总_20261101_050912.json"
    summary_json.write_text(json.dumps({
        "ok": True, "tool": "huangzhong_1104_run.py",
        "generated_at": "2026-11-01T05:09:12+08:00", "status": "ok",
        "steps": [
            {"name": "preflight", "rc": 0, "ok": True,
             "summary": {"ready": True, "blockers": 0, "warnings": 0,
                         "mature_at_1030": 2}},
            {"name": "review", "rc": 1, "ok": True,
             "summary": {"passed": 5, "total": 6, "overall": False}},
            {"name": "weekly", "rc": 0, "ok": True,
             "summary": {"runs": 86, "merge_done": 1591, "coverage": 0.054}},
            {"name": "risk_coverage", "rc": 0, "ok": True,
             "summary": {"risk_weighted": 0.0456, "equal": 0.0478,
                         "merges": 1591}},
            {"name": "incidents", "rc": 0, "ok": True,
             "summary": {"exists": True, "rows": 0, "issues": 0}},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    rep = _run(mod, env)
    assert rep["ok"] is True
    text = Path(rep["final_path"]).read_text(encoding="utf-8")
    assert "4.56%" in text
    assert "5.45%" in text


def test_risk_coverage_missing_flagged(env):
    mod = env["mod"]
    summary = SUMMARY_MD.replace(
        "| risk_coverage | 0 | ✓ | 风险加权=0.0456 等权=0.0478 |\n", "")
    write_inputs(env, summary_text=summary)
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["three_coverage"]["risk_weighted"]["pct"] is None
    text = Path(rep["final_path"]).read_text(encoding="utf-8")
    assert "风险加权覆盖率缺失" in text
    assert any("风险加权" in w for w in rep["warnings"])


def test_receipt_without_sections_falls_back_to_summary(env):
    mod = env["mod"]
    write_inputs(env, receipt_text="# 空草案\n\n无章节内容。\n")
    rep = _run(mod, env)
    assert rep["ok"] is True
    text = Path(rep["final_path"]).read_text(encoding="utf-8")
    assert "六条明细缺失" in text
    assert "需人工补六条表" in text
    assert "review 5/6" in text
    assert any("receipt 草案" in w for w in rep["warnings"])


def test_batch_mismatch_warning(env):
    mod = env["mod"]
    write_inputs(env, receipt_name="2026-11-04_黄忠观察期复核回执_草案_20261101_999999.md")
    # 汇总内 draft 引用另一个文件名 → 触发不同批次提示
    summary = SUMMARY_MD.replace("{receipt_name}", "x_草案_old.md")
    (env["dlv"] / "2026-11-04_黄忠复核执行汇总_20261101_050912.md").write_text(
        summary, encoding="utf-8")
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert any("不同批次" in w for w in rep["warnings"])


def test_cli_json_and_tmp_output(env, capsys):
    mod = env["mod"]
    write_inputs(env)
    rc = mod.main(["--json", "--repo-root", str(env["work"].parent.parent),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["written"] is True
    assert out["final_path"].endswith("2026-11-04_黄忠观察期复核回执.md")
    assert out["six_items_count"] == 6
    assert out["signature_rows"] == 3
    assert out["sections"] == ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    assert out["three_coverage"]["risk_weighted"]["pct"] == pytest.approx(0.0456)
    assert out["confirm"]["confirmed"] is True
    assert Path(out["final_path"]).is_file()


def test_no_write_and_no_work_writes(env):
    mod = env["mod"]
    write_inputs(env)
    before = _work_snapshot(env["work"])
    rep = _run(mod, env, write=False)
    assert rep["ok"] is True
    assert rep["written"] is False
    assert not env["out"].exists() or not list(env["out"].glob("*"))
    assert _work_snapshot(env["work"]) == before


def test_cli_invalid_now_rc2(env, capsys):
    mod = env["mod"]
    rc = mod.main(["--json", "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]), "--now", "bad-time"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err


# ------------------------------------- 目标日期参数化（HZ-FINAL-DATE-20261104）

RECEIPT_1110_NAME = "2026-11-10_黄忠观察期复核回执_草案_20261101_050913.md"
SUMMARY_1110_NAME = "2026-11-10_黄忠复核执行汇总_20261101_050912.md"


def _target_1110_texts():
    """把 11-04 夹具改造成 11-10 批次（文件名与九段内日期引用同步）。"""
    summary = SUMMARY_MD.replace("2026-11-04_黄忠复核执行汇总",
                                 "2026-11-10_黄忠复核执行汇总")
    receipt = (RECEIPT_MD
               .replace("# 黄忠观察期复核回执（草案）· 2026-11-04",
                        "# 黄忠观察期复核回执（草案）· 2026-11-10")
               .replace("目标复核：2026-11-04T10:00:00+08:00",
                        "目标复核：2026-11-10T10:00:00+08:00")
               .replace("（当前 / 11-04 投影 / unique 补充）",
                        "（当前 / 11-10 投影 / unique 补充）")
               .replace("| 11-04 投影（趋势工具） |",
                        "| 11-10 投影（趋势工具） |")
               .replace("11-04 10:00 预计满窗：**2 单**",
                        "11-10 10:00 预计满窗：**2 单**")
               .replace("DSH-Lead 代执行（2026-11-04）",
                        "DSH-Lead 代执行（2026-11-10）"))
    return summary, receipt


def write_inputs_1110(env, *, both_batches: bool = False):
    dlv, work = env["dlv"], env["work"]
    summary_text, receipt_text = _target_1110_texts()
    if both_batches:
        write_inputs(env)          # 同时放 11-04 旧批次，验证不串批
    (dlv / RECEIPT_1110_NAME).write_text(receipt_text, encoding="utf-8")
    (dlv / SUMMARY_1110_NAME).write_text(
        summary_text.replace("{receipt_name}", RECEIPT_1110_NAME),
        encoding="utf-8")
    (work / "incidents_confirm_20261031.md").write_text(CONFIRM_MD,
                                                        encoding="utf-8")


def test_default_target_is_1104_unchanged(env):
    """默认（不传 target_date）= v1 11-04：终版文件名/标题/签字位/归档路径不变。"""
    mod = env["mod"]
    write_inputs(env)
    rep = _run(mod, env)
    assert rep["ok"] is True
    final = Path(rep["final_path"])
    assert final.name == "2026-11-04_黄忠观察期复核回执.md"
    assert Path(rep["inputs"]["summary"]).name.startswith(
        "2026-11-04_黄忠复核执行汇总_")
    text = final.read_text(encoding="utf-8")
    assert text.startswith("# 黄忠观察期复核回执（终版模板）· 2026-11-04")
    assert "11-04 10:00 预计满窗：**2 单**" in text
    assert "版本：2026-11-04 终版模板" in text
    assert "DSH-Lead 代执行（2026-11-04）" in text
    assert "`deliverables/2026-11-04_黄忠观察期复核回执.md`" in text


def test_target_date_1110_generates_and_validates(env):
    """显式 2026-11-10：按 11-10 批次发现输入 + 生成 11-10 终版模板（可自校验）。"""
    mod = env["mod"]
    write_inputs_1110(env)
    rep = _run(mod, env, target_date="2026-11-10")
    assert rep["ok"] is True
    assert rep["written"] is True
    assert rep["target_date"] == "2026-11-10"
    final = Path(rep["final_path"])
    assert final.name == "2026-11-10_黄忠观察期复核回执.md"
    text = final.read_text(encoding="utf-8")
    assert text.startswith("# 黄忠观察期复核回执（终版模板）· 2026-11-10")
    assert "④ 11-10 投影（参考）" in text
    assert "11-10 10:00 预计满窗：**2 单**" in text
    assert "版本：2026-11-10 终版模板" in text
    assert "DSH-Lead 代执行（2026-11-10）" in text
    assert "`deliverables/2026-11-10_黄忠观察期复核回执.md`" in text
    # 解析自校验：11-10 草案的满窗行被正确解析（口径不变，只换目标日）
    assert rep["maturity"]["mature_at_1030"] == 2
    assert rep["six_overall"]["passed"] == 5
    assert rep["three_coverage"]["run_merge"]["pct"] == pytest.approx(0.0545)
    # 输入发现锁定 11-10 批次
    assert Path(rep["inputs"]["summary"]).name.startswith(
        "2026-11-10_黄忠复核执行汇总_")
    assert Path(rep["inputs"]["receipt"]).name.startswith(
        "2026-11-10_黄忠观察期复核回执_草案_")
    assert not (env["out"] / "2026-11-04_黄忠观察期复核回执.md").exists()


def test_target_date_1110_does_not_mix_1104_batch(env):
    """11-04 与 11-10 两批输入并存时，11-10 复算只取 11-10 批次。"""
    mod = env["mod"]
    write_inputs_1110(env, both_batches=True)
    rep = _run(mod, env, target_date="2026-11-10")
    assert rep["ok"] is True
    assert Path(rep["inputs"]["summary"]).name == SUMMARY_1110_NAME
    assert Path(rep["inputs"]["receipt"]).name == RECEIPT_1110_NAME
    text = Path(rep["final_path"]).read_text(encoding="utf-8")
    assert "2026-11-10" in text and "11-10 10:00 预计满窗" in text


def test_target_date_default_equals_explicit_1104(env):
    """不传 target_date 与 --target-date 2026-11-04 终版产物逐字一致。"""
    mod = env["mod"]
    write_inputs(env)
    rep1 = _run(mod, env)
    text1 = Path(rep1["final_path"]).read_text(encoding="utf-8")
    rep2 = _run(mod, env, target_date="2026-11-04", force=True)
    text2 = Path(rep2["final_path"]).read_text(encoding="utf-8")
    assert text1 == text2
    assert rep1["final_path"] == rep2["final_path"]


def test_cli_target_date_1110(env, capsys):
    mod = env["mod"]
    write_inputs_1110(env)
    rc = mod.main(["--json", "--repo-root", str(env["work"].parent.parent),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--now", NOW_ISO,
                   "--target-date", "2026-11-10"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] is True
    assert out["final_path"].endswith("2026-11-10_黄忠观察期复核回执.md")
    assert out["target_time"].startswith("2026-11-10T10:00:00")
    text = Path(out["final_path"]).read_text(encoding="utf-8")
    assert "· 2026-11-10" in text


def test_cli_invalid_target_date_rc2(env, capsys):
    mod = env["mod"]
    rc = mod.main(["--json", "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--target-date", "2026/11/10"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err
