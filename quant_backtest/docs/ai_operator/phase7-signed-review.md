# 第七批 B：Ed25519 公钥策略适配器自审

## 实现范围

本任务仅新增 `ai_operator/signed_review.py`、`tests_ai_operator/test_signed_review.py` 和本报告。没有修改 CLI、人审模块、memory/DDL、E 盘，没有生成用户真实私钥、签署真实批准或安装真人信任策略，没有创建 Git 提交、服务或代理。

固定接口如下，已提前通知主任务供本人签名工具接线：

```python
SignedReviewVerifier(bundle, expected_bundle_sha256)
signing_message(statement, authority_id, policy_id) -> bytes
verifier.verify(statement, proof, *, received_at) -> dict
```

## 契约核对

- bundle 和 expected 摘要均由可信应用代码在请求以外注入，不从 proof 安装授权。摘要是 `SHA256(canonical_json(bundle).encode("utf-8"))`，严格为 64 位小写 hex。
- bundle 必须是标准 JSON 对象，规范 UTF8 至多 262144 字节；严格顶层字段和版本，1 至 64 份完整策略，policy_id 唯一，effective_at 必须规范 UTC 且严格递增。
- 每份策略严格字段，1 至 64 个主体；主体严格八字段。非空身份、登记引用，两个指定角色，32 字节小写公钥和正确 SHA256 key_id，同策略不得重复公钥或 key_id。
- 权限有效起止和非 null 撤销时点须规范 UTC；valid_until 严格晚于 valid_from。授权有效区间为 `[valid_from, valid_until)`，撤销在 `received_at >= revoked_at` 时生效。
- 对 bundle 进行规范 JSON 深拷贝后构造不可变 tuple/MappingProxyType 权限索引；调用方修改原 bundle 不改变已构造适配器权限，返回 principal 也是新字典。
- cryptography 在构造适配器时按需导入，模块导入和 signing_message 不依赖库。缺库明确 ValueError；签名验证使用标准 `Ed25519PublicKey.verify`，没有自实现算法。
- 固定消息是 canonical JSON 的 UTF8，严格为 `{domain:"ai_operator.human_review.ed25519.v1",authority_id,policy_id,statement}`。statement 复用第六批 `_statement` 规范化，域、完整声明及 authority/policy 共同签名。
- proof 仅接受固定六字段，algorithm 严格为 Ed25519，signature_hex 为 64 字节小写 hex；拒绝额外 private_key、公钥、名字和请求级策略。
- received_at 必须是带时区字符串，规范化后不能晚于系统当前时间。只选 `effective_at <= received_at` 的最新策略，只接受该 authority/policy 和该策略登记的 key。不能回退旧策略绕过后续撤销或删除公钥。
- verify 只返回第六批四字段 principal。record/load 和撤销目标复用已有第六批接口，各自保存原 received_at；当前的新策略、到期和撤销不抹掉过去合法认证。
- 适配器不写库、不读私钥、不联网、不执行 proof。资格 flags 继续由既有事件与历史模块保持阻断，不能由签名产生 source_verified、training_eligible 或 production_eligible。

JSON 对象接口无法恢复上游解析已经丢失的重复文本键。本模块不接受原始 JSON 字符串；请求解析仍需沿用 CLI 的重复键拒绝。测试同时覆盖拒绝重复键原始字符串作为 bundle/proof，以及策略内重复公钥、policy_id 和生效时点。

## RED / GREEN 验证

工作目录：`work/ai_operator_phase7`。

```powershell
python -m unittest discover -s tests_ai_operator -p test_signed_review.py -v
```

1. 实现前 RED：17 项，17 个显式断言失败，原因均是缺少第七批 Ed25519 策略适配器。0.013 秒，退出码 1。
2. 首轮 GREEN：17 项全部通过，3.994 秒，退出码 0。
3. 自审修正“移除公钥”测试误用两个不同声明的夹具问题，确保同一声明的有效签名仅因策略删除 key 而被拒绝；补充已登记 authority/policy 改绑后的真实签名拒绝、旧策略提交零写入、64 份策略正向上限。
4. 最终 GREEN：20 项全部通过，3.426 秒，退出码 0。没有修改正式实现来迁就这些补充检查。

覆盖完整域/声明/身份策略/公钥/签名篡改、strict proof 私钥误入、bundle 摘要及标准 JSON、字段/时间/指纹/重复登记约束、256KiB 精确边界和 UTF8 超限、权限尚未生效/到期/撤销边界、未来 received、新策略旧 proof 不回退、调用方策略突变、缺 cryptography、真实工程 capture/review + record/load、非本人撤销拒绝及本人/admin 撤销、迟到到期/撤销零写入、多版本历史批准与新策略管理员撤销的目标原时点认证，以及共享适配器 4 线程/12 次独立只读连接查询零写入。

测试临时 Ed25519 私钥仅位于测试进程内存及自动清理的临时目录；没有复制到 outputs 或真实凭据目录。工程接收与审核材料复用第五/六批夹具并真正写入独立临时 SQLite，不以固定 principal stub 替代认证。

## 冻结哈希

`Get-FileHash -Algorithm SHA256`，文件原始字节摘要：

| 文件 | SHA256 |
| --- | --- |
| ai_operator/signed_review.py | cd856e4a0ded0273c7d2b60d7ee389add38b333ee6fba367fd89681a87188c3b |
| tests_ai_operator/test_signed_review.py | 95dba6f2bd0b66e5261cceb0736a27da86cf7f884e707ec9c46b92fc0c693d15 |

## 身份登记与验收缺口

enrollment_reference 只是可信管理员登记引用，本模块没有自动验证真人与公钥的绑定；固定摘要也不能证明管理员/真人身份。用户真实公钥、管理员权威、归档授权策略的真实性和本人独立核实仍未登记，CLI 真人审核应继续关闭。适配器实现和工程签名测试通过不等于真实来源或训练/生产放行。

本任务仅运行自己测试文件。独立复审、本人签名工具联调、完整回归和真实日历验收由主任务完成；本报告不声称这些外部检查已经通过。当前自审未发现契约外的实现或权限扩展。
