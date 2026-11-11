# 黄忠认证 v2 六条复核报告

- 生成时间：2026-11-10T22:16:31+08:00
- 数据根：`C:\项目\老田的AI助手`；工作目录：`C:\项目\老田的AI助手\work\huangzhong`；窗口：7 天
- 只读工具：不写业务代码/验收单/证据，仅落本复核报告

## 一、六条总览

| # | 指标 | 状态 | 实测 | 达标线 |
|---|---|---|---|---|
| ① | PASS 验收样本量 | ✓ | 341/10 单 | ≥10 单 PASS |
| ② | 受控拒收演练 | ✓ | 2 次（REJECT=1 OBSERVE=1） | ≥1 次（verdict∈{REJECT,OBSERVE}） |
| ③ | 误拒绝率 <5% | pending | 复核 28/77 单，清单不完整 | <5 推翻率（自然 REJECT≥20） |
| ④ | 漏放 = 0 | ✓ | 满窗 153 单，漏放 0 | 满 7 天窗口 PASS 单事故数 = 0 |
| ⑤ | 证据链完整率 100% + 年龄 ≤48h | ✓ | 完整率 100.0%（335/335），新鲜 335/335，年龄门 ≤48h；legacy v0 豁免 6 单 | 11 项必需字段完整率 100%；证据年龄 ≤48h |
| ⑥ | 赵云抽查 3/3 一致 | ✓ | 3/3 一致 | 3 单抽查一致（3/3） |

**综合：overall_pass = False（5/6 条 ✓）**

未达标/待补证项：
- ③ 误拒绝率 <5%：pending（复核 28/77 单，清单不完整）

## 二、逐条明细

