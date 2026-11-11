# 2026-11-11 HZ observe-only 7 天复评（一页建议）

- 窗口：2026-11-04 → 2026-11-11（L3=GO 观察期）
- 结论：**继续观察（observe-only），不强制、不回退**；复评点 11-16（ARCH1 接线）或 ③ 77/77 复核完成时
- 依据：`work/huangzhong/review_20261110_223023.json`、`preflight_2215.json`、`work/huangzhong/daily.jsonl`、`reject_review_20261111.json`

## 一、7 天数据（只读）

| 指标 | 数值 |
|---|---|
| runs | 458（窗口切点 11-03 22:16）；近 7 日 daily 口径 450 |
| PASS / REJECT / OBSERVE | 389 / 69 / 0（pass_rate 84.93%） |
| merge_done | 155 |
| coverage | 2.9548 runs/merge（约 295% 覆盖） |
| unique commit（近 7 日） | 235 |
| blocking | 0（observe 模式，全部不阻断） |
| redteam_rate | 1.0（450/450） |
| 受控演练 | 2 次（REJECT=1 / OBSERVE=1） |

## 二、六条验收状态（11-10 22:15 最终）

① PASS 样本 341/10 ✓ ｜ ② 演练 2 次 ✓ ｜ ③ 误拒率 pending（复核 56/77，0 推翻、21 待复核）｜ ④ 满窗 153/漏放 0 ✓（跟踪 188）｜ ⑤ 证据完整 335/335、新鲜 335/335（legacy 豁免 6）✓ ｜ ⑥ 赵云抽查 3/3 ✓

## 三、五项问题

1. **③ 未闭合**：自然拒收 76 单，已复核 56 单、推翻 0（0% < 5%），但还有 21 单未复核，不能宣布达标。
2. **基线红**：`test_baseline/latest.json`（11-10 20:00）passed 3686 / **failed 193** / error 0；近 7 日 29 次 REJECT 原因即 `failed=193`（另有 61 次 `failed=14`）。基线不绿时，HZ 接线后会拒收几乎全部合入——这是**正确拒收**，但会让“误拒率”失去意义，必须先修基线或定义豁免口径。
3. **observe-only 未接线**：`tools/merge_gated.py`、`tools/devtask.py` 中无 huangzhong 调用（grep 为空）；当前只有独立 30 分钟节拍验收（`:15/:45`，mode=observe、blocking=false），**没有 merge 前置 warn 记录**，无法评估“观察不阻断”的真实干扰。
4. **平均验收时长无实测**：验收单无 start/end 字段，只有固定 30 分钟节拍；无法给出 SLA，也无法判断人工复核瓶颈。
5. **覆盖率口径需固定**：coverage 2.95 是 runs/merge_done，unique commit 235；需在 HZ 文档中写明“覆盖=每合入单的验收次数”，避免与“提交覆盖率”混用。

## 四、建议

1. **继续 observe-only**（当前 mode=observe、blocking=false 保持不变），不强制、不回退。
2. 先做两件事再谈强制：① 补完 ③ 剩余 21 单复核（目标 77/77、推翻 <5%）；② 修基线 193 红或给出书面豁免口径。
3. ARCH1 11-16 接线时只接 **warn**：merge 前置只记录、不阻断；至少观察 3 个合并窗，统计 warn 命中、误拦、人工解释成本。
4. 验收单加 `started_at/finished_at`（或 duration_s），否则 ④“平均验收时长”永远无数据。
5. 强制（enforce）门槛：③ 77/77 通过 + 基线绿 + warn 观察无不可接受干扰 + 老田书面批准；回退门槛：连续 2 窗出现误拦且无法解释。

## 五、证据

- 六条复核：`work/huangzhong/review_20261110_223023.json` / `.md`
- 预检与 KPI：`deliverables/evidence/2026-11-10_hz_final/preflight_2215.json`
- 误拒复核进度：`work/huangzhong/reject_review_20261111.json`（56/77）
- 日节拍：`work/huangzhong/daily.jsonl`（450 行/7 日）
- 最终回执：`deliverables/2026-11-10_黄忠HZ最终验收_NO_GO回执_v1.md`（commit `1a899b5c3`）
