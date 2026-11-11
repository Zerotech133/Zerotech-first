# 黄忠 11-10 复核 Preflight 报告

- 生成时间：2026-11-10T22:30:00+08:00（Asia/Shanghai）
- 目标时点：2026-11-10T22:30:00+08:00（11-10 复盘会 22:30）
- 仓库根：`C:\项目\老田的AI助手`；数据目录：`C:\项目\老田的AI助手\work\huangzhong`
- 只读编排：本工具不写业务数据；review/go_no_go 子进程各自落报告
- **结论：ready = True｜blockers = 0｜warnings = 0**

## 一、检查表

| # | 检查项 | 命令/来源 | 结果 | 状态 |
|---|---|---|---|---|
| 1 | 证据新鲜度（48h 认证门） | agent_platform/data/test_baseline/latest.json；work/p1/redteam/redteam_*.json（最新） | 基线：新鲜 2.5h（门 ≤48h）；红队：新鲜 2.4h（门 ≤48h） | ✓ |
| 2 | 六条复核报告（review） | python -X utf8 tools/huangzhong_review.py --json → work/huangzhong/review_*.json（最新） | 六条 5/6 ✓；报告 review_20261110_223023.json（-0.0h 前） | ✓ |
| 3 | Go/No-Go 决策草案 | python -X utf8 tools/huangzhong_go_no_go.py --json --draft-dir <out>（存在才跑） | rc=1 → NO_GO（建议 NO-GO） | ✓ |
| 4 | ④ 满窗 maturity | acceptance_*.json 直扫（PASS 非演练；generated_at+7d） | now 满窗 153 / 跟踪 188；最早满窗 2026-11-04T07:18:01+08:00；11-10 22:30 预计满窗 153 | ✓ |
| 5 | 事故台账（incidents） | work/huangzhong/incidents.jsonl | 存在 0 条（近 7 天 0 条；latest=—） | ✓ |

## 二、Blockers（ready=False 原因）

- 无

## 三、Warnings

- 无

## 四、六条复核（review 最新报告）

- 报告：`C:\项目\老田的AI助手\work\huangzhong\review_20261110_223023.json`（生成于 2026-11-10T22:30:23+08:00；子进程 rc=1）
- 综合：overall_pass = False（5/6 条 ✓）

| 条目 | 状态 | 实测 | 达标线 |
|---|---|---|---|
| ① PASS 验收样本量 | ✓ | 341/10 单 | ≥10 单 PASS |
| ② 受控拒收演练 | ✓ | 2 次（REJECT=1 OBSERVE=1） | ≥1 次（verdict∈{REJECT,OBSERVE}） |
| ③ 误拒绝率 <5% | pending | 复核 28/77 单，清单不完整 | <5 推翻率（自然 REJECT≥20） |
| ④ 漏放 = 0 | ✓ | 满窗 153 单，漏放 0 | 满 7 天窗口 PASS 单事故数 = 0 |
| ⑤ 证据链完整率 100% + 年龄 ≤48h | ✓ | 完整率 100.0%（335/335），新鲜 335/335，年龄门 ≤48h；legacy v0 豁免 6 单 | 11 项必需字段完整率 100%；证据年龄 ≤48h |
| ⑥ 赵云抽查 3/3 一致 | ✓ | 3/3 一致 | 3 单抽查一致（3/3） |

- KPI：窗口 7 天；runs=458；merge_done=149；覆盖率=307.38%

## 五、证据新鲜度（48h 认证门）

- 基线：`C:\项目\老田的AI助手\agent_platform\data\test_baseline\latest.json`；generated_at=2026-11-10T20:00:01+08:00；年龄=2.5h；stale=False
- 红队：`C:\项目\老田的AI助手\work\p1\redteam\redteam_20261110_200528.json`；generated_at=2026-11-10T20:05:28+08:00；年龄=2.4h；stale=False；pass_rate=1.0（15/15）
- 说明：48h 为认证口径工具默认门；11-04 运行门按 R3 收紧为 ≤24h（运行门严于认证门）

## 六、④ 满窗（maturity）

- PASS 非演练样本：341 单；now 满窗 **153** 单 / 跟踪 188 单
- 最早满窗：2026-11-04T07:18:01+08:00；下一满窗：2026-11-10T22:45:01+08:00；11-10 22:30 预计满窗：**153** 单
- 口径：now 满窗 153 单 / 跟踪 188 单；11-10 22:30 预计满窗 153 单
- now 已满窗样本：acceptance_009f5b65.json（2026-11-04T10:54:45+08:00）、acceptance_02fc06f8.json（2026-11-07T14:15:00+08:00）、acceptance_0729e8b2.json（2026-11-08T19:45:00+08:00）、acceptance_074f111f.json（2026-11-09T03:15:02+08:00）、acceptance_09e2218a.json（2026-11-08T05:15:00+08:00）、acceptance_0a2d0aee.json（2026-11-06T10:45:01+08:00）、acceptance_0e56b81c.json（2026-11-08T09:15:01+08:00）、acceptance_10dcd2ae.json（2026-11-07T01:21:13+08:00）、acceptance_189672d4.json（2026-11-07T15:15:01+08:00）、acceptance_1a6d8580.json（2026-11-08T07:15:01+08:00）
- 跟踪中（最早）样本：acceptance_00135966.json（2026-11-16T15:45:01+08:00 满窗）、acceptance_01095301.json（2026-11-14T11:15:00+08:00 满窗）、acceptance_025fc94c.json（2026-11-16T00:15:05+08:00 满窗）、acceptance_0353b0bb.json（2026-11-17T15:15:00+08:00 满窗）、acceptance_0396559a.json（2026-11-17T18:15:00+08:00 满窗）、acceptance_03a05908.json（2026-11-14T15:15:01+08:00 满窗）、acceptance_05c7cfca.json（2026-11-16T16:45:01+08:00 满窗）、acceptance_0794099e.json（2026-11-13T07:15:04+08:00 满窗）、acceptance_0983a123.json（2026-11-13T09:45:06+08:00 满窗）、acceptance_09b8f298.json（2026-11-13T06:45:03+08:00 满窗）

## 七、事故台账（incidents）

- 路径：`C:\项目\老田的AI助手\work\huangzhong\incidents.jsonl`（exists=True）
- 事故台账 0 条（近 7 天 0 条；只读引用，漏放判定见复核报告 ④）

## 八、复跑命令

```powershell
cd C:\项目\老田的AI助手
C:\项目\py312\python.exe -X utf8 tools\huangzhong_1104_preflight.py --json --target-date 2026-11-10
```

> 本报告为 11-10 复核前置自检（只读编排），不替代 Go/No-Go 决策与老田签字；ready=True 仅表示前置数据齐备，不等于认证/Go 通过。