### ① PASS 验收样本量（✓）
- 实测：341/10 单；达标线：≥10 单 PASS
- PASS 单 341 份；排除演练单 0 份；去重剔除 0 份
  - 7a1bfaf8 acceptance_7a1bfaf8.json（2026-11-10T19:45:01+08:00；scope=default_latest）
  - 4ed29c8b acceptance_4ed29c8b.json（2026-11-10T19:15:00+08:00；scope=default_latest）
  - b06e8e17 acceptance_b06e8e17.json（2026-11-10T18:45:00+08:00；scope=default_latest）
  - 0396559a acceptance_0396559a.json（2026-11-10T18:15:00+08:00；scope=default_latest）
  - a13e4704 acceptance_a13e4704.json（2026-11-10T17:45:00+08:00；scope=default_latest）
  - 17fbf9df acceptance_17fbf9df.json（2026-11-10T17:15:01+08:00；scope=default_latest）
  - b8d1abe7 acceptance_b8d1abe7.json（2026-11-10T16:45:00+08:00；scope=default_latest）
  - f6650f8d acceptance_f6650f8d.json（2026-11-10T16:15:00+08:00；scope=default_latest）
  - 243fff29 acceptance_243fff29.json（2026-11-10T15:45:00+08:00；scope=default_latest）
  - 0353b0bb acceptance_0353b0bb.json（2026-11-10T15:15:00+08:00；scope=default_latest）
  - def9bce1 acceptance_def9bce1.json（2026-11-10T14:45:00+08:00；scope=default_latest）
  - 5a28d6fa acceptance_5a28d6fa.json（2026-11-10T14:15:00+08:00；scope=default_latest）
  - 916eee7f acceptance_916eee7f.json（2026-11-10T13:45:00+08:00；scope=default_latest）
  - 2a3aeb8c acceptance_2a3aeb8c.json（2026-11-10T12:45:01+08:00；scope=default_latest）
  - 8dca81c9 acceptance_8dca81c9.json（2026-11-10T12:15:01+08:00；scope=default_latest）
  - 8142bcb0 acceptance_8142bcb0.json（2026-11-10T11:45:01+08:00；scope=default_latest）
  - ab21e5ce acceptance_ab21e5ce.json（2026-11-10T11:15:01+08:00；scope=default_latest）
  - e861ec3d acceptance_e861ec3d.json（2026-11-10T10:45:01+08:00；scope=default_latest）
  - 25a5d5a4 acceptance_25a5d5a4.json（2026-11-10T10:15:00+08:00；scope=default_latest）
  - 8acf02e8 acceptance_8acf02e8.json（2026-11-10T09:45:00+08:00；scope=default_latest）
  - 3345e2dc acceptance_3345e2dc.json（2026-11-10T08:45:01+08:00；scope=default_latest）
  - 3d484fbc acceptance_3d484fbc.json（2026-11-10T08:15:01+08:00；scope=default_latest）
  - 2b20dd6b acceptance_2b20dd6b.json（2026-11-10T07:45:01+08:00；scope=default_latest）
  - 2fb6094d acceptance_2fb6094d.json（2026-11-10T06:15:01+08:00；scope=default_latest）
  - 2cc5b987 acceptance_2cc5b987.json（2026-11-10T05:15:02+08:00；scope=default_latest）
  - 82fcabe9 acceptance_82fcabe9.json（2026-11-10T00:15:00+08:00；scope=default_latest）
  - 92b08570 acceptance_92b08570.json（2026-11-09T22:45:01+08:00；scope=default_latest）
  - 41ce764a acceptance_41ce764a.json（2026-11-09T21:45:01+08:00；scope=default_latest）
  - b2a4d06b acceptance_b2a4d06b.json（2026-11-09T21:15:01+08:00；scope=default_latest）
  - c87e5761 acceptance_c87e5761.json（2026-11-09T20:45:01+08:00；scope=default_latest）
  - d158caef acceptance_d158caef.json（2026-11-09T19:15:01+08:00；scope=default_latest）
  - 7dc5fba7 acceptance_7dc5fba7.json（2026-11-09T18:45:01+08:00；scope=default_latest）
  - 47b9aac9 acceptance_47b9aac9.json（2026-11-09T17:45:01+08:00；scope=default_latest）
  - 518e495f acceptance_518e495f.json（2026-11-09T17:15:02+08:00；scope=default_latest）
  - 05c7cfca acceptance_05c7cfca.json（2026-11-09T16:45:01+08:00；scope=default_latest）
  - 12c32c60 acceptance_12c32c60.json（2026-11-09T16:15:01+08:00；scope=default_latest）
  - 00135966 acceptance_00135966.json（2026-11-09T15:45:01+08:00；scope=default_latest）
  - 6eb47a98 acceptance_6eb47a98.json（2026-11-09T15:15:01+08:00；scope=default_latest）
  - 91853b6e acceptance_91853b6e.json（2026-11-09T14:45:01+08:00；scope=default_latest）
  - efa67dea acceptance_efa67dea.json（2026-11-09T14:15:01+08:00；scope=default_latest）
  - 6706b48f acceptance_6706b48f.json（2026-11-09T13:45:00+08:00；scope=default_latest）
  - 14732868 acceptance_14732868.json（2026-11-09T13:15:01+08:00；scope=default_latest）
  - c44324fa acceptance_c44324fa.json（2026-11-09T12:45:00+08:00；scope=default_latest）
  - bf6cb490 acceptance_bf6cb490.json（2026-11-09T12:15:00+08:00；scope=default_latest）
  - eac4a5d0 acceptance_eac4a5d0.json（2026-11-09T11:15:01+08:00；scope=default_latest）
  - d98f6df1 acceptance_d98f6df1.json（2026-11-09T10:45:02+08:00；scope=default_latest）
  - d3f134eb acceptance_d3f134eb.json（2026-11-09T10:15:02+08:00；scope=default_latest）
  - ed42380f acceptance_ed42380f.json（2026-11-09T09:45:02+08:00；scope=default_latest）
  - 9d6b340e acceptance_9d6b340e.json（2026-11-09T09:15:03+08:00；scope=default_latest）
  - 4a8fb35d acceptance_4a8fb35d.json（2026-11-09T08:45:03+08:00；scope=default_latest）

### ② 受控拒收演练（✓）
- 实测：2 次（REJECT=1 OBSERVE=1）；达标线：≥1 次（verdict∈{REJECT,OBSERVE}）
- 台账文件：C:\项目\老田的AI助手\work\huangzhong\drills\drill_ledger_20261030.json；条目 2
  - acceptance_76081eea.json（76081eea）：REJECT
  - acceptance_02317909.json（02317909）：OBSERVE

