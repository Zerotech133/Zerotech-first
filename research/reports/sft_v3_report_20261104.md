# SFT v3 数据构建报告（2026-11-04）

> v2 2905+174｜通用回混 +960（去重丢 0）｜**合计 4039（train 3769 / val 270）**

| 类 | 条数 |
|---|---|
| v1_existing | 2678 |
| F_general | 960 |
| Q_citation | 219 |
| R_refusal | 182 |

## 用途
- 下一轮 SFT（当前百炼任务仍用 v2）；F 类占比约 23.8%（960/4039），防灾难性遗忘。
- 注册：`dataset_info.json` → `quant_kb_sft_v3`；配置：`configs/*_v3.yaml`。