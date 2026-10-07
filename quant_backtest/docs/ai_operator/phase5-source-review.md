# 第五批 B：来源证据对照验收

实现位置：`ai_operator/source_review.py`。冻结接口为 `build_source_review(receipt, reviewer, evidence)` 和 `validate_source_review(review, receipt)`，均先调用 A 的真实 `validate_receipt`。

## 已实现

- 四类声明文档严格解析和逐项对照：`supplier_identity`、`price_basis`、`revision`、`official_calendar`。证据请求必须包含四键，每项为 `null` 或固定 `path/url` 对象。
- 当前普通本地文件边界，拒绝 UNC、设备命名空间、符号链接和 Windows 重解析点；打开时核对文件身份，限制实际读取至 1 MiB。
- 严格 UTF-8 JSON、重复键、未知字段、非有限数、日期、SHA256、整数非布尔类型校验。官方 URL 需为对应 SSE/SZSE 的 HTTPS 真域或合法子域；所有声明 URL 都只校验格式，不访问网络。
- 日历按接收覆盖区间的全部自然日对照，同值重复合并，冲突重复拒绝，多余日期忽略；已知冲突优先于缺日。
- `raw` 要求 `adjustment_reference=null`；`qfq_snapshot`、`hfq_snapshot` 即使四类文档一致，仍添加 `adjustment_factors=missing`。
- 证据保存完整 `raw_utf8`、SHA256、字节数、规范绝对路径及声明 URL；`review_id` 绑定全部输出。离线验证不再读取路径，重新计算文档完整性、逐项检查及整体状态，并拒绝重哈希后的虚假 `pass`、未知字段或资格提升。
- 编译时间由系统在读取完毕后生成，拒绝早于凭据可用时间；验证拒绝无时区、不规范、未来或早于凭据的编译时间。
- 四项检查名称及顺序与证据类别一致；复权补充项排在最后。失败优先为 `mismatch`，缺口为 `needs_evidence`，全匹配最多为 `ready_for_human_review`。所有输出 `eligible=false`、`production_eligible=false`。

## 测试证据

先 RED：接口模块缺失，接口存在断言失败；完整 20 项测试无法取得来源审核实现。测试夹具直接使用普通临时 JSON、真实 `capture_to_store` 和真实 `MemoryStore`，未替代凭据验证主路径。

GREEN：`python -m unittest tests_ai_operator.test_source_review -v`，20 项通过，0 跳过。

全量：`python -m unittest discover -s tests_ai_operator -v`，309 项，56.172 秒，`OK (skipped=4)`。跳过为既有 bundles 两项、provenance 一项、trading_calendar 一项真实符号链接创建测试，原因均是当前 Windows 会话缺少相应权限；真实目录 junction 拒绝测试通过。本次全量包含运行当时可发现的 A、B、C 测试，主代理后续改动仍需最终复验。

覆盖了缺证据、全匹配、人审限制、供应商 ID/证券/价格口径/修订/行情字节 SHA 不匹配、日历缺日及休市冲突、日历重复、伪官方子域、交易所域绑定、非法 HTTPS、两个复权口径、JSON 字段和类型、无穷值、重复键、非法 UTF-8、文件大小、源文档删除后的离线验证，以及重新哈希的字节/检查/资格篡改。

## SHA256

- `ai_operator/source_review.py`：`284b07dd593f3de7eaefea322a0cff677f77be809e2851197aa9d66c3099e59d`
- `tests_ai_operator/test_source_review.py`：`e9e19d9cb76b69cd10f8b0a61dc8edfc20f13beddd920cfedcca1b3b03ff67c0`

## 认证限制

声明 URL 不等于实际下载记录、官方签名或真实性证明。供应商名称仅校验非空，来源 ID/修订/价格口径仅与既有凭据对照；同一数据自行生成声明不能成为独立来源认证。任何人都可能重造内容一致的文档并重新计算全部哈希，此时审核仍最多达到待人审状态。真实供应商、复权算法和因子、真实官方日历下载或签名认证均未完成，不能进入生产或训练资格。未新增或修改 trusted 开关。

仅编辑 B 所有的实现、测试和报告三文件；未提交、未写 E 盘、未联网。
