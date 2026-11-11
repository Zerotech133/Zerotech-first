# 黄忠 2026-11-04 Go/No-Go 决策草案（自动生成）

> **草案性质**：本文件由只读工具 `tools/huangzhong_go_no_go.py` 自动合成，供 11-04 观察期复核会讨论，**不是最终回执、不构成 Go 决定**；最终由老田人工签署。
> 生成时间：2026-11-10T22:16:31+08:00 ｜ 数据根：`C:\项目\老田的AI助手` ｜ 依据复核报告：`C:\项目\老田的AI助手\work\huangzhong\review_20261110_221631.json`
> 复核报告 generated_at：2026-11-10T22:16:31+08:00（年龄 0.0 h）

## 一、决策建议

**建议 NO-GO**

- 依据：六条全✓且kpi覆盖率≥10%→GO；六条全✓但覆盖率<10%→条件化GO；否则NO-GO
- 结论说明：六条未全 ✓（5/6）：按缺口补证后重跑复核与本草稿；本次不接 merge 前置（Go 需老田 L3 单独批准）
- 六条：**5/6 ✓**（复核报告 summary.overall_pass=False）
- 覆盖率：kpi 295.48%（runs 458 / merge_done 155）；unique-commit 220.00%（341/155）
- L3 提示：**Go/条件化 GO 均需老田 L3 单独批准**（本草案不构成批准）。

**判定依据**

- 六条仅 5/6 ✓，未达 GO 前置
- ③ 误拒绝率 <5%：pending（复核 28/77 单，清单不完整）

## 二、六条明细

| # | 指标 | 状态 | 实测 | 达标线 |
|---|---|---|---|---|
| ① | PASS 验收样本量 | ✓ | 341/10 单 | ≥10 单 PASS |
| ② | 受控拒收演练 | ✓ | 2 次（REJECT=1 OBSERVE=1） | ≥1 次（verdict∈{REJECT,OBSERVE}） |
| ③ | 误拒绝率 <5% | pending | 复核 28/77 单，清单不完整 | <5 推翻率（自然 REJECT≥20） |
| ④ | 漏放 = 0 | ✓ | 满窗 153 单，漏放 0 | 满 7 天窗口 PASS 单事故数 = 0 |
| ⑤ | 证据链完整率 100% + 年龄 ≤48h | ✓ | 完整率 100.0%（335/335），新鲜 335/335，年龄门 ≤48h；legacy v0 豁免 6 单 | 11 项必需字段完整率 100%；证据年龄 ≤48h |
| ⑥ | 赵云抽查 3/3 一致 | ✓ | 3/3 一致 | 3 单抽查一致（3/3） |

## 三、缺口与条件

**缺口（按优先级）**

- ③ 误拒绝率 <5%：pending（复核 28/77 单，清单不完整）

## 四、覆盖率（唯一 commit 补充口径）

- kpi 覆盖率：**295.48%**（458 runs / 155 merge_done；窗口 7 天）
- 阈值：≥10%（11-04 Go 门槛）
- 唯一 commit：**341** 个（PASS 单 341 份，重复单 0 份，缺 sha 0 份；排除演练单 2 份）
- unique-commit 覆盖率：**220.00%**
- 交叉核对：复核报告 ①=341 单；与复核报告 ① 计数一致
- 口径：唯一 commit 覆盖率 = 去重 PASS commit 数 / kpi.merge_done（排除演练单；抽检回填单仍按单计 commit）。补充口径，用于核对runs 覆盖率是否被重复验收注水。

## 五、④ 漏放（满窗 / 跟踪）