### ③ 误拒绝率 <5%（pending）
- 实测：复核 28/77 单，清单不完整；达标线：<5 推翻率（自然 REJECT≥20）
- 自然 REJECT 76 单；演练 REJECT 1 单；REJECT 总单 77
- 人工复核清单：`work/huangzhong/reject_review*.json`（已发现 2 份、29 行）
- 口径=推翻率（自然 REJECT≥20）：已复核 28/77 单；推翻 0 单
- 复核清单不完整（49/77 未见结论）→ pending；补齐 reject_review*.json 后重跑

### ④ 漏放 = 0（✓）
- 实测：满窗 153 单，漏放 0；达标线：满 7 天窗口 PASS 单事故数 = 0
- 满窗 PASS 单 153；跟踪中 188；漏放 0 起（窗口 7 天）
  - 满窗 4beda7a6 2026-11-10T22:15:01+08:00：无事故
  - 满窗 9dcf3a8f 2026-11-10T21:45:01+08:00：无事故
  - 满窗 d1918088 2026-11-10T21:15:00+08:00：无事故
  - 满窗 9cc93eda 2026-11-10T20:30:39+08:00：无事故
  - 满窗 f381a408 2026-11-10T19:15:01+08:00：无事故
  - 满窗 9d3efd72 2026-11-10T18:15:00+08:00：无事故
  - 满窗 a7748782 2026-11-10T17:45:01+08:00：无事故
  - 满窗 1c6e4a3e 2026-11-10T17:34:25+08:00：无事故
  - 满窗 216bd4d2 2026-11-10T17:15:01+08:00：无事故
  - 满窗 39df8806 2026-11-10T16:45:01+08:00：无事故
  - 跟踪 7a1bfaf8：2026-11-17T19:45:01+08:00 满窗
  - 跟踪 4ed29c8b：2026-11-17T19:15:00+08:00 满窗
  - 跟踪 b06e8e17：2026-11-17T18:45:00+08:00 满窗
  - 跟踪 0396559a：2026-11-17T18:15:00+08:00 满窗
  - 跟踪 a13e4704：2026-11-17T17:45:00+08:00 满窗
  - 跟踪 17fbf9df：2026-11-17T17:15:01+08:00 满窗
  - 跟踪 b8d1abe7：2026-11-17T16:45:00+08:00 满窗
  - 跟踪 f6650f8d：2026-11-17T16:15:00+08:00 满窗
  - 跟踪 243fff29：2026-11-17T15:45:00+08:00 满窗
  - 跟踪 0353b0bb：2026-11-17T15:15:00+08:00 满窗

### ⑤ 证据链完整率 100% + 年龄 ≤48h（✓）
- 实测：完整率 100.0%（335/335），新鲜 335/335，年龄门 ≤48h；legacy v0 豁免 6 单；达标线：11 项必需字段完整率 100%；证据年龄 ≤48h
- 证据链完整率 100.0%（335/335）；新鲜 335/335；年龄门 ≤48h
- legacy v0 豁免 6 单（不计入⑤分母；口径=exempt）：acceptance_ebd24931.json、acceptance_ff3a828c.json、acceptance_9922d6dc.json、acceptance_009f5b65.json、acceptance_88091a92.json、acceptance_725b8801.json
- legacy v0 豁免：6 单缺证据年龄字段，不计入⑤分母（老田 2026-10-30 拍板；--legacy-v0-policy count 回旧行为）

### ⑥ 赵云抽查 3/3 一致（✓）
- 实测：3/3 一致；达标线：3 单抽查一致（3/3）
- 抽查记录：`C:\项目\老田的AI助手\work\huangzhong\audit_sample_3.json`（seed=20261030-hz-audit）
- 3/3 一致达标；任一项不一致 → 该单作废重验（本工具判 ✗）

## 三、本周 KPI 摘要（daily.jsonl）
- 窗口 7 天；runs=458；PASS=389 REJECT=69 OBSERVE=0
- merge_done（分母）=155（可读）；验收覆盖率=295.48%
- 口径：覆盖率 = 窗口内 runs / merge_done（抽检回填单不计，沿用决策包第六节口径）；误拒绝/漏放见 ③④

## 五、复跑命令

```powershell
cd C:\项目\老田的AI助手
C:\项目\py312\python.exe -X utf8 tools\huangzhong_review.py --json
```

> 判据：docs/plans/2026-10-30_黄忠认证标准v2与验收工具证据口径拍板_v1.md 第二节；仅六条全 ✓ 才 overall_pass=True；pending/tracking 均不通过。
