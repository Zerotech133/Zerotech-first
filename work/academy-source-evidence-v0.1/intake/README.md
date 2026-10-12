# 量子双刀来源补交包模板

本目录用于收集候选样本进入训练评审前必须补齐的证据。模板本身不授予任何权限，也不会打开训练、LoRA、模拟成交、交易或收费上架。

## 填写顺序

1. 在 `source-evidence-intake-template-v0.1.json` 登记原始来源、原始发布日期或生效日期证据和样本 SHA256。
2. 在 `license-registration-template-v0.1.json` 登记许可证主体、用途、期限、商业范围和许可证证据哈希。
3. 由本人离线填写 `offline-review-receipt-template-v0.1.json`，逐条核对样本 SHA256 后签名。
4. 登记与签名主体、角色、期限和范围一致的 `permission_snapshot`。
5. 在 `time-split-template-v0.1.json` 填写 train/validation/test 时间窗口和 `split_group`。无法证明边界时保持 `evaluation_only`。

## 安全约束

- 只填写公钥指纹和签名结果，不填写私钥、口令或助记词。
- 文件名、mtime、聊天接收时间不能单独作为原始发布日期或生效日期证据。
- `.rzrk`、`.tne` 没有可信解码器时只登记哈希，不推断内部公式。
- 所有记录初始状态都是 `evaluation_only`；模板不会把任何记录提升为训练资格。

## 重建模板

在本任务工作树根目录执行：

```powershell
& 'C:\Program Files\Python311\python.exe' -X utf8 work/build_quantum_double_blade_intake_templates_20261013.py
```

生成后应逐项人工补证，再由候选治理审计器重新判定。缺一项就继续保持阻断。
