# 第七批运行说明：官方日历对照与本人离线签名

本批交付 Ed25519 审核验证器、本人交互签名工具和两个只读日历命令。本人公钥、授权策略及可信应用接线尚未登记，`human-review` 继续拒绝写入。工程签名通过不证明老田身份，也不提高来源、历史可用、训练或生产资格。

已接入E盘并通过目标复验：439项中435通过，4项既有Windows符号链接权限跳过；真实455天日历对照零差异、源文件hash不变。8个源码/测试与冻结版hash一致。

## 日历命令

在安装后的 `E:\老田的AI助手\quant_backtest` 目录运行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator calendar-reference --input '<官方原文参考请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator calendar-compare --input '<reference及calendar_capture请求.json>'
```

参考请求严格为 `documents/coverage_start/coverage_end`，每份 document 严格为 `kind/url/raw_utf8/sha256`；比较请求严格为 `reference/calendar_capture`。结果只写标准输出，调用者自行选择归档位置；不需要数据库、不改输入文件。重复 JSON 键、未知字段和重新计算哈希后的语义伪造均拒绝。`match/mismatch/incomplete` 是描述性结果；有差异或缺日仍正常返回结果，调用者应检查 status。

交付参考覆盖 2025-01-02 至 2026-04-01，四份官方材料推导 455 个自然日、300 个开市日，与存量日历零差异。参考以外的日期未核验。2023 规则条件生效首日的官方佐证、临时停市全集检索仍有缺口；原文与当前下载回执不证明历史下载时间。

## 本人离线操作

本人在可交互终端中执行，不把私钥或口令交给 AI、聊天、共享目录或审核请求。先选一个已存在且本人管理的离线目录：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator.offline_review_signer keygen --private-key '<本人离线目录>\review-private.pem' --public-key '<本人离线目录>\review-public.json'
```

工具显示输出位置，输入“生成”并两次输入至少 12 字符的口令。私钥为加密 Ed25519 PKCS8。Windows 在独占空文件句柄上设当前用户专属受保护 DACL 后才写内容；不能设置权限或不能关闭口令回显时拒绝。文件不覆盖，UNC、重解析点和隐藏数据流路径拒绝。工程验收仅使用自动清理的临时密钥，未代本人生成真实私钥。

仅 `review-public.json` 的公钥和 SHA256 指纹用于后续登记。可信管理员需独立确认这把公钥确属老田，登记主体、审核角色、权限期限、撤销时间和登记凭据；完整历史策略由可信应用归档，摘要固定在请求以外。当前未提供通过请求 JSON 自行安装策略或启用审核的入口。

身份登记和接线完成后，本人核对完整审核声明及真实证据，再签署：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator.offline_review_signer sign --private-key '<本人离线目录>\review-private.pem' --statement '<本地完整声明.json>' --authority-id '<已登记权威ID>' --policy-id '<当前已登记策略ID>' --output '<新证明.json>'
```

工具显示完整规范声明、authority 和 policy，输入“签署”及解密口令后生成六字段 proof。签名绑定固定域、完整声明和策略；改动 action、capture/review、期限或策略都会使验证失败。拒绝用旧策略绕过后来撤销，历史回放按原事件接收时点验权。

写入失败会返回错误。私钥写入期间仅通过原独占句柄尝试删除原对象；删除失败则保留文件。公钥输出失败保留已生成的受保护加密私钥；非秘密公钥或 proof 的故障文件也可能保留，工具不按路径自动删除，不会覆盖重试。本人确认故障内容和文件归属后再处置。

## 依赖与边界

Python 3.11，Ed25519 使用 `cryptography`，HTML 正文使用 `BeautifulSoup`；Windows 私钥权限用系统原生 API，运行工具不依赖 PowerShell。工程 ACL 测试另使用已安装的 `pwsh` 读取权限作为独立验证。

没有修改生产行情、共享数据库、账本、凭据或 `memory.py`/DDL；没有模型训练、交易、同步器或新增常驻服务。上证存量行情截止 2026-04-01，不是实时行情。