- 状态：**✓**（漏放 = 0）；满窗样本 153 单、跟踪 188 单，最早 2026-11-04T07:18:01+08:00 满窗（直扫验收单；review 明细截断 50 条仅展示）
- 已满窗样本：
  - acceptance_009f5b65.json（sha None）
  - acceptance_02fc06f8.json（sha None）
  - acceptance_0729e8b2.json（sha None）
  - acceptance_074f111f.json（sha None）
  - acceptance_09e2218a.json（sha None）
  - acceptance_0a2d0aee.json（sha None）
  - acceptance_0e56b81c.json（sha None）
  - acceptance_10dcd2ae.json（sha None）
  - acceptance_189672d4.json（sha None）
  - acceptance_1a6d8580.json（sha None）
  - acceptance_1bccbf22.json（sha None）
  - acceptance_1bd37c8d.json（sha None）
  - acceptance_1c6e4a3e.json（sha None）
  - acceptance_1e2e38bf.json（sha None）
  - acceptance_1e85953d.json（sha None）
  - acceptance_1e90f129.json（sha None）
  - acceptance_1ec7543e.json（sha None）
  - acceptance_1f4d977b.json（sha None）
  - acceptance_216bd4d2.json（sha None）
  - acceptance_22c8bae9.json（sha None）
  - acceptance_233c3ada.json（sha None）
  - acceptance_2363247d.json（sha None）
  - acceptance_24d94c9d.json（sha None）
  - acceptance_26b557b7.json（sha None）
  - acceptance_2a9aac08.json（sha None）
  - acceptance_2c20f11d.json（sha None）
  - acceptance_2d37b787.json（sha None）
  - acceptance_2e61af4f.json（sha None）
  - acceptance_309e7d13.json（sha None）
  - acceptance_31c89439.json（sha None）
  - acceptance_336ecc1c.json（sha None）
  - acceptance_33dfc322.json（sha None）
  - acceptance_39df8806.json（sha None）
  - acceptance_3c04aa9c.json（sha None）
  - acceptance_3ef7c42d.json（sha None）
  - acceptance_3f7733c4.json（sha None）
  - acceptance_42ed858e.json（sha None）
  - acceptance_438b307b.json（sha None）
  - acceptance_44a2bfed.json（sha None）
  - acceptance_44e3ac21.json（sha None）
  - acceptance_45a56a90.json（sha None）
  - acceptance_48cff91f.json（sha None）
  - acceptance_4beda7a6.json（sha None）
  - acceptance_4d8a19a2.json（sha None）
  - acceptance_4eb494f6.json（sha None）
  - acceptance_4ec2ae88.json（sha None）
  - acceptance_503853e2.json（sha None）
  - acceptance_50d7d7b8.json（sha None）
  - acceptance_52a734fa.json（sha None）
  - acceptance_52d0c4b9.json（sha None）

## 六、事故台账

- `C:\项目\老田的AI助手\work\huangzhong\incidents.jsonl`：事故台账存在：0 条（只读引用；漏放判定详见复核报告 ④）

## 七、依据路径与时间戳

- 复核报告：`C:\项目\老田的AI助手\work\huangzhong\review_20261110_221631.json`（generated_at=2026-11-10T22:16:31+08:00，窗口 7 天）
- 验收单目录：`C:\项目\老田的AI助手\work\huangzhong`（341 份 acceptance_*.json）
- drill 台账：`C:\项目\老田的AI助手\work\huangzhong\drills\drill_ledger_20261030.json`
- 事故台账：`C:\项目\老田的AI助手\work\huangzhong\incidents.jsonl`（exists=True）
- 本草案：`C:\Temp\hz_20261110_preflight_2215\2026-11-04_黄忠GoNoGo决策草案_20261110_221631.md`

## 八、Warnings（fail-visible，未编造数据）

- 无（fail-visible 检查全部通过）

## 九、复跑命令

```powershell
cd C:\项目\老田的AI助手
C:\项目\py312\python.exe -X utf8 tools\huangzhong_review.py --json
C:\项目\py312\python.exe -X utf8 tools\huangzhong_go_no_go.py --json
```

> 免责声明：本草案由工具自动合成，数据时点见上；Go/条件化 GO 均需老田 L3 单独批准，不得以本草案替代人工签署的复核回执。
